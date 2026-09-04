#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import os
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
CAUSES_PATH = ROOT / "research_outputs" / "historical_tenbagger_causes.json"
OUT_JSON = ROOT / "research_outputs" / "historical_tenbagger_signal_discovery.json"
OUT_MD = ROOT / "research_outputs" / "historical_tenbagger_signal_discovery.md"

sys.path.insert(0, str(ROOT))
from database import engine  # noqa: E402
import scripts.research_historical_tenbagger_scoreboard_v2 as scoreboard  # noqa: E402


def _load_target_codes() -> tuple[set[str], set[str]]:
    payload = json.loads(CAUSES_PATH.read_text(encoding="utf-8"))
    included = {
        str(item["stock_code"]).zfill(6)
        for item in payload.get("results", [])
        if item.get("sample_decision") != "non_operating_excluded"
    }
    excluded = {
        str(item["stock_code"]).zfill(6)
        for item in payload.get("results", [])
        if item.get("sample_decision") == "non_operating_excluded"
    }
    return included, excluded


def _event_map(frame: pd.DataFrame, date_column: str, value_column: str | None = None) -> dict:
    result = {}
    if frame.empty:
        return result
    frame = frame.copy()
    frame[date_column] = pd.to_datetime(frame[date_column], format="mixed", errors="coerce")
    frame = frame.dropna(subset=[date_column, "stock_code"])
    for code, group in frame.groupby("stock_code"):
        group = group.sort_values(date_column)
        dates = group[date_column].astype("int64").tolist()
        values = (
            pd.to_numeric(group[value_column], errors="coerce").fillna(0).tolist()
            if value_column
            else [1.0] * len(group)
        )
        result[str(code)] = (dates, values)
    return result


def _attach_trailing_event_features(snapshots: pd.DataFrame) -> pd.DataFrame:
    codes = snapshots["stock_code"].astype(str).drop_duplicates().tolist()
    contracts = pd.read_sql_query(
        text(
            """
            SELECT stock_code, disclosed_at, contract_ratio_pct, report_nm
            FROM dart_contracts
            WHERE stock_code = ANY(:codes)
            """
        ),
        engine,
        params={"codes": codes},
    )
    contracts = contracts[
        ~contracts["report_nm"].fillna("").str.contains("정정", regex=False)
    ].drop_duplicates(["stock_code", "disclosed_at", "report_nm", "contract_ratio_pct"])
    insiders = pd.read_sql_query(
        text(
            """
            SELECT stock_code, rcept_dt, change_amount, is_ceo, rcept_no
            FROM dart_insider_holdings
            WHERE stock_code = ANY(:codes) AND COALESCE(change_amount, 0) > 0
            """
        ),
        engine,
        params={"codes": codes},
    )
    insiders = insiders.drop_duplicates(["stock_code", "rcept_no", "rcept_dt", "change_amount", "is_ceo"])
    contract_map = _event_map(contracts, "disclosed_at", "contract_ratio_pct")
    insider_map = _event_map(insiders, "rcept_dt", "change_amount")
    ceo_buy_map = _event_map(insiders[pd.to_numeric(insiders["is_ceo"], errors="coerce").eq(1)], "rcept_dt")
    one_year_ns = int(pd.Timedelta(days=365).value)

    contract_count = []
    material_contract_count_10 = []
    material_contract_count_20 = []
    contract_max_ratio = []
    insider_buy_count = []
    insider_buy_amount = []
    ceo_buy_count = []
    for code, snapshot_date in zip(snapshots["stock_code"], snapshots["snapshot_date"]):
        end = int(snapshot_date.value)
        start = end - one_year_ns

        dates, values = contract_map.get(str(code), ([], []))
        left, right = bisect_left(dates, start), bisect_right(dates, end)
        selected = values[left:right]
        contract_count.append(len(selected))
        material_contract_count_10.append(sum(float(value) >= 10 for value in selected))
        material_contract_count_20.append(sum(float(value) >= 20 for value in selected))
        contract_max_ratio.append(max(selected) if selected else 0.0)

        dates, values = insider_map.get(str(code), ([], []))
        left, right = bisect_left(dates, start), bisect_right(dates, end)
        selected = values[left:right]
        insider_buy_count.append(len(selected))
        insider_buy_amount.append(sum(selected))

        dates, _ = ceo_buy_map.get(str(code), ([], []))
        ceo_buy_count.append(bisect_right(dates, end) - bisect_left(dates, start))

    snapshots["contract_count_1y"] = contract_count
    snapshots["material_contract_count_10_1y"] = material_contract_count_10
    snapshots["material_contract_count_20_1y"] = material_contract_count_20
    snapshots["contract_max_ratio_1y"] = contract_max_ratio
    snapshots["insider_buy_count_1y"] = insider_buy_count
    snapshots["insider_buy_amount_1y"] = insider_buy_amount
    snapshots["ceo_buy_count_1y"] = ceo_buy_count
    return snapshots


