#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "research_outputs" / "market2x_signal_dataset.parquet"
OUT = ROOT / "research_outputs" / "three_x_profit_objective_logic_2021plus.json"


def clean_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def load_data() -> pd.DataFrame:
    df = pd.read_parquet(DATASET).copy()
    df = df[(df["signal_month"] >= "2021-01") & (df["signal_month"] <= "2025-03")].copy()
    df = df[df["fwd_12m_ret"].notna()].copy()
    df = df[df["market"].isin(["KOSPI", "KOSDAQ"])].copy()
    df = df[df["stock_code"].astype(str).str.match(r"^\d{6}$", na=False)].copy()
    df = df[clean_num(df["close"]) >= 1000].copy()
    df = df[clean_num(df["avg_turnover20"]) >= 1e9].copy()
    # Remove obvious corporate-action/data artifacts while keeping multi-baggers.
    df = df[df["fwd_12m_ret"].between(-0.85, 20)].copy()
    return df


def condition_map(df: pd.DataFrame) -> dict[str, pd.Series]:
    c: dict[str, pd.Series] = {}
    n = lambda col: clean_num(df[col]) if col in df else pd.Series(np.nan, index=df.index)
    c["KOSDAQ"] = df["market"].eq("KOSDAQ")
    c["core_sector"] = df["sector_large"].isin(["IT", "의료", "경기소비재", "산업재"])
    c["turnover_2b"] = n("avg_turnover20") >= 2e9
    c["turnover_5b"] = n("avg_turnover20") >= 5e9
    c["vol_ratio_3x"] = n("vol_ratio20") >= 3
    c["vol_ratio_5x"] = n("vol_ratio20") >= 5
    c["vol_ratio_8x"] = n("vol_ratio20") >= 8
    c["ret_1m_pos"] = n("ret_1m") > 0
    c["ret_1m_20"] = n("ret_1m") >= 0.20
    c["ret_1m_50"] = n("ret_1m") >= 0.50
    c["ret_3m_30"] = n("ret_3m") >= 0.30
    c["ret_3m_60"] = n("ret_3m") >= 0.60
    c["ret_6m_50"] = n("ret_6m") >= 0.50
    c["near_high_80"] = n("near_high52") >= 0.80
    c["near_high_90"] = n("near_high52") >= 0.90
    c["not_too_extended"] = n("from_high52").fillna(-1).abs() <= 0.35
    c["supply20_pos"] = n("supply20_to_turnover") > 0
    c["supply60_pos"] = n("supply60_to_turnover") > 0
    c["supply20_strong"] = n("supply20_to_turnover") >= n("supply20_to_turnover").quantile(0.70)
    c["supply60_strong"] = n("supply60_to_turnover") >= n("supply60_to_turnover").quantile(0.70)
    c["export_yoy_30"] = n("export_yoy") >= 0.30
    c["export_yoy_100"] = n("export_yoy") >= 1.00
    c["contract"] = n("contract_cnt") > 0
    c["insider_buy"] = n("insider_buy_cnt") > 0
    c["fin_growth"] = (n("fin_rev_yoy") > 0) & (n("fin_op_yoy") > 0)
    c["op_margin_pos"] = n("fin_op_margin") > 0
    c["debt_le_500_or_unknown"] = n("fin_debt_ratio").isna() | (n("fin_debt_ratio") <= 500)
    c["debt_le_200_or_unknown"] = n("fin_debt_ratio").isna() | (n("fin_debt_ratio") <= 200)
    c["backlog"] = (n("backlog_present") > 0) | (n("backlog_yoy") > 0) | (n("new_order_yoy") > 0)
    c["raw_material_yoy"] = (n("raw_material_cost_yoy") > 0) | (n("dart_material_yoy") > 0) | (n("annual_material_yoy") > 0)
    c["short_cover"] = n("short_cover_1m") > 0
    return {k: v.fillna(False) for k, v in c.items()}


