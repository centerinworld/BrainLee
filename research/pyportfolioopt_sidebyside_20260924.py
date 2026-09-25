#!/usr/bin/env python3
"""PyPortfolioOpt side-by-side (RESEARCH venv): same monthly top-20 selection as the vectorbt sweep, three weighting
schemes compared on realized next-month returns - EqualWeight (current), HRP, and constrained MinVol (max 15%/name,
L2 reg, Ledoit-Wolf covariance from the prior 252 days). Compute-only: nothing here feeds live/paper trading.
Cost: 0.35% per unit of one-way turnover. Output: research_outputs/pyportfolioopt_sidebyside_20260924.{md,json}.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from pypfopt import EfficientFrontier, HRPOpt, risk_models, objective_functions

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "data_cache" / "research"
OUT = ROOT / "research_outputs"
N_HOLD, UNIVERSE, COST = 20, 1000, 0.0035


def zs(s: pd.Series) -> pd.Series:
    return (s - s.mean()) / s.std()


def main() -> None:
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet"); snap["dt"] = pd.to_datetime(snap.snapshot_date)
    ret = adj.pct_change(fill_method=None)
    lowvol = -ret.rolling(60, min_periods=40).std()
    dist = adj / adj.rolling(252, min_periods=200).max() - 1
    turn = (adj * vol).rolling(60, min_periods=40).mean()
    ok = ((vol.fillna(0) == 0).rolling(60, min_periods=30).sum() <= 5)
    ey = snap.assign(v=np.where(snap.per > 0, 1 / snap.per, np.nan)).pivot_table(index="dt", columns="stock_code", values="v")
    days = []
    for d in sorted(snap.dt.unique()):
        pos = adj.index.searchsorted(pd.Timestamp(d), side="right") - 1
        if pos >= 260:
            days.append((pd.Timestamp(d), pos))
    schemes = {"equal": {}, "hrp": {}, "minvol": {}}
    rows, prev_w = [], {k: pd.Series(dtype=float) for k in schemes}
    for i, (sd, pos) in enumerate(days[:-1]):
        td = adj.index[pos]
        nxt = adj.index[days[i + 1][1]]
        cand = turn.loc[td].where(ok.loc[td]).dropna().nlargest(UNIVERSE).index
        c2 = cand.intersection(ey.columns)
        z = pd.concat([zs(ey.loc[sd, c2].dropna()), zs(lowvol.loc[td, cand].dropna()), zs(dist.loc[td, cand].dropna())], axis=1).dropna()
        names = z.mean(axis=1).nlargest(N_HOLD).index
        hist = ret.loc[adj.index[pos - 252]:td, names].dropna(axis=1, thresh=200).fillna(0)
        names = list(hist.columns)
        if len(names) < 10:
            continue
        w = {"equal": pd.Series(1 / len(names), index=names)}
        try:
            w["hrp"] = pd.Series(HRPOpt(hist).optimize())
        except Exception:  # noqa: BLE001
            w["hrp"] = w["equal"]
        try:
            S = risk_models.CovarianceShrinkage(hist, returns_data=True).ledoit_wolf()
            ef = EfficientFrontier(None, S, weight_bounds=(0, 0.15)); ef.add_objective(objective_functions.L2_reg, gamma=0.1)
            ef.min_volatility(); w["minvol"] = pd.Series(ef.clean_weights())
        except Exception:  # noqa: BLE001
            w["minvol"] = w["equal"]
        fwd = ret.loc[(ret.index > td) & (ret.index <= nxt), names].fillna(0)
        for k, wk in w.items():
            wk = wk.reindex(names).fillna(0)
            port = (fwd * wk).sum(axis=1)
            to = (wk.sub(prev_w[k], fill_value=0)).abs().sum()
            port.iloc[0] -= to * COST
            rows.append(pd.DataFrame({"r": port, "scheme": k, "max_w": wk.max()}))
            prev_w[k] = wk
    res = pd.concat(rows)
    out = {}
    for k, g in res.groupby("scheme"):
        r = g.r.sort_index(); eq = (1 + r).cumprod(); yrs = len(r) / 252
        out[k] = {"cagr_pct": float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), "sharpe": float(r.mean() / r.std() * np.sqrt(252)),
                  "vol_pct": float(r.std() * np.sqrt(252) * 100), "mdd_pct": float(((eq / eq.cummax() - 1).min()) * 100),
                  "avg_max_weight_pct": float(g.max_w.mean() * 100)}
    (OUT / "pyportfolioopt_sidebyside_20260924.json").write_text(json.dumps(out, indent=1))
    print(pd.DataFrame(out).T.round(2).to_string())


if __name__ == "__main__":
    main()
