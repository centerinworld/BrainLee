#!/usr/bin/env python3
"""Run the Strategy Center six-window suite under one immutable data contract.

By default this is a preflight only. `--run` executes only strategies that
accept `data_asof_ts`; unsupported engines are reported as blocked rather than
silently producing incomparable results. This script never selects a new suite
for the Strategy Center. Selection remains a separate review decision.
"""
from __future__ import annotations

import argparse
import inspect
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import backtest as bt
from db_compat import connect_primary_db

PERIODS = (
    ("2020-03-01", "2021-11-30", "20.3_21.11"),
    ("2021-12-01", "2022-10-31", "21.12_22.10"),
    ("2022-11-01", "2023-10-31", "22.11_23.10"),
    ("2023-11-01", "2024-12-31", "23.11_24.12"),
    ("2024-06-01", "2025-05-31", "24.06_25.05"),
    ("2025-06-01", "2026-03-31", "25.06_26.03"),
)

RUNNERS = {
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
    # Independent external-framework candidates. They remain blocked until their
    # implementations accept data_asof_ts; this makes the missing research
    # contract visible rather than silently excluding them from comparisons.
    "aqr_multifactor": "run_backtest_aqr_multifactor",
    "piotroski_value": "run_backtest_piotroski_value",
    "magic_formula": "run_backtest_magic_formula",
    "dual_momentum": "run_backtest_dual_momentum",
}

# These engines freeze statement revisions but security-master/share revisions are
# available only as effective intervals, not versioned snapshots.
POINT_IN_TIME_APPROX_RUNNERS = {"aqr_multifactor", "piotroski_value", "magic_formula", "dual_momentum"}


def preflight(selected: set[str]) -> dict[str, dict]:
    result = {}
    for strategy, name in RUNNERS.items():
        if selected and strategy not in selected:
            continue
        function = getattr(bt, name)
        accepted = inspect.signature(function).parameters
        supports_snapshot = "data_asof_ts" in accepted
        result[strategy] = {
            "runner": name,
            "supports_data_asof": supports_snapshot,
            "supports_asof_mktcap": "asof_mktcap" in accepted,
            "snapshot_contract": (
                "point_in_time_approx" if strategy in POINT_IN_TIME_APPROX_RUNNERS
                else "reconstructible" if supports_snapshot else "missing"
            ),
            "status": "eligible" if supports_snapshot else "blocked_snapshot_contract",
        }
    return result


def run_suite(snapshot: str, selected: set[str], output: Path) -> dict:
    results = {"snapshot": snapshot, "started_at": datetime.now().isoformat(timespec="seconds"),
               "strategies": preflight(selected)}
    for strategy, item in results["strategies"].items():
        if item["status"] != "eligible":
            continue
        function = getattr(bt, item["runner"])
        accepted = inspect.signature(function).parameters
        runs = []
        for start, end, label in PERIODS:
            run_id = function(
                start, end,
                run_name=f"deterministic-suite {strategy} {label}",
                data_asof_ts=snapshot,
            )
            conn = connect_primary_db()
            cur = conn.cursor()
            cur.execute(
                "SELECT total_return_pct,total_trades,max_drawdown_pct FROM backtest_runs WHERE run_id=%s",
                (run_id,),
            )
            row = cur.fetchone()
            conn.close()
            if not row or row[0] is None:
                raise RuntimeError(f"completed run has no result: {strategy} {label} {run_id}")
            runs.append({
                "label": label, "run_id": run_id,
                "total_return_pct": float(row[0]), "total_trades": int(row[1] or 0),
                "max_drawdown_pct": float(row[2]) if row[2] is not None else None,
            })
            output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        item["runs"] = runs
        returns = [row["total_return_pct"] for row in runs]
        drawdowns = [row["max_drawdown_pct"] for row in runs if row["max_drawdown_pct"] is not None]
        item["summary"] = {
            "mean_return_pct": round(sum(returns) / len(returns), 4),
            "positive_periods": sum(value > 0 for value in returns),
            "periods": len(returns),
            "mean_max_drawdown_pct": round(sum(drawdowns) / len(drawdowns), 4) if drawdowns else None,
            "worst_max_drawdown_pct": round(min(drawdowns), 4) if drawdowns else None,
        }
        item["status"] = "completed_unreviewed"
    results["completed_at"] = datetime.now().isoformat(timespec="seconds")
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", action="store_true", help="Execute eligible six-window suites")
    parser.add_argument("--strategies", default="", help="Comma-separated strategy keys")
    parser.add_argument("--snapshot", default=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    parser.add_argument("--output", default=str(ROOT / "research_outputs" / "deterministic_strategy_suite_latest.json"))
    args = parser.parse_args()
    selected = {value.strip() for value in args.strategies.split(",") if value.strip()}
    unknown = selected - set(RUNNERS)
    if unknown:
        raise SystemExit(f"Unknown strategies: {', '.join(sorted(unknown))}")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.run:
        result = run_suite(args.snapshot, selected, output)
    else:
        result = {"snapshot": args.snapshot, "strategies": preflight(selected)}
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
