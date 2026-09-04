#!/usr/bin/env bash
set -euo pipefail

cd /Volumes/Realtek_NVME/stock_dashboard/runtime

PY="/Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3"
LOG="/Volumes/Realtek_NVME/stock_dashboard/runtime/logs/backfill_kiwoom_credit_foreign.screen.log"

"$PY" -u - <<'PY' >> "$LOG" 2>&1
import json
import sqlite3
import time
from datetime import datetime

from collectors.kiwoom_collector import KiwoomCollector
from db_utils import STOCK_DB_PATH


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def load_codes(limit: int = 2200) -> list[str]:
    conn = sqlite3.connect(str(STOCK_DB_PATH), timeout=30)
    try:
        rows = conn.execute(
            """
            SELECT stock_code
            FROM stock_universe
            WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND market_cap IS NOT NULL
            ORDER BY market_cap DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def print_json(prefix: str, payload: dict) -> None:
    print(prefix, json.dumps(payload, ensure_ascii=False), flush=True)


print(f"[{now()}] kiwoom credit/foreign backfill start", flush=True)
kc = KiwoomCollector()
health = kc.health_check()
print_json("KIWOOM_HEALTH", health)
if not health.get("ok"):
    raise SystemExit(f"Kiwoom is not ready: {health}")

codes = load_codes(2200)
print(f"[{now()}] loaded {len(codes)} stock codes", flush=True)

CREDIT_MAX_PAGES = 18
credit_saved = 0
credit_errors = 0
for i, code in enumerate(codes, start=1):
    result = kc.fetch_credit_balance(code, qry_tp="1", max_pages=CREDIT_MAX_PAGES)
    if result.get("ok"):
        credit_saved += int(result.get("saved", 0) or 0)
    else:
        credit_errors += 1
        print_json("CREDIT_ERROR", {"i": i, "stock_code": code, "result": result})

    if i == 1 or i % 10 == 0:
        print(
            f"[{now()}] credit progress {i}/{len(codes)} saved={credit_saved} errors={credit_errors}",
            flush=True,
        )
    time.sleep(0.45)

print_json(
    "CREDIT_RESULT",
    {"ok": True, "stocks": len(codes), "saved": credit_saved, "errors": credit_errors, "max_pages": CREDIT_MAX_PAGES},
)

foreign_saved = 0
foreign_errors = 0
for i, code in enumerate(codes, start=1):
    result = kc.fetch_foreign_flow(code)
    if result.get("ok"):
        foreign_saved += int(result.get("saved", 0) or 0)
    else:
        foreign_errors += 1
        print_json("FOREIGN_ERROR", {"i": i, "stock_code": code, "result": result})

    if i == 1 or i % 100 == 0:
        print(
            f"[{now()}] foreign progress {i}/{len(codes)} saved={foreign_saved} errors={foreign_errors}",
            flush=True,
        )
    time.sleep(0.35)

print_json("FOREIGN_RESULT", {"ok": True, "stocks": len(codes), "saved": foreign_saved, "errors": foreign_errors})
print(f"[{now()}] kiwoom credit/foreign backfill done", flush=True)
PY
