#!/usr/bin/env python3
"""감가상각 정의 후보와 FnGuide 연간 값 대조 (2026-10-13, C14 근거 재현용). 읽기 전용.

대상: financial_field_verification 의 field='depreciation', quarter=0(연간), FnGuide 값이 있는 칸 (--years, 기본 2023~2024).
후보 정의(원문 = data_raw/dart_fnltt 현금흐름 행, 다음 해 사업보고서 전기 칸 우선·없으면 당기 칸):
  single_row   이름이 '감가상각비'·'유형자산감가상각비'인 행 하나
  xbrl_tag     추출기(financial_rereview.extract)의 depreciation 값 = 태그 행 우선, 없으면 이름 행
  sum_rows     이름에 '감가상각'이 들어가는 현금흐름 행들의 합(무형·대손·상각후원가 제외)  ← 10-13 채택 정의
  doc_table    사업보고서 본문 현금흐름 조정 표를 같은 합 규칙으로 읽고, 다음 해 보고서 전기 열과 일치한 값만
일치 기준: |후보 − FnGuide| ≤ max(2e6, FnGuide의 0.1%) (D-7). 후보가 없는 칸은 분모에서 뺀다(후보 있는 칸 대비 일치율).
출력: research_outputs/financial_rereview_20261002/dep_definition_vs_fnguide_<연도범위>_20261013.{json,csv}
"""
from __future__ import annotations

import argparse
import collections
import csv
import gzip
import importlib.util
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002"


def _load(name, fname):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "review" / fname)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def cf_rows(code, y, fs):
    p = fn.RAW_DART / code / f"{y}_0_{fs}.json.gz"
    return [r for r in json.loads(gzip.decompress(p.read_bytes())) if r.get("sj_div") == "CF"] if p.exists() else None


def val(r, col):
    try:
        return float(str(r.get(col) or "").replace(",", ""))
    except ValueError:
        return None


def candidates(code, y, fs, doc, cache):
    nxt, cur = cf_rows(code, y + 1, fs), cf_rows(code, y, fs)
    rows, col = (nxt, "frmtrm_amount") if nxt is not None else (cur, "thstrm_amount")
    out = {}
    if rows:
        dep = [(re.sub(r"\s", "", r.get("account_nm") or ""), abs(val(r, col) or 0)) for r in rows
               if "감가상각" in (r.get("account_nm") or "") and not re.search(r"무형|대손|상각후원가", r.get("account_nm") or "")]
        single = [v for n, v in dep if n in ("감가상각비", "유형자산감가상각비")]
        out["single_row"] = single[0] if single else None
        out["sum_rows"] = sum(v for _, v in dep) if dep else None
    nx = fn._extract(code, y + 1, 0, fs, "frmtrm_amount").get("depreciation")
    cu = fn._extract(code, y, 0, fs, "thstrm_amount").get("depreciation")
    t = nx if nx is not None else cu
    out["xbrl_tag"] = None if t is None else abs(float(t))

    def rows_of(fy):
        if (code, fy) not in cache:
            p = doc.doc_for(code, fy)
            cache[(code, fy)] = doc.dep_rows(doc.load_main(p)) if p else None
        return cache[(code, fy)]
    pk = doc.pick(rows_of(y) or [], fs, fs)
    nk = doc.pick(rows_of(y + 1) or [], fs, fs) if rows_of(y + 1) else None
    out["doc_table"] = nk[1] if (pk and nk and nk[1] is not None and abs(nk[1] - pk[0]) <= max(2e6, abs(pk[0]) * 1e-3)) else None
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--years", default="2023-2024"); a = ap.parse_args()
    y0, y1 = map(int, a.years.split("-"))
    doc = _load("doc_dep", "parse_cf_adj_dep_from_doc_20261013.py")
    conn = connect_primary_db(timeout=300, readonly=True)
    cells = conn.execute("SELECT stock_code, year, report_type, fnguide_value FROM financial_field_verification WHERE field='depreciation' AND quarter=0 "
                         "AND fnguide_value IS NOT NULL AND year BETWEEN ? AND ? ORDER BY 1,2,3", (y0, y1)).fetchall()
    names = ("single_row", "xbrl_tag", "sum_rows", "doc_table")
    st = {n: collections.Counter() for n in names}; cache = {}; rows = []
    for code, y, fs, f in cells:
        f = float(f); rfs = fn.effective_fs(code, y, fs)
        cand = candidates(code, y, rfs, doc, cache)
        row = [code, y, fs, f]
        for n in names:
            v = cand.get(n)
            if v is None:
                st[n]["후보 없음"] += 1; row += [None, None]; continue
            ok = abs(v - f) <= max(2e6, abs(f) * 1e-3)
            st[n]["일치" if ok else "불일치"] += 1; row += [v, ok]
        rows.append(row)
    summary = {"years": a.years, "fnguide_cells": len(cells)}
    for n in names:
        c = st[n]; have = c["일치"] + c["불일치"]
        summary[n] = {"후보 있는 칸": have, "일치": c["일치"], "일치율_후보있는칸": round(100 * c["일치"] / have, 2) if have else None, "후보 없음": c["후보 없음"]}
    # 공통 분모: sum_rows 후보가 있는 칸 전체에서, 다른 정의는 후보가 없으면 '불일치'로 센다(정의가 커버하는 칸 수 차이까지 반영한 비교)
    idx = {n: 4 + 2 * i for i, n in enumerate(names)}
    common = [r for r in rows if r[idx["sum_rows"]] is not None]
    summary["공통분모_sum_rows후보있는칸"] = {"칸": len(common), **{n: sum(1 for r in common if r[idx[n] + 1] is True) for n in names}}
    summary["공통분모_일치율"] = {n: round(100 * summary["공통분모_sum_rows후보있는칸"][n] / len(common), 2) if common else None for n in names}
    tag = a.years.replace("-", "_")
    (OUT / f"dep_definition_vs_fnguide_{tag}_20261013.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    with open(OUT / f"dep_definition_vs_fnguide_{tag}_20261013.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["stock_code", "year", "report_type", "fnguide"] + [x for n in names for x in (n, n + "_match")]); w.writerows(rows)
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
