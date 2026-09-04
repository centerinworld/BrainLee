#!/usr/bin/env python3
"""
Discover characteristics of stocks that later beat the market by 2x.

This is signal discovery, not portfolio construction.  The unit is a stock at a
month-end signal date.  We label whether the stock later achieves at least 2x
the equal-weight market return over 3/6/12 months, then measure which
observable pre-signal conditions raise that probability.
"""

from __future__ import annotations

import json
import sqlite3
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"
HS_DB_PATH = ROOT / "hs_trade_lab/data/hs_trade_lab.db"
OUT_DIR = ROOT / "research_outputs"
START = "2020-01-01"
SIGNAL_START = "2021-01-01"
END = "2026-06-18"


def conn(path: Path = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=60)
    c.row_factory = sqlite3.Row
    return c


def load_prices() -> pd.DataFrame:
    sql = """
    SELECT
      p.stock_code, p.date, p.open, p.high, p.low, p.close, p.volume,
      COALESCE(NULLIF(p.trade_amount, 0), p.close * p.volume) AS turnover,
      CASE
        WHEN COALESCE(p.inst_net_buy_amt, 0) != 0 THEN p.inst_net_buy_amt
        ELSE COALESCE(p.inst_net_buy, 0) * p.close / 1000000.0
      END AS inst_amt,
      CASE
        WHEN COALESCE(p.frn_net_buy_amt, 0) != 0 THEN p.frn_net_buy_amt
        ELSE COALESCE(p.frn_net_buy, 0) * p.close / 1000000.0
      END AS frn_amt,
      COALESCE(su.stock_name, sm.stock_name) AS stock_name,
      COALESCE(su.market, sm.market) AS market,
      su.sector_large, su.market_cap
    FROM price_history p
    LEFT JOIN stock_universe su ON su.stock_code = p.stock_code
    LEFT JOIN stock_meta sm ON sm.stock_code = p.stock_code
    WHERE p.date BETWEEN ? AND ?
      AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND p.close > 0
      AND p.volume > 0
      AND COALESCE(su.market, sm.market) IN ('KOSPI', 'KOSDAQ')
    """
    df = pd.read_sql_query(sql, conn(), params=(START, END), parse_dates=["date"])
    df = df.sort_values(["stock_code", "date"]).reset_index(drop=True)
    df["ret_d"] = df.groupby("stock_code")["close"].pct_change()
    bad_codes = set(df.loc[df["ret_d"].abs() > 0.60, "stock_code"].unique())
    return df[~df["stock_code"].isin(bad_codes)].copy()


def load_short_monthly() -> pd.DataFrame:
    sql = """
    SELECT stock_code, bas_dt, lnb_bal AS borrow_bal_amt, lnb_rman_stck_cnt AS borrow_bal_qty
    FROM short_rank_daily
    WHERE bas_dt BETWEEN '20200101' AND '20260630'
      AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND lnb_bal IS NOT NULL
    """
    s = pd.read_sql_query(sql, conn())
    if s.empty:
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    s["date"] = pd.to_datetime(s["bas_dt"], format="%Y%m%d", errors="coerce")
    s = s.dropna(subset=["date"]).sort_values(["stock_code", "date"])
    s["signal_month"] = s["date"].dt.to_period("M").astype(str)
    idx = s.groupby(["stock_code", "signal_month"])["date"].idxmax()
    m = s.loc[idx, ["stock_code", "signal_month", "borrow_bal_amt", "borrow_bal_qty"]].copy()
    g = m.sort_values(["stock_code", "signal_month"]).groupby("stock_code")["borrow_bal_amt"]
    m["short_cover_1m"] = -(m["borrow_bal_amt"] / g.shift(1) - 1)
    m["short_cover_3m"] = -(m["borrow_bal_amt"] / g.shift(3) - 1)
    return m


def load_export_monthly() -> pd.DataFrame:
    if not HS_DB_PATH.exists():
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    sql = """
    SELECT stock_code, period_ym, SUM(export_value) AS export_value, SUM(import_value) AS import_value
    FROM analysis2_company_hs_monthly_cache
    WHERE mapping_status = 'exact'
      AND stock_code IS NOT NULL
    GROUP BY stock_code, period_ym
    """
    x = pd.read_sql_query(sql, conn(HS_DB_PATH))
    if x.empty:
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    x = x.sort_values(["stock_code", "period_ym"])
    g_exp = x.groupby("stock_code")["export_value"]
    g_imp = x.groupby("stock_code")["import_value"]
    x["export_yoy"] = x["export_value"] / g_exp.shift(12) - 1
    x["export_mom3"] = x["export_value"] / g_exp.shift(3) - 1
    x["import_yoy"] = x["import_value"] / g_imp.shift(12) - 1
    # Assume monthly customs data becomes safely usable with a 2-month delay.
    x["signal_month"] = (pd.PeriodIndex(x["period_ym"], freq="M") + 2).astype(str)
    return x[["stock_code", "signal_month", "export_value", "import_value", "export_yoy", "export_mom3", "import_yoy"]]