def _prepare_dataset() -> tuple[pd.DataFrame, dict]:
    quarters = scoreboard._load_point_in_time_earnings()
    annuals = scoreboard._load_annual_business_breakouts()
    snapshots = scoreboard._load_snapshots()
    snapshots = scoreboard._attach_earnings(snapshots, quarters)
    snapshots = scoreboard._attach_dilution(snapshots)
    snapshots = scoreboard._attach_outcome_quality(snapshots, quarters, annuals)
    snapshots = snapshots[snapshots["label_eligible"]].copy()
    snapshots = _attach_trailing_event_features(snapshots)

    target_codes, excluded_codes = _load_target_codes()
    snapshots["durable_tenbagger_24m"] = (
        snapshots["validated_tenbagger_24m"].eq(1)
        & snapshots["stock_code"].astype(str).isin(target_codes)
    ).astype(int)
    ambiguous = snapshots["validated_tenbagger_24m"].eq(1) & snapshots[
        "stock_code"
    ].astype(str).isin(excluded_codes)
    unknown_cause_codes = snapshots["validated_tenbagger_24m"].eq(1) & ~snapshots[
        "stock_code"
    ].astype(str).isin(target_codes | excluded_codes)
    ambiguous |= unknown_cause_codes
    snapshots = snapshots[~ambiguous].copy()

    snapshots["op_growth_clean"] = snapshots["op_growth"].where(
        snapshots["op_growth"].between(-5, 10)
    )
    snapshots["rev_yoy_clean"] = snapshots["rev_yoy"].where(
        snapshots["rev_yoy"].between(-0.95, 5)
    )
    coverage = {
        column: round(float(snapshots[column].notna().mean()) * 100, 2)
        for column in (
            "market_cap_억", "pbr", "ret_20d", "ret_60d", "ret_120d",
            "dist_high_252", "vol_ratio_20d", "avg_turnover_20d_억",
            "supply_20d_억", "rev_yoy_clean", "op_growth_clean",
            "dilution_count_1y", "dilution_max_pct_1y",
            "dilution_amount_to_market_cap",
            "ceo_buy_count_1y",
        )
    }
    return snapshots, coverage


def _condition(feature: str, operator: str, value: float | bool) -> dict:
    return {"feature": feature, "operator": operator, "value": value}


def _condition_name(condition: dict) -> str:
    value = condition["value"]
    if isinstance(value, bool):
        value = str(value).lower()
    return f"{condition['feature']} {condition['operator']} {value}"


def _apply_condition(frame: pd.DataFrame, condition: dict) -> pd.Series:
    series = frame[condition["feature"]]
    value = condition["value"]
    if condition["operator"] == ">=":
        return series.notna() & series.ge(value)
    if condition["operator"] == "<=":
        return series.notna() & series.le(value)
    if condition["operator"] == "==":
        return series.eq(value)
    raise ValueError(f"unsupported operator: {condition['operator']}")


