#!/usr/bin/env python3
"""FnGuide 원문 불일치 중 unresolved 590건을 DART 호출 없이 추가 분해한다.

주요 목적:
- CapEx 미해결을 구성요소(`financial_dep_capex_components`)와 대조해
  즉시 대체 후보와 정의 차이 후보로 나눈다.
- 단순 부호 반전/대체 기간 근접 후보를 별도 분류한다.

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

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
SRC = OUT / "fnguide_raw_mismatches_classified_20261004.csv"


def code6(v: object) -> str:
    s = str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def close(a: float | None, b: float | None, rel: float = 0.005, abs_tol: float = 100_000_000.0) -> bool:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= max(abs_tol, abs(float(b)) * rel)


def load_components() -> dict[tuple[str, int, int, str], dict[str, object]]:
    conn = connect_primary_db(timeout=180, readonly=True)
    rows = conn.execute(
        """
        SELECT stock_code, year, quarter, report_type,
               dep_cf_total, capex_ppe, capex_intangible,
               capex_source, cf_line_basis, rcept_no, run_id
        FROM financial_dep_capex_components
        WHERE dep_cf_total IS NOT NULL OR capex_ppe IS NOT NULL OR capex_intangible IS NOT NULL
        """
    ).fetchall()
    conn.close()
    out: dict[tuple[str, int, int, str], dict[str, object]] = {}
    for r in rows:
        out[(code6(r["stock_code"]), int(r["year"]), int(r["quarter"]), str(r["report_type"]))] = {
            "dep_cf_total": r["dep_cf_total"],
            "capex_ppe": r["capex_ppe"],
            "capex_intangible": r["capex_intangible"],
            "capex_source": r["capex_source"],
            "cf_line_basis": r["cf_line_basis"],
            "rcept_no": r["rcept_no"],
            "run_id": r["run_id"],
        }
    return out


def main() -> None:
    df = pd.read_csv(SRC, dtype={"stock_code": str})
    df["stock_code"] = df["stock_code"].map(code6)
    unresolved = df[df["cause"] == "unresolved_needs_source_review"].copy()
    comps = load_components()
    refined = []
    notes = []
    candidate_rows: list[dict[str, object]] = []
    for r in unresolved.itertuples(index=False):
        key = (r.stock_code, int(r.year), int(r.q), str(r.fs))
        db = float(r.db)
        fg = float(r.fnguide)
        comp = comps.get(key)
        cause = "still_unresolved_needs_source_excerpt"
        note = "현재 로컬 자료만으로 추가 확정 불가"
        if str(r.field) == "capex":
            if not comp:
                cause = "capex_no_component_row"
                note = "구성요소 테이블에도 CapEx 원천 없음"
            else:
                ppe = comp.get("capex_ppe")
                intangible = comp.get("capex_intangible")
                ppe_int = (float(ppe or 0) + float(intangible or 0)) if (ppe is not None or intangible is not None) else None
                if close(ppe, fg):
                    cause = "capex_component_ppe_within_abs_tolerance"
                    note = "구성요소 capex_ppe가 FnGuide와 절대금액 허용오차 내. 현재값 유지+허용오차/정의 검토 후보"
                    candidate = ppe
                elif close(ppe_int, fg):
                    cause = "capex_component_ppe_plus_intangible_within_abs_tolerance"
                    note = "유형+무형 취득 합계가 FnGuide와 절대금액 허용오차 내. FnGuide 정의 확인 후보"
                    candidate = ppe_int
                else:
                    cause = "capex_component_definition_gap"
                    note = "구성요소는 있으나 FnGuide와 불일치. 유형/무형/투자부동산/연결별도 정의 차이 후보"
                    candidate = None
                if candidate is not None and not close(db, candidate, rel=0.005, abs_tol=1_000_000.0):
                    candidate_rows.append(
                        {
                            "target_table": "cash_flow_data",
                            "target_column": "capex" if int(r.q) == 0 else "capex_q",
                            "stock_code": r.stock_code,
                            "year": int(r.year),
                            "quarter": int(r.q),
                            "report_type": r.fs,
                            "current_value": db,
                            "proposed_value": candidate,
                            "fnguide_value": fg,
                            "capex_ppe": ppe,
                            "capex_intangible": intangible,
                            "capex_source": comp.get("capex_source"),
                            "rcept_no": comp.get("rcept_no"),
                            "component_run_id": comp.get("run_id"),
                            "proposed_action": "update_capex_from_component_after_backup",
                            "apply_condition": "FnGuide 원문과 구성요소 값 일치, 현재 운영값과 제안값 불일치. cash_flow_data 백업+cashflow_fix_log 후 제한 적용",
                            "confidence": 0.9,
                        }
                    )
        elif str(r.field) in {"operating_cf", "investing_cf", "financing_cf", "net_income"} and close(-db, fg):
            cause = "sign_convention_candidate"
            note = "DB와 FnGuide가 부호 반전으로 일치. 표시 부호/파서 부호 확인 후보"
        elif 0.9 <= abs(db / fg) <= 1.1:
            cause = "basis_or_restatement_10pct_review"
            note = "10% 이내 차이. 연결범위/정정/기간 기준 후보"
        refined.append(cause)
        notes.append(note)

    unresolved["refined_cause"] = refined
    unresolved["refined_note"] = notes
    out_csv = OUT / "fnguide_unresolved_refined_20261004.csv"
    unresolved.to_csv(out_csv, index=False, encoding="utf-8-sig")
    cand = pd.DataFrame(candidate_rows)
    cand_csv = OUT / "cashflow_capex_replacement_candidate_rows_20261004.csv"
    cand.to_csv(cand_csv, index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "input_unresolved_rows": int(len(unresolved)),
        "by_refined_cause": dict(Counter(refined)),
        "capex_replacement_candidate_rows": int(len(cand)),
        "outputs": {
            "refined_unresolved": str(out_csv),
            "capex_replacement_candidates": str(cand_csv),
        },
    }
    out_json = OUT / "fnguide_unresolved_refined_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