def load_event_features() -> pd.DataFrame:
    con = conn()
    events = []
    for table, date_col, prefix in [
        ("dart_contracts", "disclosed_at", "contract"),
        ("earnings_signals", "detected_at", "earnings"),
        ("dart_insider_holdings", "rcept_dt", "insider"),
    ]:
        try:
            df = pd.read_sql_query(f'SELECT * FROM "{table}"', con)
        except Exception:
            continue
        if df.empty or date_col not in df.columns or "stock_code" not in df.columns:
            continue
        df["event_date"] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=["event_date"])
        df["signal_month"] = df["event_date"].dt.to_period("M").astype(str)
        if table == "dart_contracts":
            agg = df.groupby(["stock_code", "signal_month"]).agg(
                contract_cnt=("stock_code", "size"),
                contract_amount_krw=("contract_amount_krw", "sum"),
                contract_signal_strength=("signal_strength", "max"),
            ).reset_index()
        elif table == "earnings_signals":
            agg = df.groupby(["stock_code", "signal_month"]).agg(
                earnings_signal_cnt=("stock_code", "size"),
                ttm_rev_yoy_pct=("ttm_rev_yoy_pct", "max"),
                ttm_op_accel_pct=("ttm_op_accel_pct", "max"),
            ).reset_index()
        else:
            if "change_amount" in df.columns:
                df["insider_buy_flag"] = pd.to_numeric(df["change_amount"], errors="coerce").fillna(0) > 0
            else:
                df["insider_buy_flag"] = False
            agg = df.groupby(["stock_code", "signal_month"]).agg(
                insider_event_cnt=("stock_code", "size"),
                insider_buy_cnt=("insider_buy_flag", "sum"),
            ).reset_index()
        events.append(agg)
    con.close()
    if not events:
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    out = events[0]
    for e in events[1:]:
        out = out.merge(e, on=["stock_code", "signal_month"], how="outer")
    return out


def load_employment_features() -> pd.DataFrame:
    emp_path = ROOT / "employment_monitor/employment.db"
    if not emp_path.exists():
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    c = conn(emp_path)
    pieces = []
    try:
        nps = pd.read_sql_query(
            "SELECT stock_code, data_ym, new_hires, terminations, net_change FROM nps_monthly",
            c,
        )
        if not nps.empty:
            nps = nps.sort_values(["stock_code", "data_ym"])
            for col in ["new_hires", "terminations", "net_change"]:
                nps[col] = pd.to_numeric(nps[col], errors="coerce")
            g = nps.groupby("stock_code")
            nps["nps_net_3m"] = g["net_change"].transform(lambda s: s.rolling(3, min_periods=2).sum())
            nps["nps_hires_3m"] = g["new_hires"].transform(lambda s: s.rolling(3, min_periods=2).sum())
            nps["nps_terms_3m"] = g["terminations"].transform(lambda s: s.rolling(3, min_periods=2).sum())
            nps["signal_month"] = (pd.PeriodIndex(nps["data_ym"], freq="M") + 1).astype(str)
            pieces.append(nps[["stock_code", "signal_month", "nps_net_3m", "nps_hires_3m", "nps_terms_3m"]])

        wlb = pd.read_sql_query(
            "SELECT stock_code, data_ym, total_workers, workplace_cnt FROM wlb_monthly",
            c,
        )
        if not wlb.empty:
            wlb = wlb.sort_values(["stock_code", "data_ym"])
            wlb["total_workers"] = pd.to_numeric(wlb["total_workers"], errors="coerce")
            g = wlb.groupby("stock_code")
            wlb["wlb_workers_mom"] = wlb["total_workers"] / g["total_workers"].shift(1) - 1
            wlb["wlb_workers_3m"] = wlb["total_workers"] / g["total_workers"].shift(3) - 1
            wlb["signal_month"] = (pd.PeriodIndex(wlb["data_ym"], freq="M") + 1).astype(str)
            pieces.append(wlb[["stock_code", "signal_month", "total_workers", "wlb_workers_mom", "wlb_workers_3m"]])

        ec = pd.read_sql_query(
            "SELECT stock_code, ym, worker_count, yoy_change, mom_change FROM employment_company",
            c,
        )
        if not ec.empty:
            ec = ec.sort_values(["stock_code", "ym"])
            ec["worker_count"] = pd.to_numeric(ec["worker_count"], errors="coerce")
            ec["employment_yoy_change"] = pd.to_numeric(ec["yoy_change"], errors="coerce")
            ec["employment_mom_change"] = pd.to_numeric(ec["mom_change"], errors="coerce")
            ec["signal_month"] = (pd.PeriodIndex(ec["ym"], freq="M") + 1).astype(str)
            pieces.append(ec[["stock_code", "signal_month", "worker_count", "employment_yoy_change", "employment_mom_change"]])
    finally:
        c.close()
    if not pieces:
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    out = pieces[0]
    for p in pieces[1:]:
        out = out.merge(p, on=["stock_code", "signal_month"], how="outer")
    return out


def quarter_signal_month(year: pd.Series, quarter: pd.Series, lag_months: int = 2) -> pd.Series:
    y = pd.to_numeric(year, errors="coerce").astype("Int64")
    q = pd.to_numeric(quarter, errors="coerce").astype("Int64")
    period = pd.PeriodIndex(
        [f"{yy}Q{qq}" if pd.notna(yy) and pd.notna(qq) and 1 <= int(qq) <= 4 else None for yy, qq in zip(y, q)],
        freq="Q-DEC",
    )
    return (period.asfreq("M", "end") + lag_months).astype(str)


