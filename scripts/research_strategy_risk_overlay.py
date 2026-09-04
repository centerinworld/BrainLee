#!/usr/bin/env python3
"""Test stop-loss and trailing-stop overlays on discovered monthly strategies."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.research_best_2021_2025_strategy import (
    BUDGET,
    DB_PATH,
    OUT_DIR,
    TCOST,
    add_cross_sectional_ranks,
    backtest_rule,
    candidate_rules,
    load_frame,
    make_filters,
    summarize,
)


SUMMARY_PATH = OUT_DIR / "best_2021_2025_strategy_summary.json"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def load_daily_prices(codes: list[str]) -> pd.DataFrame:
    placeholders = ",".join("?" for _ in codes)
    con = _conn()
    px = pd.read_sql_query(
        f"""
        SELECT stock_code, date, open, high, low, close
        FROM price_history
        WHERE stock_code IN ({placeholders})
          AND date BETWEEN '2021-01-01' AND '2025-04-30'
          AND open > 0 AND high > 0 AND low > 0 AND close > 0
        ORDER BY stock_code, date
        """,
        con,
        params=codes,
        parse_dates=["date"],
    )
    con.close()
    return px


def simulate_pick_return(rows: pd.DataFrame, entry_open: float, mode: str) -> tuple[float, str, str | None]:
    if rows.empty or not entry_open:
        return np.nan, "missing", None
    exit_close = float(rows.iloc[-1]["close"])
    base_ret = exit_close / entry_open - 1
    if mode == "none":
        return base_ret, "month_end", str(rows.iloc[-1]["date"].date())

    stop_pct = None
    trail_pct = None
    if mode.startswith("stop"):
        stop_pct = float(mode.replace("stop", "")) / 100.0
    elif mode.startswith("trail"):
        trail_pct = float(mode.replace("trail", "")) / 100.0

    peak_close = entry_open
    for _, row in rows.iterrows():
        close = float(row["close"])
        if trail_pct is not None:
            peak_close = max(peak_close, close)
            if close <= peak_close * (1 - trail_pct):
                return close / entry_open - 1, "trailing_stop", str(row["date"].date())
        if stop_pct is not None and close <= entry_open * (1 - stop_pct):
            return close / entry_open - 1, "fixed_stop", str(row["date"].date())
    return base_ret, "month_end", str(rows.iloc[-1]["date"].date())


def overlay_strategy(picks: pd.DataFrame, daily: pd.DataFrame, mode: str) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    px_by_code = {code: g.sort_values("date") for code, g in daily.groupby("stock_code")}
    out = picks.copy()
    overlay_returns = []
    exit_types = []
    overlay_exit_dates = []
    for _, pick in out.iterrows():
        code = pick["stock_code"]
        entry_date = pd.to_datetime(pick["entry_date"])
        exit_date = pd.to_datetime(pick["exit_date"])
        rows = px_by_code.get(code, pd.DataFrame())
        if not rows.empty:
            rows = rows[(rows["date"] >= entry_date) & (rows["date"] <= exit_date)].copy()
        ret, exit_type, overlay_exit_date = simulate_pick_return(rows, float(pick["entry_open"]), mode)
        overlay_returns.append(ret)
        exit_types.append(exit_type)
        overlay_exit_dates.append(overlay_exit_date)
    out["overlay_ret"] = overlay_returns
    out["overlay_exit_type"] = exit_types
    out["overlay_exit_date"] = overlay_exit_dates
    out = out[out["overlay_ret"].notna()].copy()
    out["fwd_1m_ret"] = out["overlay_ret"]
    out["profit_krw_net"] = out["position_value"] * (out["fwd_1m_ret"] - TCOST)

    months = pd.DataFrame({"signal_month": sorted(picks["signal_month"].unique())})
    monthly = out.groupby("signal_month").agg(
        n=("stock_code", "count"),
        gross_ret=("fwd_1m_ret", "mean"),
        trade_hit_rate=("fwd_1m_ret", lambda s: float((s > 0).mean())),
        best=("fwd_1m_ret", "max"),
        worst=("fwd_1m_ret", "min"),
        stop_ratio=("overlay_exit_type", lambda s: float((s != "month_end").mean())),
    ).reset_index()
    monthly = months.merge(monthly, on="signal_month", how="left")
    monthly["n"] = monthly["n"].fillna(0).astype(int)
    monthly["cash_month"] = monthly["n"].eq(0)
    monthly["gross_ret"] = monthly["gross_ret"].fillna(0)
    monthly["net_ret"] = np.where(monthly["cash_month"], 0.0, monthly["gross_ret"] - TCOST)
    monthly["equity"] = BUDGET * (1 + monthly["net_ret"]).cumprod()
    summary = summarize(mode, monthly, out)
    summary["stop_ratio_pct"] = round(float((out["overlay_exit_type"] != "month_end").mean() * 100), 1)
    return summary, monthly, out


def main() -> None:
    m = add_cross_sectional_ranks(load_frame())
    filters = make_filters(m)
    rules = {r.name: r for r in candidate_rules()}
    selected = []
    if SUMMARY_PATH.exists():
        payload = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        selected.extend([payload["best_return"]["name"], payload["balanced_recommendation"]["name"], payload["best_win_rate"]["name"]])
        selected.extend([row["name"] for row in payload.get("top10", [])[:6]])
    selected = list(dict.fromkeys([name for name in selected if name in rules]))

    base_results = {}
    all_picks = []
    for name in selected:
        _, monthly, picks = backtest_rule(m, filters, rules[name])
        if not picks.empty:
            picks = picks.copy()
            picks["strategy"] = name
            all_picks.append(picks)
            base_results[name] = (monthly, picks)
    if not all_picks:
        raise SystemExit("no selected picks")

    daily = load_daily_prices(sorted(set(pd.concat(all_picks)["stock_code"].astype(str))))
    modes = ["none", "stop8", "stop10", "stop12", "stop15", "stop20", "trail10", "trail12", "trail15", "trail20"]
    rows = []
    best_payload = {}
    for name, (_base_monthly, picks) in base_results.items():
        for mode in modes:
            summary, monthly, overlay_picks = overlay_strategy(picks, daily, mode)
            summary["strategy"] = name
            summary["overlay"] = mode
            rows.append(summary)
            key = f"{name}__{mode}"
            best_payload[key] = (summary, monthly, overlay_picks)

    result = pd.DataFrame(rows)
    result["robust_score"] = (
        result["total_return_pct"]
        + 0.40 * result["active_month_hit_rate_pct"]
        + 0.25 * result["test_return_pct"]
        + 0.70 * result["max_drawdown_pct"]
    )
    result = result.sort_values(["robust_score", "total_return_pct"], ascending=[False, False])
    result.to_csv(OUT_DIR / "best_2021_2025_risk_overlay_results.csv", index=False)

    top_key = f"{result.iloc[0]['strategy']}__{result.iloc[0]['overlay']}"
    summary, monthly, overlay_picks = best_payload[top_key]
    monthly.to_csv(OUT_DIR / f"best_2021_2025_risk_overlay_{top_key}_monthly.csv", index=False)
    overlay_picks.to_csv(OUT_DIR / f"best_2021_2025_risk_overlay_{top_key}_picks.csv", index=False)
    payload = {
        "top": result.head(10).to_dict(orient="records"),
        "selected_top_key": top_key,
        "output_files": {
            "all_results": str(OUT_DIR / "best_2021_2025_risk_overlay_results.csv"),
            "top_monthly": str(OUT_DIR / f"best_2021_2025_risk_overlay_{top_key}_monthly.csv"),
            "top_picks": str(OUT_DIR / f"best_2021_2025_risk_overlay_{top_key}_picks.csv"),
        },
    }
    (OUT_DIR / "best_2021_2025_risk_overlay_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
