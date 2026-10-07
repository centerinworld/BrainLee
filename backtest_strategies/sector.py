"""
sector.py -- run_backtest_sector()
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
    QUALITY_DAY_CLASSES,
    is_excluded_day,
    last_tradable_day_before_break,
    last_tradable_index_before,
    load_adjusted_prices,
    _load_jump_aligned_corp_factors,
    _rebase_positions_for_corp_actions,
    SignalEvidenceLedger,
    _release_date_with_basis,
    evidence_aggregate,
    DB_PATH,
    _SECTOR_GROUPS,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    _load_disc_dates,
    _release_date,
    init_backtest_db,
    logger,
    sqlite3,
)


def _pit_gated_sector_op_yoy_median(conn, codes: list, calendar_year_cap: int, trade_date: str) -> float:
    """섹터 내 종목들의 영업이익 YoY 중위값 -- F01 (2026-09-12) 실제 공시일 게이팅.

    trade_date 기준으로 실제 공시(fin_disclosure_dates, 없으면 법정기한 익년3/31)돼 있던
    가장 최근 연간 실적만 사용한다. 종목별로 독립 판정하므로 섹터 내 일부 종목이 아직
    이전 연도만 공시된 상태여도 배제되지 않는다(2026-09-07 버그 수정의 부수효과 유지).
    report_type(CFS/OFS) tiebreak: CFS 우선, 종목당 연도별 1행만.

    별도 함수로 분리한 이유(재개 우선순위4, Codex 재검토): 이 로직을 회귀테스트가 실제
    실행경로로 검증할 수 있어야 한다 — 테스트 안에 로직을 복제하면 운영 코드의 게이트가
    제거돼도 테스트가 계속 통과할 수 있다.
    """
    if not codes:
        return 0.0
    ph = "({})".format(",".join("?" * len(codes)))
    rows = conn.execute(
        f"SELECT stock_code, year, operating_profit FROM ("
        f"  SELECT stock_code, year, operating_profit,"
        f"         ROW_NUMBER() OVER ("
        f"             PARTITION BY stock_code, year"
        f"             ORDER BY CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END"
        f"         ) AS rt_rn"
        f"  FROM financial_data"
        f"  WHERE stock_code IN {ph} AND is_annual=1 AND operating_profit IS NOT NULL"
        f"    AND year<=?"
        f") dedup WHERE rt_rn=1",
        list(codes) + [calendar_year_cap],
    ).fetchall()
    by_code_year: dict = {}
    for code, year, op in rows:
        by_code_year.setdefault(code, {})[int(year)] = op
    yoys = []
    for code, year_map in by_code_year.items():
        avail_years = sorted(
            (y for y in year_map if _release_date(y, 4, True, code) <= trade_date),
            reverse=True,
        )
        if not avail_years:
            continue
        cur_y = avail_years[0]
        op_c = year_map.get(cur_y)
        op_p = year_map.get(cur_y - 1)
        if op_p and op_p != 0:
            raw = (op_c - op_p) / abs(op_p) * 100
            yoys.append(min(max(raw, -200), 2000))
    return sorted(yoys)[len(yoys) // 2] if yoys else 0.0

def _sector_op_yoy_inputs(conn, codes: list, calendar_year_cap: int, trade_date: str) -> dict:
    """_pit_gated_sector_op_yoy_median()이 trade_date에 읽는 연간 재무 행(종목별 cur_y, cur_y-1)."""
    if not codes:
        return evidence_aggregate("sector_financial_data", [], source_key="empty_sector")
    ph = "({})".format(",".join("?" * len(codes)))
    rows = conn.execute(
        f"SELECT stock_code, year, operating_profit, id, report_type, created_at, updated_at FROM ("
        f"  SELECT stock_code, year, operating_profit, id, report_type, created_at, updated_at,"
        f"         ROW_NUMBER() OVER ("
        f"             PARTITION BY stock_code, year"
        f"             ORDER BY CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END"
        f"         ) AS rt_rn"
        f"  FROM financial_data"
        f"  WHERE stock_code IN {ph} AND is_annual=1 AND operating_profit IS NOT NULL"
        f"    AND year<=?"
        f") dedup WHERE rt_rn=1",
        list(codes) + [calendar_year_cap],
    ).fetchall()
    by_code_year: dict = {}
    for code, year, op, row_id, rtype, created, updated in rows:
        by_code_year.setdefault(code, {})[int(year)] = (op, row_id, rtype, created, updated)
    used, estimated = [], False
    for code, year_map in by_code_year.items():
        avail_years = sorted(
            (y for y in year_map if _release_date(y, 4, True, code) <= trade_date), reverse=True)
        if not avail_years:
            continue
        for y in (avail_years[0], avail_years[0] - 1):
            if y not in year_map:
                continue
            op, row_id, rtype, created, updated = year_map[y]
            avail, basis = _release_date_with_basis(y, 4, True, code)
            estimated = estimated or basis == "statutory_estimate"
            used.append({"row_id": row_id, "available_at": avail,
                         "value": {"stock_code": code, "year": y, "report_type": rtype,
                                   "operating_profit": op, "basis": basis,
                                   "created_at": str(created) if created else None,
                                   "updated_at": str(updated) if updated else None}})
    item = evidence_aggregate("sector_financial_data", used, source_key=f"annual_op_yoy_median:{trade_date}")
    if used:
        item["availability_basis"] = "statutory_estimate" if estimated else "actual_disclosure"
    return item


def run_backtest_sector(
    start_date: str, end_date: str,
    buy_threshold: float = 55.0,    # 섹터 BUY 기준 점수
    exit_threshold: float = 30.0,   # 섹터 EXIT 기준 점수
    rebalance_days: int = 22,        # 월 1회 리밸런싱 (22 영업일)
    per_stock: float = 10_000_000,
    max_positions: int = 9,          # 최대 3섹터 × 3종목
    stop: float = -0.12,
    trail: float = -0.30,  # 2026-07-21 -0.20→-0.30: 연속운용(2020-03~2026-03) 227.89%→245.02%, 승률46.3→46.7%, 거래175→165건(조기청산 감소)
    tp: float = 0.50,
    min_sector_hold_days: int = 44,   # 섹터 점수 재계산 후 하락해도 최소 2개월은 보유
    pick_ta_bonus: float = None,      # 리더 선정 점수에 직전 공시분기 첫 흑자전환 보너스 (예: 20.0, 실험용)
    # 2026-08-09 실험(사용자 제안): 유휴자본 문제를 티켓크기/컴포넌트수로 풀려던 시도가
    # 전부 실패(동점경쟁 불안정성/슬롯감소, ledger 'ticket_pct_reverify_after_normalization_20260809')한 뒤
    # 방향 전환 — 신규 슬롯 경쟁이 아니라 "이미 보유한 포지션의 확신도(섹터점수)가 매수
    # 시점보다 오를 때만" 추가 투입. 슬롯 경쟁 메커니즘 자체를 건드리지 않아 동점
    # 타이브레이크 불안정성과 무관(구조적으로 다른 메커니즘).
    pyramid_score_gain: float = None,     # 예: 15.0 — 진입시점 섹터점수 대비 +N점 오르면 추가매수
    pyramid_add_pct: float = 0.5,         # 추가매수 규모(기존 티켓 대비 비율, 기본 0.5=절반 티켓)
    pyramid_max_adds: int = 2,            # 포지션당 최대 추가매수 횟수(무한 물타기 방지)
    # 2026-08-23: 전체 price_history 스캔에서 214개 거래일·1,267건의 단일일 스파이크(익일
    # 원상복귀) 데이터 아티팩트 발견(2022-01-03 하루 254종목 동시발생 등, 데이터 수집/정합성
    # 문제로 강하게 의심). 매수후보 3M모멘텀 계산 시점 직전에 이런 아티팩트가 있으면 후보에서
    # 제외하는 실험 파라미터 — 기본 False(기존 동작 완전 동일), 실측 검증 후 채택 여부 결정.
    avoid_discontinuity: bool = False,
    asof_mktcap: bool = True,
    # 실험 #1 (2026-09-12, docs/claude_handoff_strategy_code_findings_20260912.md
    # "PIT로 보정한 섹터 내 선별") — 전부 기본 False(기존 동작과 완전 동일), opt-in.
    # 셋 다 F01이 이미 정착시킨 실제 공시일 게이팅(_release_date, 룩어헤드 없음) 위에서
    # 계산한다.
    use_earnings_abs_bonus: bool = False,        # 영업이익 절대 개선액(시총 대비 정규화)
    use_disclosure_freshness_bonus: bool = False,  # 사용한 연간실적의 공시 신선도
    use_cashflow_confirm_gate: bool = False,     # 영업현금흐름 확인(회계상 개선만 있고 현금 미동반 시 감점)
    # 실험 #2 (2026-09-12): 승자 보유와 매도 이후 재진입 -- 기존 익절(+tp)은 전량 매도.
    # partial_tp_pct(예: 0.5)를 주면 tp 최초 도달 시 그 비율만 실현하고 나머지는 손절/
    # 추적손절만으로 계속 보유(포지션당 1회만 적용). None(기본)이면 기존 동작과 완전히 동일.
    partial_tp_pct: float | None = None,
    # 재개 우선순위1 (2026-09-12, Codex 재검토): 비용 2배 스트레스 검증을 위해 비용률만
    # 배율로 노출한다. 1.0(기본)이면 기존 동작과 완전히 동일 — _net_profit()/_tx_cost()
    # 자체(다른 전략 다수가 공유)는 건드리지 않고, 이 함수 내부에서 그 결과의 비용
    # 부분만 사후 스케일링한다(gross-net=cost, cost*multiplier로 재계산).
    cost_multiplier: float = 1.0,
    # TASK_PARTIAL_TP_P3 (2026-09-13, Gemini 고차원 검증): 단일 초대형 종목(에코프로 등)
    # 편중성 분해 및 비편중 유니버스 검증용 종목 제외 파라미터. 기본 None(전체 대상).
    exclude_codes: list[str] | tuple[str, ...] | None = None,
    adjusted_prices: bool = None,     # W3: None이면 backtest_common.ADJUSTED_PRICES_DEFAULT
    strict_exec: bool = True,         # 2026-07-13 기본화 (Codex 계약): D종가 신호 → D+1 시가 체결.
                                      # 검증: same_close avg6 +29.2%(5/6) → next_open +31.4%(5/6) — 전략 유효성 유지.
    run_name: str = None,
    run_id: str = None,
) -> str:
    """
    V-SECTOR: 섹터 로테이션 집중 투자 전략.
    - 월 1회 섹터 스코어 계산 → BUY 섹터 발굴
    - BUY 섹터 내 급등점수 TOP3 종목 집중 매수
    - 손절 -12%, 추적손절 -30%(2026-07-21 -20%→-30%), 익절 +50%
    - 섹터 점수가 EXIT 이하로 하락해도 최소 보유기간 전에는 섹터 청산 보류
    """
    init_backtest_db()
    adjusted_prices = _bc.ADJUSTED_PRICES_DEFAULT if adjusted_prices is None else bool(adjusted_prices)
    adj_stats = {'enabled': adjusted_prices, 'candidate_skips_excluded': 0, 'candidate_skips_quality_day': 0,
                 'break_liquidations': 0, 'break_day_liquidations': 0, 'zero_volume_deferred_sells': 0,
                 'zero_volume_skipped_buys': 0, 'share_unknown_breaks': 0, 'misaligned_skipped': 0, 'stocks_with_breaks': 0}
    run_name = run_name or f"V-SECTOR섹터집중 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "sector_focus", "sector_v3_cashledger_20260714",
        {"buy_threshold": buy_threshold, "exit_threshold": exit_threshold,
         "rebalance_days": rebalance_days, "stop": stop, "trail": trail, "tp": tp,
         "min_sector_hold_days": min_sector_hold_days, "strict_exec": strict_exec,
         "per_stock": per_stock, "max_positions": max_positions,
         "asof_mktcap": asof_mktcap,
         "adjusted_prices": True if adjusted_prices else None,
         "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode=("asof_approx" if asof_mktcap else "current"),
        # 계산에 쓰이는 시총을 security_share_history 기반 정확한 as-of 값으로 교체(6기간
        # 재검증 avg6 29.98%→27.62%, 5/6양수 유지 — 소폭변동, 일부기간 오히려 개선).
        # ⚠️ 단, _SECTOR_GROUPS 자체(10업종 70종목 후보군)는 여전히 현재시점 수동선정이라
        # "pit"(완전 PIT) 등급까지는 도달 불가 — approx로 정직하게 표기.
        allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
    # F01 fix (2026-09-12, docs/claude_handoff_strategy_code_findings_20260912.md):
    # 아래 OP YoY 컴포넌트가 "실제 공시 시점"이 아니라 "회계연도<=거래일 달력연도"만으로
    # 연간 실적을 선택해 미공개 실적을 참조할 수 있었다(재현: 2024-01-05 결정일에
    # 2024년 연간 실적이 이미 완결된 DB에서는 선택 가능 — 실제로는 2025년 3월에야 공시됨).
    # 다른 전략(v2/turnaround 등)이 이미 쓰는 fin_disclosure_dates 기반 실제 공시일(없으면
    # 법정기한 익년3/31) 인프라를 동일하게 재사용한다.
    _load_disc_dates(conn)
    conn.execute("""
        INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
        VALUES (?,?,'sector_focus',?,?,?,?,'running')
    """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
    conn.execute("""
        UPDATE backtest_runs
        SET name=?, strategy='sector_focus', start_date=?, end_date=?, per_stock=?, max_pos=?, status='running'
        WHERE run_id=?
    """, (run_name, start_date, end_date, per_stock, max_positions, run_id))
    conn.commit()

    try:
        # KOSPI 데이터
        kospi_rows = conn.execute(
            "SELECT date, close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date"
        ).fetchall()
        k_dates  = [r[0] for r in kospi_rows]
        k_prices = {r[0]: float(r[1]) for r in kospi_rows}

        # 모든 섹터 후보 종목 모음 (가격 데이터 로드용)
        all_codes = list(set(c for info in _SECTOR_GROUPS.values() for c in info["codes"]))

        # 가격 데이터 로드
        # F08 fix (2026-09-12, docs/claude_handoff_strategy_code_findings_20260912.md):
        # 종목 RS(3개월/1개월 모멘텀)가 trade_date로부터 92~99일 전 가격을 찾는데,
        # 가격을 start_date 이후만 로드하면 백테스트 시작 직후(대략 첫 3개월) 구간에서는
        # DB에 실제 데이터가 있어도 이 price_data 딕셔너리 안에는 없어 RS 산출이 불가능
        # 했다(성과측정 시작일과 지표 워밍업 시작일이 뒤섞여 있던 문제). RS lookback
        # 최대치(99일)보다 넉넉한 warmup_buffer_days만큼 더 이전부터 가격을 로드하되,
        # trade_dates(실제 매매·성과측정 대상일)는 아래에서 그대로 start_date 이후로만
        # 필터링해 워밍업 구간이 성과에 포함되지 않도록 분리한다.
        warmup_buffer_days = 110
        price_load_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=warmup_buffer_days)).strftime("%Y-%m-%d")
        price_data: dict = {}  # code → {date: (close, high, low)}
        rows_p = conn.execute(
            "SELECT stock_code, date, close, high, low, open FROM price_history "
            "WHERE stock_code IN ({}) AND date>=? AND date<=? AND close>0 ORDER BY date".format(
                ",".join("?" * len(all_codes))),
            all_codes + [price_load_start, end_date]
        ).fetchall()
        for r in rows_p:
            c, d, cl, hi, lo, op = r
            if c not in price_data:
                price_data[c] = {}
            # F07 fix (2026-09-12, docs/claude_handoff_strategy_code_findings_20260912.md):
            # 시가 결측(NULL/0)을 그 자리에서 종가로 대체해두면, 아래 strict_exec 체결
            # 로직이 "그날 진짜 시가로 체결했다"고 오인한다(재현 위험: 실제로는 존재하지
            # 않는 시가에 매수/매도한 것처럼 기록됨). 결측이면 None을 그대로 보존하고,
            # 체결 시점(아래 sec_pending_buys/sells 처리부)에서 None을 "당일 미거래"와
            # 동일하게 취급해 대기(매도)/만료(매수)시킨다 — 평가용 종가(index 0)는 이
            # 대체와 무관하게 항상 실제 값을 쓴다.
            price_data[c][d] = (float(cl), float(hi) if hi else float(cl), float(lo) if lo else float(cl),
                                float(op) if op and op > 0 else None)

        # 영업일 목록
        trade_dates = sorted(set(r[1] for r in rows_p if r[1] >= start_date))

        # W3(Stock_Strategy, D12 ②): 조정 가격 로더 — 신호·손익은 조정 시계열, 체결 수량·시총은 원주가.
        # ⚠️ 후보군 `_SECTOR_GROUPS`(수동 선정 70종목)의 생존 편향은 이 이관으로 해결되지 않는다.
        adjf: Dict[str, Dict[str, float]] = {}      # code → {date: 조정 계수}
        adj_e: Dict[str, dict] = {}                 # code → 로더 항목(breaks·excluded_ranges·break_disclosed·volume)
        adj_qdays: Dict[str, set] = {}
        adj_dix: Dict[str, Dict[str, int]] = {}
        if adjusted_prices and price_data:
            _ap = load_adjusted_prices(conn, list(price_data.keys()), price_load_start, end_date)
            _qi = research_price_issues(conn, list(price_data.keys()), price_load_start, end_date, allow_confirmed_corporate_actions=True)
            for _qc, _qd, _qcls in _qi:
                if _qcls in QUALITY_DAY_CLASSES:
                    adj_qdays.setdefault(str(_qc), set()).add(str(_qd)[:10])
            for code in list(price_data.keys()):
                e = _ap.get(code)
                if not e or set(e['dates']) != set(price_data[code].keys()):
                    adj_stats['misaligned_skipped'] += 1
                    continue
                fmap = dict(zip(e['dates'], e['adj_factor']))
                adjf[code] = fmap
                price_data[code] = {d: (v[0] * fmap[d], v[1] * fmap[d], v[2] * fmap[d], (v[3] * fmap[d]) if v[3] is not None else None)
                                    for d, v in price_data[code].items()}
                adj_e[code] = e
                adj_dix[code] = {d: k for k, d in enumerate(e['dates'])}
                adj_stats['stocks_with_breaks'] += 1 if e['breaks'] else 0
                adj_stats['share_unknown_breaks'] += e.get('share_unknown_breaks', 0)

        def _af(code: str, day: str) -> float:
            return adjf.get(code, {}).get(day, 1.0) or 1.0

        def _buy_size(code: str, day: str, px: float, budget: float):
            """(조정 단위 수량, 원가). 조정 모드는 원주가 기준 정수 주식 → 조정 단위(수량 ÷ 계수)로 환산."""
            if adjusted_prices:
                f = _af(code, day)
                raw_px = px / f
                qty_raw = int(budget / raw_px)
                return qty_raw / f, qty_raw * raw_px, qty_raw, raw_px
            q = int(budget / px)
            return q, q * px, q, px

        # 시총 맵 (거래비용 슬리피지 티어용)
        mc_map = {}
        if not asof_mktcap:
            mc_map = {r[0]: float(r[1] or 1000) for r in conn.execute(
                "SELECT stock_code, market_cap FROM stock_universe WHERE stock_code IN ({})".format(
                    ",".join("?" * len(all_codes))), all_codes).fetchall()}

        # 2026-08-13: 섹터 리더 선정(sel_score)의 기관집중도 계산이 stock_universe.
        # market_cap(현재시총)을 그대로 쓰고 있었음 — _SECTOR_GROUPS 후보군 자체는
        # 여전히 현재시점 수동선정이라 완전한 PIT화는 불가능(2026-07-21/2026-08-12
        # 기존 판정)하지만, 리더 "선정 스코어" 계산에 쓰이는 시총만큼은 정확한
        # as-of 값(security_share_history)으로 교체 가능 — 부분 개선 시도.
        sector_share_intervals: Dict[str, list] = {}
        for code, effective_from, effective_to, shares, quality in conn.execute(
            """SELECT stock_code,effective_from,effective_to,shares_issued,quality
               FROM security_share_history WHERE stock_code IN ({})
               ORDER BY stock_code,effective_from""".format(",".join("?" * len(all_codes))), all_codes
        ):
            sector_share_intervals.setdefault(code, []).append(
                (effective_from, effective_to, float(shares or 0), quality)
            )

        def _shares_asof_sector(code: str, day: str) -> float:
            for effective_from, effective_to, shares, _q in reversed(sector_share_intervals.get(code, [])):
                if effective_from <= day and (effective_to is None or day < effective_to):
                    return shares
            return 0.0

        def _cost_mktcap(code: str, day: str, price: float) -> float:
            if adjusted_prices:
                price = price / (adjf.get(code, {}).get(day, 1.0) or 1.0)   # 시총은 원주가 기준
            if asof_mktcap:
                shares = _shares_asof_sector(code, day)
                return shares * price / 100_000_000 if shares > 0 else 1000.0
            return mc_map.get(code, 1000.0)

        def _net_profit_scaled(entry_p: float, exit_p: float, qty: int, mkt_cap_억: float) -> tuple:
            """_net_profit()의 비용(수수료+세금+슬리피지)만 cost_multiplier배로 스케일.
            cost_multiplier=1.0(기본)이면 _net_profit()과 완전히 동일한 값을 반환한다."""
            net_krw, net_pct = _net_profit(entry_p, exit_p, qty, mkt_cap_억)
            if cost_multiplier == 1.0:
                return net_krw, net_pct
            gross = (exit_p - entry_p) * qty
            cost = gross - net_krw
            scaled_net = gross - cost * cost_multiplier
            base = entry_p * qty
            return round(scaled_net), round((scaled_net / base) * 100, 2) if base else 0.0

        # 포지션 관리
        # positions: dict[code] → {buy_price, peak, sector, qty}
        positions: dict = {}
        # C1 (2026-07-14, Codex 필수점검): 실현손익 누산기 → 실제 현금원장으로 전환.
        # 1억원 시작, 매수 시 현금 차감(부족 시 주문 거부), 매도 시 원금+순손익(_net_profit:
        # 수수료+거래세+슬리피지 차감) 환입. 수익률 = (최종에쿼티/1억 - 1).
        initial_cash = per_stock * max_positions
        cash = initial_cash
        holding_value = 0.0
        all_trades: list = []
        sector_assignments: dict = {}  # code → sector_key (현재 보유 섹터)

        last_rebalance = ""
        sector_scores_cache: dict = {}  # date → {sector_key: score}
        sector_momentum_cache: dict = {}  # date → {sector_key: {ret1, ret3}}
        sec_pending_sells: list = []  # strict_exec: (code, reason, fraction) -- fraction=None이면 전량
        sec_pending_buys: list = []   # strict_exec: (code, sector_key, meta)
        evidence = SignalEvidenceLedger("sector_focus", {
            "buy_threshold": buy_threshold, "rebalance_days": rebalance_days,
            "pick_ta_bonus": pick_ta_bonus, "avoid_discontinuity": avoid_discontinuity,
            "flow_window_days": 92, "momentum_windows": {"ret3": "92~99d back", "ret1": "28~35d back"},
        })
        _sector_evidence_cache: dict = {}

        def _sector_evidence(sector_key: str, as_of: str) -> list:
            key = (sector_key, as_of)
            if key in _sector_evidence_cache:
                return _sector_evidence_cache[key]
            codes_e = _SECTOR_GROUPS[sector_key]["codes"]
            ph_e = "({})".format(",".join("?" * len(codes_e)))
            d_3m_e = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=92)).strftime("%Y-%m-%d")
            fin_item = _sector_op_yoy_inputs(conn, codes_e, int(as_of[:4]), as_of)
            flow_rows, per_code = [], {}
            for r in conn.execute(
                f"SELECT stock_code, date, frn_net_buy_amt, inst_net_buy_amt, frn_net_buy, inst_net_buy, close "
                f"FROM price_history WHERE stock_code IN {ph_e} AND date>=? AND date<=? "
                f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0) "
                f"ORDER BY stock_code, date",
                codes_e + [d_3m_e, as_of],
            ).fetchall():
                day_e = str(r[1])[:10]
                flow_rows.append({"row_id": f"{r[0]}:{day_e}", "available_at": day_e,
                                  "value": [r[2], r[3], r[4], r[5], r[6]]})
                agg = per_code.setdefault(r[0], {"rows": 0, "first": day_e, "last": day_e})
                agg["rows"] += 1
                agg["last"] = day_e
            flow_item = evidence_aggregate(
                "investor_flow", flow_rows, source_key=f"{sector_key}:price_history:{d_3m_e}~{as_of}",
                rows_summary={"table": "price_history", "window": [d_3m_e, as_of], "per_code": per_code})
            mom_rows = []
            for code_m in codes_e:
                series = price_data.get(code_m, {})
                if as_of in series:
                    mom_rows.append({"row_id": f"{code_m}:{as_of}", "available_at": as_of,
                                     "value": {"close": series[as_of][0], "role": "now"}})
                for role, back in (("ret3_base", range(92, 100)), ("ret1_base", range(28, 36))):
                    for d_back in back:
                        d_try = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=d_back)).strftime("%Y-%m-%d")
                        if d_try in series:
                            mom_rows.append({"row_id": f"{code_m}:{d_try}", "available_at": d_try,
                                             "value": {"close": series[d_try][0], "role": role}})
                            break
            mom_item = evidence_aggregate("price_momentum", mom_rows,
                                          source_key=f"{sector_key}:price_history:{as_of}")
            items = [fin_item, flow_item, mom_item]
            _sector_evidence_cache[key] = items
            return items

        # 2026-09-28: 보유 중 확정 기업행위(권리락·분할 등) 날 포지션을 새 주식 기준으로 재기준
        _ca_factors = {} if adjusted_prices else _load_jump_aligned_corp_factors(conn, list(price_data.keys()))
        _ca_prev_day = None

        def _sec_close_at(code, price, reason, day_):
            """조정 단위 `price`로 즉시 청산(단절 처리)."""
            nonlocal cash
            pos_ = positions.pop(code)
            sector_assignments.pop(code, None)
            _amt, _net = _net_profit_scaled(pos_["buy_price"], price, pos_.get("qty", 1), _cost_mktcap(code, day_, price))
            cash += pos_["buy_price"] * pos_.get("qty", 1) + _amt
            all_trades.append({"date": day_, "code": code, "action": "SELL", "price": price, "pnl_pct": round(_net, 2),
                               "reason": reason, "qty": pos_.get("qty", 1), "entry_price": pos_["buy_price"],
                               "pnl_krw": round(_amt), "price_raw": round(price / _af(code, day_), 4),
                               "entry_price_raw": pos_.get("entry_price_raw"), "qty_raw": pos_.get("qty_raw")})
            nonlocal sec_pending_sells
            sec_pending_sells = [t for t in sec_pending_sells if t[0] != code]
            return all_trades[-1]

        for i, trade_date in enumerate(trade_dates):
            if adjusted_prices:
                for code in list(positions.keys()):
                    e = adj_e.get(code)
                    if not e or not e['breaks']:
                        continue
                    k = adj_dix[code].get(trade_date)
                    if k is not None and k > 0 and trade_date in e['breaks']:
                        # 단절 당일: 공시 근거 없이 보유 중이면 정지 직전 거래 가능일 종가로 청산(평가 불가 표시)
                        kk = last_tradable_index_before(e, k)
                        kk = k - 1 if kk is None else kk
                        px_ = price_data[code][e['dates'][kk]][0]
                        t_ = _sec_close_at(code, px_, "단절 당일 청산(공시 근거 없음·평가 불가)", trade_date)
                        t_["evaluation"] = "unevaluable_break"; t_["basis_date"] = e['dates'][kk]
                        adj_stats['break_day_liquidations'] += 1
                        continue
                    bi = bisect.bisect_right(e['breaks'], trade_date)
                    if bi < len(e['breaks']):
                        nb = e['breaks'][bi]
                        if last_tradable_day_before_break(e, (e.get('break_disclosed') or {}).get(nb), nb) == trade_date \
                                and trade_date in price_data[code]:
                            _sec_close_at(code, price_data[code][trade_date][0], "단절 전 청산(D12, 공시 후)", trade_date)
                            adj_stats['break_liquidations'] += 1
            else:
                _rebase_positions_for_corp_actions(_ca_factors, positions, _ca_prev_day, trade_date, ('buy_price', 'peak'), 'qty')
            _ca_prev_day = trade_date
            # ── strict_exec: 전일 신호 → 오늘 시가 체결 ──
            if strict_exec:
                _still = []
                for code, reason, fraction in sec_pending_sells:
                    if code not in positions:
                        continue
                    pdata = price_data.get(code, {}).get(trade_date)
                    # F07 fix: 시가 결측(pdata[3] is None)도 "당일 미거래"와 동일하게 대기시킨다 —
                    # 종가를 대신 시가로 체결하지 않는다.
                    if pdata is None or pdata[3] is None:
                        _still.append((code, reason, fraction)); continue
                    if adjusted_prices and code in adj_e and (adj_e[code]['volume'][adj_dix[code][trade_date]] or 0) <= 0:
                        adj_stats['zero_volume_deferred_sells'] += 1      # 거래 불가능한 날 — 다음 거래일로 이월
                        _still.append((code, reason, fraction)); continue
                    px = pdata[3]
                    pos = positions[code]
                    if fraction is None:
                        pos = positions.pop(code)
                        sector_assignments.pop(code, None)
                        _pnl_amt, _net = _net_profit_scaled(pos["buy_price"], px, pos.get("qty", 1), _cost_mktcap(code, trade_date, px))
                        cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                        # 재개 우선순위1(2026-09-12, Codex 재검토): 종목별 손익귀속 검산을 하려면
                        # 원화 금액이 필요한데 이전엔 pnl_pct(비율)만 기록해 재구성이 안 됐다 —
                        # qty/entry_price/pnl_krw를 그대로 남긴다(기존 필드 제거 없음, 추가만).
                        all_trades.append({"date": trade_date, "code": code, "action": "SELL",
                                           "price": px, "pnl_pct": round(_net, 2), "reason": reason,
                                           "qty": pos.get("qty", 1), "entry_price": pos["buy_price"],
                                           "pnl_krw": round(_pnl_amt)})
                    else:
                        # 실험 #2 부분익절: 비율만큼 실현, 나머지는 동일 buy_price/peak로 계속 보유.
                        total_qty = pos.get("qty", 1)
                        sell_qty = max(1, int(total_qty * fraction))
                        sell_qty = min(sell_qty, total_qty)
                        _pnl_amt, _net = _net_profit_scaled(pos["buy_price"], px, sell_qty, _cost_mktcap(code, trade_date, px))
                        cash += pos["buy_price"] * sell_qty + _pnl_amt
                        all_trades.append({"date": trade_date, "code": code, "action": "SELL",
                                           "price": px, "pnl_pct": round(_net, 2), "reason": reason,
                                           "partial_qty": sell_qty, "remaining_qty": total_qty - sell_qty,
                                           "qty": sell_qty, "entry_price": pos["buy_price"],
                                           "pnl_krw": round(_pnl_amt)})
                        pos["qty"] = total_qty - sell_qty
                        pos["partial_tp_done"] = True
                        if pos["qty"] <= 0:
                            positions.pop(code)
                            sector_assignments.pop(code, None)
                sec_pending_sells = _still
                for code, sector_key, meta in sec_pending_buys:
                    if code in positions or len(positions) >= max_positions:
                        continue
                    pdata = price_data.get(code, {}).get(trade_date)
                    # F07 fix: 시가 결측(pdata[3] is None)도 "당일 미거래"와 동일하게 만료시킨다.
                    if pdata is None or pdata[3] is None:
                        continue  # 당일 미거래(또는 시가 결측) → 주문 만료
                    px = pdata[3]
                    if adjusted_prices and code in adj_e and (adj_e[code]['volume'][adj_dix[code][trade_date]] or 0) <= 0:
                        adj_stats['zero_volume_skipped_buys'] += 1
                        continue
                    budget = min(per_stock, cash * 0.99)
                    qty, _cost, _qraw, _rpx = _buy_size(code, trade_date, px, budget)
                    if qty < 1 or _cost > cash:
                        continue  # 현금 부족 → 주문 거부 (현금 음수 금지)
                    cash -= _cost
                    positions[code] = {"buy_price": px, "peak": px, "qty": qty,
                                       "sector": sector_key, "entry_date": trade_date,
                                       "entry_sector_score": meta.get("sector_score", 0), "pyramid_adds": 0}
                    if adjusted_prices:
                        positions[code].update({"entry_price_raw": round(_rpx, 4), "qty_raw": _qraw})
                    sector_assignments[code] = sector_key
                    all_trades.append({"date": trade_date, "code": code, "action": "BUY",
                                       "price": px, "sector": sector_key, "qty": qty, **meta,
                                       **({"price_raw": round(_rpx, 4), "qty_raw": _qraw} if adjusted_prices else {})})
                sec_pending_buys = []
            # ─────── 보유 종목 현재가 업데이트 & 매도 체크 ───────
            to_sell = []
            for code, pos in list(positions.items()):
                pdata = price_data.get(code, {}).get(trade_date)
                if pdata is None:
                    continue
                cur = pdata[0]
                peak = max(pos["peak"], cur)
                positions[code]["peak"] = peak

                ret = cur / pos["buy_price"] - 1
                trail_cur = (peak - pos["buy_price"]) / pos["buy_price"]
                trail_dd  = (cur - peak) / peak

                sell_reason = None
                fraction = None
                if ret <= stop:
                    sell_reason = f"손절{ret*100:.1f}%"
                elif trail_cur > 0.05 and trail_dd <= trail:
                    sell_reason = f"추적손절{trail_dd*100:.1f}%"
                elif ret >= tp:
                    if partial_tp_pct is not None and not pos.get("partial_tp_done"):
                        # 실험 #2: 부분익절 후 나머지는 손절/추적손절만으로 계속 보유(포지션당 1회).
                        sell_reason = f"부분익절{ret*100:.1f}%"
                        fraction = partial_tp_pct
                    elif partial_tp_pct is None:
                        sell_reason = f"익절{ret*100:.1f}%"
                    # partial_tp_pct가 설정돼 있고 이미 1회 부분익절했으면: 고정 tp로는 더 이상
                    # 청산하지 않고(fraction도 None) 손절/추적손절에만 의존해 계속 보유.

                if sell_reason:
                    to_sell.append((code, cur, sell_reason, fraction))

            if strict_exec:
                _queued = {c for c, _, _ in sec_pending_sells}
                for code, sell_price, reason, fraction in to_sell:
                    if code not in _queued:
                        sec_pending_sells.append((code, reason, fraction))
            else:
                for code, sell_price, reason, fraction in to_sell:
                    if fraction is not None:
                        pos = positions[code]
                        total_qty = pos.get("qty", 1)
                        sell_qty = max(1, min(int(total_qty * fraction), total_qty))
                        _pnl_amt, _net = _net_profit_scaled(pos["buy_price"], sell_price, sell_qty, _cost_mktcap(code, trade_date, sell_price))
                        cash += pos["buy_price"] * sell_qty + _pnl_amt
                        all_trades.append({"date": trade_date, "code": code, "action": "SELL",
                                           "price": sell_price, "pnl_pct": round(_net, 2), "reason": reason,
                                           "partial_qty": sell_qty, "remaining_qty": total_qty - sell_qty,
                                           "qty": sell_qty, "entry_price": pos["buy_price"],
                                           "pnl_krw": round(_pnl_amt)})
                        pos["qty"] = total_qty - sell_qty
                        pos["partial_tp_done"] = True
                        if pos["qty"] <= 0:
                            positions.pop(code)
                            sector_assignments.pop(code, None)
                        continue
                    pos = positions.pop(code)
                    sector_assignments.pop(code, None)
                    _pnl_amt, _net = _net_profit_scaled(pos["buy_price"], sell_price, pos.get("qty", 1), _cost_mktcap(code, trade_date, sell_price))
                    cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                    all_trades.append({
                        "date": trade_date, "code": code, "action": "SELL",
                        "price": sell_price, "pnl_pct": round(_net, 2), "reason": reason,
                        "qty": pos.get("qty", 1), "entry_price": pos["buy_price"],
                        "pnl_krw": round(_pnl_amt),
                    })

            # ─────── 월 1회 섹터 리밸런싱 ───────
            if i % rebalance_days == 0:
                # 섹터 점수 계산
                scores = {}
                momentum = {}
                for sk in _SECTOR_GROUPS:
                    # 간소화: inst/frn 집계 + op_yoy
                    codes_s = _SECTOR_GROUPS[sk]["codes"]
                    ph_s = "({})".format(",".join("?" * len(codes_s)))
                    d_3m = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=92)).strftime("%Y-%m-%d")

                    frn_s = (conn.execute(
                        f"SELECT SUM(CASE WHEN COALESCE(frn_net_buy_amt,0) != 0 "
                        f"THEN frn_net_buy_amt/100.0 ELSE COALESCE(frn_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
                        f"WHERE stock_code IN {ph_s} AND date>=? AND date<=? "
                        f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0)",
                        codes_s + [d_3m, trade_date]
                    ).fetchone() or (0,))[0] or 0.0

                    inst_s = (conn.execute(
                        f"SELECT SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0 "
                        f"THEN inst_net_buy_amt/100.0 ELSE COALESCE(inst_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
                        f"WHERE stock_code IN {ph_s} AND date>=? AND date<=? "
                        f"AND (frn_net_buy_amt!=0 OR inst_net_buy_amt!=0 OR frn_net_buy!=0 OR inst_net_buy!=0)",
                        codes_s + [d_3m, trade_date]
                    ).fetchone() or (0,))[0] or 0.0

                    # OP YoY (섹터 내 종목 중위값) — F01 (docs/claude_handoff_strategy_code_findings_20260912.md,
                    # 2026-09-12): 실제 공시 시점(fin_disclosure_dates, 없으면 법정기한)으로
                    # 종목별 게이팅. 실제 실행경로를 테스트로 검증할 수 있도록 별도 함수로
                    # 분리했다(재개 우선순위4, Codex 재검토 지적 — 로직을 테스트 안에 복제하면
                    # 운영 코드의 게이트가 제거돼도 테스트가 통과할 수 있다).
                    med_yoy = _pit_gated_sector_op_yoy_median(conn, codes_s, int(trade_date[:4]), trade_date)

                    ret3_values = []
                    ret1_values = []
                    for code_s in codes_s:
                        p_now_s = price_data.get(code_s, {}).get(trade_date)
                        p_3m_s = None
                        for d_back in range(92, 100):
                            d_try = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=d_back)).strftime("%Y-%m-%d")
                            if d_try in price_data.get(code_s, {}):
                                p_3m_s = price_data[code_s][d_try]
                                break
                        if p_now_s and p_3m_s and p_3m_s[0] > 0:
                            ret3_values.append((p_now_s[0] / p_3m_s[0] - 1) * 100)
                        p_1m_s = None
                        for d_back in range(28, 36):
                            d_try = (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=d_back)).strftime("%Y-%m-%d")
                            if d_try in price_data.get(code_s, {}):
                                p_1m_s = price_data[code_s][d_try]
                                break
                        if p_now_s and p_1m_s and p_1m_s[0] > 0:
                            ret1_values.append((p_now_s[0] / p_1m_s[0] - 1) * 100)
                    sector_ret3 = sorted(ret3_values)[len(ret3_values)//2] if ret3_values else 0.0
                    sector_ret1 = sorted(ret1_values)[len(ret1_values)//2] if ret1_values else 0.0
                    momentum[sk] = {"ret1": round(sector_ret1, 1), "ret3": round(sector_ret3, 1)}

                    sc = 0.0
                    if   frn_s >= 30000: sc += 35
                    elif frn_s >= 10000: sc += 30
                    elif frn_s >=  5000: sc += 24
                    elif frn_s >=  1500: sc += 18
                    elif frn_s >=   300: sc += 10
                    elif frn_s <  -5000: sc -= 8
                    if   inst_s >= 20000: sc += 30
                    elif inst_s >= 10000: sc += 24
                    elif inst_s >=  3000: sc += 16
                    elif inst_s >=  1000: sc += 10
                    elif inst_s >=   200: sc += 5
                    elif inst_s <  -3000: sc -= 7
                    if   med_yoy >= 100: sc += 25
                    elif med_yoy >= 50:  sc += 18
                    elif med_yoy >= 20:  sc += 10
                    elif med_yoy >= 0:   sc += 4
                    elif med_yoy < -30:  sc -= 6
                    if   sector_ret3 >= 30: sc += 25
                    elif sector_ret3 >= 20: sc += 18
                    elif sector_ret3 >= 10: sc += 10
                    elif sector_ret3 >= 5:  sc += 5
                    elif sector_ret3 < -10: sc -= 8
                    scores[sk] = round(sc, 1)

                sector_scores_cache[trade_date] = scores
                sector_momentum_cache[trade_date] = momentum

                # BUY 섹터 → 기존 보유 중 EXIT 대상 청산
                for code in list(positions.keys()):
                    sec = sector_assignments.get(code)
                    if sec and scores.get(sec, 0) < exit_threshold:
                        pdata = price_data.get(code, {}).get(trade_date)
                        if pdata:
                            entry_date = positions[code].get("entry_date")
                            hold_days = (
                                datetime.strptime(trade_date, "%Y-%m-%d") - datetime.strptime(entry_date, "%Y-%m-%d")
                            ).days if entry_date else 999
                            if hold_days < min_sector_hold_days:
                                continue
                            pos = positions.pop(code)
                            sector_assignments.pop(code, None)
                            sell_p = pdata[0]
                            _pnl_amt, pnl = _net_profit_scaled(pos["buy_price"], sell_p, pos.get("qty", 1), _cost_mktcap(code, trade_date, sell_p))
                            cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                            all_trades.append({
                                "date": trade_date, "code": code, "action": "SECTOR_EXIT",
                                "price": sell_p, "pnl_pct": round(pnl, 2),
                                "reason": f"섹터점수하락{scores.get(sec,0):.0f}→EXIT(보유{hold_days}일)",
                                "qty": pos.get("qty", 1), "entry_price": pos["buy_price"],
                                "pnl_krw": round(_pnl_amt),
                            })

                # ── 확신도 상승 시 추가매수(피라미딩, 사용자 제안 2026-08-09) ──
                # 신규 슬롯 경쟁이 아니라 "이미 보유 중인 포지션"에만 자본을 더 태우므로
                # 동점 타이브레이크 불안정성과 무관 — position_limit/슬롯 수를 전혀 건드리지 않음.
                if pyramid_score_gain is not None:
                    for code, pos in list(positions.items()):
                        sec = sector_assignments.get(code)
                        if not sec:
                            continue
                        cur_score = scores.get(sec, 0)
                        entry_score = pos.get("entry_sector_score", 0)
                        if pos.get("pyramid_adds", 0) >= pyramid_max_adds:
                            continue
                        if cur_score < entry_score + pyramid_score_gain:
                            continue
                        pdata = price_data.get(code, {}).get(trade_date)
                        if not pdata:
                            continue
                        add_px = pdata[0]
                        add_budget = min(per_stock * pyramid_add_pct, cash * 0.99)
                        add_qty, _acost, _araw, _arpx = _buy_size(code, trade_date, add_px, add_budget)
                        if add_qty < 1 or _acost > cash:
                            continue  # 현금 부족 → 스킵(음수 금지)
                        cash -= _acost
                        old_qty = pos["qty"]
                        new_qty = old_qty + add_qty
                        # 가중평균 단가로 원가 재계산 — 이후 손절/추적손절/익절 판단이 이 기준으로 이뤄짐
                        pos["buy_price"] = (pos["buy_price"] * old_qty + add_px * add_qty) / new_qty
                        pos["qty"] = new_qty
                        pos["entry_sector_score"] = cur_score  # 다음 추가매수는 이 시점 대비 재상승 요구
                        pos["pyramid_adds"] = pos.get("pyramid_adds", 0) + 1
                        all_trades.append({
                            "date": trade_date, "code": code, "action": "PYRAMID_ADD",
                            "price": add_px, "sector": sec, "qty": add_qty,
                            "reason": f"섹터점수상승{entry_score:.0f}→{cur_score:.0f}(+{cur_score-entry_score:.0f}) 추가매수#{pos['pyramid_adds']}",
                        })

                # BUY 섹터 발굴 → RS 리더 선택 (섹터 확정 시 3M 모멘텀 리더 매수)
                buy_sectors = sorted([sk for sk, sc in scores.items() if sc >= buy_threshold],
                                     key=lambda sk: -scores[sk])

                def _price_discontinuity_recent(conn, code, as_of, window=6, threshold=0.40):
                    """2026-08-23: 전체 price_history 스캔에서 확인된 데이터 아티팩트(단일일
                    스파이크 후 익일 원상복귀, 214개 거래일에 걸쳐 1,267건 — 2022-01-03 하루에만
                    254개 종목 동시발생 등 계정/수집 오류로 강하게 의심됨, 2026-08-22 stockeasy
                    _price_discontinuity()와 동일 원리)이 매수후보 선정 시점(as_of) 직전 며칠 내에
                    있으면 해당 종목의 3M모멘텀(rs3m)·기관집중도 계산이 오염됐을 수 있어 후보에서
                    제외한다. 진짜 급등/급락(분할·병합·거래재개 등)과 구분하려 하지 않고 보수적으로
                    스킵 — 매수 기회 손실 위험보다 오염된 신호로 진입하는 위험을 우선 차단."""
                    rows = conn.execute(
                        """
                        WITH p AS (
                          SELECT date, close, LAG(close) OVER(ORDER BY date) prev_close
                          FROM price_history WHERE stock_code=? AND date<=? AND close>0
                        )
                        SELECT close, prev_close FROM p
                        WHERE prev_close IS NOT NULL AND prev_close > 0
                        ORDER BY date DESC LIMIT ?
                        """,
                        (code, as_of, window),
                    ).fetchall()
                    for r in rows:
                        prev_close = float(r[1])
                        close_v = float(r[0])
                        if prev_close and abs(close_v / prev_close - 1) >= threshold:
                            return True
                    return False

                def _sector_rs_picks(conn, sector_key, as_of, top_n=3):
                    """섹터 확정 BUY 시 3개월 RS 리더 선택"""
                    codes_r = _SECTOR_GROUPS[sector_key]["codes"]
                    d3m = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=92)).strftime("%Y-%m-%d")
                    results = []
                    for c in codes_r:
                        if exclude_codes and c in exclude_codes:
                            continue
                        p_now = price_data.get(c, {}).get(as_of)
                        p_3m = None
                        for d_back in range(92, 100):
                            d_try = (datetime.strptime(as_of, "%Y-%m-%d") - timedelta(days=d_back)).strftime("%Y-%m-%d")
                            if d_try in price_data.get(c, {}):
                                p_3m = price_data[c][d_try]
                                break
                        if not p_now or not p_3m or p_3m[0] <= 0:
                            continue
                        if avoid_discontinuity and _price_discontinuity_recent(conn, c, as_of):
                            continue
                        rs3m = (p_now[0] / p_3m[0] - 1) * 100
                        # inst_3m 수급
                        inst3m_r = (conn.execute(
                            "SELECT SUM(CASE WHEN COALESCE(inst_net_buy_amt,0) != 0 "
                            "THEN inst_net_buy_amt/100.0 ELSE COALESCE(inst_net_buy,0)*COALESCE(close,0)/100000000.0 END) FROM price_history "
                            "WHERE stock_code=? AND date>=? AND date<=? AND (inst_net_buy_amt!=0 OR frn_net_buy_amt!=0 OR inst_net_buy!=0 OR frn_net_buy!=0)",
                            (c, d3m, as_of)
                        ).fetchone() or (0,))[0] or 0.0
                        _sh_r = _shares_asof_sector(c, as_of)
                        mktcap_r = (_sh_r * (p_now[0] / _af(c, as_of)) / 1e8) if _sh_r > 0 else 1000
                        inst_int_r = inst3m_r / max(1, mktcap_r) * 100
                        # RS 리더 점수 (3M 모멘텀 60% + 기관집중도 40%)
                        sel_score = rs3m * 0.6 + inst_int_r * 40

                        # 실험 #1 (2026-09-12): PIT로 보정한 섹터 내 선별 -- 셋 다 opt-in.
                        # F01과 동일한 공시일 게이팅(_release_date)으로 "as_of 시점에 실제
                        # 공시돼 있던 가장 최근 연간 실적"을 찾아 재사용한다(룩어헤드 없음).
                        if use_earnings_abs_bonus or use_disclosure_freshness_bonus or use_cashflow_confirm_gate:
                            annual_rows_c = conn.execute(
                                "SELECT year, operating_profit FROM ("
                                "  SELECT year, operating_profit,"
                                "         ROW_NUMBER() OVER (PARTITION BY year"
                                "             ORDER BY CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END) AS rt_rn"
                                "  FROM financial_data"
                                "  WHERE stock_code=? AND is_annual=1 AND operating_profit IS NOT NULL AND year<=?"
                                ") dedup WHERE rt_rn=1",
                                (c, int(as_of[:4])),
                            ).fetchall()
                            year_map_c = {int(y): op for y, op in annual_rows_c}
                            avail_years_c = sorted(
                                (y for y in year_map_c if _release_date(y, 4, True, c) <= as_of), reverse=True
                            )
                            if avail_years_c:
                                cur_y_c = avail_years_c[0]
                                op_cur_c = year_map_c.get(cur_y_c)
                                op_prev_c = year_map_c.get(cur_y_c - 1)
                                if use_earnings_abs_bonus and op_cur_c is not None and op_prev_c is not None:
                                    # 절대개선액(억원)을 시총(억원) 대비 정규화 -- % YoY와 달리
                                    # 기저효과(작은 기저에서 튀는 % 폭등)에 흔들리지 않는다.
                                    abs_improve_억 = (op_cur_c - op_prev_c) / 1e8
                                    sel_score += max(-10.0, min(10.0, abs_improve_억 / max(1.0, mktcap_r) * 100))
                                if use_disclosure_freshness_bonus:
                                    release_c = _release_date(cur_y_c, 4, True, c)
                                    try:
                                        days_since = (datetime.strptime(as_of, "%Y-%m-%d") - datetime.strptime(release_c, "%Y-%m-%d")).days
                                        sel_score += max(0.0, 10.0 - days_since / 30.0)  # 신선할수록(최근 공시) +최대 10점, 300일+면 0
                                    except ValueError:
                                        pass
                                if use_cashflow_confirm_gate:
                                    ocf_row = conn.execute(
                                        "SELECT operating_cf FROM ("
                                        "  SELECT operating_cf,"
                                        "         ROW_NUMBER() OVER (ORDER BY CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END) AS rt_rn"
                                        "  FROM cash_flow_data"
                                        "  WHERE stock_code=? AND is_annual=1 AND year=? AND operating_cf IS NOT NULL"
                                        ") dedup WHERE rt_rn=1",
                                        (c, cur_y_c),
                                    ).fetchone()
                                    if ocf_row and ocf_row[0] is not None and float(ocf_row[0]) < 0:
                                        sel_score -= 15.0  # 회계상 실적개선이 영업현금흐름 미동반 시 감점(하드 배제는 아님)

                        if pick_ta_bonus is not None:
                            # 직전 공시분기 첫 흑자전환 (as-of 표준 공시일정 기준, 룩어헤드 없음)
                            # 2026-09-08 수정: report_type(CFS/OFS) 타이브레이크 없이 LIMIT 4만
                            # 걸면 같은 분기의 CFS/OFS 두 행이 서로 다른 분기인 것처럼 섞여
                            # 흑자전환 판정(ni_rows[0] vs ni_rows[1:])이 왜곡된다 —
                            # se_momentum.py에서 발견된 것과 동일 부류의 버그. 분기당 CFS우선
                            # 1행만 남긴다.
                            ni_rows = conn.execute("""
                                SELECT net_income FROM (
                                    SELECT year, quarter, net_income,
                                           ROW_NUMBER() OVER (
                                               PARTITION BY year, quarter
                                               ORDER BY CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END
                                           ) AS rt_rn
                                    FROM financial_data
                                    WHERE stock_code=? AND is_annual=0 AND quarter BETWEEN 1 AND 4
                                      AND net_income IS NOT NULL
                                      AND (CASE WHEN quarter=1 THEN printf('%d-05-15', year)
                                                WHEN quarter=2 THEN printf('%d-08-15', year)
                                                WHEN quarter=3 THEN printf('%d-11-15', year)
                                                ELSE COALESCE((SELECT a.avail_date FROM fin_disclosure_dates a WHERE a.stock_code=?
                                                              AND a.year=financial_data.year AND a.quarter=4 AND a.is_annual=1),
                                                             printf('%d-03-31', year+1)) END) <= ?
                                ) dedup
                                WHERE rt_rn = 1
                                ORDER BY year DESC, quarter DESC LIMIT 4
                            """, (c, c, as_of)).fetchall()
                            if (len(ni_rows) >= 2 and float(ni_rows[0][0] or 0) > 0
                                    and any(float(x[0] or 0) < 0 for x in ni_rows[1:])):
                                sel_score += pick_ta_bonus
                        results.append({"code": c, "surge_score": round(sel_score, 1), "rs3m": round(rs3m, 1),
                                        "inst_intensity": round(inst_int_r, 2), "op_yoy": None, "pos_52w": None,
                                        "sector_key": sector_key})
                    results.sort(key=lambda x: -x["surge_score"])
                    return results[:top_n]

                n_slots = max_positions - len(positions)
                for sector_key in buy_sectors[:3]:  # 최대 3섹터
                    if n_slots <= 0:
                        break
                    picks = _sector_rs_picks(conn, sector_key, trade_date, top_n=3)
                    for pk in picks:
                        if n_slots <= 0:
                            break
                        code = pk["code"]
                        if code in positions:
                            continue
                        pdata = price_data.get(code, {}).get(trade_date)
                        if not pdata:
                            continue
                        if adjusted_prices and code in adj_e:
                            if is_excluded_day(adj_e[code], trade_date):
                                adj_stats['candidate_skips_excluded'] += 1; continue
                            if trade_date in adj_qdays.get(code, ()):
                                adj_stats['candidate_skips_quality_day'] += 1; continue
                        _meta = {
                            "sector_score": scores.get(sector_key, 0),
                            "sector_ret1": momentum.get(sector_key, {}).get("ret1"),
                            "sector_ret3": momentum.get(sector_key, {}).get("ret3"),
                            "surge_score": pk["surge_score"],
                            "reason": f"섹터BUY{scores.get(sector_key,0):.0f} 급등점수{pk['surge_score']}",
                        }
                        if strict_exec:
                            if code not in [c for c, _, _ in sec_pending_buys]:
                                sec_pending_buys.append((code, sector_key, _meta))
                                n_slots -= 1
                                evidence.note(code, trade_date, [
                                    {**item, "source_value": {"sector": sector_key,
                                                              "sector_score": _meta["sector_score"],
                                                              "surge_score": _meta["surge_score"]}}
                                    for item in _sector_evidence(sector_key, trade_date)
                                ])
                            continue
                        buy_p = pdata[0]
                        budget = min(per_stock, cash * 0.99)
                        qty, _cost, _qraw, _rpx = _buy_size(code, trade_date, buy_p, budget)
                        if qty < 1 or _cost > cash:
                            continue  # 현금 부족 → 주문 거부
                        cash -= _cost
                        positions[code] = {"buy_price": buy_p, "peak": buy_p, "qty": qty,
                                           "sector": sector_key, "entry_date": trade_date,
                                           "entry_sector_score": _meta.get("sector_score", 0), "pyramid_adds": 0}
                        sector_assignments[code] = sector_key
                        all_trades.append({
                            "date": trade_date, "code": code, "action": "BUY",
                            "price": buy_p, "sector": sector_key, "qty": qty, **_meta,
                        })
                        n_slots -= 1

        # 마지막 날 청산 (현금원장 방식)
        # F07 fix (2026-09-12, docs/claude_handoff_strategy_code_findings_20260912.md):
        # 기존에는 종료일에 해당 종목 시세가 없으면 그 종목이 과거에 마지막으로 거래된
        # (임의로 오래될 수 있는) 날짜의 종가를 가져다 "오늘 체결"로 기록했다 — 거래정지
        # 종목이 정지 이전 몇 달/몇 년 전 가격으로 오늘 종료청산된 것처럼 보이는 허구
        # 체결이었다. 이제 종료일 당일 시세가 실제로 있을 때만 청산으로 기록하고, 없으면
        # (거래정지 등) 과거 마지막 가격은 참고 로그로만 남기고 기존 "시세부재" 보수적
        # 처리(원금 미환입, 전액손실 표기)로 합친다 — 오늘 체결로 간주하지 않는다.
        last_date = trade_dates[-1] if trade_dates else end_date
        for code, pos in positions.items():
            pdata = price_data.get(code, {}).get(last_date)
            if pdata:
                sell_p = pdata[0]
                _pnl_amt, pnl = _net_profit_scaled(pos["buy_price"], sell_p, pos.get("qty", 1), _cost_mktcap(code, last_date, sell_p))
                cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                all_trades.append({"date": last_date, "code": code, "action": "FINAL",
                                   "price": sell_p, "pnl_pct": round(pnl, 2), "reason": "종료청산",
                                   "qty": pos.get("qty", 1), "entry_price": pos["buy_price"],
                                   "pnl_krw": round(_pnl_amt)})
            else:
                # 재개 우선순위2(2026-09-12, Codex 재검토): "시세 없음"을 pnl_pct=-100.0으로
                # 확정 기록하면 "실제로 전액 손실 확정"과 "그냥 평가불가"를 구분할 수 없다.
                # 현금원장 자체는 이 분기에서 cash를 건드리지 않으므로(매수원금이 이미
                # 빠져나간 채 되돌아오지 않음) 수익률 계산 결과는 이전과 동일하게 보수적으로
                # 유지되지만, pnl_pct는 None(확정 손익 아님)으로 두고 -100%는 "이게 최악의
                # 경우라면"이라는 하한 시나리오 라벨로만 별도 필드에 남긴다. 미청산 상태·평가
                # 불가·최종 관측일을 각각 분리해서 기록한다.
                stale_history = price_data.get(code, {})
                stale_last_date = sorted(stale_history.keys())[-1] if stale_history else None
                all_trades.append({
                    "date": last_date, "code": code, "action": "FINAL",
                    "price": None, "pnl_pct": None,
                    "disposition": "unresolved_no_price_at_period_end",
                    "reason": "평가불가(종료일 시세없음, 원금 미환입으로 보수처리)",
                    "conservative_lower_bound_pnl_pct": -100.0,
                    "last_observed_price_date_reference_only": stale_last_date,
                    "qty": pos.get("qty", 1), "entry_price": pos["buy_price"], "pnl_krw": None,
                })

        # 수익률 계산 (투자원금 기준)
        n_buy = sum(1 for t in all_trades if t["action"] == "BUY")
        n_sell = sum(1 for t in all_trades if t["action"] in ("SELL", "SECTOR_EXIT", "FINAL"))
        # F07 (2026-09-12): pnl_pct=None(평가불가/미해결)인 레코드는 확정 손익이 아니므로
        # 평균거래수익률/승률 계산에서 제외한다 -- 전체 수익률(portfolio_return, 아래)은
        # 실제 cash 원장 기준이라 이 필터와 무관하게 그대로 정확하다.
        sell_trades = [t for t in all_trades if t.get("pnl_pct") is not None and t["action"] != "BUY"]
        avg_trade_return = sum(t["pnl_pct"] for t in sell_trades) / max(1, len(sell_trades)) if sell_trades else 0.0
        portfolio_return = (cash - initial_cash) / max(1, initial_cash) * 100  # C1: 최종 현금원장 기준
        win_rate = sum(1 for t in sell_trades if t.get("pnl_pct", 0) > 0) / max(1, len(sell_trades)) * 100

        # KOSPI 비교
        k_start = next((k_prices[d] for d in k_dates if d >= start_date), None)
        k_end   = next((k_prices[d] for d in reversed(k_dates) if d <= end_date), None)
        kospi_ret = (k_end / k_start - 1) * 100 if k_start and k_end else 0.0

        alpha = portfolio_return - kospi_ret
        summary = (f"V-SECTOR {start_date[:7]}~{end_date[:7]} | "
                   f"매수{n_buy}건 매도{n_sell}건 | 자본수익{portfolio_return:.1f}% | "
                   f"평균거래{avg_trade_return:.1f}% | 승률{win_rate:.0f}% | KOSPI대비α{alpha:+.1f}%"
                   + (f"\n조정가격(W3): {json.dumps(adj_stats, ensure_ascii=False)}" if adjusted_prices else ""))

        import json as _json
        conn.execute("""
            UPDATE backtest_runs SET status='done', summary_text=?,
            total_return_pct=?, win_rate=?, total_trades=?, profit_trades=?, trades_json=?
            WHERE run_id=?
        """, (
            summary,
            round(portfolio_return, 2),
            round(win_rate, 1),
            len(sell_trades),
            sum(1 for t in sell_trades if t.get("pnl_pct", 0) > 0),
            _json.dumps({
                "trades": all_trades,
                "avg_trade_return_pct": round(avg_trade_return, 2),
                "portfolio_return_pct": round(portfolio_return, 2),
                "sector_momentum_filter": "none",
            }, ensure_ascii=False),
            run_id,
        ))
        conn.commit()
        conn.close()
        _register_execution_artifacts(run_id, initial_cash, cash)
        evidence.persist(run_id, all_trades)
        return run_id

    except Exception as e:
        import traceback as _tb
        err = f"{e}\n{_tb.format_exc()}"
        try:
            c2 = sqlite3.connect(DB_PATH, timeout=120)
            c2.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c2.commit(); c2.close()
        except Exception:
            pass
        raise



# ══════════════════════════════════════════════════════════════
#  V-RECOVERY: 낙폭과대 반등 전략
#  데이터 근거 (2026-06-29 실증):
#    MA60 -25%+ 하방 종목 → 3배 달성률 69.2% (전체 평균 6.7%의 10배!)
#    MA60 -10~-25% 하방  → 3배 달성률 9.4%
#    52주 저점 0~15% 이내 → 3배 달성률 11.4%
#    기관/외인 강매수     → 3배 달성률 3% (음의 예측력: 이미 알려진 종목)
#  → 현재 전략들이 "MA 위 + 수급 매수" 중심인데 이게 오히려 역효과
# ══════════════════════════════════════════════════════════════


