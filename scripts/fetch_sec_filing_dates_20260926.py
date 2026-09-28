"""
fetch_sec_filing_dates_20260926.py
SEC EDGAR 공식 API로 미국 종목 실제 10-Q/10-K 접수일(filingDate)을 가져와
us_financial_data.avail_date를 업데이트한다.

- ticker → CIK: https://www.sec.gov/files/company_tickers.json
- 제출이력:     https://data.sec.gov/submissions/CIK{cik10}.json
- SEC rate limit: 초당 10회, User-Agent 필수

실행:
    venv/bin/python3 scripts/fetch_sec_filing_dates_20260926.py [--apply] [--limit N]
"""
import argparse
import json
import logging
import sys
import time
import urllib.request
from pathlib import Path
from datetime import date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
from db_compat import connect_primary_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

UA = "stock-dashboard-research center.in.world@gmail.com"
CACHE_PATH = Path("/tmp/sec_filing_dates_cache.json")
INTERVAL   = 0.12   # 초당 ~8회 (SEC 한도 10회 이하 유지)


def _fetch_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def get_ticker_cik_map() -> dict[str, str]:
    """SEC 공식 ticker→CIK10 매핑 다운로드."""
    log.info("SEC ticker→CIK 맵 다운로드...")
    data = _fetch_json("https://www.sec.gov/files/company_tickers.json")
    # {str_idx: {cik_str, ticker, title}}
    return {v['ticker'].upper(): str(v['cik_str']).zfill(10) for v in data.values()}


def get_filing_dates_for_cik(cik10: str) -> list[dict]:
    """
    CIK 기준 10-Q/10-K 제출 이력 반환.
    [{'form': '10-Q', 'reportDate': '2026-03-28', 'filingDate': '2026-05-01'}, ...]
    """
    url = f"https://data.sec.gov/submissions/CIK{cik10}.json"
    try:
        sub = _fetch_json(url)
    except Exception as e:
        log.debug(f"CIK {cik10} 조회 실패: {e}")
        return []

    results = []
    recent = sub.get('filings', {}).get('recent', {})
    forms        = recent.get('form', [])
    filing_dates = recent.get('filingDate', [])
    report_dates = recent.get('reportDate', [])

    accept_datetimes = recent.get('acceptanceDateTime', [])
    for f, fd, rd, adt in zip(forms, filing_dates, report_dates,
                               accept_datetimes if accept_datetimes else ['']*len(forms)):
        if f in ('10-Q', '10-K'):
            results.append({'form': f, 'reportDate': rd, 'filingDate': fd,
                            'acceptanceDateTime': adt})

    # older filings (files 배열)
    for older_ref in sub.get('filings', {}).get('files', []):
        try:
            older_url = f"https://data.sec.gov/submissions/{older_ref['name']}"
            older = _fetch_json(older_url)
            o_forms = older.get('form', [])
            o_fd    = older.get('filingDate', [])
            o_rd    = older.get('reportDate', [])
            o_adt   = older.get('acceptanceDateTime', [])
            for f, fd, rd, adt in zip(o_forms, o_fd, o_rd,
                                      o_adt if o_adt else ['']*len(o_forms)):
                if f in ('10-Q', '10-K'):
                    results.append({'form': f, 'reportDate': rd, 'filingDate': fd,
                                   'acceptanceDateTime': adt})
            time.sleep(INTERVAL)
        except Exception:
            pass

    return results