def clean_ratio(s: pd.Series, low: float = -10.0, high: float = 20.0) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce").replace([np.inf, -np.inf], np.nan)
    return x.where(x.between(low, high))


def merge_point_features(base: pd.DataFrame, extra: pd.DataFrame) -> pd.DataFrame:
    if extra.empty:
        return base
    return base.merge(extra, on=["stock_code", "signal_month"], how="left")


def merge_asof_features(base: pd.DataFrame, extra: pd.DataFrame, max_age_months: int) -> pd.DataFrame:
    """Attach the latest known low-frequency disclosure to each monthly signal."""
    if extra.empty:
        return base
    extra = extra.dropna(subset=["stock_code", "signal_month"]).copy()
    if extra.empty:
        return base
    extra["_signal_ord"] = pd.PeriodIndex(extra["signal_month"], freq="M").astype("int64")
    extra = extra.sort_values(["stock_code", "_signal_ord"]).drop_duplicates(
        ["stock_code", "_signal_ord"], keep="last"
    )
    value_cols = [c for c in extra.columns if c not in {"stock_code", "signal_month", "_signal_ord"}]
    parts = []
    for stock_code, left in base.groupby("stock_code", sort=False):
        right = extra[extra["stock_code"] == stock_code]
        if right.empty:
            parts.append(left)
            continue
        left2 = left.copy()
        left2["_signal_ord"] = pd.PeriodIndex(left2["signal_month"], freq="M").astype("int64")
        merged = pd.merge_asof(
            left2.sort_values("_signal_ord"),
            right[["_signal_ord", *value_cols]].sort_values("_signal_ord"),
            on="_signal_ord",
            direction="backward",
        )
        # Keep the source disclosure month so old fundamentals do not leak too far.
        right_vals = right[["_signal_ord", *value_cols]].copy()
        right_vals["_source_signal_ord"] = right_vals["_signal_ord"]
        merged = pd.merge_asof(
            left2.sort_values("_signal_ord"),
            right_vals.sort_values("_signal_ord"),
            on="_signal_ord",
            direction="backward",
        )
        stale = (merged["_signal_ord"] - merged["_source_signal_ord"]) > max_age_months
        for col in value_cols:
            merged.loc[stale, col] = np.nan
        merged = merged.drop(columns=["_signal_ord", "_source_signal_ord"])
        parts.append(merged.sort_index())
    return pd.concat(parts, ignore_index=True)


def load_financial_features() -> pd.DataFrame:
    sql = """
    SELECT stock_code, year, quarter, revenue, operating_profit, net_income,
           total_liabilities, total_equity, roe
    FROM financial_data
    WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND quarter BETWEEN 1 AND 4
      AND COALESCE(is_annual, 0) = 0
    """
    f = pd.read_sql_query(sql, conn())
    if f.empty:
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    for col in ["revenue", "operating_profit", "net_income", "total_liabilities", "total_equity", "roe"]:
        f[col] = pd.to_numeric(f[col], errors="coerce")
    f["signal_month"] = quarter_signal_month(f["year"], f["quarter"], lag_months=2)
    f = f.replace({"signal_month": {"NaT": np.nan}}).dropna(subset=["signal_month"])
    f = f.sort_values(["stock_code", "year", "quarter"])
    g = f.groupby("stock_code", group_keys=False)
    f["fin_rev_yoy"] = clean_ratio(f["revenue"] / g["revenue"].shift(4) - 1)
    f["fin_op_yoy"] = clean_ratio(f["operating_profit"] / g["operating_profit"].shift(4) - 1)
    f["fin_net_yoy"] = clean_ratio(f["net_income"] / g["net_income"].shift(4) - 1)
    f["fin_op_margin"] = clean_ratio(f["operating_profit"] / f["revenue"], -2, 2)
    f["fin_net_margin"] = clean_ratio(f["net_income"] / f["revenue"], -2, 2)
    f["fin_debt_ratio"] = clean_ratio(f["total_liabilities"] / f["total_equity"], 0, 20)
    f["fin_roe"] = clean_ratio(f["roe"], -200, 300)
    f["fin_rev_accel"] = clean_ratio(f["fin_rev_yoy"] - g["fin_rev_yoy"].shift(1))
    f["fin_op_turnaround"] = ((f["operating_profit"] > 0) & (g["operating_profit"].shift(4) <= 0)).astype(float)
    keep = [
        "stock_code", "signal_month", "fin_rev_yoy", "fin_op_yoy", "fin_net_yoy",
        "fin_op_margin", "fin_net_margin", "fin_debt_ratio", "fin_roe",
        "fin_rev_accel", "fin_op_turnaround",
    ]
    return f[keep].drop_duplicates(["stock_code", "signal_month"], keep="last")


