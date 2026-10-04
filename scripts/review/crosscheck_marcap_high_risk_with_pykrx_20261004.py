#!/usr/bin/env python3
"""marcap 고위험 가격 후보 일부를 pykrx adjusted/unadjusted 양쪽으로 대조한다."""
from __future__ import annotations

import json
from collections import Counter
from datetime import timedelta
from pathlib import Path

import pandas as pd
from pykrx import stock

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research_outputs" / "price_accuracy_recheck_20261003"
SRC = OUT / "price_marcap_high_risk_replacement_candidate_rows_20261004.csv"


def close(a: float | None, b: float | None, rel: float = 0.005, abs_tol: float = 1.0) -> bool:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= max(abs_tol, abs(float(b)) * rel)


def classify(v: float | None, pg: float, marcap: float) -> str:
    if v is None or pd.isna(v):
        return "missing"
    if close(v, marcap):
        return "supports_marcap"
    if close(v, pg):
        return "supports_pg"
    return "third_value"


def fetch_close(code: str, date: str, adjusted: bool) -> tuple[float | None, str]:
    d = pd.to_datetime(date).date()
    start = (d - timedelta(days=5)).strftime("%Y%m%d")
    end = (d + timedelta(days=5)).strftime("%Y%m%d")
    try:
        got = stock.get_market_ohlcv_by_date(start, end, code, adjusted=adjusted)
        if got is None or got.empty:
            return None, "empty"
        got = got.copy()
        got.index = pd.to_datetime(got.index).strftime("%Y-%m-%d")
        if date not in got.index:
            return None, f"date_missing {got.index.min()}..{got.index.max()}"
        return float(got.loc[date, "종가"]), ""
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)[:300]


def main() -> None:
    df = pd.read_csv(SRC, dtype={"stock_code": str})
    df["date_dt"] = pd.to_datetime(df["date"])
    df["abs_close_diff"] = (df["pg_close"].astype(float) - df["marcap_close"].astype(float)).abs()
    sample = (
        df.sort_values(["date_dt", "abs_close_diff"], ascending=[False, False])
        .drop_duplicates("stock_code")
        .head(25)
        .copy()
    )
    rows = []
    for r in sample.itertuples(index=False):
        code = str(r.stock_code).zfill(6)
        adj_close, adj_note = fetch_close(code, str(r.date), True)
        raw_close, raw_note = fetch_close(code, str(r.date), False)
        rows.append(
            {
                "stock_code": code,
                "date": r.date,
                "diff_type": r.diff_type,
                "pg_close": r.pg_close,
                "marcap_close": r.marcap_close,
                "pykrx_adjusted_close": adj_close,
                "pykrx_unadjusted_close": raw_close,
                "adjusted_status": classify(adj_close, r.pg_close, r.marcap_close),
                "unadjusted_status": classify(raw_close, r.pg_close, r.marcap_close),
                "adjusted_note": adj_note,
                "unadjusted_note": raw_note,
            }
        )
    out = pd.DataFrame(rows)
    out_csv = OUT / "price_marcap_high_risk_pykrx_sample_20261004.csv"
    out.to_csv(out_csv, index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "sample_rows": int(len(out)),
        "adjusted_by_status": dict(Counter(out["adjusted_status"])),
        "unadjusted_by_status": dict(Counter(out["unadjusted_status"])),
        "output": str(out_csv),
    }
    out_json = OUT / "price_marcap_high_risk_pykrx_sample_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
