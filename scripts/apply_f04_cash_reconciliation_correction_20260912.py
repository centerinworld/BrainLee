"""
Applies the F04 fix (docs/claude_handoff_strategy_code_findings_20260912.md) to the two
ALREADY-REGISTERED combined runs whose ledgers contain pyramid_add trades and whose
cash_reconciliation artifact was computed with the buggy pre-fix formula (side=='buy' only
counted as an outflow; pyramid_add was wrongly counted as an inflow like a sell).

This does NOT touch trading data, prices, or the merged_simulator.py execution path -- it
only re-registers the cash_reconciliation VERIFICATION ARTIFACT using the exact same strict
formula persist_merged_run() now uses (post-fix), applied to the ledger that was already
produced and stored. Evidence for why this is a correction, not a loosened gate, is in
research_outputs/strategy_code_review_20260912/claude_verification_and_experiment5_result.md.

User explicitly authorized this DB write on 2026-09-12 ("수정을 하고").
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
from run_registry import register_artifact  # noqa: E402

TARGETS = [
    ("cmb_5ccf7b96b727", "b0df1c28259d78a4"),
    ("cmb_c8f841b9708d", "f594a7f285cfb090"),
]


def main():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    for run_id, run_hash in TARGETS:
        row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
        d = json.loads(row["trades_json"])
        ledger = d["ledger"]
        summary = d["summary"]
        initial_cash = summary["initial_cash"]
        final_cash = summary["cash"]

        expected_cash = initial_cash
        for entry in ledger:
            gross = float(entry["quantity"]) * float(entry["price"])
            side = entry["side"]
            if side in ("buy", "pyramid_add"):
                expected_cash -= gross + float(entry.get("fee") or 0)
            elif side == "sell":
                expected_cash += gross - float(entry.get("fee") or 0) - float(entry.get("tax") or 0)
            else:
                raise ValueError(f"unknown ledger side {side!r} in {run_id}")
        delta = final_cash - expected_cash
        passed = abs(delta) < 0.01

        result = register_artifact(run_hash, "cash_reconciliation", passed, {
            "initial_cash": initial_cash, "final_cash": final_cash,
            "final_equity": summary["equity"], "ledger_rows": len(ledger),
            "ledger_expected_cash": expected_cash, "delta": delta,
            "correction_note": (
                "F04 fix applied 2026-09-12 (docs/claude_handoff_strategy_code_findings_20260912.md): "
                "previous artifact for this run_hash used the pre-fix formula that miscounted "
                "pyramid_add ledger rows as cash inflows instead of outflows. Recomputed with the "
                "corrected formula against the SAME stored ledger (no trading data changed). "
                "Evidence: research_outputs/strategy_code_review_20260912/claude_verification_and_experiment5_result.md"
            ),
        }, DB_PATH)
        print(f"{run_id} ({run_hash}): delta={delta:.2f} passed={passed} -> {result}")

    conn.close()


if __name__ == "__main__":
    main()
