"""Backward-compatible ETF router with a parity-gated direct-source cutover."""
from __future__ import annotations

import importlib.util
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict

from fastapi import HTTPException


LEGACY_PATH = Path(__file__).resolve().parent.parent / "routes_etf.py"
SPEC = importlib.util.spec_from_file_location("_routes_etf_legacy", LEGACY_PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError(f"Cannot load legacy ETF router: {LEGACY_PATH}")
legacy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy)

# Preserve all existing helpers and endpoints for current callers and tests.
for _name in dir(legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(legacy, _name))

router = legacy.router
ETF_LIST_PATH = "/api/etf-check/etf-list/{stock_code}"
router.routes[:] = [route for route in router.routes if getattr(route,"path",None) != ETF_LIST_PATH]


@router.get("/etf-list/{stock_code}")
def get_etf_list(stock_code: str) -> Dict[str, Any]:
    if not re.match(r"^\d{6}$",stock_code):
        raise HTTPException(status_code=400,detail="종목코드는 6자리 숫자여야 합니다")
    try:
        from etf_primary_service import direct_summary,source_mode
        if source_mode()=="krx_primary":
            return direct_summary(stock_code)
    except Exception:
        # Keep the user-facing endpoint available if the new source has a runtime fault.
        pass
    result=legacy.get_etf_list(stock_code)
    result["source"]="ETFCHECK_LEGACY"
    return result


@router.get("/source-control")
def get_etf_source_control() -> Dict[str, Any]:
    conn=legacy.get_db_connection()
    try:
        try:
            control=conn.execute("SELECT * FROM etf_source_control WHERE control_id=1").fetchone()
            recent=conn.execute(
                """
                SELECT base_date,legacy_date,new_coverage_ratio,membership_jaccard,
                       count_within_one_ratio,amount_correlation,amount_total_ratio,
                       amount_median_smape,passed,failures_json,audited_at
                FROM etf_source_parity_daily ORDER BY base_date DESC LIMIT 5
                """
            ).fetchall()
        except sqlite3.OperationalError:
            control,recent=None,[]
        return {
            "control":dict(control) if control else {
                "mode":"legacy_validation","required_pass_days":5,"consecutive_pass_days":0,
            },
            "recent_parity":[dict(row) for row in recent],
        }
    finally:
        conn.close()


__all__=[name for name in globals() if not name.startswith("_")]
