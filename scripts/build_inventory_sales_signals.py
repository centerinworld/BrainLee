#!/usr/bin/env python3
"""Build inventory + revenue/order leading signal table.

Goal:
- distinguish productive inventory build-up from bad inventory accumulation.

Signals:
- build_up: inventory rises with revenue/order/backlog confirmation
- digestion: inventory falls while revenue rises
- risk: inventory rises while revenue/order confirmation is weak
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
DB_PATH = ROOT / "stock.db"
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
        except sqlite3.OperationalError as e:
            last = e
            if "locked" not in str(e).lower():
                raise
            time.sleep(min(2 ** attempt, 30))
    raise last


def init_tables(conn: sqlite3.Connection) -> None:
    execute_with_retry(conn,
        """
        CREATE TABLE IF NOT EXISTS inventory_sales_signals (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            sector_large TEXT,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL,
            fs_div TEXT NOT NULL,
            inventory_krw REAL,
            revenue REAL,
            order_backlog_krw REAL,
            order_contracts_krw REAL,
            inventory_to_revenue_pct REAL,
            inventory_qoq_pct REAL,
            inventory_yoy_pct REAL,
            revenue_qoq_pct REAL,
            revenue_yoy_pct REAL,
            backlog_qoq_pct REAL,
            contract_qoq_pct REAL,
            signal_type TEXT,
            signal_score INTEGER DEFAULT 0,
            risk_score INTEGER DEFAULT 0,
            signal_label TEXT,
            quality_flag TEXT DEFAULT 'ok',
            source_json TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, fs_div)
        )
        """
    )
    execute_with_retry(conn,
        """
        CREATE INDEX IF NOT EXISTS idx_inventory_sales_signals_latest
        ON inventory_sales_signals(stock_code, fiscal_year DESC, fiscal_quarter DESC)
        """
    )
    execute_with_retry(conn,
        """
        CREATE TABLE IF NOT EXISTS inventory_sales_signal_runs (
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


def pct(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or abs(prev) < 1e8:  # 1억원 미만의 미세 분모는 기저효과 노이즈 방지를 위해 제외
        return None
    val = (cur - prev) / abs(prev) * 100.0
    if val > 500.0:
        return 500.0
    if val < -100.0:
        return -100.0
    return round(val, 2)


def yq_to_date_range(year: int, quarter: int) -> tuple[str, str]:
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    start = f"{year:04d}-{start_month:02d}-01"
    if end_month in (1, 3, 5, 7, 8, 10, 12):
        end_day = 31
    elif end_month == 2:
        end_day = 29 if year % 4 == 0 else 28
    else:
        end_day = 30
    return start, f"{year:04d}-{end_month:02d}-{end_day:02d}"


def load_inventory(conn: sqlite3.Connection, since_year: int) -> dict:
    rows = conn.execute(
        """
        SELECT c.stock_code, u.stock_name, u.market, u.sector_large,
               c.fiscal_year, c.fiscal_quarter, c.report_type AS fs_div,
               c.inventory_assets_krw, c.confidence, c.source_rcept_no
        FROM dart_cost_quarterly c
        LEFT JOIN stock_universe u ON u.stock_code=c.stock_code
        WHERE c.fiscal_year >= ?
          AND c.inventory_assets_krw IS NOT NULL
          AND c.inventory_assets_krw > 0
          AND length(c.stock_code)=6
          AND c.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """,
        (since_year,),
    ).fetchall()
    # 2026-09 발견: 동일 종목의 재고자산 원시값이 서로 다른 회계분기에
    # 원단위까지 완전히 일치해 반복되는 사례가 다수 존재(DART 재고자산
    # API 수집기의 report_code 매핑 버그 + 텍스트파서 폴백값 고착이 원인,
    # scripts/collect_inventory_from_dart.py 참고). 정상 운영 중인 회사의
    # 재고자산이 서로 다른 분기에 원단위까지 완전 동일할 가능성은 사실상
    # 없으므로, 같은 종목 시계열 내에서 값이 반복되면 최초 등장분만 신뢰하고
    # 이후 재등장은 신호 산출에서 제외한다(근본 재수집 전까지의 방어 조치).
    by_stock: dict[str, list] = defaultdict(list)
    for r in rows:
        by_stock[r["stock_code"]].append(r)

    out = {}
    for stock_code, stock_rows in by_stock.items():
        stock_rows.sort(key=lambda r: (r["fiscal_year"], r["fiscal_quarter"]))
        seen_values: set[float] = set()
        for r in stock_rows:
            val = float(r["inventory_assets_krw"])
            if val in seen_values:
                continue
            seen_values.add(val)
            key = (r["stock_code"], r["fiscal_year"], r["fiscal_quarter"], r["fs_div"])
            out[key] = dict(r)
    return out


def load_revenue(conn: sqlite3.Connection) -> dict:
    # 2026-09 수정: 기존 코드는 (a) CASE 분기가 항상 'CFS'만 반환하는 무의미한
    # 문구였고 (b) report_type을 GROUP BY/WHERE에서 전혀 걸러내지 않아 같은
    # (종목,연도,분기)의 CFS/OFS 매출 중 더 큰 쪽이 임의로 선택됐다(이중계산은
    # 아니나 잘못된 회계기준 값이 분모로 섞여 들어감). (c) quarter=4 슬롯에
    # 진짜 4분기 단독매출(is_annual=0)과 연간누계매출(is_annual=1)이 함께
    # 있어 더 큰 연간누계값이 분모로 잘못 채택되던 문제도 함께 수정.
    rows = conn.execute(
        """
        SELECT stock_code, year, quarter, report_type AS fs_div, MAX(ABS(revenue)) AS revenue
        FROM financial_data
        WHERE report_type IN ('CFS', 'OFS')
          AND revenue IS NOT NULL AND ABS(revenue) > 0
          AND quarter BETWEEN 1 AND 4
          AND is_annual = 0
        GROUP BY stock_code, year, quarter, report_type
        """
    ).fetchall()
    return {(r["stock_code"], r["year"], r["quarter"], r["fs_div"]): float(r["revenue"]) for r in rows}


def load_backlog(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT stock_code, year, quarter,
               MAX(COALESCE(backlog_amount, backlog_normalized, 0)) AS backlog
        FROM order_backlog
        WHERE stock_code IS NOT NULL AND quarter BETWEEN 1 AND 4
        GROUP BY stock_code, year, quarter
        """
    ).fetchall()
    return {(r["stock_code"], r["year"], r["quarter"]): float(r["backlog"] or 0) for r in rows}


def load_order_contract_quarterly(conn: sqlite3.Connection, since_year: int) -> dict:
    rows = conn.execute(
        """
        SELECT stock_code,
               CAST(substr(rcept_dt,1,4) AS INTEGER) AS year,
               ((CAST(substr(rcept_dt,6,2) AS INTEGER)-1)/3)+1 AS quarter,
               SUM(CASE WHEN is_termination=0 THEN COALESCE(contract_amount,0) ELSE -COALESCE(contract_amount,0) END) AS amount
        FROM order_contracts
        WHERE rcept_dt IS NOT NULL
          AND rcept_dt >= ?
          AND contract_amount IS NOT NULL
          AND length(stock_code)=6
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        GROUP BY stock_code, year, quarter
        """,
        (f"{since_year}-01-01",),
    ).fetchall()
    return {(r["stock_code"], int(r["year"]), int(r["quarter"])): float(r["amount"] or 0) for r in rows}


def classify(row: dict) -> tuple[str, int, int, str]:
    inv_qoq = row.get("inventory_qoq_pct")
    inv_yoy = row.get("inventory_yoy_pct")
    rev_qoq = row.get("revenue_qoq_pct")
    rev_yoy = row.get("revenue_yoy_pct")
    backlog_qoq = row.get("backlog_qoq_pct")
    contract_qoq = row.get("contract_qoq_pct")
    inv_to_rev = row.get("inventory_to_revenue_pct")

    demand_confirm = False
    demand_parts: list[str] = []
    if rev_qoq is not None and rev_qoq >= 10:
        demand_confirm = True
        demand_parts.append(f"매출QoQ+{rev_qoq:.0f}%")
    if rev_yoy is not None and rev_yoy >= 20:
        demand_confirm = True
        demand_parts.append(f"매출YoY+{rev_yoy:.0f}%")
    if backlog_qoq is not None and backlog_qoq >= 20:
        demand_confirm = True
        demand_parts.append(f"수주잔고QoQ+{backlog_qoq:.0f}%")
    if contract_qoq is not None and contract_qoq >= 50:
        demand_confirm = True
        demand_parts.append(f"수주공시QoQ+{contract_qoq:.0f}%")

    if inv_qoq is not None and inv_qoq >= 20 and demand_confirm:
        score = 3
        if inv_to_rev is not None and inv_to_rev >= 20:
            score += 1
        if inv_yoy is not None and 10 <= inv_yoy <= 100:
            score += 1
        if backlog_qoq is not None and backlog_qoq >= 20:
            score += 1
        if contract_qoq is not None and contract_qoq >= 50:
            score += 1
        label = f"증산준비: 재고QoQ+{inv_qoq:.0f}% + " + " · ".join(demand_parts)
        return "build_up", min(score, 8), 0, label

    if inv_qoq is not None and inv_qoq <= -10 and (rev_qoq is not None and rev_qoq >= 10):
        score = 4
        if rev_yoy is not None and rev_yoy >= 20:
            score += 1
        return "digestion", min(score, 7), 0, f"재고소화: 재고QoQ{inv_qoq:.0f}% + 매출QoQ+{rev_qoq:.0f}%"

    if inv_qoq is not None and inv_qoq >= 30 and not demand_confirm:
        risk = 3
        if rev_qoq is not None and rev_qoq < 0:
            risk += 2
        if rev_yoy is not None and rev_yoy < 0:
            risk += 1
        if inv_to_rev is not None and inv_to_rev >= 50:
            risk += 1
        label = f"악성재고위험: 재고QoQ+{inv_qoq:.0f}%"
        if rev_qoq is not None:
            label += f" / 매출QoQ{rev_qoq:+.0f}%"
        return "risk", 0, min(risk, 8), label

    return "neutral", 0, 0, "특이 신호 없음"


def build_rows(conn: sqlite3.Connection, since_year: int) -> list[dict]:
    inv = load_inventory(conn, since_year)
    rev = load_revenue(conn)
    backlog = load_backlog(conn)
    contracts = load_order_contract_quarterly(conn, since_year)

    base: list[dict] = []
    today = date.today()
    for key, row in inv.items():
        sc, y, q, fs = key
        _, quarter_end = yq_to_date_range(y, q)
        if date.fromisoformat(quarter_end) > today:
            continue
        revenue = rev.get((sc, y, q, fs))
        b = backlog.get((sc, y, q))
        oc = contracts.get((sc, y, q))
        inv_val = float(row["inventory_assets_krw"] or 0)
        base.append({
            "stock_code": sc,
            "stock_name": row.get("stock_name") or sc,
            "market": row.get("market"),
            "sector_large": row.get("sector_large"),
            "fiscal_year": y,
            "fiscal_quarter": q,
            "fs_div": fs,
            "inventory_krw": inv_val,
            "revenue": revenue,
            "order_backlog_krw": b,
            "order_contracts_krw": oc,
            "inventory_to_revenue_pct": round(inv_val / revenue * 100, 2) if revenue else None,
            "source": {
                "inventory_confidence": row.get("confidence"),
                "inventory_rcept_no": row.get("source_rcept_no"),
            },
        })

    series: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in base:
        series[(row["stock_code"], row["fs_div"])].append(row)

    out: list[dict] = []
    for _, rows in series.items():
        rows.sort(key=lambda r: (r["fiscal_year"], r["fiscal_quarter"]))
        lookup = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in rows}
        for row in rows:
            y, q = row["fiscal_year"], row["fiscal_quarter"]
            prev_q = (y, q - 1) if q > 1 else (y - 1, 4)
            prev_y = (y - 1, q)
            pq = lookup.get(prev_q)
            py = lookup.get(prev_y)
            row["inventory_qoq_pct"] = pct(row["inventory_krw"], pq["inventory_krw"] if pq else None)
            row["inventory_yoy_pct"] = pct(row["inventory_krw"], py["inventory_krw"] if py else None)
            row["revenue_qoq_pct"] = pct(row["revenue"], pq["revenue"] if pq else None)
            row["revenue_yoy_pct"] = pct(row["revenue"], py["revenue"] if py else None)
            row["backlog_qoq_pct"] = pct(row["order_backlog_krw"], pq["order_backlog_krw"] if pq else None)
            row["contract_qoq_pct"] = pct(row["order_contracts_krw"], pq["order_contracts_krw"] if pq else None)
            stype, score, risk, label = classify(row)
            row["signal_type"] = stype
            row["signal_score"] = score
            row["risk_score"] = risk
            row["signal_label"] = label
            row["quality_flag"] = "ok"
            out.append(row)
    return out


def write_rows(conn: sqlite3.Connection, rows: list[dict], since_year: int) -> None:
    execute_with_retry(conn, "DELETE FROM inventory_sales_signals WHERE fiscal_year >= ?", (since_year,))
    # 2026-09 수정: 기존 named(:key) dict-파라미터 방식은 db_compat.py의
    # PostgresCompatCursor.execute()가 dict params를 tuple(dict)(=키 이름들의
    # 튜플)로 잘못 변환하는 버그와 만나 "the query has 0 placeholders but N
    # parameters were passed"로 항상 실패하고 있었다(named :key 플레이스홀더 자체를
    # 지원하는 변환로직이 db_compat.py에 전혀 없음 — 이 테이블이 오랫동안 실제로
    # 재구축되지 못했던 근본원인). scripts/build_contract_advance_signals.py가 이미
    # 쓰고 있는, 검증된 positional(?) + tuple 방식으로 통일해 우회.
    conn.executemany(
        """
        INSERT OR REPLACE INTO inventory_sales_signals (
            stock_code, stock_name, market, sector_large, fiscal_year, fiscal_quarter, fs_div,
            inventory_krw, revenue, order_backlog_krw, order_contracts_krw,
            inventory_to_revenue_pct, inventory_qoq_pct, inventory_yoy_pct,
            revenue_qoq_pct, revenue_yoy_pct, backlog_qoq_pct, contract_qoq_pct,
            signal_type, signal_score, risk_score, signal_label, quality_flag, source_json, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?
        )
        """,
        [
            (
                row["stock_code"], row["stock_name"], row["market"], row["sector_large"],
                row["fiscal_year"], row["fiscal_quarter"], row["fs_div"],
                row["inventory_krw"], row["revenue"], row["order_backlog_krw"], row["order_contracts_krw"],
                row["inventory_to_revenue_pct"], row["inventory_qoq_pct"], row["inventory_yoy_pct"],
                row["revenue_qoq_pct"], row["revenue_yoy_pct"], row["backlog_qoq_pct"], row["contract_qoq_pct"],
                row["signal_type"], row["signal_score"], row["risk_score"], row["signal_label"],
                row["quality_flag"],
                json.dumps(row["source"], ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            )
            for row in rows
        ],
    )
    conn.commit()


def write_report(conn: sqlite3.Connection, rows: list[dict], since_year: int) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    stocks = len({r["stock_code"] for r in rows})
    top_good = sorted(
        [r for r in rows if r["signal_score"] > 0],
        key=lambda r: (r["fiscal_year"], r["fiscal_quarter"], r["signal_score"]),
        reverse=True,
    )[:30]
    top_risk = sorted(
        [r for r in rows if r["risk_score"] > 0],
        key=lambda r: (r["fiscal_year"], r["fiscal_quarter"], r["risk_score"]),
        reverse=True,
    )[:30]
    payload = {"run_id": run_id, "since_year": since_year, "rows": len(rows), "stocks": stocks, "top_good": top_good, "top_risk": top_risk}
    (OUT_DIR / f"inventory_sales_signals_{run_id}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Inventory Sales Signals — {run_id}", "", f"- rows: {len(rows):,}", f"- stocks: {stocks:,}", "", "## Good Signals"]
    for r in top_good[:15]:
        lines.append(f"- {r['stock_name']}({r['stock_code']}) {r['fiscal_year']}Q{r['fiscal_quarter']} {r['signal_type']} score={r['signal_score']} — {r['signal_label']}")
    lines.append("")
    lines.append("## Risk Signals")
    for r in top_risk[:15]:
        lines.append(f"- {r['stock_name']}({r['stock_code']}) {r['fiscal_year']}Q{r['fiscal_quarter']} risk={r['risk_score']} — {r['signal_label']}")
    (OUT_DIR / f"inventory_sales_signals_{run_id}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    execute_with_retry(conn,
        """
        INSERT OR REPLACE INTO inventory_sales_signal_runs
        (run_id, started_at, finished_at, rows_written, stocks, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (run_id, run_id, datetime.now().isoformat(timespec="seconds"), len(rows), stocks, f"since_year={since_year}"),
    )
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since-year", type=int, default=2020)
    args = parser.parse_args()
    conn = connect()
    try:
        rows = build_rows(conn, args.since_year)
        with stock_db_write_lock("build_inventory_sales_signals", timeout=120) as locked:
            if not locked:
                print("inventory_sales_signals skipped: stock.db write lock busy")
                return 2
            init_tables(conn)
            write_rows(conn, rows, args.since_year)
            write_report(conn, rows, args.since_year)
        print(f"inventory_sales_signals built: rows={len(rows)}, stocks={len({r['stock_code'] for r in rows})}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
