"""
debug_dart_cf.py — DART CF 계정명 전체 출력 + 추가 URL 탐색
Mac mini에서 실행

    ./venv/bin/python debug_dart_cf.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import config
import requests

CODE = "078150"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ═══════════════════════════════════════════════
# 1. DART CF 계정명 전체 출력
# ═══════════════════════════════════════════════
print("\n" + "="*70)
print("[1] DART CF 계정명 전체 출력 (에이엘티 078150)")
print("="*70)

try:
    import OpenDartReader as _ODR
    dart = _ODR(config.DART_API_KEY)

    for year in [2024, 2023, 2022]:
        for rprt_code, label in [("11011", "사업보고서"), ("11014", "3분기")]:
            for fs_div in ["CFS", "OFS"]:
                try:
                    df = dart.finstate_all(CODE, year, rprt_code, fs_div=fs_div)
                    if df is None or df.empty:
                        continue
                    accs = df["account_nm"].astype(str)
                    # 영업활동이 있는 경우만 (= CF 데이터)
                    if not accs.str.contains("영업활동").any():
                        continue
                    print(f"\n★ {year}년 {label} [{fs_div}] — 전체 계정명:")
                    for _, row in df.iterrows():
                        acc = str(row.get("account_nm", "")).strip()
                        val = str(row.get("thstrm_amount", "")).strip()
                        ind = str(row.get("indent_level", "")).strip()
                        print(f"  level={ind:>2}  '{acc}'  = {val}")
                    print(f"  → 총 {len(df)}행")
                    break   # CFS 성공하면 OFS 스킵
                except Exception as e:
                    print(f"  [{fs_div}] {year} {label} 오류: {e}")
            else:
                continue
            break
except Exception as e:
    print(f"DART 초기화 실패: {e}")

# ═══════════════════════════════════════════════
# 2. 추가 URL 패턴 탐색
# ═══════════════════════════════════════════════
print("\n\n" + "="*70)
print("[2] 추가 URL 탐색")
print("="*70)

EXTRA_URLS = [
    # Naver Finance PC - income / balance / CF tabs
    ("Naver-Mobile-income",
     f"https://m.stock.naver.com/api/stock/{CODE}/finance/detail?finType=income&rptType=y"),
    ("Naver-Mobile-balance",
     f"https://m.stock.naver.com/api/stock/{CODE}/finance/detail?finType=balance&rptType=y"),
    # 다른 Naver Mobile 패턴
    ("Naver-Mobile-finance",
     f"https://m.stock.naver.com/api/stock/{CODE}/finance"),
    ("Naver-Mobile-finance2",
     f"https://m.stock.naver.com/api/stock/{CODE}/finance?finType=cf"),
    # WISEreport 다른 엔드포인트
    ("WISEreport-AJAX-CF",
     f"https://navercomp.wisereport.co.kr/v2/company/c1020001.aspx?cmp_cd={CODE}&fin_typ=0&freq_typ=A&shl=1"),
    # Naver Finance coinfo JSON
    ("Naver-coinfo-json",
     f"https://finance.naver.com/item/coinfo.naver?code={CODE}&target=cf&finType=cf"),
    # WISEreport 종합 - 첫 3000자 더 자세히
    ("WISEreport-종합-detail",
     f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={CODE}&cn=&fin_typ=0&freq_typ=A"),
]

from bs4 import BeautifulSoup
import json

for name, url in EXTRA_URLS:
    print(f"\n{'─'*60}")
    print(f"[{name}]")
    print(f"URL: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        ct = r.headers.get("Content-Type", "?")
        print(f"HTTP: {r.status_code}  Content-Type: {ct}")

        if r.status_code != 200:
            print(f"  → skip (non-200)")
            continue

        if "json" in ct:
            try:
                data = r.json()
                print(f"JSON keys: {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
                print(f"JSON 앞부분: {str(data)[:800]}")
            except Exception:
                print(f"  JSON 파싱 실패: {r.text[:200]}")
        else:
            r.encoding = r.apparent_encoding or "utf-8"
            full = r.text

            if name == "WISEreport-종합-detail":
                # 상세 테이블 분석
                soup = BeautifulSoup(full, "html.parser")
                tables = soup.find_all("table")
                print(f"테이블 수: {len(tables)}")
                for i, tbl in enumerate(tables):
                    txt = tbl.get_text(separator="|", strip=True)
                    if any(kw in txt for kw in ["영업활동", "현금흐름", "감가상각", "CF"]):
                        print(f"\n  [테이블 {i}] 관련 내용:")
                        print(f"  {txt[:600]}")
                continue

            # 일반 URL: 키워드 탐색
            for kw in ["영업활동", "감가상각", "현금흐름", "cf", "cash"]:
                idx = full.lower().find(kw.lower())
                if idx >= 0:
                    print(f"  '{kw}' 발견: ...{full[max(0,idx-30):idx+80]}...")
                else:
                    print(f"  '{kw}' 없음")

    except Exception as e:
        print(f"오류: {e}")

print("\n\n완료.")
