"""
Re-run sector_focus solo (547.36%, cmb_14d19e97767b) and sector_focus+v2 (403.09%,
cmb_90d92c104f0a) under the F01 (sector.py annual-year disclosure leak) and F02
(merged_simulator.py same-day-close lookahead) fixes from
docs/claude_handoff_strategy_code_findings_20260912.md.

F03 (ticket_pct hard-cap override) and F04 (pyramid_add cash-reconciliation sign) are
confirmed real bugs (see tests/test_strategy_code_findings_20260912.py) but do NOT affect
these two specific baselines: both were registered with ticket_pct=None and
strategy_budget_weights={} (F03 inactive) and neither uses pyramid_add orders (F04
inactive, no pyramid trades in either ledger). So this script isolates exactly the two
fixes that CAN move these two numbers, and reports old vs new side by side -- it does not
re-register anything to the production run registry (avoids re-triggering the currently
volatile price-integrity gate mid-comparison; this is a research measurement, not an
official re-registration).

v2's component run (22adc49a) is NOT re-run: F01 only touched sector.py, v2.py's own
annual-data handling was not flagged by Codex's review and is unchanged. Reusing v2's
existing stored trades_json keeps this an isolated, single-variable-at-a-time comparison
per the handoff doc's own Section 4 instruction (item 4: "개별 수정의 손익 영향과 모두
합친 영향을 분리한다").
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
from merged_simulator import CandidateOrder, MergeConfig, simulate_merged_account, tiebreak_stability  # noqa: E402

START = "2020-01-01"
END = "2026-09-08"  # exact match to the registered e1245314/22adc49a end_date

OLD_RUN_IDS = {"sector_focus": "e1245314", "v2": "22adc49a"}


def _fetch_trades_json(run_id: str) -> str:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    return row[0] if row else "[]"


def _fetch_summary(run_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    row = conn.execute("SELECT total_return_pct, total_trades FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    return {"total_return_pct": row[0], "total_trades": row[1]} if row else {}


def _trades(raw) -> list[dict]:
    payload = json.loads(raw or "[]")
    return list(payload.get("trades") or []) if isinstance(payload, dict) else list(payload)


def _orders(strategy: str, raw) -> list[CandidateOrder]:
    orders = []
    for row in _trades(raw):
        if row.get("action"):
            side = str(row["action"]).lower()
            if side not in {"buy", "sell", "pyramid"}:
                continue
            orders.append(CandidateOrder(
                str(row.get("date") or ""), str(row.get("code") or row.get("stock_code") or ""),
                side, float(row.get("price") or 0), strategy, 1.0,
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
        orders.append(CandidateOrder(str(buy_date), code, "buy", float(entry), strategy, 1.0))
        orders.append(CandidateOrder(str(sell_date), code, "sell", float(exit_price), strategy, 1.0))
    return orders


def main():
    out = {"start": START, "end": END}

    print("[1/3] OLD baseline summary (as registered)", flush=True)
    out["old_sector_focus_solo"] = _fetch_summary(OLD_RUN_IDS["sector_focus"])
    out["old_v2_solo"] = _fetch_summary(OLD_RUN_IDS["v2"])
    print(f"  sector_focus (e1245314): {out['old_sector_focus_solo']}", flush=True)
    print(f"  v2 (22adc49a): {out['old_v2_solo']}", flush=True)

    print("\n[2/3] NEW sector_focus run under F01-fixed sector.py (v2 reused unchanged)", flush=True)
    new_sf_run_id = run_backtest_sector(START, END, per_stock=10_000_000, max_positions=9)
    out["new_sector_focus_run_id"] = new_sf_run_id
    out["new_sector_focus_solo"] = _fetch_summary(new_sf_run_id)
    print(f"  new sector_focus run_id={new_sf_run_id}: {out['new_sector_focus_solo']}", flush=True)

    print("\n[3/3] Re-merge under F02-fixed merged_simulator.py (research measurement only, not registered)", flush=True)
    v2_orders = _orders("v2", _fetch_trades_json(OLD_RUN_IDS["v2"]))
    sf_old_orders = _orders("sector_focus", _fetch_trades_json(OLD_RUN_IDS["sector_focus"]))
    sf_new_orders = _orders("sector_focus", _fetch_trades_json(new_sf_run_id))

    cfg = MergeConfig(initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=20,
                       dynamic_tickets=True, strategy_budget_weights={}, tiebreak_mode="neutral_hash")

    def merged(orders, label):
        result = simulate_merged_account(orders, cfg)
        stability = tiebreak_stability(orders, cfg, trials=8)
        ret = result["summary"]["total_return_pct"]
        print(f"  {label}: return={ret:.2f}% trades={result['summary']['completed_trades']} "
              f"tiebreak_mean={stability['mean_return_pct']:.2f}% tiebreak_pctile={stability['base_percentile']}",
              flush=True)
        return {"return_pct": ret, "trades": result["summary"]["completed_trades"],
                "max_drawdown_pct": result["summary"]["max_drawdown_pct"],
                "tiebreak_stability": stability}

    # (a) sector_focus-solo MERGED through the fixed engine, OLD (pre-F01) sector_focus trades
    #     -> isolates F02's effect alone (same trades, only the merge engine changed)
    out["merge_sf_old_through_fixed_engine"] = merged(sf_old_orders, "sector_focus(OLD trades) solo, F02-fixed engine")
    # (b) sector_focus-solo MERGED through the fixed engine, NEW (post-F01) sector_focus trades
    #     -> adds F01's effect on top of (a)
    out["merge_sf_new_through_fixed_engine"] = merged(sf_new_orders, "sector_focus(NEW trades) solo, F02-fixed engine")
    # (c) sector_focus(OLD)+v2 through fixed engine -> isolates F02 alone on the 403.09% combo
    out["merge_sf_old_v2_through_fixed_engine"] = merged(sf_old_orders + v2_orders, "sector_focus(OLD)+v2, F02-fixed engine")
    # (d) sector_focus(NEW)+v2 through fixed engine -> both F01+F02 applied
    out["merge_sf_new_v2_through_fixed_engine"] = merged(sf_new_orders + v2_orders, "sector_focus(NEW)+v2, F02-fixed engine")

    out_path = ROOT / "research_outputs" / "strategy_return_research_20260912" / "f01_f02_rerun_result.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    print(f"\nSaved: {out_path}", flush=True)


if __name__ == "__main__":
    main()
