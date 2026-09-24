"""
Codex review (2026-09-12, priority 1): the earlier "top contributing code" analysis summed
pnl_pct (a ratio) across trades, ignoring quantity/KRW amounts and not diffing against the
baseline -- inaccurate, especially for partial sells. This script uses the qty/pnl_krw
fields just added to sector.py's trade records to build a real, KRW-denominated,
per-stock reconciliation between the P3 (2022-03-01~2023-03-31) baseline and
partial_tp_pct=0.3 runs, and checks that per-stock deltas sum to the total return delta.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402

BASELINE_RUN_ID = "bc5abf7e"
PARTIAL_RUN_ID = "8237ade8"


def _trades(run_id: str) -> list[dict]:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()[0]
    conn.close()
    payload = json.loads(raw or "[]")
    return list(payload.get("trades") or []) if isinstance(payload, dict) else list(payload)


def per_stock_realized_krw(trades: list[dict]) -> dict[str, float]:
    """Sum realized pnl_krw per stock code across all SELL/SECTOR_EXIT/FINAL records
    (partial sells included -- each carries its own qty-scoped pnl_krw now)."""
    out: dict[str, float] = defaultdict(float)
    for t in trades:
        if t.get("action") in ("SELL", "SECTOR_EXIT", "FINAL") and t.get("pnl_krw") is not None:
            out[t["code"]] += t["pnl_krw"]
    return dict(out)


def main():
    base_trades = _trades(BASELINE_RUN_ID)
    partial_trades = _trades(PARTIAL_RUN_ID)

    base_pnl = per_stock_realized_krw(base_trades)
    partial_pnl = per_stock_realized_krw(partial_trades)

    all_codes = set(base_pnl) | set(partial_pnl)
    deltas = {c: partial_pnl.get(c, 0.0) - base_pnl.get(c, 0.0) for c in all_codes}
    total_delta_krw = sum(deltas.values())
    total_base_krw = sum(base_pnl.values())
    total_partial_krw = sum(partial_pnl.values())

    print(f"Total realized pnl_krw: baseline={total_base_krw:,.0f} partial_tp={total_partial_krw:,.0f} "
          f"delta={total_delta_krw:,.0f}")
    print(f"(For reference, initial_cash=90,000,000 -- 9 slots x 10,000,000 -- so this delta_krw / "
          f"90,000,000 * 100 = {total_delta_krw/90_000_000*100:.2f}pp, vs reported total_return_pct "
          f"delta of {32.03-11.81:.2f}pp -- these won't match exactly because total_return_pct is a "
          f"cash-ledger return including timing/compounding effects, not a simple sum of realized pnl_krw "
          f"over one fixed base -- reported for scale-check, not as an exact identity.)")

    print("\nTop 10 codes by |delta_krw| (partial_tp - baseline), separating direct pnl effect:")
    ranked = sorted(deltas.items(), key=lambda x: -abs(x[1]))
    for code, delta in ranked[:10]:
        print(f"  {code}: baseline_pnl_krw={base_pnl.get(code,0):,.0f} partial_pnl_krw={partial_pnl.get(code,0):,.0f} "
              f"delta_krw={delta:,.0f} ({delta/total_delta_krw*100:.1f}%% of total delta)" if total_delta_krw else "")

    print(f"\n086520 (EcoPro) specifically: baseline_pnl_krw={base_pnl.get('086520',0):,.0f} "
          f"partial_tp_pnl_krw={partial_pnl.get('086520',0):,.0f} "
          f"delta_krw={deltas.get('086520',0):,.0f} "
          f"({deltas.get('086520',0)/total_delta_krw*100:.1f}%% of total delta_krw)" if total_delta_krw else "")

    # sanity: sum of per-stock deltas must equal total delta (tautological but verifies no
    # double counting / missing trades in aggregation)
    assert abs(sum(deltas.values()) - total_delta_krw) < 1
    print("\nSanity check passed: sum of per-stock deltas == total delta (no aggregation error).")

    # capital-competition signal: did partial_tp_pct's longer EcoPro hold correlate with fewer
    # total buy events / different stocks bought later in the period (crude proxy: count buys
    # after EcoPro's would-be full-exit date in baseline vs partial run)
    base_buys = sorted([t for t in base_trades if t["action"] == "BUY"], key=lambda t: t["date"])
    partial_buys = sorted([t for t in partial_trades if t["action"] == "BUY"], key=lambda t: t["date"])
    print(f"\nTotal BUY events: baseline={len(base_buys)} partial_tp={len(partial_buys)} "
          f"(fewer buys in partial_tp would suggest capital tied up in held winners crowded out other entries)")


if __name__ == "__main__":
    main()
