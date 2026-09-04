#!/usr/bin/env python3
"""2026-08-23: price_jump_audit 재빌드(2026-08-07 시점 스냅샷 -> 최신 가격 기준) 이후,
모든 등록 전략을 최신 데이터로 재실행해 검증 게이트를 다시 통과하는지 확인한다.

rerun_selected_after_price_repair.py는 selected_by='price_basis_repair'인 전략을
건너뛰므로(2026-08-14 배치 재작업 방지 목적), 이번엔 그 제한 없이 전체를 대상으로 한다.
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db
from run_registry import register_run_set, select_run
from scripts.rerun_selected_after_price_repair import _run_one, OUT


def _all_selected_specs(only: set[str] | None = None) -> dict[str, list[dict]]:
    conn = connect_stock_db(readonly=True)
    rows = conn.execute(
        """SELECT sr.strategy,sr.run_hash,sr.selected_by,m.period_label,r.start_date,r.end_date,r.per_stock,
                  s.parameter_json
           FROM selected_run_registry sr
           JOIN backtest_run_set_members m ON m.suite_hash=sr.run_hash
           JOIN backtest_run_specs s ON s.run_hash=m.run_hash
           JOIN backtest_runs r ON r.run_id=s.run_id
           WHERE sr.report_type='strategy_center' AND r.status='done'
           ORDER BY sr.strategy,m.period_label,s.created_at DESC"""
    ).fetchall()
    conn.close()
    grouped: dict[str, list[dict]] = {}
    seen = set()
    for row in rows:
        strategy, old_suite, selected_by, label, start, end, per_stock, parameters = tuple(row)
        if (only and strategy not in only) or (strategy, label) in seen:
            continue
        seen.add((strategy, label))
        grouped.setdefault(strategy, []).append({
            "old_suite": old_suite, "label": label, "start": start, "end": end,
            "per_stock": float(per_stock), "parameters": json.loads(parameters or "{}"),
        })
    return grouped


def run(only: set[str], workers: int = 4) -> dict:
    from datetime import datetime
    selected = _all_selected_specs(only)
    result = {"started_at": datetime.now().isoformat(timespec="seconds"), "strategies": {}}
    for strategy, specs in selected.items():
        item = {"old_suite": specs[0]["old_suite"], "runs": [], "status": "running"}
        result["strategies"][strategy] = item
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            if workers <= 1:
                for spec in specs:
                    item["runs"].append(_run_one(strategy, spec))
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [pool.submit(_run_one, strategy, spec) for spec in specs]
                    for future in as_completed(futures):
                        item["runs"].append(future.result())
            if len(item["runs"]) != 6:
                raise RuntimeError(f"expected 6 periods, got {len(item['runs'])}")
            members = {row["label"]: row["run_hash"] for row in item["runs"]}
            suite = register_run_set(strategy, "strategy_center", members)
            selected_suite = select_run(
                strategy, "strategy_center", suite["suite_hash"],
                selected_by="audit_rebuild_20260823",
                note="Recomputed after stale price_jump_audit rebuild (2026-08-07 -> current)",
            )
            item.update({"status": "selected", "new_suite": suite["suite_hash"],
                         "verification": selected_suite.get("verification")})
        except Exception as exc:
            item.update({"status": "failed", "error": str(exc)})
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["completed_at"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", default="")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    only = {v.strip() for v in args.strategies.split(",") if v.strip()}
    print(json.dumps(run(only, args.workers), ensure_ascii=False, indent=2))
