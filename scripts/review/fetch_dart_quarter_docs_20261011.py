#!/usr/bin/env python3
"""DART 분기·반기보고서 본문 원문 저장 (2026-10-11, 사용자 지시 '분기보고서 본문도 수집해서 별도 분기 파싱') — 대상 한정 백필.

대상(데이터 확장 아님 — 기존 미확정 칸만): 최신 원인군 집계(residual_cause_groups_*.csv)의 DB 없음(별도·연결)·기타 칸 중 분기(1~4) 칸.
  1~3분기 칸 → 그 분기 보고서 + (현금흐름 3개월 차분용) 직전 분기 보고서, 4분기 칸 → 3분기 보고서(누적; 연간은 사업보고서 본문).
보고서 선택: list.json A002(반기)·A003(분기), 보고서명 '(YYYY.MM)' = 기간 종료 월 → fin_norm.fiscal_key_from_period_end로 회계 키(비12월 결산 포함),
  같은 회계 분기는 최신 접수번호(정정본) 사용.
저장: data_raw/dart_doc_q/<code>/<회계연도>Q<분기>_<rcept_no>.zip, 목록 캐시 <code>/_list_q.json(30일).
키·중단: fetch_dart_business_docs와 같은 Keys(한도 020 → 다음 키), 점검·키·시스템 상태(800 등)면 중단·기록.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402
from fetch_dart_business_docs_20261005 import Keys, Quota, corp_map  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc_q")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
GROUPS = ("DB_MISSING_OFS", "DB_MISSING_CFS", "OTHER")


def targets():
    f = sorted(OUT.glob("residual_cause_groups_field_verif_*.csv"))[-1]
    need = set()
    for r in csv.DictReader(open(f)):
        if r["group"] not in GROUPS or r["quarter"] == "0":
            continue
        code, y, q = r["stock_code"], int(r["year"]), int(r["quarter"])
        if q == 4:
            need.add((code, y, 3))
        else:
            need.add((code, y, q))
            if q > 1:
                need.add((code, y, q - 1))
    return sorted(need)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--exit-on-quota", action="store_true"); ap.add_argument("--max-docs", type=int, default=0); a = ap.parse_args()
    need = targets()
    by_code = {}
    for code, y, q in need:
        by_code.setdefault(code, set()).add((y, q))
    conn = connect_primary_db(timeout=300, readonly=True)
    cmap = corp_map(conn)
    conn.close()
    K = Keys()
    n_doc, skipped, nolist = 0, [], []
    print(f"대상 {len(need)} 회계 분기·{len(by_code)}종목", flush=True)
    try:
        for i, (code, want) in enumerate(sorted(by_code.items()), 1):
            cc = cmap.get(code)
            if not cc:
                nolist.append((code, "corp 없음")); continue
            d = RAW / code
            d.mkdir(parents=True, exist_ok=True)
            lp = d / "_list_q.json"
            lst = json.loads(lp.read_text()) if lp.exists() and time.time() - lp.stat().st_mtime < 30 * 86400 else None
            if lst is None:
                lst = []
                for ty in ("A002", "A003"):
                    j = K.get("list.json", {"corp_code": cc, "bgn_de": f"{min(y for y, _ in want) - 1}0101", "pblntf_detail_ty": ty, "page_count": 100})
                    if j.get("status") not in ("000", "013"):
                        raise Quota() if j.get("status") in ("800", "900", "901", "010", "011", "012", "021") else RuntimeError(j)
                    lst += j.get("list", [])
                lp.write_text(json.dumps(lst, ensure_ascii=False))
            latest = {}
            for it in lst:
                m = re.search(r"\((\d{4})\.(\d{2})\)", it.get("report_nm", ""))
                if not m:
                    continue
                fk = fn.fiscal_key_from_period_end(code, m.group(1) + m.group(2), False)
                if fk and fk in want and it["rcept_no"] > latest.get(fk, ""):
                    latest[fk] = it["rcept_no"]
            for fk in want - set(latest):
                nolist.append((code, f"{fk[0]}Q{fk[1]} 보고서 목록에 없음"))
            for (y, q), rc in sorted(latest.items()):
                f = d / f"{y}Q{q}_{rc}.zip"
                if f.exists():
                    continue
                b = K.get("document.xml", {"rcept_no": rc}, binary=True)
                if b[:2] != b"PK":
                    m = re.search(rb"<status>(\d+)</status>", b[:300])
                    st = m.group(1).decode() if m else "?"
                    skipped.append((code, y, q, rc, st))
                    if st in ("800", "900", "901", "010", "011", "012", "021"):
                        print(f"DART 문서 API 상태 {st} — 중단", flush=True)
                        raise Quota()
                    continue
                f.write_bytes(b)
                n_doc += 1
                if a.max_docs and n_doc >= a.max_docs:
                    raise StopIteration
            if i % 20 == 0:
                print(f"{i}/{len(by_code)} 종목, 새 문서 {n_doc}", flush=True)
    except Quota:
        print("DART 키 한도 소진 또는 점검 — 다음 실행에서 이어 받음", flush=True)
        if a.exit_on_quota:
            sys.exit(0)
    except StopIteration:
        pass
    print(json.dumps({"새 문서": n_doc, "건너뜀": len(skipped), "건너뜀 표본": skipped[:8], "목록 없음": len(nolist), "목록 없음 표본": nolist[:8]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
