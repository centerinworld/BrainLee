"""API for contract-liability / advance-payment leading signals."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Query

from db_utils import connect_stock_db


router = APIRouter()
DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")


def _db() -> sqlite3.Connection:
    return connect_stock_db(timeout=30, row_factory=sqlite3.Row)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS contract_advance_signals (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            sector_large TEXT,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL,
            fs_div TEXT NOT NULL,
            contract_liabilities REAL,
            advances_received REAL,
            contract_assets REAL,
            gross_customer_funding REAL,
            net_customer_funding REAL,
            revenue REAL,
            gross_to_revenue_pct REAL,
            net_to_revenue_pct REAL,
            gross_qoq_pct REAL,
            gross_yoy_pct REAL,
            net_qoq_pct REAL,
            net_yoy_pct REAL,
            signal_score INTEGER DEFAULT 0,
            signal_label TEXT,
            quality_flag TEXT,
            source_accounts_json TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, fs_div)
        )
        """
    )
    conn.commit()


@router.get("/top")
def get_top_contract_advance_signals(
    min_score: int = Query(default=4, ge=0, le=10),
    limit: int = Query(default=80, ge=5, le=300),
    fs_div: str = Query(default="CFS", pattern="^(CFS|OFS|ALL)$"),
):
    conn = _db()
    try:
        _ensure_table(conn)
        fs_clause = "" if fs_div == "ALL" else "AND fs_div=?"
        params: list = [min_score]
        if fs_div != "ALL":
            params.append(fs_div)
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT *
            FROM contract_advance_signals
            WHERE signal_score >= ?
              AND quality_flag='ok'
              {fs_clause}
            ORDER BY fiscal_year DESC, fiscal_quarter DESC,
                     signal_score DESC, COALESCE(gross_to_revenue_pct,0) DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        latest_params: list = []
        latest_fs_clause = ""
        if fs_div != "ALL":
            latest_fs_clause = "WHERE fs_div=?"
            latest_params.append(fs_div)
        latest = conn.execute(
            f"SELECT MAX(fiscal_year || 'Q' || fiscal_quarter) FROM contract_advance_signals {latest_fs_clause}",
            latest_params,
        ).fetchone()[0]
        rebuilt_at = conn.execute("SELECT MAX(updated_at) FROM contract_advance_signals").fetchone()[0]
        return {
            "count": len(rows),
            "latest_period": latest,
            "last_rebuilt_at": rebuilt_at,
            "signals": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.get("/stock/{stock_code}")
def get_stock_contract_advance_signal(
    stock_code: str,
    fs_div: str = Query(default="CFS", pattern="^(CFS|OFS|ALL)$"),
):
    conn = _db()
    try:
        _ensure_table(conn)
        fs_clause = "" if fs_div == "ALL" else "AND fs_div=?"
        params: list = [stock_code]
        if fs_div != "ALL":
            params.append(fs_div)
        rows = conn.execute(
            f"""
            SELECT *
            FROM contract_advance_signals
            WHERE stock_code=?
              {fs_clause}
            ORDER BY fiscal_year ASC, fiscal_quarter ASC, fs_div ASC
            """,
            params,
        ).fetchall()
        latest = dict(rows[-1]) if rows else None
        return {
            "stock_code": stock_code,
            "count": len(rows),
            "latest": latest,
            "history": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.post("/rebuild")
def rebuild_contract_advance_signals(since_year: int = Query(default=2020, ge=2015, le=2030)):
    script = ROOT / "scripts" / "build_contract_advance_signals.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--since-year", str(since_year)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }
