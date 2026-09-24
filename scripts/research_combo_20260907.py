#!/usr/bin/env python3
"""Untried strategy-combo research using freshly re-run (bug-fixed) component trades.

Loads component trades from research_outputs/combo_research_component_trades_20260907.json
(sector_focus / v2 / aqr_multifactor, run fresh on 2026-09-07 after this session's
equity-curve, position-limit, and CFS/OFS determinism fixes), builds merged-account
orders the same way scripts/research_core_strategy_portfolio.py does, and evaluates
each combo with train(first 3 periods)/validation(last 3 periods) split plus
merged_simulator.tiebreak_stability() so a result can't be reported as a single lucky
tie-break path.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account, tiebreak_stability

PERIODS = ("20.3_21.11", "21.12_22.10", "22.11_23.10", "23.11_24.12", "24.06_25.05", "25.06_26.03")

with open(ROOT / "research_outputs" / "combo_research_component_trades_20260907.json") as f:
    DATA = json.load(f)["results"]


def _trades(raw) -> list[dict]:
    payload = json.loads(raw or "[]")
    return list(payload.get("trades") or []) if isinstance(payload, dict) else list(payload)


def _orders(strategy: str, raw, weight: float) -> list[CandidateOrder]:
    if weight <= 0:
        return []
    orders = []
    for row in _trades(raw):
        if row.get("action"):
            side = str(row["action"]).lower()
            if side not in {"buy", "sell", "pyramid"}:
                continue
            orders.append(CandidateOrder(
                str(row.get("date") or ""),
                str(row.get("code") or row.get("stock_code") or ""),
                side, float(row.get("price") or 0), strategy,
                weight * 1000 + float(row.get("surge_score") or row.get("score") or 0),
                sector=str(row.get("sector") or ""),
            ))
            continue
        buy_date = row.get("buy_date") or row.get("entry_date")
        sell_date = row.get("sell_date") or row.get("exit_date")
        entry = row.get("entry") if row.get("entry") is not None else row.get("entry_price")
        exit_price = row.get("exit") if row.get("exit") is not None else row.get("exit_price")
        code = str(row.get("code") or row.get("stock_code") or "")
        if not all((buy_date, sell_date, entry, exit_price, code)):
            continue
        priority = weight * 1000
        orders.append(CandidateOrder(str(buy_date), code, "buy", float(entry), strategy, priority))
        orders.append(CandidateOrder(str(sell_date), code, "sell", float(exit_price), strategy, priority))
    return orders


def _simulate(period: str, weights: dict[str, float]) -> dict:
    orders = []
    for strategy, weight in weights.items():
        raw = DATA[strategy][period]["trades_json"]
        orders.extend(_orders(strategy, raw, weight))
    config = MergeConfig(
        initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=10,
        dynamic_tickets=True,
        strategy_budget_weights={k: v for k, v in weights.items() if v > 0},
        tiebreak_mode="neutral_hash",
    )
    summary = simulate_merged_account(orders, config)["summary"]
    return {
        "period": period,
        "return_pct": round(float(summary["total_return_pct"]), 2),
        "mdd_pct": round(float(summary.get("max_drawdown_pct") or 0), 2),
    }


def _aggregate(rows: list[dict]) -> dict:
    returns = [row["return_pct"] for row in rows]
    return {
        "average_return_pct": round(sum(returns) / len(returns), 2),
        "positive_periods": sum(v > 0 for v in returns),
        "worst_period_return_pct": round(min(returns), 2),
        "worst_mdd_pct": round(min(row["mdd_pct"] for row in rows), 2),
    }


PROFILES = {
    "solo_sector_focus": {"sector_focus": 1.0, "v2": 0.0, "aqr_multifactor": 0.0},
    "solo_v2": {"sector_focus": 0.0, "v2": 1.0, "aqr_multifactor": 0.0},
    "solo_aqr": {"sector_focus": 0.0, "v2": 0.0, "aqr_multifactor": 1.0},
    "sector_v2_5050": {"sector_focus": .5, "v2": .5, "aqr_multifactor": 0.0},
    "sector_aqr_5050": {"sector_focus": .5, "v2": 0.0, "aqr_multifactor": .5},
    "v2_aqr_5050": {"sector_focus": 0.0, "v2": .5, "aqr_multifactor": .5},
    "sector_aqr_6040": {"sector_focus": .6, "v2": 0.0, "aqr_multifactor": .4},
    "sector_aqr_4060": {"sector_focus": .4, "v2": 0.0, "aqr_multifactor": .6},
    "three_way_equal": {"sector_focus": 1/3, "v2": 1/3, "aqr_multifactor": 1/3},
    "three_way_sector_heavy": {"sector_focus": .5, "v2": .25, "aqr_multifactor": .25},
}


def main() -> None:
    results = []
    for name, weights in PROFILES.items():
        rows = [_simulate(period, weights) for period in PERIODS]
        train, validation = _aggregate(rows[:3]), _aggregate(rows[3:])
        score = round(
            train["average_return_pct"] + .35 * train["worst_period_return_pct"]
            + .15 * train["worst_mdd_pct"], 4
        )
        results.append({
            "profile": name, "weights": weights, "selection_score": score,
            "train": train, "validation": validation, "periods": rows,
        })
    results.sort(key=lambda r: r["selection_score"], reverse=True)

    print(f"{'profile':<24} {'train_avg':>10} {'train_worst':>12} {'val_avg':>9} {'val_pos':>8} {'val_worst':>10}")
    for r in results:
        print(f"{r['profile']:<24} {r['train']['average_return_pct']:>10.2f} "
              f"{r['train']['worst_period_return_pct']:>12.2f} "
              f"{r['validation']['average_return_pct']:>9.2f} "
              f"{r['validation']['positive_periods']:>7}/3 "
              f"{r['validation']['worst_period_return_pct']:>10.2f}")

    print()
    print("Per-period tiebreak_stability (8 trials each) for ALL profiles:")
    stability_report = {}
    for row in results:
        name, weights = row["profile"], row["weights"]
        config = MergeConfig(
            initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=10,
            dynamic_tickets=True,
            strategy_budget_weights={k: v for k, v in weights.items() if v > 0},
            tiebreak_mode="neutral_hash",
        )
        per_period_stability = []
        for period in PERIODS:
            orders = []
            for strategy, weight in weights.items():
                if weight <= 0:
                    continue
                orders.extend(_orders(strategy, DATA[strategy][period]["trades_json"], weight))
            st = tiebreak_stability(orders, config, trials=8)
            st["period"] = period
            per_period_stability.append(st)
        avg_of_base = round(sum(s["base_return_pct"] for s in per_period_stability) / 6, 2)
        avg_of_mean = round(sum(s["mean_return_pct"] for s in per_period_stability) / 6, 2)
        n_flagged = sum(1 for s in per_period_stability if s["base_above_max"])
        stability_report[name] = {
            "per_period": per_period_stability,
            "avg6_of_registered_base": avg_of_base,
            "avg6_of_randomized_mean": avg_of_mean,
            "periods_flagged_path_luck": n_flagged,
        }
        print(f"{name}: avg6(registered)={avg_of_base} avg6(randomized mean)={avg_of_mean} "
              f"flagged_periods={n_flagged}/6")

    print()
    print("Re-ranked by robustness-adjusted (randomized-mean) avg6:")
    ranked = sorted(stability_report.items(), key=lambda kv: -kv[1]["avg6_of_randomized_mean"])
    for name, info in ranked:
        print(f"{name}: robust_avg6={info['avg6_of_randomized_mean']} "
              f"(headline={info['avg6_of_registered_base']}, luck_gap={round(info['avg6_of_registered_base']-info['avg6_of_randomized_mean'],2)}) "
              f"flagged={info['periods_flagged_path_luck']}/6")

    out = {"results": results, "stability_report": stability_report}
    with open(ROOT / "research_outputs" / "combo_research_20260907.json", "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
