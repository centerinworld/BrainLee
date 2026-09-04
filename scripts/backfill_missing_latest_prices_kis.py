#!/usr/bin/env python3
"""Fill only genuinely traded latest-day universe gaps from KIS current quotes."""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from kis_client import kis_client  # noqa: E402


def run(limit: int = 0) -> dict:
    today = date.today().isoformat()
    conn = connect_stock_db()
    try:
        rows = conn.execute(
            """SELECT u.stock_code,u.stock_name,MAX(p.date)
               FROM stock_universe u LEFT JOIN price_history p ON p.stock_code=u.stock_code
               WHERE NOT EXISTS(
                 SELECT 1 FROM price_history x WHERE x.stock_code=u.stock_code AND x.date=?
               )
               GROUP BY u.stock_code,u.stock_name ORDER BY MAX(p.date) DESC""",
            (today,),
        ).fetchall()
        if limit > 0:
            rows = rows[:limit]
        inserted = []
        no_trade = []
        unavailable = []
        for index, row in enumerate(rows, 1):
            code, name, last_date = row[0], row[1], row[2]
            quote = kis_client.get_current_price(code) or {}
            close = float(quote.get("close") or 0)
            volume = float(quote.get("volume") or 0)
            turnover = float(quote.get("trade_amount") or 0)
            if close <= 0:
                unavailable.append({"stock_code": code, "stock_name": name, "last_date": str(last_date)})
            elif volume <= 0 or turnover <= 0:
                no_trade.append({"stock_code": code, "stock_name": name, "last_date": str(last_date)})
            else:
                conn.execute(
                    """INSERT INTO price_history(stock_code,date,open,high,low,close,volume,trade_amount)
                       VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(stock_code,date) DO NOTHING""",
                    (
                        code, today, quote.get("open") or close, quote.get("high") or close,
                        quote.get("low") or close, close, volume, turnover,
                    ),
                )
                inserted.append(code)
                conn.commit()
            if index < len(rows):
                time.sleep(0.05)
        now = datetime.now().isoformat(timespec="seconds")
        for item in no_trade + unavailable:
            conn.execute(
                """INSERT INTO trading_restrictions(
                     stock_code,as_of,is_tradable,is_halted,is_management,warning_level,
                     is_short_overheat,source,raw_json,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(stock_code) DO UPDATE SET
                     as_of=excluded.as_of,is_tradable=0,source=excluded.source,
                     raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
                (
                    item["stock_code"], now, 0, 0, 0, "unverified", 0,
                    "KIS_NO_CURRENT_TRADE", json.dumps(item, ensure_ascii=False), now,
                ),
            )
        conn.commit()
        result = {
            "date": today, "candidates": len(rows), "inserted": len(inserted),
            "no_trade": no_trade, "unavailable": unavailable,
        }
        out = ROOT / "research_outputs" / "missing_latest_prices_kis_latest.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return result
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(run(args.limit), ensure_ascii=False, indent=2))
