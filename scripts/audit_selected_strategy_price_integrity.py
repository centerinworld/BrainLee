#!/usr/bin/env python3
"""Find selected strategy trades whose holding windows cross unusable price jumps."""
from __future__ import annotations

import json
import sys
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_utils import connect_stock_db  # noqa: E402

OUT = ROOT / "research_outputs" / "selected_strategy_price_integrity_latest.json"


def _first_text(trade: dict, *keys: str) -> str:
    for key in keys:
        value = trade.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def holding_windows(trades: list[dict], period_end: str) -> list[tuple[str, str, str]]:
    """Normalize legacy event and round-trip ledgers into holding windows."""
    open_buys: dict[str, deque[str]] = defaultdict(deque)
    windows = []
    events = []
    for trade in trades:
        code = _first_text(trade, "code", "stock_code", "sc", "ticker")
        entry = _first_text(trade, "entry_date", "buy_date")[:10]
        exit_date = _first_text(trade, "exit_date", "sell_date")[:10]
        # Some legacy engines use entry/exit as dates; most use them as prices.
        if not entry and len(str(trade.get("entry") or "")) >= 10:
            entry = str(trade["entry"])[:10]
        if not exit_date and len(str(trade.get("exit") or "")) >= 10:
            exit_date = str(trade["exit"])[:10]
        if len(code) == 6 and entry and exit_date:
            windows.append((code, entry, exit_date))
            continue

        action = str(trade.get("action") or trade.get("side") or "").upper()
        if action not in {"BUY", "SELL"}:
            continue
        day = _first_text(
            trade,
            "date", "trade_date",
            "buy_date" if action == "BUY" else "sell_date",
            "entry_date" if action == "BUY" else "exit_date",
        )[:10]
        events.append((day, code, action))

    for day, code, action in sorted(events):
        if len(code) != 6 or not day:
            continue
        if action == "BUY":
            open_buys[code].append(day)
        elif action == "SELL" and open_buys[code]:
            windows.append((code, open_buys[code].popleft(), day))
    for code, buys in open_buys.items():
        windows.extend((code, day, period_end) for day in buys)
    return windows


def audit() -> dict:
    conn = connect_stock_db(readonly=True)
    strategies = []
    try:
        selected = conn.execute(
            """SELECT strategy,run_hash FROM selected_run_registry
               WHERE report_type='strategy_center' ORDER BY strategy"""
        ).fetchall()
        for selected_strategy, suite_hash in selected:
            contaminated = []
            trade_windows = 0
            members = conn.execute(
                """SELECT m.period_label,r.trades_json,r.end_date
                   FROM backtest_run_set_members m
                   JOIN backtest_run_specs s ON s.run_hash=m.run_hash
                   JOIN backtest_runs r ON r.run_id=s.run_id
                   WHERE m.suite_hash=? AND r.status='done'
                   ORDER BY m.period_label,s.created_at DESC""",
                (suite_hash,),
            ).fetchall()
            seen_periods = set()
            for label, trades_json, run_end in members:
                if label in seen_periods or not trades_json:
                    continue
                seen_periods.add(label)
                payload = json.loads(trades_json)
                trades = payload.get("trades", []) if isinstance(payload, dict) else payload
                period_end = str(run_end or "9999-12-31")[:10]
                for code, start, end in holding_windows(trades, period_end):
                    trade_windows += 1
                    jumps = conn.execute(
                        """SELECT event_date,classification,evidence FROM price_jump_audit
                           WHERE stock_code=? AND event_date BETWEEN ? AND ? AND return_usable=0
                           ORDER BY event_date""",
                        (code, start, end),
                    ).fetchall()
                    for jump in jumps:
                        contaminated.append({
                            "period": label, "stock_code": code, "holding_start": start,
                            "holding_end": end, "event_date": jump[0],
                            "classification": jump[1], "evidence": jump[2],
                        })
            strategies.append({
                "strategy": selected_strategy,
                "suite_hash": suite_hash,
                "holding_windows": trade_windows,
                "contaminated_windows": len(contaminated),
                "price_integrity_passed": trade_windows > 0 and not contaminated,
                "status": (
                    "passed" if trade_windows > 0 and not contaminated
                    else "failed" if contaminated
                    else "no_trade_evidence"
                ),
                "examples": contaminated[:20],
                "contaminated_events": contaminated,
            })
    finally:
        conn.close()
    result = {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "strategy_count": len(strategies),
        "passed": sum(item["price_integrity_passed"] for item in strategies),
        "failed": sum(item["status"] == "failed" for item in strategies),
        "no_trade_evidence": sum(item["status"] == "no_trade_evidence" for item in strategies),
        "strategies": strategies,
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(audit(), ensure_ascii=False, indent=2))
