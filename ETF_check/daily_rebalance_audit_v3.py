"""Null-safe production entry point for the per-ETF rebalance audit."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import daily_rebalance_audit as core
import daily_rebalance_audit_v2 as compatible
from full_pdf_collector import DB_PATH, connect


_components = core._components


def null_safe_components(conn, day, ticker):
    rows = _components(conn, day, ticker)
    for values in rows.values():
        values["shares"] = values["shares"] if values["shares"] is not None else 0.0
        values["weight"] = values["weight"] if values["weight"] is not None else 0.0
    return rows


core._components = null_safe_components


def audit_day(conn, day):
    return compatible.audit_day(conn, day)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()
    conn = connect(Path(args.db))
    day = args.date or conn.execute(
        "SELECT MAX(base_date) FROM etf_pdf_full_snapshot"
    ).fetchone()[0]
    if not day:
        raise RuntimeError("No ETF PDF snapshot is available")
    print(json.dumps(audit_day(conn, day),ensure_ascii=False,indent=2))
    conn.close()


if __name__ == "__main__":
    main()
