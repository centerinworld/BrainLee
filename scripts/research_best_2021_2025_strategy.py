#!/usr/bin/env python3
"""Search portfolio rules for 2021-01 through 2025-04 using local data.

This script turns the existing monthly signal dataset into executable
month-ahead portfolio rules. It assumes the signal is observed after month-end,
buys at the next trading day's open, sells at that month-end close, and keeps
unallocated capital in cash. It is research code, not live trading advice.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("/Applications/stock_dashboard")
DB_PATH = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
DATASET_PATH = OUT_DIR / "market2x_signal_dataset.parquet"
BUDGET = 100_000_000
TCOST = 0.0035
START_MONTH = "2021-01"
LAST_SIGNAL_MONTH = "2025-03"  # exits in 2025-04, before 2025-05
TRAIN_END = "2023-12"
TEST_START = "2024-01"


@dataclass(frozen=True)
class Rule:
    name: str
    top_n: int
    filters: tuple[str, ...]
    score_cols: tuple[tuple[str, float], ...]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_frame() -> pd.DataFrame:
    if not DATASET_PATH.exists():
        from scripts.discover_market2x_signals import add_market_labels, build_monthly_frame, load_prices

        OUT_DIR.mkdir(exist_ok=True)
        m = add_market_labels(build_monthly_frame(load_prices()))
        m.to_parquet(DATASET_PATH, index=False)
    m = pd.read_parquet(DATASET_PATH)
    m["date"] = pd.to_datetime(m["date"])
    m = m.sort_values(["stock_code", "date"]).copy()
    m = m[(m["signal_month"] >= START_MONTH) & (m["signal_month"] <= LAST_SIGNAL_MONTH)].copy()
    m = attach_next_open_returns(m)
    m = attach_market_regime(m)
    m = m[m["fwd_1m_ret"].notna()].copy()

    # Remove likely corporate-action artifacts and penny/illiquid noise.
    m = m[m["fwd_1m_ret"].between(-0.55, 1.5)].copy()
    m = m[m["stock_code"].astype(str).str.match(r"^\d{6}$", na=False)].copy()
    m = m[m["market"].isin(["KOSPI", "KOSDAQ"])].copy()
    return m


def attach_next_open_returns(m: pd.DataFrame) -> pd.DataFrame:
    """Attach realistic 1-month execution returns to each monthly signal row."""
    con = _conn()
    prices = pd.read_sql_query(
        """
        SELECT stock_code, date, open, close
        FROM price_history
        WHERE date BETWEEN '2021-01-01' AND '2025-04-30'
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND open > 0
          AND close > 0
        ORDER BY stock_code, date
        """,
        con,
        parse_dates=["date"],
    )
    con.close()
    prices["ym"] = prices["date"].dt.to_period("M").astype(str)
    prices = prices.sort_values(["stock_code", "date"])
    first_idx = prices.groupby(["stock_code", "ym"])["date"].idxmin()
    last_idx = prices.groupby(["stock_code", "ym"])["date"].idxmax()
    entry = prices.loc[first_idx, ["stock_code", "ym", "date", "open"]].rename(
        columns={"ym": "exec_month", "date": "entry_date", "open": "entry_open"}
    )
    exit_ = prices.loc[last_idx, ["stock_code", "ym", "date", "close"]].rename(
        columns={"ym": "exec_month", "date": "exit_date", "close": "exit_close"}
    )
    exec_px = entry.merge(exit_, on=["stock_code", "exec_month"], how="inner")
    exec_px["signal_month"] = (pd.PeriodIndex(exec_px["exec_month"], freq="M") - 1).astype(str)
    exec_px["fwd_1m_ret"] = exec_px["exit_close"] / exec_px["entry_open"] - 1
    return m.merge(
        exec_px[["stock_code", "signal_month", "entry_date", "entry_open", "exit_date", "exit_close", "fwd_1m_ret"]],
        on=["stock_code", "signal_month"],
        how="left",
    )


def attach_market_regime(m: pd.DataFrame) -> pd.DataFrame:
    con = _conn()
    ks = pd.read_sql_query(
        """
        SELECT date, close
        FROM price_history
        WHERE stock_code = '^KS11'
          AND date BETWEEN '2020-01-01' AND '2025-04-30'
        ORDER BY date
        """,
        con,
        parse_dates=["date"],
    )
    con.close()
    if ks.empty:
        for col in ["ks_ret_1m", "ks_ret_3m", "ks_above_ma6", "ks_above_ma10", "ks_risk_on"]:
            m[col] = np.nan
        return m
    ks["signal_month"] = ks["date"].dt.to_period("M").astype(str)
    idx = ks.groupby("signal_month")["date"].idxmax()
    km = ks.loc[idx].sort_values("date").copy()
    km["ks_ret_1m"] = km["close"].pct_change(1)
    km["ks_ret_3m"] = km["close"].pct_change(3)
    km["ks_ma6"] = km["close"].rolling(6, min_periods=4).mean()
    km["ks_ma10"] = km["close"].rolling(10, min_periods=6).mean()
    km["ks_above_ma6"] = km["close"] > km["ks_ma6"]
    km["ks_above_ma10"] = km["close"] > km["ks_ma10"]
    km["ks_risk_on"] = km["ks_above_ma6"] & (km["ks_ret_3m"] > -0.08)
    keep = ["signal_month", "ks_ret_1m", "ks_ret_3m", "ks_above_ma6", "ks_above_ma10", "ks_risk_on"]
    return m.merge(km[keep], on="signal_month", how="left")


def add_cross_sectional_ranks(m: pd.DataFrame) -> pd.DataFrame:
    rank_cols = [
        "ret_1m",
        "ret_3m",
        "ret_6m",
        "ret_12_1",
        "near_high52",
        "above_low52",
        "from_high52",
        "vol60",
        "avg_turnover20",
        "vol_ratio20",
        "supply20_to_turnover",
        "supply60_to_turnover",
        "short_cover_1m",
        "short_cover_3m",
        "export_yoy",
        "nps_net_3m",
        "wlb_workers_3m",
        "fin_rev_yoy",
        "fin_op_yoy",
        "fin_op_margin",
        "fin_roe",
        "cf_ocf_yoy",
        "cf_fcf_margin",
        "backlog_yoy",
        "new_order_yoy",
        "raw_material_cost_yoy",
        "yoy_raw_material_chg",
        "dart_material_yoy",
        "annual_material_yoy",
        "contract_signal_strength",
        "ttm_op_accel_pct",
    ]
    for col in rank_cols:
        if col not in m.columns:
            continue
        s = pd.to_numeric(m[col], errors="coerce")
        m[f"r_{col}"] = s.groupby(m["signal_month"]).rank(pct=True)
    if "r_vol60" in m.columns:
        m["r_low_vol60"] = 1 - m["r_vol60"]
    if "r_from_high52" in m.columns:
        m["r_not_extended"] = 1 - m["r_from_high52"].abs()

    # Sparse fundamental/business features: zero means no usable signal that month.
    for col in [
        "contract_cnt",
        "earnings_signal_cnt",
        "insider_buy_cnt",
        "backlog_present",
        "fin_op_turnaround",
    ]:
        if col in m.columns:
            m[col] = pd.to_numeric(m[col], errors="coerce").fillna(0)
    return m


def make_filters(m: pd.DataFrame) -> dict[str, pd.Series]:
    def col(name: str) -> pd.Series:
        return pd.to_numeric(m.get(name), errors="coerce")

    f: dict[str, pd.Series] = {
        "liquid_1b": col("avg_turnover20") >= 1e9,
        "liquid_2b": col("avg_turnover20") >= 2e9,
        "liquid_5b": col("avg_turnover20") >= 5e9,
        "price_ok": col("close") >= 1000,
        "mktcap_ok": col("market_cap").fillna(1e12) >= 50_000_000_000,
        "trend_ma": (col("close") > col("ma120")) & (col("ma20") > col("ma60")),
        "trend_strong": (col("close") > col("ma200")) & (col("ma20") > col("ma60")) & (col("ma60") > col("ma120")),
        "mom_3m_pos": col("ret_3m") > 0.08,
        "mom_6m_pos": col("ret_6m") > 0.12,
        "mom_6m_strong": col("ret_6m") > 0.25,
        "not_crashed_1m": col("ret_1m") > -0.10,
        "not_overheated_1m": col("ret_1m") < 0.65,
        "near_high": col("near_high52") >= 0.72,
        "very_near_high": col("near_high52") >= 0.85,
        "low_vol": col("r_low_vol60") >= 0.45,
        "very_low_vol": col("r_low_vol60") >= 0.60,
        "supply_positive": col("supply20_to_turnover") > 0,
        "supply_rank60": col("r_supply20_to_turnover") >= 0.60,
        "supply60_positive": col("supply60_to_turnover") > 0,
        "short_cover_pos": col("short_cover_1m") > 0,
        "short_cover_rank70": col("r_short_cover_1m") >= 0.70,
        "fin_growth_pos": (col("fin_rev_yoy") > 0) & (col("fin_op_yoy") > 0),
        "fin_quality_pos": (col("fin_op_margin") > 0) & (col("fin_roe") > 0),
        "cashflow_pos": (col("cf_ocf_margin") > 0) | (col("cf_fcf_margin") > 0),
        "material_yoy_pos": (col("raw_material_cost_yoy") > 0) | (col("dart_material_yoy") > 0) | (col("annual_material_yoy") > 0),
        "material_yoy_rank70": (col("r_raw_material_cost_yoy") >= 0.70) | (col("r_dart_material_yoy") >= 0.70) | (col("r_annual_material_yoy") >= 0.70),
        "backlog_pos": (col("backlog_present") > 0) | (col("backlog_yoy") > 0) | (col("new_order_yoy") > 0),
        "employment_pos": (col("nps_net_3m") > 0) | (col("wlb_workers_3m") > 0),
        "event_pos": (col("contract_cnt") > 0) | (col("earnings_signal_cnt") > 0) | (col("insider_buy_cnt") > 0),
        "market_above_ma6": col("ks_above_ma6") > 0,
        "market_above_ma10": col("ks_above_ma10") > 0,
        "market_ret3_pos": col("ks_ret_3m") > 0,
        "market_not_weak_3m": col("ks_ret_3m") > -0.08,
        "market_risk_on": col("ks_risk_on") > 0,
    }
    return {k: v.fillna(False) for k, v in f.items()}


def score(m: pd.DataFrame, cols: tuple[tuple[str, float], ...]) -> pd.Series:
    out = pd.Series(0.0, index=m.index)
    for col, weight in cols:
        s = pd.to_numeric(m.get(col), errors="coerce").fillna(0)
        out += weight * s
    return out


def candidate_rules() -> list[Rule]:
    score_sets = {
        "trend_supply_quality": (
            ("r_ret_6m", 0.24),
            ("r_ret_3m", 0.16),
            ("r_near_high52", 0.15),
            ("r_supply20_to_turnover", 0.16),
            ("r_low_vol60", 0.12),
            ("r_fin_op_yoy", 0.09),
            ("r_fin_op_margin", 0.08),
        ),
        "trend_material": (
            ("r_ret_6m", 0.22),
            ("r_ret_3m", 0.14),
            ("r_near_high52", 0.14),
            ("r_supply20_to_turnover", 0.13),
            ("r_low_vol60", 0.10),
            ("r_raw_material_cost_yoy", 0.10),
            ("r_dart_material_yoy", 0.09),
            ("r_annual_material_yoy", 0.08),
        ),
        "squeeze_short_cover": (
            ("r_ret_3m", 0.22),
            ("r_ret_1m", 0.14),
            ("r_near_high52", 0.16),
            ("r_short_cover_1m", 0.18),
            ("r_supply20_to_turnover", 0.15),
            ("r_low_vol60", 0.15),
        ),
        "business_accel": (
            ("r_ret_6m", 0.17),
            ("r_near_high52", 0.14),
            ("r_fin_rev_yoy", 0.13),
            ("r_fin_op_yoy", 0.15),
            ("r_cf_ocf_yoy", 0.10),
            ("r_backlog_yoy", 0.10),
            ("r_new_order_yoy", 0.10),
            ("r_supply60_to_turnover", 0.11),
        ),
        "pure_rs_lowvol": (
            ("r_ret_12_1", 0.28),
            ("r_ret_6m", 0.22),
            ("r_near_high52", 0.18),
            ("r_low_vol60", 0.20),
            ("r_avg_turnover20", 0.12),
        ),
    }

    base_templates = [
        ("base_trend", ("liquid_2b", "price_ok", "trend_ma", "mom_3m_pos", "not_crashed_1m", "near_high")),
        ("strong_trend", ("liquid_2b", "price_ok", "trend_strong", "mom_6m_pos", "not_crashed_1m", "near_high")),
        ("liquid_squeeze", ("liquid_5b", "price_ok", "trend_ma", "mom_6m_strong", "not_overheated_1m", "very_near_high")),
        ("quality_trend", ("liquid_2b", "price_ok", "trend_strong", "mom_6m_pos", "fin_quality_pos")),
        ("material_trend", ("liquid_1b", "price_ok", "trend_ma", "mom_3m_pos", "material_yoy_pos")),
        ("backlog_trend", ("liquid_1b", "price_ok", "trend_ma", "mom_3m_pos", "backlog_pos")),
    ]
    optional_filters = [
        (),
        ("low_vol",),
        ("very_low_vol",),
        ("supply_positive",),
        ("supply_rank60",),
        ("supply60_positive",),
        ("short_cover_pos",),
        ("short_cover_rank70",),
        ("fin_growth_pos",),
        ("cashflow_pos",),
        ("material_yoy_rank70",),
        ("employment_pos",),
        ("event_pos",),
        ("supply_positive", "low_vol"),
        ("supply_rank60", "very_low_vol"),
        ("fin_growth_pos", "supply_positive"),
        ("material_yoy_pos", "supply_positive"),
        ("backlog_pos", "supply_positive"),
        ("short_cover_pos", "supply_positive"),
        ("market_above_ma6",),
        ("market_above_ma10",),
        ("market_ret3_pos",),
        ("market_risk_on",),
        ("market_above_ma6", "supply_positive"),
        ("market_above_ma10", "low_vol"),
        ("market_risk_on", "supply_positive"),
        ("market_risk_on", "very_low_vol"),
        ("market_risk_on", "fin_growth_pos"),
        ("market_risk_on", "material_yoy_rank70"),
    ]

    rules: list[Rule] = []
    for base_name, base in base_templates:
        for score_name, cols in score_sets.items():
            for opt in optional_filters:
                filters = tuple(dict.fromkeys((*base, *opt)))
                for top_n in (3, 5, 8, 10):
                    name = f"{base_name}__{score_name}__{'_'.join(opt) or 'plain'}__top{top_n}"
                    rules.append(Rule(name=name, top_n=top_n, filters=filters, score_cols=cols))
    return rules


def backtest_rule(m: pd.DataFrame, filters: dict[str, pd.Series], rule: Rule) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    mask = pd.Series(True, index=m.index)
    for f in rule.filters:
        mask &= filters[f]
    universe = m[mask].copy()
    if universe.empty:
        return {"name": rule.name, "months": 0}, pd.DataFrame(), pd.DataFrame()
    universe["score"] = score(universe, rule.score_cols)
    universe = universe[universe["score"].notna()]
    picks = (
        universe.sort_values(["signal_month", "score"], ascending=[True, False])
        .groupby("signal_month")
        .head(rule.top_n)
        .copy()
    )
    if picks.empty:
        return {"name": rule.name, "months": 0}, pd.DataFrame(), pd.DataFrame()

    picks["position_value"] = picks.groupby("signal_month")["stock_code"].transform(lambda s: BUDGET / len(s))
    picks["profit_krw_gross"] = picks["position_value"] * picks["fwd_1m_ret"]
    picks["profit_krw_net"] = picks["position_value"] * (picks["fwd_1m_ret"] - TCOST)

    months = pd.DataFrame({"signal_month": sorted(m["signal_month"].unique())})
    monthly = picks.groupby("signal_month").agg(
        n=("stock_code", "count"),
        gross_ret=("fwd_1m_ret", "mean"),
        trade_hit_rate=("fwd_1m_ret", lambda s: float((s > 0).mean())),
        best=("fwd_1m_ret", "max"),
        worst=("fwd_1m_ret", "min"),
    ).reset_index()
    monthly = months.merge(monthly, on="signal_month", how="left")
    monthly["n"] = monthly["n"].fillna(0).astype(int)
    monthly["cash_month"] = monthly["n"].eq(0)
    monthly["gross_ret"] = monthly["gross_ret"].fillna(0)
    monthly["net_ret"] = np.where(monthly["cash_month"], 0.0, monthly["gross_ret"] - TCOST)
    monthly["equity"] = BUDGET * (1 + monthly["net_ret"]).cumprod()

    summary = summarize(rule.name, monthly, picks)
    return summary, monthly, picks


def summarize(name: str, monthly: pd.DataFrame, picks: pd.DataFrame) -> dict:
    r = monthly["net_ret"].astype(float)
    eq = (1 + r).cumprod()
    years = len(r) / 12
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    dd = eq / eq.cummax() - 1
    active = monthly[monthly["n"] > 0]
    test = monthly[monthly["signal_month"] >= TEST_START]
    train = monthly[monthly["signal_month"] <= TRAIN_END]
    return {
        "name": name,
        "months": int(len(monthly)),
        "active_months": int(len(active)),
        "trade_count": int(len(picks)),
        "avg_names": round(float(active["n"].mean()), 2) if len(active) else 0,
        "final_equity_krw": int(round(BUDGET * eq.iloc[-1])),
        "total_return_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
        "cagr_pct": round(float(cagr * 100), 2),
        "monthly_hit_rate_pct": round(float((r > 0).mean() * 100), 1),
        "active_month_hit_rate_pct": round(float((active["net_ret"] > 0).mean() * 100), 1) if len(active) else 0,
        "trade_hit_rate_pct": round(float((picks["fwd_1m_ret"] > 0).mean() * 100), 1) if len(picks) else 0,
        "avg_monthly_net_pct": round(float(r.mean() * 100), 2),
        "max_drawdown_pct": round(float(dd.min() * 100), 2),
        "sharpe_monthly": round(float(r.mean() / r.std() * math.sqrt(12)), 2) if r.std() else 0,
        "train_return_pct": round(float(((1 + train["net_ret"]).prod() - 1) * 100), 2) if len(train) else 0,
        "test_return_pct": round(float(((1 + test["net_ret"]).prod() - 1) * 100), 2) if len(test) else 0,
        "test_month_hit_rate_pct": round(float((test["net_ret"] > 0).mean() * 100), 1) if len(test) else 0,
    }


def benchmark(m: pd.DataFrame) -> dict:
    con = _conn()
    ks = pd.read_sql_query(
        """
        SELECT date, close
        FROM price_history
        WHERE stock_code = '^KS11'
          AND date BETWEEN '2021-01-01' AND '2025-04-30'
        ORDER BY date
        """,
        con,
        parse_dates=["date"],
    )
    con.close()
    if ks.empty:
        ew = m.groupby("signal_month")["fwd_1m_ret"].mean().reset_index(name="net_ret")
        return summarize("all_liquid_equal_weight", ew.assign(n=1), pd.DataFrame({"fwd_1m_ret": []}))
    ks["signal_month"] = ks["date"].dt.to_period("M").astype(str)
    idx = ks.groupby("signal_month")["date"].idxmax()
    bm = ks.loc[idx].copy().sort_values("date")
    bm["net_ret"] = bm["close"].shift(-1) / bm["close"] - 1
    bm = bm[(bm["signal_month"] >= START_MONTH) & (bm["signal_month"] <= LAST_SIGNAL_MONTH)].dropna(subset=["net_ret"])
    bm["n"] = 1
    return summarize("KOSPI_buy_hold_monthly", bm[["signal_month", "net_ret", "n"]], pd.DataFrame({"fwd_1m_ret": bm["net_ret"]}))


def explain_rule(rule: Rule) -> dict:
    return {
        "filters": list(rule.filters),
        "score": [{"field": col, "weight": weight} for col, weight in rule.score_cols],
        "top_n": rule.top_n,
        "budget": BUDGET,
        "rebalance": "월말 신호 확인 후 다음 거래일 시가 매수, 해당 월 말 종가 매도. 월별 선정 종목 균등 배분.",
        "cost": TCOST,
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    m = add_cross_sectional_ranks(load_frame())
    filters = make_filters(m)

    summaries = []
    monthly_by_name: dict[str, pd.DataFrame] = {}
    picks_by_name: dict[str, pd.DataFrame] = {}
    rules = candidate_rules()
    by_name = {r.name: r for r in rules}
    for i, rule in enumerate(rules, start=1):
        s, monthly, picks = backtest_rule(m, filters, rule)
        if s.get("months", 0):
            summaries.append(s)
            monthly_by_name[rule.name] = monthly
            picks_by_name[rule.name] = picks
        if i % 500 == 0:
            print(f"tested {i}/{len(rules)}")

    summary_df = pd.DataFrame(summaries)
    # Require enough activity and positive validation-period return before
    # calling a rule investable.
    investable = summary_df[
        (summary_df["active_months"] >= 24)
        & (summary_df["trade_count"] >= 80)
        & (summary_df["test_return_pct"] > 0)
        & (summary_df["max_drawdown_pct"] >= -45)
    ].copy()
    if investable.empty:
        investable = summary_df.copy()
    investable["selection_score"] = (
        investable["cagr_pct"]
        + 0.35 * investable["active_month_hit_rate_pct"]
        + 0.20 * investable["test_return_pct"]
        + 0.45 * investable["max_drawdown_pct"]
    )
    best_return = investable.sort_values(
        ["total_return_pct", "test_return_pct", "monthly_hit_rate_pct"],
        ascending=[False, False, False],
    ).iloc[0]
    best_win = investable.sort_values(
        ["active_month_hit_rate_pct", "total_return_pct", "test_return_pct"],
        ascending=[False, False, False],
    ).iloc[0]
    balanced = investable.sort_values(
        ["selection_score", "total_return_pct", "active_month_hit_rate_pct"],
        ascending=[False, False, False],
    ).iloc[0]

    selected_names = list(dict.fromkeys([best_return["name"], best_win["name"], balanced["name"]]))
    for name in selected_names:
        monthly_by_name[name].to_csv(OUT_DIR / f"best_2021_2025_{name}_monthly.csv", index=False)
        picks_by_name[name].to_csv(OUT_DIR / f"best_2021_2025_{name}_picks.csv", index=False)

    summary_df.sort_values(["total_return_pct", "test_return_pct"], ascending=[False, False]).to_csv(
        OUT_DIR / "best_2021_2025_strategy_all_results.csv", index=False
    )
    investable.sort_values(["selection_score"], ascending=False).head(50).to_csv(
        OUT_DIR / "best_2021_2025_strategy_top50.csv", index=False
    )

    result = {
        "scope": {
            "db": str(DB_PATH),
            "dataset": str(DATASET_PATH),
            "budget_krw": BUDGET,
            "signal_month_start": START_MONTH,
            "last_signal_month": LAST_SIGNAL_MONTH,
            "exit_window_note": "last 1-month exits are in 2025-04, before 2025-05",
            "transaction_cost_round_trip": TCOST,
            "train": f"{START_MONTH}~{TRAIN_END}",
            "validation": f"{TEST_START}~{LAST_SIGNAL_MONTH}",
        },
        "benchmark": benchmark(m),
        "tested_rules": len(rules),
        "investable_rules": int(len(investable)),
        "best_return": best_return.to_dict(),
        "best_win_rate": best_win.to_dict(),
        "balanced_recommendation": balanced.to_dict(),
        "rule_details": {name: explain_rule(by_name[name]) for name in selected_names},
        "top10": investable.sort_values(["selection_score"], ascending=False).head(10).to_dict(orient="records"),
        "output_files": {
            "all_results": str(OUT_DIR / "best_2021_2025_strategy_all_results.csv"),
            "top50": str(OUT_DIR / "best_2021_2025_strategy_top50.csv"),
            "summary": str(OUT_DIR / "best_2021_2025_strategy_summary.json"),
            "selected_monthly_prefix": str(OUT_DIR / "best_2021_2025_<strategy>_monthly.csv"),
            "selected_picks_prefix": str(OUT_DIR / "best_2021_2025_<strategy>_picks.csv"),
        },
    }
    (OUT_DIR / "best_2021_2025_strategy_summary.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
