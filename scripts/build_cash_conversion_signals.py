#!/usr/bin/env python3
"""Build cash-conversion quality signals.

This table answers a practical trading question:
is reported revenue/profit turning into operating cash, or are receivables
building up faster than sales?
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "research_outputs"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db, stock_db_write_lock


def connect() -> sqlite3.Connection:
    return connect_stock_db(timeout=180, row_factory=sqlite3.Row)


def execute_with_retry(conn: sqlite3.Connection, sql: str, params=(), retries: int = 8):
    last = None
    for attempt in range(retries):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as exc:
            last = exc
            if "locked" not in str(exc).lower():
                raise
            time.sleep(min(2 ** attempt, 30))
    raise last


def quarter_end_date(year: int, quarter: int) -> date:
    month = quarter * 3
    day = 31 if month in (3, 12) else 30
    return date(year, month, day)


def pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or abs(prev) < 1e8:  # 1억원 미만의 미세 분모는 기저효과 노이즈 방지를 위해 제외
        return None
    val = (cur - prev) / abs(prev) * 100.0
    if val > 500.0:
        return 500.0
    if val < -100.0:
        return -100.0
    return round(val, 2)


def init_tables(conn: sqlite3.Connection) -> None:
    execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS cash_conversion_signals (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            sector_large TEXT,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL,
            fs_div TEXT NOT NULL,
            revenue REAL,
            operating_profit REAL,
            net_income REAL,
            operating_cf REAL,
            capex REAL,
            free_cf REAL,
            trade_receivable REAL,
            ocf_margin_pct REAL,
            fcf_margin_pct REAL,
            ocf_to_net_income_pct REAL,
            receivable_to_revenue_pct REAL,
            rolling4_revenue REAL,
            rolling4_operating_cf REAL,
            rolling4_free_cf REAL,
            rolling4_ocf_margin_pct REAL,
            rolling4_fcf_margin_pct REAL,
            rolling4_ocf_positive_quarters INTEGER,
            revenue_qoq_pct REAL,
            operating_cf_qoq_pct REAL,
            receivable_qoq_pct REAL,
            signal_type TEXT,
            signal_score INTEGER DEFAULT 0,
            risk_score INTEGER DEFAULT 0,
            signal_label TEXT,
            quality_flag TEXT DEFAULT 'ok',
            source_json TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, fs_div)
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(cash_conversion_signals)").fetchall()}
    for col, typ in {
        "rolling4_revenue": "REAL",
        "rolling4_operating_cf": "REAL",
        "rolling4_free_cf": "REAL",
        "rolling4_ocf_margin_pct": "REAL",
        "rolling4_fcf_margin_pct": "REAL",
        "rolling4_ocf_positive_quarters": "INTEGER",
        "revenue_mismatch_suspect": "INTEGER DEFAULT 0",
    }.items():
        if col not in existing:
            execute_with_retry(conn, f"ALTER TABLE cash_conversion_signals ADD COLUMN {col} {typ}")
    execute_with_retry(conn, """
        CREATE INDEX IF NOT EXISTS idx_cash_conversion_latest
        ON cash_conversion_signals(stock_code, fiscal_year DESC, fiscal_quarter DESC)
    """)
    execute_with_retry(conn, """
        CREATE TABLE IF NOT EXISTS cash_conversion_signal_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT,
            finished_at TEXT,
            rows_written INTEGER,
            stocks INTEGER,
            notes TEXT
        )
    """)
    conn.commit()


def load_financials(conn: sqlite3.Connection, since_year: int) -> dict:
    rows = conn.execute("""
        SELECT f.stock_code, u.stock_name, u.market, u.sector_large,
               f.year, f.quarter, COALESCE(f.report_type,'CFS') AS fs_div,
               f.revenue, f.operating_profit, f.net_income
        FROM financial_data f
        LEFT JOIN stock_universe u ON u.stock_code=f.stock_code
        WHERE f.year >= ?
          AND f.is_annual=0
          AND f.quarter BETWEEN 1 AND 4
          AND f.revenue IS NOT NULL
          AND ABS(f.revenue) > 0
          AND length(f.stock_code)=6
          AND f.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    """, (since_year,)).fetchall()
    out = {}
    today = date.today()
    for r in rows:
        y, q = int(r["year"]), int(r["quarter"])
        if quarter_end_date(y, q) > today:
            continue
        key = (r["stock_code"], y, q, r["fs_div"] or "CFS")
        out[key] = dict(r)
    return out


def load_cashflow(conn: sqlite3.Connection, since_year: int) -> dict:
    rows = conn.execute("""
        SELECT stock_code, year, quarter, COALESCE(report_type,'CFS') AS fs_div,
               COALESCE(operating_cf_q, operating_cf) AS operating_cf,
               COALESCE(capex_q, capex) AS capex,
               data_source
        FROM cash_flow_data
        WHERE year >= ?
          AND is_annual=0
          AND quarter BETWEEN 1 AND 4
          AND length(stock_code)=6
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
    """, (since_year,)).fetchall()
    out = {}
    today = date.today()
    for r in rows:
        y, q = int(r["year"]), int(r["quarter"])
        if quarter_end_date(y, q) > today:
            continue
        key = (r["stock_code"], y, q, r["fs_div"] or "CFS")
        out[key] = {
            "operating_cf": float(r["operating_cf"]) if r["operating_cf"] is not None else None,
            "capex": float(r["capex"]) if r["capex"] is not None else None,
            "cashflow_source": r["data_source"],
        }
    return out


def load_receivables(conn: sqlite3.Connection, since_year: int) -> dict:
    rows = conn.execute("""
        WITH ar AS (
            SELECT stock_code, year, quarter, COALESCE(report_type,'CFS') AS fs_div,
                   value AS trade_receivable,
                   1 AS priority
            FROM dart_bs_items
            WHERE item_key='trade_receivable'
              AND year >= ?
              AND quarter BETWEEN 1 AND 4
              AND value IS NOT NULL
              AND length(stock_code)=6
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            UNION ALL
            SELECT stock_code, fiscal_year AS year, fiscal_quarter AS quarter, COALESCE(fs_div,'CFS') AS fs_div,
                   value AS trade_receivable,
                   CASE
                     WHEN account_nm LIKE '%장기%' OR account_nm LIKE '%비유동%' OR account_nm LIKE '%총액%' OR account_nm LIKE '%Gross%' THEN 5
                     WHEN account_nm LIKE '%기타%' OR account_nm LIKE '%미수%' THEN 4
                     WHEN account_nm LIKE '%유동%' OR account_nm LIKE '%단기%' THEN 2
                     ELSE 3
                   END AS priority
            FROM dart_report_items_quarterly
            WHERE metric_name='trade_receivable'
              AND fiscal_year >= ?
              AND fiscal_quarter BETWEEN 1 AND 4
              AND value IS NOT NULL
              AND length(stock_code)=6
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND (
                account_nm LIKE '%매출채권%' OR
                account_nm LIKE '%외상매출금%' OR
                account_id LIKE '%TradeReceivable%'
              )
        )
        SELECT stock_code, year, quarter, fs_div, MAX(trade_receivable) AS trade_receivable
        FROM ar
        WHERE priority = (
          SELECT MIN(priority)
          FROM ar ar2
          WHERE ar2.stock_code=ar.stock_code
            AND ar2.year=ar.year
            AND ar2.quarter=ar.quarter
            AND ar2.fs_div=ar.fs_div
        )
        GROUP BY stock_code, year, quarter, fs_div
    """, (since_year, since_year)).fetchall()
    return {
        (r["stock_code"], int(r["year"]), int(r["quarter"]), r["fs_div"] or "CFS"): float(r["trade_receivable"])
        for r in rows
    }


FINANCIAL_SECTOR_HINTS = ("금융", "보험", "은행", "증권", "캐피탈", "카드")


def is_financial_sector(sector_large: str | None) -> bool:
    s = sector_large or ""
    return any(h in s for h in FINANCIAL_SECTOR_HINTS)


def classify(row: dict) -> tuple[str, int, int, str]:
    # 2026-09 수정: 은행/증권/보험 등 금융업은 예수금·대출 증감이 정상적인
    # 영업활동이라 일반 제조업 기준 "흑자인데 영업CF-" 로직으로 판정하면
    # 항상 위험군으로 오분류된다(반대로 예수금 유입이 큰 시기엔 항상
    # 최우수로 오분류). contract_advance_signals와 동일하게 업종 제외.
    if is_financial_sector(row.get("sector_large")):
        return "neutral", 0, 0, "금융업종 제외(현금전환 판정 대상 아님)"
    # 2026-09 수정: CFS/OFS 매출이 같은 (종목,분기)에서 극단적으로(10배+)
    # 어긋나면 financial_data 원본 파싱오류일 가능성이 높으므로 판정을
    # 보수적으로 보류한다(예: 037460 CFS 1.9조 vs OFS 154.7억).
    if row.get("revenue_mismatch_suspect"):
        return "neutral", 0, 0, "CFS/OFS 매출 불일치로 판정 보류(원본 데이터 확인 필요)"

    rev_qoq = row.get("revenue_qoq_pct")
    ocf = row.get("operating_cf")
    ni = row.get("net_income")
    ocf_margin = row.get("ocf_margin_pct")
    fcf_margin = row.get("fcf_margin_pct")
    ar_to_rev = row.get("receivable_to_revenue_pct")
    ar_qoq = row.get("receivable_qoq_pct")
    rolling_ocf_margin = row.get("rolling4_ocf_margin_pct")
    rolling_fcf_margin = row.get("rolling4_fcf_margin_pct")
    rolling_ocf_pos_q = row.get("rolling4_ocf_positive_quarters")

    good_parts: list[str] = []
    score = 0
    if ocf is not None and ocf > 0:
        score += 2
        good_parts.append("영업CF+")
    if ocf_margin is not None and ocf_margin >= 8:
        score += 2
        good_parts.append(f"OCF마진 {ocf_margin:.0f}%")
    elif ocf_margin is not None and ocf_margin >= 3:
        score += 1
        good_parts.append(f"OCF마진 {ocf_margin:.0f}%")
    if ni is not None and ni > 0 and ocf is not None and ocf >= ni:
        score += 2
        good_parts.append("이익보다 현금흐름 우수")
    if fcf_margin is not None and fcf_margin >= 0:
        score += 1
        good_parts.append("FCF+")
    if rolling_ocf_margin is not None and rolling_ocf_margin >= 5 and (rolling_ocf_pos_q or 0) >= 3:
        score += 2
        good_parts.append(f"4Q OCF마진 {rolling_ocf_margin:.0f}%")
    if rolling_fcf_margin is not None and rolling_fcf_margin >= 0:
        score += 1
        good_parts.append("4Q FCF+")
    if ar_qoq is not None and ar_qoq <= -5 and rev_qoq is not None and rev_qoq >= 0:
        score += 1
        good_parts.append("매출채권 감소")

    risk = 0
    risk_parts: list[str] = []
    if ni is not None and ni > 0 and ocf is not None and ocf < 0:
        risk += 4
        risk_parts.append("흑자인데 영업CF-")
    if ocf_margin is not None and ocf_margin <= -5:
        risk += 2
        risk_parts.append(f"OCF마진 {ocf_margin:.0f}%")
    if rolling_ocf_margin is not None and rolling_ocf_margin < 0 and (rolling_ocf_pos_q or 0) <= 1:
        risk += 3
        risk_parts.append(f"4Q OCF마진 {rolling_ocf_margin:.0f}%")
    if rolling_fcf_margin is not None and rolling_fcf_margin <= -10:
        risk += 1
        risk_parts.append(f"4Q FCF마진 {rolling_fcf_margin:.0f}%")
    if ar_qoq is not None and ar_qoq >= 30 and (rev_qoq is None or rev_qoq < 10):
        risk += 3
        risk_parts.append(f"매출채권QoQ+{ar_qoq:.0f}%")
    if ar_to_rev is not None and ar_to_rev >= 80:
        risk += 2
        risk_parts.append(f"매출채권/매출 {ar_to_rev:.0f}%")

    # 2026-09 수정: 매출채권 결측(quality_flag='missing_receivable')이어도
    # 영업CF 기반 항목만으로 만점(8점)을 달성할 수 있어 "완전한 현금전환주기
    # 판정"으로 오인되기 쉬웠다. 매출채권 정보가 없는 판정은 확신도를
    # 낮춰 만점 달성을 못 하도록 최대 6점으로 제한한다.
    if row.get("trade_receivable") is None:
        score = min(score, 6)
        risk = min(risk, 6)

    if risk >= 4:
        return "cash_risk", 0, min(risk, 8), "현금전환위험: " + " · ".join(risk_parts)
    if score >= 4:
        return "cash_quality", min(score, 8), 0, "현금전환양호: " + " · ".join(good_parts)
    return "neutral", 0, 0, "특이 신호 없음"


def _clamp_pct(val: float | None, lo: float = -500.0, hi: float = 500.0) -> float | None:
    if val is None:
        return None
    return max(lo, min(hi, val))


def build_rows(conn: sqlite3.Connection, since_year: int) -> list[dict]:
    fin = load_financials(conn, since_year)
    cf = load_cashflow(conn, since_year)
    ar = load_receivables(conn, since_year)

    # 2026-09 신규: CFS/OFS 매출 상호검증 — 같은 (종목,연도,분기)에서 두 값이
    # 존재하는데 10배 이상 어긋나면 financial_data 원본 파싱오류 가능성이
    # 높다(예: 037460 삼지전자 CFS 1.9조 vs OFS 154.7억, 122배 차이).
    revenue_by_period: dict[tuple[str, int, int], dict[str, float]] = defaultdict(dict)
    for (sc, y, q, fs), f in fin.items():
        rv = f.get("revenue")
        if rv:
            revenue_by_period[(sc, y, q)][fs] = float(rv)

    def _revenue_mismatch(sc: str, y: int, q: int) -> bool:
        pair = revenue_by_period.get((sc, y, q), {})
        cfs_v, ofs_v = pair.get("CFS"), pair.get("OFS")
        if not cfs_v or not ofs_v:
            return False
        hi, lo = max(cfs_v, ofs_v), min(cfs_v, ofs_v)
        return lo > 0 and (hi / lo) >= 10.0

    rows: list[dict] = []
    for key, f in fin.items():
        c = cf.get(key)
        if not c:
            continue
        sc, y, q, fs = key
        revenue = float(f["revenue"] or 0)
        ocf = c.get("operating_cf")
        capex = c.get("capex")
        free_cf = (ocf - abs(capex)) if ocf is not None and capex is not None else None
        receivable = ar.get(key)
        rows.append({
            "stock_code": sc,
            "stock_name": f.get("stock_name") or sc,
            "market": f.get("market"),
            "sector_large": f.get("sector_large"),
            "fiscal_year": y,
            "fiscal_quarter": q,
            "fs_div": fs,
            "revenue": revenue,
            "operating_profit": f.get("operating_profit"),
            "net_income": f.get("net_income"),
            "operating_cf": ocf,
            "capex": capex,
            "free_cf": free_cf,
            "trade_receivable": receivable,
            # 2026-09 수정: 마진 계산에 상한/하한(-500%~500%)을 걸어 극단치가
            # 필터·정렬을 지배하지 않도록 함(QoQ 계산 pct()와 동일한 캡 적용).
            "ocf_margin_pct": _clamp_pct(round(ocf / revenue * 100, 2)) if ocf is not None and revenue else None,
            "fcf_margin_pct": _clamp_pct(round(free_cf / revenue * 100, 2)) if free_cf is not None and revenue else None,
            "ocf_to_net_income_pct": round(ocf / f["net_income"] * 100, 2) if ocf is not None and f.get("net_income") else None,
            # 분기매출×4는 4분기 실합산(rolling4_revenue)이 아직 없을 때만 쓰는
            # 근사치 — 아래 2차 패스에서 rolling4_revenue가 확보되면 그걸로 재계산.
            "receivable_to_revenue_pct": round(receivable / (revenue * 4) * 100, 2) if receivable is not None and revenue else None,
            "revenue_mismatch_suspect": _revenue_mismatch(sc, y, q),
            "source": {"cashflow_source": c.get("cashflow_source"), "has_trade_receivable": receivable is not None},
        })

    series: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in rows:
        series[(r["stock_code"], r["fs_div"])].append(r)

    out: list[dict] = []
    for group in series.values():
        group.sort(key=lambda r: (r["fiscal_year"], r["fiscal_quarter"]))
        lookup = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in group}
        for r in group:
            y, q = r["fiscal_year"], r["fiscal_quarter"]
            prev_key = (y, q - 1) if q > 1 else (y - 1, 4)
            p = lookup.get(prev_key)
            r["revenue_qoq_pct"] = pct(r["revenue"], p["revenue"] if p else None)
            r["operating_cf_qoq_pct"] = pct(r["operating_cf"], p["operating_cf"] if p else None)
            r["receivable_qoq_pct"] = pct(r["trade_receivable"], p["trade_receivable"] if p else None)
            idx = group.index(r)
            window = group[max(0, idx - 3):idx + 1]
            if len(window) >= 4:
                rev4 = sum(float(x["revenue"] or 0) for x in window)
                ocf_values = [x["operating_cf"] for x in window if x["operating_cf"] is not None]
                fcf_values = [x["free_cf"] for x in window if x["free_cf"] is not None]
                ocf4 = sum(float(v) for v in ocf_values) if len(ocf_values) == 4 else None
                fcf4 = sum(float(v) for v in fcf_values) if len(fcf_values) == 4 else None
                r["rolling4_revenue"] = rev4
                r["rolling4_operating_cf"] = ocf4
                r["rolling4_free_cf"] = fcf4
                r["rolling4_ocf_margin_pct"] = _clamp_pct(round(ocf4 / rev4 * 100, 2)) if ocf4 is not None and rev4 else None
                r["rolling4_fcf_margin_pct"] = _clamp_pct(round(fcf4 / rev4 * 100, 2)) if fcf4 is not None and rev4 else None
                r["rolling4_ocf_positive_quarters"] = sum(1 for v in ocf_values if v > 0)
                # 2026-09 개선: 매출채권/매출 비율은 분기매출×4 근사 대신
                # 4분기 실합산(rolling4_revenue)이 확보되면 그걸로 재계산 —
                # 계절성/로트성 매출 기업(예 아바코, Q4매출>>Q1매출)의 왜곡을 줄임.
                if r["trade_receivable"] is not None and rev4:
                    r["receivable_to_revenue_pct"] = round(r["trade_receivable"] / rev4 * 100, 2)
            else:
                r["rolling4_revenue"] = None
                r["rolling4_operating_cf"] = None
                r["rolling4_free_cf"] = None
                r["rolling4_ocf_margin_pct"] = None
                r["rolling4_fcf_margin_pct"] = None
                r["rolling4_ocf_positive_quarters"] = None
            stype, score, risk, label = classify(r)
            r["signal_type"] = stype
            r["signal_score"] = score
            r["risk_score"] = risk
            r["signal_label"] = label
            r["quality_flag"] = "ok" if r["trade_receivable"] is not None else "missing_receivable"
            out.append(r)
    return out


def write_rows(conn: sqlite3.Connection, rows: list[dict], since_year: int) -> None:
    execute_with_retry(conn, "DELETE FROM cash_conversion_signals WHERE fiscal_year >= ?", (since_year,))
    # 2026-09 수정: 기존 named(:key) dict-파라미터 방식은 db_compat.py의
    # PostgresCompatCursor.execute()가 dict params를 tuple(dict)(=키 이름들의
    # 튜플)로 잘못 변환하는 버그와 만나 "the query has 0 placeholders but N
    # parameters were passed"로 항상 실패하고 있었다(named :key 플레이스홀더 자체를
    # 지원하는 변환로직이 db_compat.py에 전혀 없음 — 현금전환 신호가 39일간 실제로
    # 재구축되지 못했던 근본원인 중 하나). 검증된 positional(?) + tuple 방식으로 통일.
    conn.executemany("""
        INSERT OR REPLACE INTO cash_conversion_signals (
            stock_code, stock_name, market, sector_large, fiscal_year, fiscal_quarter, fs_div,
            revenue, operating_profit, net_income, operating_cf, capex, free_cf, trade_receivable,
            ocf_margin_pct, fcf_margin_pct, ocf_to_net_income_pct, receivable_to_revenue_pct,
            rolling4_revenue, rolling4_operating_cf, rolling4_free_cf,
            rolling4_ocf_margin_pct, rolling4_fcf_margin_pct, rolling4_ocf_positive_quarters,
            revenue_qoq_pct, operating_cf_qoq_pct, receivable_qoq_pct,
            signal_type, signal_score, risk_score, signal_label, quality_flag, revenue_mismatch_suspect,
            source_json, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?
        )
    """, [
        (
            r["stock_code"], r["stock_name"], r["market"], r["sector_large"], r["fiscal_year"], r["fiscal_quarter"], r["fs_div"],
            r["revenue"], r["operating_profit"], r["net_income"], r["operating_cf"], r["capex"], r["free_cf"], r["trade_receivable"],
            r["ocf_margin_pct"], r["fcf_margin_pct"], r["ocf_to_net_income_pct"], r["receivable_to_revenue_pct"],
            r["rolling4_revenue"], r["rolling4_operating_cf"], r["rolling4_free_cf"],
            r["rolling4_ocf_margin_pct"], r["rolling4_fcf_margin_pct"], r["rolling4_ocf_positive_quarters"],
            r["revenue_qoq_pct"], r["operating_cf_qoq_pct"], r["receivable_qoq_pct"],
            r["signal_type"], r["signal_score"], r["risk_score"], r["signal_label"], r["quality_flag"],
            int(bool(r.get("revenue_mismatch_suspect"))),
            json.dumps(r["source"], ensure_ascii=False),
            datetime.now().isoformat(timespec="seconds"),
        )
        for r in rows
    ])
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    conn.execute("""
        INSERT OR REPLACE INTO cash_conversion_signal_runs
        (run_id, started_at, finished_at, rows_written, stocks, notes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (run_id, run_id, datetime.now().isoformat(timespec="seconds"), len(rows), len({r["stock_code"] for r in rows}), f"since_year={since_year}"))
    conn.commit()


def write_report(rows: list[dict], since_year: int) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "run_id": run_id,
        "since_year": since_year,
        "rows": len(rows),
        "stocks": len({r["stock_code"] for r in rows}),
        "good": sum(1 for r in rows if r["signal_score"] > 0),
        "risk": sum(1 for r in rows if r["risk_score"] > 0),
    }
    (OUT_DIR / f"cash_conversion_signals_{run_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-year", type=int, default=2020)
    args = parser.parse_args()
    conn = connect()
    try:
        rows = build_rows(conn, args.since_year)
        with stock_db_write_lock("build_cash_conversion_signals", timeout=120) as locked:
            if not locked:
                print("cash_conversion_signals skipped: stock.db write lock busy")
                return 2
            init_tables(conn)
            write_rows(conn, rows, args.since_year)
            write_report(rows, args.since_year)
        print(f"cash_conversion_signals built: rows={len(rows)}, stocks={len({r['stock_code'] for r in rows})}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
