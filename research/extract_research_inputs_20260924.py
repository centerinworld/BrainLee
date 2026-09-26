#!/usr/bin/env python3
"""Extract research inputs to parquet using the PRODUCTION venv (db_compat), so the separate research venv
(numpy 2.x, alphalens/quantstats/vectorbt/PyPortfolioOpt) never needs DB credentials or touches prod packages.

Outputs (data_cache/research/):
  adj_close.parquet   wide date x code close for the snapshot universe, 2019-06-01..latest. Returns on days that the
                      price_jump_audit classifies as corporate actions are set to
                      0 before re-compounding - the standard ratio-adjustment approximation. Coverage-gap days keep their
                      multi-day return (it is real). Also writes adj_meta.json (n masked returns).
  volume.parquet      wide date x code volume (raw)
  factors.parquet     strategy_feature_snapshot_pit_v2 point-in-time features (month-end) - only columns that are NOT
                      price-derived are used as-is; price-derived ones are recomputed in the research step from adj_close
                      because the stored snapshot predates the 2026-09 price repairs.
  benchmark.parquet   ^KS11 close.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "data_cache" / "research"
CORPORATE_ACTION_CLASSES = ("confirmed_corporate_action", "corporate_action_pending_confirmation",
                            "corporate_action_share_count_evidence", "corporate_action_or_delisting_nearby")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = connect_primary_db(timeout=900)
    fac = pd.DataFrame([tuple(r) for r in conn.execute("SELECT * FROM strategy_feature_snapshot").fetchall()],
                       columns=[r[0] for r in conn.execute(
                           "SELECT column_name FROM information_schema.columns WHERE table_name='strategy_feature_snapshot' ORDER BY ordinal_position").fetchall()])
    fac.to_parquet(OUT / "factors.parquet")
    codes = sorted(fac.stock_code.unique())
    px = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,date,close,volume FROM price_history WHERE stock_code = ANY(?) AND date>='2019-06-01' AND close>0",
        (codes,)).fetchall()], columns=["code", "date", "close", "volume"])
    px["date"] = pd.to_datetime(px["date"])
    close = px.pivot(index="date", columns="code", values="close").sort_index()
    vol = px.pivot(index="date", columns="code", values="volume").sort_index()
    ev = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT stock_code,event_date,classification FROM price_jump_audit WHERE stock_code = ANY(?)", (codes,)).fetchall()],
        columns=["code", "date", "cls"])
    ev = ev[ev.cls.isin(CORPORATE_ACTION_CLASSES)]  # only real corporate actions; other flagged jumps are real moves / bad rows
    ev["date"] = pd.to_datetime(ev["date"])
    ret = close.pct_change(fill_method=None)
    masked = 0
    for code, d in zip(ev.code, ev.date):
        if code in ret.columns and d in ret.index and pd.notna(ret.at[d, code]):
            ret.at[d, code] = 0.0
            masked += 1
    first = close.apply(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
    adj = (1 + ret.fillna(0)).cumprod() * first
    adj = adj.where(close.notna())
    adj.astype("float32").to_parquet(OUT / "adj_close.parquet")
    vol.astype("float32").to_parquet(OUT / "volume.parquet")
    bm = pd.DataFrame([tuple(r) for r in conn.execute(
        "SELECT date,close FROM price_history WHERE stock_code='^KS11' AND date>='2019-06-01' ORDER BY date").fetchall()], columns=["date", "close"])
    bm["date"] = pd.to_datetime(bm["date"]); bm.set_index("date").to_parquet(OUT / "benchmark.parquet")
    (OUT / "adj_meta.json").write_text(json.dumps({"codes": len(codes), "days": len(adj), "masked_returns": masked,
                                                    "latest": str(adj.index.max().date())}))
    print({"codes": len(codes), "days": len(adj), "masked_returns": masked, "latest": str(adj.index.max().date())})


if __name__ == "__main__":
    main()
