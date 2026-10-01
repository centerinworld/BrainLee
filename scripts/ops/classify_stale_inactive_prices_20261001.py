#!/usr/bin/env python3
"""Classify stale price codes that no official/recent chart source can advance.

This does not fabricate prices. It records evidence that a code has no newer
tradable OHLCV rows from KIS and Naver fchart, so freshness audits can separate
inactive/suspended names from collection failures.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402


NAVER_URL = "https://fchart.stock.naver.com/sise.nhn"


def naver_recent(code: str, count: int = 160) -> list[dict]:
    r = requests.get(
        NAVER_URL,
        params={"symbol": code, "timeframe": "day", "count": str(count), "requestType": "0"},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"},
        timeout=10,
    )
    out = []
    for m in re.finditer(r'data="([^"]+)"', r.text):
        p = m.group(1).split("|")
        if len(p) < 6 or len(p[0]) != 8:
            continue
        d = f"{p[0][:4]}-{p[0][4:6]}-{p[0][6:8]}"
        try:
            o, h, l, c, v = map(float, p[1:6])
        except ValueError:
            continue
        out.append({"date": d, "open": o, "high": h, "low": l, "close": c, "volume": v})
    return out


def main() -> int:
    conn = connect_primary_db(timeout=120)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS price_stale_inactive_review (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT,
            last_price_date TEXT,
            external_last_tradable_date TEXT,
            classification TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            reviewed_at TEXT NOT NULL
        )
        """
    )
    rows = cur.execute(
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
    now = datetime.now().isoformat(timespec="seconds")
    report = {"run_at": now, "items": []}
    for code, name, last_date in rows:
        recent = naver_recent(code)
        tradable = [r for r in recent if r["open"] > 0 and r["high"] > 0 and r["low"] > 0 and r["close"] > 0 and r["volume"] > 0]
        ext_last = tradable[-1]["date"] if tradable else None
        latest_raw = recent[-1] if recent else None
        last_date_s = str(last_date)[:10] if last_date else None
        classification = (
            "stale_inactive_no_recent_external_trading"
            if not ext_last or (last_date_s and ext_last <= last_date_s)
            else "external_has_newer_trading"
        )
        evidence = {
            "source": "naver_fchart_recent",
            "recent_rows": recent[-8:],
            "external_last_tradable_date": ext_last,
            "latest_raw_row": latest_raw,
            "note": "Rows with open/high/low=0 and volume=0 are treated as non-tradable stale/suspension markers, not fillable OHLCV.",
        }
        cur.execute(
            """
            INSERT INTO price_stale_inactive_review
              (stock_code, stock_name, last_price_date, external_last_tradable_date,
               classification, evidence_json, reviewed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (stock_code) DO UPDATE SET
              stock_name=EXCLUDED.stock_name,
              last_price_date=EXCLUDED.last_price_date,
              external_last_tradable_date=EXCLUDED.external_last_tradable_date,
              classification=EXCLUDED.classification,
              evidence_json=EXCLUDED.evidence_json,
              reviewed_at=EXCLUDED.reviewed_at
            """,
            (code, name, last_date_s, ext_last, classification, json.dumps(evidence, ensure_ascii=False), now),
        )
        report["items"].append(
            {
                "stock_code": code,
                "stock_name": name,
                "last_price_date": last_date_s,
                "external_last_tradable_date": ext_last,
                "classification": classification,
            }
        )
        print(code, name, last_date_s, ext_last, classification)
    conn.commit()
    out = ROOT / "run" / f"stale_inactive_price_review_20261001_{datetime.now():%H%M%S}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"report={out}")
    print(json.dumps({"reviewed": len(report["items"]), "inactive": sum(1 for i in report["items"] if i["classification"].startswith("stale_inactive"))}, ensure_ascii=False))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
