#!/usr/bin/env python3
"""검증 후보 CSV를 실제 적용 전 dry-run으로 점검한다(DB 수정 없음).

목적:
- 다른 AI가 후보 CSV를 적용하기 전에 대상 키 존재 여부, 현재값 일치 여부,
  예상 변경 건수, 백업/로그 테이블 설계를 빠르게 확인한다.
- --apply는 없다. 이 스크립트는 항상 읽기 전용이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

FIN = ROOT / "research_outputs" / "financial_rereview_20261002"
PRICE = ROOT / "research_outputs" / "price_accuracy_recheck_20261003"
THIRD = ROOT / "research_outputs" / "third_party_crosscheck_20261003"


def code6(v: object) -> str:
    s = str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def close(a: float | None, b: float | None, rel: float = 1e-9, abs_tol: float = 0.5) -> bool:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= max(abs_tol, abs(float(b)) * rel)


def check_cashflow_dep(conn) -> dict[str, object]:
    path = FIN / "cashflow_depreciation_q_replacement_candidate_rows_20261004.csv"
    df = pd.read_csv(path, dtype={"stock_code": str})
    ok = stale = missing = current_changed = 0
    samples = []
    for r in df.itertuples(index=False):
        row = conn.execute("SELECT id, depreciation_q FROM cash_flow_data WHERE id=?", (int(r.cash_flow_id),)).fetchone()
        if not row:
            missing += 1
            continue
        if not close(row["depreciation_q"], r.current_depreciation_q):
            current_changed += 1
            continue
        if close(row["depreciation_q"], r.proposed_depreciation_q):
            ok += 1
        else:
            stale += 1
            if len(samples) < 10:
                samples.append({
                    "cash_flow_id": int(r.cash_flow_id),
                    "stock_code": code6(r.stock_code),
                    "year": int(r.year),
                    "quarter": int(r.quarter),
                    "current": row["depreciation_q"],
                    "proposed": r.proposed_depreciation_q,
                })
    return {
        "candidate_file": str(path),
        "rows": int(len(df)),
        "would_update": stale,
        "already_equal": ok,
        "missing_target": missing,
        "current_changed_since_candidate": current_changed,
        "sample_updates": samples,
        "apply_design": "backup cash_flow_data rows by id -> update depreciation_q -> insert cashflow_fix_log/data_fix_log run_id",
    }


def check_inventory(conn) -> dict[str, object]:
    path = FIN / "inventory_unit_replacement_candidate_rows_20261004.csv"
    df = pd.read_csv(path, dtype={"stock_code": str})
    would = same = missing = changed = 0
    for r in df.itertuples(index=False):
        row = conn.execute(
            """
            SELECT inventory_assets_krw FROM dart_cost_quarterly
            WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=? AND report_type=?
            """,
            (code6(r.stock_code), int(r.fiscal_year), int(r.fiscal_quarter), str(r.report_type)),
        ).fetchone()
        if not row:
            missing += 1
            continue
        cur = row["inventory_assets_krw"]
        if not close(cur, r.current_inventory_assets_krw):
            changed += 1
            continue
        if close(cur, r.proposed_inventory_assets_krw):
            same += 1
        else:
            would += 1
    return {
        "candidate_file": str(path),
        "rows": int(len(df)),
        "would_update": would,
        "already_equal": same,
        "missing_target": missing,
        "current_changed_since_candidate": changed,
        "apply_design": "backup dart_cost_quarterly key rows -> update inventory_assets_krw -> insert data_fix_log with source_rcept_no",
    }


def check_price_prelisting(conn) -> dict[str, object]:
    path = PRICE / "price_prelisting_exclusion_candidate_rows_20261004.csv"
    df = pd.read_csv(path, dtype={"stock_code": str})
    present = missing = changed = 0
    for r in df.itertuples(index=False):
        row = conn.execute(
            "SELECT open, high, low, close, volume FROM price_history WHERE stock_code=? AND date=?",
            (code6(r.stock_code), str(r.date)),
        ).fetchone()
        if not row:
            missing += 1
            continue
        if close(row["close"], r.close):
            present += 1
        else:
            changed += 1
    return {
        "candidate_file": str(path),
        "rows": int(len(df)),
        "would_exclude_or_delete": present,
        "missing_target": missing,
        "current_changed_since_candidate": changed,
        "apply_design": "prefer quality exclusion/canonical filter; if physical delete, backup price_history rows first and log data_fix_log",
    }


def check_identifier() -> dict[str, object]:
    path = THIRD / "identifier_enrichment_candidates_20261004.csv"
    df = pd.read_csv(path, dtype={"stock_code": str})
    return {
        "candidate_file": str(path),
        "rows": int(len(df)),
        "would_upsert_identity_rows": int(len(df)),
        "requires_schema": "stock_identity_registry table or stock_universe extension columns: corp_code,bizr_no,jurir_no,registry_extracted_at",
        "apply_design": "create identity table first; do not alter stock_universe until consumers are migrated",
    }


def main() -> None:
    conn = connect_primary_db(timeout=180, readonly=True)
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "cashflow_depreciation_q": check_cashflow_dep(conn),
        "inventory_unit": check_inventory(conn),
        "price_prelisting": check_price_prelisting(conn),
        "identifier_enrichment": check_identifier(),
    }
    conn.close()
    out = FIN / "dry_run_apply_review_candidates_20261004.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
