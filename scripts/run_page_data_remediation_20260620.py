#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PY = ROOT / "venv" / "bin" / "python"
LOG_DIR = ROOT / "run" / "page_data_remediation_20260620"
DB = ROOT / "stock.db"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def now() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def log(message: str) -> None:
    print(f"[{now()}] {message}", flush=True)


def run_step(name: str, cmd: list[str], timeout: int | None = None) -> dict:
    log(f"START {name}: {' '.join(cmd)}")
    start = time.time()
    out_path = LOG_DIR / f"{name}.log"
    err_path = LOG_DIR / f"{name}.err"
    with out_path.open("ab") as out, err_path.open("ab") as err:
        proc = subprocess.run(cmd, cwd=ROOT, stdout=out, stderr=err, timeout=timeout)
    elapsed = round(time.time() - start, 1)
    status = {"name": name, "returncode": proc.returncode, "elapsed_sec": elapsed, "log": str(out_path), "err": str(err_path)}
    log(f"DONE {name}: rc={proc.returncode} elapsed={elapsed}s")
    return status


def run_kiwoom_universe(limit: int = 2693) -> dict:
    log(f"START kiwoom_stock_universe limit={limit}")
    from collectors.kiwoom_collector import KiwoomCollector

    start = time.time()
    kc = KiwoomCollector()
    health = kc.health_check()
    if not health.get("ok"):
        result = {"ok": False, "reason": "kiwoom_not_ready", "health": health}
    else:
        conn = sqlite3.connect(str(DB), timeout=30)
        try:
            rows = conn.execute(
                """
                SELECT stock_code
                FROM stock_universe
                WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                ORDER BY COALESCE(market_cap, 0) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            codes = [r[0] for r in rows]
        finally:
            conn.close()

        updated = 0
        failed = 0
        errors: list[dict] = []
        for i, code in enumerate(codes, 1):
            res = kc.fetch_stock_info(code)
            if res.get("ok"):
                updated += 1
            else:
                failed += 1
                if len(errors) < 20:
                    errors.append({"stock_code": code, "reason": res.get("reason")})
            if i == 1 or i % 100 == 0 or i == len(codes):
                log(f"kiwoom_stock_universe {i}/{len(codes)} updated={updated} failed={failed}")
            time.sleep(0.35)
        result = {"ok": True, "updated": updated, "failed": failed, "total": len(codes), "sample_errors": errors}
    elapsed = round(time.time() - start, 1)
    path = LOG_DIR / "kiwoom_stock_universe.json"
    path.write_text(json.dumps({"elapsed_sec": elapsed, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"DONE kiwoom_stock_universe: {result}")
    return {"name": "kiwoom_stock_universe", "returncode": 0 if result.get("ok") else 1, "elapsed_sec": elapsed, "log": str(path)}


def run_kiwoom_credit_foreign(limit: int = 2200) -> dict:
    log(f"START kiwoom_credit_foreign limit={limit}")
    from collectors.kiwoom_collector import KiwoomCollector

    start = time.time()
    kc = KiwoomCollector()
    health = kc.health_check()
    summary = {"health": health, "credit_saved": 0, "credit_errors": 0, "foreign_saved": 0, "foreign_errors": 0, "stocks": 0}
    if not health.get("ok"):
        summary["ok"] = False
        summary["reason"] = "kiwoom_not_ready"
    else:
        conn = sqlite3.connect(str(DB), timeout=30)
        try:
            rows = conn.execute(
                """
                SELECT stock_code
                FROM stock_universe
                WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
                  AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                ORDER BY COALESCE(market_cap, 0) DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            codes = [r[0] for r in rows]
        finally:
            conn.close()
        summary["stocks"] = len(codes)

        for i, code in enumerate(codes, 1):
            res = kc.fetch_credit_balance(code, qry_tp="1", max_pages=18)
            if res.get("ok"):
                summary["credit_saved"] += int(res.get("saved", 0) or 0)
            else:
                summary["credit_errors"] += 1
            if i == 1 or i % 100 == 0:
                log(f"credit {i}/{len(codes)} saved={summary['credit_saved']} errors={summary['credit_errors']}")
            time.sleep(0.35)

        for i, code in enumerate(codes, 1):
            res = kc.fetch_foreign_flow(code)
            if res.get("ok"):
                summary["foreign_saved"] += int(res.get("saved", 0) or 0)
            else:
                summary["foreign_errors"] += 1
            if i == 1 or i % 200 == 0:
                log(f"foreign {i}/{len(codes)} saved={summary['foreign_saved']} errors={summary['foreign_errors']}")
            time.sleep(0.25)
        summary["ok"] = True

    elapsed = round(time.time() - start, 1)
    path = LOG_DIR / "kiwoom_credit_foreign.json"
    path.write_text(json.dumps({"elapsed_sec": elapsed, "summary": summary}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"DONE kiwoom_credit_foreign: {summary}")
    return {"name": "kiwoom_credit_foreign", "returncode": 0 if summary.get("ok") else 1, "elapsed_sec": elapsed, "log": str(path)}


def run_market_radar_refresh() -> dict:
    log("START market_radar_refresh")
    start = time.time()
    result: dict = {"ok": True}
    try:
        from routes.market_radar import _do_refresh_cache

        asyncio.run(_do_refresh_cache())
    except Exception as exc:
        result = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    elapsed = round(time.time() - start, 1)
    path = LOG_DIR / "market_radar_refresh.json"
    path.write_text(json.dumps({"elapsed_sec": elapsed, "result": result}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"DONE market_radar_refresh: {result}")
    return {"name": "market_radar_refresh", "returncode": 0 if result.get("ok") else 1, "elapsed_sec": elapsed, "log": str(path)}


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    results.append(run_step("kis_ohlcv_recent", [str(PY), "collect_kis_ohlcv.py", "--days", "10"]))
    results.append(run_step("public_recent_backfill", [str(PY), "public_data_collector.py", "--backfill", "--start", "20260609", "--end", "20260620"]))
    results.append(run_kiwoom_universe())
    results.append(run_kiwoom_credit_foreign())
    results.append(run_market_radar_refresh())
    results.append(run_step("final_page_data_audit", [str(PY), "scripts/audit_all_page_data_quality.py"]))

    summary_path = LOG_DIR / "summary.json"
    summary_path.write_text(json.dumps({"generated_at": now(), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"SUMMARY {summary_path}")
    return 0 if all(r.get("returncode") == 0 for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