def _next_trading_day(d: date) -> date:
    """주말이면 다음 월요일, 평일이면 그대로."""
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def match_period_to_filing(period_end: str, filings: list[dict]) -> str | None:
    """
    period_end (YYYY-MM-DD) 에 가장 가까운 reportDate를 가진 filing의
    실효 공시일을 반환.

    실효 공시일 결정 규칙:
      1. acceptanceDateTime이 있으면 사용 (SEC 실제 수리 시각)
         - 16:00 ET(21:00 UTC) 이전 수리 → 당일 반영
         - 16:00 ET 이후 수리 → 다음 거래일 반영 (주말 제외)
      2. 없으면 filingDate 사용

    매핑 허용 오차: ±60일 (비표준 회계연도 대응)
    """
    if not filings or not period_end:
        return None

    try:
        pe = date.fromisoformat(period_end)
    except ValueError:
        return None

    best_avail = None
    best_diff  = timedelta(days=999)

    for item in filings:
        rd_str = item.get('reportDate', '')
        if not rd_str:
            continue
        try:
            rd = date.fromisoformat(rd_str)
        except ValueError:
            continue

        diff = abs(rd - pe)
        if diff > timedelta(days=60):
            continue

        # 실효 공시일 계산
        accept_dt = item.get('acceptanceDateTime', '')  # 예: "2026-07-31T10:14:09.000Z"
        if accept_dt and len(accept_dt) >= 16:
            try:
                accept_date = date.fromisoformat(accept_dt[:10])
                hour_utc    = int(accept_dt[11:13])
                # ET = UTC-4(summer)/UTC-5(winter). 16:00 ET ≈ 20:00-21:00 UTC
                # 보수적으로 20:00 UTC 이후면 다음 거래일
                if hour_utc >= 20:
                    avail = _next_trading_day(accept_date + timedelta(days=1))
                else:
                    avail = _next_trading_day(accept_date)
                avail_str = avail.isoformat()
            except Exception:
                avail_str = item.get('filingDate', '')
        else:
            avail_str = item.get('filingDate', '')

        if diff < best_diff:
            best_diff  = diff
            best_avail = avail_str

    return best_avail


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply",  action="store_true", help="DB에 실제 반영")
    parser.add_argument("--limit",  type=int, default=0, help="테스트용 종목 수 제한")
    parser.add_argument("--resume", action="store_true", help="캐시 파일에서 이어서 실행")
    args = parser.parse_args()

    # 캐시 로드 또는 초기화
    if args.resume and CACHE_PATH.exists():
        cache: dict = json.loads(CACHE_PATH.read_text())
        log.info(f"캐시 로드: {len(cache):,}개 ticker 이미 처리됨")
    else:
        cache = {}

    # 1. SEC CIK 맵
    ticker_cik = get_ticker_cik_map()
    time.sleep(INTERVAL)

    # 2. DB에서 처리할 ticker 목록
    with connect_primary_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT ticker FROM us_financial_data
            WHERE period_end IS NOT NULL
            ORDER BY ticker
        """)
        tickers = [r[0] for r in cur.fetchall()]

    if args.limit:
        tickers = tickers[:args.limit]

    log.info(f"처리 대상: {len(tickers):,}개 ticker")

    # 3. 종목별 EDGAR 조회 (캐시 활용)
    missed_cik = 0
    for i, ticker in enumerate(tickers):
        if ticker in cache:
            continue
        cik = ticker_cik.get(ticker.upper())
        if not cik:
            cache[ticker] = []
            missed_cik += 1
            continue

        filings = get_filing_dates_for_cik(cik)
        cache[ticker] = filings
        time.sleep(INTERVAL)

        if (i + 1) % 100 == 0:
            CACHE_PATH.write_text(json.dumps(cache))
            log.info(f"  {i+1:,}/{len(tickers):,}  캐시 저장 (CIK 미매핑 {missed_cik}개)")

    CACHE_PATH.write_text(json.dumps(cache))
    log.info(f"전체 조회 완료. CIK 미매핑: {missed_cik:,}개")

    # 4. DB 업데이트 준비
    with connect_primary_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT ticker, period_end, period_type
            FROM us_financial_data
            WHERE period_end IS NOT NULL
        """)
        rows = cur.fetchall()

    updates = []
    unchanged = 0
    no_match  = 0

    for ticker, period_end, period_type in rows:
        filings = cache.get(ticker, [])
        new_avail = match_period_to_filing(period_end, filings)
        if new_avail:
            updates.append((new_avail, ticker, period_end, period_type))
        else:
            no_match += 1

    log.info(f"매핑 결과: 업데이트={len(updates):,}  미매핑={no_match:,}")

    # 5. 실제 반영
    if not args.apply:
        # dry-run: 샘플 출력
        print("\n[DRY-RUN] 변경 샘플 (첫 20개):")
        with connect_primary_db() as conn:
            cur = conn.cursor()
            sample_tickers = ['AAPL','NVDA','MSFT','TSLA','META','GOOGL','AMZN','NFLX']
            for t in sample_tickers:
                cur.execute("""
                    SELECT period_end, period_type, avail_date
                    FROM us_financial_data
                    WHERE ticker=%s ORDER BY period_end DESC LIMIT 4
                """, (t,))
                for r in cur.fetchall():
                    # 새 avail_date 찾기
                    new_a = next(
                        (u[0] for u in updates if u[1]==t and u[2]==r[0] and u[3]==r[1]),
                        None
                    )
                    lag_old = f"(+{(date.fromisoformat(r[2]) - date.fromisoformat(r[0])).days}d)" if r[2] else ""
                    lag_new = f"(+{(date.fromisoformat(new_a) - date.fromisoformat(r[0])).days}d)" if new_a else ""
                    change = f"  {r[0]} {r[1]:8s}  현재={r[2]} {lag_old}  →  SEC={new_a or '(미매핑)'} {lag_new}"
                    print(f"  {t:6s} {change}")
        print(f"\n--apply 플래그 추가 시 {len(updates):,}행 업데이트됩니다.")
        return

    # apply 모드
    with connect_primary_db() as conn:
        cur = conn.cursor()
        batch = 0
        for new_avail, ticker, period_end, period_type in updates:
            cur.execute("""
                UPDATE us_financial_data
                SET avail_date = %s
                WHERE ticker = %s AND period_end = %s AND period_type = %s
            """, (new_avail, ticker, period_end, period_type))
            batch += 1
            if batch % 5000 == 0:
                conn.commit()
                log.info(f"  {batch:,}행 커밋")
        conn.commit()
        log.info(f"완료: {batch:,}행 avail_date 업데이트")

    # 결과 통계
    with connect_primary_db() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                AVG(CAST(SUBSTRING(avail_date,9,2) AS INT) +
                    (CAST(SUBSTRING(avail_date,6,2) AS INT) - CAST(SUBSTRING(period_end,6,2) AS INT)) * 30
                ) as rough_avg_lag
            FROM us_financial_data
            WHERE avail_date IS NOT NULL AND period_end IS NOT NULL
              AND period_type = 'quarter'
        """)
        r = cur.fetchone()
        log.info(f"분기 평균 지연(개략): {r[0]:.1f}일")


if __name__ == "__main__":
    main()
