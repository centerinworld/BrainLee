#!/usr/bin/env python3
"""Audit and remove implausible macro rows from the operational database."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from macro_data_quality import PLAUSIBLE_CLOSE_RANGES, RETIRED_MACRO_SYMBOLS  # noqa: E402


REPORT = ROOT / "research_outputs" / "postgres_cutover" / "macro_contamination_repair_latest.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = connect_stock_db()
    findings: dict[str, dict] = {}
    try:
        for symbol in sorted(RETIRED_MACRO_SYMBOLS):
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM price_history WHERE stock_code=?",
                    (symbol,),
                ).fetchone()[0]
            )
            findings[symbol] = {"reason": "retired_symbol", "rows": count}
            if args.apply and count:
                conn.execute("DELETE FROM price_history WHERE stock_code=?", (symbol,))

        for symbol, (minimum, maximum) in sorted(PLAUSIBLE_CLOSE_RANGES.items()):
            rows = conn.execute(
                "SELECT date, close FROM price_history "
                "WHERE stock_code=? AND (close IS NULL OR close<? OR close>?) "
                "ORDER BY date",
                (symbol, minimum, maximum),
            ).fetchall()
            findings[symbol] = {
                "reason": "outside_plausible_range",
                "range": [minimum, maximum],
                "rows": len(rows),
                "samples": [{"date": str(row[0])[:10], "close": row[1]} for row in rows[:20]],
            }
            if args.apply and rows:
                conn.execute(
                    "DELETE FROM price_history "
                    "WHERE stock_code=? AND (close IS NULL OR close<? OR close>?)",
                    (symbol, minimum, maximum),
                )
        if args.apply:
            conn.commit()
        else:
            conn.rollback()
    finally:
        conn.close()

    affected_rows = sum(item["rows"] for item in findings.values())
    result = {
        "ok": args.apply or affected_rows == 0,
        "applied": args.apply,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "affected_rows": affected_rows,
        "findings": findings,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
