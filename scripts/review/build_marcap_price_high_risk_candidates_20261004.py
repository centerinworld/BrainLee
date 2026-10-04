#!/usr/bin/env python3
"""marcap 대조 중 고위험 가격 차이를 적용 검토 후보로 축약한다.

전체 diff 218만 행을 그대로 적용 큐로 쓰면 잡음이 크다. 이 스크립트는 DART 없이
가능한 제3자 검증 확대 결과를 다음 두 형태로 줄인다.

1) `split_basis_ratio_candidate`, `price_value_mismatch` 행: marcap 값을 대체 후보로 보존
2) 모든 diff: 종목·연도·유형별 집계

운영 DB는 읽기 전용이다.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "price_accuracy_recheck_20261003"
MARCAP = ROOT / "data_cache" / "marcap"


def normalize_code(s: pd.Series) -> pd.Series:
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def neq_num(a: pd.Series, b: pd.Series, tol: float = 0.5) -> pd.Series:
    return (a.notna() & b.notna()) & ((a.astype(float) - b.astype(float)).abs() > tol)


def load_pg_year(conn, start: str, end: str) -> pd.DataFrame:
    rows = conn.execute(
        """
        SELECT stock_code, date, open, high, low, close, volume
        FROM price_history
        WHERE date BETWEEN ? AND ?
          AND stock_code ~ '^[0-9]{6}$'
        """,
        (start, end),
    ).fetchall()
    cols = ["stock_code", "date", "pg_open", "pg_high", "pg_low", "pg_close", "pg_volume"]
    df = pd.DataFrame([tuple(r) for r in rows], columns=cols)
    if df.empty:
        return df
    df["stock_code"] = normalize_code(df["stock_code"])
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    for c in ["pg_open", "pg_high", "pg_low", "pg_close", "pg_volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def classify_diff(row: pd.Series) -> str:
    if row["_merge"] == "left_only":
        return "pg_missing_marcap_row"
    if row["_merge"] == "right_only":
        return "pg_extra_not_in_marcap"
    if any(row.get(f"{side}_open", 0) == 0 for side in ["pg", "marcap"]) and row.get("pg_close") == row.get("marcap_close"):
        return "zero_ohlc_policy_review"
    close_ratio = None
    if row.get("marcap_close") not in (0, None) and not pd.isna(row.get("marcap_close")):
        close_ratio = row.get("pg_close") / row.get("marcap_close")
    if close_ratio and (abs(close_ratio - 0.1) < 0.01 or abs(close_ratio - 10) < 0.1 or abs(close_ratio - 0.2) < 0.02 or abs(close_ratio - 5) < 0.1):
        return "split_basis_ratio_candidate"
    if close_ratio and 0.95 <= close_ratio <= 1.05:
        return "minor_price_or_rounding_diff"
    return "price_value_mismatch"


def main() -> None:
    conn = connect_primary_db(timeout=900, readonly=True)
    candidate_parts: list[pd.DataFrame] = []
    aggregate_parts: list[pd.DataFrame] = []
    yearly: list[dict[str, object]] = []

    for path in sorted(MARCAP.glob("marcap-*.parquet")):
        year = int(path.stem.split("-")[-1])
        mar = pd.read_parquet(path, columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
        if mar.empty:
            continue
        mar = mar.rename(
            columns={
                "Code": "stock_code",
                "Date": "date",
                "Open": "marcap_open",
                "High": "marcap_high",
                "Low": "marcap_low",
                "Close": "marcap_close",
                "Volume": "marcap_volume",
            }
        )
        mar["stock_code"] = normalize_code(mar["stock_code"])
        mar["date"] = pd.to_datetime(mar["date"]).dt.strftime("%Y-%m-%d")
        for c in ["marcap_open", "marcap_high", "marcap_low", "marcap_close", "marcap_volume"]:
            mar[c] = pd.to_numeric(mar[c], errors="coerce")
        pg = load_pg_year(conn, str(mar["date"].min()), str(mar["date"].max()))
        merged = mar.merge(pg, on=["stock_code", "date"], how="outer", indicator=True)
        both = merged["_merge"] == "both"
        value_diff = both & (
            neq_num(merged["pg_open"], merged["marcap_open"])
            | neq_num(merged["pg_high"], merged["marcap_high"])
            | neq_num(merged["pg_low"], merged["marcap_low"])
            | neq_num(merged["pg_close"], merged["marcap_close"])
            | neq_num(merged["pg_volume"], merged["marcap_volume"], tol=0.0)
        )
        diff = merged[value_diff | (merged["_merge"] != "both")].copy()
        if diff.empty:
            continue
        diff["year"] = year
        diff["diff_type"] = diff.apply(classify_diff, axis=1)
        agg = (
            diff.groupby(["stock_code", "year", "diff_type"], dropna=False)
            .agg(
                rows=("date", "size"),
                first_date=("date", "min"),
                last_date=("date", "max"),
                max_abs_close_diff=("pg_close", lambda s: None),
            )
            .reset_index()
        )
        # calculate close diff separately to avoid groupby lambda needing both columns
        if not diff.empty:
            diff["abs_close_diff"] = (diff["pg_close"].astype(float) - diff["marcap_close"].astype(float)).abs()
            mx = diff.groupby(["stock_code", "year", "diff_type"])["abs_close_diff"].max().reset_index()
            agg = agg.drop(columns=["max_abs_close_diff"]).merge(mx, on=["stock_code", "year", "diff_type"], how="left")
            agg = agg.rename(columns={"abs_close_diff": "max_abs_close_diff"})
        aggregate_parts.append(agg)

        high = diff[diff["diff_type"].isin(["split_basis_ratio_candidate", "price_value_mismatch"])].copy()
        if not high.empty:
            high["target_table"] = "price_history"
            high["proposed_action"] = "replace_ohlcv_with_marcap_after_backup"
            high["replacement_open"] = high["marcap_open"]
            high["replacement_high"] = high["marcap_high"]
            high["replacement_low"] = high["marcap_low"]
            high["replacement_close"] = high["marcap_close"]
            high["replacement_volume"] = high["marcap_volume"]
            high["close_ratio_pg_to_marcap"] = high["pg_close"] / high["marcap_close"].replace({0: pd.NA})
            high["apply_condition"] = (
                "marcap 원주가와 PG 값 불일치. 기업행위/식별자 충돌이 아닌지 확인 후 "
                "price_history 백업+data_fix_log로 같은 stock_code/date만 대체"
            )
            candidate_parts.append(high)
        yearly.append({"year": year, "by_diff_type": dict(Counter(diff["diff_type"]))})
        print(f"{year}: high_risk={len(high)} diff={len(diff)}")

    conn.close()
    candidates = pd.concat(candidate_parts, ignore_index=True) if candidate_parts else pd.DataFrame()
    wanted_cols = [
        "target_table",
        "proposed_action",
        "stock_code",
        "date",
        "diff_type",
        "pg_open",
        "pg_high",
        "pg_low",
        "pg_close",
        "pg_volume",
        "marcap_open",
        "marcap_high",
        "marcap_low",
        "marcap_close",
        "marcap_volume",
        "replacement_open",
        "replacement_high",
        "replacement_low",
        "replacement_close",
        "replacement_volume",
        "close_ratio_pg_to_marcap",
        "apply_condition",
    ]
    cand_path = OUT / "price_marcap_high_risk_replacement_candidate_rows_20261004.csv"
    candidates[wanted_cols].to_csv(cand_path, index=False, encoding="utf-8-sig")

    aggregates = pd.concat(aggregate_parts, ignore_index=True) if aggregate_parts else pd.DataFrame()
    agg_path = OUT / "price_marcap_crosscheck_stock_aggregate_20261004.csv"
    aggregates.to_csv(agg_path, index=False, encoding="utf-8-sig")

    total_counter: Counter[str] = Counter()
    for y in yearly:
        total_counter.update(y["by_diff_type"])
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "high_risk_candidate_rows": int(len(candidates)),
        "stock_aggregate_rows": int(len(aggregates)),
        "by_diff_type": dict(total_counter),
        "outputs": {
            "high_risk_replacement_candidates": str(cand_path),
            "stock_aggregate": str(agg_path),
        },
    }
    out_json = OUT / "price_marcap_high_risk_candidates_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
