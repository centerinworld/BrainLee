"""
Experiment #2 (docs/claude_handoff_strategy_code_findings_20260912.md
"결함 보정 후 수익률 개선 실험" #2): winner-holding via partial take-profit instead of a
fixed full +50% exit. Compares against the SAME F01+F02+F07+F08-corrected baseline used
in Experiment #1 (isolated, not combined with the Experiment #1 selection factors yet).
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
    ("baseline_full_tp_(partial_tp_pct=None)", {}),
    ("partial_tp_pct=0.3", {"partial_tp_pct": 0.3}),
    ("partial_tp_pct=0.5", {"partial_tp_pct": 0.5}),
    ("partial_tp_pct=0.7", {"partial_tp_pct": 0.7}),
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
