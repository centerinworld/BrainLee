#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB = ROOT / "stock.db"
OUT_ROOT = ROOT / "research_outputs" / "core_data_quality_20260624"
TODAY = date.today()


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def q1(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def rows(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def write_csv(path: Path, data: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not data:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(data[0].keys()))
        writer.writeheader()
        writer.writerows(data)


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        r["name"]
        for r in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def count_where(conn: sqlite3.Connection, table: str, where: str) -> int:
    return int(q1(conn, f'SELECT COUNT(*) FROM "{table}" WHERE {where}') or 0)


def audit_dynamic_table_health(conn: sqlite3.Connection, out_dir: Path) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    future_rows: list[dict[str, Any]] = []
    stock_code_rows: list[dict[str, Any]] = []

    date_cols = {
        "date",
        "base_date",
        "bas_dt",
        "dt",
        "rcept_dt",
        "ym",
    }

    for table in table_names(conn):
        cols = table_columns(conn, table)
        n = int(q1(conn, f'SELECT COUNT(*) FROM "{table}"') or 0)
        summaries.append({"table": table, "rows": n})

        if "stock_code" in cols:
            bad_code = count_where(
                conn,
                table,
                """
                stock_code IS NULL OR TRIM(stock_code)=''
                OR (
                    stock_code NOT GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                    AND stock_code NOT LIKE '^%'
                    AND stock_code NOT LIKE '%=%'
                )
                """,
            )
            if bad_code:
                stock_code_rows.append({"table": table, "bad_stock_code_rows": bad_code})

        for col in cols:
            if col not in date_cols:
                continue
            expr = None
            if col in {"bas_dt", "rcept_dt"}:
                expr = f"date(substr({col},1,4)||'-'||substr({col},5,2)||'-'||substr({col},7,2))"
            elif col == "ym":
                expr = f"date(substr({col},1,4)||'-'||substr({col},5,2)||'-01')"
            else:
                expr = f"date({col})"
            future = count_where(conn, table, f"{col} IS NOT NULL AND {expr} > date('{TODAY.isoformat()}')")
            if future:
                future_rows.append({"table": table, "date_column": col, "future_rows": future})

    write_csv(out_dir / "table_row_counts.csv", summaries)
    write_csv(out_dir / "future_dated_rows.csv", future_rows)
    write_csv(out_dir / "bad_stock_code_rows.csv", stock_code_rows)

    return {
        "table_count": len(summaries),
        "future_dated_table_columns": len(future_rows),
        "bad_stock_code_tables": len(stock_code_rows),
        "row_count_csv": str(out_dir / "table_row_counts.csv"),
        "future_rows_csv": str(out_dir / "future_dated_rows.csv"),
        "bad_stock_code_csv": str(out_dir / "bad_stock_code_rows.csv"),
    }


def audit_core(conn: sqlite3.Connection, out_dir: Path) -> dict[str, Any]:
    issue_files: dict[str, str] = {}

    price_invalid_sql = """
        stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND (
            open<=0 OR high<=0 OR low<=0 OR close<=0
            OR high<low OR high<open OR high<close
            OR low>open OR low>close OR volume<0
        )
    """
    price_invalid_ph_sql = """
        ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND (
            ph.open<=0 OR ph.high<=0 OR ph.low<=0 OR ph.close<=0
            OR ph.high<ph.low OR ph.high<ph.open OR ph.high<ph.close
            OR ph.low>ph.open OR ph.low>ph.close OR ph.volume<0
        )
    """
    stock_price_invalid_sql = """
        stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND (
            open_price<=0 OR high_price<=0 OR low_price<=0 OR close_price<=0
            OR high_price<low_price OR high_price<open_price OR high_price<close_price
            OR low_price>open_price OR low_price>close_price
            OR volume<0 OR trade_amt<0 OR market_cap<0
        )
    """
    stock_price_invalid_sp_sql = """
        sp.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        AND (
            sp.open_price<=0 OR sp.high_price<=0 OR sp.low_price<=0 OR sp.close_price<=0
            OR sp.high_price<sp.low_price OR sp.high_price<sp.open_price OR sp.high_price<sp.close_price
            OR sp.low_price>sp.open_price OR sp.low_price>sp.close_price
            OR sp.volume<0 OR sp.trade_amt<0 OR sp.market_cap<0
        )
    """

    samples = {
        "price_history_invalid_ohlcv": rows(
            conn,
            f"""
            SELECT stock_code, date, open, high, low, close, volume, trade_amount
            FROM price_history
            WHERE {price_invalid_sql}
            ORDER BY date DESC, stock_code
            LIMIT 5000
            """,
        ),
        "stock_price_daily_invalid_ohlcv": rows(
            conn,
            f"""
            SELECT bas_dt, stock_code, stock_name, open_price, high_price, low_price,
                   close_price, volume, trade_amt, market_cap
            FROM stock_price_daily
            WHERE {stock_price_invalid_sql}
            ORDER BY bas_dt DESC, stock_code
            LIMIT 5000
            """,
        ),
        "financial_quarter_eq_annual_q1_q3": rows(
            conn,
            """
            WITH a AS (
              SELECT stock_code, year, COALESCE(report_type,'CFS') report_type, revenue a_rev
              FROM financial_data
              WHERE is_annual=1 AND revenue IS NOT NULL
            ), q AS (
              SELECT stock_code, year, quarter, COALESCE(report_type,'CFS') report_type,
                     revenue q_rev, data_source
              FROM financial_data
              WHERE is_annual=0 AND quarter IN (1,2,3) AND revenue IS NOT NULL
            )
            SELECT q.stock_code, q.year, q.quarter, q.report_type, q.q_rev, a.a_rev, q.data_source
            FROM q JOIN a
              ON a.stock_code=q.stock_code AND a.year=q.year AND a.report_type=q.report_type
            WHERE q.q_rev=a.a_rev AND q.q_rev>0
            ORDER BY q.year DESC, q.stock_code, q.quarter
            LIMIT 5000
            """,
        ),
        "cashflow_quarter_eq_annual_q1_q3": rows(
            conn,
            """
            WITH a AS (
              SELECT stock_code, year, COALESCE(report_type,'CFS') report_type,
                     operating_cf a_op, investing_cf a_inv, financing_cf a_fin
              FROM cash_flow_data
              WHERE is_annual=1
            ), q AS (
              SELECT stock_code, year, quarter, COALESCE(report_type,'CFS') report_type,
                     COALESCE(operating_cf_q, operating_cf) q_op,
                     COALESCE(investing_cf_q, investing_cf) q_inv,
                     COALESCE(financing_cf_q, financing_cf) q_fin,
                     data_source
              FROM cash_flow_data
              WHERE is_annual=0 AND quarter IN (1,2,3)
            )
            SELECT q.stock_code, q.year, q.quarter, q.report_type,
                   q.q_op, a.a_op, q.q_inv, a.a_inv, q.q_fin, a.a_fin, q.data_source
            FROM q JOIN a
              ON a.stock_code=q.stock_code AND a.year=q.year AND a.report_type=q.report_type
            WHERE (q.q_op=a.a_op AND ABS(q.q_op)>0)
               OR (q.q_inv=a.a_inv AND ABS(q.q_inv)>0)
               OR (q.q_fin=a.a_fin AND ABS(q.q_fin)>0)
            ORDER BY q.year DESC, q.stock_code, q.quarter
            LIMIT 5000
            """,
        ),
        "kiwoom_investor_daily_invalid_market_fields": rows(
            conn,
            """
            SELECT stock_code, dt, close_pric, acc_trde_qty, acc_trde_prica
            FROM kiwoom_investor_daily
            WHERE close_pric<0 OR acc_trde_qty<0 OR acc_trde_prica<0
               OR close_pric IS NULL OR acc_trde_qty IS NULL OR acc_trde_prica IS NULL
            ORDER BY dt DESC, stock_code
            LIMIT 5000
            """,
        ),
    }

    for name, data in samples.items():
        path = out_dir / f"{name}.csv"
        write_csv(path, data)
        issue_files[name] = str(path)

    counts = {
        "price_history_rows": int(q1(conn, "SELECT COUNT(*) FROM price_history") or 0),
        "price_history_invalid_ohlcv_numeric": int(q1(conn, f"SELECT COUNT(*) FROM price_history WHERE {price_invalid_sql}") or 0),
        "price_history_trade_amount_missing_repairable": int(
            q1(
                conn,
                """
                SELECT COUNT(*)
                FROM price_history
                WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND close>0 AND volume>0
                  AND (trade_amount IS NULL OR trade_amount<=0)
                """,
            )
            or 0
        ),
        "price_history_invalid_repairable_from_stock_price_daily": int(
            q1(
                conn,
                f"""
                SELECT COUNT(*)
                FROM price_history ph
                JOIN stock_price_daily sp
                  ON sp.stock_code=ph.stock_code
                 AND sp.bas_dt=replace(substr(ph.date,1,10),'-','')
                WHERE {price_invalid_ph_sql}
                  AND sp.open_price>0
                  AND sp.high_price>=sp.low_price
                  AND sp.high_price>=sp.open_price
                  AND sp.high_price>=sp.close_price
                  AND sp.low_price<=sp.open_price
                  AND sp.low_price<=sp.close_price
                  AND sp.volume>=0
                """,
            )
            or 0
        ),
        "stock_price_daily_rows": int(q1(conn, "SELECT COUNT(*) FROM stock_price_daily") or 0),
        "stock_price_daily_invalid_ohlcv_numeric": int(q1(conn, f"SELECT COUNT(*) FROM stock_price_daily WHERE {stock_price_invalid_sql}") or 0),
        "stock_price_daily_invalid_repairable_from_price_history": int(
            q1(
                conn,
                f"""
                SELECT COUNT(*)
                FROM stock_price_daily sp
                JOIN price_history ph
                  ON ph.stock_code=sp.stock_code
                 AND replace(substr(ph.date,1,10),'-','')=sp.bas_dt
                WHERE {stock_price_invalid_sp_sql}
                  AND ph.open>0
                  AND ph.high>=ph.low
                  AND ph.high>=ph.open
                  AND ph.high>=ph.close
                  AND ph.low<=ph.open
                  AND ph.low<=ph.close
                  AND ph.volume>=0
                """,
            )
            or 0
        ),
        "stock_universe_invalid_ohlcv_numeric": int(
            q1(
                conn,
                """
                SELECT COUNT(*)
                FROM stock_universe
                WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND (
                    close<=0 OR open<=0 OR high<=0 OR low<=0
                    OR high<low OR high<open OR high<close
                    OR low>open OR low>close OR volume<0
                  )
                """,
            )
            or 0
        ),
        "stock_universe_trading_value_missing_repairable": int(
            q1(
                conn,
                """
                SELECT COUNT(*)
                FROM stock_universe
                WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  AND close>0 AND volume>0
                  AND (trading_value IS NULL OR trading_value<=0)
                """,
            )
            or 0
        ),
        "financial_duplicate_keys": int(
            q1(
                conn,
                """
                SELECT COUNT(*) FROM (
                  SELECT stock_code, year, quarter, is_annual, COALESCE(report_type,'CFS') rt, COUNT(*) c
                  FROM financial_data
                  GROUP BY 1,2,3,4,5
                  HAVING c>1
                )
                """,
            )
            or 0
        ),
        "cashflow_duplicate_keys": int(
            q1(
                conn,
                """
                SELECT COUNT(*) FROM (
                  SELECT stock_code, year, quarter, is_annual, COALESCE(report_type,'CFS') rt, COUNT(*) c
                  FROM cash_flow_data
                  GROUP BY 1,2,3,4,5
                  HAVING c>1
                )
                """,
            )
            or 0
        ),
        "financial_quarter_eq_annual_q1_q3": len(samples["financial_quarter_eq_annual_q1_q3"]),
        "cashflow_quarter_eq_annual_q1_q3": len(samples["cashflow_quarter_eq_annual_q1_q3"]),
        "investor_trading_daily_net_inconsistent": int(
            q1(
                conn,
                """
                SELECT COUNT(*)
                FROM investor_trading_daily
                WHERE (indv_buy IS NOT NULL AND indv_sell IS NOT NULL AND indv_net IS NOT NULL
                       AND ABS(indv_net-(indv_buy-indv_sell))>1)
                   OR (inst_buy IS NOT NULL AND inst_sell IS NOT NULL AND inst_net IS NOT NULL
                       AND ABS(inst_net-(inst_buy-inst_sell))>1)
                   OR (frgn_buy IS NOT NULL AND frgn_sell IS NOT NULL AND frgn_net IS NOT NULL
                       AND ABS(frgn_net-(frgn_buy-frgn_sell))>1)
                """,
            )
            or 0
        ),
        "kiwoom_investor_daily_invalid_market_fields": int(
            q1(
                conn,
                """
                SELECT COUNT(*)
                FROM kiwoom_investor_daily
                WHERE close_pric<0 OR acc_trde_qty<0 OR acc_trde_prica<0
                   OR close_pric IS NULL OR acc_trde_qty IS NULL OR acc_trde_prica IS NULL
                """,
            )
            or 0
        ),
        "index_rows_after_today": int(
            q1(
                conn,
                f"""
                SELECT COUNT(*)
                FROM price_history
                WHERE stock_code IN ('^KS11','^KQ11','^KS200','^KQ150')
                  AND date(date)>date('{TODAY.isoformat()}')
                """,
            )
            or 0
        ),
        "index_invalid_ohlcv": int(
            q1(
                conn,
                """
                SELECT COUNT(*)
                FROM price_history
                WHERE stock_code IN ('^KS11','^KQ11','^KS200','^KQ150')
                  AND (
                    open<=0 OR high<=0 OR low<=0 OR close<=0
                    OR high<low OR high<open OR high<close
                    OR low>open OR low>close OR volume<0
                  )
                """,
            )
            or 0
        ),
    }

    return {"counts": counts, "issue_files": issue_files}


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


def log_repair(
    conn: sqlite3.Connection,
    run_id: str,
    table: str,
    repair: str,
    affected: int,
    backup_table: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO data_quality_repair_log
          (run_id, table_name, repair_name, affected_rows, backup_table)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, table, repair, affected, backup_table),
    )


def apply_repairs(conn: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    ensure_log(conn)
    repairs: list[dict[str, Any]] = []

    def record(table: str, repair: str, affected: int, backup_table: str | None) -> None:
        log_repair(conn, run_id, table, repair, affected, backup_table)
        repairs.append(
            {
                "table": table,
                "repair": repair,
                "affected_rows": affected,
                "backup_table": backup_table,
            }
        )

    # 1) Repair price_history OHLCV from the KRX-like daily table only where
    # price_history is invalid and the peer row is internally valid.
    backup = f"data_quality_backup_price_history_ohlcv_{run_id}"
    conn.execute(f"DROP TABLE IF EXISTS {backup}")
    conn.execute(
        f"""
        CREATE TABLE {backup} AS
        SELECT ph.id, ph.stock_code, ph.date,
               ph.open old_open, ph.high old_high, ph.low old_low, ph.close old_close,
               ph.volume old_volume, ph.trade_amount old_trade_amount,
               sp.open_price new_open, sp.high_price new_high, sp.low_price new_low,
               sp.close_price new_close, sp.volume new_volume, sp.trade_amt new_trade_amount
        FROM price_history ph
        JOIN stock_price_daily sp
          ON sp.stock_code=ph.stock_code
         AND sp.bas_dt=replace(substr(ph.date,1,10),'-','')
        WHERE ph.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND (
            ph.open<=0 OR ph.high<=0 OR ph.low<=0 OR ph.close<=0
            OR ph.high<ph.low OR ph.high<ph.open OR ph.high<ph.close
            OR ph.low>ph.open OR ph.low>ph.close OR ph.volume<0
          )
          AND sp.open_price>0
          AND sp.high_price>=sp.low_price
          AND sp.high_price>=sp.open_price
          AND sp.high_price>=sp.close_price
          AND sp.low_price<=sp.open_price
          AND sp.low_price<=sp.close_price
          AND sp.volume>=0
        """
    )
    affected = int(q1(conn, f"SELECT COUNT(*) FROM {backup}") or 0)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{backup}_id ON {backup}(id)")
    if affected:
        conn.execute(
            f"""
            UPDATE price_history
            SET open=b.new_open,
                high=b.new_high,
                low=b.new_low,
                close=b.new_close,
                volume=b.new_volume,
                trade_amount=COALESCE(NULLIF(b.new_trade_amount,0), price_history.trade_amount)
            FROM {backup} b
            WHERE price_history.id=b.id
            """
        )
    record("price_history", "repair_invalid_ohlcv_from_stock_price_daily", affected, backup)

    # 2) Repair stock_price_daily from price_history where the direction is safer.
    backup = f"data_quality_backup_stock_price_daily_ohlcv_{run_id}"
    conn.execute(f"DROP TABLE IF EXISTS {backup}")
    conn.execute(
        f"""
        CREATE TABLE {backup} AS
        SELECT sp.id, sp.bas_dt, sp.stock_code,
               sp.open_price old_open_price, sp.high_price old_high_price,
               sp.low_price old_low_price, sp.close_price old_close_price,
               sp.volume old_volume, sp.trade_amt old_trade_amt,
               ph.open new_open_price, ph.high new_high_price, ph.low new_low_price,
               ph.close new_close_price, ph.volume new_volume,
               ph.trade_amount new_trade_amt
        FROM stock_price_daily sp
        JOIN price_history ph
          ON ph.stock_code=sp.stock_code
         AND replace(substr(ph.date,1,10),'-','')=sp.bas_dt
        WHERE sp.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND (
            sp.open_price<=0 OR sp.high_price<=0 OR sp.low_price<=0 OR sp.close_price<=0
            OR sp.high_price<sp.low_price OR sp.high_price<sp.open_price OR sp.high_price<sp.close_price
            OR sp.low_price>sp.open_price OR sp.low_price>sp.close_price
            OR sp.volume<0
          )
          AND ph.open>0
          AND ph.high>=ph.low
          AND ph.high>=ph.open
          AND ph.high>=ph.close
          AND ph.low<=ph.open
          AND ph.low<=ph.close
          AND ph.volume>=0
        """
    )
    affected = int(q1(conn, f"SELECT COUNT(*) FROM {backup}") or 0)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{backup}_id ON {backup}(id)")
    if affected:
        conn.execute(
            f"""
            UPDATE stock_price_daily
            SET open_price=b.new_open_price,
                high_price=b.new_high_price,
                low_price=b.new_low_price,
                close_price=b.new_close_price,
                volume=b.new_volume,
                trade_amt=COALESCE(NULLIF(b.new_trade_amt,0), stock_price_daily.trade_amt)
            FROM {backup} b
            WHERE stock_price_daily.id=b.id
            """
        )
    record("stock_price_daily", "repair_invalid_ohlcv_from_price_history", affected, backup)

    # 3) Backfill trade_amount for price_history. This is derived, not official;
    # it prevents liquidity filters from treating normal traded days as zero.
    backup = f"data_quality_backup_price_history_trade_amount_{run_id}"
    conn.execute(f"DROP TABLE IF EXISTS {backup}")
    conn.execute(
        f"""
        CREATE TABLE {backup} AS
        SELECT id, stock_code, date, close, volume, trade_amount old_trade_amount,
               ROUND(close * volume) new_trade_amount
        FROM price_history
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND close>0 AND volume>0
          AND (trade_amount IS NULL OR trade_amount<=0)
        """
    )
    affected = int(q1(conn, f"SELECT COUNT(*) FROM {backup}") or 0)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{backup}_id ON {backup}(id)")
    if affected:
        conn.execute(
            f"""
            UPDATE price_history
            SET trade_amount=b.new_trade_amount
            FROM {backup} b
            WHERE price_history.id=b.id
            """
        )
    record("price_history", "backfill_trade_amount_close_times_volume", affected, backup)

    # 4) Backfill stock_universe trading_value where it is missing but OHLCV is valid.
    backup = f"data_quality_backup_stock_universe_trading_value_{run_id}"
    conn.execute(f"DROP TABLE IF EXISTS {backup}")
    conn.execute(
        f"""
        CREATE TABLE {backup} AS
        SELECT id, stock_code, base_date, close, volume,
               trading_value old_trading_value, ROUND(close * volume) new_trading_value
        FROM stock_universe
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND close>0 AND volume>0
          AND (trading_value IS NULL OR trading_value<=0)
        """
    )
    affected = int(q1(conn, f"SELECT COUNT(*) FROM {backup}") or 0)
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{backup}_id ON {backup}(id)")
    if affected:
        conn.execute(
            f"""
            UPDATE stock_universe
            SET trading_value=b.new_trading_value
            FROM {backup} b
            WHERE stock_universe.id=b.id
            """
        )
    record("stock_universe", "backfill_trading_value_close_times_volume", affected, backup)

    conn.commit()
    return repairs


def write_report(path: Path, before: dict[str, Any], after: dict[str, Any], repairs: list[dict[str, Any]]) -> None:
    c0 = before["core"]["counts"]
    c1 = after["core"]["counts"]
    lines = [
        "# Core Data Quality Audit & Repair - 2026-06-24",
        "",
        "## Scope",
        "",
        "- SQLite DB: `/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db`",
        "- Dynamic audit: all non-system tables, row counts, bad stock codes, future-dated rows",
        "- Core audit: price, daily KRX-like prices, stock universe, financial/cashflow keys, investor flow consistency, Kiwoom market fields, repaired KRX index rows",
        "",
        "## Applied Repairs",
        "",
    ]
    for r in repairs:
        lines.append(
            f"- `{r['table']}` / `{r['repair']}`: {r['affected_rows']:,} rows "
            f"(backup: `{r['backup_table']}`)"
        )
    if not repairs:
        lines.append("- No repairs applied.")

    lines.extend(
        [
            "",
            "## Before vs After",
            "",
            "| Check | Before | After |",
            "|---|---:|---:|",
        ]
    )
    keys = [
        "price_history_invalid_ohlcv_numeric",
        "price_history_trade_amount_missing_repairable",
        "price_history_invalid_repairable_from_stock_price_daily",
        "stock_price_daily_invalid_ohlcv_numeric",
        "stock_price_daily_invalid_repairable_from_price_history",
        "stock_universe_invalid_ohlcv_numeric",
        "stock_universe_trading_value_missing_repairable",
        "financial_duplicate_keys",
        "cashflow_duplicate_keys",
        "financial_quarter_eq_annual_q1_q3",
        "cashflow_quarter_eq_annual_q1_q3",
        "investor_trading_daily_net_inconsistent",
        "kiwoom_investor_daily_invalid_market_fields",
        "index_invalid_ohlcv",
        "index_rows_after_today",
    ]
    for key in keys:
        lines.append(f"| `{key}` | {c0.get(key, 0):,} | {c1.get(key, 0):,} |")

    lines.extend(
        [
            "",
            "## Residual Issues",
            "",
            "- Remaining invalid OHLC rows are not overwritten unless another table has an internally valid same-code/same-date row.",
            "- Financial and cash-flow quarter-equals-annual cases are reported but not arithmetically overwritten; they require source-level rebuild from DART/FnGuide snapshots.",
            "- `kiwoom_investor_daily` market-field gaps are large and should be handled by source refresh or query-time filtering, not by synthetic prices.",
            "- `trade_amount` backfill in `price_history` is derived as `close * volume`, so it is suitable for liquidity filtering but should not be treated as official exchange turnover when official KRX turnover exists elsewhere.",
            "",
            "## Output Files",
            "",
            f"- Summary JSON: `{path.with_name('summary.json')}`",
            f"- Table row counts: `{after['dynamic']['row_count_csv']}`",
            f"- Future-dated rows: `{after['dynamic']['future_rows_csv']}`",
            f"- Bad stock-code rows: `{after['dynamic']['bad_stock_code_csv']}`",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply high-confidence repairs after the audit.")
    args = parser.parse_args()

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = connect()
    before = {
        "dynamic": audit_dynamic_table_health(conn, out_dir / "before"),
        "core": audit_core(conn, out_dir / "before"),
    }
    repairs: list[dict[str, Any]] = []
    if args.apply:
        repairs = apply_repairs(conn, run_id)

    if args.apply:
        after = {
            "dynamic": audit_dynamic_table_health(conn, out_dir / "after"),
            "core": audit_core(conn, out_dir / "after"),
        }
    else:
        after = before

    summary = {
        "run_id": run_id,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "database": str(DB),
        "applied_repairs": args.apply,
        "repairs": repairs,
        "before": before,
        "after": after,
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(out_dir / "report.md", before, after, repairs)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
