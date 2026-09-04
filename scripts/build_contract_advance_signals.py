#!/usr/bin/env python3
"""Build contract-liability / advance-payment leading signal tables.

This turns raw DART balance-sheet item rows into a stock-quarter signal that can
be used beside order-contract disclosures:
- contract_liabilities: 계약부채/초과청구공사
- advances_received: 선수금/선수수익
- contract_assets: 계약자산/미청구공사

Interpretation:
- gross_customer_funding = contract_liabilities + advances_received
- net_customer_funding = gross_customer_funding - contract_assets

Large positive QoQ/YoY growth in gross funding can be a revenue lead signal, but
financial/insurance contract liabilities are flagged and not scored strongly.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db, stock_db_write_lock

METRICS = {"contract_liabilities", "advances_received", "contract_assets"}

TOTAL_NAMES = {
    "contract_liabilities": {"계약부채", "확정계약부채", "초과청구공사"},
    "advances_received": {"선수금", "선수수익", "공사 및 분양선수금", "공사및분양선수금"},
    "contract_assets": {"계약자산", "확정계약자산", "미청구공사"},
}

COMPONENT_HINTS = {
    "유동",
    "비유동",
    "장기",
    "단기",
    "유동성",
}

FINANCIAL_HINTS = {
    "보험계약",
    "재보험계약",
    "투자계약",
    "변액보험",
    "무배당보험",
    "유배당보험",
}


@dataclass
class MetricPick:
    value: float | None
    account_names: list[str]
    method: str


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


def init_tables(conn: sqlite3.Connection) -> None:
    execute_with_retry(conn,
        """
        CREATE TABLE IF NOT EXISTS contract_advance_signals (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            sector_large TEXT,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL,
            fs_div TEXT NOT NULL,
            contract_liabilities REAL,
            advances_received REAL,
            contract_assets REAL,
            gross_customer_funding REAL,
            net_customer_funding REAL,
            revenue REAL,
            gross_to_revenue_pct REAL,
            net_to_revenue_pct REAL,
            gross_qoq_pct REAL,
            gross_yoy_pct REAL,
            net_qoq_pct REAL,
            net_yoy_pct REAL,
            signal_score INTEGER DEFAULT 0,
            signal_label TEXT,
            quality_flag TEXT,
            source_accounts_json TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, fs_div)
        )
        """
    )
    execute_with_retry(conn,
        """
        CREATE INDEX IF NOT EXISTS idx_contract_advance_signals_latest
        ON contract_advance_signals(stock_code, fiscal_year DESC, fiscal_quarter DESC)
        """
    )
    execute_with_retry(conn,
        """
        CREATE TABLE IF NOT EXISTS contract_advance_signal_runs (
            run_id TEXT PRIMARY KEY,
            started_at TEXT,
            finished_at TEXT,
            rows_written INTEGER,
            stocks INTEGER,
            notes TEXT
        )
        """
    )
    conn.commit()


def choose_metric(rows: list[sqlite3.Row], metric_name: str) -> MetricPick:
    if not rows:
        return MetricPick(None, [], "missing")

    clean = [r for r in rows if r["value"] is not None]
    if not clean:
        return MetricPick(None, [r["account_nm"] for r in rows], "all_null")

    def abs_value(row: sqlite3.Row) -> float:
        return abs(float(row["value"] or 0))

    total_rows = [
        r for r in clean
        if str(r["account_nm"]).strip() in TOTAL_NAMES.get(metric_name, set())
    ]
    if total_rows:
        # DART can expose current and non-current balances under one account
        # name with distinct account IDs.  Sum that group, but do not add
        # alternate labels that can represent the same disclosed balance.
        totals_by_name: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for row in total_rows:
            totals_by_name[str(row["account_nm"]).strip()].append(row)
        picked_rows = max(
            totals_by_name.values(),
            key=lambda group: abs(sum(float(row["value"] or 0) for row in group)),
        )
        return MetricPick(
            sum(float(row["value"] or 0) for row in picked_rows),
            [row["account_nm"] for row in picked_rows],
            "sum_same_name_components",
        )

    non_component = [
        r for r in clean
        if not any(h in str(r["account_nm"]) for h in COMPONENT_HINTS)
    ]
    if non_component:
        picked = max(non_component, key=abs_value)
        return MetricPick(float(picked["value"] or 0), [picked["account_nm"]], "largest_non_component")

    total = sum(float(r["value"] or 0) for r in clean)
    return MetricPick(total, [r["account_nm"] for r in clean], "sum_components")


def is_financial_like(account_names: list[str], sector_large: str | None) -> bool:
    joined = " ".join(account_names)
    if any(h in joined for h in FINANCIAL_HINTS):
        return True
    sector = sector_large or ""
    return "금융" in sector or "보험" in sector


def score_signal(
    gross: float | None,
    net: float | None,
    revenue: float | None,
    gross_qoq: float | None,
    gross_yoy: float | None,
    net_qoq: float | None,
    financial_like: bool,
) -> tuple[int, str]:
    if financial_like:
        return 0, "금융/보험성 계약부채 제외"

    score = 0
    labels: list[str] = []
    gross_to_rev = gross / revenue * 100 if gross and revenue and revenue > 0 else None
    net_to_rev = net / revenue * 100 if net is not None and revenue and revenue > 0 else None

    if gross_to_rev is not None:
        if gross_to_rev >= 50:
            score += 3
            labels.append(f"고객선수성부채/매출 {gross_to_rev:.0f}%")
        elif gross_to_rev >= 20:
            score += 2
            labels.append(f"고객선수성부채/매출 {gross_to_rev:.0f}%")
        elif gross_to_rev >= 10:
            score += 1
            labels.append(f"고객선수성부채/매출 {gross_to_rev:.0f}%")

    if gross_qoq is not None:
        if gross_qoq >= 100:
            score += 3
            labels.append(f"QoQ +{gross_qoq:.0f}%")
        elif gross_qoq >= 50:
            score += 2
            labels.append(f"QoQ +{gross_qoq:.0f}%")
        elif gross_qoq >= 20:
            score += 1
            labels.append(f"QoQ +{gross_qoq:.0f}%")

    if gross_yoy is not None:
        if gross_yoy >= 100:
            score += 2
            labels.append(f"YoY +{gross_yoy:.0f}%")
        elif gross_yoy >= 30:
            score += 1
            labels.append(f"YoY +{gross_yoy:.0f}%")

    if net_qoq is not None and net_qoq >= 50:
        score += 1
        labels.append(f"순선수성잔액 QoQ +{net_qoq:.0f}%")

    if net_to_rev is not None and net_to_rev < -20:
        score = max(0, score - 2)
        labels.append("계약자산 부담 큼")

    return min(score, 10), " / ".join(labels) if labels else "특이 신호 없음"


def pct_change(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or abs(prev) < 1e-9:
        return None
    return round((cur - prev) / abs(prev) * 100, 2)


def load_source_rows(conn: sqlite3.Connection, since_year: int) -> dict:
    rows = conn.execute(
        """
        SELECT d.stock_code, u.stock_name, u.market, u.sector_large,
               d.fiscal_year, d.fiscal_quarter, d.fs_div,
               d.metric_name, d.account_nm, d.value
        FROM dart_report_items_quarterly d
        LEFT JOIN stock_universe u ON u.stock_code=d.stock_code
        WHERE d.metric_name IN ('contract_liabilities', 'advances_received', 'contract_assets')
          AND d.fiscal_year >= ?
          AND length(d.stock_code)=6
          AND d.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """,
        (since_year,),
    ).fetchall()

    grouped: dict = defaultdict(lambda: defaultdict(list))
    meta: dict = {}
    for r in rows:
        key = (r["stock_code"], r["fiscal_year"], r["fiscal_quarter"], r["fs_div"])
        grouped[key][r["metric_name"]].append(r)
        meta[key] = {
            "stock_name": r["stock_name"],
            "market": r["market"],
            "sector_large": r["sector_large"],
        }
    return {"grouped": grouped, "meta": meta}


def load_revenue(conn: sqlite3.Connection) -> dict:
    # is_annual=0 필수: quarter=4 슬롯에는 진짜 4분기 단독매출(is_annual=0)과
    # 연간 누계매출(is_annual=1)이 함께 존재해, 이 필터 없이 MAX(ABS(revenue))를
    # 취하면 항상 더 큰 연간누계값이 분모로 잘못 채택된다(2026-09 발견/수정).
    rows = conn.execute(
        """
        SELECT stock_code, year, quarter, report_type, MAX(ABS(revenue)) AS revenue
        FROM financial_data
        WHERE report_type IN ('CFS', 'OFS')
          AND revenue IS NOT NULL
          AND ABS(revenue) > 0
          AND is_annual = 0
        GROUP BY stock_code, year, quarter, report_type
        """
    ).fetchall()
    return {
        (r["stock_code"], r["year"], r["quarter"], r["report_type"]): float(r["revenue"])
        for r in rows
    }


def build_rows(conn: sqlite3.Connection, since_year: int) -> list[dict]:
    source = load_source_rows(conn, since_year)
    grouped = source["grouped"]
    meta = source["meta"]
    revenue_map = load_revenue(conn)

    base_rows: list[dict] = []
    today = date.today()
    for key, by_metric in grouped.items():
        stock_code, fy, fq, fs_div = key
        if quarter_end_date(int(fy), int(fq)) > today:
            continue
        picks = {metric: choose_metric(by_metric.get(metric, []), metric) for metric in METRICS}
        sector_large = meta[key].get("sector_large")
        account_names = []
        for pick in picks.values():
            account_names.extend(pick.account_names)

        contract_liabilities = picks["contract_liabilities"].value
        advances = picks["advances_received"].value
        assets = picks["contract_assets"].value
        gross = (contract_liabilities or 0) + (advances or 0)
        net = gross - (assets or 0)
        revenue = revenue_map.get((stock_code, fy, fq, fs_div))
        financial_like = is_financial_like(account_names, sector_large)
        quality = "financial_like_excluded" if financial_like else "ok"
        if not account_names:
            quality = "no_source_account"

        base_rows.append({
            "stock_code": stock_code,
            "stock_name": meta[key].get("stock_name") or stock_code,
            "market": meta[key].get("market"),
            "sector_large": sector_large,
            "fiscal_year": fy,
            "fiscal_quarter": fq,
            "fs_div": fs_div,
            "contract_liabilities": contract_liabilities,
            "advances_received": advances,
            "contract_assets": assets,
            "gross_customer_funding": gross,
            "net_customer_funding": net,
            "revenue": revenue,
            "gross_to_revenue_pct": round(gross / revenue * 100, 2) if revenue else None,
            "net_to_revenue_pct": round(net / revenue * 100, 2) if revenue else None,
            "quality_flag": quality,
            "source_accounts": {
                metric: {
                    "value": picks[metric].value,
                    "accounts": picks[metric].account_names,
                    "method": picks[metric].method,
                }
                for metric in sorted(METRICS)
            },
        })

    rows_by_series: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in base_rows:
        rows_by_series[(row["stock_code"], row["fs_div"])].append(row)

    final_rows: list[dict] = []
    for _, rows in rows_by_series.items():
        rows.sort(key=lambda r: (r["fiscal_year"], r["fiscal_quarter"]))
        lookup = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in rows}
        for row in rows:
            fy, fq = row["fiscal_year"], row["fiscal_quarter"]
            prev_q = (fy, fq - 1) if fq > 1 else (fy - 1, 4)
            prev_y = (fy - 1, fq)
            pq = lookup.get(prev_q)
            py = lookup.get(prev_y)
            row["gross_qoq_pct"] = pct_change(row["gross_customer_funding"], pq["gross_customer_funding"] if pq else None)
            row["gross_yoy_pct"] = pct_change(row["gross_customer_funding"], py["gross_customer_funding"] if py else None)
            row["net_qoq_pct"] = pct_change(row["net_customer_funding"], pq["net_customer_funding"] if pq else None)
            row["net_yoy_pct"] = pct_change(row["net_customer_funding"], py["net_customer_funding"] if py else None)
            financial_like = row["quality_flag"] == "financial_like_excluded"
            score, label = score_signal(
                row["gross_customer_funding"],
                row["net_customer_funding"],
                row["revenue"],
                row["gross_qoq_pct"],
                row["gross_yoy_pct"],
                row["net_qoq_pct"],
                financial_like,
            )
            row["signal_score"] = score
            row["signal_label"] = label
            final_rows.append(row)

    return final_rows


def write_rows(conn: sqlite3.Connection, rows: list[dict], since_year: int) -> None:
    execute_with_retry(conn, "DELETE FROM contract_advance_signals WHERE fiscal_year >= ?", (since_year,))
    conn.executemany(
        """
        INSERT OR REPLACE INTO contract_advance_signals (
            stock_code, stock_name, market, sector_large, fiscal_year, fiscal_quarter, fs_div,
            contract_liabilities, advances_received, contract_assets,
            gross_customer_funding, net_customer_funding, revenue,
            gross_to_revenue_pct, net_to_revenue_pct,
            gross_qoq_pct, gross_yoy_pct, net_qoq_pct, net_yoy_pct,
            signal_score, signal_label, quality_flag, source_accounts_json, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?
        )
        """,
        [
            (
                row["stock_code"], row["stock_name"], row["market"], row["sector_large"],
                row["fiscal_year"], row["fiscal_quarter"], row["fs_div"],
                row["contract_liabilities"], row["advances_received"], row["contract_assets"],
                row["gross_customer_funding"], row["net_customer_funding"], row["revenue"],
                row["gross_to_revenue_pct"], row["net_to_revenue_pct"],
                row["gross_qoq_pct"], row["gross_yoy_pct"], row["net_qoq_pct"], row["net_yoy_pct"],
                row["signal_score"], row["signal_label"], row["quality_flag"],
                json.dumps(row["source_accounts"], ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            )
            for row in rows
        ],
    )
    conn.commit()


def write_run_report(conn: sqlite3.Connection, rows: list[dict], since_year: int) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    stocks = len({r["stock_code"] for r in rows})
    top = sorted(
        [r for r in rows if r["signal_score"] and r["quality_flag"] == "ok"],
        key=lambda r: (r["signal_score"], r["gross_to_revenue_pct"] or 0),
        reverse=True,
    )[:30]
    payload = {
        "run_id": run_id,
        "since_year": since_year,
        "rows_written": len(rows),
        "stocks": stocks,
        "top_signals": top,
    }
    (OUT_DIR / f"contract_advance_signals_{run_id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md = [
        f"# Contract Advance Signals — {run_id}",
        "",
        f"- since_year: {since_year}",
        f"- rows_written: {len(rows):,}",
        f"- stocks: {stocks:,}",
        "",
        "## Top Signals",
    ]
    for r in top[:15]:
        md.append(
            f"- {r['stock_name']}({r['stock_code']}) {r['fiscal_year']}Q{r['fiscal_quarter']} "
            f"{r['fs_div']} score={r['signal_score']} gross/rev={r['gross_to_revenue_pct']}% "
            f"QoQ={r['gross_qoq_pct']}% YoY={r['gross_yoy_pct']}% — {r['signal_label']}"
        )
    (OUT_DIR / f"contract_advance_signals_{run_id}.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    execute_with_retry(conn,
        """
        INSERT OR REPLACE INTO contract_advance_signal_runs
        (run_id, started_at, finished_at, rows_written, stocks, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            run_id,
            datetime.now().isoformat(timespec="seconds"),
            len(rows),
            stocks,
            f"since_year={since_year}",
        ),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-year", type=int, default=2020)
    args = parser.parse_args()

    conn = connect()
    try:
        rows = build_rows(conn, args.since_year)
        with stock_db_write_lock("build_contract_advance_signals", timeout=120) as locked:
            if not locked:
                print("contract_advance_signals skipped: stock.db write lock busy")
                return 2
            init_tables(conn)
            write_rows(conn, rows, args.since_year)
            write_run_report(conn, rows, args.since_year)
        print(f"contract_advance_signals built: rows={len(rows)}, stocks={len({r['stock_code'] for r in rows})}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
