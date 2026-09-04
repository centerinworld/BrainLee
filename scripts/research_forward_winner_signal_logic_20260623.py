#!/usr/bin/env python3
"""
Forward winner-signal research.

This run intentionally ignores the live selection logic.  It starts from
observable pre-move features that appeared more often in later winners, turns
them into two explicit candidate discovery rules, and backtests monthly
portfolios without using future returns in the selection score.
"""

from __future__ import annotations

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

ALL = ("2021-01", "2026-05")
TRAIN = ("2021-01", "2023-12")
VALID = ("2024-01", "2025-05")
OOS = ("2025-06", "2026-05")
TCOST = 0.007


@dataclass(frozen=True)
class Logic:
    name: str
    description: str
    mask_expr: str
    score_cols: tuple[tuple[str, float], ...]
    top_n: int = 5
    ret_col: str = "ret_m1_stop10"


LOGICS = [
    Logic(
        name="improved_growth_export_momentum_top5",
        description=(
            "매출 상위권, 영업이익 개선, 수출 상위권, 단기 수급을 결합한 초기 추세 종목 선별"
        ),
        mask_expr=(
            "liquid & early_trend & not_extended & not_chasing_high_loose "
            "& fin_rev_q70 & fin_op_q60 & export_q60 & supply20_q60"
        ),
        score_cols=(
            ("r_fin_rev_yoy", 0.20),
            ("r_fin_op_yoy", 0.18),
            ("r_fin_op_margin", 0.12),
            ("r_cf_ocf_margin", 0.12),
            ("r_low_vol60", 0.14),
            ("r_supply20_to_turnover", 0.10),
            ("r_ret_3m", 0.06),
            ("r_near_high52", -0.12),
            ("r_ret_6m", -0.08),
        ),
        top_n=5,
        ret_col="ret_m1",
    ),
    Logic(
        name="improved_quality_value_momentum_top8",
        description=(
            "추세가 살아 있으면서 매출/이익/현금흐름/수급 품질이 모두 중상위권인 종목을 넓게 선별"
        ),
        mask_expr=(
            "liquid & trend_alive & not_extended & not_chasing_high "
            "& fin_rev_q60 & fin_op_q60 & ocf_q60 & supply60_q60"
        ),
        score_cols=(
            ("r_fin_rev_yoy", 0.16),
            ("r_fin_op_yoy", 0.14),
            ("r_cf_ocf_margin", 0.14),
            ("r_low_vol60", 0.14),
            ("r_supply60_to_turnover", 0.12),
            ("r_export_yoy", 0.10),
            ("r_above_low52", 0.08),
            ("r_near_high52", -0.10),
            ("r_ret_6m", -0.08),
        ),
        top_n=8,
        ret_col="ret_m1",
    ),
    Logic(
        name="forward_growth_export_quality_top5",
        description=(
            "매출/영업이익 성장과 수출 성장 확인 후, 아직 52주 고점 추격이 아닌 초기 추세만 선별"
        ),
        mask_expr=(
            "liquid & early_trend & not_extended & not_chasing_high "
            "& fin_rev_q70 & fin_op_q70 & export_positive"
        ),
        score_cols=(
            ("r_fin_rev_yoy", 0.18),
            ("r_fin_op_yoy", 0.16),
            ("r_export_yoy", 0.14),
            ("r_cf_ocf_margin", 0.10),
            ("r_supply20_to_turnover", 0.12),
            ("r_low_vol60", 0.10),
            ("r_ret_3m", 0.08),
            ("r_near_high52", -0.12),
        ),
    ),
    Logic(
        name="forward_supply_demand_base_top5",
        description=(
            "수급 누적/저변동 베이스 위에 수주잔고, 원재료 투입, 수출 중 하나 이상의 수요 신호가 붙은 종목 선별"
        ),
        mask_expr=(
            "liquid & early_trend & not_extended & base_recovery "
            "& supply60_q70 & low_vol_q60 & demand_any"
        ),
        score_cols=(
            ("r_supply60_to_turnover", 0.18),
            ("r_low_vol60", 0.14),
            ("r_backlog_yoy", 0.13),
            ("r_raw_material_cost_yoy", 0.12),
            ("r_annual_material_yoy", 0.10),
            ("r_export_yoy", 0.10),
            ("r_fin_rev_yoy", 0.10),
            ("r_ret_6m", -0.07),
            ("r_near_high52", -0.06),
        ),
        ret_col="ret_m1_stop12_trail",
    ),
    Logic(
        name="forward_turnaround_cashflow_top5",
        description=(
            "매출 가속/영업이익 턴어라운드와 현금흐름 품질이 같이 개선되는 초기 회복형 종목 선별"
        ),
        mask_expr=(
            "liquid & early_trend & not_extended & base_recovery "
            "& rev_accel_pos & op_turnaround & ocf_q60"
        ),
        score_cols=(
            ("r_fin_rev_accel", 0.18),
            ("r_fin_op_yoy", 0.14),
            ("r_cf_ocf_margin", 0.16),
            ("r_supply20_to_turnover", 0.12),
            ("r_low_vol60", 0.12),
            ("r_above_low52", 0.08),
            ("r_ret_3m", 0.08),
            ("r_fin_debt_ratio", -0.12),
        ),
    ),
]


def conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.row_factory = sqlite3.Row
    return c


def load_base() -> pd.DataFrame:
    df = pd.read_parquet(DATASET_PATH)
    df["signal_month"] = df["signal_month"].astype(str)
    df = df[df["signal_month"].between(ALL[0], ALL[1])].copy()
    df = df[df["market"].isin(["KOSPI", "KOSDAQ"])].copy()
    df = df[df["stock_code"].astype(str).str.fullmatch(r"\d{6}")].copy()
    df = df.drop_duplicates(["stock_code", "signal_month"], keep="last")
    return df


def add_ranks(df: pd.DataFrame) -> pd.DataFrame:
    rank_cols = [
        "ret_1m",
        "ret_3m",
        "ret_6m",
        "near_high52",
        "above_low52",
        "vol60",
        "avg_turnover20",
        "supply20_to_turnover",
        "supply60_to_turnover",
        "export_yoy",
        "fin_op_margin",
        "fin_rev_yoy",
        "fin_op_yoy",
        "fin_debt_ratio",
        "fin_rev_accel",
        "cf_ocf_margin",
        "backlog_yoy",
        "raw_material_cost_yoy",
        "annual_material_yoy",
    ]
    for col in rank_cols:
        if col in df.columns:
            df[f"r_{col}"] = pd.to_numeric(df[col], errors="coerce").groupby(df["signal_month"]).rank(pct=True)
    df["r_low_vol60"] = 1 - df["r_vol60"]
    return df


def attach_next_month_returns(df: pd.DataFrame) -> pd.DataFrame:
    start = str((pd.Period(df["signal_month"].min(), "M") + 1).start_time.date())
    end = str((pd.Period(df["signal_month"].max(), "M") + 1).end_time.date())
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
    for (code, exec_month), g in px.groupby(["stock_code", "exec_month"], sort=False):
        g = g.sort_values("date")
        entry = float(g.iloc[0]["open"])
        last_close = float(g.iloc[-1]["close"])
        stop10 = entry * 0.90
        trail_high = entry
        sell_stop10 = None
        sell_trail12 = None
        for _, r in g.iterrows():
            high = float(r["high"] or r["close"])
            close = float(r["close"])
            trail_high = max(trail_high, high)
            if sell_stop10 is None and close <= stop10:
                sell_stop10 = close
            if sell_trail12 is None and close <= trail_high * 0.88 and close > entry * 0.97:
                sell_trail12 = close
        rows.append(
            {
                "stock_code": code,
                "signal_month": str(pd.Period(exec_month, "M") - 1),
                "entry_open": entry,
                "exit_close": last_close,
                "ret_m1": last_close / entry - 1,
                "ret_m1_stop10": (sell_stop10 or last_close) / entry - 1,
                "ret_m1_stop12_trail": (sell_trail12 or sell_stop10 or last_close) / entry - 1,
            }
        )
    out = df.merge(pd.DataFrame(rows), on=["stock_code", "signal_month"], how="left")
    return out[out["ret_m1"].notna()].copy()


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
    km["ma6"] = km["close"].rolling(6, min_periods=4).mean()
    km["ma10"] = km["close"].rolling(10, min_periods=6).mean()
    km["ret_3m"] = km["close"].pct_change(3)
    km["regime_ok"] = (km["close"] > km["ma6"]) & ~((km["close"] < km["ma10"]) & (km["ret_3m"] < 0))
    km["kospi_ret_m1"] = km["close"].shift(-1) / km["close"] - 1
    out = df.merge(km[["signal_month", "regime_ok", "kospi_ret_m1"]], on="signal_month", how="left")
    universe_bench = out.groupby("signal_month")["ret_m1"].mean().rename("liquid_universe_ret_m1")
    return out.merge(universe_bench, on="signal_month", how="left")


