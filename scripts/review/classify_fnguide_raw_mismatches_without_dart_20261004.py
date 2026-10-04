#!/usr/bin/env python3
"""FnGuide 원문 대조 불일치를 DART 호출 없이 원인 분류한다(읽기 전용).

입력:
  research_outputs/financial_rereview_20261002/fnguide_raw_compare_mismatches.csv

산출:
  fnguide_raw_mismatches_classified_20261004.csv
  fnguide_raw_mismatch_classification_summary_20261004.json

주의:
  이 스크립트는 운영 테이블을 수정하지 않는다. 단위/정의/기간 후보를 review 큐로
  줄이는 목적이며, 값 반영은 원문 excerpt 확인과 백업/fix_log 뒤에만 한다.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
SRC = OUT / "fnguide_raw_compare_mismatches.csv"


def near(x: float | None, target: float, rel: float = 0.04) -> bool:
    if x is None or pd.isna(x) or target == 0:
        return False
    return abs(float(x) - target) <= abs(target) * rel


def code6(v: object) -> str:
    s = str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def load_sector_map(codes: list[str]) -> dict[str, str]:
    if not codes:
        return {}
    conn = connect_primary_db(timeout=120, readonly=True)
    ph = ",".join("?" for _ in codes)
    rows = conn.execute(
        f"SELECT stock_code, COALESCE(sector_large,'') FROM stock_universe WHERE stock_code IN ({ph})",
        codes,
    ).fetchall()
    conn.close()
    return {r[0]: r[1] or "" for r in rows}


def classify(row: pd.Series, sector: str) -> tuple[str, str, float]:
    field = str(row["field"])
    q = int(row["q"])
    fs = str(row["fs"])
    ratio = row.get("db/fnguide")
    ratio = float(ratio) if not pd.isna(ratio) else None

    if field in {"depreciation", "depreciation_amortization"}:
        return (
            "definition_gap_depreciation_components",
            "FnGuide 현금흐름 조정 감가상각/상각 표시와 DB 구성요소/본문행 정의 차이. XBRL 원문 태그 리뷰 후만 적용",
            0.9,
        )
    if sector.startswith("금융") or "금융" in sector:
        return (
            "financial_sector_definition_review",
            "금융업은 매출/영업수익/순영업이익 정의가 일반 제조업과 다름. 업권별 FnGuide label 확정 후 매핑",
            0.85,
        )
    if near(ratio, 1000) or near(ratio, 0.001) or near(ratio, 1_000_000) or near(ratio, 0.000001):
        return (
            "unit_scale_candidate",
            "10^3 또는 10^6 단위 차이 후보. 원문 단위/파서 단위 확인 뒤 제한 적용",
            0.9,
        )
    if field == "net_income" and fs == "CFS":
        return (
            "net_income_parent_vs_total_or_nci",
            "연결 순이익은 지배주주 기준/전체 기준 차이가 잦음. FnGuide label과 DART 지배/비지배 검산 필요",
            0.8,
        )
    if field == "total_equity" and fs == "CFS":
        return (
            "equity_parent_vs_total_or_nci",
            "연결 자본은 지배주주지분/자본총계 차이가 잦음. FnGuide label과 DART 지배/비지배 검산 필요",
            0.8,
        )
    if q != 0 and field in {"revenue", "operating_profit", "net_income"} and (
        near(ratio, 2, 0.12) or near(ratio, 0.5, 0.12) or near(ratio, 3, 0.12) or near(ratio, 1 / 3, 0.12)
    ):
        return (
            "quarter_ytd_vs_three_month_candidate",
            "분기 3개월값/누적값 또는 Q1+Q2 집계 혼동 후보. 기간축 재확인 필요",
            0.75,
        )
    if q != 0 and field in {"operating_cf", "investing_cf", "financing_cf", "capex"} and (
        near(ratio, 2, 0.15) or near(ratio, 0.5, 0.15) or near(ratio, 3, 0.15) or near(ratio, 1 / 3, 0.15)
    ):
        return (
            "cashflow_ytd_vs_quarter_candidate",
            "현금흐름 분기 누적/3개월 차분 혼동 후보. YTD 차분 산식과 FnGuide 분기 정의 확인",
            0.8,
        )
    if ratio is not None and 0.95 <= ratio <= 1.05:
        return (
            "minor_restatement_or_rounding_review",
            "0.5% 허용오차는 넘었지만 5% 이내. 정정값/반올림/원문 수집일 차이 후보",
            0.7,
        )
    if field in {"total_assets", "total_liabilities", "total_equity"} and ratio is not None and 0.9 <= ratio <= 1.1:
        return (
            "balance_sheet_restatement_or_basis_review",
            "재무상태표 10% 이내 차이. 정정/연결범위/결산월 기준 차이 후보",
            0.65,
        )
    if field in {"revenue", "operating_profit"} and ratio is not None and (ratio > 3 or ratio < 0.33):
        return (
            "statement_mapping_or_discontinued_ops_review",
            "손익계산서 매핑/중단영업/총액-순액 기준 차이 후보. FnGuide label과 DART 계정 확인 필요",
            0.65,
        )
    return (
        "unresolved_needs_source_review",
        "현재 로컬 제3자 정보만으로 설명 부족. FnGuide 원문 excerpt와 DART/보고서 원문 확인 필요",
        0.5,
    )


def main() -> None:
    df = pd.read_csv(SRC, dtype={"stock_code": str})
    if df.empty:
        print("no mismatches")
        return
    df["stock_code"] = df["stock_code"].map(code6)
    sectors = load_sector_map(sorted(df["stock_code"].unique()))
    causes, actions, confidence = [], [], []
    for _, row in df.iterrows():
        cause, action, conf = classify(row, sectors.get(row["stock_code"], ""))
        causes.append(cause)
        actions.append(action)
        confidence.append(conf)
    df["cause"] = causes
    df["recommended_action"] = actions
    df["confidence"] = confidence
    out_csv = OUT / "fnguide_raw_mismatches_classified_20261004.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    summary = {
        "input_rows": int(len(df)),
        "by_cause": dict(Counter(causes)),
        "by_field_cause": {
            f"{field}|{cause}": int(cnt)
            for (field, cause), cnt in df.groupby(["field", "cause"]).size().sort_values(ascending=False).items()
        },
        "stocks_by_cause": {
            cause: int(g["stock_code"].nunique()) for cause, g in df.groupby("cause")
        },
    }
    out_json = OUT / "fnguide_raw_mismatch_classification_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out_csv} {len(df)} rows")
    print(json.dumps(summary["by_cause"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
