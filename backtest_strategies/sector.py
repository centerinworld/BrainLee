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

from backtest_common import (
    DB_PATH,
    _SECTOR_GROUPS,
    _net_profit,
    _record_run_spec,
    _register_execution_artifacts,
    init_backtest_db,
    logger,
    sqlite3,
)

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
    run_name = run_name or f"V-SECTOR섹터집중 {start_date[:7]}~{end_date[:7]}"
    run_id = run_id or str(uuid.uuid4())[:8]
    _record_run_spec(
        run_id, "sector_focus", "sector_v3_cashledger_20260714",
        {"buy_threshold": buy_threshold, "exit_threshold": exit_threshold,
         "rebalance_days": rebalance_days, "stop": stop, "trail": trail, "tp": tp,
         "min_sector_hold_days": min_sector_hold_days, "strict_exec": strict_exec,
         "per_stock": per_stock, "max_positions": max_positions,
         "start": start_date, "end": end_date},
        signal_timing="close_D",
        execution_timing=("next_open" if strict_exec else "same_close"),
        market_cap_mode="asof_approx",  # 2026-08-13: 리더선정 스코어(sel_score)의 기관집중도
        # 계산에 쓰이는 시총을 security_share_history 기반 정확한 as-of 값으로 교체(6기간
        # 재검증 avg6 29.98%→27.62%, 5/6양수 유지 — 소폭변동, 일부기간 오히려 개선).
        # ⚠️ 단, _SECTOR_GROUPS 자체(10업종 70종목 후보군)는 여전히 현재시점 수동선정이라
        # "pit"(완전 PIT) 등급까지는 도달 불가 — approx로 정직하게 표기.
        allocation_rule="fixed_slot",
    )

    conn = sqlite3.connect(DB_PATH, timeout=120)
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
        price_data: dict = {}  # code → {date: (close, high, low)}
        rows_p = conn.execute(
            "SELECT stock_code, date, close, high, low, open FROM price_history "
            "WHERE stock_code IN ({}) AND date>=? AND date<=? AND close>0 ORDER BY date".format(
                ",".join("?" * len(all_codes))),
            all_codes + [start_date, end_date]
        ).fetchall()
        for r in rows_p:
            c, d, cl, hi, lo, op = r
            if c not in price_data:
                price_data[c] = {}
            price_data[c][d] = (float(cl), float(hi) if hi else float(cl), float(lo) if lo else float(cl),
                                float(op) if op and op > 0 else float(cl))

        # 영업일 목록
        trade_dates = sorted(set(r[1] for r in rows_p if r[1] >= start_date))

        # 시총 맵 (거래비용 슬리피지 티어용)
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
        sec_pending_sells: list = []  # strict_exec: (code, reason)
        sec_pending_buys: list = []   # strict_exec: (code, sector_key, meta)

        for i, trade_date in enumerate(trade_dates):
            # ── strict_exec: 전일 신호 → 오늘 시가 체결 ──
            if strict_exec:
                _still = []
                for code, reason in sec_pending_sells:
                    if code not in positions:
                        continue
                    pdata = price_data.get(code, {}).get(trade_date)
                    if pdata is None:
                        _still.append((code, reason)); continue
                    px = pdata[3]
                    pos = positions.pop(code)
                    sector_assignments.pop(code, None)
                    _pnl_amt, _net = _net_profit(pos["buy_price"], px, pos.get("qty", 1), mc_map.get(code, 1000))
                    cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                    all_trades.append({"date": trade_date, "code": code, "action": "SELL",
                                       "price": px, "pnl_pct": round(_net, 2), "reason": reason})
                sec_pending_sells = _still
                for code, sector_key, meta in sec_pending_buys:
                    if code in positions or len(positions) >= max_positions:
                        continue
                    pdata = price_data.get(code, {}).get(trade_date)
                    if pdata is None:
                        continue  # 당일 미거래 → 주문 만료
                    px = pdata[3]
                    budget = min(per_stock, cash * 0.99)
                    qty = int(budget / px)
                    if qty < 1 or qty * px > cash:
                        continue  # 현금 부족 → 주문 거부 (현금 음수 금지)
                    cash -= qty * px
                    positions[code] = {"buy_price": px, "peak": px, "qty": qty,
                                       "sector": sector_key, "entry_date": trade_date,
                                       "entry_sector_score": meta.get("sector_score", 0), "pyramid_adds": 0}
                    sector_assignments[code] = sector_key
                    all_trades.append({"date": trade_date, "code": code, "action": "BUY",
                                       "price": px, "sector": sector_key, **meta})
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
                if ret <= stop:
                    sell_reason = f"손절{ret*100:.1f}%"
                elif trail_cur > 0.05 and trail_dd <= trail:
                    sell_reason = f"추적손절{trail_dd*100:.1f}%"
                elif ret >= tp:
                    sell_reason = f"익절{ret*100:.1f}%"

                if sell_reason:
                    to_sell.append((code, cur, sell_reason))

            if strict_exec:
                _queued = {c for c, _ in sec_pending_sells}
                for code, sell_price, reason in to_sell:
                    if code not in _queued:
                        sec_pending_sells.append((code, reason))
            else:
                for code, sell_price, reason in to_sell:
                    pos = positions.pop(code)
                    sector_assignments.pop(code, None)
                    _pnl_amt, _net = _net_profit(pos["buy_price"], sell_price, pos.get("qty", 1), mc_map.get(code, 1000))
                    cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                    all_trades.append({
                        "date": trade_date, "code": code, "action": "SELL",
                        "price": sell_price, "pnl_pct": round(_net, 2), "reason": reason
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

                    # OP YoY (섹터 내 종목 중위값)
                    cur_yr = str(int(trade_date[:4]))
                    prv_yr = str(int(trade_date[:4]) - 1)
                    op_rows_s = conn.execute(
                        f"SELECT stock_code, operating_profit FROM financial_data "
                        f"WHERE stock_code IN {ph_s} AND year=? AND is_annual=1 AND operating_profit IS NOT NULL",
                        codes_s + [cur_yr]
                    ).fetchall()
                    op_prev_s = {r[0]: r[1] for r in conn.execute(
                        f"SELECT stock_code, operating_profit FROM financial_data "
                        f"WHERE stock_code IN {ph_s} AND year=? AND is_annual=1 AND operating_profit IS NOT NULL",
                        codes_s + [prv_yr]
                    ).fetchall()}
                    yoys = []
                    for code_s, op_c in op_rows_s:
                        op_p = op_prev_s.get(code_s)
                        if op_p and op_p != 0:
                            raw = (op_c - op_p) / abs(op_p) * 100
                            yoys.append(min(max(raw, -200), 2000))
                    med_yoy = sorted(yoys)[len(yoys)//2] if yoys else 0.0

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
                            _pnl_amt, pnl = _net_profit(pos["buy_price"], sell_p, pos.get("qty", 1), mc_map.get(code, 1000))
                            cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                            all_trades.append({
                                "date": trade_date, "code": code, "action": "SECTOR_EXIT",
                                "price": sell_p, "pnl_pct": round(pnl, 2),
                                "reason": f"섹터점수하락{scores.get(sec,0):.0f}→EXIT(보유{hold_days}일)"
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
                        add_qty = int(add_budget / add_px)
                        if add_qty < 1 or add_qty * add_px > cash:
                            continue  # 현금 부족 → 스킵(음수 금지)
                        cash -= add_qty * add_px
                        old_qty = pos["qty"]
                        new_qty = old_qty + add_qty
                        # 가중평균 단가로 원가 재계산 — 이후 손절/추적손절/익절 판단이 이 기준으로 이뤄짐
                        pos["buy_price"] = (pos["buy_price"] * old_qty + add_px * add_qty) / new_qty
                        pos["qty"] = new_qty
                        pos["entry_sector_score"] = cur_score  # 다음 추가매수는 이 시점 대비 재상승 요구
                        pos["pyramid_adds"] = pos.get("pyramid_adds", 0) + 1
                        all_trades.append({
                            "date": trade_date, "code": code, "action": "PYRAMID_ADD",
                            "price": add_px, "sector": sec,
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
                        mktcap_r = (_sh_r * p_now[0] / 1e8) if _sh_r > 0 else 1000
                        inst_int_r = inst3m_r / max(1, mktcap_r) * 100
                        # RS 리더 점수 (3M 모멘텀 60% + 기관집중도 40%)
                        sel_score = rs3m * 0.6 + inst_int_r * 40
                        if pick_ta_bonus is not None:
                            # 직전 공시분기 첫 흑자전환 (as-of 표준 공시일정 기준, 룩어헤드 없음)
                            ni_rows = conn.execute("""
                                SELECT net_income FROM financial_data
                                WHERE stock_code=? AND is_annual=0 AND quarter BETWEEN 1 AND 4
                                  AND net_income IS NOT NULL
                                  AND (CASE WHEN quarter=1 THEN printf('%d-05-15', year)
                                            WHEN quarter=2 THEN printf('%d-08-15', year)
                                            WHEN quarter=3 THEN printf('%d-11-15', year)
                                            ELSE printf('%d-02-15', year+1) END) <= ?
                                ORDER BY year DESC, quarter DESC LIMIT 4
                            """, (c, as_of)).fetchall()
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
                            continue
                        buy_p = pdata[0]
                        budget = min(per_stock, cash * 0.99)
                        qty = int(budget / buy_p)
                        if qty < 1 or qty * buy_p > cash:
                            continue  # 현금 부족 → 주문 거부
                        cash -= qty * buy_p
                        positions[code] = {"buy_price": buy_p, "peak": buy_p, "qty": qty,
                                           "sector": sector_key, "entry_date": trade_date,
                                           "entry_sector_score": _meta.get("sector_score", 0), "pyramid_adds": 0}
                        sector_assignments[code] = sector_key
                        all_trades.append({
                            "date": trade_date, "code": code, "action": "BUY",
                            "price": buy_p, "sector": sector_key, **_meta,
                        })
                        n_slots -= 1

        # 마지막 날 청산 (현금원장 방식)
        last_date = trade_dates[-1] if trade_dates else end_date
        for code, pos in positions.items():
            pdata = price_data.get(code, {}).get(last_date) or price_data.get(code, {})
            if isinstance(pdata, dict):
                last = sorted(pdata.keys())[-1] if pdata else None
                pdata = pdata.get(last) if last else None
            if pdata:
                sell_p = pdata[0]
                _pnl_amt, pnl = _net_profit(pos["buy_price"], sell_p, pos.get("qty", 1), mc_map.get(code, 1000))
                cash += pos["buy_price"] * pos.get("qty", 1) + _pnl_amt
                all_trades.append({"date": last_date, "code": code, "action": "FINAL",
                                   "price": sell_p, "pnl_pct": round(pnl, 2), "reason": "종료청산"})
            else:
                # 시세 없음(거래정지 등) → 매수원금 그대로 환입하지 않고 전액 손실 처리 대신
                # 마지막 유효가 부재를 보수적으로 기록 (stale mark 방지: 원금 미환입)
                all_trades.append({"date": last_date, "code": code, "action": "FINAL",
                                   "price": None, "pnl_pct": -100.0, "reason": "시세부재(보수적 전액손실 처리)"})

        # 수익률 계산 (투자원금 기준)
        n_buy = sum(1 for t in all_trades if t["action"] == "BUY")
        n_sell = sum(1 for t in all_trades if t["action"] in ("SELL", "SECTOR_EXIT", "FINAL"))
        sell_trades = [t for t in all_trades if "pnl_pct" in t and t["action"] != "BUY"]
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
                   f"평균거래{avg_trade_return:.1f}% | 승률{win_rate:.0f}% | KOSPI대비α{alpha:+.1f}%")

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



