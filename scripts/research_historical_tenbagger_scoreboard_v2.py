#!/usr/bin/env python3
from __future__ import annotations

import itertools
import json
import os
import re
from bisect import bisect_left, bisect_right
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy import bindparam, inspect, text

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research_outputs" / "historical_tenbagger_scoreboard_v2.json"

import sys

sys.path.insert(0, str(ROOT))
from database import engine  # noqa: E402


def _snapshot_table() -> str:
    table = os.getenv("TENBAGGER_SNAPSHOT_TABLE", "strategy_feature_snapshot").strip()
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
        raise ValueError(f"invalid TENBAGGER_SNAPSHOT_TABLE: {table}")
    return table


def _load_snapshots() -> pd.DataFrame:
    table = _snapshot_table()
    available_columns = {column["name"] for column in inspect(engine).get_columns(table)}
    optional_columns = (
        "forward_min_ret_24m", "pre_peak_min_ret_24m", "payoff_to_pain_24m",
        "days_to_3x_24m", "days_to_5x_24m", "days_to_10x_24m",
    )
    optional_select = ",\n               ".join(
        column if column in available_columns else f"NULL AS {column}"
        for column in optional_columns
    )
    frame = pd.read_sql_query(
        text(f"""
        SELECT snapshot_date, stock_code, stock_name, market, sector_large,
               close_price, market_cap_억, per, pbr, ret_20d, ret_60d,
               ret_120d, dist_high_252, dist_low_252, vol_ratio_20d,
               avg_turnover_20d_억, supply_20d_억, heuristic_score,
               model_score_12m, forward_max_ret_24m,
               label_3x_24m, label_5x_24m, label_10x_24m,
               {optional_select}
        FROM {table}
        WHERE snapshot_date >= '2020-01-01'
          AND forward_max_ret_24m IS NOT NULL
          AND label_10x_24m IS NOT NULL
        """),
        engine,
    )
    frame["snapshot_date"] = pd.to_datetime(frame["snapshot_date"], errors="coerce")
    return frame.dropna(subset=["snapshot_date", "stock_code"]).copy()


def _load_point_in_time_earnings() -> pd.DataFrame:
    financials = pd.read_sql_query(
        """
        WITH ranked AS (
            SELECT stock_code, year, quarter, revenue, operating_profit, net_income,
                   report_type, data_source, id,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code, year, quarter
                       ORDER BY CASE WHEN report_type='CFS' THEN 0 ELSE 1 END,
                                CASE WHEN LOWER(data_source) >= 'fnguide' AND LOWER(data_source) < 'fnguidf' THEN 0
                                     WHEN LOWER(data_source) >= 'dart' AND LOWER(data_source) < 'daru' THEN 1 ELSE 2 END,
                                id DESC
                   ) AS rn
            FROM financial_data
            WHERE is_annual IS FALSE AND quarter BETWEEN 1 AND 4
        )
        SELECT stock_code, year, quarter, revenue, operating_profit, net_income
        FROM ranked WHERE rn=1
        """,
        engine,
    )
    if financials.empty:
        return financials
    financials = financials.sort_values(["stock_code", "quarter", "year"])
    prior = financials[["stock_code", "year", "quarter", "revenue", "operating_profit"]].copy()
    prior["year"] += 1
    prior = prior.rename(columns={"revenue": "revenue_prev", "operating_profit": "op_prev"})
    financials = financials.merge(prior, on=["stock_code", "year", "quarter"], how="left")
    financials["rev_yoy"] = np.where(
        financials["revenue_prev"] > 0,
        financials["revenue"] / financials["revenue_prev"] - 1.0,
        np.nan,
    )
    financials["op_turnaround"] = (
        (financials["op_prev"].fillna(0) <= 0) & (financials["operating_profit"].fillna(0) > 0)
    )
    financials["op_growth"] = np.where(
        financials["op_prev"] > 0,
        financials["operating_profit"] / financials["op_prev"] - 1.0,
        np.nan,
    )
    financials["earnings_backed"] = (
        (financials["operating_profit"].fillna(0) > 0)
        & (
            (financials["rev_yoy"].fillna(-99) >= 0.10)
            | (financials["op_growth"].fillna(-99) >= 0.20)
            | financials["op_turnaround"]
        )
    )
    month = financials["quarter"].map({1: 5, 2: 8, 3: 11, 4: 3})
    release_year = financials["year"] + (financials["quarter"] == 4).astype(int)
    financials["available_date"] = pd.to_datetime(
        {"year": release_year, "month": month, "day": 15}, errors="coerce"
    )
    return financials[
        ["stock_code", "available_date", "earnings_backed", "rev_yoy", "op_growth", "op_turnaround"]
    ].sort_values(["available_date", "stock_code"])


