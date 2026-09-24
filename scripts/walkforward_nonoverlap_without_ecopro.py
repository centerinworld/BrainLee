# -*- coding: utf-8 -*-
"""
walkforward_nonoverlap_without_ecopro.py
TASK_PARTIAL_TP_P3 Verification:
Execute 6-period non-overlapping walkforward excluding 086520 (Ecopro) to determine
whether partial_tp_pct=0.3 maintains a genuine quantitative edge across the diversified basket
without relying on single-stock concentration.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3
from backtest_strategies.sector import run_backtest_sector
from backtest_common import DB_PATH

PERIODS = [
    ("P1_2020.01-2021.01", "2020-01-01", "2021-01-31"),
    ("P2_2021.02-2022.02", "2021-02-01", "2022-02-28"),
    ("P3_2022.03-2023.03", "2022-03-01", "2023-03-31"),
    ("P4_2023.04-2024.04", "2023-04-01", "2024-04-30"),
    ("P5_2024.05-2025.05", "2024-05-01", "2025-05-31"),
    ("P6_2025.06-2026.09", "2025-06-01", "2026-09-08"),
]

EXCLUDE_ECOPRO = ["086520"]


def main():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    print("=" * 76)
    print("🔬 [TASK_PARTIAL_TP_P3] 086520(에코프로) 제외 6구간 비중복 워크포워드 검증")
    print("   비용: 1x (fee 1.5bps + tax 18bps + slip 10bps) 및 2x 비용 스트레스")
    print("=" * 76)

    base_1x, base_2x, partial_1x, partial_2x = [], [], [], []
    trades_count = {}

    for label, start, end in PERIODS:
        ids = {
            "base_1x": run_backtest_sector(
                start, end, per_stock=10_000_000, max_positions=9, cost_multiplier=1.0, exclude_codes=EXCLUDE_ECOPRO
            ),
            "base_2x": run_backtest_sector(
                start, end, per_stock=10_000_000, max_positions=9, cost_multiplier=2.0, exclude_codes=EXCLUDE_ECOPRO
            ),
            "partial_1x": run_backtest_sector(
                start, end, per_stock=10_000_000, max_positions=9, partial_tp_pct=0.3, cost_multiplier=1.0, exclude_codes=EXCLUDE_ECOPRO
            ),
            "partial_2x": run_backtest_sector(
                start, end, per_stock=10_000_000, max_positions=9, partial_tp_pct=0.3, cost_multiplier=2.0, exclude_codes=EXCLUDE_ECOPRO
            ),
        }
        rets = {k: conn.execute("SELECT total_return_pct FROM backtest_runs WHERE run_id=?", (v,)).fetchone()[0] for k, v in ids.items()}
        trades = {k: conn.execute("SELECT total_trades FROM backtest_runs WHERE run_id=?", (v,)).fetchone()[0] for k, v in ids.items()}
        
        base_1x.append(rets["base_1x"])
        base_2x.append(rets["base_2x"])
        partial_1x.append(rets["partial_1x"])
        partial_2x.append(rets["partial_2x"])
        
        edge_1x = rets["partial_1x"] - rets["base_1x"]
        edge_2x = rets["partial_2x"] - rets["base_2x"]
        
        print(f"[{label}] (거래수: base={trades['base_1x']}건, partial={trades['partial_1x']}건)", flush=True)
        print(f"  • 1x비용: base={rets['base_1x']:+.2f}% | partial_tp={rets['partial_1x']:+.2f}% ➔ edge={edge_1x:+.2f}%p", flush=True)
        print(f"  • 2x비용: base={rets['base_2x']:+.2f}% | partial_tp={rets['partial_2x']:+.2f}% ➔ edge={edge_2x:+.2f}%p", flush=True)

    avg = lambda xs: sum(xs) / len(xs)
    print("=" * 76)
    print("📊 [6구간 전체 비중복 평균 (086520 제외)]")
    print(f"  - base_1x    = {avg(base_1x):.2f}% | base_2x    = {avg(base_2x):.2f}%")
    print(f"  - partial_1x = {avg(partial_1x):.2f}% | partial_2x = {avg(partial_2x):.2f}%")
    print(f"  - avg6 edge at 1x cost = {avg(partial_1x)-avg(base_1x):+.2f}%p")
    print(f"  - avg6 edge at 2x cost = {avg(partial_2x)-avg(base_2x):+.2f}%p")
    print("=" * 76)
    conn.close()


if __name__ == "__main__":
    main()
