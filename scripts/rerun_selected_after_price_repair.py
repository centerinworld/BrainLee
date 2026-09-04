#!/usr/bin/env python3
"""Recompute selected strategy suites after canonical price repair."""
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
from db_utils import connect_stock_db  # noqa: E402
from run_registry import register_artifact, register_run_set, select_run  # noqa: E402
from scripts.audit_selected_strategy_price_integrity import holding_windows  # noqa: E402

OUT = ROOT / "research_outputs" / "selected_price_repair_rerun_latest.json"
FUNCTIONS = {
    "v_trend": "run_backtest_v1", "v1_value": "run_backtest_value",
    "v2": "run_backtest_v2", "v5": "run_backtest_v5", "v4": "run_backtest",
    "v10": "run_backtest_v10", "v11": "run_backtest_v11",
    "vbr": "run_backtest_hidden_rev", "v8": "run_backtest_v8",
    "v12": "run_backtest_v12", "regime_adaptive": "run_backtest_regime_adaptive",
    "composite": "run_backtest_composite", "golden_cross": "run_backtest_golden_cross",
    "high_profit_compound": "run_backtest_high_profit_compound",
    "sector_focus": "run_backtest_sector", "recovery": "run_backtest_recovery",
    "deep_recovery": "run_backtest_deep_recovery",
    "low_base_breakout": "run_backtest_low_base_breakout",
    "turnaround": "run_backtest_turnaround",
    "extreme_dd_volume": "run_backtest_extreme_dd_volume",
    "se_momentum": "run_backtest_se_momentum", "megatrend": "run_backtest_megatrend",
    "earnings_conviction": "run_backtest_earnings_conviction",
    "moonshot_turnaround": "run_backtest_moonshot_turnaround",
    "contract_momentum": "run_backtest_contract_momentum",
    "earnings_supply_discovery": "run_backtest_earnings_supply_discovery",
}


def _selected_specs(only: set[str]) -> dict[str, list[dict]]:
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
        if selected_by == "price_basis_repair" or (only and strategy not in only) or (strategy, label) in seen:
            continue
        seen.add((strategy, label))
        grouped.setdefault(strategy, []).append({
            "old_suite": old_suite, "label": label, "start": start, "end": end,
            "per_stock": float(per_stock), "parameters": json.loads(parameters or "{}"),
        })
    return grouped


# 2026-08-23: 진입가에 corporate_action_events.backward_price_factor를 실제로 곱해
# 수익률을 보정하는 전략만 여기 등록한다(backtest.py _corp_action_adjusted_entry 호출
# 확인된 전략만). 다른 전략은 계수를 계산해뒀어도 return 계산에 적용하지 않으므로
# 게이트를 그대로 유지해야 한다 — 잘못 완화하면 미보정 오염 수익률이 "검증됨"으로
# 통과해버리는 사고가 난다.
_STRATEGIES_WITH_CORP_ACTION_ADJUSTMENT = {"turnaround", "regime_adaptive", "composite"}


def _price_integrity(run_id: str, end_date: str, strategy: str | None = None) -> tuple[bool, dict]:
    conn = connect_stock_db(readonly=True)
    row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    payload = json.loads(row[0] or "[]") if row else []
    trades = payload.get("trades", []) if isinstance(payload, dict) else payload
    windows = holding_windows(trades, end_date)
    adjust_aware = strategy in _STRATEGIES_WITH_CORP_ACTION_ADJUSTMENT
    bad = []
    explained = []
    for code, start, end in windows:
        rows = conn.execute(
            """SELECT stock_code,event_date,classification FROM price_jump_audit
               WHERE stock_code=? AND event_date BETWEEN ? AND ? AND return_usable=0""",
            (code, start, end),
        ).fetchall()
        for item in rows:
            ev_code, ev_date = item[0], item[1]
            if adjust_aware:
                confirmed = conn.execute(
                    """SELECT 1 FROM corporate_action_events
                       WHERE stock_code=? AND event_date=? AND adjustment_status='factor_confirmed'
                         AND backward_price_factor IS NOT NULL LIMIT 1""",
                    (ev_code, ev_date),
                ).fetchone()
                if confirmed:
                    explained.append(tuple(item))
                    continue
            bad.append(tuple(item))
    conn.close()
    details = {
        "holding_windows": len(windows), "contaminated_events": [list(x) for x in bad],
        "explained_by_corp_action_adjustment": [list(x) for x in explained],
        "no_trade_evidence": not windows,
    }
    # No trades is a sample-size/performance issue, not evidence of corrupted
    # prices. Only an unusable event inside an actual holding window fails here.
    return not bad, details


