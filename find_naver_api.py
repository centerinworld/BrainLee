#!/usr/bin/env python3
"""네이버 증권 올바른 API 엔드포인트 탐색 + DB 직접 삽입"""
import requests, json, sqlite3
from datetime import datetime, timedelta

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}

end_dt   = datetime.now()
start_dt = end_dt - timedelta(days=10)
start_str = start_dt.strftime("%Y%m%d") + "000000"
end_str   = end_dt.strftime("%Y%m%d") + "235959"

# 시도할 후보 URL 목록
candidates = [
    # 신 API 경로들
    f"https://api.stock.naver.com/chart/worldstock/index/NAS/day?startDateTime={start_str}&endDateTime={end_str}",
    f"https://api.stock.naver.com/chart/worldstock/index/NAS/day",
    f"https://api.stock.naver.com/index/NAS/basic",
    f"https://api.stock.naver.com/index/NAS/detail",
    # 모바일 API
    "https://m.stock.naver.com/api/json/worldIndex/getWorldIndexListJson.nhn",
    "https://m.stock.naver.com/api/index/NAS/basic",
    # PC 시세 페이지
    "https://finance.naver.com/world/sise.nhn?symbol=NAS",
]

print("=== API 엔드포인트 탐색 ===\n")
found_url = None
found_data = None

for url in candidates:
    try:
        r = requests.get(url, headers=headers, timeout=10)
        snippet = r.text[:300].replace('\n', ' ')
        print(f"[{r.status_code}] {url}")
        if r.status_code == 200:
            print(f"  → 응답: {snippet}")
            found_url = url
            found_data = r.text
            print()
    except Exception as e:
        print(f"[ERR] {url}: {e}")
    print()

# HTML 페이지에서 현재가 파싱 시도
print("\n=== HTML 시세 페이지 파싱 ===")
html_urls = {
    "NAS": "https://finance.naver.com/world/sise.nhn?symbol=NAS",
    "SPI": "https://finance.naver.com/world/sise.nhn?symbol=SPI",
}
prices = {}
for sym, url in html_urls.items():
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.encoding = "euc-kr"
        # 현재가 파싱
        import re
        # 여러 패턴 시도
        patterns = [
            r'class="num[^"]*"[^>]*>([\d,\.]+)<',
            r'"closePrice"\s*:\s*([\d\.]+)',
            r'"close"\s*:\s*([\d\.]+)',
            r'<span[^>]*class="[^"]*num[^"]*"[^>]*>([\d,\.]+)<',
        ]
        for pat in patterns:
            m = re.search(pat, r.text)
            if m:
                val_str = m.group(1).replace(",", "")
                try:
                    val = float(val_str)
                    if val > 100:  # 지수값 최소
                        prices[sym] = val
                        print(f"{sym}: {val} (패턴: {pat[:40]})")
                        break
                except:
                    pass
        if sym not in prices:
            print(f"{sym}: 파싱 실패 (status={r.status_code})")
            print(f"  첫 500자: {r.text[:500]}")
    except Exception as e:
        print(f"{sym}: 오류 - {e}")
