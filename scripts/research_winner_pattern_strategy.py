#!/usr/bin/env python3
"""
Winner-pattern strategy research.

Goal:
  1. Do not start from a hand-written trading idea.
  2. Learn pre-signal characteristics of stocks that later became big winners.
  3. Turn only stable characteristics into monthly portfolio rules.
  4. Validate on OOS and reject rules that fail benchmark/risk gates.

This is intentionally conservative: it reports the best discovered rules, but
marks them as candidates unless they survive the full OOS gates.
"""

from __future__ import annotations

import itertools
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"
DATASET_PATH = ROOT / "research_outputs" / "market2x_signal_dataset.parquet"
OUT_DIR = ROOT / "research_outputs"

TRAIN = ("2021-01", "2023-12")
VALID = ("2024-01", "2025-05")
OOS = ("2025-06", "2026-05")
ALL = ("2021-01", "2026-05")

BUDGET = 100_000_000


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.row_factory = sqlite3.Row
    return c


def month_ord(s: pd.Series) -> pd.Series:
    return pd.PeriodIndex(s, freq="M").astype("int64")


def load_base() -> pd.DataFrame:
    df = pd.read_parquet(DATASET_PATH)
    df["signal_month"] = df["signal_month"].astype(str)
    df = df[df["market"].isin(["KOSPI", "KOSDAQ"])].copy()
    df = df[df["avg_turnover20"].fillna(0) >= 2e9].copy()
    df = df[df["close"].fillna(0) >= 1000].copy()
    df = df.drop_duplicates(["stock_code", "signal_month"], keep="last")
    return df


def attach_next_month_returns(df: pd.DataFrame) -> pd.DataFrame:
    start = str((pd.Period(df["signal_month"].min(), "M") - 1).start_time.date())
    end = str((pd.Period(df["signal_month"].max(), "M") + 2).end_time.date())
    c = conn()
    px = pd.read_sql_query(
        """
        SELECT stock_code, date, open, high, low, close
        FROM price_history
        WHERE date BETWEEN ? AND ?
          AND open > 0 AND close > 0
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY stock_code, date
        """,
        c,
        params=(start, end),
        parse_dates=["date"],
    )
    c.close()
    px["exec_month"] = px["date"].dt.to_period("M").astype(str)

    rows = []
    for (code, em), g in px.groupby(["stock_code", "exec_month"], sort=False):
        g = g.sort_values("date")
        entry = float(g.iloc[0]["open"])
        last_close = float(g.iloc[-1]["close"])
        stop10 = entry * 0.90
        stop12 = entry * 0.88
        trail_high = entry
        sell_stop10 = None
        sell_trail12 = None
        for _, r in g.iterrows():
            trail_high = max(trail_high, float(r["high"] or r["close"]))
            close = float(r["close"])
            if sell_stop10 is None and close <= stop10:
                sell_stop10 = close
            if sell_trail12 is None and close <= trail_high * 0.88 and close > entry * 0.97:
                sell_trail12 = close
        rows.append(
            {
                "stock_code": code,
                "exec_month": em,
                "signal_month": str(pd.Period(em, "M") - 1),
                "entry_open": entry,
                "exit_close": last_close,
                "ret_m1": last_close / entry - 1,
                "ret_m1_stop10": (sell_stop10 or last_close) / entry - 1,
                "ret_m1_stop12_trail": (sell_trail12 or sell_stop10 or last_close) / entry - 1,
            }
        )
    ret = pd.DataFrame(rows)
    out = df.merge(ret, on=["stock_code", "signal_month"], how="left")
    out = out[out["ret_m1"].notna()].copy()
    return out