def _candidate_conditions() -> list[dict]:
    candidates = []
    grids = {
        "market_cap_억": ("<=", [500, 1000, 1500, 3000, 5000]),
        "pbr": ("<=", [0.5, 0.8, 1.2, 2.0]),
        "ret_20d": (">=", [-0.10, 0.0, 0.10, 0.20]),
        "ret_60d": (">=", [-0.20, 0.0, 0.20, 0.40]),
        "ret_120d": (">=", [-0.30, 0.0, 0.30, 0.60]),
        "dist_high_252": ("<=", [-0.20, -0.35, -0.50, -0.65]),
        "dist_low_252": (">=", [0.10, 0.30, 0.60, 1.00]),
        "vol_ratio_20d": (">=", [1.0, 1.5, 2.0, 3.0]),
        "avg_turnover_20d_억": (">=", [1, 3, 5, 10]),
        "supply_20d_억": (">=", [0, 3, 10, 30]),
        "rev_yoy_clean": (">=", [0.10, 0.20, 0.30, 0.50]),
        "op_growth_clean": (">=", [0.20, 0.50, 1.00, 2.00]),
        "dilution_count_1y": ("<=", [0, 1, 2]),
        "dilution_max_pct_1y": ("<=", [5, 10, 20]),
        "dilution_amount_to_market_cap": ("<=", [0.05, 0.10, 0.20]),
        "contract_count_1y": (">=", [1, 2]),
        "material_contract_count_10_1y": (">=", [1, 2]),
        "material_contract_count_20_1y": (">=", [1, 2]),
        "contract_max_ratio_1y": (">=", [10, 20, 50]),
        "insider_buy_count_1y": (">=", [1, 2]),
        "ceo_buy_count_1y": (">=", [1, 2]),
    }
    for feature, (operator, values) in grids.items():
        candidates.extend(_condition(feature, operator, value) for value in values)
    candidates.extend(
        [
            _condition("earnings_backed", "==", True),
            _condition("op_turnaround", "==", True),
        ]
    )
    return candidates


def _metrics(frame: pd.DataFrame, mask: pd.Series) -> dict:
    selected = frame[mask]
    positives = int(selected["durable_tenbagger_24m"].sum())
    base_rate = float(frame["durable_tenbagger_24m"].mean())
    precision = positives / len(selected) if len(selected) else 0.0
    winner_codes = set(
        frame.loc[frame["durable_tenbagger_24m"].eq(1), "stock_code"].astype(str)
    )
    selected_winners = set(
        selected.loc[selected["durable_tenbagger_24m"].eq(1), "stock_code"].astype(str)
    )
    winner_rows = selected[selected["durable_tenbagger_24m"].eq(1)]
    winner_sectors = set(winner_rows["sector_large"].dropna().astype(str))
    sector_counts = winner_rows.drop_duplicates("stock_code")["sector_large"].value_counts()
    selected_codes = set(selected["stock_code"].astype(str))
    return {
        "rows": int(len(selected)),
        "stocks": len(selected_codes),
        "positive_rows": positives,
        "positive_stocks": len(selected_winners),
        "winner_sectors": len(winner_sectors),
        "top_winner_sector_share_pct": round(
            float(sector_counts.iloc[0]) / len(selected_winners) * 100, 2
        ) if len(selected_winners) and not sector_counts.empty else 0.0,
        "precision_pct": round(precision * 100, 4),
        "lift": round(precision / base_rate, 3) if base_rate else None,
        "winner_stock_recall_pct": round(
            len(selected_winners) / len(winner_codes) * 100, 2
        ) if winner_codes else 0.0,
        "selected_stock_success_pct": round(
            len(selected_winners) / len(selected_codes) * 100, 3
        ) if selected_codes else 0.0,
        "hit_3x_pct": round(float(selected["label_3x_24m"].mean()) * 100, 2)
        if len(selected) else 0.0,
        "hit_5x_pct": round(float(selected["label_5x_24m"].mean()) * 100, 2)
        if len(selected) else 0.0,
        "median_peak_return_pct": round(
            float(selected["forward_max_ret_24m"].median()) * 100, 1
        ) if len(selected) else None,
        "median_max_loss_pct": round(
            float(selected["forward_min_ret_24m"].median()) * 100, 1
        ) if len(selected) and selected["forward_min_ret_24m"].notna().any() else None,
        "median_pre_peak_loss_pct": round(
            float(selected["pre_peak_min_ret_24m"].median()) * 100, 1
        ) if len(selected) and selected["pre_peak_min_ret_24m"].notna().any() else None,
        "median_payoff_to_pain": round(
            float(selected["payoff_to_pain_24m"].clip(upper=99).median()), 2
        ) if len(selected) and selected["payoff_to_pain_24m"].notna().any() else None,
    }


