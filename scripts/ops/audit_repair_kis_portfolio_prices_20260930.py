#!/usr/bin/env python3
"""Audit and repair Aug/Sep 2026 Korean operational OHLCV from KIS daily bars.

This is an operational Postgres repair. Korean individual stock prices must come
from KIS, not Yahoo fallback data.
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from collectors.kis_collector import KISCollector  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from kis_client import KISClient  # noqa: E402

START = "20260801"
END = "20260930"
START_ISO = "2026-08-01"
END_ISO = "2026-09-30"


def _rows_as_lists(rows):
    return [list(r) for r in rows]


def _num_equal(a, b, tol=0.0001) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def _load_target_codes(conn) -> list[str]:
    cur = conn.cursor()
    cur.execute(
        """
        WITH codes AS (
            SELECT stock_code FROM portfolio WHERE quantity > 0
            UNION ALL
            SELECT stock_code FROM watchlist
            UNION ALL
            SELECT stock_code FROM buy_candidates
        )
        SELECT DISTINCT stock_code
        FROM codes
        WHERE stock_code ~ '^[0-9]{6}$'
        ORDER BY stock_code
        """
    )
    return [r[0] for r in cur.fetchall()]


def _load_existing(conn, code: str) -> dict[str, dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT date::text, open, high, low, close, volume
        FROM price_history
        WHERE stock_code=%s
          AND date >= %s
          AND date <= %s
        ORDER BY date
        """,
        (code, START_ISO, END_ISO),
    )
    out = {}
    for r in cur.fetchall():
        out[str(r[0])[:10]] = {
            "open": r[1],
            "high": r[2],
            "low": r[3],
            "close": r[4],
            "volume": r[5],
        }
    return out


def _diffs(existing: dict[str, dict], kis_rows: list[dict]) -> list[dict]:
    diffs = []
    for row in kis_rows:
        d = row["date"]
        old = existing.get(d)
        if not old:
            diffs.append({"date": d, "field": "missing", "old": None, "new": row})
            continue
        for key in ("open", "high", "low", "close", "volume"):
            if not _num_equal(old.get(key), row.get(key)):
                diffs.append({"date": d, "field": key, "old": old.get(key), "new": row.get(key)})
    return diffs


def _upsert_prices(conn, code: str, rows: list[dict]) -> None:
    cur = conn.cursor()
    cur.execute("SELECT set_config('app.price_basis_checked','1',true)")
    for r in rows:
        trade_amount = float(r.get("close") or 0) * float(r.get("volume") or 0)
        cur.execute(
            """
            INSERT INTO price_history
                (stock_code,date,open,high,low,close,volume,
                 inst_net_buy,frn_net_buy,trade_amount)
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
                r.get("inst_net_buy", 0.0),
                r.get("frn_net_buy", 0.0),
                trade_amount,
            ),
        )
    if rows:
        latest = rows[-1]
        prev = rows[-2] if len(rows) >= 2 else None
        change_rate = (
            round((latest["close"] - prev["close"]) / prev["close"] * 100, 4)
            if prev and prev.get("close")
            else None
        )
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
                latest["close"] * latest["volume"],
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
                latest["close"] - (prev["close"] if prev else latest["close"]),
                change_rate,
                latest["volume"],
                "KIS_OPERATIONAL_AUG_SEP_REPAIR",
                json.dumps({"latest": latest, "prev": prev}, ensure_ascii=False),
            ),
        )


async def main() -> int:
    conn = connect_stock_db(row_factory=None)
    codes = _load_target_codes(conn)
    collector = KISCollector(KISClient())
    report = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "scope": {
            "start": START_ISO,
            "end": END_ISO,
            "codes": len(codes),
            "tables": ["portfolio", "watchlist", "buy_candidates"],
        },
        "items": [],
    }

    for i, code in enumerate(codes, 1):
        kis_rows = await collector.fetch_period_ohlcv(code, START, END)
        item = {"code": code, "kis_rows": len(kis_rows), "diff_count": 0, "sample_diffs": []}
        if not kis_rows:
            item["status"] = "no_kis_rows"
            report["items"].append(item)
            print(f"[{i}/{len(codes)}] {code}: no KIS rows")
            continue
        existing = _load_existing(conn, code)
        diffs = _diffs(existing, kis_rows)
        item["diff_count"] = len(diffs)
        item["sample_diffs"] = diffs[:10]
        if diffs:
            _upsert_prices(conn, code, kis_rows)
            conn.commit()
            item["status"] = "repaired"
        else:
            item["status"] = "ok"
        report["items"].append(item)
        print(f"[{i}/{len(codes)}] {code}: {item['status']} diffs={len(diffs)} rows={len(kis_rows)}")

    out_dir = ROOT / "run"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "kis_operational_price_audit_202608_202609.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(out_path), "codes": len(codes)}, ensure_ascii=False))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
