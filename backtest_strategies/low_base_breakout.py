"""
low_base_breakout.py -- run_backtest_low_base_breakout()
Split out of backtest.py on 2026-09-03. Pure relocation, no logic changed.
"""
import json
import uuid
import math
import re
import logging
import bisect
from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

import backtest_common as _bc
from price_integrity import research_price_issues
from backtest_common import (
    DB_PATH,
    DELISTING_CONFIRM_DAYS,
    QUALITY_DAY_CLASSES,
    is_excluded_day,
    last_tradable_day_before_break,
    last_tradable_index_before,
    load_adjusted_prices,
    _CHART_BOTTOM_MIN,
    _CHART_TOP_MIN,
    _chart_bottom_confluence,
    _chart_prep,
    _chart_top_confluence,
    _final_liquidation_quote_for_code,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_low_base_breakout(
    start_date: str, end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    run_name: str = None, run_id: str = None,
    ma60_range_min: float = -0.18,   # MA60 대비 하단 (-18%)
    ma60_range_max: float = +0.10,   # MA60 대비 상단 (+10%, 막 돌파한 경우 포함)
    pct_from_low_max: float = 65.0,  # 52주 저점 대비 +65% 이내 (3배+ 종목 83% 포착)
    ma20_gap_max: float = 0.08,      # MA20이 MA60 대비 -8% 이내 (수렴 중)
    min_up_days: int = 3,            # 최근 5일 중 상승일 (가격 모멘텀 확인)
    stop: float = -0.10,             # 손절 -10%
    trail: float = -0.15,            # 이익 달성 후 추적손절 -15%
    trail_mid: float = -0.20,        # 30%+ 이익 시 -20%
    trail_big: float = -0.25,        # 80%+ 이익 시 -25% (대박 홀드)
    max_hold: int = 270,
    asof_mktcap: bool = False,       # 2026-07-17 as-of 재검증: current 대비 악화로 기각 → False 유지 (signal_experiment_ledger: low_base_breakout/no_new_signal)
    chart_confluence: bool = False,  # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    adjusted_prices: bool = None,    # W5b(REVIEW_PLAN §32-3): None이면 backtest_common.ADJUSTED_PRICES_DEFAULT
) -> str:
    """
    저점기반 돌파 전략 (V-LOWBASE).

    [실증 기반 설계 근거]
    3배+ 달성 종목 200건 분석(2022-2025):
    - 86%가 MA60 ±15% 이내 또는 MA60 상단 → 깊은 낙폭 불필요
    - 65%가 52주 저점 +30% 이내, 83%가 +60% 이내 → 저점 근방 집중
    - 44%가 거래량 0.8배 미만 → 낮은 거래량(축적) 상태에서도 가능
    V-GC(avg5=+50%)는 골든크로스 후 진입, V-LOWBASE는 골든크로스 직전/직후 초기 진입.

    진입 조건:
    A) MA60 대비 -18%~+10% 범위 (저점 근방 + 막 돌파)
    B) 52주 저점 대비 +0~65% 이내 (저점 기반 종목)
    C) MA20이 MA60 대비 -8% 이내 (골든크로스 수렴 중)
    D) 최근 5일 중 3일 이상 상승 (가격 모멘텀)
    E) 시총 300억+ (KOSPI/KOSDAQ 한정)

    매도: Trail-15%(이익후)/Trail-20%(30%+)/Trail-25%(80%+) / 손절-10% / 만료 270일
    """
    adjusted_prices = _bc.ADJUSTED_PRICES_DEFAULT if adjusted_prices is None else bool(adjusted_prices)
    # 조정 모드(deep_recovery W5b-1과 같은 규칙): 신호·손익 = 조정 시계열, 시총 = 원주가, 유니버스 = PIT(폐지 포함),
    # 기간 전체를 보고 종목을 통째로 빼던 '분할/합병 필터'·'130행 이상' 조건(미래 정보)은 쓰지 않는다(신호일 이력 i ≥ 260은 그대로).
    if adjusted_prices:
        asof_mktcap = True
    adj_stats = {'enabled': adjusted_prices, 'candidate_skips_excluded': 0, 'candidate_skips_quality_day': 0,
                 'break_liquidations': 0, 'break_day_liquidations': 0, 'zero_volume_deferred_sells': 0,
                 'zero_volume_skipped_buys': 0, 'stocks_with_breaks': 0, 'share_unknown_breaks': 0,
                 'delisted_exits': 0, 'unevaluable_delistings': 0, 'delisted_entries': 0, 'pit_mktcap_missing': 0}
    init_backtest_db()
    run_name = run_name or f"V-LOWBASE저점돌파 {start_date[:7]}~{end_date[:7]}"
    _lowbase_params = {"per_stock": per_stock, "max_positions": max_positions,
                       "start": start_date, "end": end_date}
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute(
            "INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status) "
            "VALUES (?,?,'low_base_breakout',?,?,?,?,'running')",
            (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running',strategy='low_base_breakout' WHERE run_id=?",
                     (run_id,))
        conn.commit()

    _record_run_spec(run_id, "low_base_breakout", "lowbase_v2_strict_20260715",
                     {**_lowbase_params, "asof_mktcap": asof_mktcap, "chart_confluence": chart_confluence,
                      "adjusted_prices": True if adjusted_prices else None},
                     signal_timing="close_D", execution_timing="next_open",
                     market_cap_mode=("asof_approx" if asof_mktcap else "current"),
                     allocation_rule="fixed_slot",
                     universe_version=("security_master_history_v3_pit_delisted" if adjusted_prices else
                                       "security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current"))
    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=390)).strftime('%Y-%m-%d')

        # 종목 로드: 시총 300억+(as-of 가능 시 security_master_history), KOSPI/KOSDAQ, 6자리
        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code = su.stock_code
                WHERE p.date >= ? AND p.date <= ? AND p.close >= 500
                  AND LENGTH(p.stock_code) = 6
                  AND p.stock_code NOT LIKE '%^%'
                  AND p.stock_code NOT LIKE '%=%'
                  AND p.stock_code NOT LIKE 'GC%' AND p.stock_code NOT LIKE 'CL%'
                  AND p.stock_code NOT LIKE '%-F' AND p.stock_code NOT LIKE 'NQ%'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                INNER JOIN stock_universe su ON p.stock_code = su.stock_code
                WHERE p.date >= ? AND p.date <= ? AND p.close >= 500
                  AND su.market_cap >= 300
                  AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code) = 6
                  AND p.stock_code NOT LIKE '%^%'
                  AND p.stock_code NOT LIKE '%=%'
                  AND p.stock_code NOT LIKE 'GC%' AND p.stock_code NOT LIKE 'CL%'
                  AND p.stock_code NOT LIKE '%-F' AND p.stock_code NOT LIKE 'NQ%'
                ORDER BY su.market_cap DESC
            """, (start_date, end_date)).fetchall()

        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history ORDER BY stock_code,effective_from"""
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        tradable_intervals: Dict[str, list] = {}
        for code, effective_from, effective_to in conn.execute(
            """SELECT stock_code,effective_from,effective_to
               FROM security_master_history
               WHERE is_tradable=1 AND is_etf_etn=0
                 AND market IN ('KOSPI','KOSDAQ')
               ORDER BY stock_code,effective_from"""
        ):
            tradable_intervals.setdefault(code, []).append((effective_from, effective_to))

        def _is_tradable_day(code: str, day: str) -> bool:
            for effective_from, effective_to in tradable_intervals.get(code, []):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return True
            return False

        def _shares_asof(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _quality in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        if adjusted_prices:
            _codes = sorted({str(c_) for c_, _m in codes})
            _ap = load_adjusted_prices(conn, _codes, warmup_start, end_date)
            _qdays: Dict[str, set] = {}
            for _qc, _qd, _qcls in research_price_issues(conn, _codes, warmup_start, end_date, allow_confirmed_corporate_actions=True):
                if _qcls in QUALITY_DAY_CLASSES:
                    _qdays.setdefault(str(_qc), set()).add(str(_qd)[:10])
            for code in _codes:
                e = _ap.get(code)
                if not e or not e['dates'] or not any(start_date <= d <= end_date for d in e['dates']):
                    continue
                d_list = [str(d)[:10] for d in e['dates']]
                sd[code] = {
                    'd': d_list, 'dates': d_list,
                    'c': list(e['close']), 'v': list(e['volume']), 'volumes': list(e['volume']),
                    'h': list(e['high']), 'lo': list(e['low']), 'o': list(e['open']),
                    'raw_c': list(e['raw_close']), 'f': list(e['adj_factor']),
                    'breaks': list(e['breaks']), 'excluded_ranges': list(e['excluded_ranges']),
                    'disc': e.get('break_disclosed', {}) or {}, 'qdays': _qdays.get(code, set()),
                    'master_closed_to': e.get('master_closed_to'),
                    'mkt_cap_억': 300,
                }
                adj_stats['stocks_with_breaks'] += 1 if e['breaks'] else 0
                adj_stats['share_unknown_breaks'] += e.get('share_unknown_breaks', 0)
                if chart_confluence:
                    sd[code]['chart'] = _chart_prep(d_list, sd[code]['lo'], sd[code]['c'])
        for code, mktcap in ([] if adjusted_prices else codes):
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0), COALESCE(high,close),
                       COALESCE(low,close), COALESCE(open,close)
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 130: continue
            c_list = [float(r[1]) for r in rows]
            # 분할/합병 필터
            if any(c_list[i-1]>0 and (c_list[i]/c_list[i-1]<0.45 or c_list[i]/c_list[i-1]>2.2)
                   for i in range(1, len(c_list))): continue
            sd[code] = {
                'd': [r[0] for r in rows],
                'c': c_list,
                'v': [float(r[2]) for r in rows],
                'h': [float(r[3]) for r in rows],
                'lo': [float(r[4]) for r in rows],
                'o': [float(r[5]) for r in rows],
                'mkt_cap_억': round(mktcap) if mktcap else 300,
            }
            if chart_confluence:
                sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], c_list)

        sim_dates = sorted(set(
            d for s in sd.values() for d in s['d'] if start_date <= d <= end_date
        ))
        didx = {c: {d: i for i, d in enumerate(s['d'])} for c, s in sd.items()}
        if adjusted_prices and sim_dates:
            from datetime import date as _d0
            for code, s_ in sd.items():
                s_['last_tradable_i'] = max((k for k, v_ in enumerate(s_['v']) if v_ > 0), default=None)
                s_['ends_before_period_end'] = s_['d'][-1] < sim_dates[-1]
                _mc = s_.get('master_closed_to')
                try:
                    _gap = abs((_d0.fromisoformat(_mc[:10]) - _d0.fromisoformat(s_['d'][-1][:10])).days) if _mc else None
                except Exception:
                    _gap = None
                s_['delisting_confirmed'] = bool(_gap is not None and _gap <= DELISTING_CONFIRM_DAYS)

        def _pit_mc(code, day_, raw_px):
            """신호일 시총(억원) = 상장주식 수 × 원주가. 주식 수를 모르면 가장 보수적(최대 슬리피지)."""
            sh_ = _shares_asof(code, day_)
            if sh_ > 0:
                return sh_ * raw_px / 1e8
            adj_stats['pit_mktcap_missing'] += 1
            return 1.0

        def _close_now(code, p, i, price, reason, day_):
            """조정 단위 `price`로 즉시 청산(단절·폐지 처리)."""
            nonlocal cash
            pnl_, net_pct_ = _net_profit(p['entry'], price, p['qty'], p['mkt_cap_억'])
            cash += p['invested'] + pnl_
            _f = sd[code]['f'][i]
            trades.append({'code': code, 'entry': p['entry'], 'exit': price,
                           'ret': net_pct_, 'reason': reason, 'hold': p['hold'],
                           'pnl': pnl_, 'entry_date': p['entry_date'], 'exit_date': day_,
                           'exit_raw': round(price / _f, 4) if _f else price,
                           'entry_raw': p.get('entry_raw'), 'qty_raw': p.get('qty_raw')})
            del pos[code]
            pending_sells.pop(code, None)

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        for day in sim_dates:
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos: continue
                if adjusted_prices and (sd[code]['v'][i] or 0) <= 0:
                    adj_stats['zero_volume_deferred_sells'] += 1   # 거래 불가능한 날 — 다음 거래일로 이월
                    continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['qty'], p['mkt_cap_억'])
                cash += p['invested'] + pnl
                _t = {'code': code, 'entry': p['entry'], 'exit': fill,
                      'ret': net_pct, 'reason': reason, 'hold': p['hold'],
                      'pnl': pnl, 'entry_date': p['entry_date'], 'exit_date': day}
                if adjusted_prices:
                    _ff = sd[code]['f'][i]
                    _t.update({'exit_raw': round(fill / _ff, 4) if _ff else fill,
                               'entry_raw': p.get('entry_raw'), 'qty_raw': p.get('qty_raw')})
                trades.append(_t)
                del pending_sells[code]

            if adjusted_prices:
                for code, p in list(pos.items()):
                    i = didx[code].get(day)
                    if i is not None and i > 0 and day in sd[code]['breaks']:
                        _k = last_tradable_index_before(sd[code], i)
                        _k = i - 1 if _k is None else _k
                        _close_now(code, p, i, sd[code]['c'][_k], '단절 당일 청산(공시 근거 없음·평가 불가)', day)
                        trades[-1]['evaluation'] = 'unevaluable_break'; trades[-1]['basis_date'] = sd[code]['d'][_k]
                        adj_stats['break_day_liquidations'] += 1

            marked_equity = cash + sum(
                p['qty'] * (sd[code]['c'][didx[code][day]] if day in didx[code] else p['entry'])
                for code, p in pos.items()
            )
            position_limit = max(max_positions, int(marked_equity // per_stock))
            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None: continue
                if not _is_tradable_day(code, day):
                    pending_buys.remove(code)
                    continue
                if code not in pos and len(pos) < position_limit:
                    fill = sd[code]['o'][i]
                    invest = min(per_stock, cash)
                    if adjusted_prices:
                        if (sd[code]['v'][i] or 0) <= 0 or fill <= 0:
                            adj_stats['zero_volume_skipped_buys'] += 1
                            pending_buys.remove(code); continue
                        _ff = sd[code]['f'][i] or 1.0
                        _rfill = fill / _ff
                        qty_raw = int(invest // _rfill)
                        qty = qty_raw / _ff               # 원주가 기준 정수 주식 → 조정 단위
                        invested = qty_raw * _rfill
                        _mc = _pit_mc(code, day, _rfill)
                    else:
                        qty = int(invest // fill)
                        invested = qty * fill
                        _mc = sd[code]['mkt_cap_억']
                    if qty > 0:
                        cash -= invested
                        pos[code] = {'entry': fill, 'qty': qty, 'invested': invested,
                                     'peak': fill, 'hold': 0,
                                     'mkt_cap_억': _mc,
                                     'entry_date': day}
                        if adjusted_prices:
                            pos[code].update({'entry_raw': round(_rfill, 4), 'qty_raw': qty_raw})
                            if sd[code].get('ends_before_period_end'):
                                adj_stats['delisted_entries'] += 1
                pending_buys.remove(code)

            # 매도 체크
            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if adjusted_prices and i is not None:
                    s_ = sd[code]
                    # 상장폐지·거래종료(N1 ②·§26-2 ②): 자료가 기간 끝 전에 끝나고 오늘이 마지막 거래 가능일이면 그 종가로 청산
                    if s_.get('ends_before_period_end') and s_.get('last_tradable_i') == i:
                        _close_now(code, p, i, s_['c'][i], '상장폐지 청산(delisted)', day)
                        trades[-1]['delisted'] = True
                        if s_.get('delisting_confirmed'):
                            adj_stats['delisted_exits'] += 1
                        else:
                            trades[-1]['evaluation'] = 'unevaluable_delisting'
                            adj_stats['unevaluable_delistings'] += 1
                        continue
                    # 계수 미확정 단절이 공시돼 있고 오늘이 그 전 '거래 가능한 마지막 날'이면 오늘 종가로 사전 청산(D12 ②)
                    if s_['breaks']:
                        _bi = bisect.bisect_right(s_['breaks'], day)
                        if _bi < len(s_['breaks']):
                            _nb = s_['breaks'][_bi]
                            if last_tradable_day_before_break(s_, s_['disc'].get(_nb), _nb) == day:
                                _close_now(code, p, i, s_['c'][i], '단절 전 청산(D12, 공시 후)', day)
                                adj_stats['break_liquidations'] += 1
                                continue
                if code in pending_sells: continue
                if i is None: continue
                curr = sd[code]['c'][i]
                if curr <= 0: continue
                entry = p['entry']
                peak = max(p.get('peak', entry), curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1
                ret = (curr - entry) / entry
                # 계층형 Trail
                if ret >= 0.80: tpct = trail_big
                elif ret >= 0.30: tpct = trail_mid
                else: tpct = trail
                trail_cond = (curr - peak) / peak < tpct
                stop_cond  = ret < stop
                expire_cond = p['hold'] >= max_hold
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN
                if stop_cond or trail_cond or expire_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'chart_top' if chart_top_cond else 'expire')
                    pending_sells[code] = reason

            # 매수 체크 (포지션 여유 있을 때)
            if len(pos) + len(pending_buys) < position_limit:
                candidates = []
                for code, s in sd.items():
                    if code in pos or code in pending_buys: continue
                    i = didx[code].get(day)
                    if i is None or i < 260: continue
                    if not _is_tradable_day(code, day):
                        continue
                    c_arr = s['c']
                    v_arr = s['v']
                    lo_arr = s['lo']
                    curr = c_arr[i]
                    if curr <= 0: continue
                    _rc = s['raw_c'][i] if adjusted_prices else curr   # 시총은 원주가 기준
                    if adjusted_prices:
                        if is_excluded_day(s, day):
                            adj_stats['candidate_skips_excluded'] += 1; continue
                        if day in s['qdays']:
                            adj_stats['candidate_skips_quality_day'] += 1; continue

                    # 조건 E: 시총 300억+ (as-of)
                    if asof_mktcap:
                        sh = _shares_asof(code, day)
                        if sh <= 0 or sh * _rc / 1e8 < 300:
                            continue

                    # MA20, MA60
                    ma20 = sum(c_arr[i-19:i+1]) / 20
                    ma60 = sum(c_arr[i-59:i+1]) / 60
                    if ma60 <= 0: continue

                    # 조건 A: MA60 대비 -18%~+10%
                    ma60_depth = (curr - ma60) / ma60
                    if ma60_depth < ma60_range_min or ma60_depth > ma60_range_max:
                        continue

                    # 조건 B: 52주 저점 대비 +0~65%
                    lo52 = min(lo_arr[max(0, i-260):i+1]) if i >= 260 else min(lo_arr[:i+1])
                    if lo52 <= 0: continue
                    pct_from_low = (curr - lo52) / lo52 * 100
                    if pct_from_low > pct_from_low_max: continue

                    # 조건 C: MA20이 MA60 대비 -8% 이내 (수렴 중)
                    ma20_gap = (ma20 - ma60) / ma60
                    if ma20_gap < -ma20_gap_max: continue

                    # 조건 D: 최근 5일 중 3일 이상 상승
                    if i < 4: continue
                    recent5 = c_arr[i-4:i+1]
                    up_days = sum(1 for j in range(1, 5) if recent5[j] > recent5[j-1])
                    if up_days < min_up_days: continue

                    # 스코어 계산 (정렬용)
                    # 저점에 가까울수록 높은 점수
                    low_score  = max(0, 65 - pct_from_low)   # 0~65 → 역방향
                    # MA60 수렴도 (가까울수록 좋음)
                    gap_score  = max(0, 8 - abs(ma20_gap * 100))  # 0~8
                    # 스코어 합산
                    score = low_score + gap_score
                    # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                    if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                        continue
                    candidates.append((score, code, curr))

                candidates.sort(reverse=True)
                buy_count = max(0, position_limit - len(pos) - len(pending_buys))
                pending_buys.extend(code for _, code, _ in candidates[:buy_count])

        # 미청산 포지션 강제 청산
        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            curr, final_reason = _final_liquidation_quote_for_code(conn, code, last_day, didx[code], sd[code]['c'])
            pnl, net_pct = _net_profit(p['entry'], curr, p['qty'], p['mkt_cap_억'])
            cash += p['invested'] + pnl
            trades.append({'code': code, 'entry': p['entry'], 'exit': curr,
                           'ret': net_pct, 'reason': final_reason,
                           'hold': p['hold'], 'pnl': pnl,
                           'entry_date': p['entry_date'], 'exit_date': last_day})
            pos.pop(code, None)

        # 결과 집계
        initial = per_stock * max_positions
        if not trades:
            total_return = 0.0; win_rate = 0.0; trade_cnt = 0
            final = initial
        else:
            final   = cash
            total_return = (final - initial) / initial * 100
            wins = [t for t in trades if t['pnl'] > 0]
            win_rate = len(wins) / len(trades) * 100
            trade_cnt = len(trades)

        avg_ret = sum(t['ret'] for t in trades) / len(trades) if trades else 0
        days_held = max((datetime.strptime(end_date, '%Y-%m-%d') -
                         datetime.strptime(start_date, '%Y-%m-%d')).days, 1)
        ann_return = ((1 + total_return / 100) ** (365 / days_held) - 1) * 100 if total_return > -100 else -100
        summary = (f"V-LOWBASE 엄격 다음날시가·정수주식·복리 | {start_date}~{end_date} | "
                   f"총수익률:{total_return:.1f}% | 승률:{win_rate:.1f}% | "
                   f"거래:{trade_cnt}건 | 평균:{avg_ret:.1f}%"
                   + (f"\n조정가격(W5b): {json.dumps(adj_stats, ensure_ascii=False)}" if adjusted_prices else ""))

        conn.execute(
            """
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, ann_return_pct=?, win_rate=?,
                total_trades=?, profit_trades=?, summary_text=?, trades_json=?
            WHERE run_id=?
            """,
            (
                round(total_return, 2),
                round(ann_return, 2),
                round(win_rate, 2),
                trade_cnt,
                len([t for t in trades if t.get('pnl', 0) > 0]),
                summary,
                __import__('json').dumps(trades[-50:]),
                run_id,
            ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, initial, final)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=60)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise


# ─── V-TURNAROUND 흑자전환 특화 전략 ────────────────────────────────────────

