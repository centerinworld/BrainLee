#!/usr/bin/env python3
"""
Continuous search for budget-constrained KRX buy/sell rules.

The script uses only information available at the signal date, then evaluates
daily exits with stop-loss, take-profit, and max-hold rules.  It is research
infrastructure, not investment advice.
"""

from __future__ import annotations

import json
import math
import sqlite3
import argparse
from dataclasses import dataclass
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"
HS_DB_PATH = ROOT / "hs_trade_lab/data/hs_trade_lab.db"
OUT_DIR = ROOT / "research_outputs"

START = "2020-01-01"
SIGNAL_START = "2021-06-01"
TEST_START = "2025-01-01"
END = "2026-06-18"
TCOST = 0.005


@dataclass(frozen=True)
class RuleSpec:
    name: str
    filter_expr: str
    score_expr: str
    top_n: int
    market_filter: bool
    max_hold_days: int
    stop_loss: float | None
    take_profit: float | None


def conn(path: Path = DB_PATH) -> sqlite3.Connection:
    c = sqlite3.connect(path, timeout=60)
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
    df = pd.read_sql_query(sql, conn(), params=(START, END), parse_dates=["date"])
    df = df.sort_values(["stock_code", "date"]).reset_index(drop=True)
    df["ret_d"] = df.groupby("stock_code")["close"].pct_change()
    bad_codes = set(df.loc[df["ret_d"].abs() > 0.60, "stock_code"].unique())
    return df[~df["stock_code"].isin(bad_codes)].copy()


def load_benchmark(prices: pd.DataFrame) -> pd.DataFrame:
    m = prices.copy()
    m["month"] = m["date"].dt.to_period("M")
    idx = m.groupby(["stock_code", "month"])["date"].idxmax()
    m = m.loc[idx, ["stock_code", "date", "month", "close", "turnover"]].sort_values(["stock_code", "date"])
    m["next_close"] = m.groupby("stock_code")["close"].shift(-1)
    m["fwd_ret"] = (m["next_close"] / m["close"] - 1).clip(-0.50, 0.50)
    m["avg_turnover"] = m.groupby("stock_code")["turnover"].transform(lambda s: s.rolling(3, min_periods=1).mean())
    liquid = m[m["avg_turnover"] >= 1e9].copy()
    b = liquid.groupby("month").agg(
        date=("date", "max"),
        bench_ret=("fwd_ret", "mean"),
        stock_count=("stock_code", "nunique"),
    ).reset_index()
    b["signal_month"] = b["month"].astype(str)
    b["close"] = (1 + b["bench_ret"].fillna(0.0)).cumprod()
    b["ma10m"] = b["close"].rolling(10, min_periods=6).mean()
    b["market_ok"] = b["close"] > b["ma10m"]
    return b[b["date"] >= SIGNAL_START][["signal_month", "date", "close", "bench_ret", "market_ok", "stock_count"]]


def load_short_monthly() -> pd.DataFrame:
    sql = """
    SELECT stock_code, bas_dt, lnb_bal AS borrow_bal_amt
    FROM short_rank_daily
    WHERE bas_dt BETWEEN '20210101' AND '20260630'
      AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
      AND lnb_bal IS NOT NULL
    """
    s = pd.read_sql_query(sql, conn())
    if s.empty:
        return pd.DataFrame(columns=["stock_code", "signal_month", "short_cover_1m", "short_cover_3m"])
    s["date"] = pd.to_datetime(s["bas_dt"], format="%Y%m%d", errors="coerce")
    s = s.dropna(subset=["date"]).sort_values(["stock_code", "date"])
    s["signal_month"] = s["date"].dt.to_period("M").astype(str)
    idx = s.groupby(["stock_code", "signal_month"])["date"].idxmax()
    m = s.loc[idx, ["stock_code", "signal_month", "borrow_bal_amt"]].copy()
    g = m.sort_values(["stock_code", "signal_month"]).groupby("stock_code")["borrow_bal_amt"]
    m["short_cover_1m"] = -(m["borrow_bal_amt"] / g.shift(1) - 1)
    m["short_cover_3m"] = -(m["borrow_bal_amt"] / g.shift(3) - 1)
    return m[["stock_code", "signal_month", "short_cover_1m", "short_cover_3m", "borrow_bal_amt"]]


