#!/usr/bin/env python3
"""Reconcile the two equal-weight results (RESEARCH venv).

vectorbt sweep: each entry gets a FIXED 1e8/20 cash slice (winners are not re-sized), exits at the next rebalance.
PyPortfolioOpt side-by-side: capital is re-split equally across the month's names every rebalance (monthly compounding).
Both use the same selection. This script computes the same book three ways with one shared return matrix and one cost
model so the gap is attributable: (a) monthly compounding, weights reset to 1/N each rebalance (cost on turnover);
(b) fixed-slice: each slot's capital is only reset when the slot is re-entered (approximation of vectorbt); (c) (a) with
next-day execution instead of same-day close. Output research_outputs/reconcile_ew_20260924.json.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN, OUT = ROOT / "data_cache" / "research", ROOT / "research_outputs"
N, UNIV, COST = 20, 1000, 0.0035


def zs(s):
    return (s - s.mean()) / s.std()


def stats(r):
    r = r.dropna(); eq = (1 + r).cumprod(); yrs = len(r) / 252
    return {"cagr_pct": round(float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), 2), "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2),
            "mdd_pct": round(float(((eq / eq.cummax() - 1).min()) * 100), 2)}


def main():
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet"); snap["dt"] = pd.to_datetime(snap.snapshot_date)
    ret = adj.pct_change(fill_method=None).fillna(0)
    lowvol = -adj.pct_change(fill_method=None).rolling(60, min_periods=40).std()
    dist = adj / adj.rolling(252, min_periods=200).max() - 1
    turn = (adj * vol).rolling(60, min_periods=40).mean()
    ok = ((vol.fillna(0) == 0).rolling(60, min_periods=30).sum() <= 5)
    ey = snap.assign(v=np.where(snap.per > 0, 1 / snap.per, np.nan)).pivot_table(index="dt", columns="stock_code", values="v")
    reb = []
    for d in sorted(snap.dt.unique()):
        pos = adj.index.searchsorted(pd.Timestamp(d), side="right") - 1
        if pos >= 260:
            reb.append((pd.Timestamp(d), pos))
    picks = {}
    for sd, pos in reb:
        td = adj.index[pos]
        cand = turn.loc[td].where(ok.loc[td]).dropna().nlargest(UNIV).index
        c2 = cand.intersection(ey.columns)
        z = pd.concat([zs(ey.loc[sd, c2].dropna()), zs(lowvol.loc[td, cand].dropna()), zs(dist.loc[td, cand].dropna())], axis=1).dropna()
        picks[pos] = list(z.mean(axis=1).nlargest(N).index)
    positions = sorted(picks)
    res = {}
    for name, lag in (("a_monthly_compound_same_day", 0), ("c_monthly_compound_next_day", 1)):
        rets, prev = [], set()
        for i, pos in enumerate(positions[:-1]):
            names = picks[pos]; start = pos + 1 + lag; end = positions[i + 1] + lag
            seg = ret.iloc[start:end + 1][names]
            port = seg.mean(axis=1)
            to = len(set(names) ^ prev) / (2 * N) * 2 if prev else 1.0   # fraction of book traded (one-way, both legs)
            port.iloc[0] -= to * COST / 2 * 2
            rets.append(port); prev = set(names)
        res[name] = stats(pd.concat(rets))
    # (b) the same book through vectorbt from_orders with target-percent weights (exact monthly-compounding definition) and
    # (c2) through from_signals with fixed 1/N cash slices (the definition the rule sweep used). Comparing them isolates
    # whether the sweep's lower absolute return comes from fixed-slice sizing or from something else.
    import vectorbt as vbt
    px = adj.ffill(limit=5)
    size = pd.DataFrame(np.nan, index=px.index, columns=px.columns)
    entries = pd.DataFrame(False, index=px.index, columns=px.columns); exits = entries.copy()
    prevnames = set()
    for i, pos in enumerate(positions[:-1]):
        ex = px.index[pos + 1]
        names = picks[pos]
        size.loc[ex, :] = 0.0
        size.loc[ex, names] = 1.0 / N
        entries.loc[ex, [n for n in names if n not in prevnames]] = True
        exits.loc[ex, [n for n in prevnames if n not in names]] = True
        prevnames = set(names)
    pf = vbt.Portfolio.from_orders(px, size=size, size_type="targetpercent", group_by=True, cash_sharing=True,
                                   init_cash=1e8, fees=0.0025, slippage=0.001, freq="1D")
    res["b_vectorbt_targetpercent"] = stats(pf.returns()[px.index[positions[0] + 1]:])
    pf2 = vbt.Portfolio.from_signals(px, entries=entries, exits=exits, size=1e8 / N, size_type="value", group_by=True, cash_sharing=True,
                                     init_cash=1e8, fees=0.0025, slippage=0.001, freq="1D")
    res["c2_vectorbt_fixed_slice_signals"] = stats(pf2.returns()[px.index[positions[0] + 1]:])
    (OUT / "reconcile_ew_20260924.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