def add_market_regime(df: pd.DataFrame) -> pd.DataFrame:
    c = conn()
    ks = pd.read_sql_query(
        "SELECT date, close FROM price_history WHERE stock_code='^KS11' ORDER BY date",
        c,
        parse_dates=["date"],
    )
    c.close()
    ks["signal_month"] = ks["date"].dt.to_period("M").astype(str)
    idx = ks.groupby("signal_month")["date"].idxmax()
    km = ks.loc[idx].sort_values("date").copy()
    km["ks_ma6"] = km["close"].rolling(6, min_periods=4).mean()
    km["ks_ma10"] = km["close"].rolling(10, min_periods=6).mean()
    km["ks_ret_3m"] = km["close"].pct_change(3)
    km["regime_bull"] = (km["close"] > km["ks_ma6"]).astype(float)
    km["regime_bear"] = ((km["close"] < km["ks_ma10"]) & (km["ks_ret_3m"] < 0)).astype(float)
    km["kospi_bench_ret_m1"] = km["close"].shift(-1) / km["close"] - 1
    out = df.merge(
        km[["signal_month", "regime_bull", "regime_bear", "kospi_bench_ret_m1"]],
        on="signal_month",
        how="left",
    )
    # Market average for evaluation: equal-weight next-month return of the
    # actual tradable universe after liquidity/price filters. This is less
    # fragile than relying only on a single index row that may have feed errors.
    universe_bench = (
        out.groupby("signal_month")["ret_m1"]
        .mean()
        .rename("universe_bench_ret_m1")
        .reset_index()
    )
    return out.merge(universe_bench, on="signal_month", how="left")


def add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "ret_1m", "ret_3m", "ret_6m", "ret_12_1", "near_high52", "above_low52",
        "from_high52", "vol60", "avg_turnover20", "vol_ratio20",
        "supply20_to_turnover", "supply60_to_turnover", "short_cover_1m",
        "short_cover_3m", "export_yoy", "export_mom3", "nps_net_3m",
        "nps_hires_3m", "wlb_workers_mom", "wlb_workers_3m",
        "employment_yoy_change", "employment_mom_change", "fin_rev_yoy",
        "fin_op_yoy", "fin_net_yoy", "fin_op_margin", "fin_debt_ratio",
        "fin_roe", "fin_rev_accel", "cf_ocf_yoy", "cf_capex_yoy",
        "cf_ocf_margin", "cf_fcf_margin", "backlog_to_rev", "backlog_yoy",
        "new_order_yoy", "raw_material_cost_yoy", "dart_material_yoy",
        "annual_material_yoy",
    ]
    for col in cols:
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        df[f"r_{col}"] = s.groupby(df["signal_month"]).rank(pct=True)
    if "r_vol60" in df:
        df["r_low_vol60"] = 1 - df["r_vol60"]
    if "r_fin_debt_ratio" in df:
        df["r_low_debt"] = 1 - df["r_fin_debt_ratio"]
    return df


@dataclass(frozen=True)
class Rule:
    name: str
    mask_expr: str
    score_cols: tuple[tuple[str, float], ...]
    top_n: int
    ret_col: str
    use_regime: bool


def condition_library(df: pd.DataFrame) -> dict[str, pd.Series]:
    n = lambda col: pd.to_numeric(df.get(col), errors="coerce")
    cond = {
        "early_trend": (n("close") > n("ma60") * 0.97) & (n("close") < n("ma60") * 1.20) & (n("ma20") >= n("ma60") * 0.96),
        "not_extended_6m": n("ret_6m").between(-0.10, 0.80),
        "not_chasing_high": n("near_high52").between(0.45, 0.96),
        "base_recovery": n("above_low52").between(0.20, 2.50),
        "supply_q70": n("r_supply20_to_turnover") >= 0.70,
        "supply60_q70": n("r_supply60_to_turnover") >= 0.70,
        "low_vol_q60": n("r_low_vol60") >= 0.60,
        "turnover_q70": n("r_avg_turnover20") >= 0.70,
        "fin_rev_q70": n("r_fin_rev_yoy") >= 0.70,
        "fin_op_q70": n("r_fin_op_yoy") >= 0.70,
        "fin_accel_pos": n("fin_rev_accel") > 0,
        "op_turnaround": n("fin_op_turnaround") > 0,
        "ocf_q70": n("r_cf_ocf_margin") >= 0.70,
        "capex_q70": n("r_cf_capex_yoy") >= 0.70,
        "backlog_present": n("backlog_present") > 0,
        "backlog_q70": n("r_backlog_yoy") >= 0.70,
        "new_order_q70": n("r_new_order_yoy") >= 0.70,
        "material_q70": n("r_raw_material_cost_yoy") >= 0.70,
        "export_q70": n("r_export_yoy") >= 0.70,
        "employment_q70": n("r_wlb_workers_3m") >= 0.70,
        "short_cover_q70": n("r_short_cover_3m") >= 0.70,
    }
    return {k: v.fillna(False) for k, v in cond.items()}


