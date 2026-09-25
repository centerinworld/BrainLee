#!/usr/bin/env python3
"""HANDOFF §10 P1-4 (research venv): does the operating regime guard (VT_REGIME_FILTER: block NEW entries of momentum/breakout
strategies while KOSPI < MA60) help - tested on a momentum/breakout book instead of the low-PER book used before.

Signal (breakout family like peak / v_gc): close makes a new 60-day high, volume > 1.5x its 20-day mean, 120-day return > 0,
liquid top-1000 names (60d turnover), suspended/dormant names excluded. Exit: 12% trailing stop or 60 trading days.
Sizing: 1/20 of initial cash per entry (max ~20 live). Execution: next trading day.
Costs = the operating engine's `_tx_cost`: fee 0.015%/leg + sell tax 0.18% + market-cap slippage tiers
(>=1조 0.1%, >=1000억 0.2%, >=100억 0.4%, else 0.8%), applied per asset as an average per-leg rate.
Windows reported separately: full, pre-2024, 2024-2025, and the 2026-07..09 sell-off (KOSPI 8,476 -> 6,595 in the data).
Outputs research_outputs/regime_filter_momentum_20260925.{csv,md}.
"""
from __future__ import annotations

import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import vectorbt as vbt

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN, OUT = ROOT / "data_cache" / "research", ROOT / "research_outputs"
FEE, TAX = 0.00015, 0.0018
TIERS = [(10_000, 0.001), (1_000, 0.002), (100, 0.004), (0, 0.008)]
WINDOWS = {"full": ("2020-06-01", "2026-09-23"), "2020-06~2023": ("2020-06-01", "2023-12-31"),
           "2024~2025": ("2024-01-01", "2025-12-31"), "2026 sell-off (07~09)": ("2026-07-01", "2026-09-23")}


def slip(mcap_eok: float) -> float:
    return next(r for t, r in TIERS if mcap_eok >= t)


def stats(r: pd.Series) -> dict:
    r = r.dropna()
    if len(r) < 20 or r.std() == 0:
        return {"cagr_pct": np.nan, "sharpe": np.nan, "mdd_pct": np.nan, "days": len(r)}
    eq = (1 + r).cumprod()
    yrs = max(len(r) / 252, 1e-9)
    return {"cagr_pct": round(float((eq.iloc[-1] ** (1 / yrs) - 1) * 100), 2), "sharpe": round(float(r.mean() / r.std() * np.sqrt(252)), 2),
            "mdd_pct": round(float((eq / eq.cummax() - 1).min() * 100), 2), "days": len(r)}


def main() -> None:
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet")
    bm = pd.read_parquet(IN / "benchmark.parquet").close.reindex(adj.index).ffill()
    px = adj.ffill(limit=5)
    high60 = px.rolling(60, min_periods=50).max()
    volr = vol / vol.rolling(20, min_periods=15).mean()
    mom120 = px / px.shift(120) - 1
    turn = (adj * vol).rolling(60, min_periods=40).mean()
    liquid = turn.rank(axis=1, ascending=False) <= 1000
    active = (vol.fillna(0) == 0).rolling(60, min_periods=30).sum() <= 5
    breakout = (px >= high60) & (volr > 1.5) & (mom120 > 0) & liquid & active
    entries_raw = breakout & ~breakout.shift(1, fill_value=False)
    # per-asset average per-leg cost from the latest known market cap tier
    mc = snap.sort_values("snapshot_date").drop_duplicates("stock_code", keep="last").set_index("stock_code")["market_cap_억"]
    per_leg = pd.Series({c: FEE + slip(float(mc.get(c, 50) or 50)) + TAX / 2 for c in px.columns})
    ma60 = bm.rolling(60).mean()
    regime_ok = (bm > ma60)
    rows = []
    for label, gate in (("filter OFF", None), ("filter ON (KOSPI>MA60)", regime_ok)):
        ent = entries_raw.copy()
        if gate is not None:
            ent &= gate.values[:, None]
        e = ent.shift(1, fill_value=False)
        x = e.shift(60, fill_value=False)      # time exit: 60 trading days after entry (open-source vectorbt has no td_stop)
        pf = vbt.Portfolio.from_signals(px, entries=e, exits=x, size=1e8 / 20, size_type="value", group_by=True, cash_sharing=True,
                                        init_cash=1e8, fees=per_leg.reindex(px.columns).values, sl_stop=0.12, sl_trail=True, freq="1D")
        r = pf.returns()
        for wname, (a, b) in WINDOWS.items():
            rows.append({"variant": label, "window": wname, **stats(r[a:b]), "trades": int(pf.trades.count())})
    b = bm.pct_change()
    for wname, (a, c) in WINDOWS.items():
        rows.append({"variant": "KOSPI buy&hold", "window": wname, **stats(b[a:c]), "trades": 0})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "regime_filter_momentum_20260925.csv", index=False)
    pd.set_option("display.width", 200)
    print(df.pivot_table(index="window", columns="variant", values=["cagr_pct", "mdd_pct", "sharpe"]).round(2).to_string())
    print("entries/yr approx:", int(entries_raw.sum().sum() / 6.3))


if __name__ == "__main__":
    main()
