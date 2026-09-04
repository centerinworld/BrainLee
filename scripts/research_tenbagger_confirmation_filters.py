#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
OUT_JSON = ROOT / "research_outputs" / "tenbagger_confirmation_filters_20260811.json"
OUT_MD = ROOT / "research_outputs" / "tenbagger_confirmation_filters_20260811.md"
sys.path.insert(0, str(ROOT))

from database import engine  # noqa: E402
from scripts import discover_historical_tenbagger_signals as discovery  # noqa: E402


def _quarter_available_date(frame: pd.DataFrame) -> pd.Series:
    quarter = pd.to_numeric(frame["fiscal_quarter"], errors="coerce").astype("Int64")
    year = pd.to_numeric(frame["fiscal_year"], errors="coerce").astype("Int64")
    month = quarter.map({1: 5, 2: 8, 3: 11, 4: 3}).astype("Int64")
    release_year = year + quarter.eq(4).astype("Int64")
    # Use the day after the standard filing deadline to avoid same-day look-ahead.
    day = quarter.map({1: 16, 2: 16, 3: 16, 4: 31}).astype("Int64")
    return pd.to_datetime(
        {"year": release_year, "month": month, "day": day}, errors="coerce"
    )


def _load_cash_features() -> pd.DataFrame:
    frame = pd.read_sql_query(
        text(
            """
            WITH ranked AS (
                SELECT stock_code, fiscal_year, fiscal_quarter,
                       rolling4_ocf_margin_pct, rolling4_fcf_margin_pct,
                       rolling4_ocf_positive_quarters, signal_type, risk_score,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code, fiscal_year, fiscal_quarter
                           ORDER BY CASE WHEN fs_div='CFS' THEN 0 ELSE 1 END,
                                    updated_at DESC NULLS LAST
                       ) AS rn
                FROM cash_conversion_signals
            )
            SELECT stock_code, fiscal_year, fiscal_quarter,
                   rolling4_ocf_margin_pct, rolling4_fcf_margin_pct,
                   rolling4_ocf_positive_quarters, signal_type, risk_score
            FROM ranked WHERE rn=1
            """
        ),
        engine,
    )
    frame["available_date"] = _quarter_available_date(frame)
    return frame.rename(
        columns={
            "rolling4_ocf_margin_pct": "cash_ocf_margin_pct",
            "rolling4_fcf_margin_pct": "cash_fcf_margin_pct",
            "rolling4_ocf_positive_quarters": "cash_positive_quarters",
            "signal_type": "cash_signal_type",
            "risk_score": "cash_risk_score",
        }
    )


def _load_inventory_features() -> pd.DataFrame:
    frame = pd.read_sql_query(
        text(
            """
            WITH ranked AS (
                SELECT stock_code, fiscal_year, fiscal_quarter, signal_type,
                       signal_score, risk_score,
                       ROW_NUMBER() OVER (
                           PARTITION BY stock_code, fiscal_year, fiscal_quarter
                           ORDER BY CASE WHEN fs_div='CFS' THEN 0 ELSE 1 END,
                                    updated_at DESC NULLS LAST
                       ) AS rn
                FROM inventory_sales_signals
                WHERE quality_flag='ok'
            )
            SELECT stock_code, fiscal_year, fiscal_quarter, signal_type,
                   signal_score, risk_score
            FROM ranked WHERE rn=1
            """
        ),
        engine,
    )
    frame["available_date"] = _quarter_available_date(frame)
    return frame.rename(
        columns={
            "signal_type": "inventory_signal_type",
            "signal_score": "inventory_signal_score",
            "risk_score": "inventory_risk_score",
        }
    )