def _first_alert_metrics(frame: pd.DataFrame, mask: pd.Series) -> dict:
    selected = frame[mask].sort_values(["stock_code", "snapshot_date"])
    alerts = selected.drop_duplicates("stock_code", keep="first")
    winners = alerts[alerts["durable_tenbagger_24m"].eq(1)]
    count = len(alerts)
    successes = int(winners["stock_code"].nunique())
    precision = successes / count if count else 0.0
    if count:
        z = 1.96
        denominator = 1 + z * z / count
        center = (precision + z * z / (2 * count)) / denominator
        margin = z * math.sqrt(
            precision * (1 - precision) / count + z * z / (4 * count * count)
        ) / denominator
        confidence_interval = [round(max(0.0, center - margin) * 100, 2), round(min(1.0, center + margin) * 100, 2)]
    else:
        confidence_interval = [0.0, 0.0]
    return {
        "alerts": count,
        "winner_stocks": successes,
        "precision_pct": round(precision * 100, 2),
        "precision_95ci_pct": confidence_interval,
        "winner_sectors": int(winners["sector_large"].dropna().nunique()),
        "hit_3x_pct": round(float(alerts["label_3x_24m"].mean()) * 100, 2) if count else 0.0,
        "hit_5x_pct": round(float(alerts["label_5x_24m"].mean()) * 100, 2) if count else 0.0,
        "median_peak_return_pct": round(float(alerts["forward_max_ret_24m"].median()) * 100, 1) if count else None,
        "median_max_loss_pct": round(float(alerts["forward_min_ret_24m"].median()) * 100, 1)
        if count and alerts["forward_min_ret_24m"].notna().any() else None,
        "median_pre_peak_loss_pct": round(float(alerts["pre_peak_min_ret_24m"].median()) * 100, 1)
        if count and alerts["pre_peak_min_ret_24m"].notna().any() else None,
        "median_payoff_to_pain": round(float(alerts["payoff_to_pain_24m"].clip(upper=99).median()), 2)
        if count and alerts["payoff_to_pain_24m"].notna().any() else None,
        "median_days_to_3x": round(float(alerts["days_to_3x_24m"].median()), 0)
        if count and alerts["days_to_3x_24m"].notna().any() else None,
        "median_days_to_5x": round(float(alerts["days_to_5x_24m"].median()), 0)
        if count and alerts["days_to_5x_24m"].notna().any() else None,
        "median_days_to_10x": round(float(alerts["days_to_10x_24m"].median()), 0)
        if count and alerts["days_to_10x_24m"].notna().any() else None,
    }


def _precision_tier(train: pd.DataFrame, validation: pd.DataFrame, holdout: pd.DataFrame) -> dict:
    # This rule is selected for cross-sector validation stability rather than the
    # highest observed holdout precision. Holdout never participates in selection.
    conditions = [
        _condition("contract_count_1y", ">=", 2),
        _condition("avg_turnover_20d_억", ">=", 10),
        _condition("op_growth_clean", ">=", 0.5),
    ]

    def evaluate(frame: pd.DataFrame) -> dict:
        mask = _rule_mask(frame, conditions)
        return {
            "row_metrics": _metrics(frame, mask),
            "first_alert_metrics": _first_alert_metrics(frame, mask),
        }

    result = {
        "name": "stable_precision_tier",
        "conditions": conditions,
        "selection_policy": "학습에서 생성, 2023 검증에서 양성 4종목·2업종 확인 후 2024 홀드아웃 1회 평가",
        "train": evaluate(train),
        "validation": evaluate(validation),
        "holdout": evaluate(holdout),
    }
    holdout_alerts = result["holdout"]["first_alert_metrics"]
    result["precision_target_pct"] = 15.0
    result["precision_target_pass"] = holdout_alerts["precision_pct"] >= 15.0
    result["decision"] = (
        "research_candidate_only"
        if not result["precision_target_pass"]
        else "independent_revalidation_required"
    )
    return result


