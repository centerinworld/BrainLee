"""
Codex review (2026-09-12, priority 1): "worst single trade pnl_pct" is not a substitute
for real MDD/recovery period. This reconstructs a genuine daily cash+mark-to-market
equity curve for the P3 (2022-03-01~2023-03-31) baseline and partial_tp_pct=0.3 runs from
their trade logs (now qty-tagged) + real daily closes from price_history, and computes
peak-to-trough max drawdown and recovery period for each.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import sqlite3  # noqa: E402
from backtest_common import DB_PATH  # noqa: E402

BASELINE_RUN_ID = "bc5abf7e"
PARTIAL_RUN_ID = "8237ade8"
INITIAL_CASH = 90_000_000  # per_stock=10_000_000 * max_positions=9


def _trades(run_id: str) -> list[dict]:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    raw = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()[0]
    conn.close()
    payload = json.loads(raw or "[]")
    return list(payload.get("trades") or []) if isinstance(payload, dict) else list(payload)


def _daily_prices(codes: set[str], start: str, end: str) -> dict[str, dict[str, float]]:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    ph = ",".join("?" * len(codes))
    rows = conn.execute(
        f"SELECT stock_code, date(date), close FROM price_history "
        f"WHERE stock_code IN ({ph}) AND date>=? AND date<=? AND close>0",
        (*sorted(codes), start, end),
    ).fetchall()
    conn.close()
    out: dict[str, dict[str, float]] = {}
    for code, d, close in rows:
        out.setdefault(code, {})[str(d)] = float(close)
    return out


def reconstruct_equity_curve(trades: list[dict], start: str, end: str) -> list[tuple[str, float]]:
    codes = {t["code"] for t in trades if t.get("code")}
    price_map = _daily_prices(codes, start, end)
    all_dates = sorted({d for by_date in price_map.values() for d in by_date})

    by_date: dict[str, list[dict]] = {}
    for t in trades:
        by_date.setdefault(t["date"][:10], []).append(t)

    cash = float(INITIAL_CASH)
    positions: dict[str, dict] = {}  # code -> {qty, entry_price}
    curve: list[tuple[str, float]] = []

    for day in all_dates:
        for t in by_date.get(day, []):
            action = t["action"]
            qty = t.get("qty")
            price = t.get("price")
            if action == "BUY" and qty and price:
                pos = positions.setdefault(t["code"], {"qty": 0, "entry_price": price})
                new_qty = pos["qty"] + qty
                pos["entry_price"] = (pos["entry_price"] * pos["qty"] + price * qty) / new_qty if pos["qty"] else price
                pos["qty"] = new_qty
                cash -= qty * price
            elif action == "PYRAMID_ADD" and qty and price:
                pos = positions.setdefault(t["code"], {"qty": 0, "entry_price": price})
                new_qty = pos["qty"] + qty
                pos["entry_price"] = (pos["entry_price"] * pos["qty"] + price * qty) / new_qty if pos["qty"] else price
                pos["qty"] = new_qty
                cash -= qty * price
            elif action in ("SELL", "SECTOR_EXIT", "FINAL") and qty and price:
                pos = positions.get(t["code"])
                if pos:
                    cash += qty * price  # gross proceeds; pnl_krw already reflects fee/tax net, but for
                    # equity curve purposes gross cash in + net pnl accounting would double count fees --
                    # use pnl_krw directly against the entry cost instead for consistency:
                    cash -= qty * price  # undo gross add
                    entry_cost = pos["entry_price"] * qty
                    pnl_krw = t.get("pnl_krw") or 0
                    cash += entry_cost + pnl_krw
                    pos["qty"] -= qty
                    if pos["qty"] <= 0:
                        positions.pop(t["code"], None)

        equity = cash
        for code, pos in positions.items():
            mark = price_map.get(code, {}).get(day, pos["entry_price"])
            equity += pos["qty"] * mark
        curve.append((day, equity))

    return curve


def mdd_and_recovery(curve: list[tuple[str, float]]) -> dict:
    peak = curve[0][1]
    peak_date = curve[0][0]
    max_dd = 0.0
    max_dd_date = None
    max_dd_peak_date = None
    trough_date = None
    recovery_date = None
    in_drawdown_since = None

    for date, equity in curve:
        if equity > peak:
            peak = equity
            peak_date = date
            in_drawdown_since = None
        dd = (equity - peak) / peak * 100 if peak else 0.0
        if dd < max_dd:
            max_dd = dd
            max_dd_date = date
            max_dd_peak_date = peak_date
            trough_date = date

    # find recovery: first date after trough where equity >= the peak that preceded the trough
    if trough_date and max_dd_peak_date:
        peak_value = next(e for d, e in curve if d == max_dd_peak_date)
        after_trough = [(d, e) for d, e in curve if d >= trough_date]
        for d, e in after_trough:
            if e >= peak_value:
                recovery_date = d
                break

    return {
        "max_drawdown_pct": round(max_dd, 2),
        "peak_date": max_dd_peak_date,
        "trough_date": trough_date,
        "recovery_date": recovery_date,
        "recovery_days": (None if not recovery_date else
                          (_days_between(trough_date, recovery_date))),
        "final_equity": curve[-1][1],
        "final_return_pct": round((curve[-1][1] / INITIAL_CASH - 1) * 100, 2),
    }


def _days_between(d1: str, d2: str) -> int:
    from datetime import datetime
    return (datetime.strptime(d2, "%Y-%m-%d") - datetime.strptime(d1, "%Y-%m-%d")).days


def main():
    start, end = "2022-03-01", "2023-03-31"
    for label, run_id in [("baseline", BASELINE_RUN_ID), ("partial_tp_pct=0.3", PARTIAL_RUN_ID)]:
        trades = _trades(run_id)
        curve = reconstruct_equity_curve(trades, start, end)
        stats = mdd_and_recovery(curve)
        print(f"{label} ({run_id}): {json.dumps(stats, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
