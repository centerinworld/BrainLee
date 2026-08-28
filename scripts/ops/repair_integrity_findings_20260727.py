#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "stock.db"
OUT = ROOT / "research_outputs" / "integrity_repair_20260727"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=120000")
    return conn


def scalar(conn: sqlite3.Connection, sql: str) -> int:
    return int(conn.execute(sql).fetchone()[0] or 0)


def ensure_log(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS data_quality_repair_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            table_name TEXT NOT NULL,
            repair_name TEXT NOT NULL,
            affected_rows INTEGER NOT NULL,
            backup_table TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def log_repair(conn: sqlite3.Connection, run_id: str, table: str, repair: str, affected: int, backup: str) -> None:
    conn.execute(
        """
        INSERT INTO data_quality_repair_log
          (run_id, table_name, repair_name, affected_rows, backup_table)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, table, repair, affected, backup),
    )


def backup(conn: sqlite3.Connection, name: str, sql: str) -> int:
    conn.execute(f"DROP TABLE IF EXISTS {name}")
    conn.execute(f"CREATE TABLE {name} AS {sql}")
    return scalar(conn, f"SELECT COUNT(*) FROM {name}")


BACKUP_PREFIXES = (
    "data_quality_backup_i4_quarter_revenue_",
    "data_quality_backup_i5_i9_cashflow_extreme_",
    "data_quality_backup_i10_equity_spike_",
)
BACKUP_RETENTION_DAYS = 14  # 이 잡은 매일 06:20 자동 실행 — 삭제 로직이 없어 무제한 누적되던 것을 발견(2026-08-24), 재발방지로 정리 추가


def prune_old_backups(conn: sqlite3.Connection, retention_days: int = BACKUP_RETENTION_DAYS) -> int:
    cutoff = (datetime.now() - timedelta(days=retention_days)).strftime("%Y%m%d")
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'data_quality_backup_i%'"
    ).fetchall()
    dropped = 0
    for (name,) in rows:
        for prefix in BACKUP_PREFIXES:
            if name.startswith(prefix):
                run_id = name[len(prefix):]
                date_part = run_id.split("_")[0]
                if len(date_part) == 8 and date_part.isdigit() and date_part < cutoff:
                    conn.execute(f"DROP TABLE IF EXISTS {name}")
                    dropped += 1
                break
    return dropped


def count_issues(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "quarter_revenue_near_annual": scalar(
            conn,
            """
            SELECT COUNT(*) FROM (
              WITH a AS (
                SELECT stock_code, year, revenue
                FROM financial_data
                WHERE is_annual=1 AND report_type='CFS' AND revenue > 0
              )
              SELECT f_q.id
              FROM financial_data f_q
              JOIN a ON a.stock_code=f_q.stock_code AND a.year=f_q.year
              JOIN stock_universe su ON su.stock_code=f_q.stock_code
              WHERE f_q.is_annual=0 AND f_q.report_type='CFS'
                AND f_q.year >= 2022 AND f_q.quarter IN (1,2,3)
                AND f_q.revenue IS NOT NULL
                AND ABS(f_q.revenue - a.revenue) / a.revenue < 0.03
                AND f_q.stock_code NOT LIKE '9%'
                AND COALESCE(su.stock_name,'') NOT LIKE '%리츠%'
                AND COALESCE(f_q.data_source,'') NOT LIKE '%ofs%'
                AND NOT (
                  SELECT COUNT(*) FROM financial_data f_other
                  WHERE f_other.stock_code=f_q.stock_code AND f_other.year=f_q.year
                    AND f_other.is_annual=0 AND f_other.quarter != f_q.quarter
                    AND f_other.quarter IN (1,2,3)
                    AND f_other.revenue > a.revenue * 0.3
                ) >= 2
            )
            """,
        ),
        "annual_capex_vs_revenue": scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM cash_flow_data cf
            JOIN financial_data f ON f.stock_code=cf.stock_code AND f.year=cf.year
              AND f.is_annual=1 AND f.report_type='CFS' AND f.revenue > 1e8
            JOIN stock_universe su ON su.stock_code=cf.stock_code
            WHERE cf.is_annual=1 AND cf.report_type='CFS'
              AND cf.capex IS NOT NULL
              AND cf.year >= 2022
              AND COALESCE(su.sector_large,'') NOT IN ('금융','보험','은행','금융서비스','의료','제약')
              AND cf.capex < 1e14
              AND f.revenue > 1e9
              AND cf.stock_code NOT LIKE '9%'
              AND (
                (cf.capex > f.revenue * 3  AND f.revenue >= 5e10)
                OR (cf.capex > f.revenue * 10 AND f.revenue >= 1e10 AND f.revenue < 5e10)
                OR (cf.capex > 5e11 AND cf.capex > f.revenue * 2)
              )
            """,
        ),
        "cf_extreme": scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM cash_flow_data cf
            JOIN stock_universe su ON su.stock_code=cf.stock_code
            LEFT JOIN (
              SELECT stock_code, MAX(revenue) max_rev
              FROM financial_data
              WHERE is_annual=1 AND report_type='CFS' AND revenue > 0
              GROUP BY stock_code
            ) rev ON rev.stock_code=cf.stock_code
            WHERE cf.year >= 2020
              AND cf.stock_code NOT LIKE '9%'
              AND cf.stock_code NOT IN ('005930','000660','005380','005490','000270')
              AND COALESCE(su.sector_large,'') NOT IN ('금융','보험','은행','금융서비스','의료','제약','헬스케어')
              AND (
                (cf.capex IS NOT NULL AND (
                    (cf.capex > 1e12 AND rev.max_rev IS NOT NULL AND cf.capex > rev.max_rev * 5)
                    OR (cf.capex > 1e13 AND rev.max_rev IS NULL)
                ))
                OR (cf.operating_cf IS NOT NULL AND (
                    (ABS(cf.operating_cf) > 1e12 AND rev.max_rev IS NOT NULL AND ABS(cf.operating_cf) > rev.max_rev * 5)
                    OR (ABS(cf.operating_cf) > 1e14 AND rev.max_rev IS NULL)
                ))
              )
            """,
        ),
        "equity_negative_spike": scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM financial_data f
            JOIN financial_data prev ON prev.stock_code=f.stock_code
              AND (
                (prev.year=f.year AND prev.quarter=f.quarter-1)
                OR (f.quarter=1 AND prev.year=f.year-1 AND prev.quarter=4)
              )
              AND prev.is_annual=0 AND prev.report_type='CFS'
              AND prev.total_equity > 5e9
            JOIN stock_universe su ON su.stock_code=f.stock_code
            WHERE f.is_annual=0 AND f.report_type='CFS'
              AND f.year >= 2022
              AND f.total_equity IS NOT NULL AND f.total_equity < -1e9
              AND f.total_assets IS NOT NULL AND f.total_assets > 1e10
              AND f.net_income IS NOT NULL
              AND ABS(f.total_equity - prev.total_equity) > ABS(f.net_income) * 3
              AND COALESCE(su.sector_large,'') NOT IN ('금융','보험','은행','금융서비스')
              AND f.stock_code NOT LIKE '9%'
              AND NOT (f.quarter=4 AND EXISTS (
                SELECT 1 FROM financial_data fa
                WHERE fa.stock_code=f.stock_code AND fa.year=f.year AND fa.is_annual=1
                  AND fa.total_equity IS NOT NULL
                  AND ABS(fa.total_equity - f.total_equity) / NULLIF(ABS(fa.total_equity),0) < 0.05
              ))
              AND COALESCE(f.data_source,'') NOT LIKE 'dart%'
              AND COALESCE(f.data_source,'') NOT LIKE '%dart_recollect%'
            """,
        ),
    }


