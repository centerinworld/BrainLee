#!/usr/bin/env python3
"""HANDOFF §12 R6: daily total equity of the virtual (paper) cash accounts, rebuilt from virtual_cash_ledger + price_history.

equity(d) = sum over CONSISTENT strategy accounts of [initial_cash + cumulative cash_delta + open positions marked at the latest close <= d].
An account is EXCLUDED when its ledger is inconsistent: a holding sold more than it bought (e.g. 'momentum'/'peak' - positions mirrored in before the
account existed, so sells credit cash that was never debited; balances were inflated 4.3x/1.8x on 2026-09-25).
Writes virtual_account_equity_daily (idempotent upsert, explicit DEFAULTs): trade_date, equity, cash, positions_value, n_accounts, excluded,
peak_60d, dd_pct, state ('normal' | 'half' | 'stop', with hysteresis: leave a state only when KOSPI > MA60 and dd >= -5%).
R6 thresholds: dd <= -10% -> 'half' (new entries reduced 50%), dd <= -15% -> 'stop'. Used by virtual_trade_guards (shadow by default).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

HALF, STOP, RECOVER = -10.0, -15.0, -5.0
DDL = """CREATE TABLE IF NOT EXISTS virtual_account_equity_daily (
  trade_date TEXT PRIMARY KEY, equity DOUBLE PRECISION DEFAULT NULL, cash DOUBLE PRECISION DEFAULT NULL,
  positions_value DOUBLE PRECISION DEFAULT NULL, n_accounts INTEGER DEFAULT 0, excluded TEXT DEFAULT '',
  peak_60d DOUBLE PRECISION DEFAULT NULL, dd_pct DOUBLE PRECISION DEFAULT NULL, state TEXT DEFAULT 'normal',
  updated_at TIMESTAMP DEFAULT now())"""
DDL_STRAT = """CREATE TABLE IF NOT EXISTS virtual_strategy_equity_daily (
  strategy TEXT NOT NULL, trade_date TEXT NOT NULL, equity DOUBLE PRECISION DEFAULT NULL, initial_cash DOUBLE PRECISION DEFAULT NULL,
  updated_at TIMESTAMP DEFAULT now(), PRIMARY KEY (strategy, trade_date))"""


def main(dry_run: bool = False) -> int:
    conn = connect_primary_db(timeout=300)
    if not dry_run:
        conn.execute(DDL)
        conn.execute(DDL_STRAT)
        conn.commit()
    accts = {r[0]: float(r[1]) for r in conn.execute("SELECT strategy, initial_cash FROM virtual_cash_accounts").fetchall()}
    ev = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT l.strategy, l.event_type, COALESCE(l.stock_code, h.stock_code) AS code, l.holding_id, l.quantity, l.cash_delta, l.gross_amount, l.occurred_at "
        "FROM virtual_cash_ledger l LEFT JOIN peak_holding h ON h.id = l.holding_id ORDER BY l.occurred_at, l.id").fetchall()],
        columns=["strategy", "event_type", "code", "holding_id", "qty", "cash_delta", "gross", "occurred"])
    ev["day"] = pd.to_datetime(ev.occurred.astype(str).str[:10])
    # Pair every SELL with a BUY of the same strategy: first by identical holding_id, otherwise the earliest still-open buy of the SAME quantity.
    # A paired buy is closed on the sell's day (its stock code is inherited from the sell when the buy has none). Positions are then valued from OPEN buys only,
    # so it does not matter whether a sell row carries a stock code. A strategy is INCONSISTENT when a sell cannot be paired to any buy (sells without buys).
    ev = ev.reset_index(drop=True)
    ev["closed_day"] = pd.NaT
    bad_set = set()
    for strat, g in ev.groupby("strategy"):
        open_buys = [i for i in g.index[g.event_type == "buy"]]
        for i in g.index[g.event_type == "sell"]:
            hit = next((j for j in open_buys if pd.notna(ev.at[i, "holding_id"]) and ev.at[j, "holding_id"] == ev.at[i, "holding_id"]), None)
            if hit is None:
                hit = next((j for j in open_buys if ev.at[j, "qty"] == ev.at[i, "qty"] and ev.at[j, "day"] <= ev.at[i, "day"]), None)
            if hit is None:
                bad_set.add(strat)
                continue
            if pd.isna(ev.at[hit, "code"]) and pd.notna(ev.at[i, "code"]):
                ev.at[hit, "code"] = ev.at[i, "code"]
            ev.at[hit, "closed_day"] = ev.at[i, "day"]
            open_buys.remove(hit)
    bad = sorted(bad_set)
    use = [s for s in accts if s not in bad]
    start, end = ev.day.min(), pd.Timestamp(conn.execute("SELECT MAX(date) FROM price_history WHERE stock_code='^KS11'").fetchone()[0])
    days = pd.bdate_range(start, end)
    codes = sorted(ev.code.dropna().unique())
    px = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code, date::text, close FROM price_history WHERE stock_code = ANY(?) AND date >= ? AND close > 0", (codes, start.strftime("%Y-%m-%d"))).fetchall()],
        columns=["code", "date", "close"])
    px["date"] = pd.to_datetime(px.date)
    close = px.pivot_table(index="date", columns="code", values="close", aggfunc="last").reindex(days).ffill()
    ks = pd.DataFrame([tuple(r) for r in conn.execute("SELECT date::text, close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date").fetchall()],
                      columns=["date", "close"]).drop_duplicates("date")
    ks["date"] = pd.to_datetime(ks.date)
    ks = ks.set_index("date").close
    ks_ok = (ks > ks.rolling(60).mean()).reindex(days).ffill().fillna(True)

    rows, prev_state, hist, strat_rows = [], "normal", [], []
    for d in days:
        cash = pos_val = 0.0
        for s in accts:
            e = ev[(ev.strategy == s) & (ev.day <= d)]
            s_cash = accts[s] + float(e.cash_delta.sum())
            s_pos = 0.0
            open_b = e[(e.event_type == "buy") & (e.closed_day.isna() | (e.closed_day > d))]
            for code, q, gross in zip(open_b.code, open_b.qty, open_b.gross):
                if pd.notna(code) and code in close.columns and pd.notna(close.at[d, code]):
                    s_pos += float(q) * float(close.at[d, code])
                else:
                    s_pos += float(gross)                       # code/price unknown: cost basis
            if s in bad:
                continue                                    # inconsistent ledger (sells without buys): no trustworthy equity, excluded everywhere
            cash += s_cash
            pos_val += s_pos
            strat_rows.append((s, d.strftime("%Y-%m-%d"), s_cash + s_pos, accts[s]))
        equity = cash + pos_val
        hist.append(equity)
        peak = max(hist[-60:])
        dd = (equity / peak - 1) * 100
        if dd <= STOP:
            state = "stop"
        elif dd <= HALF:
            state = "half" if prev_state != "stop" else "stop"
        elif prev_state != "normal" and not (bool(ks_ok.get(d, True)) and dd >= RECOVER):
            state = prev_state
        else:
            state = "normal"
        prev_state = state
        rows.append((d.strftime("%Y-%m-%d"), equity, cash, pos_val, len(use), ",".join(bad), peak, dd, state))
    df = pd.DataFrame(rows, columns=["trade_date", "equity", "cash", "positions_value", "n_accounts", "excluded", "peak_60d", "dd_pct", "state"])
    print(f"accounts used={len(use)} excluded(inconsistent ledger)={bad}")
    print(df.tail(8).round(1).to_string(index=False))
    print("state counts:", df.state.value_counts().to_dict(), "min dd:", round(df.dd_pct.min(), 2))
    if dry_run:
        return 0
    conn.executemany(
        "INSERT INTO virtual_strategy_equity_daily(strategy,trade_date,equity,initial_cash) VALUES(?,?,?,?) "
        "ON CONFLICT (strategy,trade_date) DO UPDATE SET equity=excluded.equity, initial_cash=excluded.initial_cash, updated_at=now()", strat_rows)
    conn.executemany(
        "INSERT INTO virtual_account_equity_daily(trade_date,equity,cash,positions_value,n_accounts,excluded,peak_60d,dd_pct,state) VALUES(?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT (trade_date) DO UPDATE SET equity=excluded.equity, cash=excluded.cash, positions_value=excluded.positions_value, n_accounts=excluded.n_accounts, "
        "excluded=excluded.excluded, peak_60d=excluded.peak_60d, dd_pct=excluded.dd_pct, state=excluded.state, updated_at=now()",
        [tuple(r) for r in rows])
    conn.commit()
    print("upserted", len(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main("--dry-run" in sys.argv))
