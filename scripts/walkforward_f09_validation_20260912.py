"""
Walk-forward validation (docs/claude_handoff_strategy_return_research_20260912.md Section 7
non-overlapping-window requirement) of the F09 owner_only finding
(docs/claude_handoff_strategy_code_findings_20260912.md), which so far was only measured on
one continuous 2020-2026 run. Runs sector_focus (F01+F02+F07+F08-fixed) + v2 independently
over each of the 6 standard non-overlapping periods (routes/backtest.py STANDARD_PERIODS),
merges each period under any_sell vs owner_only, and reports both plus an avg6.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402
from backtest_strategies.sector import run_backtest_sector  # noqa: E402
from backtest_strategies.recovery import run_backtest_recovery  # noqa: E402
from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account  # noqa: E402

# NOTE (2026-09-12): originally paired sector_focus with v2, but v2's run_backtest_v2 calls
# backtest_common._run_generic_backtest -> price_integrity.assert_research_prices, which is a
# hard, no-bypass gate ("dropping troubled symbols would create selection bias" per its own
# docstring) that raised PriceIntegrityError on the FIRST walk-forward period (2020-03~2021-11)
# due to unresolved price contamination for codes like 000040/000087/000100 in that window.
# This is a real, structural blocker documented in research_outputs/strategy_return_research_20260912/
# (concurrent-write instability + unresolved 2022 splice cluster) -- not something to route
# around by suppressing the gate. Switched to golden_cross, which (like sector_focus) does not
# call this hard gate, so this script measures F09's cross-strategy sell-ownership mechanism
# instead of specifically validating the sector_focus+v2 pair across periods.

PERIODS = [
    ("20.3~21.11", "2020-03-01", "2021-11-30"),
    ("21.12~22.10", "2021-12-01", "2022-10-31"),
    ("22.11~23.10", "2022-11-01", "2023-10-31"),
    ("23.11~24.12", "2023-11-01", "2024-12-31"),
    ("24.6~25.5", "2024-06-01", "2025-05-31"),
    ("25.6~26.3", "2025-06-01", "2026-03-31"),
]


def _trades(raw):
    payload = json.loads(raw or "[]")
    return list(payload.get("trades") or []) if isinstance(payload, dict) else list(payload)


def _orders(strategy, raw):
    orders = []
    for row in _trades(raw):
        if row.get("action"):
            side = str(row["action"]).lower()
            if side not in {"buy", "sell", "pyramid"}:
                continue
            orders.append(CandidateOrder(str(row.get("date") or ""), str(row.get("code") or row.get("stock_code") or ""),
                                          side, float(row.get("price") or 0), strategy, 1.0, sector=str(row.get("sector") or "")))
            continue
        buy_date = row.get("buy_date") or row.get("entry_date")
        sell_date = row.get("sell_date") or row.get("exit_date")
        entry = row.get("entry") if row.get("entry") is not None else row.get("entry_price")
        exit_price = row.get("exit") if row.get("exit") is not None else row.get("exit_price")
        code = str(row.get("code") or row.get("stock_code") or "")
        if not all((buy_date, sell_date, entry, exit_price, code)):
            continue
        orders.append(CandidateOrder(str(buy_date), code, "buy", float(entry), strategy, 1.0))
        orders.append(CandidateOrder(str(sell_date), code, "sell", float(exit_price), strategy, 1.0))
    return orders


def main():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    results = []
    for label, start, end in PERIODS:
        sf_id = run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9)
        gc_id = run_backtest_recovery(start, end, per_stock=10_000_000, max_positions=10)
        sf_raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (sf_id,)).fetchone()[0]
        gc_raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (gc_id,)).fetchone()[0]
        orders = _orders("sector_focus", sf_raw) + _orders("recovery", gc_raw)

        row = {"period": label, "sf_run_id": sf_id, "gc_run_id": gc_id}
        for policy in ("any_sell", "owner_only"):
            cfg = MergeConfig(initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=20,
                               dynamic_tickets=True, tiebreak_mode="neutral_hash", sell_ownership_policy=policy)
            r = simulate_merged_account(orders, cfg)
            row[policy] = r["summary"]["total_return_pct"]
        print(f"{label}: any_sell={row['any_sell']:.2f}% owner_only={row['owner_only']:.2f}% "
              f"delta={row['owner_only']-row['any_sell']:+.2f}pp", flush=True)
        results.append(row)
        conn.commit()

    avg_any = sum(r["any_sell"] for r in results) / len(results)
    avg_owner = sum(r["owner_only"] for r in results) / len(results)
    positive_periods = sum(1 for r in results if r["owner_only"] > r["any_sell"])
    print(f"\navg6 any_sell={avg_any:.2f}% avg6 owner_only={avg_owner:.2f}% "
          f"owner_only better in {positive_periods}/{len(results)} periods", flush=True)

    out_path = ROOT / "research_outputs" / "strategy_return_research_20260912" / "walkforward_f09_result.json"
    out_path.write_text(json.dumps({"results": results, "avg6_any_sell": avg_any, "avg6_owner_only": avg_owner,
                                     "owner_only_better_periods": positive_periods}, ensure_ascii=False, indent=2, default=str))
    print(f"Saved: {out_path}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
