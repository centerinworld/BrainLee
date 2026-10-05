#!/usr/bin/env python3
"""DART 사업보고서 본문 원문 저장(2021~ 연간, 2026-10-05 사용자 요청 "21년부터 수집").

용도: 본문에만 있는 항목 — 연구개발비, 생산능력·가동률, 주요 원재료 가격, 내수/수출 매출, 제품별 매출, 지역별 매출 주석(2021~22 XBRL은 주석 태그 없음).
저장: /Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc/<code>/<회계연도>_<rcept_no>.zip (DART document.xml 응답 그대로)
      목록 캐시 <code>/_list.json (list.json 사업보고서 A001, 정정 포함 — 회계연도별 최신 접수번호만 받는다)
키: scripts/review/dart_keys.py 순서(KEY1→3→4→2, KEY2 일괄 상한). 한도(020) 소진 시 다음 키, 전부 소진 시 종료(--exit-on-quota) → 다음 밤 이어서.
회계연도 = 보고서명 (YYYY.MM)의 YYYY(결산 종료 연도, §2-7과 같은 규칙). 정정 보고서가 나오면 새 접수본을 추가 저장하고 옛 원문도 보존(파서는 연도별 최신 접수번호 사용).
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
import dart_keys  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_doc")
API = "https://opendart.fss.or.kr/api"


class Quota(Exception):
    pass


class Keys:
    def __init__(self):
        self.keys = dart_keys.ordered_keys()
        self.i = 0

    def get(self, ep, params, binary=False):
        while self.i < len(self.keys):
            k = self.keys[self.i]
            if not dart_keys.allow(k):
                self.i += 1
                continue
            time.sleep(0.26)
            try:
                r = requests.get(f"{API}/{ep}", params={**params, "crtfc_key": k}, timeout=90)
            except requests.RequestException:
                time.sleep(3)
                continue
            head = r.content[:300]
            if b"<status>020</status>" in head or b'"status":"020"' in head or b'"status": "020"' in head:
                self.i += 1
                continue
            return r.content if binary else r.json()
        raise Quota()


def corp_map(conn):
    sys.path.insert(0, str(ROOT / "collectors"))
    from dart_product_mix_collector import load_corp_map
    codes = [r[0] for r in conn.execute("SELECT stock_code FROM stock_universe WHERE market IN ('KOSPI','KOSDAQ')").fetchall()]
    return load_corp_map(codes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-year", type=int, default=2021)
    ap.add_argument("--max-docs", type=int, default=0)
    ap.add_argument("--codes", default="")
    ap.add_argument("--exit-on-quota", action="store_true")
    ap.add_argument("--years", default="", help="받을 회계연도(예 2021,2022). 사용자 지시 2026-10-05: XBRL 주석이 없는 2021~2022년부터")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=300, readonly=True)
    codes = [c.strip() for c in a.codes.split(",") if c.strip()] or [r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_universe WHERE market IN ('KOSPI','KOSDAQ') AND stock_code ~ '^[0-9]{5}0$' ORDER BY stock_code").fetchall()]
    cmap = corp_map(conn)
    conn.close()
    K = Keys()
    n_doc = 0
    try:
        for i, code in enumerate(codes, 1):
            cc = cmap.get(code)
            if not cc:
                continue
            d = RAW / code
            d.mkdir(parents=True, exist_ok=True)
            lp = d / "_list.json"
            lst = json.loads(lp.read_text()) if lp.exists() else None
            if lst is None or time.time() - lp.stat().st_mtime > 30 * 86400:
                j = K.get("list.json", {"corp_code": cc, "bgn_de": f"{a.from_year + 1}0101", "pblntf_detail_ty": "A001", "page_count": 100})
                lst = j.get("list", []) if j.get("status") in ("000", "013") else []
                lp.write_text(json.dumps(lst, ensure_ascii=False))
            latest = {}
            for it in lst:
                m = re.search(r"\((\d{4})\.(\d{2})\)", it.get("report_nm", ""))
                if not m or "사업보고서" not in it.get("report_nm", ""):
                    continue
                fy = int(m.group(1))
                if fy >= a.from_year and it["rcept_no"] > latest.get(fy, ""):
                    latest[fy] = it["rcept_no"]
            want = {int(y) for y in a.years.split(",") if y.strip()}
            for fy, rc in sorted(latest.items()):
                if want and fy not in want:
                    continue
                f = d / f"{fy}_{rc}.zip"
                if f.exists():
                    continue
                b = K.get("document.xml", {"rcept_no": rc}, binary=True)
                if b[:2] != b"PK":
                    continue
                f.write_bytes(b)
                n_doc += 1
                if a.max_docs and n_doc >= a.max_docs:
                    raise StopIteration
            if i % 100 == 0:
                print(f"{i}/{len(codes)} 종목, 새 문서 {n_doc}", flush=True)
    except Quota:
        print("DART 키 한도 모두 소진 — 다음 실행에서 이어 받음", flush=True)
        if a.exit_on_quota:
            sys.exit(0)
    except StopIteration:
        pass
    print("완료, 새 문서", n_doc, flush=True)


if __name__ == "__main__":
    main()
