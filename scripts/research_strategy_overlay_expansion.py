#!/usr/bin/env python3
"""Research expanded Strategy Center overlays.

This script is deliberately a research harness, not a production signal.
It asks whether newly collected catalyst/risk data can improve the existing
Strategy Center monthly ranking without changing the core model.

Tested overlays:
- order/backlog catalysts
- program and investor supply confirmation
- sector relative strength
- recent dilution risk exclusion
- KOSPI regime cash filter

Execution model:
- monthly signal snapshot
- buy next trading day's open
- exit next monthly snapshot's next trading day's open
- equal-weight top-N
- 0.4% monthly cost when invested
"""

from __future__ import annotations

import bisect
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "stock.db"
OUT_DIR = ROOT / "research_outputs"
TCOST = 0.004
AS_OF_CUTOFF = "2026-06-30"
TRAIN_END = "2024-06-30"


@dataclass(frozen=True)
class TimedValue:
    as_of: str
    value: float


def norm_date(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None


def quarter_available(fy: int, fq: int, lag_days: int = 60) -> str:
    month = int(fq) * 3
    day = 31 if month in (3, 12) else 30
    return (date(int(fy), month, day) + timedelta(days=lag_days)).isoformat()


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone() is not None


def load_snapshots(conn: sqlite3.Connection) -> pd.DataFrame:
    return pd.read_sql_query(
        """
        SELECT *
        FROM strategy_feature_snapshot
        WHERE snapshot_date <= ?
          AND label_3x_12m IS NOT NULL
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """,
        conn,
        params=(AS_OF_CUTOFF,),
    )


def reduce_universe(df: pd.DataFrame, keep_per_month: int = 500) -> pd.DataFrame:
    df = df[
        (pd.to_numeric(df["close_price"], errors="coerce").fillna(0) > 0)
        & (pd.to_numeric(df["avg_turnover_20d_억"], errors="coerce").fillna(0) >= 20)
        & (pd.to_numeric(df["market_cap_억"], errors="coerce").fillna(0).between(100, 120_000))
        & (pd.to_numeric(df["ret_60d"], errors="coerce").fillna(0) <= 2.0)
        & (pd.to_numeric(df["pbr"], errors="coerce").fillna(0).between(0.000001, 35))
    ].copy()
    df["base_score"] = pd.to_numeric(df["model_score_12m"], errors="coerce").fillna(0)
    chunks = []
    for _, g in df.groupby("snapshot_date", sort=True):
        chunks.append(g.sort_values("base_score", ascending=False).head(keep_per_month))
    return pd.concat(chunks, ignore_index=True) if chunks else df.head(0)


def load_prices(conn: sqlite3.Connection, codes: list[str]) -> pd.DataFrame:
    chunks = []
    for i in range(0, len(codes), 800):
        part = codes[i:i + 800]
        ph = ",".join("?" for _ in part)
        chunks.append(pd.read_sql_query(
            f"""
            SELECT stock_code, substr(date,1,10) AS dt, open, close
            FROM price_history
            WHERE stock_code IN ({ph})
              AND date >= '2020-01-01'
              AND open > 0
              AND close > 0
            ORDER BY stock_code, dt
            """,
            conn,
            params=part,
        ))
    return pd.concat(chunks, ignore_index=True) if chunks else pd.DataFrame()


def next_open_lookup(prices: pd.DataFrame) -> dict[str, tuple[list[str], list[float]]]:
    out = {}
    for code, g in prices.groupby("stock_code"):
        g = g.sort_values("dt")
        out[str(code)] = (g["dt"].astype(str).tolist(), g["open"].astype(float).tolist())
    return out


def next_open(
    by_code: dict[str, tuple[list[str], list[float]]],
    code: str,
    after_date: str,
    before_or_equal: str | None = None,
) -> tuple[str | None, float | None]:
    dates, opens = by_code.get(str(code), ([], []))
    idx = bisect.bisect_right(dates, after_date)
    if idx >= len(dates):
        return None, None
    if before_or_equal and dates[idx] > before_or_equal:
        return None, None
    return dates[idx], opens[idx]


def load_order_flags(conn: sqlite3.Connection) -> dict[str, list[TimedValue]]:
    rows = conn.execute(
        """
        SELECT stock_code, rcept_dt, COALESCE(revenue_ratio_pct,0) AS ratio
        FROM order_contracts
        WHERE is_termination=0
          AND rcept_dt IS NOT NULL
          AND length(stock_code)=6
        ORDER BY stock_code, rcept_dt
        """
    ).fetchall()
    out: dict[str, list[TimedValue]] = defaultdict(list)
    for r in rows:
        d = norm_date(r["rcept_dt"])
        if not d:
            continue
        out[r["stock_code"]].append(TimedValue(d, float(r["ratio"] or 0)))
    return dict(out)


def load_dilution_flags(conn: sqlite3.Connection) -> dict[str, list[TimedValue]]:
    rows = conn.execute(
        """
        SELECT stock_code, disclosed_at, event_type,
               COALESCE(dilution_pct,0) AS dilution_pct,
               COALESCE(issue_amount,0) AS issue_amount
        FROM dilution_events
        WHERE disclosed_at IS NOT NULL
          AND length(stock_code)=6
          AND COALESCE(risk_amount_status, 'amount_confirmed') = 'amount_confirmed'
        ORDER BY stock_code, disclosed_at
        """
    ).fetchall()
    out: dict[str, list[TimedValue]] = defaultdict(list)
    risk_words = ("유상", "전환", "신주인수권", "CB", "BW", "EB")
    for r in rows:
        d = norm_date(r["disclosed_at"])
        if not d:
            continue
        event_type = str(r["event_type"] or "") + " " + str(r["issue_amount"] or "")
        risk = 1.0 if any(w in event_type for w in risk_words) and (
            float(r["dilution_pct"] or 0) >= 8 or float(r["issue_amount"] or 0) >= 5_000_000_000
        ) else 0.0
        if risk:
            out[r["stock_code"]].append(TimedValue(d, risk))
    return dict(out)


def load_backlog_flags(conn: sqlite3.Connection) -> dict[str, list[TimedValue]]:
    if not table_exists(conn, "dart_backlog_quarterly"):
        return {}
    rows = conn.execute(
        """
        SELECT stock_code, fiscal_year, fiscal_quarter,
               backlog_amount_krw, backlog_confidence, source_rcept_dt
        FROM dart_backlog_quarterly
        WHERE backlog_amount_krw IS NOT NULL
          AND length(stock_code)=6
        ORDER BY stock_code, fiscal_year, fiscal_quarter
        """
    ).fetchall()
    by_code: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        by_code[r["stock_code"]].append(r)
    out: dict[str, list[TimedValue]] = defaultdict(list)
    for code, vals in by_code.items():
        prev = None
        prev_conf = 0.0
        for r in vals:
            avail = norm_date(r["source_rcept_dt"]) or quarter_available(r["fiscal_year"], r["fiscal_quarter"])
            cur = float(r["backlog_amount_krw"] or 0)
            conf = float(r["backlog_confidence"] or 0)
            comparable = bool(
                prev and prev > 0 and cur > 0
                and max(cur, prev) / min(cur, prev) <= 20.0
            )
            if comparable and conf >= 0.95 and prev_conf >= 0.95:
                growth = cur / prev - 1.0
                if growth >= 0.30:
                    out[code].append(TimedValue(avail, min(growth, 3.0)))
            prev = cur
            prev_conf = conf
    return dict(out)


def load_program_flow(conn: sqlite3.Connection) -> dict[str, list[TimedValue]]:
    rows = conn.execute(
        """
        SELECT stock_code, dt, SUM(COALESCE(net_buy_amt_krw,0)) AS net_amt
        FROM broker_program_stock_daily
        WHERE dt BETWEEN '2020-01-01' AND ?
          AND length(stock_code)=6
        GROUP BY stock_code, dt
        ORDER BY stock_code, dt
        """,
        (AS_OF_CUTOFF,),
    ).fetchall()
    out: dict[str, list[TimedValue]] = defaultdict(list)
    for r in rows:
        d = norm_date(r["dt"])
        if d:
            out[r["stock_code"]].append(TimedValue(d, float(r["net_amt"] or 0) / 100_000_000))
    return dict(out)


def load_sector_rs(conn: sqlite3.Connection) -> dict[str, list[TimedValue]]:
    if not table_exists(conn, "stockeasy_sector_rs_daily"):
        return {}
    rows = conn.execute(
        """
        SELECT dt, sector_name, rs_score
        FROM stockeasy_sector_rs_daily
        WHERE dt BETWEEN '2020-01-01' AND ?
        ORDER BY sector_name, dt
        """,
        (AS_OF_CUTOFF,),
    ).fetchall()
    out: dict[str, list[TimedValue]] = defaultdict(list)
    for r in rows:
        d = norm_date(r["dt"])
        if d:
            out[r["sector_name"]].append(TimedValue(d, float(r["rs_score"] or 0)))
    return dict(out)


def recent_max(points: dict[str, list[TimedValue]], key: str, as_of: str, lookback_days: int) -> float:
    vals = points.get(str(key)) or []
    dates = [p.as_of for p in vals]
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < 0:
        return 0.0
    cutoff = date.fromisoformat(as_of) - timedelta(days=lookback_days)
    best = 0.0
    while idx >= 0 and date.fromisoformat(vals[idx].as_of) >= cutoff:
        best = max(best, vals[idx].value)
        idx -= 1
    return best


def rolling_sum(points: dict[str, list[TimedValue]], key: str, as_of: str, lookback_days: int) -> float:
    vals = points.get(str(key)) or []
    dates = [p.as_of for p in vals]
    idx = bisect.bisect_right(dates, as_of) - 1
    if idx < 0:
        return 0.0
    cutoff = date.fromisoformat(as_of) - timedelta(days=lookback_days)
    total = 0.0
    while idx >= 0 and date.fromisoformat(vals[idx].as_of) >= cutoff:
        total += vals[idx].value
        idx -= 1
    return total


def asof_value(points: dict[str, list[TimedValue]], key: str, as_of: str) -> float:
    vals = points.get(str(key)) or []
    dates = [p.as_of for p in vals]
    idx = bisect.bisect_right(dates, as_of) - 1
    return vals[idx].value if idx >= 0 else 0.0


def add_overlay_features(conn: sqlite3.Connection, df: pd.DataFrame) -> pd.DataFrame:
    orders = load_order_flags(conn)
    dilution = load_dilution_flags(conn)
    backlog = load_backlog_flags(conn)
    program = load_program_flow(conn)
    sector_rs = load_sector_rs(conn)

    rows = []
    for r in df.to_dict("records"):
        as_of = str(r["snapshot_date"])[:10]
        code = str(r["stock_code"])
        sector = str(r.get("sector_large") or "")
        order_ratio = recent_max(orders, code, as_of, 180)
        backlog_growth = recent_max(backlog, code, as_of, 240)
        program20 = rolling_sum(program, code, as_of, 30)
        program60 = rolling_sum(program, code, as_of, 90)
        recent_dilution = recent_max(dilution, code, as_of, 365)
        rs_score = asof_value(sector_rs, sector, as_of)
        supply20 = float(r.get("supply_20d_억") or 0)
        rows.append({
            **r,
            "order_ratio_recent": order_ratio,
            "order_big": int(order_ratio >= 10),
            "backlog_growth_recent": backlog_growth,
            "backlog_good": int(backlog_growth >= 0.30),
            "program20_억": program20,
            "program60_억": program60,
            "program_good": int(program20 >= 5 and program60 >= 0),
            "supply_good": int(supply20 >= 5),
            "sector_rs_score": rs_score,
            "sector_rs_good": int(rs_score >= 60),
            "recent_dilution_risk": int(recent_dilution > 0),
            "price_risk": int(float(r.get("ret_60d") or 0) < -0.25 or float(r.get("dist_high_252") or 0) < -0.70),
        })
    return pd.DataFrame(rows)


def kospi_regime(conn: sqlite3.Connection) -> dict[str, bool]:
    df = pd.read_sql_query(
        """
        SELECT substr(date,1,10) AS dt, close
        FROM price_history
        WHERE stock_code='^KS11'
          AND date BETWEEN '2019-01-01' AND ?
          AND close>0
        ORDER BY dt
        """,
        conn,
        params=(AS_OF_CUTOFF,),
    )
    if df.empty:
        return {}
    df["dt"] = pd.to_datetime(df["dt"])
    df["ym"] = df["dt"].dt.to_period("M").astype(str)
    month = df.loc[df.groupby("ym")["dt"].idxmax()].sort_values("dt").copy()
    month["ma6"] = month["close"].rolling(6, min_periods=4).mean()
    month["ret3"] = month["close"] / month["close"].shift(3) - 1
    month["ok"] = (month["close"] >= month["ma6"]) | (month["ret3"] > 0)
    return {str(k): bool(v) for k, v in month.set_index("ym")["ok"].shift(1).dropna().items()}


def add_rank_score(g: pd.DataFrame, weights: dict[str, float]) -> pd.DataFrame:
    out = g.copy()
    out["rank_score"] = (
        out["base_score"].astype(float)
        + weights["order"] * out["order_big"].astype(float)
        + weights["backlog"] * out["backlog_good"].astype(float)
        + weights["program"] * out["program_good"].astype(float)
        + weights["supply"] * out["supply_good"].astype(float)
        + weights["sector_rs"] * out["sector_rs_good"].astype(float)
        - weights["dilution"] * out["recent_dilution_risk"].astype(float)
        - weights["price_risk"] * out["price_risk"].astype(float)
    )
    return out


def apply_predicate(g: pd.DataFrame, name: str) -> pd.DataFrame:
    if name == "none":
        return g
    catalyst_or_flow = (
        (g["order_big"].astype(int) > 0)
        | (g["backlog_good"].astype(int) > 0)
        | (g["program_good"].astype(int) > 0)
        | (g["supply_good"].astype(int) > 0)
    )
    no_dilution = g["recent_dilution_risk"].astype(int) == 0
    if name == "no_dilution":
        return g[no_dilution]
    if name == "catalyst_or_flow":
        return g[catalyst_or_flow]
    if name == "no_dilution_catalyst_or_flow":
        return g[no_dilution & catalyst_or_flow]
    raise ValueError(name)


def run_monthly(
    month_groups: dict[str, pd.DataFrame],
    by_code: dict[str, tuple[list[str], list[float]]],
    months: list[str],
    top_n: int,
    weights: dict[str, float],
    pred_name: str,
    regime: dict[str, bool],
    use_regime: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly = []
    picks = []
    for idx, month in enumerate(months[:-1]):
        next_month = months[idx + 1]
        ym = month[:7]
        if use_regime and not regime.get(ym, True):
            monthly.append({"month": month, "n": 0, "ret_net": 0.0, "ret_gross": 0.0, "skip": "regime_off"})
            continue
        g = month_groups.get(month)
        if g is None or g.empty:
            monthly.append({"month": month, "n": 0, "ret_net": 0.0, "ret_gross": 0.0, "skip": "empty"})
            continue
        g = apply_predicate(g, pred_name)
        if g.empty:
            monthly.append({"month": month, "n": 0, "ret_net": 0.0, "ret_gross": 0.0, "skip": "empty"})
            continue
        g = add_rank_score(g, weights)
        selected = g.sort_values("rank_score", ascending=False).head(top_n)
        rets = []
        for _, r in selected.iterrows():
            entry_limit = (pd.Timestamp(str(month)[:10]) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
            entry_date, entry = next_open(
                by_code,
                str(r["stock_code"]),
                str(month)[:10],
                before_or_equal=entry_limit,
            )
            exit_limit = (pd.Timestamp(str(next_month)[:10]) + pd.Timedelta(days=10)).strftime("%Y-%m-%d")
            exit_date, exitp = next_open(
                by_code,
                str(r["stock_code"]),
                str(next_month)[:10],
                before_or_equal=exit_limit,
            )
            if not entry or not exitp:
                continue
            ret = exitp / entry - 1.0
            rets.append(ret)
            picks.append({
                "month": month,
                "stock_code": r["stock_code"],
                "stock_name": r.get("stock_name"),
                "sector": r.get("sector_large"),
                "rank_score": float(r["rank_score"]),
                "entry_date": entry_date,
                "exit_date": exit_date,
                "ret": ret,
                "order_ratio_recent": r.get("order_ratio_recent"),
                "backlog_growth_recent": r.get("backlog_growth_recent"),
                "program20_억": r.get("program20_억"),
                "supply20_억": r.get("supply_20d_억"),
                "sector_rs_score": r.get("sector_rs_score"),
                "recent_dilution_risk": r.get("recent_dilution_risk"),
            })
        gross = sum(rets) / len(rets) if rets else 0.0
        monthly.append({
            "month": month,
            "n": len(rets),
            "ret_gross": gross,
            "ret_net": gross - (TCOST if rets else 0.0),
            "skip": "",
        })
    return pd.DataFrame(monthly), pd.DataFrame(picks)


def summarize_monthly(name: str, monthly: pd.DataFrame) -> dict:
    if monthly.empty:
        return {"name": name, "months": 0}
    r = monthly["ret_net"].astype(float)
    equity = (1 + r).cumprod()
    dd = equity / equity.cummax() - 1
    years = max(len(r) / 12, 1e-9)
    return {
        "name": name,
        "months": int(len(r)),
        "invested_months": int((monthly["n"] > 0).sum()),
        "avg_positions": round(float(monthly.loc[monthly["n"] > 0, "n"].mean() or 0), 2),
        "total_return_pct": round((float(equity.iloc[-1]) - 1) * 100, 2),
        "cagr_pct": round((float(equity.iloc[-1]) ** (1 / years) - 1) * 100, 2),
        "avg_monthly_pct": round(float(r.mean()) * 100, 2),
        "median_monthly_pct": round(float(r.median()) * 100, 2),
        "win_month_pct": round(float((r > 0).mean()) * 100, 2),
        "max_drawdown_pct": round(float(dd.min()) * 100, 2),
        "worst_month_pct": round(float(r.min()) * 100, 2),
        "return_to_mdd": round(((float(equity.iloc[-1]) - 1) * 100) / abs(float(dd.min()) * 100), 2) if dd.min() < 0 else None,
    }


def objective(s: dict, base: dict) -> float:
    return (
        (s.get("total_return_pct", -999) - base.get("total_return_pct", 0)) * 0.25
        + (s.get("cagr_pct", -999) - base.get("cagr_pct", 0)) * 2.0
        + (s.get("max_drawdown_pct", -100) - base.get("max_drawdown_pct", -100)) * 1.8
        + (s.get("win_month_pct", 0) - base.get("win_month_pct", 0)) * 0.5
    )


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB, timeout=60)
    conn.row_factory = sqlite3.Row

    raw = load_snapshots(conn)
    reduced = reduce_universe(raw)
    enriched = add_overlay_features(conn, reduced)
    month_groups = {str(m): g.copy() for m, g in enriched.groupby("snapshot_date", sort=True)}
    prices = load_prices(conn, sorted(enriched["stock_code"].astype(str).unique().tolist()))
    by_code = next_open_lookup(prices)
    regime = kospi_regime(conn)
    conn.close()

    months = sorted(enriched["snapshot_date"].astype(str).unique().tolist())
    periods = {
        "full_2020_2026": ("2020-01-31", "2026-06-30"),
        "train_2020_2024h1": ("2020-01-31", TRAIN_END),
        "test_2024h2_2026": ("2024-07-31", "2026-06-30"),
    }
    grid = {
        "order": [0.0, 0.04, 0.08],
        "backlog": [0.0, 0.04, 0.08],
        "program": [0.0, 0.03, 0.06],
        "supply": [0.0, 0.03],
        "sector_rs": [0.0, 0.02, 0.04],
        "dilution": [0.0, 0.06, 0.12],
        "price_risk": [0.0, 0.04],
    }
    pred_names = ["none", "no_dilution", "catalyst_or_flow", "no_dilution_catalyst_or_flow"]

    all_monthly = []
    all_picks = []
    summaries = []

    base_weights = {k: 0.0 for k in grid}
    for top_n in (8, 12, 20):
        for period_name, (start, end) in periods.items():
            period_months = [m for m in months if start <= m <= end]
            monthly, picks = run_monthly(month_groups, by_code, period_months, top_n, base_weights, "none", regime, False)
            s = summarize_monthly(f"{period_name}_baseline_top{top_n}", monthly)
            s.update({"period": period_name, "top_n": top_n, "predicate": "none", "use_regime": False, **{f"w_{k}": 0.0 for k in grid}})
            summaries.append(s)
            monthly["run_name"] = s["name"]
            picks["run_name"] = s["name"]
            all_monthly.append(monthly)
            all_picks.append(picks)

    baseline_by_key = {(s["period"], s["top_n"]): s for s in summaries if "baseline" in s["name"]}

    weight_keys = list(grid)
    tested = 0
    candidate_weights = [
        {"order": 0.08, "backlog": 0.00, "program": 0.00, "supply": 0.00, "sector_rs": 0.00, "dilution": 0.00, "price_risk": 0.00},
        {"order": 0.00, "backlog": 0.08, "program": 0.00, "supply": 0.00, "sector_rs": 0.00, "dilution": 0.00, "price_risk": 0.00},
        {"order": 0.08, "backlog": 0.08, "program": 0.00, "supply": 0.00, "sector_rs": 0.00, "dilution": 0.00, "price_risk": 0.00},
        {"order": 0.00, "backlog": 0.00, "program": 0.06, "supply": 0.03, "sector_rs": 0.00, "dilution": 0.00, "price_risk": 0.00},
        {"order": 0.04, "backlog": 0.04, "program": 0.06, "supply": 0.03, "sector_rs": 0.00, "dilution": 0.00, "price_risk": 0.00},
        {"order": 0.04, "backlog": 0.04, "program": 0.06, "supply": 0.03, "sector_rs": 0.04, "dilution": 0.00, "price_risk": 0.00},
        {"order": 0.04, "backlog": 0.04, "program": 0.06, "supply": 0.03, "sector_rs": 0.04, "dilution": 0.12, "price_risk": 0.00},
        {"order": 0.04, "backlog": 0.04, "program": 0.06, "supply": 0.03, "sector_rs": 0.04, "dilution": 0.12, "price_risk": 0.04},
        {"order": 0.08, "backlog": 0.08, "program": 0.06, "supply": 0.03, "sector_rs": 0.04, "dilution": 0.12, "price_risk": 0.04},
        {"order": 0.08, "backlog": 0.04, "program": 0.03, "supply": 0.03, "sector_rs": 0.04, "dilution": 0.12, "price_risk": 0.04},
        {"order": 0.04, "backlog": 0.08, "program": 0.03, "supply": 0.03, "sector_rs": 0.04, "dilution": 0.12, "price_risk": 0.04},
        {"order": 0.08, "backlog": 0.08, "program": 0.03, "supply": 0.00, "sector_rs": 0.04, "dilution": 0.12, "price_risk": 0.04},
    ]
    for weights in candidate_weights:
        for pred_name in pred_names:
            for use_regime in (False, True):
                for top_n in (8, 12, 20):
                    for period_name, (start, end) in periods.items():
                        period_months = [m for m in months if start <= m <= end]
                        monthly, picks = run_monthly(month_groups, by_code, period_months, top_n, weights, pred_name, regime, use_regime)
                        s = summarize_monthly(f"{period_name}_{pred_name}_top{top_n}_{'regime' if use_regime else 'plain'}", monthly)
                        base = baseline_by_key[(period_name, top_n)]
                        s.update({
                            "period": period_name,
                            "top_n": top_n,
                            "predicate": pred_name,
                            "use_regime": use_regime,
                            "objective_vs_base": round(objective(s, base), 3),
                            **{f"w_{k}": weights[k] for k in weight_keys},
                        })
                        summaries.append(s)
                        if period_name == "full_2020_2026" and s["objective_vs_base"] > 20:
                            monthly["run_name"] = s["name"]
                            picks["run_name"] = s["name"]
                            all_monthly.append(monthly)
                            all_picks.append(picks)
                        tested += 1

    summary_df = pd.DataFrame(summaries)
    train_rank = summary_df[summary_df["period"] == "train_2020_2024h1"].sort_values("objective_vs_base", ascending=False)
    key_cols = ["top_n", "predicate", "use_regime", *[f"w_{k}" for k in weight_keys]]
    robust_keys = train_rank.head(50)[key_cols].drop_duplicates()
    robust = summary_df.merge(robust_keys, on=key_cols, how="inner")
    test_robust = robust[robust["period"] == "test_2024h2_2026"].sort_values(["objective_vs_base", "return_to_mdd"], ascending=False)
    full_robust = robust[robust["period"] == "full_2020_2026"].sort_values(["objective_vs_base", "return_to_mdd"], ascending=False)

    out_json = OUT_DIR / "strategy_overlay_expansion_20260728.json"
    out_csv = OUT_DIR / "strategy_overlay_expansion_summary_20260728.csv"
    out_md = OUT_DIR / "strategy_overlay_expansion_20260728.md"
    out_monthly = OUT_DIR / "strategy_overlay_expansion_monthly_20260728.csv"
    out_picks = OUT_DIR / "strategy_overlay_expansion_picks_20260728.csv"

    summary_df.to_csv(out_csv, index=False)
    if all_monthly:
        pd.concat(all_monthly, ignore_index=True).to_csv(out_monthly, index=False)
    if all_picks:
        pd.concat(all_picks, ignore_index=True).to_csv(out_picks, index=False)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "assumptions": {
            "signal": "strategy_feature_snapshot monthly row",
            "execution": "next trading day open to next monthly signal next trading day open",
            "cost": TCOST,
            "universe": "top 500 base-score names per month after liquidity and basic sanity filters",
            "selection_warning": "only test_2024h2_2026 rows should be used as out-of-sample evidence for train-selected overlays",
        },
        "data": {
            "raw_snapshots": int(len(raw)),
            "reduced_snapshots": int(len(enriched)),
            "months": int(len(months)),
            "tested_config_period_rows": int(tested),
        },
        "baseline": [s for s in summaries if "baseline" in s["name"]],
        "best_train_selected_test": test_robust.head(20).to_dict("records"),
        "best_train_selected_full": full_robust.head(20).to_dict("records"),
    }
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Strategy Overlay Expansion Research — 2026-07-28",
        "",
        f"- generated_at: {payload['generated_at']}",
        f"- raw_snapshots: {len(raw):,}",
        f"- reduced_snapshots: {len(enriched):,}",
        f"- tested rows: {tested:,}",
        "- execution: monthly snapshot -> next trading day open, exit next monthly open",
        "- cost: 0.4% per invested month",
        "",
        "## Baseline",
        "",
        "| period | top_n | total % | CAGR % | MDD % | win % |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for s in payload["baseline"]:
        lines.append(f"| {s['period']} | {s['top_n']} | {s['total_return_pct']} | {s['cagr_pct']} | {s['max_drawdown_pct']} | {s['win_month_pct']} |")
    lines.extend([
        "",
        "## Train-Selected Candidates: Test Period",
        "",
        "| rank | top_n | predicate | regime | total % | CAGR % | MDD % | win % | obj | weights |",
        "|---:|---:|---|---|---:|---:|---:|---:|---:|---|",
    ])
    for i, r in enumerate(payload["best_train_selected_test"][:12], 1):
        weights = ", ".join(f"{k[2:]}={r[k]}" for k in sorted(r) if k.startswith("w_") and float(r[k]) != 0)
        lines.append(
            f"| {i} | {r['top_n']} | {r['predicate']} | {r['use_regime']} | "
            f"{r['total_return_pct']} | {r['cagr_pct']} | {r['max_drawdown_pct']} | {r['win_month_pct']} | "
            f"{r['objective_vs_base']} | {weights or 'base'} |"
        )
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- Use rows above as research candidates only. They were selected by train-period objective, then checked in 2024H2-2026.",
        "- If a candidate improves only full-period but not test-period, treat it as overfit.",
        "- Before Strategy Center promotion, Claude should verify feature availability dates and rerun in the shared strict simulator.",
        "",
        "## Output Files",
        "",
        f"- `{out_json}`",
        f"- `{out_csv}`",
        f"- `{out_monthly}`",
        f"- `{out_picks}`",
    ])
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({
        "json": str(out_json),
        "md": str(out_md),
        "summary_csv": str(out_csv),
        "best_test": payload["best_train_selected_test"][:5],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
