#!/usr/bin/env python3
from __future__ import annotations

import sqlite3

DB = "/Applications/stock_dashboard/employment_monitor/employment.db"


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(employment_company)").fetchall()}
    if "source" not in cols:
        conn.execute("ALTER TABLE employment_company ADD COLUMN source TEXT")

    # 보험 API 월별 데이터
    conn.execute(
        "UPDATE employment_company SET source='insurance_api' "
        "WHERE (source IS NULL OR source='') "
        "AND ym LIKE '____-__' AND (bizr_no IS NOT NULL AND bizr_no<>'')"
    )
    # 사업보고서 연말 데이터
    conn.execute(
        "UPDATE employment_company SET source='report_annual' "
        "WHERE (source IS NULL OR source='') "
        "AND ym LIKE '____-12' AND (bizr_no IS NULL OR bizr_no='')"
    )
    # 그 외 미분류
    conn.execute(
        "UPDATE employment_company SET source='unknown' "
        "WHERE (source IS NULL OR source='')"
    )
    conn.commit()

    rows = conn.execute(
        "SELECT source, COUNT(*) cnt FROM employment_company GROUP BY source ORDER BY cnt DESC"
    ).fetchall()
    for r in rows:
        print(r["source"], r["cnt"])
    conn.close()


if __name__ == "__main__":
    main()