def repair_quarter_revenue(conn: sqlite3.Connection, run_id: str) -> dict:
    table = f"data_quality_backup_i4_quarter_revenue_{run_id}"
    affected = backup(
        conn,
        table,
        """
        WITH a AS (
          SELECT stock_code, year, revenue
          FROM financial_data
          WHERE is_annual=1 AND report_type='CFS' AND revenue > 0
        )
        SELECT f_q.*
        FROM financial_data f_q
        JOIN a ON a.stock_code=f_q.stock_code AND a.year=f_q.year
        JOIN stock_universe su ON su.stock_code=f_q.stock_code
        WHERE f_q.is_annual=0 AND f_q.report_type='CFS'
          AND f_q.year >= 2022 AND f_q.quarter IN (1,2,3)
          AND f_q.revenue IS NOT NULL
          AND ABS(f_q.revenue - a.revenue) / a.revenue < 0.03
          AND f_q.stock_code NOT LIKE '9%'
          AND COALESCE(su.stock_name,'') NOT LIKE '%리츠%'
          AND COALESCE(f_q.data_source,'') NOT LIKE '%ofs%'
          AND NOT (
            SELECT COUNT(*) FROM financial_data f_other
            WHERE f_other.stock_code=f_q.stock_code AND f_other.year=f_q.year
              AND f_other.is_annual=0 AND f_other.quarter != f_q.quarter
              AND f_other.quarter IN (1,2,3)
              AND f_other.revenue > a.revenue * 0.3
          ) >= 2
        """,
    )
    if affected:
        conn.execute(
            f"""
            UPDATE financial_data
            SET revenue=NULL,
                data_source=COALESCE(data_source,'') || '_integrity_i4_revenue_null'
            WHERE id IN (SELECT id FROM {table})
            """
        )
    log_repair(conn, run_id, "financial_data", "null_quarter_revenue_near_annual", affected, table)
    return {"affected": affected, "backup": table}


