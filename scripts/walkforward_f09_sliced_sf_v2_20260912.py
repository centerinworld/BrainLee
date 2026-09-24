"""
Approximate walk-forward re-check of F09 (owner_only vs any_sell) specifically for the
sector_focus+v2 PAIR that actually showed the +138.56pp effect in the continuous
2020-2026 run -- since run_backtest_v2 cannot be re-executed fresh per-period (blocked by
price_integrity.assert_research_prices, a hard no-bypass gate -- see
research_outputs/strategy_code_review_20260912/claude_f01_f09_findings_table.md), this
instead SLICES the already-computed, already-stored continuous orders (sector_focus run
32771286 = F01+F02+F07+F08-fixed, v2 run 22adc49a) by each of the 6 standard period date
ranges and merges each slice independently under both policies.

This is NOT a true re-simulation (each period starts fresh with 100M capital rather than
carrying over position/capital state from the previous period, and a position open across
a period boundary is truncated) -- it is a directional check on whether cross-strategy sell
conflicts between THIS SPECIFIC pair cluster in particular periods or are spread evenly,
using the exact trades that produced the original finding. Report this limitation
alongside any number from this script.
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
from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account  # noqa: E402

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
    sf_raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id='32771286'").fetchone()[0]
    v2_raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id='22adc49a'").fetchone()[0]
    sf_all = _orders("sector_focus", sf_raw)
    v2_all = _orders("v2", v2_raw)
    conn.close()

    results = []
    for label, start, end in PERIODS:
        period_orders = [o for o in (sf_all + v2_all) if start <= o.date <= end]
        row = {"period": label, "n_orders": len(period_orders)}
        for policy in ("any_sell", "owner_only"):
            cfg = MergeConfig(initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=20,
                               dynamic_tickets=True, tiebreak_mode="neutral_hash", sell_ownership_policy=policy)
            r = simulate_merged_account(period_orders, cfg)
            row[policy] = r["summary"]["total_return_pct"]
            row[f"{policy}_not_owner_rejections"] = sum(1 for e in r["events"] if e.get("reason") == "not_owner_strategy")
        print(f"{label}: orders={row['n_orders']} any_sell={row['any_sell']:.2f}% owner_only={row['owner_only']:.2f}% "
              f"delta={row['owner_only']-row['any_sell']:+.2f}pp not_owner_rejections={row['owner_only_not_owner_rejections']}", flush=True)
        results.append(row)

    avg_any = sum(r["any_sell"] for r in results) / len(results)
    avg_owner = sum(r["owner_only"] for r in results) / len(results)
    total_rejections = sum(r["owner_only_not_owner_rejections"] for r in results)
    print(f"\navg6 any_sell={avg_any:.2f}% avg6 owner_only={avg_owner:.2f}% "
          f"total not_owner_rejections across 6 sliced periods={total_rejections}", flush=True)

    out_path = ROOT / "research_outputs" / "strategy_return_research_20260912" / "walkforward_f09_sliced_sf_v2_result.json"
    out_path.write_text(json.dumps({"method": "sliced_existing_continuous_orders_not_true_resimulation",
                                     "results": results, "avg6_any_sell": avg_any, "avg6_owner_only": avg_owner},
                                    ensure_ascii=False, indent=2, default=str))
    print(f"Saved: {out_path}", flush=True)


if __name__ == "__main__":
    main()
