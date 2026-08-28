#!/usr/bin/env python3
"""
Find robust tenbagger-style signals with budget-constrained portfolios.

The goal is not to maximize a single backtest line.  It compares price,
investor-flow, short/lending, and export signals while keeping position count
explicit, so the resulting rule is usable when capital is limited.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("/Applications/stock_dashboard")
DB_PATH = ROOT / "stock.db"
HS_DB_PATH = ROOT / "hs_trade_lab/data/hs_trade_lab.db"
OUT_DIR = ROOT / "research_outputs"

START = "2020-01-01"
SIGNAL_START = "2021-06-01"
TEST_START = "2025-01-01"
END = "2026-06-05"
TCOST = 0.004  # monthly rebalance, round-trip + slippage


@dataclass(frozen=True)
class Rule:
    name: str
    top_n: int
    market_filter: bool
    filter_expr: str
    score_expr: str


RULES = [
    Rule(
        "price_supply_squeeze_top5",
        5,
        True,
        "avg_turnover20 >= 5e9 and close >= 1000 and ret_1m > 0.05 and ret_3m > 0.20 and ret_6m > 0.20 and near_high52 > 0.80",
        "0.30*rank_ret_1m + 0.20*rank_ret_3m + 0.20*rank_supply20 + 0.20*rank_avg_turnover20 + 0.10*rank_near_high52",
    ),
    Rule(
        "squeeze_shortcover_top5",
        5,
        True,
        "avg_turnover20 >= 5e9 and close >= 1000 and ret_1m > 0.05 and ret_3m > 0.20 and ret_6m > 0.20 and near_high52 > 0.80",
        "0.25*rank_ret_1m + 0.20*rank_ret_3m + 0.15*rank_supply20 + 0.15*rank_avg_turnover20 + 0.10*rank_near_high52 + 0.15*rank_short_cover_1m",
    ),
    Rule(
        "squeeze_export_top5",
        5,
        True,
        "avg_turnover20 >= 5e9 and close >= 1000 and ret_1m > 0.05 and ret_3m > 0.20 and ret_6m > 0.20 and near_high52 > 0.80 and export_yoy > 0",
        "0.25*rank_ret_1m + 0.20*rank_ret_3m + 0.15*rank_supply20 + 0.15*rank_avg_turnover20 + 0.10*rank_near_high52 + 0.15*rank_export_yoy",
    ),
    Rule(
        "all_signal_confirmed_top5",
        5,
        True,
        "avg_turnover20 >= 5e9 and close >= 1000 and ret_1m > 0.05 and ret_3m > 0.20 and ret_6m > 0.20 and near_high52 > 0.80 and supply20 > 0 and short_cover_1m > -0.20",
        "0.22*rank_ret_1m + 0.18*rank_ret_3m + 0.15*rank_supply20 + 0.15*rank_avg_turnover20 + 0.10*rank_near_high52 + 0.10*rank_short_cover_1m + 0.10*rank_export_yoy_fill",
    ),
    Rule(
        "drawdown_reversal_event_top8",
        8,
        False,
        "avg_turnover20 >= 3e9 and close >= 1000 and from_high52 <= -0.30 and ret_1m > 0.03 and vol_ratio20 > 1.5 and supply20 > 0",
        "0.25*rank_ret_1m + 0.20*rank_supply20 + 0.20*rank_vol_ratio20 + 0.15*rank_short_cover_1m + 0.10*rank_export_yoy_fill + 0.10*rank_low_vol60",
    ),
]


def conn(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    return c


def load_prices() -> pd.DataFrame:
    sql = """
    SELECT
      p.stock_code, p.date, p.open, p.high, p.low, p.close, p.volume,
      COALESCE(NULLIF(p.trade_amount, 0), p.close * p.volume) AS turnover,
      COALESCE(p.inst_net_buy_amt, 0) AS inst_amt,
      COALESCE(p.frn_net_buy_amt, 0) AS frn_amt,
      COALESCE(su.stock_name, sm.stock_name) AS stock_name,
      COALESCE(su.market, sm.market) AS market
    FROM price_history p
    LEFT JOIN stock_universe su ON su.stock_code = p.stock_code
    LEFT JOIN stock_meta sm ON sm.stock_code = p.stock_code
    WHERE p.date BETWEEN ? AND ?
      AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND p.close > 0
      AND p.volume > 0
      AND COALESCE(su.market, sm.market) IN ('KOSPI', 'KOSDAQ')
    """
    df = pd.read_sql_query(sql, conn(DB_PATH), params=(START, END), parse_dates=["date"])
    df = df.sort_values(["stock_code", "date"]).reset_index(drop=True)
    df["ret_d"] = df.groupby("stock_code")["close"].pct_change()
    bad = set(df.loc[df["ret_d"].abs() > 0.60, "stock_code"].unique())
    return df[~df["stock_code"].isin(bad)].copy()


def load_short_monthly() -> pd.DataFrame:
    sql = """
    SELECT stock_code, bas_dt, lnb_bal AS borrow_bal_amt, lnb_rman_stck_cnt AS borrow_bal_qty
    FROM short_rank_daily
    WHERE bas_dt BETWEEN '20210101' AND '20260630'
      AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND lnb_bal IS NOT NULL
    """
    s = pd.read_sql_query(sql, conn(DB_PATH))
    if s.empty:
        return pd.DataFrame(columns=["stock_code", "signal_month", "short_cover_1m", "short_cover_3m", "borrow_bal_amt"])
    s["date"] = pd.to_datetime(s["bas_dt"], format="%Y%m%d", errors="coerce")
    s = s.dropna(subset=["date"]).sort_values(["stock_code", "date"])
    s["signal_month"] = s["date"].dt.to_period("M").astype(str)
    idx = s.groupby(["stock_code", "signal_month"])["date"].idxmax()
    m = s.loc[idx, ["stock_code", "signal_month", "borrow_bal_amt"]].copy()
    m = m.sort_values(["stock_code", "signal_month"])
    g = m.groupby("stock_code")["borrow_bal_amt"]
    m["short_cover_1m"] = -(m["borrow_bal_amt"] / g.shift(1) - 1)
    m["short_cover_3m"] = -(m["borrow_bal_amt"] / g.shift(3) - 1)
    return m[["stock_code", "signal_month", "short_cover_1m", "short_cover_3m", "borrow_bal_amt"]]


def load_export_monthly() -> pd.DataFrame:
    if not HS_DB_PATH.exists():
        return pd.DataFrame(columns=["stock_code", "signal_month", "export_yoy", "export_3m"])
    sql = """
    SELECT stock_code, period_ym, SUM(export_value) AS export_value
    FROM analysis2_company_hs_monthly_cache
    WHERE mapping_status = 'exact'
      AND flow_type = 'export'
      AND stock_code IS NOT NULL
    GROUP BY stock_code, period_ym
    """
    x = pd.read_sql_query(sql, conn(HS_DB_PATH))
    if x.empty:
        return pd.DataFrame(columns=["stock_code", "signal_month", "export_yoy", "export_3m"])
    x = x.sort_values(["stock_code", "period_ym"])
    g = x.groupby("stock_code")["export_value"]
    x["export_yoy"] = x["export_value"] / g.shift(12) - 1
    x["export_3m"] = x["export_value"].rolling(3, min_periods=2).mean().reset_index(level=0, drop=True)
    x["signal_month"] = (pd.PeriodIndex(x["period_ym"], freq="M") + 2).astype(str)
    return x[["stock_code", "signal_month", "export_yoy", "export_3m"]]


def add_price_features(df: pd.DataFrame) -> pd.DataFrame:
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
    df["from_high52"] = df["close"] / df["high52"] - 1
    df["vol60"] = g["ret_d"].transform(lambda s: s.rolling(60, min_periods=40).std())
    df["low_vol60"] = -df["vol60"]
    df["avg_turnover20"] = g["turnover"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["vol_ratio20"] = df["volume"] / g["volume"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["supply20"] = supply.groupby(df["stock_code"]).transform(lambda s: s.rolling(20, min_periods=15).sum())
    df["supply60"] = supply.groupby(df["stock_code"]).transform(lambda s: s.rolling(60, min_periods=40).sum())
    df["month"] = df["date"].dt.to_period("M")
    idx = df.groupby(["stock_code", "month"])["date"].idxmax()
    m = df.loc[idx].copy().sort_values(["stock_code", "date"])
    m["next_close"] = m.groupby("stock_code")["close"].shift(-1)
    m["fwd_1m_ret"] = m["next_close"] / m["close"] - 1
    m["signal_month"] = m["month"].astype(str)
    return m[m["date"] >= SIGNAL_START].copy()


def add_external_features(m: pd.DataFrame) -> pd.DataFrame:
    m = m.merge(load_short_monthly(), on=["stock_code", "signal_month"], how="left")
    m = m.merge(load_export_monthly(), on=["stock_code", "signal_month"], how="left")
    m["export_yoy_fill"] = m["export_yoy"].fillna(0.0)
    for col in [
        "ret_1m", "ret_3m", "ret_6m", "ret_12_1", "near_high52", "from_high52",
        "supply20", "supply60", "avg_turnover20", "vol_ratio20", "low_vol60",
        "short_cover_1m", "short_cover_3m", "borrow_bal_amt", "export_yoy",
        "export_yoy_fill",
    ]:
        if col in m.columns:
            m[f"rank_{col}"] = m.groupby("signal_month")[col].rank(pct=True)
    return m


def load_benchmark() -> pd.DataFrame:
    b = pd.read_sql_query(
        "SELECT date, close FROM price_history WHERE stock_code='^KS11' AND date BETWEEN ? AND ? ORDER BY date",
        conn(DB_PATH),
        params=(SIGNAL_START, END),
        parse_dates=["date"],
    )
    b["month"] = b["date"].dt.to_period("M")
    b = b.loc[b.groupby("month")["date"].idxmax()].copy()
    b["fwd_1m_ret"] = b["close"].shift(-1) / b["close"] - 1
    b["ma10m"] = b["close"].rolling(10, min_periods=6).mean()
    b["market_ok"] = b["close"] > b["ma10m"]
    b["signal_month"] = b["month"].astype(str)
    return b


def run_rule(signals: pd.DataFrame, bench: pd.DataFrame, rule: Rule) -> tuple[pd.DataFrame, pd.DataFrame]:
    x = signals.query(rule.filter_expr).copy()
    x["score"] = x.eval(rule.score_expr)
    if rule.market_filter:
        x = x.merge(bench[["signal_month", "market_ok"]], on="signal_month", how="left")
        x = x[x["market_ok"].fillna(False)]
    picks = (
        x.sort_values(["signal_month", "score"], ascending=[True, False])
        .groupby("signal_month")
        .head(rule.top_n)
        .copy()
    )
    picks = picks[picks["fwd_1m_ret"].notna()].copy()
    port = picks.groupby("signal_month").agg(
        ret=("fwd_1m_ret", "mean"),
        n=("stock_code", "count"),
        best=("fwd_1m_ret", "max"),
        worst=("fwd_1m_ret", "min"),
        score=("score", "mean"),
    ).reset_index()
    all_months = bench.loc[bench["fwd_1m_ret"].notna(), ["signal_month"]]
    port = all_months.merge(port, on="signal_month", how="left")
    port["cash_month"] = port["ret"].isna()
    port["ret"] = port["ret"].fillna(0.0)
    port["n"] = port["n"].fillna(0).astype(int)
    port["ret_net"] = np.where(port["cash_month"], 0.0, port["ret"] - TCOST)
    return port, picks


def summarize(name: str, monthly: pd.DataFrame) -> dict:
    r = monthly["ret_net"].dropna().astype(float)
    if len(r) == 0:
        return {"name": name, "months": 0}
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    years = len(r) / 12
    return {
        "name": name,
        "months": int(len(r)),
        "invested_months": int((monthly["n"] > 0).sum()) if "n" in monthly else int(len(r)),
        "total_return_pct": round((eq.iloc[-1] - 1) * 100, 2),
        "cagr_pct": round((eq.iloc[-1] ** (1 / years) - 1) * 100, 2),
        "avg_monthly_pct": round(r.mean() * 100, 2),
        "hit_rate_pct": round((r > 0).mean() * 100, 1),
        "max_drawdown_pct": round(dd.min() * 100, 2),
        "sharpe_monthly": round((r.mean() / r.std() * math.sqrt(12)) if r.std() else 0, 2),
    }


def split_summary(monthly: pd.DataFrame) -> dict:
    return {
        "train_2021_2024": summarize("train_2021_2024", monthly[(monthly.signal_month >= SIGNAL_START[:7]) & (monthly.signal_month <= "2024-12")]),
        "test_2025_2026": summarize("test_2025_2026", monthly[(monthly.signal_month >= TEST_START[:7]) & (monthly.signal_month <= END[:7])]),
    }


def label_3x_profile(signals: pd.DataFrame) -> dict:
    x = signals[signals["fwd_1m_ret"].notna()].copy()
    # Approximate future 12m max from monthly closes.
    x = x.sort_values(["stock_code", "signal_month"])
    x["future_12m_max"] = x.groupby("stock_code")["close"].transform(lambda s: s.shift(-1).rolling(12, min_periods=3).max().shift(-11))
    x["hit_3x_12m"] = x["future_12m_max"] / x["close"] >= 3
    cuts = {}
    for col in ["ret_1m", "ret_3m", "supply20", "short_cover_1m", "export_yoy"]:
        valid = x[col].notna()
        if valid.sum() < 100:
            continue
        top = x[valid & (x.groupby("signal_month")[col].rank(pct=True) >= 0.90)]
        base = x[valid]
        cuts[col] = {
            "base_3x_rate_pct": round(base["hit_3x_12m"].mean() * 100, 2),
            "top_decile_3x_rate_pct": round(top["hit_3x_12m"].mean() * 100, 2),
            "top_decile_rows": int(len(top)),
        }
    return cuts


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    prices = load_prices()
    signals = add_external_features(add_price_features(prices))
    bench = load_benchmark()

    summaries = []
    for rule in RULES:
        monthly, picks = run_rule(signals, bench, rule)
        s = summarize(rule.name, monthly)
        s.update(split_summary(monthly))
        s["avg_positions_when_invested"] = round(monthly.loc[monthly["n"] > 0, "n"].mean(), 1) if (monthly["n"] > 0).any() else 0
        summaries.append(s)
        monthly.to_csv(OUT_DIR / f"{rule.name}_monthly.csv", index=False)
        picks.to_csv(OUT_DIR / f"{rule.name}_picks.csv", index=False)

    b = bench[bench["fwd_1m_ret"].notna()].copy()
    b["ret_net"] = b["fwd_1m_ret"]
    b["n"] = 1
    bs = summarize("KOSPI_^KS11", b)
    bs.update(split_summary(b))
    summaries.append(bs)

    result = {
        "scope": {
            "start": SIGNAL_START,
            "end": END,
            "test_start": TEST_START,
            "transaction_cost": TCOST,
            "notes": [
                "Signals are monthly close based and next-month returns are used for evaluation.",
                "Short/lending data starts in 2021; Kiwoom credit and foreign holding are too recent for full-period backtest.",
                "HS export features use exact stock-code mappings and are shifted two months to approximate data availability.",
            ],
        },
        "summaries": summaries,
        "signal_3x_lift": label_3x_profile(signals),
    }
    (OUT_DIR / "tenbagger_signal_research.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    pd.DataFrame(summaries).to_csv(OUT_DIR / "tenbagger_signal_research.csv", index=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
