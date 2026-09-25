"""Daily equity-curve storage for backtest runs (HANDOFF §10 P1-7/P1-8).

Table `backtest_equity_curve(run_id, date, source, equity)` keeps ONE curve per (run, source) so curves of different origin can
coexist; reads go through the view `backtest_equity_curve_best_v`, which picks per run the highest-priority source:
    engine (engine mark-to-market)  >  mtm_reconstructed (trades + prices)  >  realized_pnl (closed trades only, step curve)
    >  realized_pnl_assumed_100m (step curve on an ASSUMED 100M capital - CAGR/MDD are not trustworthy)
`quality` in the view flags the last two as 'approximate' / 'assumed_capital' so risk metrics can exclude them.
"""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Iterable

SOURCES_BY_PRIORITY = ["engine", "mtm_reconstructed", "realized_pnl", "realized_pnl_assumed_100m"]

DDL = """
CREATE TABLE IF NOT EXISTS backtest_equity_curve (
  run_id TEXT NOT NULL, date TEXT NOT NULL, equity DOUBLE PRECISION NOT NULL, source TEXT NOT NULL,
  PRIMARY KEY(run_id, source, date));
CREATE OR REPLACE VIEW backtest_equity_curve_best_v AS
SELECT e.run_id, e.date, e.equity, e.source,
       CASE e.source WHEN 'engine' THEN 'mark_to_market' WHEN 'mtm_reconstructed' THEN 'mark_to_market_approx'
            WHEN 'realized_pnl' THEN 'step_curve' ELSE 'assumed_capital' END AS quality
FROM backtest_equity_curve e
JOIN (SELECT run_id, source FROM (
        SELECT run_id, source, ROW_NUMBER() OVER (PARTITION BY run_id ORDER BY CASE source
            WHEN 'engine' THEN 1 WHEN 'mtm_reconstructed' THEN 2 WHEN 'realized_pnl' THEN 3 ELSE 4 END) rn
        FROM (SELECT DISTINCT run_id, source FROM backtest_equity_curve) s) r WHERE rn = 1) b
  ON b.run_id = e.run_id AND b.source = e.source;
"""


def migrate_primary_key(conn) -> None:
    """Old PK was (run_id,date): make it (run_id,source,date). Idempotent."""
    pk = [r[0] for r in conn.execute(
        """SELECT a.attname FROM pg_index i JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=ANY(i.indkey)
           WHERE i.indrelid='backtest_equity_curve'::regclass AND i.indisprimary""").fetchall()]
    if pk and "source" not in pk:
        conn.execute("ALTER TABLE backtest_equity_curve DROP CONSTRAINT backtest_equity_curve_pkey")
        conn.execute("ALTER TABLE backtest_equity_curve ADD PRIMARY KEY (run_id, source, date)")


def ensure_schema(conn) -> None:
    from price_integrity import native_script
    conn.execute("CREATE TABLE IF NOT EXISTS backtest_equity_curve (run_id TEXT NOT NULL, date TEXT NOT NULL, "
                 "equity DOUBLE PRECISION NOT NULL, source TEXT NOT NULL, PRIMARY KEY(run_id, source, date))")
    migrate_primary_key(conn)
    native_script(conn, DDL)


def store_curve(conn, run_id: str, rows: Iterable[tuple[str, float]], source: str) -> int:
    """Replace the (run_id, source) curve with rows [(date, equity), ...]."""
    rows = [(str(d)[:10], float(v)) for d, v in rows if v is not None]
    if not rows:
        return 0
    conn.execute("DELETE FROM backtest_equity_curve WHERE run_id=? AND source=?", (run_id, source))
    conn.executemany("INSERT INTO backtest_equity_curve(run_id,date,equity,source) VALUES(?,?,?,?) "
                     "ON CONFLICT (run_id,source,date) DO UPDATE SET equity=excluded.equity",
                     [(run_id, d, v, source) for d, v in rows])
    return len(rows)


