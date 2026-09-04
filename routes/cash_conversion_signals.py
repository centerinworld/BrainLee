"""API for cash-conversion quality signals."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, Query


router = APIRouter()
DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cash_conversion_signals (
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            market TEXT,
            sector_large TEXT,
            fiscal_year INTEGER NOT NULL,
            fiscal_quarter INTEGER NOT NULL,
            fs_div TEXT NOT NULL,
            revenue REAL,
            operating_profit REAL,
            net_income REAL,
            operating_cf REAL,
            capex REAL,
            free_cf REAL,
            trade_receivable REAL,
            ocf_margin_pct REAL,
            fcf_margin_pct REAL,
            ocf_to_net_income_pct REAL,
            receivable_to_revenue_pct REAL,
            rolling4_revenue REAL,
            rolling4_operating_cf REAL,
            rolling4_free_cf REAL,
            rolling4_ocf_margin_pct REAL,
            rolling4_fcf_margin_pct REAL,
            rolling4_ocf_positive_quarters INTEGER,
            revenue_qoq_pct REAL,
            operating_cf_qoq_pct REAL,
            receivable_qoq_pct REAL,
            signal_type TEXT,
            signal_score INTEGER DEFAULT 0,
            risk_score INTEGER DEFAULT 0,
            signal_label TEXT,
            quality_flag TEXT DEFAULT 'ok',
            source_json TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (stock_code, fiscal_year, fiscal_quarter, fs_div)
        )
    """)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(cash_conversion_signals)").fetchall()}
    for col, typ in {
        "rolling4_revenue": "REAL",
        "rolling4_operating_cf": "REAL",
        "rolling4_free_cf": "REAL",
        "rolling4_ocf_margin_pct": "REAL",
        "rolling4_fcf_margin_pct": "REAL",
        "rolling4_ocf_positive_quarters": "INTEGER",
    }.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE cash_conversion_signals ADD COLUMN {col} {typ}")
    conn.commit()


@router.get("/top")
def get_top_cash_conversion_signals(
    mode: str = Query(default="all", pattern="^(all|good|risk)$"),
    min_score: int = Query(default=4, ge=0, le=10),
    limit: int = Query(default=100, ge=5, le=300),
    fs_div: str = Query(default="CFS", pattern="^(CFS|OFS|ALL)$"),
):
    conn = _db()
    try:
        _ensure_table(conn)
        fs_clause = "" if fs_div == "ALL" else "AND fs_div=?"
        metric_clause = {
            "good": "AND signal_score >= ?",
            "risk": "AND risk_score >= ?",
            "all": "AND (signal_score >= ? OR risk_score >= ?)",
        }[mode]
        params: list = [min_score, min_score] if mode == "all" else [min_score]
        if fs_div != "ALL":
            params.append(fs_div)
        params.append(limit)
        rows = conn.execute(
            f"""
            SELECT *
            FROM cash_conversion_signals
            WHERE 1=1
              {metric_clause}
              {fs_clause}
            ORDER BY fiscal_year DESC, fiscal_quarter DESC,
                     CASE WHEN COALESCE(signal_score,0) >= COALESCE(risk_score,0)
                          THEN COALESCE(signal_score,0) ELSE COALESCE(risk_score,0) END DESC,
                     COALESCE(ocf_margin_pct,0) DESC
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
            f"SELECT MAX(fiscal_year || 'Q' || fiscal_quarter) FROM cash_conversion_signals {latest_fs_clause}",
            latest_params,
        ).fetchone()[0]
        # 2026-09 신규: 배치가 실제로 마지막 실행된 시각(신선도) 노출 —
        # 이 파이프라인이 상류 잡 실패로 39일간 조용히 정체된 적이 있어
        # 프론트에서 오래된 데이터를 최신처럼 보여주는 걸 방지.
        rebuilt_at = conn.execute("SELECT MAX(updated_at) FROM cash_conversion_signals").fetchone()[0]
        return {"count": len(rows), "latest_period": latest, "last_rebuilt_at": rebuilt_at, "signals": [dict(r) for r in rows]}
    finally:
        conn.close()


@router.get("/stock/{stock_code}")
def get_stock_cash_conversion_signal(
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
            FROM cash_conversion_signals
            WHERE stock_code=?
              {fs_clause}
            ORDER BY fiscal_year ASC, fiscal_quarter ASC, fs_div ASC
            """,
            params,
        ).fetchall()
        return {
            "stock_code": stock_code,
            "count": len(rows),
            "latest": dict(rows[-1]) if rows else None,
            "history": [dict(r) for r in rows],
        }
    finally:
        conn.close()


@router.post("/rebuild")
def rebuild_cash_conversion_signals(since_year: int = Query(default=2020, ge=2015, le=2030)):
    script = ROOT / "scripts" / "build_cash_conversion_signals.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--since-year", str(since_year)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=900,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-2000:],
        "stderr": proc.stderr[-2000:],
    }
