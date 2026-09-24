"""
Experiment #1 (docs/claude_handoff_strategy_code_findings_20260912.md
"결함 보정 후 수익률 개선 실험" #1): PIT-corrected sector-internal stock selection.

Compares the F01+F02+F07+F08-corrected sector_focus baseline against three new opt-in
ranking factors (absolute earnings improvement, disclosure freshness, operating cash-flow
confirmation) individually and combined, over the same period/capital/cost.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest_strategies.sector import run_backtest_sector  # noqa: E402
import sqlite3  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402

START, END = "2020-01-01", "2026-09-08"

VARIANTS = [
    ("baseline_F01_F02_F07_F08", {}),
    ("+earnings_abs_bonus", {"use_earnings_abs_bonus": True}),
    ("+disclosure_freshness_bonus", {"use_disclosure_freshness_bonus": True}),
    ("+cashflow_confirm_gate", {"use_cashflow_confirm_gate": True}),
    ("+all_three_combined", {"use_earnings_abs_bonus": True, "use_disclosure_freshness_bonus": True, "use_cashflow_confirm_gate": True}),
]


def main():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    for label, kwargs in VARIANTS:
        run_id = run_backtest_sector(START, END, per_stock=10_000_000, max_positions=9, **kwargs)
        row = conn.execute("SELECT total_return_pct, total_trades FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
        print(f"{label}: run_id={run_id} return={row[0]}% trades={row[1]}", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
