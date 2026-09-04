#!/usr/bin/env python3
"""Cross-check audited price jumps against Naver Finance daily history."""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402

DB = ROOT / "stock.db"
OUT = ROOT / "research_outputs" / "naver_price_crosscheck_20260712.json"
ITEM_RE = re.compile(r'data="([^"]+)"')

DDL = """
CREATE TABLE IF NOT EXISTS external_price_verification (
  stock_code TEXT NOT NULL,
  event_date TEXT NOT NULL,
  external_source TEXT NOT NULL,
  external_previous_date TEXT,
  external_previous_close REAL,
  external_event_close REAL,
  external_price_ratio REAL,
  agreement_class TEXT NOT NULL,
  confidence REAL NOT NULL,
  evidence TEXT NOT NULL DEFAULT '',
  verified_at TEXT NOT NULL,
  PRIMARY KEY(stock_code,event_date,external_source)
);
CREATE INDEX IF NOT EXISTS idx_epv_agreement ON external_price_verification(agreement_class,confidence);
"""


def fetch_history(code: str) -> tuple[str, dict[str, float], str | None]:
    url = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=7000&requestType=0"
    try:
        response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        response.raise_for_status()
        rows = {}
        for raw in ITEM_RE.findall(response.text):
            fields = raw.split("|")
            if len(fields) >= 5 and len(fields[0]) == 8:
                try:
                    close = float(fields[4])
                    if close > 0:
                        rows[fields[0]] = close
                except ValueError:
                    pass
        return code, rows, None
    except Exception as exc:
        return code, {}, str(exc)


def close_match(a: float | None, b: float | None, tolerance: float = 0.15) -> bool:
    return bool(a and b and abs(a-b) / max(abs(b), 0.01) <= tolerance)


def run(
    conn: sqlite3.Connection,
    workers: int = 6,
    only_new: bool = False,
    codes: list[str] | None = None,
) -> dict:
    conn.row_factory = sqlite3.Row
    conn.executescript(DDL)
    new_filter = """AND NOT EXISTS (
      SELECT 1 FROM external_price_verification v
      WHERE v.stock_code=price_jump_audit.stock_code AND v.event_date=price_jump_audit.event_date
        AND v.external_source='naver_finance'
    )""" if only_new else ""
    code_filter = ""
    params: tuple[str, ...] = ()
    if codes:
        code_filter = " AND stock_code IN ({})".format(",".join("?" for _ in codes))
        params = tuple(codes)
    audits = conn.execute(
        f"""SELECT stock_code,event_date,previous_date,price_ratio,public_price_ratio,classification
            FROM price_jump_audit WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            {code_filter} {new_filter}""",
        params,
    ).fetchall()
    by_code: dict[str, list[sqlite3.Row]] = {}
    for row in audits:
        by_code.setdefault(row["stock_code"], []).append(row)
    histories: dict[str, dict[str, float]] = {}
    errors: dict[str, str] = {}
    started = time.time()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_history, code) for code in by_code]
        for future in as_completed(futures):
            code, rows, error = future.result()
            histories[code] = rows
            if error:
                errors[code] = error

    now = datetime.now().isoformat(timespec="seconds")
    records = []
    agreement_counts: dict[str, int] = {}
    for code, rows in by_code.items():
        history = histories.get(code, {})
        dates = sorted(history)
        for audit in rows:
            event_key = audit["event_date"].replace("-", "")
            previous_key = audit["previous_date"].replace("-", "") if audit["previous_date"] else None
            event_close = history.get(event_key)
            previous_close = history.get(previous_key) if previous_key else None
            previous_external_date = previous_key
            if event_close and not previous_close:
                candidates = [d for d in dates if d < event_key]
                if candidates:
                    previous_external_date = candidates[-1]
                    previous_close = history[previous_external_date]
            ratio = event_close / previous_close if event_close and previous_close else None
            internal_match = close_match(ratio, audit["price_ratio"])
            public_match = close_match(ratio, audit["public_price_ratio"])
            if not ratio:
                agreement, confidence = "external_missing", 0.0
            elif internal_match and public_match:
                agreement, confidence = "all_three_agree", 0.98
            elif internal_match:
                agreement, confidence = "naver_confirms_price_history", 0.9
            elif public_match:
                agreement, confidence = "naver_confirms_public_raw", 0.95
            else:
                agreement, confidence = "three_way_disagreement", 0.3
            agreement_counts[agreement] = agreement_counts.get(agreement, 0) + 1
            evidence = (
                f"internal={audit['price_ratio']:.6f}; "
                f"public={audit['public_price_ratio'] if audit['public_price_ratio'] is not None else 'NA'}; "
                f"naver={ratio if ratio is not None else 'NA'}"
            )
            records.append((code,audit["event_date"],"naver_finance",previous_external_date,
                            previous_close,event_close,ratio,agreement,confidence,evidence,now))
    conn.executemany(
        """INSERT INTO external_price_verification VALUES(?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(stock_code,event_date,external_source) DO UPDATE SET
             external_previous_date=excluded.external_previous_date,
             external_previous_close=excluded.external_previous_close,
             external_event_close=excluded.external_event_close,
             external_price_ratio=excluded.external_price_ratio,
             agreement_class=excluded.agreement_class,confidence=excluded.confidence,
             evidence=excluded.evidence,verified_at=excluded.verified_at""", records
    )
    # Two independent stored series plus Naver agreement decide the upgraded audit class.
    conn.execute(
        """UPDATE price_jump_audit SET
             classification='externally_confirmed_internal_corruption', return_usable=0,
             evidence=evidence||'; Naver confirms public raw series'
           WHERE EXISTS (SELECT 1 FROM external_price_verification v
                         WHERE v.stock_code=price_jump_audit.stock_code AND v.event_date=price_jump_audit.event_date
                           AND v.agreement_class='naver_confirms_public_raw' AND v.confidence>=0.9)"""
    )
    conn.execute(
        """UPDATE price_jump_audit SET
             classification='externally_confirmed_market_jump', return_usable=1,
             evidence=evidence||'; Naver confirms price_history jump'
           WHERE EXISTS (SELECT 1 FROM external_price_verification v
                         WHERE v.stock_code=price_jump_audit.stock_code AND v.event_date=price_jump_audit.event_date
                           AND v.agreement_class IN ('naver_confirms_price_history','all_three_agree') AND v.confidence>=0.9)
             AND classification NOT IN ('corporate_action_or_delisting_nearby')"""
    )
    conn.commit()
    result = {"stocks_requested": len(by_code), "events_checked": len(records),
              "request_errors": len(errors), "agreement": agreement_counts,
              "elapsed_seconds": round(time.time()-started, 1), "verified_at": now}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--only-new", action="store_true")
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--codes", default="", help="comma-separated stock codes")
    args = parser.parse_args()
    codes = [value.strip().zfill(6) for value in args.codes.split(",") if value.strip()]
    conn = connect_stock_db(timeout=60)
    try:
        print(json.dumps(run(
            conn,
            workers=max(1, min(args.workers, 8)),
            only_new=args.only_new,
            codes=codes or None,
        ), ensure_ascii=False, indent=2))
    finally:
        conn.close()