def conditions(df: pd.DataFrame) -> dict[str, pd.Series]:
    n = lambda col: pd.to_numeric(df.get(col), errors="coerce")
    demand_any = (
        (n("r_backlog_yoy") >= 0.70)
        | (n("r_raw_material_cost_yoy") >= 0.70)
        | (n("r_annual_material_yoy") >= 0.70)
        | (n("export_yoy") > 0.10)
    )
    out = {
        "liquid": (n("avg_turnover20") >= 2e9) & (n("close") >= 1000),
        "early_trend": (n("close") > n("ma60") * 0.97) & (n("close") < n("ma60") * 1.22) & (n("ma20") >= n("ma60") * 0.96),
        "trend_alive": (n("ma20") > n("ma60")) & (n("close") > n("ma120") * 0.96),
        "not_extended": n("ret_6m").between(-0.10, 0.80),
        "not_chasing_high": n("near_high52").between(0.45, 0.96),
        "not_chasing_high_loose": n("near_high52").between(0.40, 1.02),
        "base_recovery": n("above_low52").between(0.15, 2.50),
        "fin_rev_q60": n("r_fin_rev_yoy") >= 0.60,
        "fin_rev_q70": n("r_fin_rev_yoy") >= 0.70,
        "fin_op_q60": n("r_fin_op_yoy") >= 0.60,
        "fin_op_q70": n("r_fin_op_yoy") >= 0.70,
        "export_q60": n("r_export_yoy") >= 0.60,
        "export_positive": n("export_yoy") > 0,
        "supply20_q60": n("r_supply20_to_turnover") >= 0.60,
        "supply60_q70": n("r_supply60_to_turnover") >= 0.70,
        "supply60_q60": n("r_supply60_to_turnover") >= 0.60,
        "low_vol_q60": n("r_low_vol60") >= 0.60,
        "demand_any": demand_any,
        "rev_accel_pos": n("fin_rev_accel") > 0,
        "op_turnaround": n("fin_op_turnaround") > 0,
        "ocf_q60": n("r_cf_ocf_margin") >= 0.60,
    }
    return {k: v.fillna(False) for k, v in out.items()}


def eval_mask(df: pd.DataFrame, expr: str) -> pd.Series:
    scope = conditions(df)
    return pd.eval(expr, local_dict=scope, engine="python").fillna(False)


def score(df: pd.DataFrame, cols: tuple[tuple[str, float], ...]) -> pd.Series:
    out = pd.Series(0.0, index=df.index)
    for col, weight in cols:
        out += weight * pd.to_numeric(df.get(col), errors="coerce").fillna(0.0)
    return out


def summarize(monthly: pd.Series) -> dict:
    monthly = monthly.astype(float)
    eq = (1 + monthly).cumprod()
    dd = eq / eq.cummax() - 1
    years = len(monthly) / 12
    return {
        "months": int(len(monthly)),
        "active_months": int((monthly != 0).sum()),
        "total_return_pct": round(float((eq.iloc[-1] - 1) * 100), 2),
        "cagr_pct": round(float((eq.iloc[-1] ** (1 / years) - 1) * 100), 2) if years > 0 else 0.0,
        "avg_monthly_pct": round(float(monthly.mean() * 100), 2),
        "hit_month_pct": round(float((monthly[monthly != 0] > 0).mean() * 100), 1) if (monthly != 0).any() else 0.0,
        "mdd_pct": round(float(dd.min() * 100), 2),
        "sharpe": round(float(monthly.mean() / monthly.std() * math.sqrt(12)), 2) if monthly.std() else 0.0,
    }