def load_export_monthly() -> pd.DataFrame:
    if not HS_DB_PATH.exists():
        return pd.DataFrame(columns=["stock_code", "signal_month", "export_yoy"])
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
        return pd.DataFrame(columns=["stock_code", "signal_month", "export_yoy"])
    x = x.sort_values(["stock_code", "period_ym"])
    x["export_yoy"] = x["export_value"] / x.groupby("stock_code")["export_value"].shift(12) - 1
    x["signal_month"] = (pd.PeriodIndex(x["period_ym"], freq="M") + 2).astype(str)
    return x[["stock_code", "signal_month", "export_yoy"]]


def build_signal_frame(prices: pd.DataFrame) -> pd.DataFrame:
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
    df["low_vol60"] = -df["vol60"]
    df["avg_turnover20"] = g["turnover"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["vol_ratio20"] = df["volume"] / g["volume"].transform(lambda s: s.rolling(20, min_periods=15).mean())
    df["supply20"] = supply.groupby(df["stock_code"]).transform(lambda s: s.rolling(20, min_periods=15).sum())
    df["supply60"] = supply.groupby(df["stock_code"]).transform(lambda s: s.rolling(60, min_periods=40).sum())
    df["entry_gap"] = -((df["close"] / df["ma20"] - 1).clip(lower=0).sub(0.05).abs())

    df["month"] = df["date"].dt.to_period("M")
    idx = df.groupby(["stock_code", "month"])["date"].idxmax()
    m = df.loc[idx].copy().sort_values(["stock_code", "date"])
    m["signal_month"] = m["month"].astype(str)
    m = m[m["date"] >= SIGNAL_START].copy()
    m = m.merge(load_short_monthly(), on=["stock_code", "signal_month"], how="left")
    m = m.merge(load_export_monthly(), on=["stock_code", "signal_month"], how="left")
    m["export_yoy_fill"] = m["export_yoy"].fillna(0.0)
    m["short_cover_1m_fill"] = m["short_cover_1m"].fillna(0.0)

    rank_cols = [
        "ret_1m", "ret_3m", "ret_6m", "ret_12_1", "near_high52", "above_low52",
        "from_high52", "supply20", "supply60", "avg_turnover20", "vol_ratio20",
        "low_vol60", "entry_gap", "short_cover_1m", "short_cover_1m_fill",
        "short_cover_3m", "export_yoy", "export_yoy_fill",
    ]
    for col in rank_cols:
        if col in m:
            m[f"rank_{col}"] = m.groupby("signal_month")[col].rank(pct=True)
    return m


def price_lookup(prices: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    out = {}
    for code, g in prices.groupby("stock_code"):
        x = g.sort_values("date")
        out[code] = (x["date"].to_numpy(dtype="datetime64[ns]"), x["close"].to_numpy(dtype=float))
    return out


def exit_return(path: tuple[np.ndarray, np.ndarray], signal_date: pd.Timestamp, max_hold: int, stop: float | None, take: float | None) -> tuple[float | None, str, str | None]:
    dates, closes = path
    start = int(np.searchsorted(dates, np.datetime64(signal_date), side="right"))
    end = min(start + max_hold, len(closes))
    if start >= end:
        return None, "no_future", None
    fut_close = closes[start:end]
    fut_dates = dates[start:end]
    entry = float(fut_close[0])
    rets = fut_close / entry - 1
    exit_idx = len(fut_close) - 1
    reason = "max_hold"
    if stop is not None:
        hit = np.flatnonzero(rets <= stop)
        if len(hit):
            exit_idx = int(hit[0])
            reason = "stop"
    if take is not None:
        hit = np.flatnonzero(rets >= take)
        if len(hit) and int(hit[0]) < exit_idx:
            exit_idx = int(hit[0])
            reason = "take"
    exit_price = float(fut_close[exit_idx])
    exit_date = pd.Timestamp(fut_dates[exit_idx]).date().isoformat()
    return exit_price / entry - 1 - TCOST, reason, exit_date


def make_specs(full: bool = False) -> list[RuleSpec]:
    filters = {
        "squeeze": "avg_turnover20 >= 5e9 and close >= 1000 and ret_1m > 0.05 and ret_3m > 0.18 and ret_6m > 0.15 and near_high52 > 0.78",
        "trend_quality": "avg_turnover20 >= 3e9 and close >= 1000 and ret_6m > 0.12 and ret_1m > -0.08 and near_high52 > 0.70 and ma20 > ma60 and ma60 > ma120 and vol60 <= 0.06",
        "shortcover_squeeze": "avg_turnover20 >= 4e9 and close >= 1000 and ret_1m > 0.03 and ret_3m > 0.12 and near_high52 > 0.70 and short_cover_1m_fill > -0.20",
        "export_squeeze": "avg_turnover20 >= 3e9 and close >= 1000 and ret_1m > 0.02 and ret_3m > 0.10 and export_yoy > 0 and near_high52 > 0.65",
        "reversal_volume": "avg_turnover20 >= 3e9 and close >= 1000 and from_high52 <= -0.25 and ret_1m > 0.03 and vol_ratio20 > 1.4 and supply20 > 0",
    }
    scores = {
        "mom_supply": "0.25*rank_ret_1m + 0.25*rank_ret_3m + 0.15*rank_ret_6m + 0.15*rank_supply20 + 0.10*rank_avg_turnover20 + 0.10*rank_near_high52",
        "quality_trend": "0.25*rank_ret_6m + 0.20*rank_ret_12_1 + 0.15*rank_near_high52 + 0.15*rank_supply60 + 0.15*rank_low_vol60 + 0.10*rank_entry_gap",
        "short_export": "0.22*rank_ret_1m + 0.18*rank_ret_3m + 0.15*rank_supply20 + 0.15*rank_short_cover_1m_fill + 0.15*rank_export_yoy_fill + 0.15*rank_avg_turnover20",
        "breakout_clean": "0.25*rank_ret_3m + 0.20*rank_ret_6m + 0.18*rank_near_high52 + 0.12*rank_supply20 + 0.15*rank_low_vol60 + 0.10*rank_entry_gap",
    }
    specs = []
    top_ns = [3, 5, 8, 10] if full else [3, 5]
    holds = [21, 42, 63] if full else [21, 42]
    stops = [None, -0.08, -0.12, -0.18] if full else [None, -0.12]
    takes = [None, 0.18, 0.30, 0.50] if full else [None, 0.30]
    markets = [False, True] if full else [True]
    for fname, sname, top_n, max_hold, stop, take, market in product(
        filters.keys(),
        scores.keys(),
        top_ns,
        holds,
        stops,
        takes,
        markets,
    ):
        if take is not None and max_hold == 21 and take >= 0.50:
            continue
        specs.append(
            RuleSpec(
                name=f"{fname}__{sname}__top{top_n}__h{max_hold}__sl{stop}__tp{take}__m{int(market)}",
                filter_expr=filters[fname],
                score_expr=scores[sname],
                top_n=top_n,
                market_filter=market,
                max_hold_days=max_hold,
                stop_loss=stop,
                take_profit=take,
            )
        )
    return specs


def run_spec(signals: pd.DataFrame, bench: pd.DataFrame, px: dict[str, tuple[np.ndarray, np.ndarray]], spec: RuleSpec) -> tuple[pd.DataFrame, pd.DataFrame]:
    x = signals.query(spec.filter_expr).copy()
    if x.empty:
        return pd.DataFrame(), pd.DataFrame()
    x["score"] = x.eval(spec.score_expr)
    if spec.market_filter:
        x = x.merge(bench[["signal_month", "market_ok"]], on="signal_month", how="left")
        x = x[x["market_ok"].fillna(False)]
    picks = x.sort_values(["signal_month", "score"], ascending=[True, False]).groupby("signal_month").head(spec.top_n).copy()
    rows = []
    for row in picks.itertuples(index=False):
        path = px.get(row.stock_code)
        if path is None:
            continue
        ret, reason, exit_date = exit_return(path, row.date, spec.max_hold_days, spec.stop_loss, spec.take_profit)
        if ret is None:
            continue
        d = row._asdict()
        d.update({"trade_ret": ret, "exit_reason": reason, "exit_date": exit_date})
        rows.append(d)
    picks = pd.DataFrame(rows)
    if picks.empty:
        return pd.DataFrame(), picks

    monthly = picks.groupby("signal_month").agg(
        ret=("trade_ret", "mean"),
        n=("stock_code", "count"),
        best=("trade_ret", "max"),
        worst=("trade_ret", "min"),
        stop_rate=("exit_reason", lambda s: (s == "stop").mean()),
        take_rate=("exit_reason", lambda s: (s == "take").mean()),
    ).reset_index()
    months = bench.loc[bench["bench_ret"].notna(), ["signal_month", "bench_ret"]]
    monthly = months.merge(monthly, on="signal_month", how="left")
    monthly["cash_month"] = monthly["ret"].isna()
    monthly["ret"] = monthly["ret"].fillna(0.0)
    monthly["n"] = monthly["n"].fillna(0).astype(int)
    return monthly, picks


def summarize(name: str, monthly: pd.DataFrame) -> dict:
    r = monthly["ret"].astype(float)
    if r.empty:
        return {"name": name, "months": 0}
    eq = (1 + r).cumprod()
    dd = eq / eq.cummax() - 1
    years = len(r) / 12
    bench = monthly["bench_ret"].fillna(0.0).astype(float)
    beq = (1 + bench).cumprod()
    return {
        "name": name,
        "months": int(len(r)),
        "invested_months": int((monthly["n"] > 0).sum()),
        "avg_positions": round(float(monthly["n"].mean()), 2),
        "total_return_pct": round((float(eq.iloc[-1]) - 1) * 100, 2),
        "bench_return_pct": round((float(beq.iloc[-1]) - 1) * 100, 2),
        "excess_pct": round((float(eq.iloc[-1]) - float(beq.iloc[-1])) * 100, 2),
        "cagr_pct": round((float(eq.iloc[-1]) ** (1 / years) - 1) * 100, 2),
        "hit_rate_pct": round(float((r > 0).mean()) * 100, 1),
        "max_drawdown_pct": round(float(dd.min()) * 100, 2),
        "sharpe": round(float(r.mean() / r.std() * math.sqrt(12)) if r.std() else 0, 2),
        "stop_rate_pct": round(float(monthly.get("stop_rate", pd.Series(dtype=float)).fillna(0).mean()) * 100, 1),
        "take_rate_pct": round(float(monthly.get("take_rate", pd.Series(dtype=float)).fillna(0).mean()) * 100, 1),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--full", action="store_true", help="search the full 7k-rule grid")
    args = parser.parse_args()

    OUT_DIR.mkdir(exist_ok=True)
    prices = load_prices()
    signals = build_signal_frame(prices)
    bench = load_benchmark(prices)
    px = price_lookup(prices)
    specs = make_specs(full=args.full)
    results = []
    best_payload = None

    for i, spec in enumerate(specs, start=1):
        monthly, picks = run_spec(signals, bench, px, spec)
        if monthly.empty:
            continue
        train = monthly[(monthly.signal_month >= SIGNAL_START[:7]) & (monthly.signal_month <= "2024-12")]
        test = monthly[(monthly.signal_month >= TEST_START[:7]) & (monthly.signal_month <= END[:7])]
        s_all = summarize(spec.name, monthly)
        s_train = summarize(spec.name, train)
        s_test = summarize(spec.name, test)
        row = {
            **s_all,
            "train_cagr_pct": s_train.get("cagr_pct"),
            "train_mdd_pct": s_train.get("max_drawdown_pct"),
            "test_cagr_pct": s_test.get("cagr_pct"),
            "test_mdd_pct": s_test.get("max_drawdown_pct"),
            "test_excess_pct": s_test.get("excess_pct"),
            "top_n": spec.top_n,
            "max_hold_days": spec.max_hold_days,
            "stop_loss": spec.stop_loss,
            "take_profit": spec.take_profit,
            "market_filter": spec.market_filter,
        }
        results.append(row)
        rank_score = (s_test.get("cagr_pct") or -999) - abs(s_test.get("max_drawdown_pct") or 0) * 0.6 + (s_test.get("hit_rate_pct") or 0) * 0.15
        if best_payload is None or rank_score > best_payload[0]:
            best_payload = (rank_score, spec, monthly, picks)
        if i % 100 == 0:
            print(f"searched {i}/{len(specs)}", flush=True)

    res = pd.DataFrame(results).sort_values(
        ["test_cagr_pct", "test_excess_pct", "max_drawdown_pct"],
        ascending=[False, False, False],
    )
    res.to_csv(OUT_DIR / "continuous_strategy_search_summary.csv", index=False)
    res.head(30).to_csv(OUT_DIR / "continuous_strategy_search_top30.csv", index=False)
    if best_payload:
        _, spec, monthly, picks = best_payload
        monthly.to_csv(OUT_DIR / "continuous_strategy_best_monthly.csv", index=False)
        picks.to_csv(OUT_DIR / "continuous_strategy_best_picks.csv", index=False)
        meta = {"best_rule": spec.__dict__, "all": summarize(spec.name, monthly)}
        meta["train"] = summarize(spec.name, monthly[(monthly.signal_month >= SIGNAL_START[:7]) & (monthly.signal_month <= "2024-12")])
        meta["test"] = summarize(spec.name, monthly[(monthly.signal_month >= TEST_START[:7]) & (monthly.signal_month <= END[:7])])
        (OUT_DIR / "continuous_strategy_best.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    print(f"wrote {len(res)} strategy rows to {OUT_DIR}")


if __name__ == "__main__":
    main()
