#!/usr/bin/env python3
"""Validate a small predeclared strategy portfolio on one shared cash account."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account
from strategy_governance import classify_strategy

STRATEGIES = ("sector_focus", "contract_momentum", "golden_cross")
PERIODS = (
    "20.3~21.11", "21.12~22.10", "22.11~23.10",
    "23.11~24.12", "24.6~25.5", "25.6~26.3",
)
# Profiles are declared before seeing the last three validation periods.
PROFILES = {
    "core_equal": {"sector_focus": .50, "contract_momentum": .50, "golden_cross": .00},
    "core_sector60": {"sector_focus": .60, "contract_momentum": .40, "golden_cross": .00},
    "core_contract60": {"sector_focus": .40, "contract_momentum": .60, "golden_cross": .00},
    "satellite10": {"sector_focus": .45, "contract_momentum": .45, "golden_cross": .10},
    "satellite20": {"sector_focus": .40, "contract_momentum": .40, "golden_cross": .20},
    "satellite30": {"sector_focus": .35, "contract_momentum": .35, "golden_cross": .30},
}


def _trades(raw: str | None) -> list[dict]:
    payload = json.loads(raw or "[]")
    return list(payload.get("trades") or []) if isinstance(payload, dict) else list(payload)


def _orders(strategy: str, raw: str | None, weight: float) -> list[CandidateOrder]:
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
                weight * 1000 + float(row.get("score") or row.get("surge_score") or 0),
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
        priority = weight * 1000 + float(row.get("score") or row.get("signal_score") or 0)
        orders.extend((
            CandidateOrder(str(buy_date), code, "buy", float(entry), strategy, priority),
            CandidateOrder(str(sell_date), code, "sell", float(exit_price), strategy, priority),
        ))
    return orders


def _selected_runs(conn: sqlite3.Connection) -> tuple[dict, list[dict]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT s.strategy,m.period_label,r.run_id,r.total_return_pct,r.trades_json,
               COALESCE(json_extract(rs.manifest_json,
                 '$.members."' || m.period_label || '".status'),'legacy') verification_status
        FROM selected_run_registry s
        JOIN backtest_run_sets rs ON rs.suite_hash=s.run_hash
        JOIN backtest_run_set_members m ON m.suite_hash=s.run_hash
        JOIN backtest_run_specs bs ON bs.run_hash=m.run_hash
        JOIN backtest_runs r ON r.run_id=bs.run_id
        WHERE s.report_type='strategy_center' AND s.strategy IN (?,?,?)
        """, STRATEGIES,
    ).fetchall()
    selected = {(row["strategy"], row["period_label"]): dict(row) for row in rows}
    governance = []
    for strategy in STRATEGIES:
        period_rows = {
            period: {
                "total_return_pct": selected[(strategy, period)]["total_return_pct"],
                "verification_status": selected[(strategy, period)]["verification_status"],
            }
            for period in PERIODS
        }
        governance.append({"strategy": strategy, **classify_strategy(period_rows)})
    return selected, governance


def _simulate(selected: dict, period: str, weights: dict[str, float]) -> dict:
    orders = []
    for strategy, weight in weights.items():
        orders.extend(_orders(strategy, selected[(strategy, period)]["trades_json"], weight))
    config = MergeConfig(
        initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=10,
        dynamic_tickets=True,
        strategy_budget_weights={key: value for key, value in weights.items() if value > 0},
        tiebreak_mode="neutral_hash",
    )
    summary = simulate_merged_account(orders, config)["summary"]
    return {
        "period": period,
        "return_pct": round(float(summary["total_return_pct"]), 2),
        "mdd_pct": round(float(summary["max_drawdown_pct"]), 2),
        "completed_trades": int(summary.get("completed_trades") or 0),
        "buy_rejections": int(summary.get("buy_rejections") or 0),
    }


def _aggregate(rows: list[dict]) -> dict:
    returns = [row["return_pct"] for row in rows]
    return {
        "average_return_pct": round(sum(returns) / len(returns), 2),
        "positive_periods": sum(value > 0 for value in returns),
        "worst_period_return_pct": round(min(returns), 2),
        "worst_mdd_pct": round(min(row["mdd_pct"] for row in rows), 2),
    }


def main() -> None:
    with sqlite3.connect(DB_PATH, timeout=60) as conn:
        selected, governance = _selected_runs(conn)
    results = []
    for name, weights in PROFILES.items():
        rows = [_simulate(selected, period, weights) for period in PERIODS]
        train, validation = _aggregate(rows[:3]), _aggregate(rows[3:])
        score = round(
            train["average_return_pct"] + .35 * train["worst_period_return_pct"]
            + .15 * train["worst_mdd_pct"], 4
        )
        results.append({
            "profile": name, "weights": weights, "selection_score": score,
            "train": train, "validation": validation, "periods": rows,
        })
    results.sort(key=lambda row: row["selection_score"], reverse=True)
    chosen = results[0]
    verdict = (
        "retain_for_paper_validation"
        if chosen["validation"]["positive_periods"] >= 2
        and chosen["validation"]["average_return_pct"] > 0 else "reject"
    )
    report = {
        "as_of": date.today().isoformat(), "auto_trading_allowed": False,
        "selection_contract": "first_3_periods_only; last_3_periods untouched validation",
        "strategy_governance": governance, "chosen_profile": chosen,
        "validation_verdict": verdict, "all_profiles": results,
    }
    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "core_strategy_portfolio_20260810.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Core strategy portfolio validation - 2026-08-10", "",
        f"- auto trading allowed: {report['auto_trading_allowed']}",
        f"- chosen profile: {chosen['profile']} ({json.dumps(chosen['weights'], ensure_ascii=False)})",
        f"- train: {chosen['train']}",
        f"- untouched validation: {chosen['validation']}",
        f"- verdict: {verdict}", "", "## All Predeclared Profiles",
    ]
    for row in results:
        lines.append(
            f"- {row['profile']}: score {row['selection_score']} / train {row['train']} / validation {row['validation']}"
        )
    (OUT_DIR / "core_strategy_portfolio_20260810.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print(json.dumps({"chosen": chosen, "verdict": verdict}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