def _load_annual_business_breakouts() -> pd.DataFrame:
    annuals = pd.read_sql_query(
        """
        WITH ranked AS (
            SELECT stock_code, year, revenue, operating_profit, net_income,
                   ROW_NUMBER() OVER (
                       PARTITION BY stock_code, year
                       ORDER BY CASE WHEN report_type='CFS' THEN 0 ELSE 1 END,
                                CASE WHEN LOWER(data_source) >= 'fnguide' AND LOWER(data_source) < 'fnguidf' THEN 0
                                     WHEN LOWER(data_source) >= 'dart' AND LOWER(data_source) < 'daru' THEN 1 ELSE 2 END,
                                id DESC
                   ) AS rn
            FROM financial_data
            WHERE is_annual IS TRUE
        )
        SELECT stock_code, year, revenue, operating_profit, net_income
        FROM ranked WHERE rn=1
        """,
        engine,
    )
    if annuals.empty:
        return annuals
    prior = annuals[["stock_code", "year", "revenue", "operating_profit"]].copy()
    prior["year"] += 1
    prior = prior.rename(columns={"revenue": "revenue_prev", "operating_profit": "op_prev"})
    annuals = annuals.merge(prior, on=["stock_code", "year"], how="left")
    annuals["rev_yoy"] = np.where(
        annuals["revenue_prev"] > 0,
        annuals["revenue"] / annuals["revenue_prev"] - 1.0,
        np.nan,
    )
    annuals["op_growth"] = np.where(
        annuals["op_prev"] > 0,
        annuals["operating_profit"] / annuals["op_prev"] - 1.0,
        np.nan,
    )
    annuals["op_turnaround"] = (
        (annuals["op_prev"].fillna(0) <= 0) & (annuals["operating_profit"].fillna(0) > 0)
    )
    annuals["business_breakout"] = (
        (annuals["revenue_prev"].fillna(0) > 0)
        & (annuals["rev_yoy"].fillna(-99) >= 0.15)
        & (annuals["rev_yoy"].fillna(99) <= 5.0)
        & ((annuals["revenue"].fillna(0) - annuals["revenue_prev"].fillna(0)) >= 5_000_000_000)
        & (annuals["operating_profit"].fillna(0) > 0)
        & ((annuals["op_growth"].fillna(-99) >= 0.20) | annuals["op_turnaround"])
    )
    annuals["available_date"] = pd.to_datetime(
        {"year": annuals["year"] + 1, "month": 3, "day": 31}, errors="coerce"
    )
    return annuals[["stock_code", "available_date", "business_breakout"]]


def _attach_earnings(snapshots: pd.DataFrame, financials: pd.DataFrame) -> pd.DataFrame:
    if financials.empty:
        snapshots["earnings_backed"] = False
        return snapshots
    left = snapshots.sort_values(["snapshot_date", "stock_code"])
    right = financials.sort_values(["available_date", "stock_code"])
    merged = pd.merge_asof(
        left,
        right,
        left_on="snapshot_date",
        right_on="available_date",
        by="stock_code",
        direction="backward",
        allow_exact_matches=True,
    )
    merged["earnings_backed"] = merged["earnings_backed"].eq(True)
    return merged


