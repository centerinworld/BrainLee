"""
Unresolved item #4 (docs/claude_handoff_to_codex_20260912_final.md section 6): the
partial_tp_pct=0.3 MERGED-account (sector_focus+v2) walk-forward across the 6 standard
periods was not yet done -- only the continuous 2020-2026 run was measured for the merge.

Method: fresh per-period sector_focus(partial_tp_pct=0.3) runs (real re-simulation, no
gate issue) + v2 orders SLICED from the existing continuous run 22adc49a by period date
range (v2 cannot be re-executed fresh per-period -- price_integrity.assert_research_prices
hard-blocks it, see section 6 item 3). This is the same hybrid method already used and
labeled in scripts/walkforward_f09_sliced_sf_v2_20260912.py -- NOT a true full
re-simulation for the v2 leg, reported as such.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402
from backtest_strategies.sector import run_backtest_sector  # noqa: E402
from merged_simulator import MergeConfig, simulate_merged_account, orders_from_trades_json  # noqa: E402

PERIODS = [
    ("20.3~21.11", "2020-03-01", "2021-11-30"),
    ("21.12~22.10", "2021-12-01", "2022-10-31"),
    ("22.11~23.10", "2022-11-01", "2023-10-31"),
    ("23.11~24.12", "2023-11-01", "2024-12-31"),
    ("24.6~25.5", "2024-06-01", "2025-05-31"),
    ("25.6~26.3", "2025-06-01", "2026-03-31"),
]


def main():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    v2_raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id='22adc49a'").fetchone()[0]
    v2_all = orders_from_trades_json("v2", v2_raw)

    base_rets, partial_rets = [], []
    for label, start, end in PERIODS:
        base_id = run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9)
        partial_id = run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9, partial_tp_pct=0.3)
        base_raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (base_id,)).fetchone()[0]
        partial_raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (partial_id,)).fetchone()[0]
        v2_slice = [o for o in v2_all if start <= o.date <= end]

        cfg = MergeConfig(initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=20,
                           dynamic_tickets=True, tiebreak_mode="neutral_hash")
        base_orders = orders_from_trades_json("sector_focus", base_raw) + v2_slice
        partial_orders = orders_from_trades_json("sector_focus", partial_raw) + v2_slice
        base_ret = simulate_merged_account(base_orders, cfg)["summary"]["total_return_pct"]
        partial_ret = simulate_merged_account(partial_orders, cfg)["summary"]["total_return_pct"]
        base_rets.append(base_ret)
        partial_rets.append(partial_ret)
        print(f"{label}: baseline(+v2 slice)={base_ret:.2f}% partial_tp0.3(+v2 slice)={partial_ret:.2f}% "
              f"delta={partial_ret-base_ret:+.2f}pp", flush=True)

    avg_base = sum(base_rets) / len(base_rets)
    avg_partial = sum(partial_rets) / len(partial_rets)
    better = sum(1 for b, p in zip(base_rets, partial_rets) if p > b)
    print(f"\navg6 baseline(+v2 slice)={avg_base:.2f}% avg6 partial_tp0.3(+v2 slice)={avg_partial:.2f}% "
          f"better_periods={better}/{len(PERIODS)}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
