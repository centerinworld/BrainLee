"""
debug_wisereport_post.py — WISEreport ASP.NET UpdatePanel POST 시도
Mac mini에서 실행

    ./venv/bin/python debug_wisereport_post.py
"""
import re
import requests
from bs4 import BeautifulSoup

CODE = "078150"
BASE_URL = (
    f"https://navercomp.wisereport.co.kr/v2/company/c1020001.aspx"
    f"?cmp_cd={CODE}&fin_typ=0&freq_typ=A"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": f"https://finance.naver.com/item/main.naver?code={CODE}",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)

print("=" * 70)
print("[1] GET → __VIEWSTATE 추출 + 스크립트 URL 탐색")
print("=" * 70)

resp = session.get(BASE_URL, timeout=20)
resp.encoding = resp.apparent_encoding or "utf-8"
soup = BeautifulSoup(resp.text, "html.parser")

# ASP.NET 히든 필드 추출
def _hidden(name):
    tag = soup.find("input", {"name": name})
    return tag["value"] if tag and tag.get("value") else ""

vs  = _hidden("__VIEWSTATE")
vsg = _hidden("__VIEWSTATEGENERATOR")
ev  = _hidden("__EVENTVALIDATION")
print(f"__VIEWSTATE length      : {len(vs)}")
print(f"__VIEWSTATEGENERATOR    : {vsg[:40]}")
print(f"__EVENTVALIDATION length: {len(ev)}")

# ScriptManager / UpdatePanel 이름 찾기
sm_tag = soup.find(id=re.compile(r"ScriptManager", re.I))
sm_id  = sm_tag["id"] if sm_tag else "ScriptManager1"
print(f"ScriptManager id: {sm_id}")

# 외부 JS 파일 URL 수집
print("\n── 외부 JS 파일:")
for s in soup.find_all("script", src=True):
    src = s.get("src", "")
    if src:
        print(f"  {src}")

# 인라인 스크립트에서 ajax/fetch 키워드 탐색
print("\n── 인라인 스크립트 (ajax/url 포함 발췌):")
for s in soup.find_all("script"):
    content = s.string or ""
    for kw in ["ajax", "fetch", "XMLHttpRequest", "GetData", "cFData", "cfData",
                "getData", "getFinData", "finData", ".aspx", ".json", ".asmx"]:
        if kw.lower() in content.lower():
            idx = content.lower().find(kw.lower())
            print(f"  [{kw}] ...{content[max(0,idx-60):idx+120]}...")
            break

# ── UpdatePanel POST 시도 ──────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("[2] ASP.NET UpdatePanel POST 시도")
print("=" * 70)

UPDATE_PANELS = [
    "pnlContents", "UpdatePanel1", "TSUpdatePanel1",
    "upFinance", "pnlCF", "Panel1",
]

base_post = {
    "__EVENTTARGET":        "",
    "__EVENTARGUMENT":      "",
    "__VIEWSTATE":          vs,
    "__VIEWSTATEGENERATOR": vsg,
    "__EVENTVALIDATION":    ev,
    "__ASYNCPOST":          "true",
}

for panel in UPDATE_PANELS:
    post_data = {**base_post, f"{sm_id}": f"{sm_id}|{panel}"}
    try:
        r = session.post(
            BASE_URL,
            data=post_data,
            headers={
                **HEADERS,
                "X-MicrosoftAjax":  "Delta=true",
                "Content-Type":     "application/x-www-form-urlencoded; charset=utf-8",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=15,
        )
        found = any(kw in r.text for kw in ["영업활동", "감가상각", "현금흐름"])
        print(f"  POST panel={panel}: HTTP {r.status_code}, len={len(r.text)}, found={found}")
        if found:
            print(f"  ★ 데이터 발견! 앞 500자:\n{r.text[:500]}")
    except Exception as e:
        print(f"  POST panel={panel}: 오류 {e}")

# ── 분기 URL도 동일 시도 ──────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("[3] 분기(freq_typ=Q) GET 테스트")
print("=" * 70)
q_url = BASE_URL.replace("freq_typ=A", "freq_typ=Q")
rq = session.get(q_url, timeout=15)
rq.encoding = rq.apparent_encoding or "utf-8"
found_q = any(kw in rq.text for kw in ["영업활동", "감가상각"])
print(f"  GET 분기: HTTP {rq.status_code}, found={found_q}")
if found_q:
    print(f"  발견: {rq.text[:500]}")

# ── 대안 URL 패턴 시도 ─────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("[4] 대안 URL 패턴")
print("=" * 70)
ALT_URLS = [
    f"https://navercomp.wisereport.co.kr/v2/company/c1020001.ashx?cmp_cd={CODE}&fin_typ=0&freq_typ=A",
    f"https://navercomp.wisereport.co.kr/v2/company/GetCFData.aspx?cmp_cd={CODE}",
    f"https://navercomp.wisereport.co.kr/v2/company/ajax/c1020001.aspx?cmp_cd={CODE}",
    f"https://navercomp.wisereport.co.kr/api/cf?cmp_cd={CODE}",
    f"https://navercomp.wisereport.co.kr/v2/company/cF1020001.aspx?cmp_cd={CODE}&fin_typ=0&freq_typ=A",
]
for url in ALT_URLS:
    try:
        r = session.get(url, timeout=10)
        ct = r.headers.get("Content-Type", "?")
        found = any(kw in r.text for kw in ["영업활동", "감가상각"])
        print(f"  {url.split('wisereport.co.kr')[1]}")
        print(f"    → HTTP {r.status_code}  CT={ct}  found={found}")
        if found:
            print(f"    ★ 발견: {r.text[:400]}")
    except Exception as e:
        print(f"    오류: {e}")

print("\n완료.")
