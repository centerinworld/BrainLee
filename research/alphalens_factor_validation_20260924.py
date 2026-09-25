#!/usr/bin/env python3
"""Factor validation with alphalens-reloaded (run in the RESEARCH venv: ../research_venv/bin/python).

Inputs come from research/extract_research_inputs_20260924.py (prod venv). Price-derived factors are recomputed
from the corrected, jump-adjusted close (the stored snapshot predates the 2026-09 price repairs); non-price
factors (PER/PBR/size/supply/heuristic/model scores) are taken point-in-time from strategy_feature_snapshot_pit_v2.
Forward returns (20/60/120 trading days) come from the same adjusted close, so corporate-action jumps do not
leak into IC. Output: research_outputs/alphalens_factor_validation_20260924.{json,csv,md}.
model_score_* are reported but flagged: their training window is unknown here, so they may be in-sample.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import alphalens as al

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "data_cache" / "research"
OUT = ROOT / "research_outputs"
PERIODS = (20, 60, 120)


def build_factors(adj: pd.DataFrame, vol: pd.DataFrame, snap: pd.DataFrame) -> dict[str, pd.Series]:
    ret = adj.pct_change(fill_method=None)
    f = {}
    f["mom_20d"] = adj / adj.shift(20) - 1
    f["mom_60d"] = adj / adj.shift(60) - 1
    f["mom_120d"] = adj / adj.shift(120) - 1
    f["mom_252d_ex_1m"] = adj.shift(21) / adj.shift(252) - 1
    f["dist_high_252"] = adj / adj.rolling(252, min_periods=200).max() - 1
    f["low_vol_60d"] = -ret.rolling(60, min_periods=40).std()
    turn = (adj * vol)
    f["turnover_surge_20_120"] = np.log(turn.rolling(20, min_periods=15).mean() / turn.rolling(120, min_periods=80).mean())
    wide = {}
    for name, fac in f.items():
        wide[name] = fac
    # point-in-time snapshot factors (one row per month-end x stock)
    snap = snap.copy()
    snap["dt"] = pd.to_datetime(snap["snapshot_date"])
    snap["earn_yield"] = np.where(snap.per > 0, 1 / snap.per, np.nan)
    snap["book_yield"] = np.where(snap.pbr > 0, 1 / snap.pbr, np.nan)
    snap["small_size"] = -snap["market_cap_log"]
    for col in ["earn_yield", "book_yield", "small_size", "supply_20d_억", "heuristic_score", "model_score_6m", "model_score_12m"]:
        wide[col] = snap.pivot_table(index="dt", columns="stock_code", values=col)
    dates = pd.DatetimeIndex(sorted(snap["dt"].unique()))
    idx = adj.index
    out = {}
    for name, w in wide.items():
        rows = []
        for d in dates:
            pos = idx.searchsorted(d, side="right") - 1  # last trading day on/before the snapshot date
            if pos < 0:
                continue
            td = idx[pos]
            src = w.loc[d] if name in ("earn_yield", "book_yield", "small_size", "supply_20d_억", "heuristic_score",
                                       "model_score_6m", "model_score_12m") else w.loc[td]
            s = src.dropna()
            s.index = pd.MultiIndex.from_product([[td], s.index], names=["date", "asset"])
            rows.append(s)
        out[name] = pd.concat(rows) if rows else pd.Series(dtype=float)
    return out


ELIGIBLE = True


def main() -> None:
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet")
    factors = build_factors(adj, vol, snap)
    # alphalens infers a business-day calendar; KRX holidays make the index irregular, so lay prices on a plain
    # weekday grid (holidays carry the last close = 0 return; horizons are therefore in weekdays, ~= trading days).
    # eligibility: drop stock-days with >5 zero-volume days in the last 60 (suspended / dormant names would
    # look 'low-vol' with zero forward return and distort ICs)
    zero = (vol.fillna(0) == 0).rolling(60, min_periods=30).sum()
    ok = (zero <= 5)
    prices = adj.dropna(how="all")
    prices = prices.reindex(pd.bdate_range(prices.index.min(), prices.index.max())).ffill(limit=5)
    rows = []
    for name, fac in factors.items():
        fac = fac[~fac.index.duplicated()].replace([np.inf, -np.inf], np.nan).dropna()
        if ELIGIBLE:
            keep = [bool(ok.at[d, a]) if (d in ok.index and a in ok.columns) else False for d, a in fac.index]
            fac = fac[keep]
        try:
            data = al.utils.get_clean_factor_and_forward_returns(
                fac, prices, quantiles=5, periods=PERIODS, max_loss=0.6, filter_zscore=None)
        except Exception as exc:  # noqa: BLE001
            rows.append({"factor": name, "error": repr(exc)[:160]})
            continue
        ic = al.performance.factor_information_coefficient(data)
        qret, _ = al.performance.mean_return_by_quantile(data, by_date=False)
        for p in [f"{x}D" for x in PERIODS]:
            s = ic[p].dropna()
            step = {"20D": 1, "60D": 3, "120D": 6}[p]  # non-overlapping horizon windows (monthly snapshots)
            s_no = s.iloc[::step]
            spread = qret[p].iloc[-1] - qret[p].iloc[0]
            rows.append({"factor": name, "horizon": p, "n_dates": int(len(s)), "n_obs": int(len(data)),
                         "ic_mean": float(s.mean()), "ic_std": float(s.std()),
                         "ic_ir": float(s.mean() / s.std()) if s.std() else np.nan,
                         "ic_tstat": float(s.mean() / (s.std() / np.sqrt(len(s)))) if s.std() and len(s) > 1 else np.nan,
                         "ic_pos_pct": float((s > 0).mean() * 100),
                         "ic_tstat_nonoverlap": float(s_no.mean() / (s_no.std() / np.sqrt(len(s_no)))) if s_no.std() and len(s_no) > 2 else np.nan,
                         "n_dates_nonoverlap": int(len(s_no)),
                         "q5_minus_q1_fwd_ret_pct": float(spread * 100),
                         "q1_ret_pct": float(qret[p].iloc[0] * 100), "q5_ret_pct": float(qret[p].iloc[-1] * 100),
                         "in_sample_risk": name.startswith("model_score")})
    df = pd.DataFrame(rows)
    OUT.mkdir(exist_ok=True)
    df.to_csv(OUT / "alphalens_factor_validation_20260924.csv", index=False)
    (OUT / "alphalens_factor_validation_20260924.json").write_text(df.to_json(orient="records", force_ascii=False, indent=1))
    show = df[df.get("horizon").isin(["60D"])].sort_values("ic_ir", ascending=False) if "horizon" in df else df
    print(show[["factor", "n_dates", "ic_mean", "ic_ir", "ic_tstat", "ic_tstat_nonoverlap", "ic_pos_pct", "q5_minus_q1_fwd_ret_pct"]].round(4).to_string(index=False))


if __name__ == "__main__":
    main()
