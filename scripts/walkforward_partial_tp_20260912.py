"""Walk-forward check of Experiment #2 (partial_tp_pct=0.3) standalone sector_focus
performance across the 6 standard periods -- the original finding (263.29%->319.90%) was
a single continuous 2020-2026 run only."""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest_strategies.sector import run_backtest_sector  # noqa: E402
import sqlite3  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402

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
    baseline_rets, partial_rets = [], []
    for label, start, end in PERIODS:
        base_id = run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9)
        partial_id = run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9, partial_tp_pct=0.3)
        base_ret = conn.execute("SELECT total_return_pct FROM backtest_runs WHERE run_id=?", (base_id,)).fetchone()[0]
        partial_ret = conn.execute("SELECT total_return_pct FROM backtest_runs WHERE run_id=?", (partial_id,)).fetchone()[0]
        baseline_rets.append(base_ret)
        partial_rets.append(partial_ret)
        print(f"{label}: baseline={base_ret}% partial_tp0.3={partial_ret}% delta={partial_ret-base_ret:+.2f}pp", flush=True)
    print(f"\navg6 baseline={sum(baseline_rets)/6:.2f}% avg6 partial_tp0.3={sum(partial_rets)/6:.2f}% "
          f"better_periods={sum(1 for b,p in zip(baseline_rets,partial_rets) if p>b)}/6", flush=True)
    conn.close()


if __name__ == "__main__":
    main()
