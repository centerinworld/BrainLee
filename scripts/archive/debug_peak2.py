import sys, json, os
sys.path.insert(0, '/Applications/stock_dashboard')
import config, requests
from bs4 import BeautifulSoup

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
    "Referer": "https://stockeasy.intellio.kr/",
})

# 세션 쿠키 로드
if os.path.exists('/Applications/stock_dashboard/stockeasy_session.json'):
    with open('/Applications/stock_dashboard/stockeasy_session.json') as f:
        for k,v in json.load(f).items():
            s.cookies.set(k, v)

r2 = s.get("https://stockeasy.intellio.kr/strategy-room/peak", timeout=15)
soup = BeautifulSoup(r2.text, "html.parser")
tables = soup.select("table")

print(f"테이블 수: {len(tables)}")
for ti, table in enumerate(tables[:2]):
    print(f"\n[Table {ti}] 전체 행:")
    for j, row in enumerate(table.select("tr")):
        cells = [c.get_text(strip=True) for c in row.select("td,th")]
        print(f"  [{j:02d}] {cells}")

# DB 현재 상태 확인
print("\n=== DB 현재 Peak 데이터 ===")
import httpx
r = httpx.get("http://127.0.0.1:8000/api/trend/holdings", timeout=5)
if r.status_code == 200:
    data = r.json()
    print(f"총 {len(data)}건")
    for h in data:
        print(f"  [{h.get('id')}] {h.get('stock_name')} | 섹터={h.get('sector')} | 활성={h.get('is_active')} | 보유일={h.get('hold_days')}")
