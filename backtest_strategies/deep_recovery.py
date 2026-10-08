"""
deep_recovery.py -- run_backtest_deep_recovery()
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

def run_backtest_deep_recovery(
    start_date: str,
    end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    stop: float = -0.13,
    trail: float = -0.22,
    trail_big: float = -0.30,
    tp: float = 1.00,               # 100%+ 익절 (2배 노림)
    max_hold: int = 300,
    ma60_depth_min: float = -0.25,  # MA60 -25% 이상 낙폭 (데이터 최강구간 시작)
    ma60_depth_max: float = -0.60,  # -60% 이하는 상폐 위험
    pct_from_low_min: float = 10.0, # 저점에서 최소 +10% 반등 확인
    pct_from_low_max: float = 100.0,# 저점 대비 +100% 이내
    vol_ratio: float = 1.5,         # 거래량 1.5x+ (완화)
    asof_mktcap: bool = False,      # 2026-07-17 as-of 재검증: current 대비 악화로 기각 → False 유지 (signal_experiment_ledger: deep_recovery/no_new_signal)
    chart_confluence: bool = False, # 2026-07-18 공통모듈: 일봉+주봉+캔들 컨플루언스(2/3) 진입게이트+고점청산
    adjusted_prices: bool = None,   # W5b(REVIEW_PLAN §32-3): None이면 backtest_common.ADJUSTED_PRICES_DEFAULT
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-DEEP: 깊은낙폭 반등 집중 전략.

    [데이터 기반 설계 — 2026-07-02 실증]
    370만 거래일 분석:
      MA60 -25~-35%: 평균 120d +56.3%
      MA60 -35~-45%: 평균 120d +73.9%
      MA60 -45%↓:    평균 120d +103.5%
    → V-RECOVERY(-20~-65%)보다 최강구간(-25~-60%)에 집중

    진입 조건:
    A) MA60 대비 -25% ~ -60% 낙폭 (최강구간 집중)
    B) 52주 저점 대비 +10~100% (저점 탈출 확인 후 포착)
    C) 거래량 1.5x+ (진입 확인)
    D) 최근 5일 중 3일 이상 상승 (반등 지속 확인)
    E) 시총 300억+ (안전 마진)
    F) KOSPI MA120 × 0.80 이상 (패닉장 제외)

    매도: Trail -22%(이익 후) / Trail -30%(50%+ 이익) / 손절 -13% / 만료 300일 / 익절 100%
    """
    adjusted_prices = _bc.ADJUSTED_PRICES_DEFAULT if adjusted_prices is None else bool(adjusted_prices)
    # 조정 모드: 신호·손익 = 조정 시계열, 가격 수준·시총 필터·체결 기록 = 원주가, 유니버스 = 기간 중 상장(폐지 포함) PIT 마스터,
    # 시총 = 신호일 주식 수 × 원주가. 기간 전체를 보고 종목을 통째로 빼던 '분할/합병 필터'·'90행 이상' 조건(미래 정보)은 쓰지 않는다.
    if adjusted_prices:
        asof_mktcap = True
    adj_stats = {'enabled': adjusted_prices, 'candidate_skips_excluded': 0, 'candidate_skips_quality_day': 0,
                 'break_liquidations': 0, 'break_day_liquidations': 0, 'zero_volume_deferred_sells': 0,
                 'zero_volume_skipped_buys': 0, 'stocks_with_breaks': 0, 'share_unknown_breaks': 0,
                 'delisted_exits': 0, 'unevaluable_delistings': 0, 'delisted_entries': 0, 'pit_mktcap_missing': 0}
    init_backtest_db()
    run_name = run_name or f"V-DEEP깊은낙폭 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "deep_recovery", "deep_v2_strict_20260715",
        {"stop": stop, "trail": trail, "trail_big": trail_big, "tp": tp, "max_hold": max_hold,
         "ma60_depth_min": ma60_depth_min, "ma60_depth_max": ma60_depth_max,
         "pct_from_low_min": pct_from_low_min, "pct_from_low_max": pct_from_low_max,
         "vol_ratio": vol_ratio, "per_stock": per_stock, "max_positions": max_positions,
         "asof_mktcap": asof_mktcap, "chart_confluence": chart_confluence,
         "adjusted_prices": True if adjusted_prices else None,
         "start": start_date, "end": end_date},
        signal_timing="close_D", execution_timing="next_open",
        market_cap_mode=("asof_approx" if asof_mktcap else "current"), allocation_rule="fixed_slot",
        universe_version=("security_master_history_v3_pit_delisted" if adjusted_prices else
                          "security_master_history_v1_mixed_approx" if asof_mktcap else "stock_universe_current"),
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'deep_recovery',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=300)).strftime('%Y-%m-%d')

        k_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 ORDER BY date
        """).fetchall()
        k_dates  = [r[0] for r in k_rows]
        k_prices = [float(r[1]) for r in k_rows]
        k_idx    = {d: i for i, d in enumerate(k_dates)}

        def _k_ma120(date: str) -> Optional[float]:
            idx = k_idx.get(date)
            if idx is None:
                for d in reversed(k_dates):
                    if d <= date: idx = k_idx[d]; break
            if idx is None or idx < 120: return None
            return sum(k_prices[idx-119:idx+1]) / 120

        if asof_mktcap:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN security_master_history sm ON sm.stock_code=p.stock_code
                  AND substr(p.date,1,10)>=sm.effective_from
                  AND (sm.effective_to IS NULL OR substr(p.date,1,10)<sm.effective_to)
                  AND sm.is_tradable=1 AND sm.is_etf_etn=0
                  AND sm.market IN ('KOSPI','KOSDAQ')
                LEFT JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            """, (start_date, end_date)).fetchall()
        else:
            codes = conn.execute("""
                SELECT DISTINCT p.stock_code, su.market_cap
                FROM price_history p
                JOIN stock_universe su ON p.stock_code=su.stock_code
                WHERE p.date BETWEEN ? AND ? AND p.close>0
                  AND su.market_cap >= 300
                  AND su.market IN ('KOSPI','KOSDAQ')
                  AND LENGTH(p.stock_code)=6
                  AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
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
            _period_last = None
            for code in _codes:
                e = _ap.get(code)
                if not e or not e['dates']:
                    continue
                if not any(start_date <= d <= end_date for d in e['dates']):
                    continue
                _raw_o = [ (o_ / f_ if f_ else o_) for o_, f_ in zip(e['open'], e['adj_factor'])]
                sd[code] = {
                    'd': list(e['dates']), 'dates': list(e['dates']),
                    'c': list(e['close']), 'v': list(e['volume']), 'volumes': list(e['volume']),
                    'h': list(e['high']), 'lo': list(e['low']), 'o': list(e['open']),
                    'raw_c': list(e['raw_close']), 'raw_o': _raw_o, 'f': list(e['adj_factor']),
                    'breaks': list(e['breaks']), 'excluded_ranges': list(e['excluded_ranges']),
                    'disc': e.get('break_disclosed', {}) or {}, 'qdays': _qdays.get(code, set()),
                    'master_closed_to': e.get('master_closed_to'),
                    'mkt_cap_억': 300,
                }
                adj_stats['stocks_with_breaks'] += 1 if e['breaks'] else 0
                adj_stats['share_unknown_breaks'] += e.get('share_unknown_breaks', 0)
                if chart_confluence:
                    sd[code]['chart'] = _chart_prep(sd[code]['d'], sd[code]['lo'], sd[code]['c'])
        for code, mktcap in ([] if adjusted_prices else codes):
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0) AS v,
                       COALESCE(high,close) AS h, COALESCE(low,close) AS lo,
                       COALESCE(open,close) AS o
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 90: continue
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
            pnl_, net_pct_ = _net_profit(p['entry'], price, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl_
            _f = sd[code]['f'][i]
            trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day_,
                           'entry': p['entry'], 'exit': price, 'pnl_pct': net_pct_,
                           'reason': reason, 'pnl': round(pnl_, 0),
                           'exit_raw': round(price / _f, 4) if _f else price,
                           'entry_raw': p.get('entry_raw'), 'shares_raw': p.get('shares_raw')})
            del pos[code]
            pending_sells.pop(code, None)

        cash = per_stock * max_positions
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: Dict[str, str] = {}
        pending_buys: List[str] = []

        for day in sim_dates:
            # 전일 종가로 확정된 주문만 다음 거래일 시가에 체결한다.
            for code, reason in list(pending_sells.items()):
                i = didx[code].get(day)
                if i is None or code not in pos:
                    continue
                if adjusted_prices and (sd[code]['v'][i] or 0) <= 0:
                    adj_stats['zero_volume_deferred_sells'] += 1   # 거래 불가능한 날 — 다음 거래일로 이월
                    continue
                fill = sd[code]['o'][i]
                p = pos.pop(code)
                pnl, net_pct = _net_profit(p['entry'], fill, p['shares'], p.get('mkt_cap_억', 300))
                cash += p['shares'] * p['entry'] + pnl
                _t = {'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                      'entry': p['entry'], 'exit': fill, 'pnl_pct': net_pct,
                      'reason': reason, 'pnl': round(pnl, 0)}
                if adjusted_prices:
                    _ff = sd[code]['f'][i]
                    _t.update({'exit_raw': round(fill / _ff, 4) if _ff else fill,
                               'entry_raw': p.get('entry_raw'), 'shares_raw': p.get('shares_raw')})
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
                p['shares'] * (sd[code]['c'][didx[code][day]] if day in didx[code] else p['entry'])
                for code, p in pos.items()
            )
            position_limit = max(max_positions, int(marked_equity // per_stock))
            for code in list(pending_buys):
                i = didx[code].get(day)
                if i is None:
                    continue
                if not _is_tradable_day(code, day):
                    pending_buys.remove(code)
                    continue
                if code not in pos and len(pos) < position_limit:
                    fill = sd[code]['o'][i]
                    budget = min(per_stock, cash * 0.99)
                    if adjusted_prices:
                        if (sd[code]['v'][i] or 0) <= 0 or fill <= 0:
                            adj_stats['zero_volume_skipped_buys'] += 1
                            pending_buys.remove(code); continue
                        _ff = sd[code]['f'][i] or 1.0
                        _rfill = fill / _ff
                        shares_raw = int(budget // _rfill)
                        shares = shares_raw / _ff          # 원주가 기준 정수 주식 → 조정 단위
                        _cost = shares_raw * _rfill
                        _mc = _pit_mc(code, day, _rfill)
                    else:
                        shares = int(budget // fill)
                        _cost = shares * fill
                        _mc = sd[code].get('mkt_cap_억', 300)
                    if shares > 0:
                        cash -= _cost
                        pos[code] = {'entry': fill, 'shares': shares, 'buy_date': day,
                                     'hold': 0, 'peak': fill,
                                     'mkt_cap_억': _mc}
                        trades.append({'code': code, 'buy_date': day, 'entry': fill,
                                       'shares': shares, 'action': 'buy'})
                        if adjusted_prices:
                            pos[code].update({'entry_raw': round(_rfill, 4), 'shares_raw': shares_raw})
                            trades[-1].update({'entry_raw': round(_rfill, 4), 'shares_raw': shares_raw})
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
                peak  = max(p.get('peak', entry), curr)
                p['peak'] = peak
                p['hold'] = p.get('hold', 0) + 1
                ret = (curr - entry) / entry
                tpct = trail_big if ret >= 0.50 else trail
                trail_cond = (curr - peak) / peak < tpct
                stop_cond  = ret < stop
                tp_cond    = ret >= tp
                expire_cond = p['hold'] >= max_hold
                chart_top_cond = False
                if chart_confluence and ret >= 0.10 and not trail_cond:
                    s_ = sd[code]
                    chart_top_cond = _chart_top_confluence(
                        s_['c'], s_['o'], s_['h'], s_['lo'], s_.get('chart'), i) >= _CHART_TOP_MIN
                if stop_cond or trail_cond or tp_cond or expire_cond or chart_top_cond:
                    reason = ('stop' if stop_cond else 'trail' if trail_cond else
                              'tp' if tp_cond else 'chart_top' if chart_top_cond else 'expire')
                    pending_sells[code] = reason

            if len(pos) + len(pending_buys) >= position_limit:
                continue

            # KOSPI 필터
            kma120 = _k_ma120(day)
            if kma120:
                ki = k_idx.get(day)
                if ki is None:
                    for d in reversed(k_dates):
                        if d <= day: ki = k_idx[d]; break
                if ki is not None and k_prices[ki] < kma120 * 0.80:
                    continue

            candidates = []
            for code, s in sd.items():
                if code in pos or code in pending_buys: continue
                i = didx[code].get(day)
                if i is None or i < 80: continue
                if not _is_tradable_day(code, day):
                    continue
                c = s['c']
                v = s['v']
                lo = s['lo']
                curr = c[i]
                _rc = s['raw_c'][i] if adjusted_prices else curr   # 가격 수준·시총은 원주가 기준
                if _rc < 500: continue
                if adjusted_prices:
                    if is_excluded_day(s, day):
                        adj_stats['candidate_skips_excluded'] += 1; continue
                    if day in s['qdays']:
                        adj_stats['candidate_skips_quality_day'] += 1; continue

                # [E] 시총 300억+ (as-of): 신호일 기준 주가×상장주식수
                if asof_mktcap:
                    sh = _shares_asof(code, day)
                    if sh <= 0 or sh * _rc / 1e8 < 300:
                        continue

                # [A] MA60 대비 낙폭 범위
                ma60 = sum(c[max(0,i-59):i+1]) / min(60, i+1)
                if ma60 <= 0: continue
                depth = (curr - ma60) / ma60
                if depth > ma60_depth_min or depth < ma60_depth_max:
                    continue

                # [B] 52주 저점 대비 위치
                p252 = lo[max(0,i-251):i+1]
                low52 = min(p252) if p252 else curr
                if low52 <= 0: continue
                pct_from_low = (curr - low52) / low52 * 100
                if pct_from_low < pct_from_low_min or pct_from_low > pct_from_low_max:
                    continue

                # [C] 거래량 확인
                v_now  = v[i]
                v_avg20 = sum(v[max(0,i-20):i]) / max(1, min(20,i))
                if v_now <= 0 or v_avg20 <= 0 or v_now < v_avg20 * vol_ratio:
                    continue

                # [D] 최근 5일 중 3일 이상 상승
                if i >= 5:
                    up_days = sum(1 for j in range(i-4, i+1) if j > 0 and c[j] > c[j-1])
                    if up_days < 3:
                        continue

                # 복합 점수: 낙폭 깊이 + 저점반등 최적구간 보너스
                depth_score = min(-depth * 100, 55)
                # 최적 구간 30~80% 반등에 보너스
                low_bonus = 12.0 if 30 <= pct_from_low <= 80 else (
                            6.0 if pct_from_low <= 30 else 2.0)
                score = depth_score + low_bonus
                # 바닥 컨플루언스 게이트 (2026-07-18 공통모듈)
                if chart_confluence and _chart_bottom_confluence(
                    s['c'], s['o'], s['h'], s['lo'], s.get('chart'), i) < _CHART_BOTTOM_MIN:
                    continue
                candidates.append((score, code, curr, i))

            candidates.sort(reverse=True)

            available = max(0, position_limit - len(pos) - len(pending_buys))
            pending_buys.extend(code for _, code, _, _ in candidates[:min(3, available)])

        # 최종 청산
        final_val = cash
        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in pos.items():
            last_c, final_reason = _final_liquidation_quote_for_code(conn, code, last_day, didx[code], sd[code]['c'])
            pnl, net_pct = _net_profit(p['entry'], last_c, p['shares'], p.get('mkt_cap_억', 300))
            final_val += p['shares'] * p['entry'] + pnl
            trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                'entry': p['entry'], 'exit': last_c,
                'pnl_pct': net_pct, 'reason': final_reason,
                'pnl': round(pnl, 0),
            })

        init_cap = per_stock * max_positions
        total_ret = (final_val - init_cap) / init_cap * 100
        closed = [t for t in trades if 'sell_date' in t and t.get('reason') != 'buy']
        n_trades = len(closed)
        win_rate = sum(1 for t in closed if t.get('pnl_pct', 0) > 0) / max(n_trades, 1) * 100
        days_held = (datetime.strptime(end_date, '%Y-%m-%d') -
                     datetime.strptime(start_date, '%Y-%m-%d')).days
        ann_ret = ((1 + total_ret / 100) ** (365 / max(days_held, 1)) - 1) * 100

        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, ann_return_pct=?,
                win_rate=?, total_trades=?,
                trades_json=?, summary_text=?
            WHERE run_id=?
        """, (round(total_ret, 2), round(ann_ret, 2),
              round(win_rate, 2), n_trades,
              json.dumps(trades, ensure_ascii=False),
              f"엄격 다음날시가·정수주식·복리 | 총수익 {total_ret:.1f}% | 연환산 {ann_ret:.1f}% | 승률 {win_rate:.0f}% | {n_trades}거래"
              + (f"\n조정가격(W5b): {json.dumps(adj_stats, ensure_ascii=False)}" if adjusted_prices else ""),
              run_id))
        conn.commit()
        _register_execution_artifacts(run_id, init_cap, final_val)
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


