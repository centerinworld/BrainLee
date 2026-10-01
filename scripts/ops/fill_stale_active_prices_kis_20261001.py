#!/usr/bin/env python3
"""Fill remaining stale active price_history rows from KIS official daily bars."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from collectors.kis_collector import KISCollector  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from kis_client import KISClient  # noqa: E402


END = "20261001"
END_ISO = "2026-10-01"
RUN_DIR = ROOT / "run"


def _yyyymmdd(d: str) -> str:
    return d.replace("-", "")[:8]


def _next_day_yyyymmdd(d: str) -> str:
    return (date.fromisoformat(d[:10]) + timedelta(days=1)).strftime("%Y%m%d")


def _load_stale_codes(conn) -> list[dict]:
    rows = conn.execute(
        """
        WITH universe_cols AS (
          SELECT DISTINCT stock_code, stock_name
          FROM stock_universe
          WHERE stock_code ~ '^[0-9]{6}$'
            AND (stock_name IS NULL OR stock_name !~ '(우선|스팩|SPAC|리츠|ETF|ETN)')
        ),
        latest AS (
          SELECT stock_code, MAX(date::date) AS last_date
          FROM price_history
          WHERE stock_code ~ '^[0-9]{6}$' AND date ~ '^\\d{4}-\\d{2}-\\d{2}$'
          GROUP BY stock_code
        ),
        global_latest AS (
          SELECT MAX(date::date) AS max_date
          FROM price_history
          WHERE stock_code ~ '^[0-9]{6}$' AND date ~ '^\\d{4}-\\d{2}-\\d{2}$'
        )
        SELECT u.stock_code, u.stock_name, l.last_date::text
        FROM universe_cols u
        LEFT JOIN latest l USING (stock_code)
        CROSS JOIN global_latest g
        WHERE l.last_date IS NULL OR l.last_date < g.max_date - INTERVAL '10 days'
        ORDER BY l.last_date NULLS FIRST, u.stock_code
        """
    ).fetchall()
    return [{"stock_code": r[0], "stock_name": r[1], "last_date": str(r[2])[:10] if r[2] else None} for r in rows]


def _valid_row(r: dict) -> bool:
    try:
        o, h, l, c, v = (float(r[k]) for k in ("open", "high", "low", "close", "volume"))
    except Exception:
        return False
    return c > 0 and o > 0 and h > 0 and l > 0 and v >= 0 and h >= max(o, l, c) and l <= min(o, h, c)


def _upsert_rows(conn, code: str, rows: list[dict]) -> None:
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.price_basis_checked','1',true)")
    for r in rows:
        trade_amount = float(r["close"]) * float(r["volume"])
        cur.execute(
            """
            INSERT INTO price_history
              (stock_code,date,open,high,low,close,volume,trade_amount,
               inst_net_buy,frn_net_buy)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (stock_code,date) DO UPDATE SET
              open=EXCLUDED.open,
              high=EXCLUDED.high,
              low=EXCLUDED.low,
              close=EXCLUDED.close,
              volume=EXCLUDED.volume,
              trade_amount=EXCLUDED.trade_amount
            """,
            (
                code,
                r["date"],
                r["open"],
                r["high"],
                r["low"],
                r["close"],
                r["volume"],
                trade_amount,
                r.get("inst_net_buy", 0.0),
                r.get("frn_net_buy", 0.0),
            ),
        )
    if rows:
        latest = rows[-1]
        prev = rows[-2] if len(rows) > 1 else conn.execute(
            "SELECT close FROM price_history WHERE stock_code=%s AND date < %s ORDER BY date DESC LIMIT 1",
            (code, latest["date"]),
        ).fetchone()
        prev_close = float(prev["close"] if isinstance(prev, dict) else prev[0]) if prev else float(latest["close"])
        change_rate = round((float(latest["close"]) - prev_close) / prev_close * 100, 4) if prev_close else None
        cur.execute(
            """
            UPDATE stock_universe
            SET base_date=%s, close=%s, open=%s, high=%s, low=%s,
                change_rate=%s, volume=%s, trading_value=%s,
                updated_at=CURRENT_TIMESTAMP
            WHERE stock_code=%s
            """,
            (
                latest["date"],
                latest["close"],
                latest["open"],
                latest["high"],
                latest["low"],
                change_rate,
                latest["volume"],
                float(latest["close"]) * float(latest["volume"]),
                code,
            ),
        )
        cur.execute(
            """
            INSERT INTO kiwoom_realtime_quote
              (stock_code,last_price,change_price,change_rate,trade_volume,
               source_type,raw_json,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP)
            ON CONFLICT (stock_code) DO UPDATE SET
              last_price=EXCLUDED.last_price,
              change_price=EXCLUDED.change_price,
              change_rate=EXCLUDED.change_rate,
              trade_volume=EXCLUDED.trade_volume,
              source_type=EXCLUDED.source_type,
              raw_json=EXCLUDED.raw_json,
              updated_at=CURRENT_TIMESTAMP
            """,
            (
                code,
                latest["close"],
                float(latest["close"]) - prev_close,
                change_rate,
                latest["volume"],
                "KIS_STALE_ACTIVE_PRICE_FILL",
                json.dumps({"latest": latest, "run": "fill_stale_active_prices_kis_20261001"}, ensure_ascii=False),
            ),
        )


async def main() -> int:
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect_stock_db(timeout=120)
    codes = _load_stale_codes(conn)
    collector = KISCollector(KISClient())
    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "end": END_ISO,
        "target_count": len(codes),
        "items": [],
    }
    for idx, item in enumerate(codes, 1):
        code = item["stock_code"]
        start = _next_day_yyyymmdd(item["last_date"]) if item.get("last_date") else "20260101"
        rows = await collector.fetch_period_ohlcv(code, start, END)
        rows = [r for r in rows if _valid_row(r)]
        row_item = {**item, "start": start, "kis_rows": len(rows)}
        if rows:
            _upsert_rows(conn, code, rows)
            conn.commit()
            row_item["status"] = "filled"
            row_item["first_new_date"] = rows[0]["date"]
            row_item["last_new_date"] = rows[-1]["date"]
        else:
            row_item["status"] = "no_kis_rows"
        report["items"].append(row_item)
        print(f"[{idx}/{len(codes)}] {code} {item.get('stock_name')}: {row_item['status']} rows={len(rows)}")
    out = RUN_DIR / f"stale_active_price_kis_fill_20261001_{datetime.now():%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={out}")
    print(json.dumps({
        "target_count": report["target_count"],
        "filled": sum(1 for r in report["items"] if r["status"] == "filled"),
        "no_kis_rows": sum(1 for r in report["items"] if r["status"] == "no_kis_rows"),
    }, ensure_ascii=False))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