def repair_cashflow_extremes(conn: sqlite3.Connection, run_id: str) -> dict:
    table = f"data_quality_backup_i5_i9_cashflow_extreme_{run_id}"
    affected = backup(
        conn,
        table,
        """
        WITH rev AS (
          SELECT stock_code, MAX(revenue) max_rev
          FROM financial_data
          WHERE is_annual=1 AND report_type='CFS' AND revenue > 0
          GROUP BY stock_code
        ), annual_bad AS (
          SELECT cf.id
          FROM cash_flow_data cf
          JOIN financial_data f ON f.stock_code=cf.stock_code AND f.year=cf.year
            AND f.is_annual=1 AND f.report_type='CFS' AND f.revenue > 1e8
          JOIN stock_universe su ON su.stock_code=cf.stock_code
          WHERE cf.is_annual=1 AND cf.report_type='CFS'
            AND cf.capex IS NOT NULL
            AND cf.year >= 2022
            AND COALESCE(su.sector_large,'') NOT IN ('금융','보험','은행','금융서비스','의료','제약')
            AND cf.capex < 1e14
            AND f.revenue > 1e9
            AND cf.stock_code NOT LIKE '9%'
            AND (
              (cf.capex > f.revenue * 3  AND f.revenue >= 5e10)
              OR (cf.capex > f.revenue * 10 AND f.revenue >= 1e10 AND f.revenue < 5e10)
              OR (cf.capex > 5e11 AND cf.capex > f.revenue * 2)
            )
        ), extreme_bad AS (
          SELECT cf.id
          FROM cash_flow_data cf
          JOIN stock_universe su ON su.stock_code=cf.stock_code
          LEFT JOIN rev ON rev.stock_code=cf.stock_code
          WHERE cf.year >= 2020
            AND cf.stock_code NOT LIKE '9%'
            AND cf.stock_code NOT IN ('005930','000660','005380','005490','000270')
            AND COALESCE(su.sector_large,'') NOT IN ('금융','보험','은행','금융서비스','의료','제약','헬스케어')
            AND (
              (cf.capex IS NOT NULL AND (
                  (cf.capex > 1e12 AND rev.max_rev IS NOT NULL AND cf.capex > rev.max_rev * 5)
                  OR (cf.capex > 1e13 AND rev.max_rev IS NULL)
              ))
              OR (cf.operating_cf IS NOT NULL AND (
                  (ABS(cf.operating_cf) > 1e12 AND rev.max_rev IS NOT NULL AND ABS(cf.operating_cf) > rev.max_rev * 5)
                  OR (ABS(cf.operating_cf) > 1e14 AND rev.max_rev IS NULL)
              ))
            )
        )
        SELECT cf.*, rev.max_rev,
               CASE WHEN cf.capex IS NOT NULL AND (
                   cf.id IN (SELECT id FROM annual_bad)
                   OR (cf.capex > 1e12 AND rev.max_rev IS NOT NULL AND cf.capex > rev.max_rev * 5)
                   OR (cf.capex > 1e13 AND rev.max_rev IS NULL)
               ) THEN 1 ELSE 0 END AS null_capex,
               CASE WHEN cf.operating_cf IS NOT NULL AND (
                   (ABS(cf.operating_cf) > 1e12 AND rev.max_rev IS NOT NULL AND ABS(cf.operating_cf) > rev.max_rev * 5)
                   OR (ABS(cf.operating_cf) > 1e14 AND rev.max_rev IS NULL)
               ) THEN 1 ELSE 0 END AS null_operating_cf
        FROM cash_flow_data cf
        LEFT JOIN rev ON rev.stock_code=cf.stock_code
        WHERE cf.id IN (SELECT id FROM annual_bad UNION SELECT id FROM extreme_bad)
        """,
    )
    if affected:
        conn.execute(
            f"""
            UPDATE cash_flow_data
            SET capex=CASE WHEN (SELECT null_capex FROM {table} b WHERE b.id=cash_flow_data.id)=1 THEN NULL ELSE capex END,
                capex_q=CASE WHEN (SELECT null_capex FROM {table} b WHERE b.id=cash_flow_data.id)=1 THEN NULL ELSE capex_q END,
                operating_cf=CASE WHEN (SELECT null_operating_cf FROM {table} b WHERE b.id=cash_flow_data.id)=1 THEN NULL ELSE operating_cf END,
                operating_cf_q=CASE WHEN (SELECT null_operating_cf FROM {table} b WHERE b.id=cash_flow_data.id)=1 THEN NULL ELSE operating_cf_q END,
                data_source=COALESCE(data_source,'') || '_integrity_i5_i9_null'
            WHERE id IN (SELECT id FROM {table})
            """
        )
    log_repair(conn, run_id, "cash_flow_data", "null_capex_or_ocf_extreme_integrity_i5_i9", affected, table)
    return {"affected": affected, "backup": table}


