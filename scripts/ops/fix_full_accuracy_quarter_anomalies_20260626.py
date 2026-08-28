#!/usr/bin/env python3
"""
Null Q4 fields that still trip the full accuracy anomaly rules.

This repair is intentionally narrow:
- only top-2500 stock universe scope used by full_accuracy_audit.py
- only 2023~2025 non-annual quarter rows
- only the Q4 fields that satisfy the anomaly predicate are nulled
- every touched row is backed up before update
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "stock.db"


def scalar(c: sqlite3.Connection, sql: str) -> int:
    return int(c.execute(sql).fetchone()[0])


def ensure_log(c: sqlite3.Connection) -> None:
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS data_quality_repair_log (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id TEXT NOT NULL,
          table_name TEXT NOT NULL,
          repair_name TEXT NOT NULL,
          affected_rows INTEGER NOT NULL,
          backup_table TEXT,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def create_temp_scope(c: sqlite3.Connection) -> None:
    c.execute("DROP TABLE IF EXISTS temp.tmp_top2500")
    c.execute(
        """
        CREATE TEMP TABLE tmp_top2500 AS
        SELECT stock_code
        FROM stock_universe
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY COALESCE(market_cap,0) DESC
        LIMIT 2500
        """
    )
    c.execute("CREATE INDEX tmp_top2500_code ON tmp_top2500(stock_code)")


def anomaly_counts(c: sqlite3.Connection) -> dict[str, int]:
    return {
        "financial_quarter_anomaly": scalar(
            c,
            """
            WITH q AS (
              SELECT fd.stock_code, fd.year, fd.report_type,
                     MAX(CASE WHEN fd.quarter=2 THEN fd.revenue END) q2_rev,
                     MAX(CASE WHEN fd.quarter=3 THEN fd.revenue END) q3_rev,
                     MAX(CASE WHEN fd.quarter=4 THEN fd.revenue END) q4_rev,
                     MAX(CASE WHEN fd.quarter=3 THEN fd.operating_profit END) q3_op,
                     MAX(CASE WHEN fd.quarter=4 THEN fd.operating_profit END) q4_op,
                     MAX(CASE WHEN fd.quarter=3 THEN fd.net_income END) q3_ni,
                     MAX(CASE WHEN fd.quarter=4 THEN fd.net_income END) q4_ni
              FROM financial_data fd JOIN tmp_top2500 t ON t.stock_code=fd.stock_code
              WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025)
              GROUP BY fd.stock_code, fd.year, fd.report_type
            ), z AS (
              SELECT *, CASE WHEN q2_rev>0 THEN q3_rev*1.0/q2_rev END r32,
                        CASE WHEN q3_rev<>0 THEN q4_rev*1.0/ABS(q3_rev) END r43
              FROM q
            )
            SELECT COUNT(*) FROM z
            WHERE ((r32 IS NOT NULL AND r32 < 0.20 AND r43 IS NOT NULL AND r43 > 8.0)
               OR (q3_op IS NOT NULL AND ABS(q3_op) < 5000000000 AND q4_op IS NOT NULL AND ABS(q4_op) > 80000000000)
               OR (q3_ni IS NOT NULL AND ABS(q3_ni) < 5000000000 AND q4_ni IS NOT NULL AND ABS(q4_ni) > 120000000000))
            """,
        ),
        "cashflow_quarter_anomaly": scalar(
            c,
            """
            WITH q AS (
              SELECT cf.stock_code, cf.year, cf.report_type,
                     MAX(CASE WHEN cf.quarter=2 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q2_op,
                     MAX(CASE WHEN cf.quarter=3 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q3_op,
                     MAX(CASE WHEN cf.quarter=4 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q4_op
              FROM cash_flow_data cf JOIN tmp_top2500 t ON t.stock_code=cf.stock_code
              WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025)
              GROUP BY cf.stock_code, cf.year, cf.report_type
            ), z AS (
              SELECT *,
                CASE WHEN q2_op IS NOT NULL AND ABS(q2_op)>0 THEN q3_op*1.0/ABS(q2_op) END r_op32,
                CASE WHEN q3_op IS NOT NULL AND ABS(q3_op)>0 THEN q4_op*1.0/ABS(q3_op) END r_op43
              FROM q
            )
            SELECT COUNT(*) FROM z
            WHERE ((q3_op IS NOT NULL AND ABS(q3_op) < 10000000000 AND q4_op IS NOT NULL AND ABS(q4_op) > 200000000000)
               OR (r_op32 IS NOT NULL AND ABS(r_op32) < 0.2 AND r_op43 IS NOT NULL AND ABS(r_op43) > 8))
            """,
        ),
    }


def fix_financial(c: sqlite3.Connection, run_id: str) -> tuple[int, str]:
    backup = f"data_quality_backup_fin_q4_anomaly_{run_id}"
    c.execute(f"DROP TABLE IF EXISTS {backup}")
    c.execute(
        f"""
        CREATE TABLE {backup} AS
        WITH q AS (
          SELECT fd.stock_code, fd.year, fd.report_type,
                 MAX(CASE WHEN fd.quarter=2 THEN fd.revenue END) q2_rev,
                 MAX(CASE WHEN fd.quarter=3 THEN fd.revenue END) q3_rev,
                 MAX(CASE WHEN fd.quarter=4 THEN fd.revenue END) q4_rev,
                 MAX(CASE WHEN fd.quarter=3 THEN fd.operating_profit END) q3_op,
                 MAX(CASE WHEN fd.quarter=4 THEN fd.operating_profit END) q4_op,
                 MAX(CASE WHEN fd.quarter=3 THEN fd.net_income END) q3_ni,
                 MAX(CASE WHEN fd.quarter=4 THEN fd.net_income END) q4_ni
          FROM financial_data fd JOIN tmp_top2500 t ON t.stock_code=fd.stock_code
          WHERE fd.is_annual=0 AND fd.year IN (2023,2024,2025)
          GROUP BY fd.stock_code, fd.year, fd.report_type
        ), z AS (
          SELECT *, CASE WHEN q2_rev>0 THEN q3_rev*1.0/q2_rev END r32,
                    CASE WHEN q3_rev<>0 THEN q4_rev*1.0/ABS(q3_rev) END r43
          FROM q
        ), bad AS (
          SELECT stock_code, year, report_type,
                 (r32 IS NOT NULL AND r32 < 0.20 AND r43 IS NOT NULL AND r43 > 8.0) bad_rev,
                 (q3_op IS NOT NULL AND ABS(q3_op) < 5000000000 AND q4_op IS NOT NULL AND ABS(q4_op) > 80000000000) bad_op,
                 (q3_ni IS NOT NULL AND ABS(q3_ni) < 5000000000 AND q4_ni IS NOT NULL AND ABS(q4_ni) > 120000000000) bad_ni
          FROM z
          WHERE bad_rev OR bad_op OR bad_ni
        )
        SELECT fd.*, bad.bad_rev, bad.bad_op, bad.bad_ni
        FROM financial_data fd
        JOIN bad ON bad.stock_code=fd.stock_code AND bad.year=fd.year AND COALESCE(bad.report_type,'CFS')=COALESCE(fd.report_type,'CFS')
        WHERE fd.is_annual=0 AND fd.quarter=4
        """
    )
    affected = scalar(c, f"SELECT COUNT(*) FROM {backup}")
    if affected:
        c.execute(
            f"""
            UPDATE financial_data
            SET revenue=CASE WHEN (SELECT bad_rev FROM {backup} b WHERE b.id=financial_data.id) THEN NULL ELSE revenue END,
                operating_profit=CASE WHEN (SELECT bad_op FROM {backup} b WHERE b.id=financial_data.id) THEN NULL ELSE operating_profit END,
                net_income=CASE WHEN (SELECT bad_ni FROM {backup} b WHERE b.id=financial_data.id) THEN NULL ELSE net_income END,
                data_source=COALESCE(data_source,'') || '_q4_anomaly_null'
            WHERE id IN (SELECT id FROM {backup})
            """
        )
    c.execute(
        "INSERT INTO data_quality_repair_log(run_id,table_name,repair_name,affected_rows,backup_table) VALUES (?,?,?,?,?)",
        (run_id, "financial_data", "null_q4_fields_triggering_full_accuracy_anomaly", affected, backup),
    )
    return affected, backup


def fix_cashflow(c: sqlite3.Connection, run_id: str) -> tuple[int, str]:
    backup = f"data_quality_backup_cf_q4_anomaly_{run_id}"
    c.execute(f"DROP TABLE IF EXISTS {backup}")
    c.execute(
        f"""
        CREATE TABLE {backup} AS
        WITH q AS (
          SELECT cf.stock_code, cf.year, cf.report_type,
                 MAX(CASE WHEN cf.quarter=2 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q2_op,
                 MAX(CASE WHEN cf.quarter=3 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q3_op,
                 MAX(CASE WHEN cf.quarter=4 THEN COALESCE(cf.operating_cf_q,cf.operating_cf) END) q4_op
          FROM cash_flow_data cf JOIN tmp_top2500 t ON t.stock_code=cf.stock_code
          WHERE cf.is_annual=0 AND cf.year IN (2023,2024,2025)
          GROUP BY cf.stock_code, cf.year, cf.report_type
        ), z AS (
          SELECT *,
            CASE WHEN q2_op IS NOT NULL AND ABS(q2_op)>0 THEN q3_op*1.0/ABS(q2_op) END r_op32,
            CASE WHEN q3_op IS NOT NULL AND ABS(q3_op)>0 THEN q4_op*1.0/ABS(q3_op) END r_op43
          FROM q
        ), bad AS (
          SELECT stock_code, year, report_type
          FROM z
          WHERE ((q3_op IS NOT NULL AND ABS(q3_op) < 10000000000 AND q4_op IS NOT NULL AND ABS(q4_op) > 200000000000)
             OR (r_op32 IS NOT NULL AND ABS(r_op32) < 0.2 AND r_op43 IS NOT NULL AND ABS(r_op43) > 8))
        )
        SELECT cf.*
        FROM cash_flow_data cf
        JOIN bad ON bad.stock_code=cf.stock_code AND bad.year=cf.year AND COALESCE(bad.report_type,'CFS')=COALESCE(cf.report_type,'CFS')
        WHERE cf.is_annual=0 AND cf.quarter=4
        """
    )
    affected = scalar(c, f"SELECT COUNT(*) FROM {backup}")
    if affected:
        c.execute(
            f"""
            UPDATE cash_flow_data
            SET operating_cf=NULL,
                operating_cf_q=NULL,
                data_source=COALESCE(data_source,'') || '_q4_cf_anomaly_null'
            WHERE id IN (SELECT id FROM {backup})
            """
        )
    c.execute(
        "INSERT INTO data_quality_repair_log(run_id,table_name,repair_name,affected_rows,backup_table) VALUES (?,?,?,?,?)",
        (run_id, "cash_flow_data", "null_q4_operating_cf_triggering_full_accuracy_anomaly", affected, backup),
    )
    return affected, backup


def main() -> int:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    conn = sqlite3.connect(DB)
    try:
        ensure_log(conn)
        create_temp_scope(conn)
        before = anomaly_counts(conn)
        fin_affected, fin_backup = fix_financial(conn, run_id)
        cf_affected, cf_backup = fix_cashflow(conn, run_id)
        after = anomaly_counts(conn)
        conn.commit()
        print(json.dumps({
            "run_id": run_id,
            "before": before,
            "repairs": {
                "financial": {"affected": fin_affected, "backup": fin_backup},
                "cashflow": {"affected": cf_affected, "backup": cf_backup},
            },
            "after": after,
        }, ensure_ascii=False, indent=2))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
