#!/usr/bin/env python3
"""vectorbt rule sweep (RESEARCH venv). Explores regime filter x stop-loss x trailing-stop for a monthly-rebalanced,
equal-weight top-N book selected by the factors Alphalens validated (earn_yield, low_vol_60d, dist_high_252).

Design guards against over-fitting: parameters are ranked on TRAIN (2020-01..2023-12) only and then reported on the
untouched TEST window (2024-01..). Signals are executed on the NEXT trading day (no same-bar look-ahead); costs = 0.25%
fee + 0.10% slippage per side. Universe = 1,000 most liquid names by 60d turnover at each rebalance; suspended/dormant
names excluded like in the Alphalens run. Results are research candidates: per the adoption rule they must be reproduced
in the production backtest engine before touching live/paper defaults.
"""
from __future__ import annotations

import itertools
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "data_cache" / "research"
OUT = ROOT / "research_outputs"
N_HOLD, UNIVERSE = 20, 1000
TRAIN_END, TEST_START = "2023-12-31", "2024-01-01"


def zs(df: pd.DataFrame) -> pd.DataFrame:
    return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1), axis=0)


def main() -> None:
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet")
    bm = pd.read_parquet(IN / "benchmark.parquet").close.reindex(adj.index).ffill()
    ret = adj.pct_change(fill_method=None)
    lowvol = -ret.rolling(60, min_periods=40).std()
    dist = adj / adj.rolling(252, min_periods=200).max() - 1
    turn = (adj * vol).rolling(60, min_periods=40).mean()
    zero = ((vol.fillna(0) == 0).rolling(60, min_periods=30).sum() <= 5)
    snap["dt"] = pd.to_datetime(snap.snapshot_date)
    ey = snap.assign(v=np.where(snap.per > 0, 1 / snap.per, np.nan)).pivot_table(index="dt", columns="stock_code", values="v")
    reb_days = []
    for d in sorted(snap.dt.unique()):
        pos = adj.index.searchsorted(pd.Timestamp(d), side="right") - 1
        if pos >= 260:
            reb_days.append((pd.Timestamp(d), adj.index[pos]))
    sel = pd.DataFrame(False, index=adj.index, columns=adj.columns)
    for snap_d, td in reb_days:
        cand = turn.loc[td].where(zero.loc[td]).dropna().nlargest(UNIVERSE).index
        z = pd.concat([zs(ey.reindex([snap_d])[cand.intersection(ey.columns)]).iloc[0],
                       zs(lowvol.loc[[td], cand]).iloc[0], zs(dist.loc[[td], cand]).iloc[0]], axis=1)
        score = z.mean(axis=1, skipna=False).dropna().nlargest(N_HOLD).index
        sel.loc[td, score] = True
    rebal = sel.any(axis=1)
    hold = sel.where(rebal).ffill().fillna(False)             # target holdings: forward-filled from each rebalance day
    prev = hold.shift(1).fillna(False)
    entries_raw = hold & ~prev
    exits_raw = ~hold & prev
    px = adj.where(adj.notna(), np.nan).ffill(limit=5)
    ma = {w: bm.rolling(w).mean() for w in (100, 200)}
    results = []
    STOPS = [(None, False), (0.10, False), (0.15, False), (0.20, False), (0.15, True), (0.25, True)]  # (stop, trailing?)
    for regime, (sl, trailing) in itertools.product((None, 100, 200), STOPS):
        ent = entries_raw.copy()
        if regime:
            ent &= (bm > ma[regime]).values[:, None]
        e, x = ent.shift(1).fillna(False), exits_raw.shift(1).fillna(False)   # execute next trading day
        pf = vbt.Portfolio.from_signals(px, entries=e, exits=x, size=1e8 / N_HOLD, size_type="value", group_by=True,
                                        cash_sharing=True, init_cash=1e8, fees=0.0025, slippage=0.001,
                                        sl_stop=sl, sl_trail=trailing, freq="1D")
        r = pf.returns()
        def m(seg):
            if len(seg) < 60 or seg.std() == 0:
                return {}
            eq = (1 + seg).cumprod()
            yrs = len(seg) / 252
            return {"cagr": eq.iloc[-1] ** (1 / yrs) - 1, "sharpe": seg.mean() / seg.std() * np.sqrt(252),
                    "mdd": (eq / eq.cummax() - 1).min()}
        tr, te = m(r[:TRAIN_END]), m(r[TEST_START:])
        results.append({"regime_ma": regime or 0, "stop": sl or 0, "trailing": trailing,
                        **{f"train_{k}": v for k, v in tr.items()}, **{f"test_{k}": v for k, v in te.items()}})
    df = pd.DataFrame(results).sort_values("train_sharpe", ascending=False)
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "vectorbt_rule_sweep_20260924.csv", index=False)
    b = bm.pct_change().dropna()
    base = {"kospi_train_cagr": float((1 + b[:TRAIN_END]).prod() ** (252 / len(b[:TRAIN_END])) - 1),
            "kospi_test_cagr": float((1 + b[TEST_START:]).prod() ** (252 / len(b[TEST_START:])) - 1)}
    (OUT / "vectorbt_rule_sweep_20260924.json").write_text(json.dumps({"benchmark": base, "results": df.to_dict("records")}, ensure_ascii=False, indent=1, default=float))
    pd.set_option("display.width", 200)
    print(base); print(df.round(3).to_string(index=False))


if __name__ == "__main__":
    main()
