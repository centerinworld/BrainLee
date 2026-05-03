from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "data" / "hs_trade_lab.db"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill trade_series_cache from customs_monthly_record(itemtrade)."
    )
    parser.add_argument(
        "--all-hs",
        action="store_true",
        help="Use all HS codes from itemtrade, not only mapped hs_code_company_map.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Delete existing flow_type='total' rows before backfill.",
    )
    args = parser.parse_args()

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    if args.truncate:
        conn.execute("DELETE FROM trade_series_cache WHERE flow_type='total'")
        conn.commit()

    if args.all_hs:
        where_hs = ""
        params: tuple = ()
    else:
        where_hs = """
          AND hs_code IN (
              SELECT DISTINCT hs_code
              FROM hs_code_company_map
              WHERE hs_code IS NOT NULL
                AND TRIM(hs_code) <> ''
                AND mapping_status IN ('confirmed','exact','composite')
          )
        """
        params = ()

    sql = f"""
    INSERT INTO trade_series_cache (
        hs_code, period_ym, flow_type, export_value, import_value, trade_balance, source_name, raw_json
    )
    SELECT
        hs_code,
        period_ym,
        'total' AS flow_type,
        SUM(export_value) AS export_value,
        SUM(import_value) AS import_value,
        SUM(export_value) - SUM(import_value) AS trade_balance,
        'customs_monthly_record:itemtrade' AS source_name,
        '{{"source":"customs_monthly_record","endpoint":"itemtrade"}}' AS raw_json
    FROM customs_monthly_record
    WHERE endpoint='itemtrade'
      AND hs_code GLOB '[0-9]*'
      AND LENGTH(hs_code)=10
      AND period_ym LIKE '____-__'
      {where_hs}
    GROUP BY hs_code, period_ym
    ON CONFLICT(hs_code, period_ym, flow_type) DO UPDATE SET
      export_value=excluded.export_value,
      import_value=excluded.import_value,
      trade_balance=excluded.trade_balance,
      source_name=excluded.source_name,
      raw_json=excluded.raw_json,
      updated_at=CURRENT_TIMESTAMP
    """

    cur = conn.execute(sql, params)
    conn.commit()

    total_rows = conn.execute(
        "SELECT COUNT(*) FROM trade_series_cache WHERE flow_type='total'"
    ).fetchone()[0]
    mapped_rows = conn.execute(
        """
        SELECT COUNT(*)
        FROM trade_series_cache t
        WHERE t.flow_type='total'
          AND t.hs_code IN (
              SELECT DISTINCT hs_code
              FROM hs_code_company_map
              WHERE mapping_status IN ('confirmed','exact','composite')
          )
        """
    ).fetchone()[0]
    max_period = conn.execute(
        "SELECT MAX(period_ym) FROM trade_series_cache WHERE flow_type='total'"
    ).fetchone()[0]

    print(
        json.dumps(
            {
                "changed_rows": cur.rowcount,
                "total_flow_total_rows": total_rows,
                "mapped_flow_total_rows": mapped_rows,
                "latest_period_ym": max_period,
                "mode": "all_hs" if args.all_hs else "mapped_only",
            },
            ensure_ascii=False,
        )
    )
    conn.close()


if __name__ == "__main__":
    main()