def _rule_mask(frame: pd.DataFrame, conditions: list[dict]) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    for condition in conditions:
        mask &= _apply_condition(frame, condition)
    return mask


def _score_train(metrics: dict) -> float:
    lift = float(metrics.get("lift") or 0)
    recall = float(metrics.get("winner_stock_recall_pct") or 0) / 100
    return lift * math.sqrt(max(recall, 0.001))


def _is_train_eligible(metrics: dict) -> bool:
    return (
        metrics["rows"] >= 350
        and metrics["positive_stocks"] >= 4
        and metrics["winner_sectors"] >= 2
        and (metrics["lift"] or 0) >= 1.10
    )


def _is_stable(train_metrics: dict, validation_metrics: dict) -> bool:
    train_lift = float(train_metrics.get("lift") or 0)
    validation_lift = float(validation_metrics.get("lift") or 0)
    return (
        validation_metrics["rows"] >= 150
        and validation_metrics["positive_stocks"] >= 3
        and validation_metrics["winner_sectors"] >= 2
        and validation_lift >= 1.20
        and validation_lift >= train_lift * 0.50
    )


def _serialize_rule(conditions: list[dict], train: dict, validation: dict | None = None) -> dict:
    item = {
        "name": " AND ".join(_condition_name(condition) for condition in conditions),
        "conditions": conditions,
        "train": train,
        "train_score": round(_score_train(train), 4),
    }
    if validation is not None:
        item["validation"] = validation
        item["stable"] = _is_stable(train, validation)
    return item


def _evaluate_holdout(stable: list[dict], holdout: pd.DataFrame) -> list[dict]:
    confirmed = []
    for item in stable:
        metrics = _metrics(holdout, _rule_mask(holdout, item["conditions"]))
        first_alert_metrics = _first_alert_metrics(
            holdout, _rule_mask(holdout, item["conditions"])
        )
        item["holdout"] = metrics
        item["holdout_first_alert"] = first_alert_metrics
        item["holdout_pass"] = bool(
            metrics["rows"] >= 50
            and metrics["positive_stocks"] >= 2
            and metrics["winner_sectors"] >= 2
            and float(metrics.get("lift") or 0) >= 1.20
            and first_alert_metrics["alerts"] >= 50
            and first_alert_metrics["winner_stocks"] >= 2
            and first_alert_metrics["winner_sectors"] >= 2
        )
        if item["holdout_pass"]:
            confirmed.append(item)
    confirmed.sort(
        key=lambda item: (
            item["holdout_first_alert"]["precision_pct"],
            item["holdout_first_alert"]["hit_5x_pct"],
            item["holdout"]["lift"],
        ),
        reverse=True,
    )
    return confirmed


