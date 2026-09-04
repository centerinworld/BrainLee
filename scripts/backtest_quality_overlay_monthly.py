#!/usr/bin/env python3
"""Execution-style monthly backtest for Strategy Center quality overlay.

This tests whether the validated auxiliary overlay improves actual monthly
portfolio returns, not just forward-label ranking quality.

Assumptions:
- Signal date: monthly `strategy_feature_snapshot.snapshot_date`.
- Execution: next trading day's open after each signal date.
- Rebalance: monthly, equal-weight TopN.
- Cost: 0.4% per invested month to approximate turnover, fees, and slippage.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

import pandas as pd

from research_quality_overlay_sweep import enrich_snapshots


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
TCOST = 0.004


def filter_liquid(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        (df["close_price"].fillna(0) > 0)
        & (df["vol_ratio_20d"].fillna(0) > 0.01)
        & (df["market_cap_억"].fillna(0).between(100, 100_000))
        & (df["pbr"].fillna(0).between(0.000001, 30))
        & (df["ret_60d"].fillna(0) <= 2.0)
    ].copy()


def load_prices(codes: set[str]) -> pd.DataFrame:
    conn = sqlite3.connect(DB, timeout=60)
    chunks = []
    code_list = sorted(codes)
    for i in range(0, len(code_list), 800):
        part = code_list[i:i + 800]
        ph = ",".join("?" for _ in part)
        chunks.append(pd.read_sql_query(
            f"""
            SELECT stock_code, substr(date,1,10) AS date, open, close
            FROM price_history
            WHERE stock_code IN ({ph})
              AND date >= '2020-01-01'
              AND open > 0
              AND close > 0
            ORDER BY stock_code, date
            """,
            conn,
            params=part,
        ))
    conn.close()
    if not chunks:
        return pd.DataFrame(columns=["stock_code", "date", "open", "close"])
    return pd.concat(chunks, ignore_index=True)


def build_next_open_lookup(prices: pd.DataFrame):
    by_code = {}
    for code, g in prices.groupby("stock_code"):
        g = g.sort_values("date")
        by_code[code] = (g["date"].tolist(), g["open"].astype(float).tolist())
    return by_code


def next_open(by_code, code: str, after_date: str):
    dates, opens = by_code.get(code, ([], []))
    if not dates:
        return None, None
    # small linear scan is fine because called only for selected names
    # and dates are monthly; avoid importing bisect in this small helper.
    lo, hi = 0, len(dates)
    while lo < hi:
        mid = (lo + hi) // 2
        if dates[mid] <= after_date:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(dates):
        return None, None
    return dates[lo], opens[lo]


def add_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in ["advance_good", "order_recent", "cash_good", "inventory_good", "quality_risk_count"]:
        df[col] = df[col].fillna(0).astype(int)
    df["model_rank_score"] = df["model_score_12m"].fillna(0).astype(float)
    df["score_model"] = df["model_rank_score"]
    df["score_advance"] = df["model_rank_score"] + 0.10 * df["advance_good"]
    df["score_order"] = df["model_rank_score"] + 0.06 * df["order_recent"]
    df["score_no_risk"] = df["model_rank_score"] - 0.05 * df["quality_risk_count"]
    df["quality_overlay_score"] = (
        df["model_rank_score"]
        + 0.10 * df["advance_good"]
        + 0.06 * df["order_recent"]
        + 0.01 * df["cash_good"]
        - 0.02 * df["inventory_good"]
    )
    df["quality_overlay_pool"] = (
        (df["quality_risk_count"] == 0)
        | (df["advance_good"] > 0)
        | (df["order_recent"] > 0)
    )
    return df


def select_month(df: pd.DataFrame, month: str, strategy: str, top_n: int) -> pd.DataFrame:
    g = df[df["snapshot_date"] == month].copy()
    if strategy == "model":
        return g.sort_values("model_rank_score", ascending=False).head(top_n)
    if strategy == "advance":
        return g.sort_values("score_advance", ascending=False).head(top_n)
    if strategy == "order":
        return g.sort_values("score_order", ascending=False).head(top_n)
    if strategy == "no_risk":
        return g.sort_values("score_no_risk", ascending=False).head(top_n)
    if strategy == "overlay":
        g = g[g["quality_overlay_pool"]]
        return g.sort_values("quality_overlay_score", ascending=False).head(top_n)
    raise ValueError(strategy)


def run_backtest(df: pd.DataFrame, by_code, strategy: str, top_n: int, start: str, end: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    months = sorted(m for m in df["snapshot_date"].unique().tolist() if start <= m <= end)
    rows = []
    picks = []
    for idx, month in enumerate(months[:-1]):
        next_month = months[idx + 1]
        selected = select_month(df, month, strategy, top_n)
        rets = []
        for _, r in selected.iterrows():
            code = r["stock_code"]
            entry_date, entry = next_open(by_code, code, month)
            exit_date, exitp = next_open(by_code, code, next_month)
            if not entry or not exitp:
                continue
            ret = exitp / entry - 1
            rets.append(ret)
            picks.append({
                "strategy": strategy,
                "top_n": top_n,
                "signal_month": month,
                "stock_code": code,
                "stock_name": r.get("stock_name"),
                "entry_date": entry_date,
                "exit_date": exit_date,
                "entry": entry,
                "exit": exitp,
                "ret": ret,
                "model_score": r.get("model_rank_score"),
                "overlay_score": r.get("quality_overlay_score"),
                "advance_score": r.get("score_advance"),
                "order_score": r.get("score_order"),
                "no_risk_score": r.get("score_no_risk"),
                "advance_good": int(r.get("advance_good") or 0),
                "order_recent": int(r.get("order_recent") or 0),
                "cash_good": int(r.get("cash_good") or 0),
                "inventory_good": int(r.get("inventory_good") or 0),
                "risk_count": int(r.get("quality_risk_count") or 0),
            })
        gross = sum(rets) / len(rets) if rets else 0.0
        net = gross - (TCOST if rets else 0.0)
        rows.append({"strategy": strategy, "top_n": top_n, "month": month, "n": len(rets), "ret_gross": gross, "ret_net": net})
    return pd.DataFrame(rows), pd.DataFrame(picks)


def summarize(name: str, monthly: pd.DataFrame) -> dict:
    if monthly.empty:
        return {"name": name, "months": 0}
    r = monthly["ret_net"].astype(float)
    equity = (1 + r).cumprod()
    roll_max = equity.cummax()
    dd = equity / roll_max - 1
    total = equity.iloc[-1] - 1
    years = max(len(r) / 12, 1e-9)
    cagr = equity.iloc[-1] ** (1 / years) - 1
    return {
        "name": name,
        "months": int(len(r)),
        "invested_months": int((monthly["n"] > 0).sum()),
        "avg_positions": round(float(monthly.loc[monthly["n"] > 0, "n"].mean() or 0), 2),
        "total_return_pct": round(total * 100, 2),
        "cagr_pct": round(cagr * 100, 2),
        "avg_monthly_pct": round(r.mean() * 100, 2),
        "median_monthly_pct": round(r.median() * 100, 2),
        "win_month_pct": round((r > 0).mean() * 100, 2),
        "max_drawdown_pct": round(dd.min() * 100, 2),
        "best_month_pct": round(r.max() * 100, 2),
        "worst_month_pct": round(r.min() * 100, 2),
    }


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    rows = enrich_snapshots()
    df = add_scores(filter_liquid(pd.DataFrame(rows)))
    prices = load_prices(set(df["stock_code"].astype(str)))
    by_code = build_next_open_lookup(prices)

    configs = [
        ("full_2020_2026", "2020-01-31", "2026-07-06"),
        ("train_2020_2024h1", "2020-01-31", "2024-06-30"),
        ("test_2024h2_2026", "2024-07-31", "2026-07-06"),
    ]
    all_monthly = []
    all_picks = []
    summaries = []
    for period_name, start, end in configs:
        for top_n in (10, 20):
            for strategy in ("model", "advance", "order", "no_risk", "overlay"):
                monthly, picks = run_backtest(df, by_code, strategy, top_n, start, end)
                monthly["period"] = period_name
                picks["period"] = period_name
                all_monthly.append(monthly)
                all_picks.append(picks)
                s = summarize(f"{period_name}_{strategy}_top{top_n}", monthly)
                s.update({"period": period_name, "strategy": strategy, "top_n": top_n})
                summaries.append(s)

    monthly_df = pd.concat(all_monthly, ignore_index=True)
    picks_df = pd.concat(all_picks, ignore_index=True)
    summary_df = pd.DataFrame(summaries)
    monthly_df.to_csv(OUT_DIR / "quality_overlay_monthly_backtest_monthly_20260726.csv", index=False)
    picks_df.to_csv(OUT_DIR / "quality_overlay_monthly_backtest_picks_20260726.csv", index=False)
    summary_df.to_csv(OUT_DIR / "quality_overlay_monthly_backtest_summary_20260726.csv", index=False)

    payload = {"assumptions": {"execution": "next trading day open", "rebalance": "monthly", "tcost": TCOST}, "summaries": summaries}
    (OUT_DIR / "quality_overlay_monthly_backtest_20260726.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Quality Overlay Monthly Backtest — 2026-07-26", "", "- execution: next trading day open", "- rebalance: monthly equal-weight", f"- cost: {TCOST*100:.2f}% per invested month", ""]
    for s in summaries:
        lines.append(
            f"- {s['name']}: total={s['total_return_pct']}%, CAGR={s['cagr_pct']}%, "
            f"avg_month={s['avg_monthly_pct']}%, win={s['win_month_pct']}%, MDD={s['max_drawdown_pct']}%"
        )
    (OUT_DIR / "quality_overlay_monthly_backtest_20260726.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
