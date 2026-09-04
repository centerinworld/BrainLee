#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "research_outputs" / "tenbagger_walkforward_cohorts_20260811.json"
OUT_MD = ROOT / "research_outputs" / "tenbagger_walkforward_cohorts_20260811.md"
sys.path.insert(0, str(ROOT))

from scripts import discover_historical_tenbagger_signals as discovery  # noqa: E402


RULES = {
    "market_baseline": [],
    "legacy_score_55": [
        {"feature": "heuristic_score", "operator": ">=", "value": 55},
    ],
    "risk_adjusted_earnings_demand": [
        {"feature": "supply_20d_억", "operator": ">=", "value": 10},
        {"feature": "op_growth_clean", "operator": ">=", "value": 1.0},
    ],
    "cross_section_rank_top10": [
        {"feature": "earnings_demand_rank", "operator": ">=", "value": 0.90},
    ],
}


def _yearly_first_alerts(frame: pd.DataFrame, conditions: list[dict]) -> list[dict]:
    selected = frame.copy()
    if conditions:
        selected = selected[discovery._rule_mask(selected, conditions)].copy()
    selected["cohort_year"] = selected["snapshot_date"].dt.year
    rows = []
    for year, cohort in selected.groupby("cohort_year", sort=True):
        cohort = cohort.sort_values(["stock_code", "snapshot_date"])
        first = cohort.drop_duplicates("stock_code", keep="first")
        metrics = discovery._first_alert_metrics(
            first, pd.Series(True, index=first.index)
        )
        metrics["year"] = int(year)
        rows.append(metrics)
    return rows


def main() -> None:
    frame, coverage = discovery._prepare_dataset()
    frame["op_growth_rank"] = frame.groupby("snapshot_date")["op_growth_clean"].rank(pct=True)
    frame["supply_rank"] = frame.groupby("snapshot_date")["supply_20d_억"].rank(pct=True)
    frame["revenue_growth_rank"] = frame.groupby("snapshot_date")["rev_yoy_clean"].rank(pct=True)
    frame["earnings_demand_rank"] = frame[
        ["op_growth_rank", "supply_rank", "revenue_growth_rank"]
    ].mean(axis=1, skipna=False)
    cohorts = {
        name: _yearly_first_alerts(frame, conditions)
        for name, conditions in RULES.items()
    }
    baseline_by_year = {item["year"]: item for item in cohorts["market_baseline"]}
    for name, rows in cohorts.items():
        for item in rows:
            baseline = baseline_by_year.get(item["year"], {})
            base_precision = float(baseline.get("precision_pct") or 0)
            item["precision_lift_vs_market"] = (
                round(item["precision_pct"] / base_precision, 3)
                if base_precision else None
            )
            item["payoff_to_pain_delta_vs_market"] = (
                round(item["median_payoff_to_pain"] - baseline["median_payoff_to_pain"], 2)
                if item["median_payoff_to_pain"] is not None
                and baseline.get("median_payoff_to_pain") is not None else None
            )

    candidate = cohorts["risk_adjusted_earnings_demand"]
    positive_precision_years = sum(item["precision_pct"] > 0 for item in candidate)
    risk_better_years = sum(
        (item["payoff_to_pain_delta_vs_market"] or 0) >= 0 for item in candidate
    )
    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "snapshot_table": discovery.scoreboard._snapshot_table(),
        "methodology": {
            "policy": "fixed rules; first alert per stock per calendar-year cohort",
            "selection": "no thresholds are selected from yearly results",
            "target": "durable business-backed 24-month 10x",
            "status": "exploratory reused historical periods; not an independent holdout",
            "cross_section_rank": (
                "equal-weight monthly percentiles of operating-profit growth, "
                "20-day supply, and revenue growth; complete cases only"
            ),
        },
        "rows": len(frame),
        "stocks": int(frame["stock_code"].nunique()),
        "coverage_pct": coverage,
        "rules": RULES,
        "cohorts": cohorts,
        "candidate_stability": {
            "cohort_years": len(candidate),
            "years_with_tenbagger_hit": positive_precision_years,
            "years_with_payoff_to_pain_at_or_above_market": risk_better_years,
            "stable_every_year": bool(
                candidate
                and positive_precision_years == len(candidate)
                and risk_better_years == len(candidate)
            ),
            "sustainable_signal_count": int(
                bool(
                    candidate
                    and positive_precision_years == len(candidate)
                    and risk_better_years == len(candidate)
                )
            ),
        },
        "production_ready": False,
        "auto_trading_allowed": False,
    }
    tmp = OUT_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT_JSON)

    lines = [
        "# 텐버거 연도별 워크포워드 코호트 (2026-08-11)",
        "",
        "- 종목별·연도별 최초 알림만 평가",
        "- 임계값 재탐색 없음",
        "- 자동매매 비활성",
        "",
        "| 연도 | 규칙 | 알림 | 10배 | 3배 | 5배 | 최고점 전 손실 | 수익/고통 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|",
    ]
    for name, rows in cohorts.items():
        for item in rows:
            lines.append(
                f"| {item['year']} | {name} | {item['alerts']} | "
                f"{item['precision_pct']}% | {item['hit_3x_pct']}% | "
                f"{item['hit_5x_pct']}% | {item['median_pre_peak_loss_pct']}% | "
                f"{item['median_payoff_to_pain']} |"
            )
    stability = payload["candidate_stability"]
    lines += [
        "",
        "## 결론",
        "",
        f"- 위험조정 후보의 10배 적중 연도: {stability['years_with_tenbagger_hit']}/{stability['cohort_years']}",
        f"- 시장 이상 수익/고통 연도: {stability['years_with_payoff_to_pain_at_or_above_market']}/{stability['cohort_years']}",
        f"- 전 연도 안정 통과: `{str(stability['stable_every_year']).lower()}`",
        "- 전 연도 안정 통과와 독립 미래검증 전에는 실전 승격하지 않는다.",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-table", default="strategy_feature_snapshot_pit_v2")
    args = parser.parse_args()
    os.environ["TENBAGGER_SNAPSHOT_TABLE"] = args.snapshot_table
    main()
