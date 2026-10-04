#!/usr/bin/env python3
"""제3자 식별자 대조 결과를 review 큐로 합친다(DART 미사용, DB 수정 없음)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "research_outputs" / "third_party_crosscheck_20261003"


def code6(v: object) -> str:
    if pd.isna(v):
        return ""
    s = str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6) if s else ""


def classify_name(pg_name: str, ext_name: str) -> str:
    p = str(pg_name or "")
    e = str(ext_name or "")
    if p.replace(" ", "") in e.replace(" ", "") or e.replace(" ", "") in p.replace(" ", ""):
        return "legal_name_alias_or_suffix"
    if any(token in p for token in ["우", "우B", "2우"]) or any(token in e for token in ["우", "우B", "2우"]):
        return "preferred_share_name_review"
    if any(token in p for token in ["스팩", "SPAC"]) or any(token in e for token in ["스팩", "SPAC"]):
        return "spac_or_renamed_entity_review"
    return "name_change_or_identity_review"


def add_rows(rows: list[dict[str, object]], path: str, source: str, issue: str) -> None:
    f = SRC / path
    if not f.exists():
        return
    df = pd.read_csv(f, dtype=str).fillna("")
    for r in df.to_dict("records"):
        code = code6(r.get("code") or r.get("ticker"))
        ext_name = r.get("corp_name") or r.get("sm_name") or ""
        pg_name = r.get("stock_name") or ""
        cause = issue
        if issue == "name_mismatch":
            cause = classify_name(pg_name, ext_name)
        if issue == "pg_only" and ("우" in pg_name or "우B" in pg_name):
            cause = "pg_only_preferred_share_missing_in_external"
        elif issue == "pg_only":
            cause = "pg_only_needs_listing_status_or_external_coverage_review"
        elif issue.endswith("_only"):
            cause = f"{source}_{issue}_historical_or_missing_universe_review"
        rows.append(
            {
                "source": source,
                "issue": issue,
                "cause": cause,
                "stock_code": code,
                "pg_name": pg_name,
                "external_name": ext_name,
                "pg_market": r.get("market") or r.get("market_x") or "",
                "external_market": r.get("market_y") or "",
                "corp_code": r.get("corp_code") or "",
                "bizr_no": r.get("bizr_no") or "",
                "jurir_no": r.get("jurir_no") or "",
                "isin_code": r.get("isin_code") or "",
                "review_action": "식별자/상장상태/corp_code 매핑 확인 후 stock_universe 보강 후보",
            }
        )


def main() -> None:
    rows: list[dict[str, object]] = []
    add_rows(rows, "kr_company_registry_pg_only.csv", "kr_company_registry", "pg_only")
    add_rows(rows, "kr_company_registry_registry_only.csv", "kr_company_registry", "registry_only")
    add_rows(rows, "kr_company_registry_name_mismatch.csv", "kr_company_registry", "name_mismatch")
    add_rows(rows, "stock_master_pg_only.csv", "finance_data_stock_master", "pg_only")
    add_rows(rows, "stock_master_stock_master_only.csv", "finance_data_stock_master", "stock_master_only")
    add_rows(rows, "stock_master_name_mismatch.csv", "finance_data_stock_master", "name_mismatch")
    out = pd.DataFrame(rows)
    out_path = SRC / "identifier_review_queue_20261004.csv"
    out.to_csv(out_path, index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "rows": int(len(out)),
        "by_source_issue": {
            f"{source}|{issue}": int(cnt)
            for (source, issue), cnt in out.groupby(["source", "issue"]).size().sort_values(ascending=False).items()
        },
        "by_cause": dict(Counter(out["cause"])) if not out.empty else {},
        "output": str(out_path),
    }
    summary_path = SRC / "identifier_review_queue_summary_20261004.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