def _attach_dilution(snapshots: pd.DataFrame) -> pd.DataFrame:
    events = pd.read_sql_query(
        text("""
        SELECT stock_code, disclosed_at, event_type, issue_amount, dilution_pct,
               rcept_no, report_nm
        FROM dilution_events
        WHERE event_type IN ('CB','BW','EB','RIGHTS','RIGHTS_BONUS','유상증자')
          AND COALESCE(risk_amount_status, 'amount_confirmed') != 'not_amount_applicable'
          AND COALESCE(report_nm, '') NOT LIKE '%정정%'
        """),
        engine,
    )
    events["disclosed_at"] = pd.to_datetime(events["disclosed_at"], format="mixed", errors="coerce")
    events = events.dropna(subset=["stock_code", "disclosed_at"]).drop_duplicates(
        ["stock_code", "rcept_no", "event_type", "disclosed_at"]
    )
    event_map = {}
    for code, group in events.groupby("stock_code"):
        ordered = group.sort_values("disclosed_at")
        event_map[str(code)] = (
            ordered["disclosed_at"].astype("int64").tolist(),
            pd.to_numeric(ordered["issue_amount"], errors="coerce").fillna(0).tolist(),
            pd.to_numeric(ordered["dilution_pct"], errors="coerce").fillna(0).tolist(),
        )
    counts = []
    amounts = []
    max_pcts = []
    amount_to_market_cap = []
    one_year_ns = int(pd.Timedelta(days=365).value)
    for code, snapshot_date, market_cap in zip(
        snapshots["stock_code"], snapshots["snapshot_date"], snapshots["market_cap_억"]
    ):
        dates, issue_amounts, dilution_pcts = event_map.get(str(code), ([], [], []))
        end = int(snapshot_date.value)
        left = bisect_left(dates, end - one_year_ns)
        right = bisect_right(dates, end)
        selected_amounts = issue_amounts[left:right]
        selected_pcts = dilution_pcts[left:right]
        amount = float(sum(selected_amounts))
        counts.append(right - left)
        amounts.append(amount)
        max_pcts.append(float(max(selected_pcts, default=0)))
        cap_krw = float(market_cap or 0) * 100_000_000
        amount_to_market_cap.append(amount / cap_krw if cap_krw > 0 else np.nan)
    snapshots["dilution_count_1y"] = counts
    snapshots["dilution_amount_1y"] = amounts
    snapshots["dilution_max_pct_1y"] = max_pcts
    snapshots["dilution_amount_to_market_cap"] = amount_to_market_cap
    return snapshots


