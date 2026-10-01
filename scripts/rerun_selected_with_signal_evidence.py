#!/usr/bin/env python3
"""다중 입력 증거 원장이 붙은 엔진으로 선택 전략 6구간을 재실행하고, 모든 감사를
통과한 suite만 selected_run_registry에 올린다(2026-09-28).

순서: 6구간 재실행 → 구간별 가격·상장구간 감사 → 구간별 데이터 가용시점(다중 입력)
판정 → suite 등록 → (전부 통과 시에만) 선택 교체 → 선택 전략 전체 가격/가용시점
감사 재실행. 기존 suite와 수익률 차이는 데이터/체결/신호 변경으로 분해해 남긴다.

    PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/rerun_selected_with_signal_evidence.py \
        --strategies contract_momentum,turnaround [--workers 3] [--no-select]
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest as bt  # noqa: E402
from backtest_common import SIGNAL_EVIDENCE_REQUIREMENTS, _normalize_entries  # noqa: E402
from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact, register_run_set, select_run  # noqa: E402
from scripts.rerun_selected_after_price_repair import FUNCTIONS, _price_integrity  # noqa: E402

OUT = ROOT / "research_outputs" / "selected_signal_evidence_rerun_latest.json"
SELECTED_BY = "claude_20260928_signal_evidence_rerun"


def _selected_specs(only: set[str]) -> dict[str, list[dict]]:
    conn = connect_stock_db(readonly=True)
    rows = conn.execute(
        """SELECT sr.strategy,sr.run_hash,m.period_label,m.run_hash,r.start_date,r.end_date,r.per_stock,
                  s.parameter_json,r.total_return_pct,r.trades_json
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
        (strategy, old_suite, label, old_hash, start, end, per_stock,
         parameters, old_ret, old_trades) = tuple(row)
        if strategy not in only or (strategy, label) in seen:
            continue
        seen.add((strategy, label))
        payload = json.loads(old_trades or "[]")
        grouped.setdefault(strategy, []).append({
            "old_suite": old_suite, "old_run_hash": old_hash, "label": label,
            "start": start, "end": end, "per_stock": float(per_stock or 0),
            "parameters": json.loads(parameters or "{}"),
            "old_return_pct": old_ret,
            "old_trades": payload.get("trades", []) if isinstance(payload, dict) else payload,
        })
    return grouped


def _trade_prices(trades: list) -> dict:
    out = {}
    for t in trades or []:
        if not isinstance(t, dict):
            continue
        code = t.get("stock_code") or t.get("code") or t.get("sc")
        entry = t.get("entry_date") or t.get("buy_date") or (
            t.get("entry") if isinstance(t.get("entry"), str) else None)
        if code and entry:
            price = t.get("entry_price")
            if price is None and not isinstance(t.get("entry"), str):
                price = t.get("entry")
            out.setdefault((str(code), str(entry)[:10]), price)
    return out


def _attribution(spec: dict, new_params: dict, new_trades: list, new_ret) -> dict:
    """수익률 차이를 데이터 변경 / 체결 변경 / 신호 변경으로 분해한다.

    - data_change: 소스 스냅샷 revision 지문이 다름(가격·재무 원천 갱신)
    - signal_change: 진입(종목, 진입일) 집합이 다름 — 신호 로직/입력이 바뀜
    - execution_change: 같은 진입인데 진입가가 다름 — 체결가(시가 원천·기업행위 보정 등) 변화
    """
    old_snap = (spec["parameters"].get("_source_snapshot") or {}).get("revision_fingerprint")
    new_snap = (new_params.get("_source_snapshot") or {}).get("revision_fingerprint")
    old_entries = set(_normalize_entries(spec["old_trades"]))
    new_entries = set(_normalize_entries(new_trades))
    old_px, new_px = _trade_prices(spec["old_trades"]), _trade_prices(new_trades)
    repriced = sorted(
        k for k in old_entries & new_entries
        if old_px.get(k) is not None and new_px.get(k) is not None
        and abs(float(old_px[k]) - float(new_px[k])) > 1e-6
    )
    old_code = spec["parameters"].get("_code_fingerprint") or {}
    new_code = new_params.get("_code_fingerprint") or {}
    return {
        "old_return_pct": spec["old_return_pct"], "new_return_pct": new_ret,
        "return_diff_pct": (None if spec["old_return_pct"] is None or new_ret is None
                            else round(float(new_ret) - float(spec["old_return_pct"]), 2)),
        "data_change": old_snap != new_snap,
        "signal_change": old_entries != new_entries,
        "execution_change": bool(repriced),
        "entries_old": len(old_entries), "entries_new": len(new_entries),
        "entries_added": len(new_entries - old_entries),
        "entries_removed": len(old_entries - new_entries),
        "entries_repriced": len(repriced),
        "code_files_changed": sorted(k for k in set(old_code) | set(new_code)
                                     if old_code.get(k) != new_code.get(k)),
    }


