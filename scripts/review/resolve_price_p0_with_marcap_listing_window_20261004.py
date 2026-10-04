#!/usr/bin/env python3
"""가격 P0 후보를 marcap 상장 출현 구간으로 추가 분류한다(DART 미사용).

`price_p0_without_dart_resolve_20261003.py`가 값 대체 후보를 찾지 못한 소수 원화
가격 후보에 대해, 해당 코드가 marcap에 존재한 첫/마지막 날짜와 비교한다. 후보 날짜가
marcap 첫 출현일보다 이르면 단순 보정가가 아니라 상장 전 이력/티커 신원 혼입 후보로
분류한다.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
PRICE_DIR = ROOT / "research_outputs" / "price_accuracy_recheck_20261003"
SRC = PRICE_DIR / "price_p0_without_dart_resolution.csv"
MARCAP = ROOT / "data_cache" / "marcap"


def code6(v: object) -> str:
    s = str(v)
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def load_marcap_window(codes: set[str]) -> dict[str, dict[str, str]]:
    parts = []
    for p in sorted(MARCAP.glob("marcap-*.parquet")):
        df = pd.read_parquet(p, columns=["Code", "Date", "Open", "High", "Low", "Close", "Volume"])
        df["Code"] = df["Code"].astype(str).str.zfill(6)
        df = df[df["Code"].isin(codes)]
        if not df.empty:
            parts.append(df)
    if not parts:
        return {}
    all_df = pd.concat(parts, ignore_index=True)
    all_df["Date"] = pd.to_datetime(all_df["Date"]).dt.strftime("%Y-%m-%d")
    out: dict[str, dict[str, str]] = {}
    for code, g in all_df.groupby("Code"):
        out[code] = {
            "marcap_first_date": str(g["Date"].min()),
            "marcap_last_date": str(g["Date"].max()),
            "marcap_rows": int(len(g)),
        }
    return out


def main() -> None:
    df = pd.read_csv(SRC, dtype={"stock_code": str})
    df["stock_code"] = df["stock_code"].map(code6)
    windows = load_marcap_window(set(df["stock_code"]))
    classifications = []
    notes = []
    firsts = []
    lasts = []
    rows = []
    for _, r in df.iterrows():
        w = windows.get(r["stock_code"])
        firsts.append(w["marcap_first_date"] if w else None)
        lasts.append(w["marcap_last_date"] if w else None)
        rows.append(w["marcap_rows"] if w else 0)
        date = str(r["date"])
        if not w:
            classifications.append("no_marcap_code_window")
            notes.append("marcap 캐시에 해당 코드가 없음. 식별자/상장상태 별도 확인")
        elif date < w["marcap_first_date"]:
            classifications.append("pre_marcap_listing_history_contamination")
            notes.append("후보 날짜가 marcap 첫 출현일보다 이름. 상장 전 이력/티커 신원/보정계열 혼입 후보")
        elif date > w["marcap_last_date"]:
            classifications.append("post_marcap_last_date_review")
            notes.append("후보 날짜가 marcap 마지막 출현일보다 늦음. 상폐/거래정지/커버리지 확인")
        else:
            classifications.append(str(r["without_dart_resolution"]))
            notes.append(str(r.get("resolution_note") or "marcap 출현 구간 안. 기존 P0 분류 유지"))
    df["marcap_first_date"] = firsts
    df["marcap_last_date"] = lasts
    df["marcap_rows"] = rows
    df["marcap_window_resolution"] = classifications
    df["marcap_window_note"] = notes
    out_csv = PRICE_DIR / "price_p0_marcap_window_resolution_20261004.csv"
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")
    summary = {
        "input_rows": int(len(df)),
        "stocks": int(df["stock_code"].nunique()),
        "by_resolution": dict(Counter(classifications)),
        "by_candidate_resolution": {
            f"{cand}|{res}": int(cnt)
            for (cand, res), cnt in df.groupby(["candidate_type", "marcap_window_resolution"]).size().sort_values(ascending=False).items()
        },
    }
    out_json = PRICE_DIR / "price_p0_marcap_window_resolution_summary_20261004.json"
    out_json.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out_csv} {len(df)} rows")
    print(json.dumps(summary["by_resolution"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
