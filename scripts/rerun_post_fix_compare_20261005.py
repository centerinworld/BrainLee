#!/usr/bin/env python3
"""Stock_Strategy P0-8a: 코드·데이터 정정 후 전 전략 6구간 재실행 — **선택(select)하지 않고** 비교용으로만 남긴다.

- 기존 선택 suite의 구간·파라미터를 그대로 재사용(rerun_all_after_audit_rebuild와 같은 입력).
- 새 suite는 report_type='post_fix_20261005'로 등록해 화면 매트릭스(strategy_center)를 바꾸지 않는다.
- 결과: research_outputs/post_fix_20261005_rerun.json (전략·구간별 수익률/MDD/거래수), 이전 값은 matrix_pre_data_fix_20261004.json.
사용: python3 scripts/rerun_post_fix_compare_20261005.py --strategies v8,v12 --workers 2 [--repeat]
  --repeat: 같은 전략 1구간을 2회 돌려 결과 동일 여부 확인(P0-7 요구).
2026-10-06 수정(docs/Stock_Strategy.md §9 2026-10-06 검토):
  - 다시 돌려도 이전 결과를 지우지 않는다 — 완료된 결과는 `attempts`에 옮겨 보존하고 새 결과를 위에 쓴다.
  - 가격 무결성 게이트 실패 구간도 예외로 버리지 않고 수치와 `integrity_passed=false`를 함께 기록한다.
  - run 이름을 `post_fix_20261005 …`로 남겨 백테스트 실행 목록에서 다른 재실행과 구분한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_run_set  # noqa: E402
from scripts.rerun_all_after_audit_rebuild import _all_selected_specs  # noqa: E402
from scripts.rerun_selected_after_price_repair import FUNCTIONS, _price_integrity  # noqa: E402
from run_registry import register_artifact  # noqa: E402
import backtest as bt  # noqa: E402
import inspect  # noqa: E402
import uuid  # noqa: E402

OUT = ROOT / "research_outputs" / "post_fix_20261005_rerun.json"
REPORT_TYPE = "post_fix_20261005"


def _metrics(run_id: str) -> dict:
    conn = connect_stock_db(readonly=True)
    r = conn.execute(
        "SELECT total_return_pct,max_drawdown_pct,total_trades,win_rate FROM backtest_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    conn.close()
    return {"total_return_pct": r[0], "mdd": r[1], "trades": r[2], "win_rate": r[3]} if r else {}


def _run_one(strategy: str, spec: dict) -> dict:
    """rerun_selected_after_price_repair._run_one과 같은 실행이지만 ① 이름이 post_fix ② 게이트 실패를 예외 대신 기록."""
    function = getattr(bt, FUNCTIONS[strategy])
    accepted = set(inspect.signature(function).parameters)
    params = {k: v for k, v in spec["parameters"].items()
              if k in accepted and k not in {"start_date", "end_date", "start", "end", "run_id", "run_name"}}
    if "per_stock" in accepted:
        params["per_stock"] = spec["per_stock"]
    run_id = str(uuid.uuid4())[:8]
    run_name = f"{REPORT_TYPE} {strategy} {spec['label']}"
    conn = connect_stock_db()
    conn.execute("""INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status)
                    VALUES(?,?,?,?,?,?,'running') ON CONFLICT(run_id) DO NOTHING""",
                 (run_id, run_name, strategy, spec["start"], spec["end"], spec["per_stock"]))
    conn.commit()
    conn.close()
    function(spec["start"], spec["end"], run_name=run_name, run_id=run_id, **params)
    conn = connect_stock_db(readonly=True)
    row = conn.execute("""SELECT r.status,s.run_hash FROM backtest_runs r
                          JOIN backtest_run_specs s ON s.run_id=r.run_id WHERE r.run_id=?""", (run_id,)).fetchone()
    conn.close()
    if not row or row[0] != "done":
        raise RuntimeError(f"run did not complete: {strategy} {spec['label']} {run_id}")
    passed, details = _price_integrity(run_id, spec["end"], strategy=strategy)
    register_artifact(str(row[1]), "price_integrity", passed, {
        **details, "repair_rerun": True, "strategy": strategy, "period": spec["label"], "report_type": REPORT_TYPE})
    return {"label": spec["label"], "run_id": run_id, "run_hash": str(row[1]), "integrity_passed": bool(passed)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--repeat", action="store_true")
    a = ap.parse_args()
    only = {v.strip() for v in a.strategies.split(",") if v.strip()}
    specs = _all_selected_specs(only or None)
    result = json.loads(OUT.read_text()) if OUT.exists() else {"strategies": {}}
    result["started_at"] = result.get("started_at") or datetime.now().isoformat(timespec="seconds")
    for strategy, items in specs.items():
        prev = result["strategies"].get(strategy)
        attempts = (prev or {}).pop("attempts", []) if prev else []
        if prev and prev.get("runs"):
            attempts.append(prev)  # 이전 결과 보존(2026-10-06: 재실행이 v12 결과를 지운 사고 재발 방지)
        item = {"runs": {}, "status": "running", "attempts": attempts}
        result["strategies"][strategy] = item
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            with ThreadPoolExecutor(max_workers=max(1, a.workers)) as pool:
                futs = {pool.submit(_run_one, strategy, s): s for s in items}
                for f in as_completed(futs):
                    row = f.result()
                    item["runs"][row["label"]] = {"run_id": row["run_id"], "run_hash": row["run_hash"],
                                                  "integrity_passed": row["integrity_passed"], **_metrics(row["run_id"])}
            if len(item["runs"]) != 6:
                raise RuntimeError(f"expected 6 periods, got {len(item['runs'])}")
            try:
                suite = register_run_set(strategy, REPORT_TYPE, {k: v["run_hash"] for k, v in item["runs"].items()})
                item["suite_hash"] = suite.get("suite_hash")
            except Exception as exc:  # 스냅샷 지문이 실행 중 바뀌면 등록만 실패 — 수치는 유효
                item["suite_error"] = str(exc)
            item["status"] = "done" if all(r.get("integrity_passed") for r in item["runs"].values()) else "done_integrity_gate_failed"
        except Exception as exc:
            item.update({"status": "failed", "error": str(exc)})
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        if a.repeat and item["status"] == "done":
            label = sorted(item["runs"])[0]
            spec = next(s for s in items if s["label"] == label)
            again = _run_one(strategy, spec)
            m2 = _metrics(again["run_id"])
            m1 = {k: item["runs"][label][k] for k in ("total_return_pct", "mdd", "trades")}
            item["repeat_check"] = {"label": label, "first": m1, "second": {k: m2.get(k) for k in m1}, "same": all(m1[k] == m2.get(k) for k in m1)}
            OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["completed_at"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v.get("status") for k, v in result["strategies"].items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