def load_cashflow_features() -> pd.DataFrame:
    sql = """
    SELECT cf.stock_code, cf.year, cf.quarter, cf.operating_cf_q, cf.capex_q, fd.revenue
    FROM cash_flow_data cf
    LEFT JOIN financial_data fd
      ON fd.stock_code = cf.stock_code
     AND fd.year = cf.year
     AND fd.quarter = cf.quarter
     AND COALESCE(fd.is_annual, 0) = 0
    WHERE cf.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND cf.quarter BETWEEN 1 AND 4
    """
    cf = pd.read_sql_query(sql, conn())
    if cf.empty:
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    for col in ["operating_cf_q", "capex_q", "revenue"]:
        cf[col] = pd.to_numeric(cf[col], errors="coerce")
    cf["signal_month"] = quarter_signal_month(cf["year"], cf["quarter"], lag_months=2)
    cf = cf.replace({"signal_month": {"NaT": np.nan}}).dropna(subset=["signal_month"])
    cf = cf.sort_values(["stock_code", "year", "quarter"])
    g = cf.groupby("stock_code", group_keys=False)
    cf["cf_ocf_yoy"] = clean_ratio(cf["operating_cf_q"] / g["operating_cf_q"].shift(4) - 1)
    cf["cf_capex_yoy"] = clean_ratio(cf["capex_q"].abs() / g["capex_q"].shift(4).abs() - 1)
    cf["cf_ocf_margin"] = clean_ratio(cf["operating_cf_q"] / cf["revenue"], -5, 5)
    cf["cf_fcf"] = cf["operating_cf_q"] - cf["capex_q"].abs()
    cf["cf_fcf_margin"] = clean_ratio(cf["cf_fcf"] / cf["revenue"], -5, 5)
    keep = ["stock_code", "signal_month", "cf_ocf_yoy", "cf_capex_yoy", "cf_ocf_margin", "cf_fcf_margin"]
    return cf[keep].drop_duplicates(["stock_code", "signal_month"], keep="last")


def load_backlog_features() -> pd.DataFrame:
    sql = """
    SELECT stock_code, year, quarter, backlog_amount, new_orders, revenue_base,
           backlog_to_rev, new_order_amount, completion_ratio
    FROM order_backlog
    WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND quarter BETWEEN 1 AND 4
    """
    b = pd.read_sql_query(sql, conn())
    if b.empty:
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    for col in ["backlog_amount", "new_orders", "revenue_base", "backlog_to_rev", "new_order_amount", "completion_ratio"]:
        b[col] = pd.to_numeric(b[col], errors="coerce")
    b["signal_month"] = quarter_signal_month(b["year"], b["quarter"], lag_months=2)
    b = b.replace({"signal_month": {"NaT": np.nan}}).dropna(subset=["signal_month"])
    b = b.sort_values(["stock_code", "year", "quarter"])
    g = b.groupby("stock_code", group_keys=False)
    b["backlog_to_rev"] = clean_ratio(b["backlog_to_rev"], 0, 30)
    b["backlog_yoy"] = clean_ratio(b["backlog_amount"] / g["backlog_amount"].shift(4) - 1)
    b["new_order_yoy"] = clean_ratio(b["new_order_amount"] / g["new_order_amount"].shift(4) - 1)
    b["backlog_present"] = (b["backlog_amount"] > 0).astype(float)
    keep = ["stock_code", "signal_month", "backlog_to_rev", "backlog_yoy", "new_order_yoy", "completion_ratio", "backlog_present"]
    return b[keep].drop_duplicates(["stock_code", "signal_month"], keep="last")


