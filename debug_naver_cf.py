"""
debug_naver_cf.py — 에이엘티(078150) CF 데이터 URL 진단
Mac mini에서만 실행 (인터넷 필요)

    ./venv/bin/python debug_naver_cf.py
"""
import requests
from bs4 import BeautifulSoup

CODE = "078150"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": f"https://finance.naver.com/item/main.naver?code={CODE}",
}

URLS = [
    ("WISEreport-CF-연간",
     f"https://navercomp.wisereport.co.kr/v2/company/c1020001.aspx?cmp_cd={CODE}&fin_typ=0&freq_typ=A"),
    ("WISEreport-종합",
     f"https://navercomp.wisereport.co.kr/v2/company/c1010001.aspx?cmp_cd={CODE}&cn=&fin_typ=0&freq_typ=A"),
    ("Naver-Mobile-CF-연간",
     f"https://m.stock.naver.com/api/stock/{CODE}/finance/detail?finType=cf&rptType=y"),
    ("Naver-Mobile-CF-분기",
     f"https://m.stock.naver.com/api/stock/{CODE}/finance/detail?finType=cf&rptType=q"),
    ("FnGuide",
     f"https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{CODE}&cID=&MenuYn=Y&ReportGB=D&NewMenuID=103&stkGb=701"),
    ("Naver-CF-iframe",
     f"https://finance.naver.com/item/coinfo.naver?code={CODE}&target=cf"),
]

for name, url in URLS:
    print(f"\n{'='*60}")
    print(f"[{name}]")
    print(f"URL: {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        print(f"HTTP: {r.status_code}  Content-Type: {r.headers.get('Content-Type','?')}")
        ct = r.headers.get("Content-Type", "")
        if "json" in ct:
            import json
            data = r.json()
            print("JSON keys:", list(data.keys()) if isinstance(data, dict) else type(data).__name__)
            print("JSON 앞부분:", str(data)[:500])
        else:
            # HTML → 테이블/키워드 확인
            r.encoding = r.apparent_encoding or "utf-8"
            soup = BeautifulSoup(r.text, "html.parser")
            tables = soup.find_all("table")
            print(f"테이블 수: {len(tables)}")
            # 영업활동 키워드 위치 확인
            full_text = r.text
            for kw in ["영업활동", "감가상각", "depreciation", "operating", "현금흐름"]:
                idx = full_text.find(kw)
                if idx >= 0:
                    print(f"  '{kw}' 발견: ...{full_text[max(0,idx-30):idx+50]}...")
                else:
                    print(f"  '{kw}' 없음")
            print(f"HTML 앞 300자: {r.text[:300]}")
    except Exception as e:
        print(f"오류: {e}")
