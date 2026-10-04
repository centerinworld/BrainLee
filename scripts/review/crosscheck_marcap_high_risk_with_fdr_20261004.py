#!/usr/bin/env python3
"""marcap 고위험 가격 후보 일부를 FinanceDataReader로 추가 대조한다.

FDR은 보정/수정주가 계열을 줄 수 있으므로 write 소스가 아니다. 다만 marcap과 PG 중
어느 쪽에 가까운지 소량 표본을 확인해 제3자 검증 큐의 우선순위를 조정한다.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research_outputs" / "price_accuracy_recheck_20261003"
SRC = OUT / "price_marcap_high_risk_replacement_candidate_rows_20261004.csv"


def close(a: float | None, b: float | None, rel: float = 0.005, abs_tol: float = 1.0) -> bool:
    if a is None or b is None or pd.isna(a) or pd.isna(b):
        return False
    return abs(float(a) - float(b)) <= max(abs_tol, abs(float(b)) * rel)


def main() -> None:
    import FinanceDataReader as fdr

    df = pd.read_csv(SRC, dtype={"stock_code": str})
    # 최근 연도와 큰 차이를 우선. 네트워크 호출을 줄이기 위해 종목별 첫 행만.
    df["date_dt"] = pd.to_datetime(df["date"])
    df["abs_close_diff"] = (df["pg_close"].astype(float) - df["marcap_close"].astype(float)).abs()
    sample = (
        df.sort_values(["date_dt", "abs_close_diff"], ascending=[False, False])
        .drop_duplicates("stock_code")
        .head(40)
        .copy()
    )
    rows = []
    for r in sample.itertuples(index=False):
        code = str(r.stock_code).zfill(6)
        d = pd.to_datetime(r.date).date()
        start = (d - timedelta(days=5)).strftime("%Y-%m-%d")
        end = (d + timedelta(days=5)).strftime("%Y-%m-%d")
        status = "fetch_error"
        fdr_close = None
        note = ""
        try:
            got = fdr.DataReader(code, start, end)
            if got is None or got.empty:
                status = "fdr_empty"
            else:
                got = got.copy()
                got.index = pd.to_datetime(got.index).strftime("%Y-%m-%d")
                if str(r.date) in got.index:
                    fdr_close = float(got.loc[str(r.date), "Close"])
                    if close(fdr_close, r.marcap_close):
                        status = "fdr_supports_marcap"
                    elif close(fdr_close, r.pg_close):
                        status = "fdr_supports_pg"
                    else:
                        status = "fdr_third_value"
                else:
                    status = "fdr_date_missing"
                    note = f"available {got.index.min()}..{got.index.max()}"
        except Exception as exc:  # noqa: BLE001
            note = str(exc)[:300]
        rows.append(
            {
                "stock_code": code,
                "date": r.date,
                "diff_type": r.diff_type,
                "pg_close": r.pg_close,
                "marcap_close": r.marcap_close,
                "fdr_close": fdr_close,
                "status": status,
                "note": note,
            }
        )
    out = pd.DataFrame(rows)
    out_csv = OUT / "price_marcap_high_risk_fdr_sample_20261004.csv"
    out.to_csv(out_csv, index=False, encoding="utf-8-sig")
    summary = {
        "generated_at": "2026-10-04",
        "operating_db_modified": False,
        "sample_rows": int(len(out)),
        "by_status": dict(Counter(out["status"])),
        "output": str(out_csv),
        "warning": "FDR은 보정주가 계열 가능성이 있어 write source가 아니라 보조 대조용",
    }
    out_json = OUT / "price_marcap_high_risk_fdr_sample_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