def load_material_features() -> pd.DataFrame:
    c = conn()
    pieces = []

    cost = pd.read_sql_query(
        """
        SELECT stock_code, year, quarter, raw_material_cost, total_cogs, revenue,
               raw_material_ratio, cogs_ratio, yoy_raw_material_chg
        FROM cost_structure
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND quarter BETWEEN 1 AND 4
        """,
        c,
    )
    if not cost.empty:
        for col in ["raw_material_cost", "total_cogs", "revenue", "raw_material_ratio", "cogs_ratio", "yoy_raw_material_chg"]:
            cost[col] = pd.to_numeric(cost[col], errors="coerce")
        cost.loc[cost["raw_material_cost"].between(-1e6, 1e6), "raw_material_cost"] = np.nan
        cost["signal_month"] = quarter_signal_month(cost["year"], cost["quarter"], lag_months=2)
        cost = cost.replace({"signal_month": {"NaT": np.nan}}).dropna(subset=["signal_month"])
        cost = cost.sort_values(["stock_code", "year", "quarter"])
        g = cost.groupby("stock_code", group_keys=False)
        cost["raw_material_ratio"] = clean_ratio(cost["raw_material_ratio"], 0, 2)
        cost["cogs_ratio"] = clean_ratio(cost["cogs_ratio"], 0, 2)
        cost["raw_material_cost_yoy"] = clean_ratio(cost["raw_material_cost"] / g["raw_material_cost"].shift(4) - 1)
        cost["yoy_raw_material_chg"] = clean_ratio(cost["yoy_raw_material_chg"])
        pieces.append(cost[[
            "stock_code", "signal_month", "raw_material_ratio", "cogs_ratio",
            "raw_material_cost_yoy", "yoy_raw_material_chg",
        ]])

    dart = pd.read_sql_query(
        """
        SELECT dc.stock_code, dc.fiscal_year AS year, dc.fiscal_quarter AS quarter,
               dc.material_cost_krw, dc.confidence, fd.revenue
        FROM dart_cost_quarterly dc
        LEFT JOIN financial_data fd
          ON fd.stock_code = dc.stock_code
         AND fd.year = dc.fiscal_year
         AND fd.quarter = dc.fiscal_quarter
         AND COALESCE(fd.is_annual, 0) = 0
        WHERE dc.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND dc.fiscal_quarter BETWEEN 1 AND 4
          AND dc.material_cost_krw IS NOT NULL
          AND dc.confidence >= 0.65
        """,
        c,
    )
    if not dart.empty:
        for col in ["material_cost_krw", "confidence", "revenue"]:
            dart[col] = pd.to_numeric(dart[col], errors="coerce")
        # Parser failures often capture the fiscal year (2024, 2025, 2026) as the amount.
        dart = dart[dart["material_cost_krw"] >= 1e7].copy()
        dart["signal_month"] = quarter_signal_month(dart["year"], dart["quarter"], lag_months=2)
        dart = dart.replace({"signal_month": {"NaT": np.nan}}).dropna(subset=["signal_month"])
        dart = dart.sort_values(["stock_code", "year", "quarter", "confidence"])
        dart = dart.drop_duplicates(["stock_code", "year", "quarter"], keep="last")
        g = dart.groupby("stock_code", group_keys=False)
        dart["dart_material_yoy"] = clean_ratio(dart["material_cost_krw"] / g["material_cost_krw"].shift(4) - 1)
        dart["dart_material_to_rev"] = clean_ratio(dart["material_cost_krw"] / dart["revenue"], 0, 5)
        pieces.append(dart[["stock_code", "signal_month", "dart_material_yoy", "dart_material_to_rev"]])

    annual = pd.read_sql_query(
        """
        SELECT stock_code, year, material_purchase_krw
        FROM dart_material_purchase
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND material_purchase_krw IS NOT NULL
        """,
        c,
    )
    c.close()
    if not annual.empty:
        annual["material_purchase_krw"] = pd.to_numeric(annual["material_purchase_krw"], errors="coerce")
        annual = annual[annual["material_purchase_krw"] >= 1e7].copy()
        annual = annual.sort_values(["stock_code", "year"])
        g = annual.groupby("stock_code", group_keys=False)
        annual["annual_material_yoy"] = clean_ratio(annual["material_purchase_krw"] / g["material_purchase_krw"].shift(1) - 1)
        annual["signal_month"] = (pd.PeriodIndex(annual["year"].astype(str) + "-12", freq="M") + 4).astype(str)
        pieces.append(annual[["stock_code", "signal_month", "annual_material_yoy"]])

    if not pieces:
        return pd.DataFrame(columns=["stock_code", "signal_month"])
    out = pieces[0]
    for p in pieces[1:]:
        out = out.merge(p, on=["stock_code", "signal_month"], how="outer")
    out = out.drop_duplicates(["stock_code", "signal_month"], keep="last")
    out["_signal_ord"] = pd.PeriodIndex(out["signal_month"], freq="M").astype("int64")
    out = out.sort_values(["stock_code", "_signal_ord"])
    value_cols = [c for c in out.columns if c not in {"stock_code", "signal_month", "_signal_ord"}]
    out[value_cols] = out.groupby("stock_code", group_keys=False)[value_cols].ffill()
    return out.drop(columns=["_signal_ord"])