def _discover(train: pd.DataFrame, validation: pd.DataFrame) -> tuple[list[dict], list[dict]]:
    univariate = []
    for condition in _candidate_conditions():
        mask = _rule_mask(train, [condition])
        metrics = _metrics(train, mask)
        if _is_train_eligible(metrics):
            item = _serialize_rule([condition], metrics)
            item["train_first_alert"] = _first_alert_metrics(train, mask)
            univariate.append(item)
    univariate.sort(key=lambda item: item["train_score"], reverse=True)

    # Pair only the strongest train-selected conditions and disallow duplicate
    # feature pairs. Validation never participates in rule generation.
    pair_pool = univariate[:24]
    pairs = []
    seen = set()
    for left_index, left in enumerate(pair_pool):
        for right in pair_pool[left_index + 1:]:
            conditions = left["conditions"] + right["conditions"]
            if len({condition["feature"] for condition in conditions}) < 2:
                continue
            name = tuple(sorted(_condition_name(condition) for condition in conditions))
            if name in seen:
                continue
            seen.add(name)
            mask = _rule_mask(train, conditions)
            metrics = _metrics(train, mask)
            if _is_train_eligible(metrics):
                item = _serialize_rule(conditions, metrics)
                item["train_first_alert"] = _first_alert_metrics(train, mask)
                pairs.append(item)
    pairs.sort(key=lambda item: item["train_score"], reverse=True)

    train_finalists = sorted(univariate[:15] + pairs[:35], key=lambda item: item["train_score"], reverse=True)
    validated = []
    for item in train_finalists:
        validation_metrics = _metrics(validation, _rule_mask(validation, item["conditions"]))
        validated_item = _serialize_rule(item["conditions"], item["train"], validation_metrics)
        validated_item["train_first_alert"] = item["train_first_alert"]
        validated_item["validation_first_alert"] = _first_alert_metrics(
            validation, _rule_mask(validation, item["conditions"])
        )
        validated_item["first_alert_stable"] = bool(
            validated_item["stable"]
            and validated_item["validation_first_alert"]["alerts"] >= 50
            and validated_item["validation_first_alert"]["winner_stocks"] >= 2
            and validated_item["validation_first_alert"]["winner_sectors"] >= 2
        )
        validated.append(validated_item)
    stable = [item for item in validated if item["first_alert_stable"]]
    stable.sort(
        key=lambda item: (
            item["validation"]["lift"],
            item["validation"]["winner_stock_recall_pct"],
        ),
        reverse=True,
    )
    return validated, stable


