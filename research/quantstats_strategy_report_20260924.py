#!/usr/bin/env python3
"""QuantStats performance report for the 26 selected strategies vs KOSPI (run in the RESEARCH venv).

Per strategy the six period runs are chained by daily returns, each period clipped to dates after the previous
period's last date (two standard periods overlap in 2024). Curves are 'engine' (mark-to-market) or reconstructed
'realized_pnl*' (closed trades only, lumpy) - the source mix is reported per strategy so reconstructed curves are
not mistaken for mark-to-market ones. Outputs: research_outputs/quantstats_summary_20260924.{csv,json} and HTML
tearsheets for the top strategies in research_outputs/quantstats/.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import quantstats as qs

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "data_cache" / "research"
OUT = ROOT / "research_outputs"
qs.extend_pandas()


def strategy_returns(g: pd.DataFrame) -> tuple[pd.Series, dict]:
    parts, srcs, last_end = [], [], None
    for (period, run_id, start), gg in g.groupby(["period", "run_id", "start"], sort=False):
        eq = gg.assign(date=pd.to_datetime(gg["date"])).drop_duplicates("date").set_index("date").equity.sort_index()
        eq = eq.reindex(pd.bdate_range(eq.index.min(), eq.index.max())).ffill()
        r = eq.pct_change().dropna()
        if last_end is not None:
            r = r[r.index > last_end]
        if r.empty:
            continue
        parts.append(r); srcs.append(gg.source.iloc[0]); last_end = r.index.max()
    if not parts:
        return pd.Series(dtype=float), {}
    r = pd.concat(parts).sort_index()
    return r[~r.index.duplicated()], {"periods": len(parts), "engine_periods": sum(s == "engine" for s in srcs),
                                       "reconstructed_periods": sum(s != "engine" for s in srcs)}


def main() -> None:
    df = pd.read_parquet(IN / "strategy_period_equity.parquet")
    bm = pd.read_parquet(IN / "benchmark.parquet").close
    bm = bm.reindex(pd.bdate_range(bm.index.min(), bm.index.max())).ffill().pct_change().dropna()
    rows = []
    (OUT / "quantstats").mkdir(parents=True, exist_ok=True)
    rets = {}
    for strat, g in df.groupby("strategy"):
        r, meta = strategy_returns(g)
        if len(r) < 60:
            rows.append({"strategy": strat, "note": "too few return days", **meta}); continue
        rets[strat] = r
        b = bm.reindex(r.index).fillna(0)
        g_ = qs.stats.greeks(r, b)
        rows.append({
            "strategy": strat, "start": str(r.index.min().date()), "end": str(r.index.max().date()), "days": len(r), **meta,
            "cagr_pct": float(qs.stats.cagr(r) * 100), "sharpe": float(qs.stats.sharpe(r)), "sortino": float(qs.stats.sortino(r)),
            "max_drawdown_pct": float(qs.stats.max_drawdown(r) * 100), "volatility_pct": float(qs.stats.volatility(r) * 100),
            "calmar": float(qs.stats.calmar(r)), "win_month_pct": float(qs.stats.win_rate(r, aggregate="M") * 100) if hasattr(qs.stats, "win_rate") else np.nan,
            "best_month_pct": float(qs.stats.best(r, aggregate="M") * 100), "worst_month_pct": float(qs.stats.worst(r, aggregate="M") * 100),
            "kospi_cagr_pct": float(qs.stats.cagr(b) * 100), "alpha_annual_pct": float(g_["alpha"] * 100), "beta": float(g_["beta"]),
            "excess_cagr_pct": float((qs.stats.cagr(r) - qs.stats.cagr(b)) * 100),
        })
    s = pd.DataFrame(rows)
    s.to_csv(OUT / "quantstats_summary_20260924.csv", index=False)
    (OUT / "quantstats_summary_20260924.json").write_text(s.to_json(orient="records", force_ascii=False, indent=1))
    top = s.dropna(subset=["sharpe"]).sort_values("sharpe", ascending=False).head(3).strategy.tolist()
    for strat in top:
        qs.reports.html(rets[strat], benchmark=bm.reindex(rets[strat].index).fillna(0),
                        output=str(OUT / "quantstats" / f"{strat}_vs_kospi_20260924.html"), title=f"{strat} vs KOSPI", download_filename=None)
    print(s.sort_values("sharpe", ascending=False)[["strategy", "days", "engine_periods", "reconstructed_periods", "cagr_pct", "sharpe", "sortino",
                                                     "max_drawdown_pct", "kospi_cagr_pct", "alpha_annual_pct", "beta"]].round(2).to_string(index=False))


if __name__ == "__main__":
    main()