def _attach_outcome_quality(
    snapshots: pd.DataFrame,
    financials: pd.DataFrame,
    annual_breakouts: pd.DataFrame,
) -> pd.DataFrame:
    """Separate durable business-backed winners from transient 10x price prints."""
    snapshots = snapshots.copy()
    snapshots["future_earnings_quarters"] = 0
    snapshots["future_annual_breakouts"] = 0
    snapshots["sustained_3x_months"] = 0
    snapshots["price_artifact_jumps_24m"] = 0
    snapshots["label_eligible"] = True

    earnings_map: dict[str, tuple[list[int], list[int]]] = {}
    if not financials.empty:
        for code, group in financials.groupby("stock_code"):
            ordered = group.dropna(subset=["available_date"]).sort_values("available_date")
            dates = ordered["available_date"].astype("int64").tolist()
            flags = ordered["earnings_backed"].astype(int).tolist()
            prefix = [0]
            for flag in flags:
                prefix.append(prefix[-1] + flag)
            earnings_map[str(code)] = (dates, prefix)

    annual_map: dict[str, tuple[list[int], list[int]]] = {}
    if not annual_breakouts.empty:
        for code, group in annual_breakouts.groupby("stock_code"):
            ordered = group.dropna(subset=["available_date"]).sort_values("available_date")
            dates = ordered["available_date"].astype("int64").tolist()
            prefix = [0]
            for flag in ordered["business_breakout"].astype(int).tolist():
                prefix.append(prefix[-1] + flag)
            annual_map[str(code)] = (dates, prefix)

    positive_indexes = snapshots.index[snapshots["label_10x_24m"] == 1].tolist()
    price_timeline = pd.read_sql_query(
        text(f"""
        SELECT snapshot_date, stock_code, close_price
        FROM {_snapshot_table()}
        WHERE snapshot_date >= '2020-01-01' AND close_price > 0
        """),
        engine,
    )
    price_timeline["snapshot_date"] = pd.to_datetime(price_timeline["snapshot_date"], errors="coerce")
    price_timeline = price_timeline.dropna(subset=["snapshot_date", "stock_code"])
    grouped_prices: dict[str, tuple[list[int], list[float]]] = {}
    for code, group in price_timeline.groupby("stock_code"):
        ordered = group.sort_values("snapshot_date")
        grouped_prices[str(code)] = (
            ordered["snapshot_date"].astype("int64").tolist(),
            ordered["close_price"].fillna(0).astype(float).tolist(),
        )

    # A KRX common stock cannot move more than the daily price limit. Larger
    # close-to-close jumps are mixed adjusted/raw series or an unadjusted
    # corporate action, so every outcome window containing one is unusable.
    codes = snapshots["stock_code"].astype(str).drop_duplicates().tolist()
    daily_sql = text(
        """
        WITH ordered AS (
            SELECT stock_code, date, close,
                   LAG(close) OVER (PARTITION BY stock_code ORDER BY date, id) AS previous_close
            FROM price_history
            WHERE stock_code IN :codes AND close > 0
        )
        SELECT stock_code, date
        FROM ordered
        WHERE date >= '2019-11-01'
          AND (close / NULLIF(previous_close, 0) > 1.45
               OR close / NULLIF(previous_close, 0) < 0.69)
        ORDER BY stock_code, date
        """
    ).bindparams(bindparam("codes", expanding=True))
    daily = pd.read_sql_query(daily_sql, engine, params={"codes": codes})
    daily["date"] = pd.to_datetime(daily["date"], errors="coerce")
    daily = daily.dropna(subset=["date", "stock_code"])
    anomaly_map = {
        str(code): sorted(group["date"].astype("int64").tolist())
        for code, group in daily.groupby("stock_code")
    }

    horizon_ns = int(pd.Timedelta(days=730).value)
    lookback_ns = int(pd.Timedelta(days=35).value)
    for idx, row in snapshots.iterrows():
        code = str(row["stock_code"])
        start = int(row["snapshot_date"].value)
        end = start + horizon_ns
        anomaly_dates = anomaly_map.get(code, [])
        anomaly_count = bisect_right(anomaly_dates, end) - bisect_left(
            anomaly_dates, start - lookback_ns
        )
        snapshots.at[idx, "price_artifact_jumps_24m"] = anomaly_count
        snapshots.at[idx, "label_eligible"] = anomaly_count == 0

    for idx in positive_indexes:
        row = snapshots.loc[idx]
        code = str(row["stock_code"])
        start = int(row["snapshot_date"].value)
        end = start + horizon_ns

        earning_dates, earning_prefix = earnings_map.get(code, ([], [0]))
        earning_left = bisect_right(earning_dates, start)
        earning_right = bisect_right(earning_dates, end)
        snapshots.at[idx, "future_earnings_quarters"] = (
            earning_prefix[earning_right] - earning_prefix[earning_left]
        )
        annual_dates, annual_prefix = annual_map.get(code, ([], [0]))
        annual_left = bisect_right(annual_dates, start)
        annual_right = bisect_right(annual_dates, end)
        snapshots.at[idx, "future_annual_breakouts"] = (
            annual_prefix[annual_right] - annual_prefix[annual_left]
        )

        price_dates, prices = grouped_prices.get(code, ([], []))
        price_left = bisect_right(price_dates, start)
        price_right = bisect_right(price_dates, end)
        base_price = float(row.get("close_price") or 0)
        if base_price > 0:
            snapshots.at[idx, "sustained_3x_months"] = sum(
                1 for price in prices[price_left:price_right] if price >= base_price * 3.0
            )

    snapshots["validated_tenbagger_24m"] = (
        (snapshots["label_10x_24m"] == 1)
        & snapshots["label_eligible"]
        & (snapshots["future_annual_breakouts"] >= 1)
        & (snapshots["sustained_3x_months"] >= 3)
    ).astype(int)
    snapshots["price_artifact_10x_proxy"] = (
        (snapshots["label_10x_24m"] == 1) & ~snapshots["label_eligible"]
    ).astype(int)
    snapshots["issue_only_10x_proxy"] = (
        (snapshots["label_10x_24m"] == 1)
        & snapshots["label_eligible"]
        & (snapshots["validated_tenbagger_24m"] == 0)
    ).astype(int)
    return snapshots


