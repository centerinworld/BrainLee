#!/usr/bin/env python3
"""Repair kiwoom_realtime_quote numeric fields from saved raw_json.

Older snapshots stored Kiwoom numeric FID payloads in raw_json but parsed only
named keys, leaving last_price/change_rate/trade_volume as zero. This script
replays the same parser used by KiwoomCollector and updates rows that can be
recovered from raw_json.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.kiwoom_collector import KiwoomCollector  # noqa: E402
from db_utils import STOCK_DB_PATH  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows to scan. 0 means all.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    kc = KiwoomCollector()
    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")

    sql = """
        SELECT stock_code, raw_json
        FROM kiwoom_realtime_quote
        WHERE raw_json IS NOT NULL
          AND raw_json != ''
          AND (
              COALESCE(last_price, 0) = 0
              OR COALESCE(change_rate, 0) = 0
              OR COALESCE(trade_volume, 0) = 0
          )
        ORDER BY updated_at DESC
    """
    if args.limit and args.limit > 0:
        sql += " LIMIT ?"
        rows = conn.execute(sql, (args.limit,)).fetchall()
    else:
        rows = conn.execute(sql).fetchall()

    scanned = 0
    repaired = 0
    for row in rows:
        scanned += 1
        try:
            payload = json.loads(row["raw_json"])
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        fields = kc._extract_realtime_fields(payload)
        if not any(fields.get(k) for k in ("last_price", "change_rate", "trade_volume", "trade_strength", "bid1", "ask1")):
            continue
        repaired += 1
        if args.dry_run:
            continue
        conn.execute(
            """
            UPDATE kiwoom_realtime_quote
            SET last_price = CASE WHEN ? != 0 THEN ? ELSE last_price END,
                change_price = CASE WHEN ? != 0 THEN ? ELSE change_price END,
                change_rate = CASE WHEN ? != 0 THEN ? ELSE change_rate END,
                trade_volume = CASE WHEN ? != 0 THEN ? ELSE trade_volume END,
                trade_strength = CASE WHEN ? != 0 THEN ? ELSE trade_strength END,
                bid1 = CASE WHEN ? != 0 THEN ? ELSE bid1 END,
                ask1 = CASE WHEN ? != 0 THEN ? ELSE ask1 END,
                bid_qty1 = CASE WHEN ? != 0 THEN ? ELSE bid_qty1 END,
                ask_qty1 = CASE WHEN ? != 0 THEN ? ELSE ask_qty1 END
            WHERE stock_code = ?
            """,
            (
                fields["last_price"], fields["last_price"],
                fields["change_price"], fields["change_price"],
                fields["change_rate"], fields["change_rate"],
                fields["trade_volume"], fields["trade_volume"],
                fields["trade_strength"], fields["trade_strength"],
                fields["bid1"], fields["bid1"],
                fields["ask1"], fields["ask1"],
                fields["bid_qty1"], fields["bid_qty1"],
                fields["ask_qty1"], fields["ask_qty1"],
                row["stock_code"],
            ),
        )

    if not args.dry_run:
        conn.commit()
    conn.close()
    print({"scanned": scanned, "repairable": repaired, "dry_run": args.dry_run})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
