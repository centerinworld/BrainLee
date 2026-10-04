#!/usr/bin/env python3
"""kr-company-registry 기반 식별자 보강 후보를 만든다(DB 수정 없음).

stock_universe에는 현재 corp_code/bizr_no/jurir_no 컬럼이 없다. 이 스크립트는
운영 테이블에 바로 쓰지 않고, 다음 AI가 별도 identity table 또는 컬럼 추가를 검토할
수 있도록 높은 신뢰의 매칭 후보 행을 CSV로 남긴다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research_outputs" / "third_party_crosscheck_20261003"


def main() -> None:
    matched_path = OUT / "kr_company_registry_matched.csv"
    if matched_path.exists():
        matched = pd.read_csv(matched_path, dtype=str).fillna("")
        limitation = None
    else:
        matched = pd.read_csv(OUT / "kr_company_registry_name_mismatch.csv", dtype=str).fillna("")
        limitation = (
            "kr_company_registry_matched.csv가 없어 name_mismatch만 후보화. "
            "third_party_crosscheck_20261003.py 재실행 필요"
        )
    rows = []
    for r in matched.to_dict("records"):
        rows.append(
            {
                "target_design": "stock_identity_registry_or_stock_universe_extension",
                "proposed_action": "upsert_identifier_after_alias_review",
                "stock_code": r.get("code", ""),
                "pg_stock_name": r.get("stock_name", ""),
                "registry_corp_name": r.get("corp_name", ""),
                "pg_market": r.get("market_x", ""),
                "registry_market": r.get("market_y", ""),
                "corp_code": r.get("corp_code", ""),
                "bizr_no": r.get("bizr_no", ""),
                "jurir_no": r.get("jurir_no", ""),
                "corp_cls": r.get("corp_cls", ""),
                "registry_extracted_at": r.get("extracted_at", ""),
                "confidence": 0.95 if r.get("stock_name", "") == r.get("corp_name", "") else 0.9,
                "apply_condition": "ticker와 시장이 일치. 이름 차이가 있으면 법인명/상호 약칭 alias인지 확인 후 보강",
            }
        )
    out = pd.DataFrame(rows)
    out_csv = OUT / "identifier_enrichment_candidates_20261004.csv"
    out.to_csv(out_csv, index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "candidate_rows": int(len(out)),
        "limitation": limitation,
        "output": str(out_csv),
    }
    out_json = OUT / "identifier_enrichment_candidates_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
