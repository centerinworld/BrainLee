#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.fnguide_financial_collector import fetch_fnguide_all, upsert_cashflow, _conn

DB = "/Applications/stock_dashboard/stock.db"
TARGETS = ["096770", "178320", "268280"]  # SK이노베이션, 서진시스템, 미원에스씨
YEARS = [2022, 2023, 2024, 2025]


def ensure_log(conn: sqlite3.Connection):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS depreciation_q_fix_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          fixed_at TEXT NOT NULL,
          stock_code TEXT NOT NULL,
          year INTEGER NOT NULL,
          quarter INTEGER NOT NULL,
          report_type TEXT NOT NULL,
          row_id INTEGER,
          old_depreciation_q REAL,
          new_depreciation_q REAL,
          cause_code TEXT,
          note TEXT
        )
        """
    )
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cash_flow_data)").fetchall()]
    if "depreciation_q" not in cols:
        conn.execute("ALTER TABLE cash_flow_data ADD COLUMN depreciation_q REAL")


def recompute_depr_q(conn: sqlite3.Connection, stock_code: str, year: int, report_type: str = "CFS") -> int:
    rows = conn.execute(
        """
        SELECT id, quarter, is_annual, depreciation, depreciation_q, data_source
        FROM cash_flow_data
        WHERE stock_code=? AND year=? AND COALESCE(report_type,'CFS')=?
        ORDER BY is_annual, quarter, id
        """,
        (stock_code, year, report_type),
    ).fetchall()
    q = {int(r["quarter"]): r for r in rows if int(r["is_annual"] or 0) == 0}
    a = next((r for r in rows if int(r["is_annual"] or 0) == 1), None)
    if not a:
        return 0
    if not all(k in q for k in (1, 2, 3)):
        return 0
    if any(q[k]["depreciation"] is None for k in (1, 2, 3)):
        return 0
    if a["depreciation"] is None:
        return 0

    q1 = float(q[1]["depreciation"])
    q2 = float(q[2]["depreciation"])
    q3 = float(q[3]["depreciation"])
    ann = float(a["depreciation"])
    d = {1: q1, 2: q2 - q1, 3: q3 - q2, 4: ann - q3}
    touched = 0
    for k in (1, 2, 3, 4):
        if k not in q:
            continue
        row = q[k]
        old = row["depreciation_q"]
        new = d[k]
        if old is not None and abs(float(old) - new) < 1:
            continue
        conn.execute(
            "UPDATE cash_flow_data SET depreciation_q=?, value_type='cumulative→derived', data_source='fnguide_fixed_deprq' WHERE id=?",
            (new, row["id"]),
        )
        conn.execute(
            """
            INSERT INTO depreciation_q_fix_log
            (fixed_at,stock_code,year,quarter,report_type,row_id,old_depreciation_q,new_depreciation_q,cause_code,note)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                stock_code,
                year,
                k,
                report_type,
                row["id"],
                old,
                new,
                "MIXED_UNIT_REBUILT",
                f"ann={ann},q1={q1},q2={q2},q3={q3}",
            ),
        )
        touched += 1
    return touched


def main():
    conn = _conn()
    conn.row_factory = sqlite3.Row
    ensure_log(conn)

    stats = {
        "targets": TARGETS,
        "years": YEARS,
        "fnguide_upsert_changed": 0,
        "deprq_recomputed_rows": 0,
    }

    # 1) FnGuide 원천으로 강제 덮어쓰기(교차검증 없이)
    for code in TARGETS:
        all_cfs = fetch_fnguide_all(code, "CFS")
        if not all_cfs:
            continue
        annual = all_cfs.get("annual", {}) or {}
        qtr = all_cfs.get("quarterly", {}) or {}
        for y in YEARS:
            y_ann = annual.get(y, {})
            if y_ann:
                res = upsert_cashflow(conn, code, y, 0, 1, "CFS", y_ann, override=True)
                if res in ("inserted", "overridden", "fill_only"):
                    stats["fnguide_upsert_changed"] += 1
            for q in (1, 2, 3):
                yq = qtr.get(y, {}).get(q, {})
                if not yq:
                    continue
                res = upsert_cashflow(conn, code, y, q, 0, "CFS", yq, override=True)
                if res in ("inserted", "overridden", "fill_only"):
                    stats["fnguide_upsert_changed"] += 1

    # 2) dep_q 재계산
    for code in TARGETS:
        for y in YEARS:
            stats["deprq_recomputed_rows"] += recompute_depr_q(conn, code, y, "CFS")

    conn.commit()
    conn.close()
    print(stats)


if __name__ == "__main__":
    main()
