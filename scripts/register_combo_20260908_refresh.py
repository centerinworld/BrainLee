"""
2026-09-08: 사용자 승인 — 오늘 코드로 재현된 sector_focus+v2 병합계좌(403.1%,
안정성검증 통과)를 공식 헤드라인으로 persist_merged_run에 재등록.
component run_id: sector_focus=e1245314(hash 24ab3fdb0a28), v2=22adc49a(hash 5d1dbed8b823).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest_common import DB_PATH
from merged_simulator import CandidateOrder, MergeConfig, persist_merged_run
import sqlite3


def _fetch_trades_json(run_id: str) -> str:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    conn.close()
    return row[0] if row else "[]"


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
                side, float(row.get("price") or 0), strategy,
                1.0, sector=str(row.get("sector") or ""),
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
    trades_s = _fetch_trades_json("e1245314")
    trades_v = _fetch_trades_json("22adc49a")
    orders = _orders("sector_focus", trades_s) + _orders("v2", trades_v)

    config = MergeConfig(
        initial_cash=100_000_000, ticket_budget=10_000_000, max_positions=20,
        dynamic_tickets=True, strategy_budget_weights={}, tiebreak_mode="neutral_hash",
    )

    result = persist_merged_run(
        orders,
        component_run_hashes=["24ab3fdb0a28", "5d1dbed8b823"],
        config=config,
        db_path=DB_PATH,
        tiebreak_trials=8,
        allow_path_luck=False,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
