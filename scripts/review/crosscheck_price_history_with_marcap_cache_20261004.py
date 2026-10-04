#!/usr/bin/env python3
"""PostgreSQL price_history를 로컬 marcap parquet 캐시와 연도별 전수 대조한다.

DART 호출 없이 가능한 제3자/공식 캐시 확대 검증이다. 운영 DB는 읽기 전용이며,
모든 불일치를 즉시 수정하지 않고 요약과 샘플을 review 큐로 남긴다.
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
MAX_MISMATCH_ROWS = 200_000


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
    yearly: list[dict[str, object]] = []
    mismatch_parts: list[pd.DataFrame] = []
    remaining = MAX_MISMATCH_ROWS

    for path in sorted(MARCAP.glob("marcap-*.parquet")):
        year = path.stem.split("-")[-1]
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
        start, end = str(mar["date"].min()), str(mar["date"].max())
        pg = load_pg_year(conn, start, end)
        merged = mar.merge(pg, on=["stock_code", "date"], how="outer", indicator=True)
        both = merged["_merge"] == "both"
        value_diff = both & (
            neq_num(merged["pg_open"], merged["marcap_open"])
            | neq_num(merged["pg_high"], merged["marcap_high"])
            | neq_num(merged["pg_low"], merged["marcap_low"])
            | neq_num(merged["pg_close"], merged["marcap_close"])
            | neq_num(merged["pg_volume"], merged["marcap_volume"], tol=0.0)
        )
        missing_diff = merged["_merge"] != "both"
        diff = merged[value_diff | missing_diff].copy()
        if not diff.empty:
            diff["diff_type"] = diff.apply(classify_diff, axis=1)
            if remaining > 0:
                take = diff.head(remaining)
                mismatch_parts.append(take)
                remaining -= len(take)
        counts = Counter(diff["diff_type"]) if not diff.empty else Counter()
        yearly.append(
            {
                "year": int(year),
                "marcap_rows": int(len(mar)),
                "pg_rows_in_window": int(len(pg)),
                "overlap_rows": int(both.sum()),
                "diff_rows": int(len(diff)),
                "diff_rate_vs_overlap_plus_missing": float(len(diff) / len(merged)) if len(merged) else 0.0,
                "by_diff_type": dict(counts),
            }
        )
        print(f"{year}: marcap={len(mar)} pg={len(pg)} diff={len(diff)}")

    conn.close()
    mismatch_df = pd.concat(mismatch_parts, ignore_index=True) if mismatch_parts else pd.DataFrame()
    mismatch_path = OUT / "price_history_marcap_crosscheck_mismatches_20261004.csv"
    mismatch_df.to_csv(mismatch_path, index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "source": "local marcap parquet cache",
        "years": yearly,
        "totals": {
            "marcap_rows": int(sum(r["marcap_rows"] for r in yearly)),
            "pg_rows_in_window": int(sum(r["pg_rows_in_window"] for r in yearly)),
            "overlap_rows": int(sum(r["overlap_rows"] for r in yearly)),
            "diff_rows": int(sum(r["diff_rows"] for r in yearly)),
        },
        "mismatch_csv": str(mismatch_path),
        "mismatch_csv_cap": MAX_MISMATCH_ROWS,
    }
    total_counter: Counter[str] = Counter()
    for r in yearly:
        total_counter.update(r["by_diff_type"])
    summary["totals"]["by_diff_type"] = dict(total_counter)
    out_json = OUT / "price_history_marcap_crosscheck_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary["totals"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
