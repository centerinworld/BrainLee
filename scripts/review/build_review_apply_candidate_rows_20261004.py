#!/usr/bin/env python3
"""DART 호출 없이 적용 검토용 후보 행을 만든다(운영 DB 수정 없음).

산출 목적:
- 가격 P0: 기존 `price_history` 행을 삭제/격리할 후보 키를 명시한다.
- 감가상각: `cash_flow_data.depreciation_q`를 어떤 값으로 대체할지
  현재값/제안값/FnGuide/구성요소 근거를 한 행에 담는다.

주의:
이 스크립트는 후보 CSV/JSON만 생성한다. PostgreSQL UPDATE/DELETE는 하지 않는다.
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

PRICE_DIR = ROOT / "research_outputs" / "price_accuracy_recheck_20261003"
FIN_DIR = ROOT / "research_outputs" / "financial_rereview_20261002"


def code6(v: object) -> str:
    s = str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def close_enough(a: float | None, b: float | None, rel: float = 0.005, abs_tol: float = 100_000_000.0) -> bool:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= max(abs_tol, abs(float(b)) * rel)


def build_price_candidates() -> tuple[int, int]:
    src = PRICE_DIR / "price_p0_marcap_window_resolution_20261004.csv"
    df = pd.read_csv(src, dtype={"stock_code": str})
    df["stock_code"] = df["stock_code"].map(code6)

    pre = df[df["marcap_window_resolution"] == "pre_marcap_listing_history_contamination"].copy()
    pre_out = pre[
        [
            "stock_code",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "candidate_type",
            "marcap_first_date",
            "marcap_last_date",
            "marcap_rows",
            "marcap_window_resolution",
            "marcap_window_note",
        ]
    ].copy()
    pre_out.insert(0, "target_table", "price_history")
    pre_out.insert(1, "proposed_action", "exclude_or_delete_after_backup")
    pre_out["replacement_open"] = None
    pre_out["replacement_high"] = None
    pre_out["replacement_low"] = None
    pre_out["replacement_close"] = None
    pre_out["replacement_volume"] = None
    pre_out["apply_condition"] = (
        "백업 후 canonical/quality exclusion 우선. 물리 삭제 시 marcap_first_date 이전 행만 삭제"
    )
    pre_out["confidence"] = 0.95
    pre_path = PRICE_DIR / "price_prelisting_exclusion_candidate_rows_20261004.csv"
    pre_out.to_csv(pre_path, index=False, encoding="utf-8-sig")

    zero = df[df["marcap_window_resolution"] == "source_agrees_zero_ohlc_positive_close_policy_decision"].copy()
    zero_out = zero[
        [
            "stock_code",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "candidate_type",
            "marcap_open",
            "marcap_high",
            "marcap_low",
            "marcap_close",
            "marcap_volume",
            "marcap_window_resolution",
        ]
    ].copy()
    zero_out.insert(0, "target_table", "price_history")
    zero_out.insert(1, "proposed_action", "mark_non_tradable_in_canonical_view")
    zero_out["replacement_open"] = zero_out["open"]
    zero_out["replacement_high"] = zero_out["high"]
    zero_out["replacement_low"] = zero_out["low"]
    zero_out["replacement_close"] = zero_out["close"]
    zero_out["replacement_volume"] = zero_out["volume"]
    zero_out["apply_condition"] = "raw 보존, 수익률/거래대금/전략 입력에서는 return_usable=0"
    zero_out["confidence"] = 0.9
    zero_path = PRICE_DIR / "price_zero_ohlc_nontradable_candidate_rows_20261004.csv"
    zero_out.to_csv(zero_path, index=False, encoding="utf-8-sig")
    return len(pre_out), len(zero_out)


def load_component_rows(keys: list[tuple[str, int, int, str]]) -> dict[tuple[str, int, int, str], dict[str, object]]:
    if not keys:
        return {}
    conn = connect_primary_db(timeout=180, readonly=True)
    out: dict[tuple[str, int, int, str], dict[str, object]] = {}
    # 후보 수가 작지 않아 VALUES 대신 단순 전체 후보를 읽고 파이썬에서 필터한다.
    rows = conn.execute(
        """
        SELECT
            cf.id AS cash_flow_id,
            cf.stock_code,
            cf.year,
            cf.quarter,
            cf.report_type,
            cf.depreciation_q AS current_depreciation_q,
            cf.data_source AS current_data_source,
            c.dep_cf_total,
            c.dep_source,
            c.cf_line_basis,
            c.rcept_no,
            c.run_id AS component_run_id
        FROM cash_flow_data cf
        JOIN financial_dep_capex_components c
          ON c.stock_code = cf.stock_code
         AND c.year = cf.year
         AND c.quarter = cf.quarter
         AND c.report_type = cf.report_type
        WHERE cf.is_annual = false
          AND cf.quarter BETWEEN 1 AND 4
          AND c.dep_cf_total IS NOT NULL
        """
    ).fetchall()
    wanted = set(keys)
    for r in rows:
        key = (code6(r["stock_code"]), int(r["year"]), int(r["quarter"]), str(r["report_type"]))
        if key not in wanted:
            continue
        out[key] = {
            "cash_flow_id": r["cash_flow_id"],
            "current_depreciation_q": r["current_depreciation_q"],
            "current_data_source": r["current_data_source"],
            "dep_cf_total": r["dep_cf_total"],
            "dep_source": r["dep_source"],
            "cf_line_basis": r["cf_line_basis"],
            "rcept_no": r["rcept_no"],
            "component_run_id": r["component_run_id"],
        }
    conn.close()
    return out


def build_depreciation_candidates() -> int:
    src = FIN_DIR / "fnguide_raw_mismatches_classified_20261004.csv"
    df = pd.read_csv(src, dtype={"stock_code": str})
    df["stock_code"] = df["stock_code"].map(code6)
    dep = df[
        (df["field"].isin(["depreciation", "depreciation_amortization"]))
        & (df["q"].astype(int) > 0)
        & (df["cause"] == "definition_gap_depreciation_components")
    ].copy()
    keys = [(r.stock_code, int(r.year), int(r.q), str(r.fs)) for r in dep.itertuples(index=False)]
    comp = load_component_rows(keys)
    rows: list[dict[str, object]] = []
    for r in dep.itertuples(index=False):
        key = (r.stock_code, int(r.year), int(r.q), str(r.fs))
        c = comp.get(key)
        if not c:
            continue
        fnguide_value = float(r.fnguide)
        proposed = c["dep_cf_total"]
        current = c["current_depreciation_q"]
        if not close_enough(proposed, fnguide_value):
            continue
        if close_enough(current, proposed):
            continue
        rows.append(
            {
                "target_table": "cash_flow_data",
                "target_column": "depreciation_q",
                "cash_flow_id": c["cash_flow_id"],
                "stock_code": key[0],
                "year": key[1],
                "quarter": key[2],
                "report_type": key[3],
                "current_depreciation_q": current,
                "proposed_depreciation_q": proposed,
                "fnguide_value": fnguide_value,
                "current_minus_proposed": None if current is None else float(current) - float(proposed),
                "proposed_minus_fnguide": float(proposed) - fnguide_value,
                "current_data_source": c["current_data_source"],
                "dep_source": c["dep_source"],
                "cf_line_basis": c["cf_line_basis"],
                "rcept_no": c["rcept_no"],
                "component_run_id": c["component_run_id"],
                "proposed_action": "update_depreciation_q_after_backup",
                "apply_condition": (
                    "FnGuide 원문과 dep_cf_total 일치, 현 운영 depreciation_q만 stale. "
                    "cash_flow_data 백업+cashflow_fix_log 후 제한 적용"
                ),
                "confidence": 0.95,
            }
        )
    out = pd.DataFrame(rows)
    out_path = FIN_DIR / "cashflow_depreciation_q_replacement_candidate_rows_20261004.csv"
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    return len(out)


def build_inventory_candidates() -> int:
    src = FIN_DIR / "inventory_fnguide_raw_mismatches_classified_20261004.csv"
    df = pd.read_csv(src, dtype={"stock_code": str})
    df["stock_code"] = df["stock_code"].map(code6)
    tgt = df[df["cause"].isin(["unit_error_db_1000x_fnguide", "unit_error_db_1_1000_of_fnguide"])].copy()
    rows: list[dict[str, object]] = []
    for r in tgt.itertuples(index=False):
        current = float(r.db_inventory)
        fnguide = float(r.fnguide_inventory)
        if r.cause == "unit_error_db_1000x_fnguide":
            proposed = current / 1000.0
        else:
            proposed = current * 1000.0
        rows.append(
            {
                "target_table": "dart_cost_quarterly",
                "target_column": "inventory_assets_krw",
                "stock_code": r.stock_code,
                "fiscal_year": int(r.year),
                "fiscal_quarter": int(r.q),
                "report_type": r.fs,
                "current_inventory_assets_krw": current,
                "proposed_inventory_assets_krw": proposed,
                "fnguide_inventory": fnguide,
                "proposed_minus_fnguide": proposed - fnguide,
                "source_rcept_no": r.source_rcept_no,
                "parser_version": r.parser_version,
                "cause": r.cause,
                "proposed_action": "update_inventory_assets_krw_after_source_excerpt_check",
                "apply_condition": (
                    "FnGuide 원문과 1000배/1_1000배 단위 후보가 맞음. "
                    "DART 원문 excerpt 확인 후 dart_cost_quarterly 백업+fix_log로 제한 적용"
                ),
                "confidence": 0.85,
            }
        )
    out = pd.DataFrame(rows)
    out_path = FIN_DIR / "inventory_unit_replacement_candidate_rows_20261004.csv"
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    return len(out)


def main() -> None:
    pre_count, zero_count = build_price_candidates()
    dep_count = build_depreciation_candidates()
    inv_count = build_inventory_candidates()
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "candidate_rows": {
            "price_prelisting_exclusion": pre_count,
            "price_zero_ohlc_nontradable": zero_count,
            "cashflow_depreciation_q_replacement": dep_count,
            "inventory_unit_replacement": inv_count,
        },
        "outputs": {
            "price_prelisting_exclusion": str(PRICE_DIR / "price_prelisting_exclusion_candidate_rows_20261004.csv"),
            "price_zero_ohlc_nontradable": str(PRICE_DIR / "price_zero_ohlc_nontradable_candidate_rows_20261004.csv"),
            "cashflow_depreciation_q_replacement": str(FIN_DIR / "cashflow_depreciation_q_replacement_candidate_rows_20261004.csv"),
            "inventory_unit_replacement": str(FIN_DIR / "inventory_unit_replacement_candidate_rows_20261004.csv"),
        },
        "by_action": dict(
            Counter(
                {
                    "exclude_or_delete_after_backup": pre_count,
                    "mark_non_tradable_in_canonical_view": zero_count,
                    "update_depreciation_q_after_backup": dep_count,
                    "update_inventory_assets_krw_after_source_excerpt_check": inv_count,
                }
            )
        ),
    }
    out_json = FIN_DIR / "review_apply_candidate_rows_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
