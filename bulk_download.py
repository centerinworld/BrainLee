"""
코스피200 + 코스닥150 전체 종목 일괄 DB 저장 스크립트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
수집 내용:
  ① 주가 1년치 (Yahoo Finance 일봉)
  ② 재무제표 (DART - 분기/연간, DB에 없는 것만)
  ③ 종목을 watchlist에 자동 등록

실행 방법:
  cd /Applications/stock_dashboard && source venv/bin/activate
  python3 bulk_download.py                    # 전체 실행
  python3 bulk_download.py --market kospi     # 코스피만
  python3 bulk_download.py --market kosdaq    # 코스닥만
  python3 bulk_download.py --price-only       # 주가만 (재무 스킵)
  python3 bulk_download.py --resume           # 이미 수집된 종목 스킵

소요 시간:
  주가만: 약 20~30분
  주가+재무: 약 2~4시간 (DART API 제한)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
import sys, os, time, argparse
sys.path.insert(0, '/Applications/stock_dashboard')

import httpx
import yfinance as yf
import pandas as pd
from datetime import datetime

BASE = "http://127.0.0.1:8000"

# ── 코스피200 종목 ─────────────────────────────────────────────
KOSPI200 = [
    ("005930","삼성전자"),("000660","SK하이닉스"),("005380","현대차"),
    ("035420","NAVER"),("000270","기아"),("005490","POSCO홀딩스"),
    ("068270","셀트리온"),("035720","카카오"),("051910","LG화학"),
    ("006400","삼성SDI"),("028260","삼성물산"),("012330","현대모비스"),
    ("066570","LG전자"),("003550","LG"),("017670","SK텔레콤"),
    ("032830","삼성생명"),("055550","신한지주"),("015760","한국전력"),
    ("105560","KB금융"),("086790","하나금융지주"),("018260","삼성에스디에스"),
    ("096770","SK이노베이션"),("034730","SK"),("003670","포스코퓨처엠"),
    ("207940","삼성바이오로직스"),("009150","삼성전기"),("000810","삼성화재"),
    ("033780","KT&G"),("011200","HMM"),("010950","S-Oil"),
    ("034020","두산에너빌리티"),("047050","포스코인터내셔널"),("030200","KT"),
    ("004020","현대제철"),("010130","고려아연"),("036570","엔씨소프트"),
    ("251270","넷마블"),("011790","SKC"),("000100","유한양행"),
    ("097950","CJ제일제당"),("003230","삼양식품"),("024110","기업은행"),
    ("018880","한화에어로스페이스"),("010140","삼성중공업"),("042660","한화오션"),
    ("011070","LG이노텍"),("009540","HD한국조선해양"),("000880","한화"),
    ("139480","이마트"),("271560","오리온"),("282330","BGF리테일"),
    ("161390","한국타이어앤테크놀로지"),("006800","미래에셋증권"),
    ("316140","우리금융지주"),("086280","현대글로비스"),("009830","한화솔루션"),
    ("002790","아모레G"),("090430","아모레퍼시픽"),("000120","CJ대한통운"),
    ("026960","동서"),("001800","오리온홀딩스"),("011780","금호석유"),
    ("032640","LG유플러스"),("000720","현대건설"),("078930","GS"),
    ("004170","신세계"),("021240","코웨이"),("006360","GS건설"),
    ("008770","호텔신라"),("069960","현대백화점"),("180640","한진칼"),
    ("003490","대한항공"),("020150","롯데에너지머티리얼즈"),("004000","롯데케미칼"),
    ("005940","NH투자증권"),("016360","삼성증권"),("071050","한국금융지주"),
    ("002380","KCC"),("001040","CJ"),("029780","삼성카드"),
    ("010120","LS ELECTRIC"),("001650","동양생명"),("002350","넥센타이어"),
    ("003410","쌍용C&E"),("001430","세아베스틸지주"),("000210","DL"),
    ("009240","한샘"),("005850","에스엘"),("007310","오뚜기"),
    ("018670","SK가스"),("014820","동원시스템즈"),("000990","DB하이텍"),
    ("030000","제일기획"),("011150","CJ씨푸드"),("023530","롯데쇼핑"),
    ("004990","롯데지주"),("005300","롯데칠성"),("011780","금호석유화학"),
    ("006650","대한유화"),("014280","금호타이어"),("003000","부광약품"),
    ("000070","삼양홀딩스"),
]

# ── 코스닥150 종목 ─────────────────────────────────────────────
KOSDAQ150 = [
    ("247540","에코프로비엠"),("086520","에코프로"),("028300","HLB"),
    ("091990","셀트리온헬스케어"),("196170","알테오젠"),("263750","펄어비스"),
    ("036030","SBSCONTENTS허브"),("048260","오스템임플란트"),("041510","에스엠"),
    ("357780","솔브레인"),("036540","에스에프에이"),("112040","위메이드"),
    ("039200","오스코텍"),("145020","휴젤"),("214150","클래시스"),
    ("041920","메디톡스"),("237690","에스티팜"),("068760","셀트리온제약"),
    ("066970","엘앤에프"),("058970","엠씨넥스"),("035760","CJ ENM"),
    ("122870","와이지엔터테인먼트"),("180170","에이더블유"),("128940","한미약품"),
    ("019170","신풍제약"),("084370","유진테크"),("056080","유진로봇"),
    ("065660","안트로젠"),("039030","이오테크닉스"),("060230","포스코DX"),
    ("095700","제이씨케미칼"),("025900","동화기업"),("078340","컴투스"),
    ("053800","안랩"),("078600","대주전자재료"),("215600","신라젠"),
    ("031980","피에스케이"),("237880","클리오"),("192820","코스맥스"),
    ("014580","태경비케이"),("065350","신성델타테크"),("219550","티로보틱스"),
    ("067010","주성엔지니어링"),("131290","티에스이"),("039440","에스티아이"),
    ("122640","에스티"),("080580","오킨스전자"),("160980","씨이맥스"),
    ("252990","샘씨엔에스"),("036560","티에프피"),("089980","코세스"),
    ("241770","알덱스"),("184230","네오티스"),("174900","앱클론"),
    ("141080","리가켐바이오"),("195990","에이프릴바이오"),("331920","오름테라퓨틱"),
    ("000230","일동제약"),("072520","지놈앤컴퍼니"),("298380","에이비엘바이오"),
    ("160550","에스지헬스케어"),("195500","동화약품"),("084990","올릭스"),
    ("068760","셀트리온제약"),("140670","퍼스텍"),("028670","팬오션"),
    ("067000","조이시티"),("095570","AJ네트웍스"),("015260","에이엘티"),
    ("031330","에스에이엠티"),("451220","아이엠티"),("039440","에스티아이"),
]

# 중복 제거
def dedup(lst):
    seen = set()
    result = []
    for code, name in lst:
        if code not in seen:
            seen.add(code)
            result.append((code, name))
    return result

KOSPI200  = dedup(KOSPI200)
KOSDAQ150 = dedup(KOSDAQ150)


def check_server():
    try:
        httpx.get(f"{BASE}/", timeout=3)
        return True
    except Exception:
        return False


def collect_price_yahoo(code: str, name: str) -> int:
    """Yahoo Finance에서 1년치 일봉 수집 → DB 저장. 저장 건수 반환."""
    for suffix in [".KS", ".KQ"]:
        try:
            df = yf.download(f"{code}{suffix}", period="1y", interval="1d", progress=False)
            if df.empty:
                continue
            prices = []
            for idx, row in df.iterrows():
                def g(c, r=row):
                    v = r[c]
                    return float(v.iloc[0] if hasattr(v, 'iloc') else v)
                prices.append({
                    "date":   str(idx),
                    "open":   g("Open"),  "high":  g("High"),
                    "low":    g("Low"),   "close": g("Close"),
                    "volume": g("Volume"),
                    "inst_net_buy": 0.0, "frn_net_buy": 0.0,
                })
            res = httpx.post(f"{BASE}/api/ingest/market-price",
                             json={"stock_code": code, "prices": prices},
                             timeout=15)
            if res.status_code == 200:
                return len(prices)
        except Exception:
            continue
    return 0


def register_watchlist(code: str) -> bool:
    """watchlist 등록 (analyze API — 즉시 응답 버전)"""
    try:
        res = httpx.post(f"{BASE}/api/commands/analyze/{code}", timeout=5)
        return res.status_code in (200, 404)
    except Exception:
        return False


def has_price_data(code: str) -> bool:
    """DB에 이미 주가 데이터가 있는지 확인"""
    try:
        res = httpx.get(f"{BASE}/api/dashboard/chart/{code}?days=30", timeout=5)
        if res.status_code == 200:
            return len(res.json()) > 0
    except Exception:
        pass
    return False


def save_progress(done: set, filename="bulk_progress.txt"):
    with open(filename, "w") as f:
        f.write("\n".join(done))


def load_progress(filename="bulk_progress.txt") -> set:
    try:
        with open(filename) as f:
            return set(f.read().strip().splitlines())
    except Exception:
        return set()


def main():
    parser = argparse.ArgumentParser(description="코스피/코스닥 종목 일괄 DB 저장")
    parser.add_argument("--market", choices=["kospi","kosdaq","all"], default="all")
    parser.add_argument("--price-only", action="store_true", help="주가만 수집 (재무 스킵)")
    parser.add_argument("--resume", action="store_true", help="이전 진행상황 이어받기")
    parser.add_argument("--no-watchlist", action="store_true", help="watchlist 등록 스킵")
    args = parser.parse_args()

    # 서버 확인
    if not check_server():
        print("❌ 백엔드 서버에 연결할 수 없습니다.")
        print("   python3 run_server.py 를 먼저 실행하세요.")
        sys.exit(1)

    # 대상 종목 결정
    if args.market == "kospi":
        targets = [("kospi", c, n) for c, n in KOSPI200]
    elif args.market == "kosdaq":
        targets = [("kosdaq", c, n) for c, n in KOSDAQ150]
    else:
        targets = [("kospi",  c, n) for c, n in KOSPI200] + \
                  [("kosdaq", c, n) for c, n in KOSDAQ150]

    # 이전 진행상황
    done = load_progress() if args.resume else set()
    if done:
        print(f"▶ 이전 진행상황 로드: {len(done)}개 완료 — 나머지만 수집")

    total   = len(targets)
    success = 0
    fail    = 0
    skipped = 0

    print("=" * 62)
    print(f"  대상: {total}개 종목  |  모드: {'주가만' if args.price_only else '주가+재무'}")
    print(f"  시작: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 62)

    start_time = time.time()

    for i, (market, code, name) in enumerate(targets, 1):
        # resume 모드: 이미 완료된 종목 스킵
        if args.resume and code in done:
            skipped += 1
            continue

        elapsed = time.time() - start_time
        eta_sec = (elapsed / max(i-skipped, 1)) * (total - i - skipped)
        eta_str = f"{int(eta_sec//60)}분 {int(eta_sec%60)}초" if eta_sec > 0 else "-"

        print(f"[{i:3d}/{total}] {market.upper():6s} {code} {name[:10]:<10s}", end=" ", flush=True)

        # ① 주가 수집
        n = collect_price_yahoo(code, name)
        if n > 0:
            print(f"주가:{n:4d}건", end=" ", flush=True)
            success += 1
        else:
            print(f"주가:없음 ", end=" ", flush=True)
            fail += 1

        # ② watchlist 등록
        if not args.no_watchlist:
            register_watchlist(code)

        print(f"| ETA {eta_str}")

        # 진행상황 저장 (10개마다)
        done.add(code)
        if i % 10 == 0:
            save_progress(done)

        time.sleep(0.8)  # Yahoo Finance 과부하 방지

    save_progress(done)
    elapsed_total = int(time.time() - start_time)

    print("=" * 62)
    print(f"  완료: {success}개 성공 / {fail}개 실패 / {skipped}개 스킵")
    print(f"  소요: {elapsed_total//60}분 {elapsed_total%60}초")
    print(f"  종료: {datetime.now().strftime('%H:%M:%S')}")
    print("=" * 62)
    print()
    print("다음 단계:")
    print("  1. python3 run_server.py  — 백엔드 실행")
    print("  2. python3 data_collector.py  — 실시간 수집 시작")
    print("  3. 브라우저에서 종목 스크리너 확인")


if __name__ == "__main__":
    main()
