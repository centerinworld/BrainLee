#!/usr/bin/env python3
"""Rebuild paper-trading cash ledgers from immutable peak_trade rows."""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from virtual_trading_ledger import ensure_schema, record_trade  # noqa: E402

DEFAULT_SEED = 100_000_000.0
OUT = ROOT / "research_outputs" / "virtual_cash_ledger_migration_latest.json"


def rebuild() -> dict:
    conn = connect_stock_db()
    ensure_schema(conn)
    strategies = [row[0] for row in conn.execute(
        "SELECT DISTINCT strategy FROM peak_trade WHERE strategy IS NOT NULL AND strategy<>''"
    ).fetchall()]
    results = {}
    try:
        for strategy in strategies:
            try:
                conn.execute("DELETE FROM virtual_cash_ledger WHERE strategy=?", (strategy,))
                conn.execute("DELETE FROM virtual_position_costs WHERE strategy=?", (strategy,))
                conn.execute("DELETE FROM virtual_cash_accounts WHERE strategy=?", (strategy,))
                open_holding_ids: dict[str, deque[int | None]] = defaultdict(deque)
                rows = conn.execute(
                    """SELECT id,holding_id,stock_name,tx_type,price,quantity,
                              COALESCE(total_amount,amount,0),COALESCE(profit,0),
                              COALESCE(tx_at,tx_date,created_at,'')
                       FROM peak_trade WHERE strategy=? ORDER BY COALESCE(tx_at,tx_date,created_at,''),id""",
                    (strategy,),
                ).fetchall()
                inserted = skipped = 0
                current_trade_id = None
                for row in rows:
                    trade_id, holding_id, name, side, price, qty, _, profit, occurred_at = row
                    current_trade_id = trade_id
                    side = str(side or "").lower()
                    qty = int(qty or 0)
                    price = float(price or 0)
                    if side not in {"buy", "sell"} or qty <= 0 or price <= 0:
                        skipped += 1
                        continue
                    if side == "buy":
                        # Legacy peak_trade rows rarely carry holding_id. A stable negative
                        # trade id links buy costs to the later FIFO sell without colliding
                        # with real positive peak_holding ids.
                        holding_id = int(holding_id) if holding_id else -int(trade_id)
                        open_holding_ids[str(name)].append(holding_id)
                    elif not holding_id:
                        if not open_holding_ids[str(name)]:
                            raise ValueError(f"orphan sell without prior buy: trade_id={trade_id} stock={name}")
                        holding_id = open_holding_ids[str(name)].popleft()
                    result = record_trade(
                        conn, strategy=strategy, initial_cash=DEFAULT_SEED, side=side,
                        stock_code="", stock_name=str(name or ""),
                        holding_id=int(holding_id) if holding_id else None,
                        quantity=qty, price=price, ref_key=f"peak_trade:{trade_id}",
                        occurred_at=str(occurred_at), gross_profit=float(profit or 0),
                    )
                    inserted += int(result["inserted"])
                conn.commit()
            except ValueError as exc:
                conn.rollback()
                conn.execute("DELETE FROM virtual_cash_ledger WHERE strategy=?", (strategy,))
                conn.execute("DELETE FROM virtual_position_costs WHERE strategy=?", (strategy,))
                conn.execute("DELETE FROM virtual_cash_accounts WHERE strategy=?", (strategy,))
                conn.commit()
                results[strategy] = {
                    "status": "historical_invalid", "trade_id": current_trade_id,
                    "error": str(exc),
                }
                continue
            account = conn.execute(
                "SELECT balance_krw,realized_pnl_net,total_fees,total_taxes,total_slippage FROM virtual_cash_accounts WHERE strategy=?",
                (strategy,),
            ).fetchone()
            account_values = list(account) if account else []
            results[strategy] = {
                "status": "complete",
                "inserted": inserted, "skipped": skipped,
                "balance_krw": float(account[0]) if account else DEFAULT_SEED,
                "realized_pnl_net": float(account[1]) if account else 0.0,
                "costs": sum(float(value or 0) for value in account_values[2:5]) if account else 0.0,
            }
    finally:
        conn.close()
    result = {"strategies": len(results), "results": results}
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(rebuild(), ensure_ascii=False, indent=2))
