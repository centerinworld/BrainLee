#!/usr/bin/env python3
"""Freeze current strategy-center holdings as prospective forward-test signals."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402
from live_signal_tracker import register_signal  # noqa: E402

ALLOWED_STRATEGIES = {"v_gc", "v_contract_momentum"}


def capture() -> dict:
    signal_date = datetime.now().date().isoformat()
    available_at = datetime.now().isoformat(timespec="seconds")
    conn = connect_stock_db()
    try:
        rows = conn.execute(
            """SELECT stock_code,stock_name,strategy,buy_price,current_price,entry_date,
                      entry_reason_json,updated_at
               FROM peak_holding
               WHERE is_active=1 AND stock_code IS NOT NULL AND length(stock_code)=6
               ORDER BY strategy,stock_code"""
        ).fetchall()
        selected = [row for row in rows if str(row[2] or "") in ALLOWED_STRATEGIES]
        signal_ids = []
        skipped_existing_episode = 0
        for row in selected:
            prior = conn.execute(
                """SELECT signal_payload_json FROM live_signal_registry
                   WHERE stock_code=? AND strategy_id=? AND action='BUY_CANDIDATE'
                   ORDER BY signal_date DESC LIMIT 1""",
                (row[0], row[2]),
            ).fetchone()
            if prior:
                try:
                    prior_payload = json.loads(prior[0] or "{}")
                except (TypeError, ValueError):
                    prior_payload = {}
                if str(prior_payload.get("source_entry_date") or "") == str(row[5] or ""):
                    skipped_existing_episode += 1
                    continue
            payload = {
                "source": "peak_holding_prospective_snapshot",
                "stock_name": row[1],
                "observed_buy_price": row[3],
                "observed_current_price": row[4],
                "source_entry_date": row[5],
                "entry_reason_json": row[6],
                "source_updated_at": str(row[7]),
            }
            signal_ids.append(register_signal(
                stock_code=row[0], signal_type="strategy_center_buy",
                strategy_id=row[2], signal_date=signal_date, available_at=available_at,
                action="BUY_CANDIDATE", payload=payload, conn=conn,
            ))
        conn.commit()
        return {
            "signal_date": signal_date,
            "captured": len(signal_ids),
            "skipped_existing_episode": skipped_existing_episode,
            "strategies": sorted({row[2] for row in selected}),
            "signal_ids": signal_ids,
        }
    finally:
        conn.close()


if __name__ == "__main__":
    print(json.dumps(capture(), ensure_ascii=False, indent=2))