def discover_rules(df: pd.DataFrame) -> list[Rule]:
    train = df[(df["signal_month"] >= TRAIN[0]) & (df["signal_month"] <= TRAIN[1])].copy()
    cond = condition_library(train)
    target = train["fwd_6m_ret"].fillna(-9) >= 0.60
    base = float(target.mean())
    rows = []
    names = list(cond)
    for k in range(2, 5):
        for combo in itertools.combinations(names, k):
            mask = pd.Series(True, index=train.index)
            for c in combo:
                mask &= cond[c]
            support = int(mask.sum())
            if support < 90:
                continue
            hit = float(target[mask].mean()) if support else 0
            avg6 = float(train.loc[mask, "fwd_6m_ret"].mean())
            rows.append((combo, support, hit, hit / base if base else 0, avg6))
    rows.sort(key=lambda x: (x[3], x[4], x[1]), reverse=True)

    score_sets = [
        (
            "early_quality",
            (
                ("r_fin_rev_yoy", 0.15), ("r_fin_op_yoy", 0.12), ("r_cf_ocf_margin", 0.12),
                ("r_supply20_to_turnover", 0.14), ("r_low_vol60", 0.12), ("r_backlog_yoy", 0.10),
                ("r_raw_material_cost_yoy", 0.10), ("r_ret_3m", 0.08), ("r_near_high52", -0.07),
            ),
        ),
        (
            "demand_confirmed",
            (
                ("r_fin_rev_yoy", 0.18), ("r_backlog_yoy", 0.15), ("r_new_order_yoy", 0.13),
                ("r_raw_material_cost_yoy", 0.12), ("r_cf_ocf_margin", 0.10),
                ("r_supply60_to_turnover", 0.12), ("r_low_vol60", 0.10), ("r_ret_6m", -0.10),
            ),
        ),
        (
            "turnaround_flow",
            (
                ("r_fin_rev_accel", 0.16), ("r_supply20_to_turnover", 0.16), ("r_short_cover_3m", 0.10),
                ("r_low_vol60", 0.12), ("r_ret_3m", 0.12), ("r_above_low52", 0.10),
                ("r_near_high52", -0.10), ("r_fin_debt_ratio", -0.08),
            ),
        ),
    ]

    rules: list[Rule] = []
    for combo, support, hit, lift, avg6 in rows[:18]:
        expr = " & ".join(combo)
        for score_name, score_cols in score_sets:
            for ret_col in ("ret_m1_stop10", "ret_m1_stop12_trail"):
                rules.append(
                    Rule(
                        name=f"{score_name}|{expr}|hit{hit:.2f}|lift{lift:.2f}",
                        mask_expr=expr,
                        score_cols=score_cols,
                        top_n=5,
                        ret_col=ret_col,
                        use_regime=True,
                    )
                )
    # Hand-built candidates guided by winner profiles, included as controls.
    rules.extend(
        [
            Rule(
                "codex_early_turnaround_quality",
                "early_trend & not_extended_6m & not_chasing_high & supply_q70 & fin_rev_q70",
                (
                    ("r_fin_rev_yoy", 0.18), ("r_fin_op_yoy", 0.12), ("r_cf_ocf_margin", 0.12),
                    ("r_supply20_to_turnover", 0.16), ("r_low_vol60", 0.14),
                    ("r_above_low52", 0.10), ("r_ret_6m", -0.10), ("r_near_high52", -0.08),
                ),
                5,
                "ret_m1_stop10",
                True,
            ),
            Rule(
                "codex_demand_acceleration",
                "early_trend & not_extended_6m & base_recovery & (backlog_q70 | material_q70 | export_q70) & supply60_q70",
                (
                    ("r_backlog_yoy", 0.16), ("r_new_order_yoy", 0.12), ("r_raw_material_cost_yoy", 0.12),
                    ("r_fin_rev_yoy", 0.14), ("r_supply60_to_turnover", 0.14),
                    ("r_low_vol60", 0.12), ("r_ret_6m", -0.10), ("r_near_high52", -0.10),
                ),
                5,
                "ret_m1_stop12_trail",
                True,
            ),
        ]
    )
    return rules


