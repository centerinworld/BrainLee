#!/usr/bin/env python3
"""정정공시 점검(읽기 전용, 2026-10-09 사용자 지적 '정정공시가 있다').

대상: financial_field_verification 2021년 이후 1순위 필드 중 '원인 조사' 칸(설명된 예외 포함).
칸마다 우리 원문(data_raw/dart_fnltt/<code>/<year>_<q>_<fs>.json.gz)의 rcept_no와 DART 공시 목록(list.json, 정기공시)에서
그 기간 보고서의 최신 접수번호(정정본 포함)를 비교한다. Q4 칸은 사업보고서(0)와 3분기(3) 두 원문을 본다.
  same_latest       : 원문이 이미 최신(정정본이면 정정본)
  stale_original    : 원문 이후 [기재정정] 등 새 보고서 있음 → 재수집 대상
  no_raw / no_list  : 원문 또는 목록 없음
출력: research_outputs/financial_rereview_20261002/correction_filing_audit_20261009.json (+ 목록 캐시 data_raw/dart_list/<code>.json)
"""
from __future__ import annotations

import collections
import gzip
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
import requests  # noqa: E402

import dart_keys  # noqa: E402
import fiscal_period  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
LIST_CACHE = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_list")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002" / "correction_filing_audit_20261009.json"
FIELDS = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity",
          "operating_cf", "investing_cf", "financing_cf")
KIND = {0: "사업보고서", 1: "분기보고서", 2: "반기보고서", 3: "분기보고서"}
STOP = {"010", "011", "012", "021", "100", "101", "800", "900", "901", "020"}


def corp_list(key, code, corp):
    p = LIST_CACHE / f"{code}.json"
    if p.exists() and time.time() - p.stat().st_mtime < 86400:
        return json.loads(p.read_text())
    out, page = [], 1
    while True:
        d = requests.get("https://opendart.fss.or.kr/api/list.json",
                         params={"crtfc_key": key, "corp_code": corp, "bgn_de": "20210101", "end_de": "20261231",
                                 "pblntf_ty": "A", "page_no": page, "page_count": 100}, timeout=40).json()
        st = d.get("status")
        if st in STOP:
            raise RuntimeError(f"dart_status_{st}")
        if st != "000":
            break
        out += [{k: i[k] for k in ("report_nm", "rcept_no", "rcept_dt")} for i in d.get("list", [])]
        if page >= int(d.get("total_page") or 1):
            break
        page += 1
    LIST_CACHE.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, ensure_ascii=False))
    return out


def period_reports(lst, code, y, q):
    """그 기간 보고서들(접수번호 오름차순). 보고서명 '(YYYY.MM)'의 결산월로 매칭."""
    pe = fiscal_period.period_end(code, y, 4 if q == 0 else q)  # 'YYYY-MM'
    tag = f"({pe[:4]}.{pe[5:7]})"
    kind = KIND[q]
    return sorted([i for i in lst if tag in i["report_nm"] and kind in i["report_nm"]], key=lambda i: i["rcept_no"])


def raw_rcept(code, y, q, fs):
    p = RAW / code / f"{y}_{q}_{fs}.json.gz"
    if not p.exists():
        return None
    rows = json.loads(gzip.decompress(p.read_bytes()))
    return rows[0].get("rcept_no") if rows else None


def main():
    conn = connect_primary_db(readonly=True)
    cells = [tuple(r) for r in conn.execute(f"""SELECT v.stock_code, v.year, v.quarter, v.report_type, v.field, v.db_value, v.fnguide_value,
                COALESCE(e.reason_code,'-') FROM financial_field_verification v
                LEFT JOIN financial_field_exception e USING (stock_code,year,quarter,report_type,field)
                WHERE v.year>=2021 AND v.status='원인 조사' AND v.field IN ({','.join('?' * len(FIELDS))})""", FIELDS).fetchall()]
    conn.close()
    corp = json.load(open(ROOT / "data_cache" / "dart_corp_codes.json"))
    key = dart_keys.ordered_keys()[0]
    lists, st, detail = {}, collections.Counter(), []
    for code in sorted({c[0] for c in cells}):
        if code in corp:
            lists[code] = corp_list(key, code, corp[code])
            time.sleep(0.15)
    for code, y, q, fs, f, dbv, fg, rc in cells:
        if code not in lists:
            st["no_list"] += 1
            continue
        for rq in ((0, 3) if q == 4 else (q,)):
            rr = raw_rcept(code, y, rq, fs)
            reps = period_reports(lists[code], code, y, rq)
            if rr is None:
                verdict = "no_raw"
            elif not reps:
                verdict = "no_list_match"
            elif rr == reps[-1]["rcept_no"]:
                verdict = "same_latest" + ("(정정본)" if "정정" in reps[-1]["report_nm"] else "")
            elif rr in [i["rcept_no"] for i in reps]:
                verdict = "stale_original"
            else:
                verdict = "raw_not_in_list"
            st[verdict] += 1
            if verdict not in ("same_latest",):
                detail.append({"cell": [code, y, q, fs, f], "reason_code": rc, "raw_q": rq, "raw_rcept": rr,
                               "reports": [[i["report_nm"], i["rcept_no"]] for i in reps], "verdict": verdict,
                               "db": dbv, "fnguide": fg})
    res = {"cells": len(cells), "stocks": len(lists), "stats": dict(st),
           "by_verdict_stock": {k: sorted({d["cell"][0] for d in detail if d["verdict"] == k}) for k in {d["verdict"] for d in detail}},
           "detail": detail}
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("cells", "stocks", "stats")}, ensure_ascii=False))
    print({k: len(v) for k, v in res["by_verdict_stock"].items()})


if __name__ == "__main__":
    main()
