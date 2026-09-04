#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from sqlalchemy import bindparam, text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "postgres_cutover" / "delisted_price_backfill_latest.json"
sys.path.insert(0, str(ROOT))

from database import engine  # noqa: E402
from scripts.backfill_naver_ohlcv_2015_2018 import fetch  # noqa: E402


def _targets(limit: int = 0) -> list[dict]:
    query = text(
        """
        SELECT stock_code, stock_name, effective_from, effective_to
        FROM security_master_history
        WHERE effective_to IS NOT NULL
          AND effective_to >= '2019-01-01'
          AND is_tradable=1
          AND is_etf_etn=0
          AND security_type='주권'
          AND market IN ('KOSPI','KOSDAQ')
        ORDER BY stock_code
        """
    )
    with engine.connect() as conn:
        rows = [dict(row._mapping) for row in conn.execute(query)]
    return rows[:limit] if limit > 0 else rows


def _has_internal_price_jump(rows: list[tuple]) -> bool:
    closes = [float(row[5]) for row in sorted(rows, key=lambda item: item[1]) if float(row[5]) > 0]
    return any(
        current / previous > 1.45 or current / previous < 0.69
        for previous, current in zip(closes, closes[1:])
        if previous > 0
    )


def _chunks(values: list[dict], size: int = 1000):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def _persist(rows: list[tuple], allowed_codes: set[str]) -> tuple[int, int]:
    mappings = [
        {
            "stock_code": row[0], "date": row[1], "open": row[2], "high": row[3],
            "low": row[4], "close": row[5], "volume": row[6], "source_url": row[7],
            "fetched_at": row[8],
        }
        for row in rows
    ]
    stage_sql = text(
        """
        INSERT INTO naver_price_history_backfill
          (stock_code,date,open,high,low,close,volume,source_url,fetched_at)
        VALUES
          (:stock_code,:date,:open,:high,:low,:close,:volume,:source_url,:fetched_at)
        ON CONFLICT (stock_code,date) DO UPDATE SET
          open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
          close=EXCLUDED.close, volume=EXCLUDED.volume,
          source_url=EXCLUDED.source_url, fetched_at=EXCLUDED.fetched_at
        """
    )
    price_sql = text(
        """
        INSERT INTO price_history(stock_code,date,open,high,low,close,volume)
        VALUES (:stock_code,:date,:open,:high,:low,:close,:volume)
        ON CONFLICT (stock_code,date) DO NOTHING
        """
    )
    before = 0
    with engine.connect() as conn:
        before = int(conn.execute(text("SELECT COUNT(*) FROM price_history")).scalar() or 0)
    with engine.begin() as conn:
        for chunk in _chunks(mappings):
            conn.execute(stage_sql, chunk)
        accepted = [row for row in mappings if row["stock_code"] in allowed_codes]
        for chunk in _chunks(accepted):
            conn.execute(price_sql, chunk)
    with engine.connect() as conn:
        after = int(conn.execute(text("SELECT COUNT(*) FROM price_history")).scalar() or 0)
    return len(mappings), after - before


def _coverage(codes: list[str]) -> dict:
    if not codes:
        return {}
    query = text(
        """
        SELECT COUNT(*) AS rows, COUNT(DISTINCT stock_code) AS stocks,
               MIN(date) AS min_date, MAX(date) AS max_date
        FROM price_history
        WHERE stock_code IN :codes AND date >= '2019-01-01'
        """
    ).bindparams(bindparam("codes", expanding=True))
    with engine.connect() as conn:
        return dict(conn.execute(query, {"codes": codes}).one()._mapping)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    targets = _targets(args.limit)
    fetched_rows: list[tuple] = []
    results = []
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 6))) as pool:
        futures = {}
        for target in targets:
            start = max("20190101", str(target["effective_from"]).replace("-", ""))
            end = str(target["effective_to"]).replace("-", "")
            futures[pool.submit(fetch, target["stock_code"], start, end)] = target
        for future in as_completed(futures):
            target = futures[future]
            code, rows, error = future.result()
            jump = _has_internal_price_jump(rows) if rows else False
            results.append(
                {
                    "stock_code": code,
                    "stock_name": target["stock_name"],
                    "rows": len(rows),
                    "first_date": rows[0][1] if rows else None,
                    "last_date": rows[-1][1] if rows else None,
                    "internal_price_jump": jump,
                    "error": error,
                }
            )
            fetched_rows.extend(rows)

    successful_codes = {
        item["stock_code"] for item in results
        if item["rows"] > 0 and not item["error"]
    }
    clean_codes = {
        item["stock_code"] for item in results
        if item["stock_code"] in successful_codes and not item["internal_price_jump"]
    }
    staged = inserted = 0
    if args.apply and fetched_rows:
        staged, inserted = _persist(fetched_rows, successful_codes)
    coverage = _coverage(sorted(successful_codes)) if args.apply else {}
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "apply" if args.apply else "audit",
        "targets": len(targets),
        "successful_codes": sum(not item["error"] and item["rows"] > 0 for item in results),
        "empty_codes": sum(not item["error"] and item["rows"] == 0 for item in results),
        "error_codes": sum(bool(item["error"]) for item in results),
        "artifact_review_codes": sum(item["internal_price_jump"] for item in results),
        "clean_codes": len(clean_codes),
        "insert_eligible_codes": len(successful_codes),
        "fetched_rows": len(fetched_rows),
        "staged_rows_processed": staged,
        "inserted_missing_price_rows": inserted,
        "coverage": coverage,
        "results": sorted(results, key=lambda item: item["stock_code"]),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(OUT)
    print(json.dumps({key: value for key, value in payload.items() if key != "results"}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
