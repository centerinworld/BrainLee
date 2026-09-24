"""Re-run and re-register v4's 6-period suite after the _final_liquidation_quote fix.

docs/CLAUDE_HANDOFF_TO_CODEX_20260912_price_integrity.md item 4 found that v4's
stored 23.11~24.12 trades_json closes 282690 at exit_price=13500 (+3.29% "profit")
80 days after the stock actually stopped trading (2024-10-09) - that stale value
exactly matches the last observed close before the volume-0 suspension. This was
generated before backtest_common._final_liquidation_quote() existed; that function
now correctly returns (0.0, "기간종료(시세부재 전액손실)") when the period-end date
has no real quote (tests/test_backtest_final_liquidation.py, already passing).
The bug is fixed in code - only the stored run predates the fix.

Reuses the same re-run+re-register pattern already used twice for v4
(scratch/register_v4_asof_20260727.py) with the forbidden /Applications path
import removed (CLAUDE.md: only /Volumes/Realtek_NVME/stock_dashboard[/runtime]
are valid paths).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
import backtest as bt  # noqa: E402
from run_registry import derive_status, register_run_set, select_run  # noqa: E402

PERIODS = [
    ("2020-03-01", "2021-11-30", "20.3~21.11"),
    ("2021-12-01", "2022-10-31", "21.12~22.10"),
    ("2022-11-01", "2023-10-31", "22.11~23.10"),
    ("2023-11-01", "2024-12-31", "23.11~24.12"),
    ("2024-06-01", "2025-05-31", "24.6~25.5"),
    ("2025-06-01", "2026-03-31", "25.6~26.3"),
]


def main() -> None:
    conn = connect_primary_db(timeout=120)
    conn.row_factory = sqlite3.Row

    members = {}
    rets = []
    for start, end, label in PERIODS:
        rid = bt.run_backtest(start, end, run_name=f"V4_PIT_PROVENANCE_TEMPORAL_MASK_{label}", asof_mktcap=True)
        spec = conn.execute(
            "SELECT run_hash, market_cap_mode FROM backtest_run_specs WHERE run_id=?", (rid,)
        ).fetchone()
        if not spec or not spec["run_hash"]:
            print(f"  {label} spec 없음 - 중단", flush=True)
            break
        row = conn.execute(
            "SELECT total_return_pct, total_trades, win_rate FROM backtest_runs WHERE run_id=?", (rid,)
        ).fetchone()
        ret = row["total_return_pct"] if row else None
        status = derive_status(conn, spec["run_hash"])
        print(f"  {label}: ret={ret} trades={row['total_trades'] if row else '-'} "
              f"win={row['win_rate'] if row else '-'} mode={spec['market_cap_mode']} "
              f"status={status['status']}", flush=True)
        members[label] = spec["run_hash"]
        rets.append(ret)

    result: dict = {}
    if len(members) == 6:
        suite = register_run_set("v4", "strategy_center", members)
        select_run("v4", "strategy_center", suite["run_hash"], selected_by="codex",
                   note="2026-09-23 v4 최종 재실행 - KRX 2015~2026 일별 PIT 상장/주식수, "
                        "재무행 provenance, 가격이상 시점별 차단, 확인된 합병 회수가치 반영")
        avg6 = sum(r for r in rets if r is not None) / 6
        pos = sum(1 for r in rets if r and r > 0)
        result = {"avg6": round(avg6, 2), "pos": pos, "rets": rets,
                  "suite": suite["run_hash"], "status": suite["status"]}
        print(f"등록완료: suite={suite['run_hash']} status={suite['status']} avg6={avg6:.2f}% ({pos}/6 양수)",
              flush=True)
    else:
        result = {"error": "incomplete", "rets": rets}

    print(json.dumps(result, ensure_ascii=False, indent=1))
    conn.close()


if __name__ == "__main__":
    main()