def backtest(df: pd.DataFrame, logic: Logic, start: str, end: str) -> tuple[dict, pd.DataFrame, pd.DataFrame]:
    sub = df[df["signal_month"].between(start, end)].copy()
    months = sorted(sub["signal_month"].unique())
    mask = eval_mask(sub, logic.mask_expr) & sub["regime_ok"].fillna(False)
    picks = sub[mask].copy()
    picks["score"] = score(picks, logic.score_cols)
    picks = (
        picks.sort_values(["signal_month", "score"], ascending=[True, False])
        .groupby("signal_month")
        .head(logic.top_n)
        .copy()
    )
    picks["ret_net"] = pd.to_numeric(picks[logic.ret_col], errors="coerce").fillna(0.0) - TCOST
    monthly = picks.groupby("signal_month")["ret_net"].mean().reindex(months, fill_value=0.0)
    bench = sub.groupby("signal_month")["liquid_universe_ret_m1"].first().reindex(months).fillna(0.0)
    kospi = sub.groupby("signal_month")["kospi_ret_m1"].first().reindex(months).fillna(0.0)
    monthly_df = pd.DataFrame(
        {
            "signal_month": months,
            "strategy_ret": monthly.values,
            "liquid_universe_ret": bench.values,
            "kospi_ret": kospi.values,
            "pick_count": picks.groupby("signal_month")["stock_code"].count().reindex(months, fill_value=0).values,
        }
    )
    s = summarize(monthly)
    b = summarize(bench)
    k = summarize(kospi)
    s.update(
        {
            "period": f"{start}~{end}",
            "trade_count": int(len(picks)),
            "avg_names": round(float(monthly_df["pick_count"].mean()), 2),
            "liquid_universe_return_pct": b["total_return_pct"],
            "kospi_return_pct": k["total_return_pct"],
            "alpha_vs_liquid_universe_pct": round(s["total_return_pct"] - b["total_return_pct"], 2),
            "alpha_vs_kospi_pct": round(s["total_return_pct"] - k["total_return_pct"], 2),
        }
    )
    pick_cols = [
        "signal_month",
        "stock_code",
        "stock_name",
        "market",
        "sector_large",
        "score",
        "entry_open",
        "exit_close",
        logic.ret_col,
        "ret_net",
    ]
    return s, monthly_df, picks[[c for c in pick_cols if c in picks.columns]].copy()


def winner_feature_notes(df: pd.DataFrame) -> list[dict]:
    profile_cols = [
        "ret_3m",
        "near_high52",
        "above_low52",
        "avg_turnover20",
        "fin_rev_yoy",
        "fin_op_yoy",
        "export_yoy",
        "supply60_to_turnover",
        "cf_ocf_margin",
        "backlog_yoy",
        "raw_material_cost_yoy",
    ]
    rows = []
    target = df["target_market2x_6m"].fillna(0).astype(bool)
    for col in profile_cols:
        s = pd.to_numeric(df[col], errors="coerce")
        if s.notna().mean() < 0.05:
            continue
        rows.append(
            {
                "feature": col,
                "winner_median": round(float(s[target].median()), 6),
                "nonwinner_median": round(float(s[~target].median()), 6),
                "winner_p75": round(float(s[target].quantile(0.75)), 6),
                "nonwinner_p75": round(float(s[~target].quantile(0.75)), 6),
                "coverage_pct": round(float(s.notna().mean() * 100), 1),
            }
        )
    return rows


def main() -> int:
    OUT_DIR.mkdir(exist_ok=True)
    df = add_market_regime(attach_next_month_returns(add_ranks(load_base())))
    summaries = []
    details: dict[str, dict] = {}
    for logic in LOGICS:
        detail = {"description": logic.description, "mask": logic.mask_expr, "score_cols": logic.score_cols}
        all_picks = []
        for label, period in [("train", TRAIN), ("valid", VALID), ("oos", OOS), ("all", ALL)]:
            summary, monthly, picks = backtest(df, logic, *period)
            summary = {"logic": logic.name, "split": label, **summary}
            summaries.append(summary)
            detail[label] = summary
            if label == "all":
                monthly.to_csv(OUT_DIR / f"{logic.name}_20260623_monthly.csv", index=False)
                picks.to_csv(OUT_DIR / f"{logic.name}_20260623_picks.csv", index=False)
            picks.insert(0, "split", label)
            all_picks.append(picks)
        pd.concat(all_picks, ignore_index=True).to_csv(
            OUT_DIR / f"{logic.name}_20260623_all_split_picks.csv", index=False
        )
        details[logic.name] = detail

    summary_df = pd.DataFrame(summaries)
    summary_path = OUT_DIR / "forward_winner_signal_logic_20260623_summary.csv"
    json_path = OUT_DIR / "forward_winner_signal_logic_20260623_result.json"
    profile_path = OUT_DIR / "forward_winner_signal_logic_20260623_feature_notes.csv"
    summary_df.to_csv(summary_path, index=False)
    pd.DataFrame(winner_feature_notes(df)).to_csv(profile_path, index=False)
    payload = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "method": {
            "selection_guardrail": "future return columns are used only for population-level feature notes, never for a pick score or mask",
            "execution": "signal month features -> next month first open buy -> month-end close sell, equal-weight top 5",
            "transaction_cost": TCOST,
            "periods": {"train": TRAIN, "valid": VALID, "oos": OOS, "all": ALL},
            "benchmark": "same-month liquid tradable universe equal-weight; KOSPI retained only as a warning reference because local ^KS11 has known data-quality concerns",
        },
        "logic_count": len(LOGICS),
        "summaries": summaries,
        "details": details,
        "outputs": {
            "summary_csv": str(summary_path),
            "feature_notes_csv": str(profile_path),
        },
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