def _write_report(
    snapshots: pd.DataFrame,
    train: pd.DataFrame,
    validation: pd.DataFrame,
    holdout: pd.DataFrame,
    coverage: dict,
    finalists: list[dict],
    stable: list[dict],
    confirmed: list[dict],
) -> dict:
    baseline_train = _metrics(train, pd.Series(True, index=train.index))
    baseline_validation = _metrics(validation, pd.Series(True, index=validation.index))
    baseline_holdout = _metrics(holdout, pd.Series(True, index=holdout.index))
    baseline_first_alert = {
        "train": _first_alert_metrics(train, pd.Series(True, index=train.index)),
        "validation": _first_alert_metrics(validation, pd.Series(True, index=validation.index)),
        "holdout": _first_alert_metrics(holdout, pd.Series(True, index=holdout.index)),
    }
    current_score = [_condition("heuristic_score", ">=", 55)]
    current_train = _metrics(train, _rule_mask(train, current_score))
    current_validation = _metrics(validation, _rule_mask(validation, current_score))
    current_holdout = _metrics(holdout, _rule_mask(holdout, current_score))
    precision_tier = _precision_tier(train, validation, holdout)
    precision_pass = precision_tier["precision_target_pass"]
    conclusion = (
        "holdout_confirmed_and_precision_target_passed"
        if confirmed and precision_pass
        else "holdout_signal_but_precision_insufficient"
        if confirmed
        else "no_holdout_confirmed_signal"
    )
    recommended_names = {
        "earnings_acceleration": "op_growth_clean >= 1.0",
        "demand_confirmation": "supply_20d_억 >= 10 AND rev_yoy_clean >= 0.2",
        "earnings_demand": "supply_20d_억 >= 10 AND op_growth_clean >= 1.0",
        "liquid_earnings": "avg_turnover_20d_억 >= 10 AND op_growth_clean >= 1.0",
        "turnaround_demand": "supply_20d_억 >= 3 AND op_turnaround == true",
    }
    confirmed_by_name = {item["name"]: item for item in confirmed}
    recommended = {
        key: confirmed_by_name[name]
        for key, name in recommended_names.items()
        if name in confirmed_by_name
    }
    split_map = {
        "train_first_alert": "train",
        "validation_first_alert": "validation",
        "holdout_first_alert": "holdout",
    }
    risk_adjusted = []
    for item in confirmed:
        if all(
            item[signal_split]["median_payoff_to_pain"]
            >= baseline_first_alert[baseline_split]["median_payoff_to_pain"]
            and item[signal_split]["median_pre_peak_loss_pct"]
            >= baseline_first_alert[baseline_split]["median_pre_peak_loss_pct"]
            for signal_split, baseline_split in split_map.items()
        ):
            risk_adjusted.append(item)
    risk_adjusted.sort(
        key=lambda item: (
            item["holdout_first_alert"]["precision_pct"],
            item["holdout_first_alert"]["median_payoff_to_pain"],
        ),
        reverse=True,
    )
    payload = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "conclusion": conclusion,
        "production_ready": False,
        "auto_trading_allowed": False,
        "methodology": {
            "target": "정상 가격열·지속형·사업원인 확정 24개월 10배",
            "inputs": "스냅샷 시점에 공개된 가격·밸류·수급·실적·수주·내부자·희석 변수만 사용",
            "excluded_inputs": ["model_score_6m", "model_score_12m", "미래 실적", "사후 원인 분류"],
            "train": "2020-01-01~2022-12-31",
            "validation": "2023-01-01~2023-12-31",
            "final_evaluation": "2024-01-01~2024-07-31 (reused research holdout; not untouched)",
            "search": "도메인 고정 임계값 단일 조건 후 train 상위 조건의 2개 조합만 생성",
            "stable_gate": "validation 150행·행승자 3종목·2섹터, 최초알림 승자 2종목·2섹터, lift 1.2 이상",
            "holdout_gate": "2024 평가 50행, 최초알림 승자 2종목·2섹터, 행 lift 1.2 이상",
            "precision_gate": "종목별 최초 알림 기준 텐버거 적중률 15% 이상",
            "snapshot_table": scoreboard._snapshot_table(),
        },
        "data": {
            "rows": len(snapshots),
            "stocks": int(snapshots["stock_code"].nunique()),
            "durable_positive_rows": int(snapshots["durable_tenbagger_24m"].sum()),
            "durable_positive_stocks": int(
                snapshots.loc[snapshots["durable_tenbagger_24m"].eq(1), "stock_code"].nunique()
            ),
            "coverage_pct": coverage,
        },
        "baseline": {"train": baseline_train, "validation": baseline_validation, "holdout": baseline_holdout},
        "baseline_first_alert": baseline_first_alert,
        "current_score_55": {"train": current_train, "validation": current_validation, "holdout": current_holdout},
        "validation_tested_finalists": finalists,
        "stable_signals": stable,
        "holdout_confirmed_signals": confirmed,
        "recommended_signal_families": recommended,
        "aggregate_risk_adjusted_research_signals": risk_adjusted,
        "risk_adjusted_gate": (
            "aggregate split screen only: each train/validation/final-evaluation "
            "first-alert cohort must have "
            "payoff-to-pain >= cohort baseline and pre-peak loss no worse than baseline"
        ),
        "precision_tier": precision_tier,
    }
    tmp = OUT_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(OUT_JSON)

    lines = [
        "# 과거 사업형 텐버거 선행 시그널 탐색",
        "",
        f"- 결론: `{conclusion}`",
        f"- 전체 표본: {len(snapshots):,}행 / 지속형 양성 {payload['data']['durable_positive_rows']:,}행",
        f"- 기존 55점 검증 lift: {current_validation.get('lift')}x",
        f"- 2023 검증 통과 시그널: {len(stable)}개",
        f"- 2024 최종 홀드아웃 통과 시그널: {len(confirmed)}개",
        f"- 고정밀 티어 최초 알림 적중률: {precision_tier['holdout']['first_alert_metrics']['precision_pct']}%",
        f"- 고정밀 티어 판정: `{precision_tier['decision']}` (목표 {precision_tier['precision_target_pct']}%)",
        "- 연구 전용이며 자동매매 사용 금지",
        "",
        "## 검증 통과 규칙",
        "",
    ]
    if not confirmed:
        lines.append("- 현재 안정성 기준을 통과한 선행 시그널 없음")
    for item in confirmed[:15]:
        validation_metrics = item["holdout"]
        lines.append(
            f"- `{item['name']}`: 홀드아웃 lift {validation_metrics['lift']}x, "
            f"정밀도 {validation_metrics['precision_pct']}%, "
            f"승자 재현율 {validation_metrics['winner_stock_recall_pct']}%"
        )
    lines += ["", "## 채택할 신호 계열", ""]
    for key, item in recommended.items():
        metrics = item["holdout"]
        first_alert = item["holdout_first_alert"]
        lines.append(
            f"- `{key}` / `{item['name']}`: lift {metrics['lift']}x, "
            f"최초알림 10배 {first_alert['precision_pct']}%, "
            f"3배 {first_alert['hit_3x_pct']}%, 5배 {first_alert['hit_5x_pct']}%, "
            f"중앙 최대수익 {first_alert['median_peak_return_pct']}%"
        )
    lines += ["", "## 위험조정 통과 연구 신호", ""]
    if not risk_adjusted:
        lines.append("- 세 구간에서 수익/고통과 최고점 전 손실 기준을 모두 통과한 신호 없음")
    for item in risk_adjusted:
        first_alert = item["holdout_first_alert"]
        lines.append(
            f"- `{item['name']}`: 10배 {first_alert['precision_pct']}%, "
            f"3배 {first_alert['hit_3x_pct']}%, 5배 {first_alert['hit_5x_pct']}%, "
            f"최고점 전 중앙 손실 {first_alert['median_pre_peak_loss_pct']}%, "
            f"수익/고통 {first_alert['median_payoff_to_pain']}"
        )
    lines.append("- 탐색적 위험조정 분류이며 독립 미래 기간 검증 전에는 실전 승격 금지")
    precision_alert = precision_tier["holdout"]["first_alert_metrics"]
    lines += [
        "",
        "## 고정밀 연구 티어",
        "",
        f"- 조건: `{_condition_name(precision_tier['conditions'][0])} AND "
        f"{_condition_name(precision_tier['conditions'][1])} AND {_condition_name(precision_tier['conditions'][2])}`",
        f"- 종목별 최초 알림: {precision_alert['alerts']}건 / 텐버거 {precision_alert['winner_stocks']}건 / "
        f"적중률 {precision_alert['precision_pct']}% (95% CI {precision_alert['precision_95ci_pct'][0]}~{precision_alert['precision_95ci_pct'][1]}%)",
        f"- 3배 {precision_alert['hit_3x_pct']}% / 5배 {precision_alert['hit_5x_pct']}% / "
        f"중앙 최대수익 {precision_alert['median_peak_return_pct']}%",
        f"- 판정: `{precision_tier['decision']}`; 15% 정밀도 목표 미달이면 실전 후보로 승격하지 않는다.",
    ]
    lines += [
        "",
        "## 해석 제한",
        "",
        "- 원인 확정은 목표 정의에만 사용했고 입력 변수로 사용하지 않았다.",
        "- 상장폐지 종목을 PIT 유니버스에 포함했지만 2019년 이전 가격 커버리지는 불완전하다.",
        "- 검증 양성 수가 작아 통과 규칙도 독립 기간 추가 검증 전에는 실전 사용하지 않는다.",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    return payload


def main() -> None:
    snapshots, coverage = _prepare_dataset()
    train = snapshots[snapshots["snapshot_date"] <= pd.Timestamp("2022-12-31")].copy()
    validation = snapshots[
        snapshots["snapshot_date"].between(pd.Timestamp("2023-01-01"), pd.Timestamp("2023-12-31"))
    ].copy()
    holdout = snapshots[
        snapshots["snapshot_date"].between(pd.Timestamp("2024-01-01"), pd.Timestamp("2024-07-31"))
    ].copy()
    finalists, stable = _discover(train, validation)
    confirmed = _evaluate_holdout(stable, holdout)
    payload = _write_report(
        snapshots, train, validation, holdout, coverage, finalists, stable, confirmed
    )
    print(json.dumps({
        "conclusion": payload["conclusion"],
        "data": payload["data"],
        "baseline": payload["baseline"],
        "current_score_55": payload["current_score_55"],
        "stable_signals": payload["stable_signals"][:10],
        "holdout_confirmed_signals": payload["holdout_confirmed_signals"][:10],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-table", default="strategy_feature_snapshot")
    args = parser.parse_args()
    os.environ["TENBAGGER_SNAPSHOT_TABLE"] = args.snapshot_table
    main()
