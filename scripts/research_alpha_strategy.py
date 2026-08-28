#!/usr/bin/env python3
"""
Research KRX alpha signals against KOSPI.

This is an offline research script, not live trading advice.  It deliberately
uses only daily price/supply fields that are observable at the signal date and
keeps a train/test split so a single lucky period does not define the rule.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


DB_PATH = Path("/Applications/stock_dashboard/stock.db")
OUT_DIR = Path("/Applications/stock_dashboard/research_outputs")
START = "2020-01-01"
SIGNAL_START = "2021-06-01"
TEST_START = "2025-01-01"
END = "2026-06-05"
TCOST = 0.003  # buy+sell round-trip cost/slippage approximation per rebalance


@dataclass(frozen=True)
class StrategySpec:
    name: str
    top_n: int
    market_filter: bool
    filter_expr: str
    score_expr: str


SPECS = [
    StrategySpec(
        name="regime_squeeze_top3",
        top_n=3,
        market_filter=True,
        filter_expr=(
            "avg_turnover20 >= 5e9 and close >= 1000 and ret_1m > 0.05 "
            "and ret_3m > 0.20 and ret_6m > 0.20 and near_high52 > 0.80"
        ),
        score_expr=(
            "0.30*rank_ret_1m + 0.20*rank_ret_3m + 0.20*rank_supply20 "
            "+ 0.20*rank_avg_turnover20 + 0.10*rank_near_high52"
        ),
    ),
    StrategySpec(
        name="regime_squeeze_top10",
        top_n=10,
        market_filter=True,
        filter_expr=(
            "avg_turnover20 >= 5e9 and close >= 1000 and ret_1m > 0.05 "
            "and ret_3m > 0.20 and ret_6m > 0.20 and near_high52 > 0.80"
        ),
        score_expr=(
            "0.30*rank_ret_1m + 0.20*rank_ret_3m + 0.20*rank_supply20 "
            "+ 0.20*rank_avg_turnover20 + 0.10*rank_near_high52"
        ),
    ),
    StrategySpec(
        name="rs_12_1_top10",
        top_n=10,
        market_filter=False,
        filter_expr="avg_turnover20 >= 2e9 and close >= 1000 and ret_12_1 == ret_12_1",
        score_expr="rank_ret_12_1",
    ),
    StrategySpec(
        name="rs_6m_supply_top10",
        top_n=10,
        market_filter=False,
        filter_expr=(
            "avg_turnover20 >= 2e9 and close >= 1000 and ret_6m > 0.15 "
            "and ret_1m > -0.10 and near_high52 >= 0.70 and supply60 > 0"
        ),
        score_expr=(
            "0.30*rank_ret_6m + 0.20*rank_ret_3m + 0.15*rank_near_high52 "
            "+ 0.15*rank_supply60 + 0.10*rank_low_vol60 + 0.10*rank_entry_gap"
        ),
    ),
    StrategySpec(
        name="breakout_base_top10",
        top_n=10,
        market_filter=False,
        filter_expr=(
            "avg_turnover20 >= 2e9 and close >= 1000 and ret_3m >= 0.10 "
            "and ret_3m <= 1.20 and ret_1m >= -0.05 and ret_1m <= 0.65 "
            "and near_high52 >= 0.75 and near_high52 <= 1.08 and ma20 > ma60 "
            "and ma60 > ma120 and supply20 > 0"
        ),
        score_expr=(
            "0.25*rank_ret_3m + 0.20*rank_ret_6m + 0.15*rank_near_high52 "
            "+ 0.15*rank_supply20 + 0.10*rank_low_vol60 + 0.15*rank_entry_gap"
        ),
    ),
    StrategySpec(
        name="quality_trend_proxy_top12",
        top_n=12,
        market_filter=True,
        filter_expr=(
            "avg_turnover20 >= 3e9 and close >= 1000 and ret_6m > 0.10 "
            "and ret_1m > -0.08 and near_high52 >= 0.72 and ma20 > ma60 "
            "and ma60 > ma120 and close > ma200 and vol60 <= 0.055"
        ),
        score_expr=(
            "0.30*rank_ret_6m + 0.20*rank_ret_12_1 + 0.15*rank_near_high52 "
            "+ 0.15*rank_supply60 + 0.10*rank_low_vol60 + 0.10*rank_entry_gap"
        ),
    ),
    StrategySpec(
        name="defensive_rs_supply_top8",
        top_n=8,
        market_filter=True,
        filter_expr=(
            "avg_turnover20 >= 5e9 and close >= 1000 and ret_6m > 0.20 "
            "and ret_1m > -0.05 and ret_1m < 0.50 and near_high52 >= 0.80 "
            "and supply60 > 0 and vol60 <= 0.050 and close > ma120"
        ),
        score_expr=(
            "0.30*rank_ret_6m + 0.20*rank_ret_3m + 0.20*rank_supply60 "
            "+ 0.15*rank_near_high52 + 0.15*rank_low_vol60"
        ),
    ),
]


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


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
    df = pd.read_sql_query(sql, _conn(), params=(START, END), parse_dates=["date"])
    df = df.sort_values(["stock_code", "date"]).reset_index(drop=True)
    df["ret_d"] = df.groupby("stock_code")["close"].pct_change()

    # Split/corporate-action glitches are the source of several 900% backtest
    # outliers.  Korean daily limits make >60% one-day moves suspicious here.
    bad_codes = set(df.loc[df["ret_d"].abs() > 0.60, "stock_code"].unique())
    df = df[~df["stock_code"].isin(bad_codes)].copy()
    return df


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("stock_code", group_keys=False)
    close = g["close"]
    volume = g["volume"]
    turnover = g["turnover"]
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
    df["vol60"] = g["ret_d"].transform(lambda s: s.rolling(60, min_periods=40).std())
    df["low_vol60"] = -df["vol60"]
    df["avg_turnover20"] = turnover.transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["avg_turnover60"] = turnover.transform(lambda s: s.rolling(60, min_periods=40).mean())
    df["vol_ratio20"] = df["volume"] / volume.transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["supply20"] = supply.groupby(df["stock_code"]).transform(lambda s: s.rolling(20, min_periods=15).sum())
    df["supply60"] = supply.groupby(df["stock_code"]).transform(lambda s: s.rolling(60, min_periods=40).sum())
    df["entry_gap"] = -((df["close"] / df["ma20"] - 1).clip(lower=0).sub(0.05).abs())

    # Month-end signal rows and next-month holding return.
    df["month"] = df["date"].dt.to_period("M")
    month_last_idx = df.groupby(["stock_code", "month"])["date"].idxmax()
    m = df.loc[month_last_idx].copy().sort_values(["stock_code", "date"])
    m["next_close"] = m.groupby("stock_code")["close"].shift(-1)
    m["next_date"] = m.groupby("stock_code")["date"].shift(-1)
    m["fwd_1m_ret"] = m["next_close"] / m["close"] - 1
    m["signal_month"] = m["date"].dt.to_period("M").astype(str)

    rank_cols = [
        "ret_1m",
        "ret_3m",
        "ret_6m",
        "ret_12_1",
        "near_high52",
        "above_low52",
        "supply20",
        "supply60",
        "low_vol60",
        "entry_gap",
        "avg_turnover20",
    ]
    for col in rank_cols:
        m[f"rank_{col}"] = m.groupby("signal_month")[col].rank(pct=True)
    return m[m["date"] >= SIGNAL_START].copy()


def load_benchmark() -> pd.DataFrame:
    b = pd.read_sql_query(
        """
        SELECT date, close
        FROM price_history
        WHERE stock_code = '^KS11' AND date BETWEEN ? AND ?
        ORDER BY date
        """,
        _conn(),
        params=(SIGNAL_START, END),
        parse_dates=["date"],
    )
    b["month"] = b["date"].dt.to_period("M")
    b = b.loc[b.groupby("month")["date"].idxmax()].copy()
    b["fwd_1m_ret"] = b["close"].shift(-1) / b["close"] - 1
    b["ma200"] = b["close"].rolling(10, min_periods=6).mean()  # monthly proxy
    b["market_ok"] = b["close"] > b["ma200"]
    b["signal_month"] = b["month"].astype(str)
    return b[["signal_month", "date", "close", "fwd_1m_ret", "market_ok"]]


def portfolio_returns(signals: pd.DataFrame, bench: pd.DataFrame, spec: StrategySpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    m = signals.query(spec.filter_expr).copy()
    m["score"] = m.eval(spec.score_expr)
    if spec.market_filter:
        m = m.merge(bench[["signal_month", "market_ok"]], on="signal_month", how="left")
        m = m[m["market_ok"].fillna(False)]

    picks = (
        m.sort_values(["signal_month", "score"], ascending=[True, False])
        .groupby("signal_month")
        .head(spec.top_n)
        .copy()
    )
    picks = picks[picks["fwd_1m_ret"].notna()].copy()
    port = (
        picks.groupby("signal_month")
        .agg(
            ret=("fwd_1m_ret", "mean"),
            n=("stock_code", "count"),
            avg_score=("score", "mean"),
            best=("fwd_1m_ret", "max"),
            worst=("fwd_1m_ret", "min"),
        )
        .reset_index()
    )
    all_months = bench.loc[bench["fwd_1m_ret"].notna(), ["signal_month"]].copy()
    port = all_months.merge(port, on="signal_month", how="left")
    port["cash_month"] = port["ret"].isna()
    port["ret"] = port["ret"].fillna(0.0)
    port["n"] = port["n"].fillna(0).astype(int)
    port["avg_score"] = port["avg_score"].fillna(0.0)
    port["ret_net"] = np.where(port["cash_month"], 0.0, port["ret"] - TCOST)
    return port, picks


def summarize_returns(name: str, monthly: pd.DataFrame, ret_col: str = "ret_net") -> dict:
    r = monthly[ret_col].dropna().astype(float)
    if len(r) == 0:
        return {"name": name, "months": 0}
    eq = (1 + r).cumprod()
    years = len(r) / 12
    cagr = eq.iloc[-1] ** (1 / years) - 1 if years > 0 else np.nan
    dd = eq / eq.cummax() - 1
    return {
        "name": name,
        "months": int(len(r)),
        "total_return_pct": round((eq.iloc[-1] - 1) * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "avg_monthly_pct": round(r.mean() * 100, 2),
        "hit_rate_pct": round((r > 0).mean() * 100, 1),
        "max_drawdown_pct": round(dd.min() * 100, 2),
        "sharpe_monthly": round((r.mean() / r.std() * math.sqrt(12)) if r.std() else 0, 2),
    }


def add_splits(summary: dict, monthly: pd.DataFrame) -> dict:
    for label, start, end in [
        ("train_2021_2024", SIGNAL_START, "2024-12-31"),
        ("test_2025_2026", TEST_START, END),
    ]:
        sub = monthly[(monthly["signal_month"] >= start[:7]) & (monthly["signal_month"] <= end[:7])]
        summary[label] = summarize_returns(label, sub)
    return summary


def profile_3x() -> dict:
    conn = _conn()
    rows = pd.read_sql_query(
        """
        WITH monthly AS (
          SELECT
            p.stock_code,
            substr(p.date, 1, 7) AS ym,
            MAX(p.date) AS month_date
          FROM price_history p
          JOIN stock_universe su ON su.stock_code = p.stock_code
          WHERE p.date BETWEEN '2021-01-01' AND '2026-06-05'
            AND p.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
            AND su.market IN ('KOSPI', 'KOSDAQ')
            AND p.close > 0
          GROUP BY p.stock_code, ym
        ),
        px AS (
          SELECT m.stock_code, m.ym, p.close
          FROM monthly m
          JOIN price_history p ON p.stock_code=m.stock_code AND p.date=m.month_date
        ),
        roll AS (
          SELECT
            stock_code, ym, close,
            MIN(close) OVER (PARTITION BY stock_code ORDER BY ym ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS min_12m,
            MAX(close) OVER (PARTITION BY stock_code ORDER BY ym ROWS BETWEEN 11 PRECEDING AND CURRENT ROW) AS max_12m
          FROM px
        )
        SELECT
          stock_code,
          MIN(ym) AS first_3x_month,
          MAX(max_12m / NULLIF(min_12m, 0)) AS max_12m_ratio
        FROM roll
        WHERE min_12m > 0 AND max_12m / min_12m >= 3
        GROUP BY stock_code
        """,
        conn,
    )
    return {
        "three_x_stock_count": int(len(rows)),
        "median_max_12m_ratio": round(float(rows["max_12m_ratio"].median()), 2) if len(rows) else None,
        "p90_max_12m_ratio": round(float(rows["max_12m_ratio"].quantile(0.9)), 2) if len(rows) else None,
    }


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    prices = load_prices()
    signals = add_features(prices)
    bench = load_benchmark()

    bench_monthly = bench[bench["fwd_1m_ret"].notna()].copy()
    bench_monthly["ret_net"] = bench_monthly["fwd_1m_ret"]
    all_eligible = (
        signals.query("avg_turnover20 >= 2e9 and close >= 1000 and fwd_1m_ret == fwd_1m_ret")
        .groupby("signal_month")
        .agg(ret=("fwd_1m_ret", "mean"), n=("stock_code", "count"))
        .reset_index()
    )
    all_eligible["ret_net"] = all_eligible["ret"]

    summaries = []
    pick_paths = {}
    for spec in SPECS:
        monthly, picks = portfolio_returns(signals, bench, spec)
        summary = summarize_returns(spec.name, monthly)
        summary = add_splits(summary, monthly)
        summary["avg_names_per_month"] = round(float(monthly["n"].mean()), 1) if not monthly.empty else 0
        summaries.append(summary)
        monthly.to_csv(OUT_DIR / f"{spec.name}_monthly.csv", index=False)
        picks.to_csv(OUT_DIR / f"{spec.name}_picks.csv", index=False)
        pick_paths[spec.name] = str(OUT_DIR / f"{spec.name}_picks.csv")

    summaries.append(add_splits(summarize_returns("KOSPI_^KS11", bench_monthly), bench_monthly))
    summaries.append(add_splits(summarize_returns("all_liquid_equal_weight", all_eligible), all_eligible))

    result = {
        "run_scope": {
            "db": str(DB_PATH),
            "start": SIGNAL_START,
            "end": END,
            "test_start": TEST_START,
            "transaction_cost_per_rebalance": TCOST,
            "universe_note": "6-digit KOSPI/KOSDAQ rows with stock_universe/stock_meta market labels; symbols with >60% one-day returns removed as corporate-action glitches.",
            "signal_timing_note": "monthly close signal, next monthly return; no future returns in score.",
        },
        "three_x_profile": profile_3x(),
        "summaries": summaries,
        "pick_paths": pick_paths,
    }
    (OUT_DIR / "alpha_strategy_summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    pd.DataFrame(summaries).to_csv(OUT_DIR / "alpha_strategy_summary.csv", index=False)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