def curve_from_result(result: dict) -> list[tuple[str, float]]:
    """Engine daily curve from a result dict (`equity_curve` = [{date, equity}, ...]) or []."""
    curve = result.get("equity_curve") or []
    out = []
    for p in curve:
        try:
            out.append((str(p["date"])[:10], float(p["equity"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out


_K = {"code": ("stock_code", "code", "sc"), "entry": ("entry_date", "buy_date", "entry"), "exit": ("exit_date", "sell_date", "exit"),
      "ep": ("entry_price",), "ret": ("profit_pct", "pnl_pct", "return_pct"), "pnl": ("profit_amt", "pnl"), "qty": ("qty",)}


def _is_date(v) -> bool:
    return isinstance(v, str) and len(v) >= 10 and v[4] == "-"


def _pick(t: dict, key: str):
    for k in _K[key]:
        v = t.get(k)
        if v is None or v == "":
            continue
        if key in ("entry", "exit") and not _is_date(v):
            continue  # some schemas store the PRICE under 'entry'/'exit'
        return v
    if key == "ep":
        v = t.get("entry")
        return v if isinstance(v, (int, float)) else None
    return None


def mtm_curve_from_trades(conn, trades: list[dict], start: str, end: str, initial: float, per_stock: float = 1e7):
    """Daily mark-to-market curve from closed-trade logs + price_history (jump-adjusted at audited corporate actions).

    equity(d) = initial + realized P&L of trades exited on/before d + sum over open trades of size * (P_d / P_entry - 1),
    ratio taken on the SAME adjusted series (a logged as-traded entry price would sit on another level across a split).
    Approximation: fixed size per trade (qty*entry, else |pnl|/|return|, else per_stock), no cash constraint."""
    import numpy as np
    import pandas as pd
    tr = []
    for t in trades:
        code, en, ex = _pick(t, "code"), _pick(t, "entry"), _pick(t, "exit")
        ep, pnl, ret, qty = _pick(t, "ep"), _pick(t, "pnl"), _pick(t, "ret"), _pick(t, "qty")
        if not (code and en and pnl is not None):
            continue
        if qty and ep:
            size = float(qty) * float(ep)
        elif ret not in (None, 0) and abs(float(ret)) > 0.01:
            size = abs(float(pnl)) / abs(float(ret) / 100)
        else:
            size = float(per_stock)
        tr.append((str(code), str(en)[:10], str(ex)[:10] if ex else None, float(pnl), size))
    if not tr:
        return []
    days = pd.bdate_range(str(start)[:10], str(end)[:10])
    codes = sorted({c for c, *_ in tr})
    px = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,date,close FROM price_history WHERE stock_code = ANY(?) AND date>=? AND date<=? AND close>0",
        (codes, str(start)[:10], str(end)[:10])).fetchall()], columns=["code", "date", "close"])
    if px.empty:
        return []
    px["date"] = pd.to_datetime(px["date"])
    close = px.pivot(index="date", columns="code", values="close").sort_index()
    ev = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,event_date FROM price_jump_audit WHERE stock_code = ANY(?) AND classification IN "
        "('confirmed_corporate_action','corporate_action_pending_confirmation','corporate_action_share_count_evidence','corporate_action_or_delisting_nearby')",
        (codes,)).fetchall()], columns=["code", "date"])
    ret = close.pct_change(fill_method=None)
    for c, d in zip(ev.code, pd.to_datetime(ev.date)):
        if c in ret.columns and d in ret.index:
            ret.at[d, c] = 0.0
    adj = ((1 + ret.fillna(0)).cumprod() * close.bfill().iloc[0]).where(close.notna())
    adj = adj.reindex(days.union(adj.index)).ffill().reindex(days)
    eq = np.full(len(days), float(initial))
    for code, en, ex, pnl, size in tr:
        if code not in adj.columns:
            continue
        p = adj[code].values
        i0 = days.searchsorted(pd.Timestamp(en))
        if i0 >= len(days) or not p[i0] or p[i0] != p[i0]:
            continue
        exd = pd.Timestamp(ex) if ex else None
        live = (days >= pd.Timestamp(en)) & ((days < exd) if exd is not None else True)
        unreal = (p / p[i0] - 1) * size
        eq += np.where(live & ~np.isnan(unreal), unreal, 0.0)
        if exd is not None:
            eq += np.where(days >= exd, pnl, 0.0)
    return [(d.strftime("%Y-%m-%d"), float(v)) for d, v in zip(days, eq)]


def save_run_curve(conn, run_id: str, result: dict, start: str | None = None, end: str | None = None,
                   per_stock: float = 1e7, max_pos: int = 10) -> str | None:
    """Store the best available curve for a finished run; returns the source used (None if nothing could be built).
    Also mirrors the curve into backtest_runs.equity_json."""
    curve = curve_from_result(result)
    source = "engine"
    if not curve:
        trades = result.get("trades") or []
        if trades and start and end:
            curve = mtm_curve_from_trades(conn, trades, start, end, float(per_stock) * float(max_pos) or 1e8, float(per_stock))
            source = "mtm_reconstructed"
    if not curve:
        return None
    ensure_schema(conn)
    store_curve(conn, run_id, curve, source)
    conn.execute("UPDATE backtest_runs SET equity_json=? WHERE run_id=?",
                 (json.dumps([{"date": d, "equity": v} for d, v in curve]), run_id))
    return source
