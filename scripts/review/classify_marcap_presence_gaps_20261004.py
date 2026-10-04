#!/usr/bin/env python3
"""marcap 대조의 PG extra/missing을 종목 메타 기준으로 추가 분류한다(DB 수정 없음)."""
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
SRC = OUT / "price_marcap_crosscheck_stock_aggregate_20261004.csv"


def code6(v: object) -> str:
    s = str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def load_meta(codes: list[str]) -> pd.DataFrame:
    conn = connect_primary_db(timeout=120, readonly=True)
    rows = conn.execute(
        f"""
        SELECT stock_code, stock_name, market, stock_type, isin_code, secugrp_nm,
               kind_stkcert_nm, isu_full_name, listed_date, base_date, source
        FROM stock_universe
        WHERE stock_code IN ({','.join('?' for _ in codes)})
        """,
        codes,
    ).fetchall()
    conn.close()
    return pd.DataFrame([tuple(r) for r in rows], columns=[
        "stock_code", "stock_name", "market", "stock_type", "isin_code", "secugrp_nm",
        "kind_stkcert_nm", "isu_full_name", "listed_date", "base_date", "source",
    ])


def classify(row: pd.Series) -> str:
    name = " ".join(str(row.get(c) or "") for c in ["stock_name", "stock_type", "secugrp_nm", "kind_stkcert_nm", "isu_full_name"])
    dt = str(row.get("diff_type") or "")
    if "ETF" in name or "ETN" in name:
        return "etf_etn_or_fund_coverage_difference"
    if "스팩" in name or "SPAC" in name:
        return "spac_listing_or_merger_coverage_difference"
    if "우" in str(row.get("stock_name") or "") or "우선" in name:
        return "preferred_share_coverage_difference"
    if not str(row.get("stock_name") or ""):
        return "stock_universe_meta_missing_for_code"
    if dt == "pg_missing_marcap_row" and int(row.get("year") or 0) <= 2011:
        return "early_history_pg_missing_marcap_has_rows"
    if dt == "pg_extra_not_in_marcap":
        return "pg_extra_needs_listing_status_or_identifier_review"
    return "presence_gap_needs_review"


def main() -> None:
    df = pd.read_csv(SRC, dtype={"stock_code": str})
    df["stock_code"] = df["stock_code"].map(code6)
    target = df[df["diff_type"].isin(["pg_missing_marcap_row", "pg_extra_not_in_marcap"])].copy()
    codes = sorted(target["stock_code"].dropna().unique())
    meta = load_meta(codes)
    if not meta.empty:
        meta["stock_code"] = meta["stock_code"].map(code6)
    joined = target.merge(meta, on="stock_code", how="left")
    joined["presence_gap_cause"] = joined.apply(classify, axis=1)
    out_csv = OUT / "price_marcap_presence_gap_classified_20261004.csv"
    joined.to_csv(out_csv, index=False, encoding="utf-8-sig")

    summary_df = (
        joined.groupby(["presence_gap_cause", "diff_type"], dropna=False)
        .agg(rows=("rows", "sum"), stock_years=("stock_code", "count"), stocks=("stock_code", "nunique"))
        .reset_index()
        .sort_values(["rows"], ascending=False)
    )
    summary_csv = OUT / "price_marcap_presence_gap_summary_20261004.csv"
    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "input_stock_year_rows": int(len(target)),
        "input_diff_rows_sum": int(target["rows"].sum()),
        "by_cause": {
            cause: int(rows)
            for cause, rows in joined.groupby("presence_gap_cause")["rows"].sum().sort_values(ascending=False).items()
        },
        "outputs": {
            "classified": str(out_csv),
            "summary_csv": str(summary_csv),
        },
    }
    out_json = OUT / "price_marcap_presence_gap_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
