#!/usr/bin/env python3
"""Persist KIS executions and flag local/broker lifecycle mismatches."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from kis_client import kis_client  # noqa: E402
from live_trading_data import ensure_live_data_schema  # noqa: E402


def reconcile() -> dict:
    ensure_live_data_schema()
    executions = kis_client.get_today_executions() or []
    today = datetime.now().strftime("%Y-%m-%d")
    now = datetime.now().isoformat(timespec="seconds")
    inserted = unresolved = 0
    conn = connect_stock_db()
    try:
        for item in executions:
            broker_no = str(item.get("order_no") or "").strip()
            if not broker_no:
                unresolved += 1
                continue
            local = conn.execute(
                """SELECT order_id,qty,filled_qty,status FROM live_orders
                   WHERE mode='LIVE' AND stock_code=? AND side=?
                     AND substr(created_at,1,10)=?
                   ORDER BY order_id DESC LIMIT 1""",
                (item.get("stock_code"), item.get("tx_type"), today),
            ).fetchone()
            local_id = int(local[0]) if local else None
            requested = float(local[1]) if local else None
            filled = float(item.get("quantity") or 0)
            status = "matched" if local and filled == float(local[2] or local[1] or 0) else "unmatched"
            mismatch = "" if status == "matched" else ("local_order_missing" if not local else "fill_quantity_mismatch")
            conn.execute(
                """INSERT INTO broker_order_reconciliation(
                     local_order_id,broker_order_no,trade_date,stock_code,side,requested_qty,filled_qty,
                     avg_fill_price,broker_status,reconciliation_status,mismatch_reason,source_payload,reconciled_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(trade_date,broker_order_no) DO UPDATE SET
                     local_order_id=excluded.local_order_id,requested_qty=excluded.requested_qty,
                     filled_qty=excluded.filled_qty,avg_fill_price=excluded.avg_fill_price,
                     broker_status=excluded.broker_status,reconciliation_status=excluded.reconciliation_status,
                     mismatch_reason=excluded.mismatch_reason,source_payload=excluded.source_payload,
                     reconciled_at=excluded.reconciled_at""",
                (
                    local_id, broker_no, today, item.get("stock_code"), item.get("tx_type"), requested,
                    filled, item.get("price"), item.get("broker_status") or "filled", status, mismatch,
                    json.dumps(item.get("raw") or item, ensure_ascii=False), now,
                ),
            )
            inserted += 1
            unresolved += int(status != "matched")
        conn.commit()
    finally:
        conn.close()
    return {"trade_date": today, "broker_executions": len(executions), "upserted": inserted, "unresolved": unresolved}


if __name__ == "__main__":
    result = reconcile()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1 if result["unresolved"] else 0)