def _run_one(strategy: str, spec: dict) -> dict:
    function = getattr(bt, FUNCTIONS[strategy])
    accepted = set(inspect.signature(function).parameters)
    params = {
        key: value for key, value in spec["parameters"].items()
        if key in accepted and key not in {"start_date", "end_date", "start", "end", "run_id", "run_name"}
    }
    if "per_stock" in accepted:
        params["per_stock"] = spec["per_stock"]
    run_id = str(uuid.uuid4())[:8]
    run_name = f"price-repair {strategy} {spec['label']}"
    conn = connect_stock_db()
    conn.execute(
        """INSERT INTO backtest_runs
             (run_id,name,strategy,start_date,end_date,per_stock,status)
           VALUES(?,?,?,?,?,?,'running') ON CONFLICT(run_id) DO NOTHING""",
        (run_id, run_name, strategy, spec["start"], spec["end"], spec["per_stock"]),
    )
    conn.commit()
    conn.close()
    function(spec["start"], spec["end"], run_name=run_name, run_id=run_id, **params)
    conn = connect_stock_db(readonly=True)
    row = conn.execute(
        """SELECT r.status,s.run_hash FROM backtest_runs r
           JOIN backtest_run_specs s ON s.run_id=r.run_id WHERE r.run_id=?""", (run_id,)
    ).fetchone()
    conn.close()
    if not row or row[0] != "done":
        raise RuntimeError(f"run did not complete: {strategy} {spec['label']} {run_id}")
    passed, details = _price_integrity(run_id, spec["end"], strategy=strategy)
    register_artifact(str(row[1]), "price_integrity", passed, {
        **details, "repair_rerun": True, "strategy": strategy, "period": spec["label"],
    })
    if not passed:
        raise RuntimeError(f"new run failed price integrity: {strategy} {spec['label']}")
    return {"label": spec["label"], "run_id": run_id, "run_hash": str(row[1]), **details}


def run(only: set[str], workers: int) -> dict:
    selected = _selected_specs(only)
    result = {"started_at": datetime.now().isoformat(timespec="seconds"), "strategies": {}}
    for strategy, specs in selected.items():
        item = {"old_suite": specs[0]["old_suite"], "runs": [], "status": "running"}
        result["strategies"][strategy] = item
        OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            with ThreadPoolExecutor(max_workers=max(1, min(workers, 6))) as pool:
                futures = [pool.submit(_run_one, strategy, spec) for spec in specs]
                for future in as_completed(futures):
                    item["runs"].append(future.result())
            if len(item["runs"]) != 6:
                raise RuntimeError(f"expected 6 periods, got {len(item['runs'])}")
            members = {row["label"]: row["run_hash"] for row in item["runs"]}
            suite = register_run_set(strategy, "strategy_center", members)
            selected_suite = select_run(
                strategy, "strategy_center", suite["suite_hash"],
                selected_by="price_basis_repair",
                note="Recomputed after homogeneous Naver price-basis repair",
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", default="")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    only = {value.strip() for value in args.strategies.split(",") if value.strip()}
    print(json.dumps(run(only, args.workers), ensure_ascii=False, indent=2))