def _run_one(strategy: str, spec: dict, data_asof_ts: str) -> dict:
    function = getattr(bt, FUNCTIONS[strategy])
    accepted = set(inspect.signature(function).parameters)
    params = {
        key: value for key, value in spec["parameters"].items()
        if key in accepted and key not in {"start_date", "end_date", "start", "end", "run_id", "run_name"}
    }
    if "per_stock" in accepted and spec["per_stock"]:
        params["per_stock"] = spec["per_stock"]
    if "data_asof_ts" in accepted:
        params["data_asof_ts"] = data_asof_ts  # 6구간 공통 스냅샷 고정
    run_id = str(uuid.uuid4())[:8]
    run_name = f"signal-evidence {strategy} {spec['label']}"
    conn = connect_stock_db()
    conn.execute(
        """INSERT INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status)
           VALUES(?,?,?,?,?,?,'running') ON CONFLICT(run_id) DO NOTHING""",
        (run_id, run_name, strategy, spec["start"], spec["end"], spec["per_stock"]),
    )
    conn.commit()
    conn.close()
    function(spec["start"], spec["end"], run_name=run_name, run_id=run_id, **params)
    conn = connect_stock_db(readonly=True)
    row = conn.execute(
        """SELECT r.status,s.run_hash,r.total_return_pct,r.trades_json,s.parameter_json
           FROM backtest_runs r JOIN backtest_run_specs s ON s.run_id=r.run_id WHERE r.run_id=?""",
        (run_id,),
    ).fetchone()
    availability = None
    if row:
        art = conn.execute(
            "SELECT passed,details_json FROM run_verification_artifacts WHERE run_hash=? AND artifact_type='data_availability'",
            (str(row[1]),),
        ).fetchone()
        if art:
            availability = {"passed": bool(art[0]), **json.loads(art[1] or "{}")}
    conn.close()
    if not row or row[0] != "done":
        raise RuntimeError(f"run did not complete: {strategy} {spec['label']} {run_id}")
    run_hash = str(row[1])
    price_ok, price_details = _price_integrity(run_id, spec["end"], strategy=strategy)
    register_artifact(run_hash, "price_integrity", price_ok, {
        **price_details, "signal_evidence_rerun": True, "strategy": strategy, "period": spec["label"],
    })
    payload = json.loads(row[3] or "[]")
    trades = payload.get("trades", []) if isinstance(payload, dict) else payload
    new_params = json.loads(row[4] or "{}")
    return {
        "label": spec["label"], "run_id": run_id, "run_hash": run_hash,
        "price_integrity": price_ok,
        "price_contaminated_events": price_details.get("contaminated_events"),
        "data_availability": bool(availability and availability.get("passed")),
        "availability": {k: (availability or {}).get(k) for k in (
            "executed_entries", "evidence_rows", "missing_evidence", "available_after_signal",
            "decision_not_before_entry", "row_without_available_at", "statutory_estimate_rows",
            "pit_grade", "samples")},
        "attribution": _attribution(spec, new_params, trades, row[2]),
    }


def run(only: set[str], workers: int, do_select: bool) -> dict:
    data_asof_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    selected = _selected_specs(only)
    result = {"started_at": datetime.now().isoformat(timespec="seconds"),
              "data_asof_ts": data_asof_ts, "select": do_select, "strategies": {}}
    missing = sorted(only - set(selected))
    if missing:
        result["missing_selected_suite"] = missing
    for strategy, specs in selected.items():
        item = {"old_suite": specs[0]["old_suite"], "runs": [], "status": "running"}
        result["strategies"][strategy] = item
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as pool:
                futures = [pool.submit(_run_one, strategy, spec, data_asof_ts) for spec in specs]
                for future in as_completed(futures):
                    item["runs"].append(future.result())
            item["runs"].sort(key=lambda r: r["label"])
            if len(item["runs"]) != 6:
                raise RuntimeError(f"expected 6 periods, got {len(item['runs'])}")
            members = {r["label"]: r["run_hash"] for r in item["runs"]}
            suite = register_run_set(strategy, "strategy_center", members)
            item["new_suite"] = suite["suite_hash"]
            # 선택 교체 게이트 = 완료 기준인 공식 선택전략 가격·상장구간 감사와 같은 규칙
            # (보유창 오염 7% 이하 + 생존편향 0건). 구간별 엄격 판정은 참고로 남긴다.
            from scripts.audit_selected_strategy_price_integrity import audit as official_price_audit
            official = official_price_audit({strategy: suite["suite_hash"]})["strategies"][0]
            item["official_price_audit"] = {k: official.get(k) for k in (
                "holding_windows", "contaminated_windows", "price_jump_window_contamination_ratio",
                "survivorship_findings", "price_integrity_passed", "status")}
            item["strict_period_price_integrity"] = all(r["price_integrity"] for r in item["runs"])
            price_ok = bool(official.get("price_integrity_passed"))
            avail_ok = all(r["data_availability"] for r in item["runs"])
            item["gates"] = {"price_integrity_6of6": price_ok, "data_availability_6of6": avail_ok}
            if price_ok and avail_ok and do_select:
                chosen = select_run(
                    strategy, "strategy_center", suite["suite_hash"], selected_by=SELECTED_BY,
                    note="Rerun with multi-input signal evidence ledger; price+availability gates 6/6",
                )
                item.update({"status": "selected", "verification": chosen.get("status")})
            elif price_ok and avail_ok:
                item["status"] = "passed_not_selected"
            else:
                item["status"] = "gates_failed_old_suite_kept"
        except Exception as exc:  # noqa: BLE001
            item.update({"status": "failed", "error": str(exc)})
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    result["completed_at"] = datetime.now().isoformat(timespec="seconds")
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", default=",".join(sorted(SIGNAL_EVIDENCE_REQUIREMENTS)))
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--no-select", action="store_true")
    args = parser.parse_args()
    only = {v.strip() for v in args.strategies.split(",") if v.strip()}
    out = run(only, args.workers, not args.no_select)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "runs"}
                      for k, v in out["strategies"].items()}, ensure_ascii=False, indent=2))
