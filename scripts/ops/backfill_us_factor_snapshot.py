#!/usr/bin/env python3
"""
scripts/ops/backfill_us_factor_snapshot.py

기존 us_factor_snapshot의 NULL 필드들을 us_price_history + us_financial_data + us_cashflow_data로
일괄 백필합니다.

채움 대상:
  - graham_intrinsic, graham_discount
  - atr14, atr_stop_loss, atr_risk_pct
  - rs_score
  - high_52w, low_52w
  - return_1m, return_3m, return_6m, return_1y (NULL인 것만)
  - revenue_growth_yoy, op_income_growth_yoy, net_income_growth_yoy (NULL인 것만)
  - roe, roa, debt_to_equity (NULL인 것만)
  - fcf_yield (NULL인 것만)
  - total_score (재계산)
  - system_action (재계산)
"""

from __future__ import annotations
import sqlite3
import math
import logging
from pathlib import Path
from datetime import datetime

DB = Path('/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('backfill_us_factor')

MARKET_3M_BASELINE = 3.5
COMMIT_EVERY = 100


def safe_float(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def compute_atr14(closes: list) -> float | None:
    if len(closes) < 15:
        return None
    tr_list = [abs(closes[i] - closes[i - 1]) for i in range(len(closes) - 14, len(closes))]
    return sum(tr_list) / len(tr_list) if tr_list else None


def compute_system_action(total_score, above_200ma, rs_score, return_3m, graham_discount) -> str:
    sc = total_score or 0.0
    rs = rs_score or 0.0
    above = above_200ma or 0
    if sc >= 70 and above == 1 and rs > 0:
        return "BUY"
    elif sc >= 70 and above == 1:
        return "HOLD"
    elif sc >= 50 and above == 1:
        return "HOLD"
    elif sc < 40 or (above == 0 and sc < 50):
        return "SELL"
    else:
        return "WATCH"


def calc_total_score(row: dict) -> float:
    score = 0.0
    r1m  = row.get("return_1m") or 0
    r3m  = row.get("return_3m") or 0
    r6m  = row.get("return_6m") or 0
    r1y  = row.get("return_1y") or 0
    if r1m > 0:  score += 5
    if r3m > 5:  score += 7
    elif r3m > 0: score += 4
    if r6m > 10: score += 9
    elif r6m > 0: score += 5
    if r1y > 20: score += 9
    elif r1y > 0: score += 5

    opm   = row.get("op_margin") or 0
    roe   = row.get("roe") or 0
    rev_g = row.get("revenue_growth_yoy") or 0
    if opm > 20:  score += 10
    elif opm > 10: score += 6
    elif opm > 0:  score += 3
    if roe > 15:  score += 10
    elif roe > 8:  score += 6
    elif roe > 0:  score += 3
    if rev_g > 10: score += 10
    elif rev_g > 0: score += 5

    per  = row.get("per")
    pbr  = row.get("pbr")
    fcf  = row.get("fcf_yield") or 0
    if per is not None and 0 < per < 25: score += 7
    elif per is not None and 0 < per < 40: score += 4
    if pbr is not None and 0 < pbr < 3: score += 7
    elif pbr is not None and 0 < pbr < 6: score += 4
    if fcf > 3: score += 6
    elif fcf > 1: score += 3

    above = row.get("above_200ma") or 0
    rs    = row.get("rs_score") or 0
    if above == 1: score += 10
    if rs > 5:  score += 10
    elif rs > 0: score += 6

    return min(round(score, 2), 100.0)


def backfill(conn: sqlite3.Connection):
    cur = conn.cursor()
    today = datetime.now().strftime("%Y-%m-%d")

    # 처리 대상 ticker 목록
    tickers = [r[0] for r in cur.execute(
        "SELECT ticker FROM us_factor_snapshot ORDER BY ticker"
    ).fetchall()]
    logger.info(f"백필 대상: {len(tickers):,}개 ticker")

    # 재무 데이터 메모리 로드
    logger.info("재무 데이터 로딩 중...")
    fin_annual = {}   # ticker -> (eps, bps, opm, per, pbr, opi, rev, ni, eq, assets, liab)
    for r in cur.execute("""
        SELECT f1.ticker, f1.eps, f1.bps, f1.opm, f1.per, f1.pbr,
               f1.operating_income, f1.revenue, f1.net_income, f1.equity, f1.assets, f1.liabilities
        FROM us_financial_data f1
        INNER JOIN (
            SELECT ticker, MAX(period_end) AS pe FROM us_financial_data
            WHERE period_type='annual' GROUP BY ticker
        ) f2 ON f1.ticker=f2.ticker AND f1.period_end=f2.pe AND f1.period_type='annual'
    """).fetchall():
        fin_annual[r[0]] = r[1:]

    # 전년도 재무 (YoY)
    fin_prev = {}
    for r in cur.execute("""
        SELECT f1.ticker, f1.revenue, f1.operating_income, f1.net_income
        FROM us_financial_data f1
        INNER JOIN (
            SELECT ticker, MAX(period_end) AS pe FROM us_financial_data
            WHERE period_type='annual' GROUP BY ticker
        ) latest ON f1.ticker=latest.ticker
        INNER JOIN (
            SELECT ticker, MAX(period_end) AS pe FROM us_financial_data
            WHERE period_type='annual'
            GROUP BY ticker HAVING COUNT(*) >= 2
        ) has2 ON f1.ticker=has2.ticker
        WHERE f1.period_type='annual'
          AND f1.period_end < latest.pe
        ORDER BY f1.ticker, f1.period_end DESC
    """).fetchall():
        if r[0] not in fin_prev:
            fin_prev[r[0]] = r[1:]

    # 현금흐름 + 시총
    cf_map = {}
    for r in cur.execute("""
        SELECT c.ticker, c.free_cf
        FROM us_cashflow_data c
        INNER JOIN (
            SELECT ticker, MAX(period_end) AS pe FROM us_cashflow_data
            WHERE period_type='annual' GROUP BY ticker
        ) m ON c.ticker=m.ticker AND c.period_end=m.pe AND c.period_type='annual'
    """).fetchall():
        cf_map[r[0]] = safe_float(r[1])

    mcap_map = {r[0]: safe_float(r[1]) for r in cur.execute(
        "SELECT ticker, market_cap FROM us_stock_meta WHERE market_cap IS NOT NULL"
    ).fetchall()}

    # 기존 snapshot 값
    snap_map = {}
    for r in cur.execute("""
        SELECT ticker, price, above_200ma, return_1m, return_3m, return_6m, return_1y,
               op_margin, roe, roa, per, pbr, fcf_yield, debt_to_equity,
               revenue_growth_yoy, op_income_growth_yoy, net_income_growth_yoy,
               eps, bps
        FROM us_factor_snapshot
    """).fetchall():
        snap_map[r[0]] = {
            "price": safe_float(r[1]),
            "above_200ma": r[2],
            "return_1m": safe_float(r[3]),
            "return_3m": safe_float(r[4]),
            "return_6m": safe_float(r[5]),
            "return_1y": safe_float(r[6]),
            "op_margin": safe_float(r[7]),
            "roe": safe_float(r[8]),
            "roa": safe_float(r[9]),
            "per": safe_float(r[10]),
            "pbr": safe_float(r[11]),
            "fcf_yield": safe_float(r[12]),
            "debt_to_equity": safe_float(r[13]),
            "revenue_growth_yoy": safe_float(r[14]),
            "op_income_growth_yoy": safe_float(r[15]),
            "net_income_growth_yoy": safe_float(r[16]),
            "eps": safe_float(r[17]),
            "bps": safe_float(r[18]),
        }

    logger.info("가격 이력에서 팩터 계산 중...")
    ok = 0

    for i, tk in enumerate(tickers, 1):
        try:
            closes_asc = [
                safe_float(r[0]) for r in cur.execute(
                    "SELECT close FROM us_price_history WHERE ticker=? AND close IS NOT NULL ORDER BY date ASC",
                    (tk,)
                ).fetchall()
                if safe_float(r[0]) is not None and safe_float(r[0]) > 0
            ]

            if len(closes_asc) < 5:
                continue

            snap = snap_map.get(tk, {})
            curr_p = closes_asc[-1]

            # 이동평균 & 52주
            ma200 = sum(closes_asc[-200:]) / 200.0 if len(closes_asc) >= 200 else None
            above_200ma = snap.get("above_200ma")
            if above_200ma is None:
                above_200ma = 1 if (ma200 and curr_p > ma200) else 0

            w52 = closes_asc[-252:] if len(closes_asc) >= 252 else closes_asc
            high_52w = max(w52)
            low_52w  = min(w52)

            # 수익률
            def _ret(n):
                if len(closes_asc) > n and closes_asc[-n - 1] > 0:
                    return (closes_asc[-1] - closes_asc[-n - 1]) / closes_asc[-n - 1] * 100.0
                return None

            ret_1m = snap.get("return_1m") or _ret(21)
            ret_3m = snap.get("return_3m") or _ret(63)
            ret_6m = snap.get("return_6m") or _ret(126)
            ret_1y = snap.get("return_1y") or _ret(252)

            # ATR14
            atr14     = compute_atr14(closes_asc)
            atr_stop  = (curr_p - 2.0 * atr14) if atr14 else None
            atr_risk  = ((curr_p - atr_stop) / curr_p * 100.0) if atr_stop else None

            # RS
            rs_score = None
            if len(closes_asc) >= 63:
                stock_3m = (closes_asc[-1] - closes_asc[-63]) / closes_asc[-63] * 100.0
                rs_score = stock_3m - MARKET_3M_BASELINE

            # 재무
            fin = fin_annual.get(tk)
            eps_val = snap.get("eps") or (safe_float(fin[0]) if fin else None)
            bps_val = snap.get("bps") or (safe_float(fin[1]) if fin else None)
            opm_val = snap.get("op_margin") or (safe_float(fin[2]) if fin else None)
            per_val = snap.get("per") or (safe_float(fin[3]) if fin else None)
            pbr_val = snap.get("pbr") or (safe_float(fin[4]) if fin else None)
            opi_val = safe_float(fin[5]) if fin else None
            rev_val = safe_float(fin[6]) if fin else None
            ni_val  = safe_float(fin[7]) if fin else None
            eq_val  = safe_float(fin[8]) if fin else None
            ast_val = safe_float(fin[9]) if fin else None
            lib_val = safe_float(fin[10]) if fin else None

            # YoY
            prev = fin_prev.get(tk)
            rev_g = snap.get("revenue_growth_yoy")
            opi_g = snap.get("op_income_growth_yoy")
            ni_g  = snap.get("net_income_growth_yoy")
            if prev:
                prev_rev = safe_float(prev[0])
                prev_opi = safe_float(prev[1])
                prev_ni  = safe_float(prev[2])
                if rev_g is None and prev_rev and prev_rev != 0 and rev_val:
                    rev_g = (rev_val - prev_rev) / abs(prev_rev) * 100.0
                if opi_g is None and prev_opi and prev_opi != 0 and opi_val:
                    opi_g = (opi_val - prev_opi) / abs(prev_opi) * 100.0
                if ni_g is None and prev_ni and prev_ni != 0 and ni_val:
                    ni_g = (ni_val - prev_ni) / abs(prev_ni) * 100.0

            roe_val = snap.get("roe") or (
                (ni_val / eq_val * 100.0) if (ni_val and eq_val and eq_val != 0) else None
            )
            roa_val = snap.get("roa") or (
                (ni_val / ast_val * 100.0) if (ni_val and ast_val and ast_val != 0) else None
            )
            de_val  = snap.get("debt_to_equity") or (
                (lib_val / eq_val) if (lib_val and eq_val and eq_val > 0) else None
            )

            # FCF yield
            fcf_yield = snap.get("fcf_yield")
            if fcf_yield is None:
                fcf = cf_map.get(tk)
                mcap = mcap_map.get(tk)
                if fcf and mcap and mcap > 0:
                    fcf_yield = fcf / mcap * 100.0

            # Graham
            graham_int  = None
            graham_disc = None
            if eps_val and bps_val and eps_val > 0 and bps_val > 0:
                graham_int = math.sqrt(22.5 * eps_val * bps_val)
                if curr_p > 0 and graham_int > 0:
                    graham_disc = (graham_int - curr_p) / graham_int * 100.0

            # 종합 점수 재계산
            factor_row = {
                "return_1m": ret_1m, "return_3m": ret_3m,
                "return_6m": ret_6m, "return_1y": ret_1y,
                "op_margin": opm_val, "roe": roe_val,
                "revenue_growth_yoy": rev_g,
                "per": per_val, "pbr": pbr_val,
                "fcf_yield": fcf_yield,
                "above_200ma": above_200ma, "rs_score": rs_score,
            }
            total_score   = calc_total_score(factor_row)
            system_action = compute_system_action(total_score, above_200ma, rs_score, ret_3m, graham_disc)

            cur.execute("""
                UPDATE us_factor_snapshot
                SET
                    high_52w              = ?,
                    low_52w               = ?,
                    above_200ma           = ?,
                    return_1m             = COALESCE(return_1m, ?),
                    return_3m             = COALESCE(return_3m, ?),
                    return_6m             = COALESCE(return_6m, ?),
                    return_1y             = COALESCE(return_1y, ?),
                    atr14                 = ?,
                    atr_stop_loss         = ?,
                    atr_risk_pct          = ?,
                    rs_score              = ?,
                    graham_intrinsic      = ?,
                    graham_discount       = ?,
                    op_margin             = COALESCE(op_margin, ?),
                    roe                   = COALESCE(roe, ?),
                    roa                   = COALESCE(roa, ?),
                    revenue_growth_yoy    = COALESCE(revenue_growth_yoy, ?),
                    op_income_growth_yoy  = COALESCE(op_income_growth_yoy, ?),
                    net_income_growth_yoy = COALESCE(net_income_growth_yoy, ?),
                    fcf_yield             = COALESCE(fcf_yield, ?),
                    debt_to_equity        = COALESCE(debt_to_equity, ?),
                    eps                   = COALESCE(eps, ?),
                    bps                   = COALESCE(bps, ?),
                    total_score           = ?,
                    system_action         = ?,
                    updated_at            = CURRENT_TIMESTAMP
                WHERE ticker = ?
            """, (
                high_52w, low_52w, above_200ma,
                ret_1m, ret_3m, ret_6m, ret_1y,
                atr14, atr_stop, atr_risk,
                rs_score,
                graham_int, graham_disc,
                opm_val, roe_val, roa_val,
                rev_g, opi_g, ni_g,
                fcf_yield, de_val,
                eps_val, bps_val,
                total_score, system_action,
                tk,
            ))
            ok += 1

            if ok % COMMIT_EVERY == 0:
                conn.commit()
                logger.info(f"[{i}/{len(tickers)}] {ok}개 완료...")

        except Exception as e:
            logger.debug(f"{tk} 실패: {e}")
            continue

    conn.commit()
    logger.info(f"백필 완료: {ok}/{len(tickers)}개 처리")


def print_stats(conn: sqlite3.Connection):
    cur = conn.cursor()
    total = cur.execute("SELECT COUNT(*) FROM us_factor_snapshot").fetchone()[0]
    logger.info(f"\n=== us_factor_snapshot 채움률 ({total}행) ===")
    cols = [
        "return_1m", "return_3m", "rs_score", "atr14", "graham_intrinsic",
        "high_52w", "roe", "roa", "revenue_growth_yoy", "fcf_yield",
        "debt_to_equity", "total_score", "system_action",
    ]
    for c in cols:
        null = cur.execute(f"SELECT COUNT(*) FROM us_factor_snapshot WHERE {c} IS NULL").fetchone()[0]
        pct  = (total - null) / total * 100 if total else 0
        logger.info(f"  {c:30s}: {total-null:5}/{total} ({pct:.0f}%)")

    logger.info("\n=== system_action 분포 ===")
    for r in cur.execute("SELECT system_action, COUNT(*) FROM us_factor_snapshot GROUP BY system_action ORDER BY 2 DESC").fetchall():
        logger.info(f"  {r[0]}: {r[1]}")


def main():
    conn = sqlite3.connect(str(DB), timeout=300)
    conn.execute("PRAGMA busy_timeout=300000")
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        logger.info("=== BEFORE BACKFILL ===")
        print_stats(conn)
        backfill(conn)
        logger.info("=== AFTER BACKFILL ===")
        print_stats(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
