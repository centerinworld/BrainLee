#!/usr/bin/env python3
"""Backfill clean history for canonical Yahoo macro symbols."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yfinance as yf  # noqa: E402

from db_utils import connect_stock_db  # noqa: E402
from macro_data_quality import is_plausible_macro_close  # noqa: E402


DEFAULT_SYMBOLS = ("DX-Y.NYB", "2YY=F")
REPORT = ROOT / "research_outputs" / "postgres_cutover" / "macro_clean_backfill_latest.json"


def value(row, key: str) -> float:
    raw = row.get(key, 0)
    if hasattr(raw, "iloc"):
        raw = raw.iloc[0]
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--period", default="3y")
    parser.add_argument("--symbols", nargs="+", default=list(DEFAULT_SYMBOLS))
    args = parser.parse_args()

    conn = connect_stock_db()
    results: dict[str, dict] = {}
    try:
        for symbol in args.symbols:
            frame = yf.Ticker(symbol).history(period=args.period, interval="1d", auto_adjust=True)
            accepted = []
            rejected = 0
            for stamp, row in frame.iterrows():
                close = value(row, "Close")
                if not is_plausible_macro_close(symbol, close):
                    rejected += 1
                    continue
                accepted.append(
                    (
                        symbol,
                        stamp.strftime("%Y-%m-%d"),
                        value(row, "Open"),
                        value(row, "High"),
                        value(row, "Low"),
                        close,
                        value(row, "Volume"),
                        0.0,
                        0.0,
                    )
                )
            if accepted:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO price_history
                    (stock_code,date,open,high,low,close,volume,inst_net_buy,frn_net_buy)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    accepted,
                )
                conn.commit()
            results[symbol] = {
                "downloaded": len(frame),
                "accepted": len(accepted),
                "rejected": rejected,
                "min_date": accepted[0][1] if accepted else None,
                "max_date": accepted[-1][1] if accepted else None,
            }
    finally:
        conn.close()

    report = {
        "ok": all(item["accepted"] > 0 for item in results.values()),
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "period": args.period,
        "results": results,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