def metrics(df: pd.DataFrame, mask: pd.Series, name: str) -> dict | None:
    s = df[mask.fillna(False)].copy()
    count = len(s)
    if count < 40:
        return None
    r12 = clean_num(s["fwd_12m_ret"]).dropna()
    r6 = clean_num(s["fwd_6m_ret"]).dropna()
    if len(r12) < 40:
        return None
    gains = r12[r12 > 0].sum()
    losses = -r12[r12 < 0].sum()
    return {
        "name": name,
        "count": int(count),
        "stocks": int(s["stock_code"].nunique()),
        "months": int(s["signal_month"].nunique()),
        "avg_12m_ret_pct": round(float(r12.mean() * 100), 2),
        "median_12m_ret_pct": round(float(r12.median() * 100), 2),
        "avg_6m_ret_pct": round(float(r6.mean() * 100), 2) if len(r6) else None,
        "win_rate_12m_pct": round(float((r12 > 0).mean() * 100), 2),
        "double_rate_12m_pct": round(float((r12 >= 1.0).mean() * 100), 2),
        "triple_rate_12m_pct": round(float((r12 >= 2.0).mean() * 100), 2),
        "loss_rate_30pct_pct": round(float((r12 <= -0.30).mean() * 100), 2),
        "p10_12m_ret_pct": round(float(r12.quantile(0.10) * 100), 2),
        "profit_factor": round(float(gains / losses), 2) if losses > 0 else None,
        "total_profit_units": round(float(r12.sum()), 2),
        "profit_score": round(float(r12.mean() * np.sqrt(len(r12))), 4),
    }


def main() -> int:
    df = load_data()
    cond = condition_map(df)
    names = [
        "KOSDAQ", "core_sector", "turnover_2b", "turnover_5b",
        "vol_ratio_3x", "vol_ratio_5x", "vol_ratio_8x",
        "ret_1m_pos", "ret_1m_20", "ret_1m_50",
        "ret_3m_30", "ret_3m_60", "ret_6m_50",
        "near_high_80", "near_high_90", "not_too_extended",
        "supply20_pos", "supply60_pos", "supply20_strong", "supply60_strong",
        "export_yoy_30", "export_yoy_100", "contract", "insider_buy",
        "fin_growth", "op_margin_pos", "debt_le_500_or_unknown", "debt_le_200_or_unknown",
        "backlog", "raw_material_yoy", "short_cover",
    ]
    rows = []
    # Include single conditions and combinations.
    for k in range(1, 6):
        for combo in combinations(names, k):
            cs = set(combo)
            if {"vol_ratio_3x", "vol_ratio_5x"} <= cs or {"vol_ratio_3x", "vol_ratio_8x"} <= cs or {"vol_ratio_5x", "vol_ratio_8x"} <= cs:
                continue
            if {"ret_1m_pos", "ret_1m_20"} <= cs or {"ret_1m_pos", "ret_1m_50"} <= cs or {"ret_1m_20", "ret_1m_50"} <= cs:
                continue
            if {"ret_3m_30", "ret_3m_60"} <= cs:
                continue
            if {"near_high_80", "near_high_90"} <= cs:
                continue
            if {"debt_le_500_or_unknown", "debt_le_200_or_unknown"} <= cs:
                continue
            mask = pd.Series(True, index=df.index)
            for name in combo:
                mask &= cond[name]
            row = metrics(df, mask, " AND ".join(combo))
            if row:
                rows.append(row)

    by_avg = sorted(rows, key=lambda r: (r["avg_12m_ret_pct"], r["count"]), reverse=True)[:50]
    by_total = sorted(rows, key=lambda r: (r["total_profit_units"], r["avg_12m_ret_pct"]), reverse=True)[:50]
    by_score = sorted(rows, key=lambda r: (r["profit_score"], r["avg_12m_ret_pct"]), reverse=True)[:50]
    by_pf = sorted(rows, key=lambda r: (r["profit_factor"] or 0, r["avg_12m_ret_pct"], r["count"]), reverse=True)[:50]

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": {
            "dataset": str(DATASET),
            "signal_month": "2021-01~2025-03",
            "unit": "monthly stock signal",
            "rows": int(len(df)),
            "stocks": int(df["stock_code"].nunique()),
            "base_avg_12m_ret_pct": round(float(df["fwd_12m_ret"].mean() * 100), 2),
            "base_median_12m_ret_pct": round(float(df["fwd_12m_ret"].median() * 100), 2),
            "base_triple_rate_12m_pct": round(float((df["fwd_12m_ret"] >= 2.0).mean() * 100), 2),
            "min_count": 40,
            "objective_note": "수익 목적함수: 평균 12개월 수익, 총 profit units, profit_score(mean*sqrt(n)), profit factor를 함께 랭킹.",
        },
        "top_by_profit_score": by_score,
        "top_by_avg_12m_return": by_avg,
        "top_by_total_profit": by_total,
        "top_by_profit_factor": by_pf,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(OUT), "scope": payload["scope"], "top_by_profit_score": by_score[:20]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