def _metrics(frame: pd.DataFrame, mask: pd.Series, baseline_rate: float, winner_codes: set[str]) -> dict:
    picked = frame.loc[mask].copy()
    if picked.empty:
        return {"rows": 0}
    picked_codes = set(picked["stock_code"].astype(str))
    captured_winners = set(
        picked.loc[picked["validated_tenbagger_24m"] == 1, "stock_code"].astype(str)
    )
    precision = float(picked["validated_tenbagger_24m"].mean())
    return {
        "rows": int(len(picked)),
        "stocks": int(len(picked_codes)),
        "validated_tenbagger_rows": int(picked["validated_tenbagger_24m"].sum()),
        "raw_tenbagger_rows": int(picked["label_10x_24m"].sum()),
        "validated_precision_pct": round(precision * 100.0, 3),
        "raw_10x_precision_pct": round(float(picked["label_10x_24m"].mean()) * 100.0, 3),
        "validated_lift": round(precision / baseline_rate, 3) if baseline_rate else None,
        "winner_stock_recall_pct": round(len(captured_winners) / len(winner_codes) * 100.0, 2) if winner_codes else 0.0,
        "selected_stock_success_pct": round(len(captured_winners) / len(picked_codes) * 100.0, 2) if picked_codes else 0.0,
        "hit_3x_pct": round(float(picked["label_3x_24m"].mean()) * 100.0, 2),
        "hit_5x_pct": round(float(picked["label_5x_24m"].mean()) * 100.0, 2),
        "median_peak_pct": round(float(picked["forward_max_ret_24m"].median()) * 100.0, 1),
        "avg_peak_pct": round(float(picked["forward_max_ret_24m"].mean()) * 100.0, 1),
    }


def _mask(frame: pd.DataFrame, rule: dict) -> pd.Series:
    mask = pd.Series(True, index=frame.index)
    if rule["score"] is not None:
        mask &= frame["heuristic_score"].fillna(-1) >= rule["score"]
    if rule["turnover"] is not None:
        mask &= frame["avg_turnover_20d_억"].fillna(0) >= rule["turnover"]
    if rule["mcap"] is not None:
        mask &= frame["market_cap_억"].fillna(np.inf).between(100, rule["mcap"])
    if rule["pbr"] is not None:
        mask &= frame["pbr"].fillna(np.inf).between(0.01, rule["pbr"])
    if rule["drawdown"] is not None:
        mask &= frame["dist_high_252"].fillna(1) <= rule["drawdown"]
    if rule["earnings"]:
        mask &= frame["earnings_backed"]
    if rule["dilution"] is not None:
        mask &= frame["dilution_count_1y"] <= rule["dilution"]
    return mask


def _rule_name(rule: dict) -> str:
    return "|".join(f"{key}={value}" for key, value in rule.items())


