#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path("/Applications/stock_dashboard/stock.db")
OUT_DIR = Path("/Applications/stock_dashboard/scratch")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = OUT_DIR / f"financial_identity_audit_{ts}.json"

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        summary = []
        for is_annual in (0, 1):
            row = conn.execute(
                """
                SELECT
                  COUNT(*) AS n,
                  SUM(CASE WHEN total_assets IS NULL THEN 1 ELSE 0 END) AS assets_null,
                  SUM(CASE WHEN total_liabilities IS NULL THEN 1 ELSE 0 END) AS liab_null,
                  SUM(CASE WHEN total_equity IS NULL THEN 1 ELSE 0 END) AS eq_null,
                  SUM(CASE WHEN total_assets IS NOT NULL AND total_liabilities IS NOT NULL
                            AND ABS(total_assets-total_liabilities) < 1 THEN 1 ELSE 0 END) AS assets_eq_liab,
                  SUM(CASE WHEN total_assets IS NOT NULL AND total_equity IS NOT NULL
                            AND ABS(total_assets-total_equity) < 1 THEN 1 ELSE 0 END) AS assets_eq_eq,
                  SUM(CASE WHEN total_assets IS NOT NULL AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL
                            AND ABS((total_liabilities + total_equity) - total_assets) > 1000 THEN 1 ELSE 0 END) AS identity_mismatch
                FROM financial_data
                WHERE report_type='CFS' AND is_annual=?
                """,
                (is_annual,),
            ).fetchone()
            summary.append({"is_annual": is_annual, **dict(row)})

        top_bad = [
            dict(r)
            for r in conn.execute(
                """
                SELECT stock_code, year, quarter, is_annual, report_type,
                       total_assets, total_liabilities, total_equity, data_source
                FROM financial_data
                WHERE report_type='CFS'
                  AND (
                    total_assets IS NULL
                    OR total_liabilities IS NULL
                    OR total_equity IS NULL
                    OR (total_assets IS NOT NULL AND total_liabilities IS NOT NULL AND ABS(total_assets-total_liabilities) < 1)
                    OR (total_assets IS NOT NULL AND total_equity IS NOT NULL AND ABS(total_assets-total_equity) < 1)
                    OR (total_assets IS NOT NULL AND total_liabilities IS NOT NULL AND total_equity IS NOT NULL
                        AND ABS((total_liabilities + total_equity) - total_assets) > 1000)
                  )
                ORDER BY is_annual DESC, year DESC, quarter DESC
                LIMIT 300
                """
            ).fetchall()
        ]

        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db_path": str(DB_PATH),
            "summary": summary,
            "sample_bad_rows": top_bad,
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(str(out_path))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

