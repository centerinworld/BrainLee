#!/usr/bin/env python3
"""HANDOFF §10 P1-5 (research venv): (1) train/validation IC sign stability, (2) sector-neutral IC, (3) delisted names kept in the
forward returns, (4) disclosure-event study. Reuses factor construction from alphalens_factor_validation_20260924.py.
Outputs research_outputs/alphalens_split_events_20260925.{csv,json,md}."""
from __future__ import annotations

import importlib.util
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import alphalens as al
import sys as _sys
_sys.path.insert(0, str(Path(__file__).parent))
from alphalens_compat import tolerant_freq  # noqa: E402

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
IN, OUT = ROOT / "data_cache" / "research", ROOT / "research_outputs"
spec = importlib.util.spec_from_file_location("alv", Path(__file__).with_name("alphalens_factor_validation_20260924.py"))
alv = importlib.util.module_from_spec(spec); spec.loader.exec_module(alv)
SPLIT = pd.Timestamp("2024-12-31")
H = {"20D": 20, "60D": 60}


def build_prices(adj: pd.DataFrame):
    """Weekday grid; names whose series simply END (delisting) are carried at their last close instead of dropped, so the
    return to the last print (often a crash) stays in the forward returns. Short gaps elsewhere ffill up to 5 days."""
    last = adj.apply(lambda s: s.last_valid_index())
    delisted = set(last[last < adj.index.max() - pd.Timedelta(days=20)].index)
    p = adj.reindex(pd.bdate_range(adj.index.min(), adj.index.max()))
    p = p.ffill(limit=5)
    for c in delisted:
        p[c] = adj[c].reindex(p.index).ffill()
    return p, delisted


def ic_table(data, sector_neutral: bool):
    ic = al.performance.factor_information_coefficient(data, group_adjust=sector_neutral)
    return ic


def stats(s: pd.Series) -> dict:
    s = s.dropna()
    return {"n": int(len(s)), "ic": float(s.mean()) if len(s) else np.nan,
            "t": float(s.mean() / (s.std() / np.sqrt(len(s)))) if len(s) > 2 and s.std() else np.nan}


def main() -> None:
    adj = pd.read_parquet(IN / "adj_close.parquet").astype(float)
    vol = pd.read_parquet(IN / "volume.parquet").astype(float).reindex(adj.index)
    snap = pd.read_parquet(IN / "factors.parquet")
    prices, delisted = build_prices(adj)
    factors = alv.build_factors(adj, vol, snap)
    sector = snap.dropna(subset=["sector_large"]).drop_duplicates("stock_code", keep="last").set_index("stock_code").sector_large.to_dict()
    sector = {c: sector.get(c, "미분류") for c in adj.columns}   # every asset needs a group
    zero = (vol.fillna(0) == 0).rolling(60, min_periods=30).sum() <= 5
    rows = []
    for name, fac in factors.items():
        fac = fac[~fac.index.duplicated()].replace([np.inf, -np.inf], np.nan).dropna()
        fac = fac[[bool(zero.at[d, a]) if (d in zero.index and a in zero.columns) else False for d, a in fac.index]]
        try:
            with tolerant_freq():
                data = al.utils.get_clean_factor_and_forward_returns(fac, prices, quantiles=5, periods=(20, 60), max_loss=0.6,
                                                                groupby=sector, binning_by_group=False)
        except Exception as exc:  # noqa: BLE001
            rows.append({"factor": name, "error": repr(exc)[:120]}); continue
        for label, sn in (("raw", False), ("sector_neutral", True)):
            ic = ic_table(data, sn)
            for h in H:
                col = h
                s = ic[col]; tr, va = s[s.index <= SPLIT], s[s.index > SPLIT]
                a, b, allv = stats(tr), stats(va), stats(s)
                rows.append({"factor": name, "ic_kind": label, "horizon": col, "train_n": a["n"], "train_ic": a["ic"], "train_t": a["t"],
                             "valid_n": b["n"], "valid_ic": b["ic"], "valid_t": b["t"],
                             "sign_kept": bool(np.sign(a["ic"]) == np.sign(b["ic"])) if a["ic"] == a["ic"] and b["ic"] == b["ic"] else None,
                             "all_ic": allv["ic"], "all_t": allv["t"], "delisted_names": len(delisted)})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "alphalens_split_events_20260925.csv", index=False)

    # ---- event study (entry = close of the first trading day AFTER the disclosure date) ----
    ev = pd.read_parquet(IN / "events.parquet"); ev["date"] = pd.to_datetime(ev["date"])
    ev = ev[(ev.date >= prices.index.min()) & (ev.code.isin(prices.columns))]
    idx = prices.index
    fwd = {h: prices.shift(-n) / prices - 1 for h, n in H.items()}
    med = {h: fwd[h].median(axis=1) for h in H}
    res = []
    for kind, g in ev.groupby("kind"):
        for h in H:
            ex = []
            for code, d in zip(g.code, g.date):
                i = idx.searchsorted(d, side="right")            # next weekday after disclosure
                if i >= len(idx) - H[h]:
                    continue
                r = fwd[h].iat[i, fwd[h].columns.get_loc(code)]
                m = med[h].iat[i]
                if r == r and m == m:
                    ex.append((idx[i], r - m))
            if not ex:
                continue
            e = pd.DataFrame(ex, columns=["d", "x"])
            lo, hi = e.x.quantile(0.01), e.x.quantile(0.99)
            e["x"] = e.x.clip(lo, hi)                       # winsorise 1/99: single extreme prints (e.g. a re-listing) dominated means
            def st(x):
                return (float(x.mean() * 100), float(x.mean() / (x.std() / np.sqrt(len(x)))) if len(x) > 2 and x.std() else np.nan, len(x))
            a, b, c = st(e[e.d <= SPLIT].x), st(e[e.d > SPLIT].x), st(e.x)
            res.append({"kind": kind, "horizon": h, "n": c[2], "mean_excess_pct": c[0], "median_excess_pct": float(e.x.median() * 100), "t": c[1],
                        "train_n": a[2], "train_excess_pct": a[0], "train_t": a[1], "valid_n": b[2], "valid_excess_pct": b[0], "valid_t": b[1]})
    er = pd.DataFrame(res); er.to_csv(OUT / "event_study_20260925.csv", index=False)
    json.dump({"factors": df.to_dict("records"), "events": er.to_dict("records")}, open(OUT / "alphalens_split_events_20260925.json", "w"), ensure_ascii=False, default=float)
    pd.set_option("display.width", 220)
    show = df[(df.horizon == "60D") & df.ic_kind.isin(["raw", "sector_neutral"])].pivot_table(index="factor", columns="ic_kind", values=["train_ic", "valid_ic"]).round(3)
    print(show.to_string()); print(); print(er[er.horizon == "60D"].round(2).to_string(index=False))
    print("delisted names carried:", len(delisted))


if __name__ == "__main__":
    main()