def main() -> None:
    financials = _load_point_in_time_earnings()
    annual_breakouts = _load_annual_business_breakouts()
    snapshots = _load_snapshots()
    snapshots = _attach_earnings(snapshots, financials)
    snapshots = _attach_dilution(snapshots)
    snapshots = _attach_outcome_quality(snapshots, financials, annual_breakouts)
    all_snapshots = snapshots
    snapshots = snapshots[snapshots["label_eligible"]].copy()
    train = snapshots[snapshots["snapshot_date"] <= pd.Timestamp("2022-12-31")].copy()
    validation = snapshots[
        (snapshots["snapshot_date"] >= pd.Timestamp("2023-01-01"))
        & (snapshots["snapshot_date"] <= pd.Timestamp("2024-07-31"))
    ].copy()

    baseline = {
        "score": 55.0,
        "turnover": 3.0,
        "mcap": 3000.0,
        "pbr": None,
        "drawdown": None,
        "earnings": False,
        "dilution": None,
    }
    grid = []
    for values in itertools.product(
        [45.0, 55.0, 65.0],
        [1.0, 3.0, 5.0],
        [1500.0, 3000.0, 5000.0],
        [None, 0.8, 1.2],
        [None, -0.5, -0.6, -0.7],
        [False, True],
        [None, 0, 2],
    ):
        grid.append(dict(zip(baseline, values)))

    def period_context(frame: pd.DataFrame) -> tuple[float, set[str]]:
        return float(frame["validated_tenbagger_24m"].mean()), set(
            frame.loc[frame["validated_tenbagger_24m"] == 1, "stock_code"].astype(str)
        )

    train_base_rate, train_winners = period_context(train)
    val_base_rate, val_winners = period_context(validation)
    evaluated = []
    for rule in grid:
        train_metrics = _metrics(train, _mask(train, rule), train_base_rate, train_winners)
        if train_metrics.get("rows", 0) < 500 or train_metrics.get("validated_tenbagger_rows", 0) < 10:
            continue
        train_score = (
            float(train_metrics.get("validated_lift") or 0) * 0.55
            + float(train_metrics.get("winner_stock_recall_pct") or 0) / 100.0 * 0.45
        )
        evaluated.append({
            "name": _rule_name(rule),
            "rule": rule,
            "train_selection_score": round(train_score, 4),
            "train": train_metrics,
        })
    evaluated.sort(key=lambda item: item["train_selection_score"], reverse=True)

    finalists = evaluated[:30]
    coverage_finalists = sorted(
        [
            item for item in evaluated
            if (item["train"].get("validated_lift") or 0) >= 1.2
        ],
        key=lambda item: (
            item["train"].get("winner_stock_recall_pct") or 0,
            item["train"].get("validated_lift") or 0,
        ),
        reverse=True,
    )[:20]
    validation_candidates = {item["name"]: item for item in finalists + coverage_finalists}
    for item in validation_candidates.values():
        item["validation"] = _metrics(
            validation, _mask(validation, item["rule"]), val_base_rate, val_winners
        )
        item["stable"] = bool(
            (item["validation"].get("rows") or 0) >= 200
            and (item["validation"].get("validated_tenbagger_rows") or 0) >= 5
            and (item["validation"].get("validated_lift") or 0) >= 1.2
        )

    named_rules = {
        "baseline": baseline,
        "baseline_earnings": {**baseline, "earnings": True},
        "baseline_earnings_dilution2": {**baseline, "earnings": True, "dilution": 2},
        "small_value_earnings": {
            "score": 45.0, "turnover": 3.0, "mcap": 1500.0, "pbr": 1.2,
            "drawdown": None, "earnings": True, "dilution": 2,
        },
        "deep_drawdown_earnings": {
            "score": 45.0, "turnover": 3.0, "mcap": 3000.0, "pbr": None,
            "drawdown": -0.6, "earnings": True, "dilution": 2,
        },
    }
    comparisons = []
    for name, rule in named_rules.items():
        comparisons.append({
            "name": name,
            "rule": rule,
            "train": _metrics(train, _mask(train, rule), train_base_rate, train_winners),
            "validation": _metrics(validation, _mask(validation, rule), val_base_rate, val_winners),
        })

    report = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "decision": {
            "current_score": "rejected",
            "production_ready": False,
            "auto_trading_allowed": False,
            "reason": "기존 55점 기준이 train과 validation 모두에서 지속형 텐버거 기준 대비 lift 1 미만",
            "research_use_only": "안정 규칙도 낮은 절대 정밀도와 제한된 승자 재현율 때문에 추가 독립 검증 필요",
        },
        "methodology": {
            "target": "가격열 정상 + 24개월 내 10배 + 향후 연매출 15%/영업이익 개선 + 3배 가격 3개월 이상 유지",
            "raw_target": "24개월 내 월말 종가 대비 최고 종가 10배(label_10x_24m)",
            "outcome_separation": "당시 입력 신호와 사후 사업실체 검증을 분리; 사후 실적은 예측 입력에 사용하지 않음",
            "train": "2020-01-01~2022-12-31",
            "validation": "2023-01-01~2024-07-31 (24개월 라벨 완결 구간)",
            "selection": "규칙 탐색과 순위 결정은 train만 사용; validation은 사후 안정성 확인",
            "grain": "월말 종목 스냅샷; 종목 반복 왜곡을 보기 위해 distinct-stock 성공률/승자 recall 병기",
            "earnings_timing": "분기 발표 가능일을 Q1 5/15, Q2 8/15, Q3 11/15, Q4 다음해 3/15로 제한",
            "known_limitation": "현재 stock_universe 기반 데이터라 상장폐지 종목 survivorship bias 가능",
            "leakage_warning": "model_score_12m은 2024-06까지 학습되어 본 validation의 독립 모델 증거로 사용하지 않음",
            "price_quality_gate": "기준일 35일 전부터 24개월 후까지 일간 종가비율 1.45 초과 또는 0.69 미만이면 가격 아티팩트로 라벨 계산에서 제외",
        },
        "data_quality": {
            "rows": int(len(snapshots)),
            "stocks": int(snapshots["stock_code"].nunique()),
            "duplicate_snapshot_keys": int(snapshots.duplicated(["snapshot_date", "stock_code"]).sum()),
            "train_rows": int(len(train)),
            "validation_rows": int(len(validation)),
            "train_base_10x_pct": round(train_base_rate * 100.0, 3),
            "validation_base_10x_pct": round(val_base_rate * 100.0, 3),
            "raw_10x_rows": int(snapshots["label_10x_24m"].sum()),
            "validated_10x_rows": int(snapshots["validated_tenbagger_24m"].sum()),
            "issue_only_proxy_rows": int(snapshots["issue_only_10x_proxy"].sum()),
            "price_artifact_excluded_rows": int((~all_snapshots["label_eligible"]).sum()),
            "price_artifact_10x_rows": int(all_snapshots["price_artifact_10x_proxy"].sum()),
            "earnings_coverage_pct": round(float(snapshots["available_date"].notna().mean()) * 100.0, 2),
        },
        "named_comparisons": comparisons,
        "train_selected_finalists": finalists,
        "train_selected_coverage_finalists": coverage_finalists,
    }
    output_text = json.dumps(report, ensure_ascii=False, indent=2)
    tmp_path = OUT.with_suffix(f"{OUT.suffix}.tmp")
    tmp_path.write_text(output_text, encoding="utf-8")
    tmp_path.replace(OUT)
    print(json.dumps({
        "output": str(OUT),
        "data_quality": report["data_quality"],
        "named_comparisons": comparisons,
        "stable_finalists": [item for item in finalists if item["stable"]][:10],
        "coverage_finalists": coverage_finalists[:10],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
