#!/usr/bin/env python3
"""HANDOFF §12 R4 inputs (operating venv, DB read-only): monthly candidate alpha factors at the snapshot dates of factors.parquet.

All values use only data known on the snapshot date (short/credit: that day's print; consensus: reports dated <= snapshot;
SUE: quarterly results after the filing lag Q1-Q3 45d / Q4 90d). Output: data_cache/research/alpha_sources.parquet
  short_ratio_20d   sum(short_qty,20d)/sum(volume,20d)          short_ratio_chg   change vs 20 trading days earlier
  borrow_pct        borrow_bal_pct (대차잔고 비율)                 borrow_chg_20d    change vs 20 trading days earlier
  credit_ratio      credit_ratio (신용잔고율)                      credit_chg_20d    credit_balance_amt / 20d earlier - 1
  cons_rev_60d      mean(target/prev_target - 1) of reports in the last 60d (>=2 reports)   cons_breadth_60d (#up - #down)/#reports
  cons_upside       mean target price of the last 60d / close - 1
  sue_ni / sue_op   (Q - Q[-4]) / std of the last 8 available (Q - Q[-4]) diffs, consolidated preferred
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

IN = ROOT / "data_cache" / "research"
conn = connect_primary_db(readonly=True, timeout=900)
snap = pd.read_parquet(IN / "factors.parquet")[["snapshot_date", "stock_code"]].copy()
snap["snapshot_date"] = pd.to_datetime(snap.snapshot_date)
dates = sorted(snap.snapshot_date.unique())
vol = pd.read_parquet(IN / "volume.parquet").astype(float)
vol.index = pd.to_datetime(vol.index)
close = pd.read_parquet(IN / "adj_close.parquet").astype(float)
close.index = pd.to_datetime(close.index)


def wide(sql, val, key="d") -> pd.DataFrame:
    df = pd.DataFrame([tuple(r) for r in conn.execute(sql).fetchall()], columns=["d", "stock_code", val])
    df["d"] = pd.to_datetime(df.d, format="%Y%m%d", errors="coerce")
    return df.dropna(subset=["d"]).pivot_table(index="d", columns="stock_code", values=val, aggfunc="last").sort_index()


out = snap.set_index(["snapshot_date", "stock_code"])
frames = {}

# ---- short selling / lending balance
sq = wide("SELECT bas_dt, stock_code, short_qty FROM short_sell_daily", "short_qty").reindex(vol.index)
v = vol.reindex(index=sq.index, columns=sq.columns)
ratio = sq.rolling(20, min_periods=15).sum() / v.rolling(20, min_periods=15).sum()
frames["short_ratio_20d"] = ratio
frames["short_ratio_chg"] = ratio - ratio.shift(20)
bp = wide("SELECT bas_dt, stock_code, borrow_bal_pct FROM short_sell_daily", "b").reindex(vol.index)
frames["borrow_pct"] = bp
frames["borrow_chg_20d"] = bp - bp.shift(20)

# ---- credit balance
cr = wide("SELECT dt, stock_code, credit_ratio FROM kiwoom_credit_balance", "c")
ca = wide("SELECT dt, stock_code, credit_balance_amt FROM kiwoom_credit_balance", "a")
cr, ca = cr.reindex(vol.index).ffill(limit=3), ca.reindex(vol.index).ffill(limit=3)
frames["credit_ratio"] = cr
frames["credit_chg_20d"] = ca / ca.shift(20) - 1

res = {}
for name, w in frames.items():
    w = w.reindex(dates, method="ffill", tolerance=pd.Timedelta(days=5))
    res[name] = w.stack(future_stack=True).rename(name)
factors = pd.concat(res, axis=1)
factors.index.names = ["snapshot_date", "stock_code"]

# ---- consensus revisions (reports dated <= snapshot)
ct = pd.DataFrame([tuple(r) for r in conn.execute(
    "SELECT stock_code, report_date, target_price, prev_target_price FROM consensus_targets WHERE target_price>0").fetchall()],
    columns=["stock_code", "report_date", "tp", "prev"])
ct["report_date"] = pd.to_datetime(ct.report_date, errors="coerce")
ct = ct.dropna(subset=["report_date"])
ct["rev"] = np.where(ct.prev > 0, ct.tp / ct.prev - 1, np.nan)
ct["rev"] = ct.rev.clip(-0.5, 0.5)
rows = []
for d in dates:
    w = ct[(ct.report_date <= d) & (ct.report_date > d - pd.Timedelta(days=60))]
    g = w.groupby("stock_code")
    m = pd.DataFrame({"n": g.size(), "rev": g.rev.mean(), "up": g.rev.apply(lambda s: (s > 0).sum()), "dn": g.rev.apply(lambda s: (s < 0).sum()),
                      "tp": g.tp.mean()})
    m = m[m.n >= 2]
    if not len(m):
        continue
    px = close.reindex([d], method="ffill").iloc[0]
    m["cons_rev_60d"] = m.rev
    m["cons_breadth_60d"] = (m.up - m.dn) / m.n
    m["cons_upside"] = m.tp / px.reindex(m.index).values - 1
    m["snapshot_date"] = d
    rows.append(m.reset_index()[["snapshot_date", "stock_code", "cons_rev_60d", "cons_breadth_60d", "cons_upside"]])
cons = pd.concat(rows).set_index(["snapshot_date", "stock_code"]) if rows else pd.DataFrame()

# ---- standardized unexpected earnings (SUE)
fd = pd.DataFrame([tuple(r) for r in conn.execute(
    "SELECT stock_code, year, quarter, report_type, net_income, operating_profit FROM financial_data "
    "WHERE is_annual IS FALSE AND quarter BETWEEN 1 AND 4 AND year>=2016").fetchall()],
    columns=["stock_code", "year", "quarter", "rt", "ni", "op"])
fd["prio"] = (fd.rt != "CFS").astype(int)
fd = fd.sort_values(["stock_code", "year", "quarter", "prio"]).drop_duplicates(["stock_code", "year", "quarter"])
fd["qidx"] = fd.year * 4 + fd.quarter
fd["avail"] = pd.to_datetime(fd.year.astype(str) + "-" + (fd.quarter * 3).astype(int).map("{:02d}".format) + "-01") + pd.offsets.MonthEnd(0) \
    + pd.to_timedelta(np.where(fd.quarter == 4, 90, 45), unit="D")
sue_rows = []
for code, g in fd.groupby("stock_code"):
    g = g.set_index("qidx").sort_index()
    full = g.reindex(range(g.index.min(), g.index.max() + 1))
    for col, name in (("ni", "sue_ni"), ("op", "sue_op")):
        diff = full[col] - full[col].shift(4)
        sd = diff.shift(1).rolling(8, min_periods=5).std()
        full[name] = diff / sd.replace(0, np.nan)
    full["avail"] = full.avail.fillna(g.avail.reindex(full.index).ffill())
    sue_rows.append(full.assign(stock_code=code)[["stock_code", "avail", "sue_ni", "sue_op"]].dropna(subset=["avail"]))
sue = pd.concat(sue_rows)
sue["avail"] = pd.to_datetime(sue.avail)
sue = sue.dropna(subset=["sue_ni", "sue_op"], how="all").sort_values("avail")
snap_keys = snap.rename(columns={"snapshot_date": "avail"}).sort_values("avail")
sue_at = pd.merge_asof(snap_keys, sue, on="avail", by="stock_code", direction="backward", tolerance=pd.Timedelta(days=130))
sue_at = sue_at.rename(columns={"avail": "snapshot_date"}).set_index(["snapshot_date", "stock_code"])[["sue_ni", "sue_op"]]

final = factors.join(cons, how="outer").join(sue_at, how="outer").reset_index()
for c in [c for c in final.columns if c not in ("snapshot_date", "stock_code")]:
    final[c] = final[c].replace([np.inf, -np.inf], np.nan)
final.to_parquet(IN / "alpha_sources.parquet")
print(final.shape)
print(final.drop(columns=["snapshot_date", "stock_code"]).notna().mean().round(3).to_dict())
print(final.groupby(final.snapshot_date.dt.year).cons_rev_60d.apply(lambda s: s.notna().sum()).to_dict())