def build_monthly_frame(prices: pd.DataFrame) -> pd.DataFrame:
    df = prices.copy()
    g = df.groupby("stock_code", group_keys=False)
    close = g["close"]
    supply = df["inst_amt"] + df["frn_amt"]
    for n in (20, 60, 120, 200):
        df[f"ma{n}"] = close.transform(lambda s, n=n: s.rolling(n, min_periods=n).mean())
    df["ret_1m"] = close.pct_change(21)
    df["ret_3m"] = close.pct_change(63)
    df["ret_6m"] = close.pct_change(126)
    df["ret_12_1"] = close.transform(lambda s: s.shift(21) / s.shift(252) - 1)
    df["high52"] = close.transform(lambda s: s.rolling(252, min_periods=180).max())
    df["low52"] = close.transform(lambda s: s.rolling(252, min_periods=180).min())
    df["near_high52"] = df["close"] / df["high52"]
    df["above_low52"] = df["close"] / df["low52"] - 1
    df["from_high52"] = df["close"] / df["high52"] - 1
    df["vol60"] = g["ret_d"].transform(lambda s: s.rolling(60, min_periods=40).std())
    df["avg_turnover20"] = g["turnover"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["vol_ratio20"] = df["volume"] / g["volume"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["supply20"] = supply.groupby(df["stock_code"]).transform(lambda s: s.rolling(20, min_periods=15).sum())
    df["supply60"] = supply.groupby(df["stock_code"]).transform(lambda s: s.rolling(60, min_periods=40).sum())
    df["supply20_to_turnover"] = df["supply20"] / (df["avg_turnover20"] * 20)
    df["supply60_to_turnover"] = df["supply60"] / (df["avg_turnover20"] * 60)
    df["month"] = df["date"].dt.to_period("M")
    idx = df.groupby(["stock_code", "month"])["date"].idxmax()
    m = df.loc[idx].copy().sort_values(["stock_code", "date"])
    m["signal_month"] = m["month"].astype(str)
    for h in (3, 6, 12):
        m[f"fwd_{h}m_ret"] = m.groupby("stock_code")["close"].shift(-h) / m["close"] - 1
    m = m[m["date"] >= SIGNAL_START].copy()
    for extra in [load_short_monthly(), load_export_monthly(), load_employment_features()]:
        m = merge_point_features(m, extra)
    m = merge_point_features(m, load_event_features())
    for extra, max_age in [
        (load_financial_features(), 5),
        (load_cashflow_features(), 5),
        (load_backlog_features(), 5),
        (load_material_features(), 13),
    ]:
        m = merge_asof_features(m, extra, max_age_months=max_age)
    fill_zero = [c for c in m.columns if c.endswith("_cnt") or c in ["insider_buy_cnt", "contract_amount_krw"]]
    for c in fill_zero:
        m[c] = m[c].fillna(0)
    return m


def add_market_labels(m: pd.DataFrame) -> pd.DataFrame:
    liquid = m[m["avg_turnover20"] >= 1e9].copy()
    for h in (3, 6, 12):
        bench = liquid.groupby("signal_month")[f"fwd_{h}m_ret"].mean().rename(f"market_{h}m_ret")
        m = m.merge(bench, on="signal_month", how="left")
        base = m[f"market_{h}m_ret"]
        fwd = m[f"fwd_{h}m_ret"]
        abs_floor = {3: 0.15, 6: 0.25, 12: 0.40}[h]
        m[f"target_market2x_{h}m"] = np.where(
            base > 0,
            (fwd >= 2 * base) & (fwd >= abs_floor),
            fwd >= abs_floor,
        )
        m[f"target_bigwinner_{h}m"] = fwd >= {3: 0.30, 6: 0.60, 12: 1.00}[h]
    return m


def condition_table(m: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    conds = {}
    specs = {
        "ret_1m": ["q80", "q90"],
        "ret_3m": ["q70", "q80", "q90"],
        "ret_6m": ["q70", "q80", "q90"],
        "near_high52": ["q70", "q80", "q90"],
        "above_low52": ["q70", "q80", "q90"],
        "from_high52": ["q30", "q50"],
        "vol60": ["q20", "q40"],
        "avg_turnover20": ["q70", "q80", "q90"],
        "vol_ratio20": ["q70", "q80", "q90"],
        "supply20_to_turnover": ["q70", "q80", "q90"],
        "supply60_to_turnover": ["q70", "q80", "q90"],
        "short_cover_1m": ["q70", "q80", "q90"],
        "short_cover_3m": ["q70", "q80", "q90"],
        "export_yoy": ["gt0", "q70", "q80", "q90"],
        "export_mom3": ["gt0", "q70", "q80"],
        "contract_cnt": ["gt0"],
        "earnings_signal_cnt": ["gt0"],
        "insider_buy_cnt": ["gt0"],
        "ttm_rev_yoy_pct": ["q80", "q90"],
        "ttm_op_accel_pct": ["q80", "q90"],
        "nps_net_3m": ["gt0", "q70", "q80", "q90"],
        "nps_hires_3m": ["q70", "q80", "q90"],
        "nps_terms_3m": ["q70", "q80", "q90"],
        "wlb_workers_mom": ["gt0", "q70", "q80", "q90"],
        "wlb_workers_3m": ["gt0", "q70", "q80", "q90"],
        "employment_yoy_change": ["gt0", "q70", "q80", "q90"],
        "employment_mom_change": ["gt0", "q70", "q80", "q90"],
        "fin_rev_yoy": ["gt0", "q70", "q80", "q90"],
        "fin_op_yoy": ["gt0", "q70", "q80", "q90"],
        "fin_net_yoy": ["gt0", "q70", "q80", "q90"],
        "fin_op_margin": ["gt0", "q70", "q80", "q90"],
        "fin_net_margin": ["gt0", "q70", "q80"],
        "fin_debt_ratio": ["q20", "q40"],
        "fin_roe": ["q70", "q80", "q90"],
        "fin_rev_accel": ["gt0", "q70", "q80"],
        "fin_op_turnaround": ["gt0"],
        "cf_ocf_yoy": ["gt0", "q70", "q80", "q90"],
        "cf_capex_yoy": ["gt0", "q70", "q80", "q90"],
        "cf_ocf_margin": ["gt0", "q70", "q80", "q90"],
        "cf_fcf_margin": ["gt0", "q70", "q80", "q90"],
        "backlog_to_rev": ["gt0", "q70", "q80", "q90"],
        "backlog_yoy": ["gt0", "q70", "q80", "q90"],
        "new_order_yoy": ["gt0", "q70", "q80", "q90"],
        "completion_ratio": ["q20", "q40", "q70", "q80"],
        "backlog_present": ["gt0"],
        "raw_material_ratio": ["q20", "q40", "q70", "q80"],
        "cogs_ratio": ["q20", "q40", "q70", "q80"],
        "raw_material_cost_yoy": ["gt0", "q70", "q80", "q90"],
        "yoy_raw_material_chg": ["gt0", "q70", "q80", "q90"],
        "dart_material_yoy": ["gt0", "q70", "q80", "q90"],
        "dart_material_to_rev": ["q20", "q40", "q70", "q80"],
        "annual_material_yoy": ["gt0", "q70", "q80", "q90"],
    }
    for col, qs in specs.items():
        if col not in m.columns:
            continue
        s = pd.to_numeric(m[col], errors="coerce")
        for q in qs:
            if q == "gt0":
                conds[f"{col}>0"] = s > 0
                continue
            pct = int(q[1:]) / 100
            thr = s.quantile(pct)
            if pd.isna(thr):
                continue
            if q in ("q20", "q30", "q40"):
                conds[f"{col}<={q}({thr:.4g})"] = s <= thr
            else:
                conds[f"{col}>={q}({thr:.4g})"] = s >= thr
    cond_df = pd.DataFrame(conds, index=m.index).fillna(False)
    return cond_df, list(cond_df.columns)


def lift_rows(m: pd.DataFrame, cond_df: pd.DataFrame, cond_names: list[str], target: str, min_support: int = 80) -> pd.DataFrame:
    base_rate = float(m[target].mean())
    rows = []
    for name in cond_names:
        mask = cond_df[name].to_numpy()
        support = int(mask.sum())
        if support < min_support:
            continue
        hit = float(m.loc[mask, target].mean())
        rows.append({
            "target": target,
            "condition": name,
            "support": support,
            "hit_rate_pct": round(hit * 100, 2),
            "base_rate_pct": round(base_rate * 100, 2),
            "lift": round(hit / base_rate, 3) if base_rate else None,
        })
    return pd.DataFrame(rows)


def combo_lifts(m: pd.DataFrame, cond_df: pd.DataFrame, cond_names: list[str], target: str, min_support: int = 40) -> pd.DataFrame:
    base_rate = float(m[target].mean())
    rows = []
    # Use only individually promising conditions to keep the search transparent.
    single = lift_rows(m, cond_df, cond_names, target, min_support=80)
    if single.empty:
        return single
    shortlist = single.sort_values(["lift", "support"], ascending=[False, False]).head(35)["condition"].tolist()
    for a, b in combinations(shortlist, 2):
        mask = (cond_df[a] & cond_df[b]).to_numpy()
        support = int(mask.sum())
        if support < min_support:
            continue
        hit = float(m.loc[mask, target].mean())
        rows.append({
            "target": target,
            "condition": f"{a} AND {b}",
            "support": support,
            "hit_rate_pct": round(hit * 100, 2),
            "base_rate_pct": round(base_rate * 100, 2),
            "lift": round(hit / base_rate, 3) if base_rate else None,
        })
    return pd.DataFrame(rows)


def describe_winners(m: pd.DataFrame, target: str) -> pd.DataFrame:
    features = [
        "ret_1m", "ret_3m", "ret_6m", "near_high52", "above_low52", "vol60",
        "avg_turnover20", "vol_ratio20", "supply20_to_turnover", "supply60_to_turnover",
        "short_cover_1m", "short_cover_3m", "export_yoy", "export_mom3",
        "nps_net_3m", "nps_hires_3m", "wlb_workers_mom", "wlb_workers_3m",
        "employment_yoy_change", "employment_mom_change",
        "fin_rev_yoy", "fin_op_yoy", "fin_op_margin", "fin_debt_ratio", "fin_roe",
        "cf_ocf_yoy", "cf_ocf_margin", "cf_fcf_margin",
        "backlog_to_rev", "backlog_yoy", "new_order_yoy",
        "raw_material_ratio", "raw_material_cost_yoy", "yoy_raw_material_chg",
        "dart_material_yoy", "dart_material_to_rev", "annual_material_yoy",
    ]
    rows = []
    y = m[target].fillna(False)
    for col in features:
        if col not in m.columns:
            continue
        a = pd.to_numeric(m.loc[y, col], errors="coerce")
        b = pd.to_numeric(m.loc[~y, col], errors="coerce")
        rows.append({
            "target": target,
            "feature": col,
            "winner_median": a.median(),
            "nonwinner_median": b.median(),
            "winner_p75": a.quantile(0.75),
            "nonwinner_p75": b.quantile(0.75),
            "coverage_pct": round(a.notna().mean() * 100, 1),
        })
    return pd.DataFrame(rows)


def quant_indicator_lifts(m: pd.DataFrame, target: str, min_months: int = 12) -> pd.DataFrame:
    q = pd.read_sql_query(
        """
        SELECT s.indicator_key, c.epic_indicator_name, s.period, s.series_name, s.value
        FROM quant_major_indicator_series s
        LEFT JOIN quant_major_indicator_catalog c ON c.indicator_key = s.indicator_key
        WHERE s.value IS NOT NULL
        """,
        conn(),
    )
    if q.empty:
        return pd.DataFrame()
    q["period_dt"] = pd.to_datetime(q["period"], errors="coerce")
    q = q.dropna(subset=["period_dt"])
    q["period_month"] = q["period_dt"].dt.to_period("M")
    # Collapse daily/weekly/monthly data to month-end observations.
    q = q.sort_values(["indicator_key", "series_name", "period_dt"])
    idx = q.groupby(["indicator_key", "series_name", "period_month"])["period_dt"].idxmax()
    q = q.loc[idx, ["indicator_key", "epic_indicator_name", "series_name", "period_month", "value"]].copy()
    q["value"] = pd.to_numeric(q["value"], errors="coerce")
    q = q.dropna(subset=["value"]).sort_values(["indicator_key", "series_name", "period_month"])
    g = q.groupby(["indicator_key", "series_name"])["value"]
    q["mom3"] = q["value"] / g.shift(3) - 1
    q["yoy12"] = q["value"] / g.shift(12) - 1
    q["signal_month"] = (q["period_month"] + 1).astype(str)

    base_rate = float(m[target].mean())
    rows = []
    month_target = m.groupby("signal_month").agg(
        hit=(target, "mean"),
        rows=(target, "size"),
    ).reset_index()
    for (key, series), sub in q.groupby(["indicator_key", "series_name"]):
        if sub["signal_month"].nunique() < min_months:
            continue
        meta = sub["epic_indicator_name"].dropna()
        label = meta.iloc[-1] if len(meta) else key
        sub = sub.merge(month_target, on="signal_month", how="inner")
        if sub.empty:
            continue
        for metric in ["value", "mom3", "yoy12"]:
            s = pd.to_numeric(sub[metric], errors="coerce")
            if s.notna().sum() < min_months:
                continue
            for direction, thr in [
                ("high80", s.quantile(0.8)),
                ("high90", s.quantile(0.9)),
                ("low20", s.quantile(0.2)),
                ("low10", s.quantile(0.1)),
            ]:
                if pd.isna(thr):
                    continue
                mask = s <= thr if direction.startswith("low") else s >= thr
                support_months = int(mask.sum())
                support_rows = int(sub.loc[mask, "rows"].sum())
                if support_months < 4 or support_rows < 1000:
                    continue
                hit = float(np.average(sub.loc[mask, "hit"], weights=sub.loc[mask, "rows"]))
                rows.append({
                    "target": target,
                    "indicator_key": key,
                    "indicator_name": label,
                    "series_name": series,
                    "metric": metric,
                    "direction": direction,
                    "threshold": float(thr),
                    "support_months": support_months,
                    "support_rows": support_rows,
                    "hit_rate_pct": round(hit * 100, 2),
                    "base_rate_pct": round(base_rate * 100, 2),
                    "lift": round(hit / base_rate, 3) if base_rate else None,
                })
    return pd.DataFrame(rows)


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    prices = load_prices()
    m = add_market_labels(build_monthly_frame(prices))
    m = m[m["avg_turnover20"] >= 1e9].copy()
    cond_df, cond_names = condition_table(m)

    all_single = []
    all_combo = []
    all_desc = []
    all_quant = []
    for h in (3, 6, 12):
        target = f"target_market2x_{h}m"
        valid = m[m[f"fwd_{h}m_ret"].notna() & m[f"market_{h}m_ret"].notna()].copy()
        cdf = cond_df.loc[valid.index]
        all_single.append(lift_rows(valid, cdf, cond_names, target))
        all_combo.append(combo_lifts(valid, cdf, cond_names, target))
        all_desc.append(describe_winners(valid, target))
        all_quant.append(quant_indicator_lifts(valid, target))

    single = pd.concat(all_single, ignore_index=True).sort_values(["target", "lift", "support"], ascending=[True, False, False])
    combo = pd.concat(all_combo, ignore_index=True).sort_values(["target", "lift", "support"], ascending=[True, False, False])
    desc = pd.concat(all_desc, ignore_index=True)
    quant = pd.concat([x for x in all_quant if x is not None and not x.empty], ignore_index=True)
    single.to_csv(OUT_DIR / "market2x_signal_single_lifts.csv", index=False)
    combo.to_csv(OUT_DIR / "market2x_signal_combo_lifts.csv", index=False)
    desc.to_csv(OUT_DIR / "market2x_winner_feature_profile.csv", index=False)
    quant.to_csv(OUT_DIR / "market2x_quant_indicator_lifts.csv", index=False)
    m.to_parquet(OUT_DIR / "market2x_signal_dataset.parquet", index=False)

    summary = {
        "rows": int(len(m)),
        "months": int(m["signal_month"].nunique()),
        "stocks": int(m["stock_code"].nunique()),
        "targets": {
            f"{h}m": {
                "base_rate_pct": round(float(m[f"target_market2x_{h}m"].mean()) * 100, 2),
                "valid_rows": int(m[f"fwd_{h}m_ret"].notna().sum()),
            }
            for h in (3, 6, 12)
        },
        "top_single": single.groupby("target").head(8).to_dict(orient="records"),
        "top_combo": combo.groupby("target").head(8).to_dict(orient="records"),
        "top_quant": quant.sort_values(["target", "lift", "support_rows"], ascending=[True, False, False]).groupby("target").head(8).to_dict(orient="records") if not quant.empty else [],
    }
    (OUT_DIR / "market2x_signal_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