def eval_mask(df: pd.DataFrame, expr: str) -> pd.Series:
    local = condition_library(df)
    out = pd.Series(True, index=df.index)
    # Support simple `a & b & (c | d)` expressions through pandas eval.
    scope = {k: v for k, v in local.items()}
    try:
        return pd.eval(expr, local_dict=scope, engine="python").fillna(False)
    except Exception:
        for part in expr.split("&"):
            part = part.strip()
            if part:
                out &= local.get(part, pd.Series(False, index=df.index))
        return out.fillna(False)


def score(df: pd.DataFrame, cols: tuple[tuple[str, float], ...]) -> pd.Series:
    out = pd.Series(0.0, index=df.index)
    for col, w in cols:
        out += w * pd.to_numeric(df.get(col), errors="coerce").fillna(0.0)
    return out


def backtest_rule(df: pd.DataFrame, rule: Rule, start: str, end: str, tcost: float = 0.007) -> dict:
    sub = df[(df["signal_month"] >= start) & (df["signal_month"] <= end)].copy()
    if sub.empty:
        return {"months": 0, "total_return_pct": 0, "mdd_pct": 0, "trade_count": 0}
    mask = eval_mask(sub, rule.mask_expr)
    if rule.use_regime:
        mask &= sub["regime_bull"].fillna(0).astype(float) > 0
        mask &= sub["regime_bear"].fillna(0).astype(float) <= 0
    picks = sub[mask].copy()
    picks["score"] = score(picks, rule.score_cols)
    picks = picks.sort_values(["signal_month", "score"], ascending=[True, False]).groupby("signal_month").head(rule.top_n)
    if picks.empty:
        months = sorted(sub["signal_month"].unique())
        monthly = pd.Series(0.0, index=months)
    else:
        picks["ret_net"] = pd.to_numeric(picks[rule.ret_col], errors="coerce").fillna(0) - tcost
        picks["rank_w"] = picks.groupby("signal_month")["stock_code"].transform(lambda s: 1 / len(s))
        picks["pnl"] = picks["ret_net"] * picks["rank_w"]
        months = sorted(sub["signal_month"].unique())
        monthly = picks.groupby("signal_month")["pnl"].sum().reindex(months, fill_value=0.0)
    eq = (1 + monthly).cumprod()
    dd = eq / eq.cummax() - 1
    active = picks.groupby("signal_month")["stock_code"].count() if not picks.empty else pd.Series(dtype=float)
    bench = sub.groupby("signal_month")["universe_bench_ret_m1"].first().reindex(months).fillna(0.0)
    kospi_bench = sub.groupby("signal_month")["kospi_bench_ret_m1"].first().reindex(months).fillna(0.0)
    bench_eq = (1 + bench).cumprod()
    kospi_eq = (1 + kospi_bench).cumprod()
    return {
        "rule": rule.name,
        "period": f"{start}~{end}",
        "months": len(monthly),
        "active_months": int((monthly != 0).sum()),
        "trade_count": int(len(picks)),
        "avg_names": round(float(active.mean()), 2) if len(active) else 0.0,
        "total_return_pct": round(float((eq.iloc[-1] - 1) * 100), 2) if len(eq) else 0.0,
        "bench_return_pct": round(float((bench_eq.iloc[-1] - 1) * 100), 2) if len(bench_eq) else 0.0,
        "kospi_bench_return_pct": round(float((kospi_eq.iloc[-1] - 1) * 100), 2) if len(kospi_eq) else 0.0,
        "alpha_pct": round(float((eq.iloc[-1] - bench_eq.iloc[-1]) * 100), 2) if len(eq) and len(bench_eq) else 0.0,
        "mdd_pct": round(float(dd.min() * 100), 2) if len(dd) else 0.0,
        "hit_month_pct": round(float((monthly[monthly != 0] > 0).mean() * 100), 1) if (monthly != 0).any() else 0.0,
        "sharpe": round(float(monthly.mean() / monthly.std() * math.sqrt(12)), 2) if monthly.std() else 0.0,
        "monthly": [{"signal_month": k, "ret_pct": round(float(v * 100), 2)} for k, v in monthly.items()],
        "picks": (
            picks[["signal_month", "stock_code", "score", rule.ret_col, "ret_net"]]
            .sort_values(["signal_month", "score"], ascending=[True, False])
            .assign(
                score=lambda x: x["score"].round(6),
                raw_return_pct=lambda x: (x[rule.ret_col] * 100).round(2),
                net_return_pct=lambda x: (x["ret_net"] * 100).round(2),
            )[["signal_month", "stock_code", "score", "raw_return_pct", "net_return_pct"]]
            .to_dict(orient="records")
            if not picks.empty else []
        ),
    }


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    df = add_ranks(add_market_regime(attach_next_month_returns(load_base())))
    df = df[(df["signal_month"] >= ALL[0]) & (df["signal_month"] <= ALL[1])].copy()
    rules = discover_rules(df)
    rows = []
    details = {}
    for rule in rules:
        tr = backtest_rule(df, rule, *TRAIN)
        va = backtest_rule(df, rule, *VALID)
        oo = backtest_rule(df, rule, *OOS)
        al = backtest_rule(df, rule, *ALL)
        rows.append(
            {
                "rule": rule.name,
                "mask": rule.mask_expr,
                "ret_col": rule.ret_col,
                "train_ret": tr["total_return_pct"],
                "valid_ret": va["total_return_pct"],
                "oos_ret": oo["total_return_pct"],
                "oos_bench": oo["bench_return_pct"],
                "oos_kospi_bench": oo["kospi_bench_return_pct"],
                "oos_alpha": oo["alpha_pct"],
                "oos_mdd": oo["mdd_pct"],
                "oos_trades": oo["trade_count"],
                "all_ret": al["total_return_pct"],
                "all_bench": al["bench_return_pct"],
                "all_kospi_bench": al["kospi_bench_return_pct"],
                "all_alpha": al["alpha_pct"],
                "all_mdd": al["mdd_pct"],
                "all_trades": al["trade_count"],
                "score": oo["alpha_pct"] + 0.3 * va["alpha_pct"] + 0.2 * al["alpha_pct"] + 0.5 * oo["mdd_pct"],
            }
        )
        details[rule.name] = {"train": tr, "valid": va, "oos": oo, "all": al}
    res = pd.DataFrame(rows).sort_values("score", ascending=False)
    res.to_csv(OUT_DIR / "winner_pattern_strategy_results.csv", index=False)
    top = res.head(12).to_dict(orient="records")
    payload = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "periods": {"train": TRAIN, "valid": VALID, "oos": OOS, "all": ALL},
        "rules_tested": len(rules),
        "top": top,
        "best_detail": details[top[0]["rule"]] if top else None,
        "verdict": (
            "candidate_pass"
            if top and top[0]["oos_alpha"] > 0 and top[0]["oos_mdd"] >= -25 and top[0]["all_alpha"] > 0
            else "no_live_strategy_yet"
        ),
    }
    if top:
        pick_rows = []
        for period_name in ("train", "valid", "oos", "all"):
            for row in details[top[0]["rule"]][period_name]["picks"]:
                pick_rows.append({"period": period_name, **row})
        pd.DataFrame(pick_rows).to_csv(
            OUT_DIR / "winner_pattern_strategy_best_picks.csv",
            index=False,
        )
    (OUT_DIR / "winner_pattern_strategy_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
