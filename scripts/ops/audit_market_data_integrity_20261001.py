#!/usr/bin/env python3
"""Audit non-financial market data integrity in the Postgres primary DB.

Scope deliberately excludes financial statements and cash-flow tables. It
checks price, derived daily/weekly/monthly bar readiness, investor/foreign
flows, short-selling, securities lending, credit/margin, and program trading.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db, primary_database_label  # noqa: E402


RUN_DIR = ROOT / "run"
TODAY = date.today()
DATE_RE = re.compile(r"^\d{4}-?\d{2}-?\d{2}")


def to_jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def row_to_dict(columns: list[str], row: Any) -> dict[str, Any]:
    return {col: to_jsonable(row[idx]) for idx, col in enumerate(columns)}


class Auditor:
    def __init__(self) -> None:
        self.conn = connect_primary_db(readonly=True, timeout=180)
        self.cur = self.conn.cursor()
        self.issues: list[dict[str, Any]] = []
        self.sections: dict[str, Any] = {}
        self.schemas: dict[str, set[str]] = {}

    def close(self) -> None:
        self.conn.close()

    def execute(self, sql: str, params: tuple[Any, ...] | None = None, *, timeout_ms: int = 120000) -> None:
        self.cur.execute(f"SET statement_timeout = {int(timeout_ms)}")
        self.cur.execute(sql, params or ())

    def one(self, sql: str, params: tuple[Any, ...] | None = None, *, timeout_ms: int = 120000) -> Any:
        self.execute(sql, params, timeout_ms=timeout_ms)
        row = self.cur.fetchone()
        return row[0] if row else None

    def rows(
        self,
        sql: str,
        params: tuple[Any, ...] | None = None,
        *,
        limit: int | None = None,
        timeout_ms: int = 120000,
    ) -> list[dict[str, Any]]:
        self.execute(sql, params, timeout_ms=timeout_ms)
        cols = [d[0] for d in self.cur.description]
        out = [row_to_dict(cols, row) for row in self.cur.fetchall()]
        return out[:limit] if limit else out

    def table_exists(self, table: str) -> bool:
        return bool(
            self.one(
                """
                SELECT EXISTS (
                  SELECT 1 FROM information_schema.tables
                  WHERE table_schema='public' AND table_name=%s
                )
                """,
                (table,),
            )
        )

    def load_schemas(self) -> None:
        rows = self.rows(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema='public'
            ORDER BY table_name, ordinal_position
            """
        )
        schemas: dict[str, set[str]] = {}
        for r in rows:
            schemas.setdefault(r["table_name"], set()).add(r["column_name"])
        self.schemas = schemas

    def date_expr(self, column: str) -> str:
        q = f'"{column}"'
        return (
            f"CASE "
            f"WHEN {q} IS NULL THEN NULL "
            f"WHEN {q}::text ~ '^\\d{{8}}$' THEN to_date({q}::text, 'YYYYMMDD') "
            f"WHEN {q}::text ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}' THEN substring({q}::text,1,10)::date "
            f"ELSE NULL END"
        )

    def add_issue(
        self,
        severity: str,
        area: str,
        check: str,
        count: int,
        *,
        detail: str,
        sample: list[dict[str, Any]] | None = None,
    ) -> None:
        if count <= 0:
            return
        self.issues.append(
            {
                "severity": severity,
                "area": area,
                "check": check,
                "count": int(count),
                "detail": detail,
                "sample": sample or [],
            }
        )

    def generic_table_checks(self, spec: dict[str, Any]) -> None:
        table = spec["table"]
        if not self.table_exists(table):
            self.add_issue("CRITICAL", table, "table_missing", 1, detail=f"{table} table is missing")
            return
        cols = self.schemas.get(table, set())
        date_col = spec.get("date")
        key = spec.get("key", [])
        section: dict[str, Any] = {"columns_checked": sorted(cols)}
        section["row_count"] = int(self.one(f'SELECT COUNT(*) FROM "{table}"') or 0)
        if "stock_code" in cols:
            section["distinct_stock_codes"] = int(self.one(f'SELECT COUNT(DISTINCT stock_code) FROM "{table}"') or 0)
        if date_col and date_col in cols:
            dex = self.date_expr(date_col)
            row = self.rows(
                f"""
                SELECT MIN({dex}) AS min_date, MAX({dex}) AS max_date,
                       COUNT(*) FILTER (WHERE {dex} IS NULL AND "{date_col}" IS NOT NULL) AS invalid_date_rows,
                       COUNT(*) FILTER (WHERE {dex} > %s::date) AS future_date_rows
                FROM "{table}"
                """,
                (TODAY.isoformat(),),
            )[0]
            section.update(row)
            self.add_issue(
                "CRITICAL",
                table,
                "invalid_dates",
                int(row["invalid_date_rows"] or 0),
                detail=f"{table}.{date_col} has unparsable dates",
            )
            self.add_issue(
                "CRITICAL",
                table,
                "future_dates",
                int(row["future_date_rows"] or 0),
                detail=f"{table}.{date_col} contains dates after {TODAY}",
            )
        if key and all(c in cols for c in key):
            key_sql = ", ".join(f'"{c}"' for c in key)
            dup_count = int(
                self.one(
                    f"""
                    SELECT COUNT(*) FROM (
                      SELECT {key_sql}, COUNT(*) AS n
                      FROM "{table}"
                      GROUP BY {key_sql}
                      HAVING COUNT(*) > 1
                    ) d
                    """
                )
                or 0
            )
            section["duplicate_keys"] = dup_count
            sample = self.rows(
                f"""
                SELECT {key_sql}, COUNT(*) AS n
                FROM "{table}"
                GROUP BY {key_sql}
                HAVING COUNT(*) > 1
                ORDER BY n DESC
                LIMIT 10
                """,
                timeout_ms=60000,
            )
            self.add_issue(
                "CRITICAL",
                table,
                "duplicate_grain",
                dup_count,
                detail=f"{table} has duplicate rows at grain ({', '.join(key)})",
                sample=sample,
            )
        self.sections.setdefault("tables", {})[table] = section

    def audit_price_history(self) -> None:
        if not self.table_exists("price_history"):
            self.add_issue("CRITICAL", "price", "price_history_missing", 1, detail="price_history table is missing")
            return
        invalid = int(
            self.one(
                """
                SELECT COUNT(*)
                FROM price_history
                WHERE stock_code ~ '^[0-9]{6}$'
                  AND (
                    open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR
                    open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR
                    high < GREATEST(open, close, low) OR low > LEAST(open, close, high) OR
                    COALESCE(volume,0) < 0 OR COALESCE(trade_amount,0) < 0
                  )
                """,
                timeout_ms=180000,
            )
            or 0
        )
        invalid_sample = self.rows(
            """
            SELECT stock_code, date, open, high, low, close, volume, trade_amount
            FROM price_history
            WHERE stock_code ~ '^[0-9]{6}$'
              AND (
                open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL OR
                open <= 0 OR high <= 0 OR low <= 0 OR close <= 0 OR
                high < GREATEST(open, close, low) OR low > LEAST(open, close, high) OR
                COALESCE(volume,0) < 0 OR COALESCE(trade_amount,0) < 0
              )
            ORDER BY date DESC, stock_code
            LIMIT 20
            """,
            timeout_ms=180000,
        )
        self.add_issue(
            "CRITICAL",
            "price",
            "price_history_invalid_ohlcv",
            invalid,
            detail="6-digit Korean stock rows must have positive OHLC, high/low envelope consistency, and nonnegative volume/amount",
            sample=invalid_sample,
        )
        self.sections["price_history_validity"] = {
            "invalid_ohlcv_rows": invalid,
            "sample": invalid_sample,
        }

        if self.table_exists("canonical_price_history_v"):
            try:
                quality = self.rows(
                    """
                    SELECT COALESCE(canonical_quality, quality_status, 'unknown') AS quality, COUNT(*) AS rows
                    FROM canonical_price_history_v
                    WHERE stock_code ~ '^[0-9]{6}$'
                    GROUP BY 1
                    ORDER BY rows DESC
                    """,
                    timeout_ms=45000,
                )
                unresolved = sum(int(r["rows"]) for r in quality if str(r["quality"]) not in {"normal", "suspended"})
                self.sections["canonical_price_quality"] = quality
                self.add_issue(
                    "MEDIUM",
                    "price",
                    "canonical_non_normal_rows",
                    unresolved,
                    detail="canonical_price_history_v has rows outside normal/suspended quality; these need review before return use",
                    sample=quality[:10],
                )
            except Exception as exc:  # noqa: BLE001
                self.conn.rollback()
                self.sections["canonical_price_quality"] = {
                    "skipped": True,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "note": "Optional view-level quality distribution timed out; authoritative source-table OHLCV checks still ran on price_history.",
                }
                self.add_issue(
                    "MEDIUM",
                    "price",
                    "canonical_price_quality_view_timeout",
                    1,
                    detail="canonical_price_history_v quality distribution query timed out and was skipped; optimize/materialize this view if it is required for routine audits",
                )

        if self.table_exists("canonical_price_returns_v"):
            # The canonical returns view is expensive over the whole archive.
            # Full-history OHLCV invariants are checked directly on price_history;
            # this supplemental jump scan focuses on recent operational risk.
            try:
                extreme = int(
                    self.one(
                        """
                        SELECT COUNT(*)
                        FROM canonical_price_returns_v
                        WHERE stock_code ~ '^[0-9]{6}$'
                          AND date >= '2024-01-01'
                          AND COALESCE(return_usable, 0) = 1
                          AND safe_daily_return IS NOT NULL
                          AND ABS(safe_daily_return) > 0.35
                        """,
                        timeout_ms=45000,
                    )
                    or 0
                )
                sample: list[dict[str, Any]] = []
                self.sections["canonical_extreme_returns"] = {
                    "window": "date >= 2024-01-01",
                    "count": extreme,
                    "sample": sample,
                    "sample_note": "Skipped because canonical_price_returns_v sample retrieval was too expensive; price_history full-history OHLCV validation remains authoritative.",
                }
                self.add_issue(
                    "HIGH",
                    "price",
                    "extreme_usable_returns",
                    extreme,
                    detail="return_usable rows have absolute daily returns above 35%; likely split/corporate-action or stale-source exceptions",
                    sample=sample,
                )
            except Exception as exc:  # noqa: BLE001
                self.conn.rollback()
                self.sections["canonical_extreme_returns"] = {
                    "skipped": True,
                    "reason": f"{type(exc).__name__}: {exc}",
                    "note": "Optional returns-view jump scan timed out; source-table OHLCV checks still ran on price_history.",
                }
                self.add_issue(
                    "MEDIUM",
                    "price",
                    "canonical_returns_view_timeout",
                    1,
                    detail="canonical_price_returns_v jump scan timed out and was skipped; optimize/materialize this view if it is required for routine audits",
                )

    def audit_price_cross_source(self) -> None:
        if not (self.table_exists("price_history") and self.table_exists("stock_price_daily")):
            return
        mismatch = int(
            self.one(
                """
                SELECT COUNT(*)
                FROM price_history ph
                JOIN stock_price_daily spd
                  ON spd.stock_code = ph.stock_code
                 AND spd.bas_dt = ph.date
                WHERE ph.stock_code ~ '^[0-9]{6}$'
                  AND (
                    COALESCE(ph.open,-1) <> COALESCE(spd.open_price,-1) OR
                    COALESCE(ph.high,-1) <> COALESCE(spd.high_price,-1) OR
                    COALESCE(ph.low,-1) <> COALESCE(spd.low_price,-1) OR
                    COALESCE(ph.close,-1) <> COALESCE(spd.close_price,-1) OR
                    COALESCE(ph.volume,-1) <> COALESCE(spd.volume,-1)
                  )
                """,
                timeout_ms=240000,
            )
            or 0
        )
        sample = self.rows(
            """
            SELECT ph.stock_code, ph.date,
                   ph.open AS ph_open, spd.open_price AS spd_open,
                   ph.high AS ph_high, spd.high_price AS spd_high,
                   ph.low AS ph_low, spd.low_price AS spd_low,
                   ph.close AS ph_close, spd.close_price AS spd_close,
                   ph.volume AS ph_volume, spd.volume AS spd_volume
            FROM price_history ph
            JOIN stock_price_daily spd
              ON spd.stock_code = ph.stock_code
             AND spd.bas_dt = ph.date
            WHERE ph.stock_code ~ '^[0-9]{6}$'
              AND (
                COALESCE(ph.open,-1) <> COALESCE(spd.open_price,-1) OR
                COALESCE(ph.high,-1) <> COALESCE(spd.high_price,-1) OR
                COALESCE(ph.low,-1) <> COALESCE(spd.low_price,-1) OR
                COALESCE(ph.close,-1) <> COALESCE(spd.close_price,-1) OR
                COALESCE(ph.volume,-1) <> COALESCE(spd.volume,-1)
              )
            ORDER BY ph.date DESC, ph.stock_code
            LIMIT 20
            """,
            timeout_ms=240000,
        )
        self.sections["price_cross_source"] = {"price_history_vs_stock_price_daily_mismatch_rows": mismatch, "sample": sample}
        self.add_issue(
            "HIGH",
            "price",
            "price_history_vs_stock_price_daily_mismatch",
            mismatch,
            detail="Overlapping price_history and stock_price_daily rows disagree on OHLCV",
            sample=sample,
        )

    def audit_derived_bars(self) -> None:
        if not self.table_exists("price_history"):
            return
        def period_rows(grain: str) -> dict[str, Any]:
            if grain not in {"week", "month"}:
                raise ValueError(f"unsupported grain: {grain}")
            sql = f"""
            WITH base AS (
              SELECT stock_code, date::date AS d, open, high, low, close, COALESCE(volume,0) AS volume
              FROM price_history
              WHERE stock_code ~ '^[0-9]{{6}}$'
                AND date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
                AND open > 0 AND high > 0 AND low > 0 AND close > 0
            ),
            bars AS (
              SELECT stock_code,
                     date_trunc('{grain}', d)::date AS period_start,
                     (array_agg(open ORDER BY d ASC))[1] AS bar_open,
                     MAX(high) AS bar_high,
                     MIN(low) AS bar_low,
                     (array_agg(close ORDER BY d DESC))[1] AS bar_close,
                     SUM(volume) AS bar_volume,
                     COUNT(*) AS sessions
              FROM base
              GROUP BY stock_code, date_trunc('{grain}', d)::date
            )
            SELECT COUNT(*) AS periods,
                   COUNT(*) FILTER (
                     WHERE bar_open <= 0 OR bar_high <= 0 OR bar_low <= 0 OR bar_close <= 0
                        OR bar_high < GREATEST(bar_open, bar_close, bar_low)
                        OR bar_low > LEAST(bar_open, bar_close, bar_high)
                        OR bar_volume < 0 OR sessions <= 0
                   ) AS invalid_periods
            FROM bars
            """
            return self.rows(sql, timeout_ms=240000)[0]

        weekly = period_rows("week")
        monthly = period_rows("month")
        self.sections["derived_bars"] = {
            "basis": "Derived from price_history daily rows; no separate weekly/monthly OHLCV storage table was found.",
            "weekly": weekly,
            "monthly": monthly,
        }
        self.add_issue(
            "CRITICAL",
            "derived_bars",
            "weekly_invalid_periods",
            int(weekly["invalid_periods"] or 0),
            detail="Weekly bars derived from daily price_history fail OHLCV invariants",
        )
        self.add_issue(
            "CRITICAL",
            "derived_bars",
            "monthly_invalid_periods",
            int(monthly["invalid_periods"] or 0),
            detail="Monthly bars derived from daily price_history fail OHLCV invariants",
        )

    def audit_active_freshness(self) -> None:
        if not (self.table_exists("stock_universe") and self.table_exists("price_history")):
            return
        universe_cols = self.schemas.get("stock_universe", set())
        stock_name_expr = "stock_name" if "stock_name" in universe_cols else "NULL::text AS stock_name"
        filters = ["stock_code ~ '^[0-9]{6}$'"]
        if "is_active" in universe_cols:
            filters.append("COALESCE(is_active, true) = true")
        if "is_common_stock" in universe_cols:
            filters.append("COALESCE(is_common_stock, true) = true")
        else:
            common_parts = []
            if "stock_type" in universe_cols:
                common_parts.append("stock_type IS NULL OR stock_type !~ '(우선|우선주|스팩|SPAC|리츠|ETF|ETN)'")
            if "secugrp_nm" in universe_cols:
                common_parts.append("secugrp_nm IS NULL OR secugrp_nm !~ '(우선|스팩|SPAC|ETF|ETN)'")
            if "kind_stkcert_nm" in universe_cols:
                common_parts.append("kind_stkcert_nm IS NULL OR kind_stkcert_nm !~ '(우선|스팩|SPAC|ETF|ETN)'")
            if "stock_name" in universe_cols:
                common_parts.append("stock_name IS NULL OR stock_name !~ '(우선|스팩|SPAC|리츠|ETF|ETN)'")
            if common_parts:
                filters.append("(" + ") AND (".join(common_parts) + ")")
        universe_filter = " AND ".join(filters)
        reviewed_inactive_join = ""
        reviewed_inactive_where = ""
        if self.table_exists("price_stale_inactive_review"):
            reviewed_inactive_join = """
                LEFT JOIN price_stale_inactive_review psir
                  ON psir.stock_code = a.stock_code
                 AND psir.classification = 'stale_inactive_no_recent_external_trading'
            """
            reviewed_inactive_where = "AND psir.stock_code IS NULL"
        max_date = self.one("SELECT MAX(date::date) FROM price_history WHERE stock_code ~ '^[0-9]{6}$' AND date ~ '^\\d{4}-\\d{2}-\\d{2}$'")
        stale = int(
            self.one(
                f"""
                WITH active AS (
                  SELECT DISTINCT stock_code
                  FROM stock_universe
                  WHERE {universe_filter}
                ),
                latest AS (
                  SELECT stock_code, MAX(date::date) AS last_date
                  FROM price_history
                  WHERE stock_code ~ '^[0-9]{{6}}$' AND date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
                  GROUP BY stock_code
                )
                SELECT COUNT(*)
                FROM active a
                LEFT JOIN latest l USING (stock_code)
                {reviewed_inactive_join}
                WHERE l.last_date IS NULL OR l.last_date < %s::date - INTERVAL '10 days'
                {reviewed_inactive_where}
                """,
                (str(max_date),),
                timeout_ms=180000,
            )
            or 0
        )
        sample = self.rows(
            f"""
            WITH active AS (
              SELECT DISTINCT stock_code, {stock_name_expr}
              FROM stock_universe
              WHERE {universe_filter}
            ),
            latest AS (
              SELECT stock_code, MAX(date::date) AS last_date
              FROM price_history
              WHERE stock_code ~ '^[0-9]{{6}}$' AND date ~ '^\\d{{4}}-\\d{{2}}-\\d{{2}}$'
              GROUP BY stock_code
            )
            SELECT a.stock_code, a.stock_name, l.last_date
            FROM active a
            LEFT JOIN latest l USING (stock_code)
            {reviewed_inactive_join}
            WHERE l.last_date IS NULL OR l.last_date < %s::date - INTERVAL '10 days'
            {reviewed_inactive_where}
            ORDER BY l.last_date NULLS FIRST, a.stock_code
            LIMIT 30
            """,
            (str(max_date),),
            timeout_ms=180000,
        )
        self.sections["active_stock_price_freshness"] = {
            "global_latest_price_date": to_jsonable(max_date),
            "active_common_stale_over_10_days": stale,
            "sample": sample,
        }
        self.add_issue(
            "HIGH",
            "price",
            "active_common_price_stale",
            stale,
            detail="Active common-stock universe has no recent price_history row within 10 days of global latest date",
            sample=sample,
        )

    def audit_investor_flows(self) -> None:
        if self.table_exists("investor_trading_daily"):
            neg = int(
                self.one(
                    """
                    SELECT COUNT(*) FROM investor_trading_daily
                    WHERE COALESCE(indv_buy,0) < 0 OR COALESCE(indv_sell,0) < 0
                       OR COALESCE(inst_buy,0) < 0 OR COALESCE(inst_sell,0) < 0
                       OR COALESCE(frgn_buy,0) < 0 OR COALESCE(frgn_sell,0) < 0
                    """
                )
                or 0
            )
            net_bad = int(
                self.one(
                    """
                    SELECT COUNT(*) FROM investor_trading_daily
                    WHERE (indv_buy IS NOT NULL AND indv_sell IS NOT NULL AND indv_net IS NOT NULL
                           AND ABS(indv_buy - indv_sell - indv_net) > 1)
                       OR (inst_buy IS NOT NULL AND inst_sell IS NOT NULL AND inst_net IS NOT NULL
                           AND ABS(inst_buy - inst_sell - inst_net) > 1)
                       OR (frgn_buy IS NOT NULL AND frgn_sell IS NOT NULL AND frgn_net IS NOT NULL
                           AND ABS(frgn_buy - frgn_sell - frgn_net) > 1)
                    """
                )
                or 0
            )
            null_buy_sell = int(
                self.one(
                    """
                    SELECT COUNT(*) FROM investor_trading_daily
                    WHERE indv_buy IS NULL OR indv_sell IS NULL
                       OR inst_buy IS NULL OR inst_sell IS NULL
                       OR frgn_buy IS NULL OR frgn_sell IS NULL
                    """
                )
                or 0
            )
            self.sections["investor_trading_daily_validity"] = {
                "negative_buy_sell_rows": neg,
                "net_formula_mismatch_rows_when_buy_sell_present": net_bad,
                "rows_with_missing_buy_sell": null_buy_sell,
                "note": "investor_trading_daily is deprecated in project docs and often stores only net fields; price_history/kiwoom_investor_daily are preferred for operational supply signals.",
            }
            self.add_issue("CRITICAL", "investor", "negative_buy_sell", neg, detail="investor_trading_daily buy/sell fields must be nonnegative")
            self.add_issue("CRITICAL", "investor", "net_formula_mismatch", net_bad, detail="buy - sell must equal net for individual/institution/foreign flows")

        if self.table_exists("foreign_holding_daily"):
            invalid = int(
                self.one(
                    """
                    SELECT COUNT(*) FROM foreign_holding_daily
                    WHERE COALESCE(frgn_hold_qty,0) < 0
                       OR COALESCE(frgn_hold_pct,0) < 0 OR COALESCE(frgn_hold_pct,0) > 100
                       OR COALESCE(frgn_limit_pct,0) < 0 OR COALESCE(frgn_limit_pct,0) > 100
                    """
                )
                or 0
            )
            self.sections["foreign_holding_validity"] = {"invalid_qty_or_pct_rows": invalid}
            self.add_issue("CRITICAL", "foreign_holding", "invalid_qty_or_pct", invalid, detail="foreign holding quantities must be nonnegative and pct fields must be 0..100")

        if self.table_exists("kiwoom_foreign_flow"):
            invalid = int(
                self.one(
                    """
                    SELECT COUNT(*) FROM kiwoom_foreign_flow
                    WHERE COALESCE(close_price,0) < 0 OR COALESCE(poss_stock_cnt,0) < 0
                       OR COALESCE(weight,0) < 0 OR COALESCE(weight,0) > 100
                       OR COALESCE(limit_exhaust_rate,0) < 0 OR COALESCE(limit_exhaust_rate,0) > 100
                    """
                )
                or 0
            )
            self.sections["kiwoom_foreign_flow_validity"] = {"invalid_rows": invalid}
            self.add_issue("CRITICAL", "foreign_flow", "invalid_kiwoom_foreign_flow", invalid, detail="kiwoom foreign holding/weight fields outside valid ranges")

    def audit_short_credit_lending(self) -> None:
        checks = [
            (
                "short_sell_daily",
                "COALESCE(short_qty,0) < 0 OR COALESCE(short_amt,0) < 0 OR COALESCE(borrow_bal_qty,0) < 0 OR COALESCE(borrow_bal_amt,0) < 0 OR COALESCE(borrow_bal_pct,0) < 0 OR COALESCE(borrow_bal_pct,0) > 100",
            ),
            (
                "short_rank_daily",
                "COALESCE(lnb_ccl_stck_cnt,0) < 0 OR COALESCE(rcal_rdpt_stck_cnt,0) < 0 OR COALESCE(rdpt_stck_cnt,0) < 0 OR COALESCE(lnb_rman_stck_cnt,0) < 0 OR COALESCE(lnb_bal,0) < 0",
            ),
            (
                "short_sector_daily",
                "COALESCE(stck_lndn_bal,0) < 0 OR COALESCE(stck_lndn_rto,0) < 0 OR COALESCE(stck_lndn_rto,0) > 100 OR COALESCE(stck_brw_bal,0) < 0 OR COALESCE(stck_brw_rto,0) < 0 OR COALESCE(stck_brw_rto,0) > 100",
            ),
            (
                "short_sale_daily",
                "COALESCE(short_qty,0) < 0 OR COALESCE(short_amt,0) < 0 OR COALESCE(trade_volume,0) < 0 OR COALESCE(trade_amount,0) < 0 OR COALESCE(short_volume_ratio,0) < 0 OR COALESCE(short_volume_ratio,0) > 100 OR COALESCE(short_amount_ratio,0) < 0 OR COALESCE(short_amount_ratio,0) > 100",
            ),
            (
                "kiwoom_credit_balance",
                "COALESCE(credit_balance_qty,0) < 0 OR COALESCE(credit_balance_amt,0) < 0 OR COALESCE(credit_ratio,0) < 0 OR COALESCE(credit_ratio,0) > 100 OR COALESCE(new_credit_qty,0) < 0 OR COALESCE(repay_credit_qty,0) < 0",
            ),
            (
                "kiwoom_margin_daily",
                "COALESCE(credit_balance,0) < 0 OR COALESCE(credit_buy_balance,0) < 0 OR COALESCE(credit_sell_balance,0) < 0 OR COALESCE(loan_balance,0) < 0 OR COALESCE(short_balance,0) < 0 OR COALESCE(credit_ratio,0) < 0 OR COALESCE(credit_ratio,0) > 100",
            ),
            (
                "margin_balance_daily",
                "COALESCE(credit_balance,0) < 0 OR COALESCE(credit_amount,0) < 0 OR COALESCE(credit_ratio,0) < 0 OR COALESCE(credit_ratio,0) > 100 OR COALESCE(short_balance,0) < 0",
            ),
        ]
        section: dict[str, Any] = {}
        for table, predicate in checks:
            if not self.table_exists(table):
                continue
            invalid = int(self.one(f'SELECT COUNT(*) FROM "{table}" WHERE {predicate}') or 0)
            sample = self.rows(f'SELECT * FROM "{table}" WHERE {predicate} LIMIT 10')
            section[table] = {"invalid_rows": invalid, "sample": sample}
            self.add_issue("CRITICAL", table, "invalid_nonnegative_or_ratio_fields", invalid, detail=f"{table} contains negative quantities/amounts or invalid ratio range", sample=sample)

        if self.table_exists("short_foreign_trade"):
            mismatch = int(
                self.one(
                    """
                    SELECT COUNT(*) FROM short_foreign_trade
                    WHERE ABS(COALESCE(forg_lnb_ccl_stck_cnt,0) + COALESCE(ntiv_lnb_ccl_stck_cnt,0) - COALESCE(sum_lnb_ccl_stck_cnt,0)) > 1
                       OR ABS(COALESCE(forg_lnb_ccl_amt,0) + COALESCE(ntiv_lnb_ccl_amt,0) - COALESCE(sum_lnb_ccl_amt,0)) > 1
                    """
                )
                or 0
            )
            section["short_foreign_trade"] = {"sum_formula_mismatch_rows": mismatch}
            self.add_issue("CRITICAL", "short_foreign_trade", "sum_formula_mismatch", mismatch, detail="foreign + domestic securities lending totals must equal sum fields")

        if self.table_exists("short_sell_daily") and self.table_exists("short_rank_daily"):
            mismatch = int(
                self.one(
                    """
                    SELECT COUNT(*)
                    FROM short_sell_daily s
                    JOIN short_rank_daily r
                      ON r.stock_code = s.stock_code
                     AND r.bas_dt = s.bas_dt
                    WHERE s.borrow_bal_qty IS NOT NULL
                      AND r.lnb_rman_stck_cnt IS NOT NULL
                      AND ABS(s.borrow_bal_qty - r.lnb_rman_stck_cnt) > GREATEST(1000, ABS(r.lnb_rman_stck_cnt) * 0.02)
                    """,
                    timeout_ms=180000,
                )
                or 0
            )
            sample = self.rows(
                """
                SELECT s.stock_code, s.bas_dt, s.borrow_bal_qty, r.lnb_rman_stck_cnt
                FROM short_sell_daily s
                JOIN short_rank_daily r
                  ON r.stock_code = s.stock_code
                 AND r.bas_dt = s.bas_dt
                WHERE s.borrow_bal_qty IS NOT NULL
                  AND r.lnb_rman_stck_cnt IS NOT NULL
                  AND ABS(s.borrow_bal_qty - r.lnb_rman_stck_cnt) > GREATEST(1000, ABS(r.lnb_rman_stck_cnt) * 0.02)
                ORDER BY s.bas_dt DESC, s.stock_code
                LIMIT 20
                """,
                timeout_ms=180000,
            )
            section["short_sell_vs_rank_borrow_balance"] = {"mismatch_rows": mismatch, "sample": sample}
            self.add_issue("HIGH", "short_lending", "borrow_balance_cross_source_mismatch", mismatch, detail="short_sell_daily borrow balance differs materially from short_rank_daily remaining lending balance", sample=sample)

        self.sections["short_credit_lending_validity"] = section

    def audit_program_trading(self) -> None:
        if not self.table_exists("broker_program_stock_daily"):
            return
        invalid = int(
            self.one(
                """
                SELECT COUNT(*)
                FROM broker_program_stock_daily
                WHERE COALESCE(trade_volume,0) < 0 OR COALESCE(sell_qty,0) < 0 OR COALESCE(buy_qty,0) < 0
                   OR COALESCE(sell_amt_krw,0) < 0 OR COALESCE(buy_amt_krw,0) < 0
                   OR ABS(COALESCE(buy_qty,0) - COALESCE(sell_qty,0) - COALESCE(net_buy_qty,0)) > 1
                   OR ABS(COALESCE(buy_amt_krw,0) - COALESCE(sell_amt_krw,0) - COALESCE(net_buy_amt_krw,0)) > 1
                """
            )
            or 0
        )
        sample = self.rows(
            """
            SELECT source, stock_code, dt, sell_qty, buy_qty, net_buy_qty, sell_amt_krw, buy_amt_krw, net_buy_amt_krw
            FROM broker_program_stock_daily
            WHERE COALESCE(trade_volume,0) < 0 OR COALESCE(sell_qty,0) < 0 OR COALESCE(buy_qty,0) < 0
               OR COALESCE(sell_amt_krw,0) < 0 OR COALESCE(buy_amt_krw,0) < 0
               OR ABS(COALESCE(buy_qty,0) - COALESCE(sell_qty,0) - COALESCE(net_buy_qty,0)) > 1
               OR ABS(COALESCE(buy_amt_krw,0) - COALESCE(sell_amt_krw,0) - COALESCE(net_buy_amt_krw,0)) > 1
            LIMIT 20
            """
        )
        self.sections["program_trading_validity"] = {"invalid_or_net_mismatch_rows": invalid, "sample": sample}
        self.add_issue("CRITICAL", "program_trading", "invalid_or_net_mismatch", invalid, detail="program trading buy/sell/net fields are internally inconsistent", sample=sample)

    def run(self) -> dict[str, Any]:
        self.load_schemas()
        specs = [
            {"table": "price_history", "date": "date", "key": ["stock_code", "date"]},
            {"table": "stock_price_daily", "date": "bas_dt", "key": ["stock_code", "bas_dt"]},
            {"table": "investor_trading_daily", "date": "bas_dt", "key": ["stock_code", "bas_dt"]},
            {"table": "kiwoom_investor_daily", "date": "dt", "key": ["stock_code", "dt"]},
            {"table": "kiwoom_foreign_flow", "date": "dt", "key": ["stock_code", "dt"]},
            {"table": "foreign_holding_daily", "date": "bas_dt", "key": ["stock_code", "bas_dt"]},
            {"table": "short_sell_daily", "date": "bas_dt", "key": ["stock_code", "bas_dt"]},
            {"table": "short_rank_daily", "date": "bas_dt", "key": ["stock_code", "bas_dt"]},
            {"table": "short_sector_daily", "date": "bas_dt", "key": ["stock_code", "bas_dt", "sic_cd"]},
            {"table": "short_sale_daily", "date": "trade_date", "key": ["stock_code", "trade_date"]},
            {"table": "short_foreign_trade", "date": "bas_dt", "key": ["bas_dt"]},
            {"table": "kiwoom_credit_balance", "date": "dt", "key": ["stock_code", "dt"]},
            {"table": "kiwoom_margin_daily", "date": "base_date", "key": ["stock_code", "base_date"]},
            {"table": "margin_balance_daily", "date": "dt", "key": ["stock_code", "dt"]},
            {"table": "broker_program_stock_daily", "date": "dt", "key": ["source", "stock_code", "dt", "market_channel"]},
        ]
        for spec in specs:
            print(f"[audit] generic {spec['table']}", flush=True)
            self.generic_table_checks(spec)
        print("[audit] price_history", flush=True)
        self.audit_price_history()
        print("[audit] price cross-source", flush=True)
        self.audit_price_cross_source()
        print("[audit] derived bars", flush=True)
        self.audit_derived_bars()
        print("[audit] active freshness", flush=True)
        self.audit_active_freshness()
        print("[audit] investor flows", flush=True)
        self.audit_investor_flows()
        print("[audit] short/credit/lending", flush=True)
        self.audit_short_credit_lending()
        print("[audit] program trading", flush=True)
        self.audit_program_trading()
        return {
            "run_at": datetime.now().isoformat(timespec="seconds"),
            "database": primary_database_label(),
            "scope": "non_financial_market_data_only",
            "excluded": ["financial statements", "cash flows"],
            "sections": self.sections,
            "issues": sorted(
                self.issues,
                key=lambda r: ({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}.get(r["severity"], 9), -int(r["count"])),
            ),
        }