def repair_equity_spikes(conn: sqlite3.Connection, run_id: str) -> dict:
    table = f"data_quality_backup_i10_equity_spike_{run_id}"
    affected = backup(
        conn,
        table,
        """
        SELECT f.*
        FROM financial_data f
        JOIN financial_data prev ON prev.stock_code=f.stock_code
          AND (
            (prev.year=f.year AND prev.quarter=f.quarter-1)
            OR (f.quarter=1 AND prev.year=f.year-1 AND prev.quarter=4)
          )
          AND prev.is_annual=0 AND prev.report_type='CFS'
          AND prev.total_equity > 5e9
        JOIN stock_universe su ON su.stock_code=f.stock_code
        WHERE f.is_annual=0 AND f.report_type='CFS'
          AND f.year >= 2022
          AND f.total_equity IS NOT NULL AND f.total_equity < -1e9
          AND f.total_assets IS NOT NULL AND f.total_assets > 1e10
          AND f.net_income IS NOT NULL
          AND ABS(f.total_equity - prev.total_equity) > ABS(f.net_income) * 3
          AND COALESCE(su.sector_large,'') NOT IN ('금융','보험','은행','금융서비스')
          AND f.stock_code NOT LIKE '9%'
          AND NOT (f.quarter=4 AND EXISTS (
            SELECT 1 FROM financial_data fa
            WHERE fa.stock_code=f.stock_code AND fa.year=f.year AND fa.is_annual=1
              AND fa.total_equity IS NOT NULL
              AND ABS(fa.total_equity - f.total_equity) / NULLIF(ABS(fa.total_equity),0) < 0.05
          ))
          AND COALESCE(f.data_source,'') NOT LIKE 'dart%'
          AND COALESCE(f.data_source,'') NOT LIKE '%dart_recollect%'
        """,
    )
    if affected:
        conn.execute(
            f"""
            UPDATE financial_data
            SET total_equity=NULL,
                data_source=COALESCE(data_source,'') || '_integrity_i10_equity_null'
            WHERE id IN (SELECT id FROM {table})
            """
        )
    log_repair(conn, run_id, "financial_data", "null_unexplained_negative_equity_spike", affected, table)
    return {"affected": affected, "backup": table}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    conn = connect()
    try:
        ensure_log(conn)
        dropped = prune_old_backups(conn)
        if dropped:
            print(f"[BACKUP_PRUNE] {BACKUP_RETENTION_DAYS}일 초과 백업 {dropped}개 삭제")
        before = count_issues(conn)
        repairs = {
            "quarter_revenue": repair_quarter_revenue(conn, run_id),
            "cashflow_extremes": repair_cashflow_extremes(conn, run_id),
            "equity_spikes": repair_equity_spikes(conn, run_id),
        }
        after = count_issues(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    payload = {"run_id": run_id, "before": before, "repairs": repairs, "after": after}
    out = OUT / f"summary_{run_id}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
