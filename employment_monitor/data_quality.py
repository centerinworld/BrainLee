#!/usr/bin/env python3
"""Employment data integrity and freshness audit."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EMP_DB = ROOT / "employment_monitor" / "employment.db"
DEFAULT_STOCK_DB = ROOT / "stock.db"


def _month_lag(data_ym: str | None, today: date | None = None) -> int | None:
    if not data_ym or len(data_ym) != 6 or not data_ym.isdigit():
        return None
    current = today or date.today()
    year, month = int(data_ym[:4]), int(data_ym[4:])
    if not 1 <= month <= 12:
        return None
    return (current.year - year) * 12 + current.month - month


def _missing_months(conn: sqlite3.Connection, table: str) -> list[str]:
    values = [str(row[0]) for row in conn.execute(
        f"SELECT DISTINCT data_ym FROM {table} ORDER BY data_ym"
    ).fetchall()]
    valid = {value for value in values if len(value) == 6 and value.isdigit()}
    if not valid:
        return []
    start, end = min(valid), max(valid)
    year, month = int(start[:4]), int(start[4:])
    missing: list[str] = []
    while f"{year:04d}{month:02d}" <= end:
        value = f"{year:04d}{month:02d}"
        if value not in valid:
            missing.append(value)
        month += 1
        if month == 13:
            year += 1
            month = 1
    return missing


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
    ).fetchone() is not None


def audit_employment_data(
    emp_db: str | Path = DEFAULT_EMP_DB,
    stock_db: str | Path | None = DEFAULT_STOCK_DB,
) -> dict:
    conn = sqlite3.connect(f"file:{Path(emp_db)}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
        latest_wlb = conn.execute("SELECT MAX(data_ym) FROM wlb_monthly").fetchone()[0]
        latest_nps = conn.execute("SELECT MAX(data_ym) FROM nps_monthly").fetchone()[0]

        checks = {
            "nps_duplicate_keys": conn.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM nps_monthly GROUP BY stock_code,data_ym HAVING COUNT(*)>1)"
            ).fetchone()[0],
            "wlb_duplicate_keys": conn.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM wlb_monthly GROUP BY stock_code,data_ym HAVING COUNT(*)>1)"
            ).fetchone()[0],
            "company_duplicate_keys": conn.execute(
                "SELECT COUNT(*) FROM (SELECT 1 FROM employment_company GROUP BY stock_code,ym HAVING COUNT(*)>1)"
            ).fetchone()[0],
            "nps_net_mismatch": conn.execute(
                "SELECT COUNT(*) FROM nps_monthly WHERE net_change != new_hires-terminations"
            ).fetchone()[0],
            "nps_negative_values": conn.execute(
                "SELECT COUNT(*) FROM nps_monthly WHERE new_hires<0 OR terminations<0 OR wkpl_count<0"
            ).fetchone()[0],
            "wlb_negative_values": conn.execute(
                "SELECT COUNT(*) FROM wlb_monthly WHERE total_workers<0 OR workplace_cnt<0"
            ).fetchone()[0],
            "invalid_nps_months": conn.execute(
                "SELECT COUNT(*) FROM nps_monthly WHERE LENGTH(data_ym)!=6 OR data_ym GLOB '*[^0-9]*' OR SUBSTR(data_ym,5,2) NOT BETWEEN '01' AND '12'"
            ).fetchone()[0],
            "invalid_wlb_months": conn.execute(
                "SELECT COUNT(*) FROM wlb_monthly WHERE LENGTH(data_ym)!=6 OR data_ym GLOB '*[^0-9]*' OR SUBSTR(data_ym,5,2) NOT BETWEEN '01' AND '12'"
            ).fetchone()[0],
            "invalid_stock_codes": conn.execute(
                "SELECT COUNT(*) FROM nps_monthly WHERE LENGTH(stock_code)!=6 OR UPPER(stock_code) GLOB '*[^0-9A-Z]*'"
            ).fetchone()[0] + conn.execute(
                "SELECT COUNT(*) FROM wlb_monthly WHERE LENGTH(stock_code)!=6 OR UPPER(stock_code) GLOB '*[^0-9A-Z]*'"
            ).fetchone()[0],
            "nps_zero_workplace_count": conn.execute(
                "SELECT COUNT(*) FROM nps_monthly WHERE wkpl_count=0"
            ).fetchone()[0],
            "nps_structural_transfer_candidates": conn.execute(
                "SELECT COUNT(*) FROM nps_monthly WHERE MAX(new_hires,terminations)>=2000"
            ).fetchone()[0],
        }
        if _table_exists(conn, "nps_portal_monthly"):
            checks.update({
                "nps_portal_net_mismatch": conn.execute(
                    "SELECT COUNT(*) FROM nps_portal_monthly WHERE net_change!=new_hires-terminations"
                ).fetchone()[0],
                "nps_portal_negative_values": conn.execute(
                    "SELECT COUNT(*) FROM nps_portal_monthly WHERE subscriber_count<0 OR new_hires<0 OR terminations<0 OR workplace_count<0"
                ).fetchone()[0],
            })
        if _table_exists(conn, "wlb_portal_annual"):
            checks["wlb_portal_negative_values"] = conn.execute(
                "SELECT COUNT(*) FROM wlb_portal_annual WHERE total_workers<0 OR workplace_count<0"
            ).fetchone()[0]

        monthly = {
            "wlb_latest_ym": latest_wlb,
            "wlb_latest_rows": conn.execute(
                "SELECT COUNT(*) FROM wlb_monthly WHERE data_ym=?", (latest_wlb,)
            ).fetchone()[0] if latest_wlb else 0,
            "wlb_month_lag": _month_lag(latest_wlb),
            "wlb_missing_months": _missing_months(conn, "wlb_monthly"),
            "nps_latest_ym": latest_nps,
            "nps_latest_rows": conn.execute(
                "SELECT COUNT(*) FROM nps_monthly WHERE data_ym=?", (latest_nps,)
            ).fetchone()[0] if latest_nps else 0,
            "nps_month_lag": _month_lag(latest_nps),
            "nps_missing_months": _missing_months(conn, "nps_monthly"),
        }

        history: dict[str, dict] = {}
        if _table_exists(conn, "nps_portal_monthly"):
            row = conn.execute(
                "SELECT COUNT(*),COUNT(DISTINCT data_ym),MIN(data_ym),MAX(data_ym),COUNT(DISTINCT stock_code) FROM nps_portal_monthly"
            ).fetchone()
            history["nps_portal"] = {
                "rows": row[0], "months": row[1], "oldest_ym": row[2], "latest_ym": row[3],
                "stocks": row[4], "missing_months": _missing_months(conn, "nps_portal_monthly"),
            }
        if _table_exists(conn, "wlb_portal_annual"):
            row = conn.execute(
                "SELECT COUNT(*),COUNT(DISTINCT data_year),MIN(data_year),MAX(data_year),COUNT(DISTINCT stock_code) FROM wlb_portal_annual"
            ).fetchone()
            present_years = {
                str(value[0]) for value in conn.execute("SELECT DISTINCT data_year FROM wlb_portal_annual")
                if str(value[0]).isdigit()
            }
            missing_years = (
                [str(year) for year in range(int(row[2]), int(row[3]) + 1) if str(year) not in present_years]
                if row[2] and row[3] else []
            )
            history["wlb_portal"] = {
                "rows": row[0], "years": row[1], "oldest_year": row[2], "latest_year": row[3],
                "stocks": row[4], "missing_years": missing_years,
            }

        coverage = None
        if stock_db and Path(stock_db).exists():
            try:
                conn.execute("ATTACH DATABASE ? AS stock_src", (str(stock_db),))
                universe = conn.execute(
                    "SELECT COUNT(DISTINCT stock_code) FROM stock_src.stock_universe WHERE secugrp_nm='주권'"
                ).fetchone()[0]
                wlb_stocks = conn.execute(
                    "SELECT COUNT(DISTINCT stock_code) FROM wlb_monthly WHERE data_ym=?", (latest_wlb,)
                ).fetchone()[0]
                nps_stocks = conn.execute(
                    "SELECT COUNT(DISTINCT stock_code) FROM nps_monthly WHERE data_ym=?", (latest_nps,)
                ).fetchone()[0]
                coverage = {
                    "universe_stocks": universe,
                    "wlb_stocks": wlb_stocks,
                    "wlb_pct": round(100 * wlb_stocks / universe, 1) if universe else None,
                    "nps_stocks": nps_stocks,
                    "nps_pct": round(100 * nps_stocks / universe, 1) if universe else None,
                }
            except sqlite3.Error:
                coverage = None

        errors: list[str] = []
        warnings: list[str] = []
        if integrity != ["ok"]:
            errors.append("SQLite integrity_check failed")
        for key in (
            "nps_duplicate_keys", "wlb_duplicate_keys", "company_duplicate_keys",
            "nps_net_mismatch", "nps_negative_values", "wlb_negative_values",
            "invalid_nps_months", "invalid_wlb_months", "invalid_stock_codes",
        ):
            if checks[key]:
                errors.append(f"{key}={checks[key]}")
        for key in ("nps_portal_net_mismatch", "nps_portal_negative_values", "wlb_portal_negative_values"):
            if checks.get(key):
                errors.append(f"{key}={checks[key]}")
        if monthly["wlb_month_lag"] is None or monthly["wlb_month_lag"] > 1:
            warnings.append(f"WLB freshness lag={monthly['wlb_month_lag']} months")
        if monthly["nps_month_lag"] is None or monthly["nps_month_lag"] > 3:
            warnings.append(f"NPS freshness lag={monthly['nps_month_lag']} months")
        if monthly["wlb_missing_months"]:
            warnings.append("WLB missing months: " + ",".join(monthly["wlb_missing_months"]))
        if monthly["nps_missing_months"]:
            warnings.append("NPS missing months: " + ",".join(monthly["nps_missing_months"]))
        if checks["nps_zero_workplace_count"]:
            warnings.append(f"NPS workplace count is zero in {checks['nps_zero_workplace_count']} rows")
        if coverage and coverage["wlb_pct"] is not None and coverage["wlb_pct"] < 90:
            warnings.append(f"WLB coverage is {coverage['wlb_pct']}%")
        if coverage and coverage["nps_pct"] is not None and coverage["nps_pct"] < 75:
            warnings.append(f"NPS coverage is {coverage['nps_pct']}%")

        return {
            "status": "error" if errors else "warning" if warnings else "ok",
            "integrity": integrity,
            "checks": checks,
            "monthly": monthly,
            "history": history,
            "coverage": coverage,
            "errors": errors,
            "warnings": warnings,
        }
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit employment data quality")
    parser.add_argument("--emp-db", default=str(DEFAULT_EMP_DB))
    parser.add_argument("--stock-db", default=str(DEFAULT_STOCK_DB))
    args = parser.parse_args()
    report = audit_employment_data(args.emp_db, args.stock_db)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 2 if report["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
