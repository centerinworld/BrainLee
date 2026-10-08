"""
earnings_supply_discovery.py -- run_backtest_earnings_supply_discovery()
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
    DELISTING_CONFIRM_DAYS,
    QUALITY_DAY_CLASSES,
    is_excluded_day,
    last_tradable_day_before_break,
    last_tradable_index_before,
    load_adjusted_prices,
    _load_jump_aligned_corp_factors,
    _rebase_positions_for_corp_actions,
    SignalEvidenceLedger,
    _release_date_with_basis,
    evidence_item,
    DB_PATH,
    _final_liquidation_quote_for_code,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _release_date,
    init_backtest_db,
    logger,
    sqlite3,
)

def run_backtest_earnings_supply_discovery(
    start_date: str,
    end_date: str,
    total_capital: float = 100_000_000,
    max_positions: int = 25,
    op_growth_min: float = 1.0,       # 분기 영업이익 YoY 성장 하한(1.0=100%). Codex 2026-08-11 PIT
                                       # walk-forward 발견(생존편향 제거 데이터셋, 학습/검증 양쪽 lift>1):
                                       # supply_20d_억>=10 & op_growth>=100% -> 10x 2.13%/3x 16.31%/
                                       # 5x 8.51%(3배 기준 목표15% 이미 초과). 개별예측 정밀도가 아니라
                                       # V-MOONSHOT과 동일한 분산+익절없음+넓은손절 포트폴리오로 실전
                                       # 백테스트해 실제 운용수익률을 확인하기 위해 이식.
    supply_min_억: float = 10.0,      # 20일 기관+외국인 순매수 합계 하한(억원)
    min_mktcap_억: float = 300,
    stop_loss: float = -0.35,         # V-MOONSHOT과 동일 설계(변동성 큰 모집단, 조기손절 방지)
    trail_pct: float = -0.35,
    max_hold: int = 500,              # ~2년, 텐버거 중위 도달기간(1.3~1.7년) 고려
    asof_mktcap: bool = True,
    strict_exec: bool = True,
    adjusted_prices: bool = None,  # W5b(REVIEW_PLAN §32-3): None이면 backtest_common.ADJUSTED_PRICES_DEFAULT
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-DISCOVERY — Codex PIT(생존편향 제거) walk-forward 발굴 신호 실전 백테스트.

    [배경] 2026-08-11 Codex가 상장폐지 종목 214개(과거가격 200,586건) 포함한 point-in-time
    데이터셋(strategy_feature_snapshot_pit_v2, 187,543행/2,691종목)으로 재검증한 결과,
    기존 heuristic_score>=55 로직이 모든 검증구간에서 역신호(lift<1.0)로 확인되어 폐기됨.
    대신 발굴된 5개 신호 중 최강(`earnings_demand`: 20일 순매수 10억+ & 영업이익 100%+ 성장)이
    학습/검증 양쪽 lift>1을 유지했으나, "10배 단독 예측 정밀도"(2.13%)는 목표(15%) 미달로
    Codex는 실전 승격을 보류함(research_candidate_only). 단 "3배 기준"으로는 이미 목표 초과
    (16.31%>15%) — 개별 예측기가 아니라 V-MOONSHOT과 같은 분산 포트폴리오(익절없음+넓은손절+
    긴만기)로 운용하면 실제 수익이 날 수 있는지 별도 검증 필요.

    매수: 분기 영업이익 YoY 성장(as-of 공시일 기준) >= op_growth_min
         + 20일 기관+외국인 순매수 합계 >= supply_min_억 — 최대 max_positions종목 분산.
    매도: 손절 stop_loss(하드) / 추적손절 trail_pct(이익권) / 만료 max_hold거래일.
    """
    adjusted_prices = _bc.ADJUSTED_PRICES_DEFAULT if adjusted_prices is None else bool(adjusted_prices)
    # 조정 모드(W5b 공통 규칙): 신호·손익 = 조정 시계열(보유 재기준 대신), 시총 = 원주가, 유니버스 = PIT(폐지 포함),
    # '분할/합병 필터'·'60행 이상'(기간 전체 = 미래 정보) 미사용. 수급(순매수 금액)은 금액이라 조정과 무관.
    if adjusted_prices and not strict_exec:
        raise ValueError("adjusted_prices는 strict_exec(다음날 시가 체결)에서만 지원")
    if adjusted_prices:
        asof_mktcap = True
    adj_stats = {'enabled': adjusted_prices, 'candidate_skips_excluded': 0, 'candidate_skips_quality_day': 0,
                 'break_liquidations': 0, 'break_day_liquidations': 0, 'zero_volume_deferred_sells': 0,
                 'zero_volume_skipped_buys': 0, 'stocks_with_breaks': 0, 'share_unknown_breaks': 0,
                 'delisted_exits': 0, 'unevaluable_delistings': 0, 'delisted_entries': 0, 'pit_mktcap_missing': 0}
    init_backtest_db()
    run_name = run_name or f"V-DISCOVERY {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "earnings_supply_discovery", "earnings_supply_discovery_v1_20260811",
        {"op_growth_min": op_growth_min, "supply_min_억": supply_min_억,
         "min_mktcap_억": min_mktcap_억, "stop_loss": stop_loss, "trail_pct": trail_pct,
         "max_hold": max_hold, "max_positions": max_positions, "asof_mktcap": asof_mktcap,
         "total_capital": total_capital, "start": start_date, "end": end_date,
         "adjusted_prices": True if adjusted_prices else None},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "not_applicable"),
        allocation_rule="diversified_basket",
        **({"universe_version": "security_master_history_v3_pit_delisted"} if adjusted_prices else {}),
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs
          (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'earnings_supply_discovery',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, total_capital / max_positions, max_positions))
    conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d') - timedelta(days=420)).strftime('%Y-%m-%d')
        _pref_pat = re.compile(r"\d?우[A-Z]?$")

        _mktcap_gate = "" if asof_mktcap else "AND COALESCE(market_cap, 0) >= ?"
        _mktcap_param = [] if asof_mktcap else [min_mktcap_억]
        all_rows = conn.execute(f"""
            SELECT stock_code, stock_name, market_cap FROM stock_universe
            WHERE market IN ('유가증권','코스피','코스닥','KOSPI','KOSDAQ')
              {_mktcap_gate}
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """, _mktcap_param).fetchall()
        codes = [r[0] for r in all_rows if not (r[1] and _pref_pat.search(r[1]))]
        if adjusted_prices:
            codes = sorted(set(codes) | {r[0] for r in conn.execute("""
                SELECT DISTINCT sm.stock_code FROM security_master_history sm
                WHERE sm.is_tradable=1 AND sm.is_etf_etn=0 AND sm.market IN ('KOSPI','KOSDAQ')
                  AND (sm.security_type IS NULL OR sm.security_type != 'preferred')
                  AND sm.effective_from <= ? AND (sm.effective_to IS NULL OR sm.effective_to > ?)
                  AND LENGTH(sm.stock_code)=6
                ORDER BY sm.stock_code
            """, (end_date, start_date)).fetchall()})
        mktcap_map = {r[0]: (r[2] or 300) for r in all_rows}
        share_intervals: Dict[str, list] = {}
        if asof_mktcap:
            for code, effective_from, effective_to, shares, quality in conn.execute(
                """SELECT stock_code,effective_from,effective_to,shares_issued,quality
                   FROM security_share_history WHERE stock_code IN ({})
                   ORDER BY stock_code,effective_from""".format(",".join("?" * len(codes))), codes
            ):
                share_intervals.setdefault(code, []).append(
                    (effective_from, effective_to, float(shares or 0), quality)
                )

        def _shares_asof_ed(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        sd: Dict[str, dict] = {}
        if adjusted_prices:
            _codes = sorted({str(c_) for c_ in codes})
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
                    'c': list(e['close']), 'o': list(e['open']), 'h': list(e['high']), 'lo': list(e['low']),
                    'v': list(e['volume']), 'volumes': list(e['volume']),
                    'raw_c': list(e['raw_close']), 'f': list(e['adj_factor']),
                    'breaks': list(e['breaks']), 'excluded_ranges': list(e['excluded_ranges']),
                    'disc': e.get('break_disclosed', {}) or {}, 'qdays': _qdays.get(code, set()),
                    'master_closed_to': e.get('master_closed_to'),
                    'mkt_cap_억': 300,
                }
                adj_stats['stocks_with_breaks'] += 1 if e['breaks'] else 0
                adj_stats['share_unknown_breaks'] += e.get('share_unknown_breaks', 0)
            for code, s_ in sd.items():
                _sup = {str(d)[:10]: float(v or 0) for d, v in conn.execute(
                    "SELECT date, (COALESCE(inst_net_buy_amt,0) + COALESCE(frn_net_buy_amt,0)) / 100.0 FROM price_history "
                    "WHERE stock_code=? AND date>=? AND date<=? AND close>0", (code, warmup_start, end_date)).fetchall()}
                _sl = [_sup.get(d, 0.0) for d in s_['d']]
                _pf = [0.0]
                for v in _sl:
                    _pf.append(_pf[-1] + v)
                s_['supply_20d'] = [_pf[i + 1] - _pf[max(0, i - 19)] for i in range(len(_sl))]
        for code in ([] if adjusted_prices else codes):
            rows = conn.execute("""
                SELECT date, close, COALESCE(open, close) AS o,
                       (COALESCE(inst_net_buy_amt,0) + COALESCE(frn_net_buy_amt,0)) / 100.0 AS supply_억
                FROM price_history
                WHERE stock_code=? AND date>=? AND date<=? AND close>0
                ORDER BY date
            """, (code, warmup_start, end_date)).fetchall()
            if len(rows) < 60:
                continue
            c_list = [float(r[1]) for r in rows]
            if any(c_list[i-1] > 0 and (c_list[i]/c_list[i-1] < 0.45 or c_list[i]/c_list[i-1] > 2.2)
                   for i in range(1, len(c_list))):
                continue
            supply_list = [float(r[3] or 0) for r in rows]
            # 20일 롤링 합계(prefix-sum, O(1) 조회용)
            prefix = [0.0]
            for v in supply_list:
                prefix.append(prefix[-1] + v)
            supply_20d = [
                prefix[i + 1] - prefix[max(0, i - 19)]
                for i in range(len(supply_list))
            ]
            sd[code] = {
                'd': [str(r[0])[:10] for r in rows],
                'c': c_list,
                'o': [float(r[2]) if r[2] and r[2] > 0 else float(r[1]) for r in rows],
                'supply_20d': supply_20d,
                'mkt_cap_억': round(mktcap_map.get(code, 300)) or 300,
            }

        sim_dates = sorted(set(d for s in sd.values() for d in s['d'] if start_date <= d <= end_date))
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
            sh_ = _shares_asof_ed(code, day_)
            if sh_ > 0:
                return sh_ * raw_px / 1e8
            adj_stats['pit_mktcap_missing'] += 1
            return 1.0

        def _close_now(code, p, i, price, reason, day_):
            """조정 단위 `price`로 즉시 청산(단절·폐지 처리)."""
            nonlocal cash, pending_sells
            pnl_, net_pct_ = _net_profit(p['entry'], price, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl_
            _f = sd[code]['f'][i]
            trades.append({'code': code, 'buy_date': p['buy_date'], 'sell_date': day_,
                           'entry': p['entry'], 'exit': price, 'pnl_pct': net_pct_,
                           'reason': reason, 'pnl': round(pnl_, 0),
                           'exit_raw': round(price / _f, 4) if _f else price,
                           'entry_raw': p.get('entry_raw'), 'shares_raw': p.get('shares_raw')})
            del pos[code]
            pending_sells = [(c_, r_) for c_, r_ in pending_sells if c_ != code]

        if not sd:
            raise RuntimeError("유니버스가 비어있음(가격이력 부족)")

        # 분기 영업이익 YoY 성장(as-of 공시일 기준) — Codex PIT 연구와 동일 정의
        overrides = {r[0]: r[1] for r in conn.execute(
            "SELECT stock_code, config_value FROM stock_collection_config "
            "WHERE config_key='preferred_report_type'")}
        raw_rows = conn.execute("""
            SELECT stock_code, year, quarter, report_type, operating_profit, id, created_at, updated_at
            FROM financial_data
            WHERE is_annual=0 AND quarter BETWEEN 1 AND 4 AND operating_profit IS NOT NULL
              AND stock_code IN ({})
            ORDER BY stock_code, year, quarter
        """.format(",".join("?" * len(sd))), list(sd.keys())).fetchall()
        by_quarter: Dict[tuple, dict] = {}
        for r in raw_rows:
            key = (r[0], r[1], r[2])
            by_quarter.setdefault(key, {})[r[3]] = r
        panel: Dict[str, list] = {}
        for (code, y, q), variants in by_quarter.items():
            pref = overrides.get(code, "CFS")
            r_op = variants.get(pref) or next(iter(variants.values()))
            panel.setdefault(code, []).append((y, q, r_op[4], r_op))
        for code in panel:
            panel[code].sort(key=lambda x: (x[0], x[1]))

        def _avail_date(y: int, q: int, code: str = None) -> str:
            # 공용 _release_date() 재사용 — 실제 DART 공시일(fin_disclosure_dates) 우선,
            # 없으면 법정기한(분기+45일 근사) fallback. 과거 하드코딩 공식만 쓰던 버그 수정(2026-08-30).
            return _release_date(y, q, False, code)

        # 종목별 (avail_date, op_growth) 이벤트 리스트
        growth_events: Dict[str, list] = {}
        for code, qs in panel.items():
            n = len(qs)
            for i in range(4, n):
                y, q, op, row_now = qs[i]
                op_prev, row_prev = qs[i - 4][2], qs[i - 4][3]
                if op is None or op_prev is None or op_prev <= 0:
                    continue
                growth = op / op_prev - 1.0
                if not (-5 <= growth <= 10):  # PIT 연구와 동일 이상치 제외
                    continue
                avail = _avail_date(y, q, code)
                growth_events.setdefault(code, []).append((avail, growth, (row_now, row_prev)))
        for code in growth_events:
            growth_events[code].sort(key=lambda e: (e[0], e[1]))

        evidence = SignalEvidenceLedger("earnings_supply_discovery", {
            "op_growth_min": op_growth_min, "supply_min_억": supply_min_억,
            "report_type": "preferred(stock_collection_config) else CFS", "lag_quarters": 4,
        })

        def _note_evidence(code: str, day: str) -> None:
            evs = [e for e in growth_events.get(code, ()) if e[0] <= day]
            items = []
            if evs:
                for role, row in zip(("current", "year_ago"), evs[-1][2]):
                    avail, basis = _release_date_with_basis(row[1], row[2], False, code)
                    items.append(evidence_item(
                        "financial_data", row[5], avail, basis=basis,
                        source_key=f"{row[1]}Q{row[2]}:{row[3]}:{role}",
                        value={"operating_profit": row[4], "growth": evs[-1][1]},
                        collected_at=row[6], modified_at=row[7],
                    ))
            evidence.note(code, day, items or [evidence_item("financial_data", None, None)])

        def _current_growth(code: str, day: str):
            evs = growth_events.get(code)
            if not evs:
                return None
            avail = [e for e in evs if e[0] <= day]
            if not avail:
                return None
            return avail[-1][1]

        per_stock = total_capital / max_positions
        cash = total_capital
        pos: Dict[str, dict] = {}
        trades = []
        pending_sells: list = []
        pending_buys: list = []

        # 2026-09-28: 보유 중 확정 기업행위(권리락·분할 등) 날 포지션을 새 주식 기준으로 재기준
        _ca_factors = {} if adjusted_prices else _load_jump_aligned_corp_factors(conn, list(sd.keys()))
        _ca_prev_day = None
        for day in sim_dates:
            _rebase_positions_for_corp_actions(_ca_factors, pos, _ca_prev_day, day, ('entry', 'peak'), 'shares')
            _ca_prev_day = day
            if strict_exec:
                _still = []
                for code, reason in pending_sells:
                    if code not in pos:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        _still.append((code, reason)); continue
                    px = sd[code]['o'][i]
                    if px <= 0:
                        _still.append((code, reason)); continue
                    if adjusted_prices and (sd[code]['v'][i] or 0) <= 0:
                        adj_stats['zero_volume_deferred_sells'] += 1   # 거래 불가능한 날 — 다음 거래일로 이월
                        _still.append((code, reason)); continue
                    p = pos.pop(code)
                    pnl, net_pct = _net_profit(p['entry'], px, p['shares'], p.get('mkt_cap_억', 300))
                    cash += p['shares'] * p['entry'] + pnl
                    trades.append({
                        'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                        'entry': p['entry'], 'exit': px,
                        'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                    })
                    if adjusted_prices:
                        _ff = sd[code]['f'][i]
                        trades[-1].update({'exit_raw': round(px / _ff, 4) if _ff else px,
                                           'entry_raw': p.get('entry_raw'), 'shares_raw': p.get('shares_raw')})
                pending_sells = _still
                if adjusted_prices:
                    for code, p in list(pos.items()):
                        i = didx[code].get(day)
                        if i is not None and i > 0 and day in sd[code]['breaks']:
                            _k = last_tradable_index_before(sd[code], i)
                            _k = i - 1 if _k is None else _k
                            _close_now(code, p, i, sd[code]['c'][_k], '단절 당일 청산(공시 근거 없음·평가 불가)', day)
                            trades[-1]['evaluation'] = 'unevaluable_break'; trades[-1]['basis_date'] = sd[code]['d'][_k]
                            adj_stats['break_day_liquidations'] += 1

                for code in pending_buys:
                    if code in pos or len(pos) >= max_positions:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    px = sd[code]['o'][i]
                    if adjusted_prices:
                        if is_excluded_day(sd[code], day):   # 계수 미확정 단절 제외 구간(단절 당일 포함)에는 체결도 하지 않는다(D12 ②)
                            adj_stats['excluded_fill_cancels'] = adj_stats.get('excluded_fill_cancels', 0) + 1
                            continue
                        if px <= 0 or (sd[code]['v'][i] or 0) <= 0:
                            adj_stats['zero_volume_skipped_buys'] += 1
                            continue
                        _ff = sd[code]['f'][i] or 1.0
                        _rpx = px / _ff
                        if cash < _rpx * 10:
                            continue
                        budget = min(per_stock, cash * 0.99)
                        shares_raw = int(budget // _rpx)
                        if shares_raw <= 0:
                            continue
                        cash -= shares_raw * _rpx
                        pos[code] = {'entry': px, 'shares': shares_raw / _ff, 'buy_date': day, 'hold': 0,
                                     'peak': px, 'mkt_cap_억': _pit_mc(code, day, _rpx),
                                     'entry_raw': round(_rpx, 4), 'shares_raw': shares_raw}
                        if sd[code].get('ends_before_period_end'):
                            adj_stats['delisted_entries'] += 1
                        continue
                    if px <= 0 or cash < px * 10:
                        continue
                    budget = min(per_stock, cash * 0.99)
                    shares = int(budget // px)
                    if shares <= 0:
                        continue
                    cash -= shares * px
                    pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                 'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}
                pending_buys = []

            for code, p in list(pos.items()):
                i = didx[code].get(day)
                if i is None:
                    continue
                if adjusted_prices:
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
                curr = sd[code]['c'][i]
                if curr <= 0:
                    continue
                p['hold'] += 1
                p['peak'] = max(p.get('peak', p['entry']), curr)
                ret = curr / p['entry'] - 1
                stop_cond = ret <= stop_loss
                expire_cond = p['hold'] >= max_hold
                trail_cond = trail_pct is not None and ret > 0 and (curr - p['peak']) / p['peak'] < trail_pct
                if stop_cond or expire_cond or trail_cond:
                    reason = 'stop' if stop_cond else 'trail' if trail_cond else 'expire'
                    if strict_exec:
                        if code not in [c for c, _ in pending_sells]:
                            pending_sells.append((code, reason))
                    else:
                        pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
                        cash += p['shares'] * p['entry'] + pnl
                        trades.append({
                            'code': code, 'buy_date': p['buy_date'], 'sell_date': day,
                            'entry': p['entry'], 'exit': curr,
                            'pnl_pct': net_pct, 'reason': reason, 'pnl': round(pnl, 0),
                        })
                        pos.pop(code, None)

            if len(pos) + len(pending_buys) < max_positions:
                candidates = []
                pending_codes = set(pending_buys) if strict_exec else set()
                for code in sd:
                    if code in pos or code in pending_codes:
                        continue
                    i = didx[code].get(day)
                    if i is None:
                        continue
                    curr = sd[code]['c'][i]
                    if curr <= 0:
                        continue
                    _rc = sd[code]['raw_c'][i] if adjusted_prices else curr   # 시총은 원주가 기준
                    if adjusted_prices:
                        if is_excluded_day(sd[code], day):
                            adj_stats['candidate_skips_excluded'] += 1; continue
                        if day in sd[code]['qdays']:
                            adj_stats['candidate_skips_quality_day'] += 1; continue
                    if asof_mktcap:
                        _sh = _shares_asof_ed(code, day)
                        if _sh <= 0 or _sh * _rc / 1e8 < min_mktcap_억:
                            continue
                    growth = _current_growth(code, day)
                    if growth is None or growth < op_growth_min:
                        continue
                    supply_now = sd[code]['supply_20d'][i]
                    if supply_now < supply_min_억:
                        continue
                    candidates.append((growth, code))
                candidates.sort(reverse=True)
                slots = max_positions - len(pos) - len(pending_codes)
                picked = candidates[:slots]
                if strict_exec:
                    for _, code in picked:
                        pending_buys.append(code)
                        _note_evidence(code, day)
                else:
                    for _, code in picked:
                        i = didx[code].get(day)
                        px = sd[code]['c'][i]
                        budget = min(per_stock, cash * 0.99)
                        shares = int(budget // px)
                        if shares <= 0 or cash < px * 10:
                            continue
                        cash -= shares * px
                        pos[code] = {'entry': px, 'shares': shares, 'buy_date': day, 'hold': 0,
                                     'peak': px, 'mkt_cap_억': sd[code]['mkt_cap_억']}

        last_day = sim_dates[-1] if sim_dates else end_date
        for code, p in list(pos.items()):
            curr, final_reason = _final_liquidation_quote_for_code(conn, code, last_day, didx[code], sd[code]['c'])
            pnl, net_pct = _net_profit(p['entry'], curr, p['shares'], p.get('mkt_cap_억', 300))
            cash += p['shares'] * p['entry'] + pnl
            trades.append({
                'code': code, 'buy_date': p['buy_date'], 'sell_date': last_day,
                'entry': p['entry'], 'exit': curr,
                'pnl_pct': net_pct, 'reason': final_reason, 'pnl': round(pnl, 0),
            })

        name_map = {}
        all_codes = list({t['code'] for t in trades})
        for i in range(0, len(all_codes), 400):
            batch = all_codes[i:i + 400]
            ph = ",".join("?" * len(batch))
            for sc, sn in conn.execute(
                f"SELECT stock_code, stock_name FROM stock_universe WHERE stock_code IN ({ph})", batch
            ):
                name_map[sc] = sn
        for t in trades:
            t['stock_name'] = name_map.get(t['code'], t['code'])

        total_return = (cash - total_capital) / total_capital * 100
        win_trades = sum(1 for t in trades if t['pnl'] > 0)
        win_rate = (win_trades / len(trades) * 100) if trades else 0.0
        summary_text = (
            f"기간: {start_date} ~ {end_date}\n"
            f"★ V-DISCOVERY: op_growth>={op_growth_min*100:.0f}% + supply20d>={supply_min_억}억 / "
            f"손절{stop_loss*100:.0f}% / trail{trail_pct*100:.0f}% / 만기{max_hold}거래일\n"
            f"총 거래: {len(trades)}건  승률: {win_rate:.1f}%  총수익률: {total_return:.2f}%"
            + (f"\n조정가격(W5b): {json.dumps(adj_stats, ensure_ascii=False)}" if adjusted_prices else "")
        )
        conn.execute("""
            UPDATE backtest_runs
            SET status='done', total_return_pct=?, win_rate=?, total_trades=?,
                profit_trades=?, trades_json=?, summary_text=?
            WHERE run_id=?
        """, (
            round(total_return, 2), round(win_rate, 2), len(trades), win_trades,
            json.dumps({"trades": trades}, ensure_ascii=False), summary_text, run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, total_capital, cash, asof_mktcap=asof_mktcap)
        evidence.persist(run_id, trades)
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