def _attach_latest(snapshots: pd.DataFrame, features: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    if features.empty:
        for column in columns:
            snapshots[column] = pd.NA
        return snapshots
    right = features.dropna(subset=["available_date", "stock_code"]).copy()
    right["stock_code"] = right["stock_code"].astype(str)
    left = snapshots.copy()
    left["stock_code"] = left["stock_code"].astype(str)
    return pd.merge_asof(
        left.sort_values(["snapshot_date", "stock_code"]),
        right[["stock_code", "available_date", *columns]].sort_values(
            ["available_date", "stock_code"]
        ),
        left_on="snapshot_date",
        right_on="available_date",
        by="stock_code",
        direction="backward",
        allow_exact_matches=True,
    )


def _condition(feature: str, operator: str, value) -> dict:
    return {"feature": feature, "operator": operator, "value": value}


FIXED_RULES = {
    "existing_precision_tier": [
        _condition("contract_count_1y", ">=", 2),
        _condition("avg_turnover_20d_억", ">=", 10),
        _condition("op_growth_clean", ">=", 0.5),
    ],
    "cash_quality_confirmation": [
        _condition("contract_count_1y", ">=", 2),
        _condition("avg_turnover_20d_억", ">=", 10),
        _condition("op_growth_clean", ">=", 0.5),
        _condition("cash_positive_quarters", ">=", 3),
        _condition("cash_ocf_margin_pct", ">=", 5),
    ],
    "cash_and_dilution_confirmation": [
        _condition("contract_count_1y", ">=", 2),
        _condition("avg_turnover_20d_억", ">=", 10),
        _condition("op_growth_clean", ">=", 0.5),
        _condition("cash_positive_quarters", ">=", 3),
        _condition("cash_ocf_margin_pct", ">=", 5),
        _condition("dilution_max_pct_1y", "<=", 10),
        _condition("dilution_amount_to_market_cap", "<=", 0.10),
    ],
    "free_cash_flow_confirmation": [
        _condition("contract_count_1y", ">=", 2),
        _condition("avg_turnover_20d_억", ">=", 10),
        _condition("op_growth_clean", ">=", 0.5),
        _condition("cash_fcf_margin_pct", ">=", 0),
    ],
    "business_and_cash_confirmation": [
        _condition("contract_count_1y", ">=", 2),
        _condition("earnings_backed", "==", True),
        _condition("cash_positive_quarters", ">=", 3),
        _condition("cash_ocf_margin_pct", ">=", 5),
    ],
    "demand_and_cash_confirmation": [
        _condition("contract_count_1y", ">=", 2),
        _condition("supply_20d_억", ">=", 10),
        _condition("cash_positive_quarters", ">=", 3),
        _condition("cash_ocf_margin_pct", ">=", 5),
    ],
    "growth_cash_confirmation": [
        _condition("rev_yoy_clean", ">=", 0.20),
        _condition("op_growth_clean", ">=", 1.0),
        _condition("avg_turnover_20d_억", ">=", 10),
        _condition("cash_positive_quarters", ">=", 3),
        _condition("cash_ocf_margin_pct", ">=", 5),
    ],
    "ceo_buy_confirmation": [
        _condition("contract_count_1y", ">=", 2),
        _condition("earnings_backed", "==", True),
        _condition("avg_turnover_20d_억", ">=", 10),
        _condition("ceo_buy_count_1y", ">=", 1),
    ],
    "inventory_digestion_confirmation": [
        _condition("contract_count_1y", ">=", 2),
        _condition("earnings_backed", "==", True),
        _condition("avg_turnover_20d_억", ">=", 10),
        _condition("inventory_signal_type", "==", "digestion"),
    ],
}


def _evaluate(frame: pd.DataFrame, conditions: list[dict]) -> dict:
    mask = discovery._rule_mask(frame, conditions)
    return {
        "row_metrics": discovery._metrics(frame, mask),
        "first_alert_metrics": discovery._first_alert_metrics(frame, mask),
    }


def _rule_name(conditions: list[dict]) -> str:
    return " AND ".join(discovery._condition_name(item) for item in conditions)


def main() -> None:
    snapshots, base_coverage = discovery._prepare_dataset()
    cash_columns = [
        "cash_ocf_margin_pct", "cash_fcf_margin_pct", "cash_positive_quarters",
        "cash_signal_type", "cash_risk_score",
    ]
    inventory_columns = [
        "inventory_signal_type", "inventory_signal_score", "inventory_risk_score",
    ]
    snapshots = _attach_latest(snapshots, _load_cash_features(), cash_columns)
    snapshots = _attach_latest(snapshots, _load_inventory_features(), inventory_columns)

    splits = {
        "train_2020_2022": snapshots[snapshots["snapshot_date"] <= pd.Timestamp("2022-12-31")],
        "validation_2023": snapshots[snapshots["snapshot_date"].between("2023-01-01", "2023-12-31")],
        "holdout_2024_h1": snapshots[snapshots["snapshot_date"].between("2024-01-01", "2024-07-31")],
        "all_labeled": snapshots,
    }
    results = []
    for name, conditions in FIXED_RULES.items():
        evaluations = {split: _evaluate(frame, conditions) for split, frame in splits.items()}
        yearly_lifts = [
            float(evaluations[split]["row_metrics"].get("lift") or 0)
            for split in ("train_2020_2022", "validation_2023", "holdout_2024_h1")
        ]
        overall_alert = evaluations["all_labeled"]["first_alert_metrics"]
        promotion_pass = bool(
            overall_alert["alerts"] >= 50
            and overall_alert["precision_pct"] >= 15.0
            and overall_alert["precision_95ci_pct"][0] >= 5.0
            and min(yearly_lifts) >= 1.2
        )
        results.append(
            {
                "name": name,
                "rule": _rule_name(conditions),
                "conditions": conditions,
                "evaluations": evaluations,
                "min_split_lift": round(min(yearly_lifts), 3),
                "promotion_pass": promotion_pass,
            }
        )
    results.sort(
        key=lambda item: (
            item["promotion_pass"],
            item["evaluations"]["holdout_2024_h1"]["first_alert_metrics"]["precision_pct"],
            item["min_split_lift"],
        ),
        reverse=True,
    )
    promoted = [item["name"] for item in results if item["promotion_pass"]]
    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "methodology": {
            "status": "exploratory_confirmation_not_independent_holdout",
            "feature_timing": "quarterly features available after statutory filing deadline",
            "target": "durable business-backed 24-month 10x",
            "snapshot_table": discovery.scoreboard._snapshot_table(),
            "point_in_time_universe": True,
            "rule_selection": "nine domain-fixed confirmation rules; no automated threshold search",
            "promotion_gate": "50 first alerts, precision >=15%, Wilson lower bound >=5%, every split lift >=1.2",
            "limitations": [
                "2024 holdout was used by earlier research and is no longer untouched",
                "delisted-history coverage is improved but incomplete before 2019",
                "quarterly filing dates are conservative inferred deadlines, not exact receipt timestamps",
            ],
        },
        "rows": len(snapshots),
        "stocks": int(snapshots["stock_code"].nunique()),
        "coverage_pct": {
            **base_coverage,
            **{
                column: round(float(snapshots[column].notna().mean()) * 100, 2)
                for column in cash_columns + inventory_columns
            },
        },
        "promotion_target_pct": 15.0,
        "promoted_rules": promoted,
        "production_ready": bool(promoted),
        "auto_trading_allowed": False,
        "results": results,
    }
    tmp = OUT_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT_JSON)

    lines = [
        "# 텐버거 확인 필터 연구 (2026-08-11)",
        "",
        f"- 표본: {len(snapshots):,}행 / {payload['stocks']:,}종목",
        f"- 승격 규칙: {len(promoted)}개",
        "- 자동매매: 비활성",
        "- 판정: 독립 홀드아웃이 아닌 탐색적 확인 연구",
        "",
        "| 규칙 | 전체 최초알림 | 전체 적중률 | 3배 | 5배 | 2024H1 적중률 | 최소 split lift | 승격 |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in results:
        overall = item["evaluations"]["all_labeled"]["first_alert_metrics"]
        holdout = item["evaluations"]["holdout_2024_h1"]["first_alert_metrics"]
        lines.append(
            f"| {item['name']} | {overall['alerts']} | {overall['precision_pct']}% | "
            f"{overall['hit_3x_pct']}% | {overall['hit_5x_pct']}% | "
            f"{holdout['precision_pct']}% | {item['min_split_lift']}x | "
            f"{'통과' if item['promotion_pass'] else '보류'} |"
        )
    lines += [
        "",
        "## 결론",
        "",
        "15% 정밀도와 연도별 안정성 기준을 모두 통과한 규칙만 승격한다. "
        "통과 규칙이 없으면 기존 연구 태그를 유지하고 추천·자동매매에는 연결하지 않는다.",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({
        "production_ready": payload["production_ready"],
        "promoted_rules": promoted,
        "coverage_pct": payload["coverage_pct"],
        "results": [
            {
                "name": item["name"],
                "overall": item["evaluations"]["all_labeled"]["first_alert_metrics"],
                "holdout": item["evaluations"]["holdout_2024_h1"]["first_alert_metrics"],
                "min_split_lift": item["min_split_lift"],
                "promotion_pass": item["promotion_pass"],
            }
            for item in results
        ],
        "output_json": str(OUT_JSON),
        "output_md": str(OUT_MD),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-table", default="strategy_feature_snapshot_pit_v2")
    args = parser.parse_args()
    os.environ["TENBAGGER_SNAPSHOT_TABLE"] = args.snapshot_table
    main()
