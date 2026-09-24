"""
Codex review follow-up: standalone partial_tp_pct=0.3 cost-2x stress across the genuinely
non-overlapping 6-period partition (not the earlier overlapping STANDARD_PERIODS).
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

PERIODS = [
    ("P1_2020.01-2021.01", "2020-01-01", "2021-01-31"),
    ("P2_2021.02-2022.02", "2021-02-01", "2022-02-28"),
    ("P3_2022.03-2023.03", "2022-03-01", "2023-03-31"),
    ("P4_2023.04-2024.04", "2023-04-01", "2024-04-30"),
    ("P5_2024.05-2025.05", "2024-05-01", "2025-05-31"),
    ("P6_2025.06-2026.09", "2025-06-01", "2026-09-08"),
]


def main():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    base_1x, base_2x, partial_1x, partial_2x = [], [], [], []
    for label, start, end in PERIODS:
        ids = {
            "base_1x": run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9, cost_multiplier=1.0),
            "base_2x": run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9, cost_multiplier=2.0),
            "partial_1x": run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9, partial_tp_pct=0.3, cost_multiplier=1.0),
            "partial_2x": run_backtest_sector(start, end, per_stock=10_000_000, max_positions=9, partial_tp_pct=0.3, cost_multiplier=2.0),
        }
        rets = {k: conn.execute("SELECT total_return_pct FROM backtest_runs WHERE run_id=?", (v,)).fetchone()[0] for k, v in ids.items()}
        base_1x.append(rets["base_1x"]); base_2x.append(rets["base_2x"])
        partial_1x.append(rets["partial_1x"]); partial_2x.append(rets["partial_2x"])
        print(f"{label}: base(1x/2x)={rets['base_1x']}%/{rets['base_2x']}% "
              f"partial_tp(1x/2x)={rets['partial_1x']}%/{rets['partial_2x']}% "
              f"edge(1x)={rets['partial_1x']-rets['base_1x']:+.2f}pp edge(2x)={rets['partial_2x']-rets['base_2x']:+.2f}pp", flush=True)

    avg = lambda xs: sum(xs) / len(xs)
    print(f"\navg6 base_1x={avg(base_1x):.2f}% base_2x={avg(base_2x):.2f}% "
          f"partial_1x={avg(partial_1x):.2f}% partial_2x={avg(partial_2x):.2f}%")
    print(f"avg6 edge at 1x cost = {avg(partial_1x)-avg(base_1x):+.2f}pp")
    print(f"avg6 edge at 2x cost = {avg(partial_2x)-avg(base_2x):+.2f}pp")
    conn.close()


if __name__ == "__main__":
    main()
