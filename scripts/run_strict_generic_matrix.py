"""Re-run generic Strategy Center engines under the strict cash contract."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from backtest import (
    DB_PATH,
    init_backtest_db,
    run_backtest_hidden_rev,
    run_backtest_v1,
    run_backtest_v10,
    run_backtest_v11,
    run_backtest_v2,
    run_backtest_v5,
    run_backtest_value,
)

PERIODS = [
    ("2020-03-01", "2021-11-30", "bull"),
    ("2021-12-01", "2022-10-31", "bear"),
    ("2022-11-01", "2023-10-31", "recovery"),
    ("2023-11-01", "2024-12-31", "ai_rally"),
    ("2024-06-01", "2025-05-31", "recent"),
    ("2025-06-01", "2026-07-10", "latest"),
    ("2020-03-01", "2026-07-10", "continuous"),
]

STRATEGIES = {
    "v_trend": run_backtest_v1,
    "v1_value": run_backtest_value,
    "v2": run_backtest_v2,
    "v5": run_backtest_v5,
    "v10": run_backtest_v10,
    "v11": run_backtest_v11,
    "vbr": run_backtest_hidden_rev,
}


def prepare_run(run_id: str, strategy: str, start: str, end: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """INSERT OR REPLACE INTO backtest_runs
               (run_id,name,strategy,start_date,end_date,per_stock,max_pos,status)
               VALUES (?,?,?,?,?,?,?,?)""",
            (run_id, f"STRICT {strategy}", strategy, start, end, 10_000_000, 10, "queued"),
        )


def read_result(run_id: str) -> dict:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT total_return_pct,ann_return_pct,win_rate,total_trades,
                      max_drawdown_pct,summary_text,status
               FROM backtest_runs WHERE run_id=?""",
            (run_id,),
        ).fetchone()
    return dict(row) if row else {"status": "missing"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="*", choices=sorted(STRATEGIES), default=list(STRATEGIES))
    parser.add_argument("--output", default="data/strict_generic_matrix_20260712.json")
    args = parser.parse_args()
    init_backtest_db()
    results = {}
    for strategy in args.strategies:
        fn = STRATEGIES[strategy]
        results[strategy] = {}
        for start, end, label in PERIODS:
            run_id = f"strict_{strategy}_{label}_260712"
            prepare_run(run_id, strategy, start, end)
            print(f"START {strategy} {label}", flush=True)
            fn(start, end, per_stock=10_000_000, max_positions=10,
               run_name=f"STRICT {strategy} {label}", run_id=run_id)
            results[strategy][label] = read_result(run_id)
            print(json.dumps(results[strategy][label], ensure_ascii=False), flush=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"WROTE {output}", flush=True)


if __name__ == "__main__":
    main()
