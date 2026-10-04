#!/usr/bin/env python3
"""aikstockdata 재무 불일치를 FnGuide 원문으로 삼각검증한다(DART 미사용).

입력:
  research_outputs/third_party_crosscheck_20261003/aik_stock_json_all_financial_mismatches_classified.csv
  data_raw/fnguide_wcomp/*/*.json.gz

산출:
  research_outputs/third_party_crosscheck_20261003/aik_stock_json_all_mismatches_fnguide_triangulated_20261004.csv
  research_outputs/third_party_crosscheck_20261003/aik_stock_json_all_mismatches_fnguide_triangulated_summary_20261004.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from compare_db_vs_fnguide_raw_20261003 import parse_code  # noqa: E402

IN = ROOT / "research_outputs" / "third_party_crosscheck_20261003" / "aik_stock_json_all_financial_mismatches_classified.csv"
OUT = ROOT / "research_outputs" / "third_party_crosscheck_20261003"


def close(a: float | None, b: float | None) -> bool:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= max(2e6, abs(float(b)) * 0.005)


def code6(v: object) -> str:
    s = str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def fnguide_value(raw: dict, period: str, fs: str, field: str) -> tuple[float | None, str]:
    if period.endswith("Q1"):
        y = int(period[:4])
        v = raw.get((y, 1, fs), {}).get(field)
        return v, "fg_q1"
    if period.endswith("H1"):
        y = int(period[:4])
        if field in {"revenue", "operating_profit", "net_income"}:
            v1 = raw.get((y, 1, fs), {}).get(field)
            v2 = raw.get((y, 2, fs), {}).get(field)
            if v1 is not None and v2 is not None:
                return float(v1) + float(v2), "fg_q1_plus_q2"
            # 일부 원문은 반기 누적을 Q2 칸으로 제공하는 경우가 있어 보조 후보로 둔다.
            if v2 is not None:
                return float(v2), "fg_q2_only_fallback"
        v = raw.get((y, 2, fs), {}).get(field)
        return v, "fg_q2"
    return None, "unsupported_period"


def classify(row: pd.Series) -> tuple[str, str]:
    fg = row.get("fnguide_value")
    pg = row.get("pg_value")
    aik = row.get("aik_value")
    fg_pg = close(fg, pg)
    fg_aik = close(fg, aik)
    if pd.isna(fg):
        return "fnguide_raw_missing", "FnGuide 원문 미확보 또는 해당 필드 없음"
    if fg_pg and not fg_aik:
        return "fnguide_supports_postgresql", "FnGuide 원문은 PostgreSQL과 일치. aik 쪽 정의/기간/반올림 차이 후보"
    if fg_aik and not fg_pg:
        return "fnguide_supports_aik", "FnGuide 원문은 aik와 일치. PostgreSQL 원인 조사/수정 후보"
    if fg_pg and fg_aik:
        return "all_close_after_tolerance", "삼자 모두 허용오차 내. 이전 불일치 기준 또는 반올림 경계"
    return "three_way_disagree", "FnGuide/aik/PostgreSQL 모두 다름. 기간/연결별도/정의 재확인 필요"


def main() -> None:
    df = pd.read_csv(IN, dtype={"code": str})
    if df.empty:
        print("no mismatches")
        return
    df["code"] = df["code"].map(code6)
    raw_cache: dict[str, tuple[dict, str | None]] = {}
    fg_values, fg_basis = [], []
    for _, row in df.iterrows():
        code = row["code"]
        if code not in raw_cache:
            raw_cache[code] = parse_code(code)
        raw, _day = raw_cache[code]
        v, basis = fnguide_value(raw, str(row["period"]), str(row["report_type"]), str(row["field"]))
        fg_values.append(v)
        fg_basis.append(basis)
    df["fnguide_value"] = fg_values
    df["fnguide_compare_basis"] = fg_basis
    tri = df.apply(classify, axis=1, result_type="expand")
    df["triangulation"] = tri[0]
    df["triangulation_note"] = tri[1]
    out_csv = OUT / "aik_stock_json_all_mismatches_fnguide_triangulated_20261004.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    summary = {
        "input_rows": int(len(df)),
        "stocks": int(df["code"].nunique()),
        "by_triangulation": dict(Counter(df["triangulation"])),
        "by_field_triangulation": {
            f"{field}|{tri}": int(cnt)
            for (field, tri), cnt in df.groupby(["field", "triangulation"]).size().sort_values(ascending=False).items()
        },
    }
    out_json = OUT / "aik_stock_json_all_mismatches_fnguide_triangulated_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out_csv} {len(df)} rows")
    print(json.dumps(summary["by_triangulation"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