def write_markdown(report: dict[str, Any], path: Path) -> None:
    sev_counts: dict[str, int] = {}
    for issue in report["issues"]:
        sev_counts[issue["severity"]] = sev_counts.get(issue["severity"], 0) + 1
    lines = [
        f"# 시장 데이터 무결성 감사 — {report['run_at']}",
        "",
        f"- DB: `{report['database']}`",
        "- 범위: 주가, 일봉/주봉/월봉 파생 가능성, 외국인/투자자 수급, 공매도, 대차/신용/융자, 프로그램 매매",
        "- 제외: 재무제표, 현금흐름",
        f"- 이슈 수: CRITICAL {sev_counts.get('CRITICAL', 0)}, HIGH {sev_counts.get('HIGH', 0)}, MEDIUM {sev_counts.get('MEDIUM', 0)}",
        "",
        "## 핵심 이슈",
        "",
    ]
    if not report["issues"]:
        lines.append("발견된 무결성 이슈 없음.")
    else:
        lines += ["| 등급 | 영역 | 점검 | 건수 | 설명 |", "|---|---|---|--:|---|"]
        for issue in report["issues"][:80]:
            lines.append(
                f"| {issue['severity']} | {issue['area']} | `{issue['check']}` | {issue['count']:,} | {issue['detail']} |"
            )
    lines += ["", "## 테이블 커버리지", "", "| 테이블 | 행수 | 종목수 | 기간 | 중복키 |", "|---|--:|--:|---|--:|"]
    for table, info in sorted(report["sections"].get("tables", {}).items()):
        period = f"{info.get('min_date')} ~ {info.get('max_date')}" if info.get("min_date") or info.get("max_date") else "-"
        lines.append(
            f"| `{table}` | {int(info.get('row_count') or 0):,} | {int(info.get('distinct_stock_codes') or 0):,} | {period} | {int(info.get('duplicate_keys') or 0):,} |"
        )
    derived = report["sections"].get("derived_bars", {})
    if derived:
        lines += [
            "",
            "## 주봉/월봉",
            "",
            f"- 저장 테이블: 별도 발견 없음. 기준: {derived.get('basis')}",
            f"- 주봉 파생 기간 수: {int(derived.get('weekly', {}).get('periods') or 0):,}, 무효 기간: {int(derived.get('weekly', {}).get('invalid_periods') or 0):,}",
            f"- 월봉 파생 기간 수: {int(derived.get('monthly', {}).get('periods') or 0):,}, 무효 기간: {int(derived.get('monthly', {}).get('invalid_periods') or 0):,}",
        ]
    price_fresh = report["sections"].get("active_stock_price_freshness", {})
    if price_fresh:
        lines += [
            "",
            "## 가격 신선도",
            "",
            f"- 전체 최신 거래일: {price_fresh.get('global_latest_price_date')}",
            f"- 활성 보통주 중 10일 이상 가격 공백: {int(price_fresh.get('active_common_stale_over_10_days') or 0):,}",
        ]
    lines += ["", "## 검증 원칙", "", "- 모든 점검은 운영 Postgres primary DB를 읽기 전용으로 조회했다.", "- 재무제표/현금흐름 테이블은 감사 범위에서 제외했다.", "- JSON 보고서에는 각 주요 이슈의 샘플 행이 포함된다.", ""]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = RUN_DIR / f"market_data_integrity_{stamp}.json"
    md_path = RUN_DIR / f"market_data_integrity_{stamp}.md"
    auditor = Auditor()
    try:
        report = auditor.run()
    finally:
        auditor.close()
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=to_jsonable), encoding="utf-8")
    write_markdown(report, md_path)
    critical = sum(1 for i in report["issues"] if i["severity"] == "CRITICAL")
    high = sum(1 for i in report["issues"] if i["severity"] == "HIGH")
    medium = sum(1 for i in report["issues"] if i["severity"] == "MEDIUM")
    print(f"json={json_path}")
    print(f"markdown={md_path}")
    print(f"issues: CRITICAL={critical} HIGH={high} MEDIUM={medium}")
    for issue in report["issues"][:20]:
        print(f"{issue['severity']:8s} {issue['area']:28s} {issue['check']:42s} {issue['count']:12,d}")
    return 1 if critical or high else 0


if __name__ == "__main__":
    raise SystemExit(main())
