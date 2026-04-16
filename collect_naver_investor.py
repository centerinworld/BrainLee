"""
collect_naver_investor.py — 네이버 금융 기관/외국인 순매수 히스토리 수집

네이버 금융 /item/frgn.naver 에서 종목별 기관/외국인 순매수(수량) 일별 히스토리.
  - 페이지당 20행, 263페이지 = 약 20년치 (2005년~현재)
  - 3년치 = 약 38페이지 / 5년치 = 약 63페이지

저장:
  price_history.inst_net_buy  (기관 순매수 수량)
  price_history.frn_net_buy   (외국인 순매수 수량)
  ※ 이미 0이 아닌 값이 있는 날짜는 덮어쓰지 않음

사용법:
  python3 collect_naver_investor.py                    # 전종목 3년치 (38페이지)
  python3 collect_naver_investor.py --years 5          # 전종목 5년치
  python3 collect_naver_investor.py --pages 263        # 전종목 전체 (20년)
  python3 collect_naver_investor.py --codes 005930,000660  # 특정 종목만
  python3 collect_naver_investor.py --limit 100        # 시총 상위 100종목만
  python3 collect_naver_investor.py --years 3 --force  # 기존 값도 덮어쓰기

소요 시간 (페이지당 0.25초 × 38페이지):
  시총 상위 200종목 3년치 → 약 30분
  전종목(4000개) 3년치    → 약 10시간 (야간 실행 권장)
"""

import argparse
import re
import sqlite3
import time
import sys
import os
from datetime import datetime, date, timedelta

import requests
from bs4 import BeautifulSoup

DB_PATH   = "/Applications/stock_dashboard/stock.db"
BASE_URL  = "https://finance.naver.com/item/frgn.naver"
SLEEP_PAGE = 0.25   # 페이지 요청 간격 (초)
SLEEP_STOCK = 0.5   # 종목 전환 간격 (초)


def _make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent":     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer":        "https://finance.naver.com/",
        "Accept-Language": "ko-KR,ko;q=0.9",
    })
    return s


def _scrape_page(session: requests.Session, code: str, page: int) -> list[dict]:
    """한 페이지 스크래핑. 반환: [{date, inst_net_buy, frn_net_buy}, ...]"""
    try:
        r = session.get(BASE_URL, params={"code": code, "page": page}, timeout=15)
        r.encoding = "euc-kr"
    except Exception as e:
        print(f"    [네이버] {code} p{page} 요청 오류: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []

    for tbl in soup.find_all("table"):
        hdrs = [th.get_text(strip=True) for th in tbl.find_all("th")]
        if "날짜" not in hdrs or "기관" not in hdrs:
            continue
        for row in tbl.find_all("tr")[1:]:
            cols = [td.get_text(strip=True) for td in row.find_all("td")]
            if not cols or not cols[0] or cols[0] == "날짜":
                continue
            try:
                date_str = cols[0].replace(".", "-")  # "2026.04.15" → "2026-04-15"
                if len(date_str) != 10:
                    continue
                # 기관(cols[5]), 외국인(cols[6])
                def _to_int(s):
                    s = s.replace(",", "").replace("+", "").replace(" ", "")
                    if s in ("", "-", "0"):
                        return 0
                    return int(s)
                inst = _to_int(cols[5]) if len(cols) > 5 else 0
                frn  = _to_int(cols[6]) if len(cols) > 6 else 0
                results.append({"date": date_str, "inst_net_buy": inst, "frn_net_buy": frn})
            except Exception:
                pass
        break  # 원하는 테이블 찾으면 종료

    return results


def _get_last_page(session: requests.Session, code: str) -> int:
    """종목의 마지막 페이지 번호 조회."""
    try:
        r = session.get(BASE_URL, params={"code": code, "page": 1}, timeout=15)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"page=\d+"))
        pages = [int(re.search(r"page=(\d+)", a["href"]).group(1)) for a in links]
        return max(pages) if pages else 1
    except Exception:
        return 1


def collect_stock(session: requests.Session, conn: sqlite3.Connection,
                  stock_code: str, max_pages: int, force: bool = False) -> int:
    """
    한 종목의 수급 히스토리 수집.
    - 수집 중단 날짜: 이미 데이터가 있는 날짜가 10개 이상 연속 나오면 중지 (--force 아닐 때)
    반환: 저장된 행 수
    """
    saved = 0
    consecutive_existing = 0

    for page in range(1, max_pages + 1):
        rows = _scrape_page(session, stock_code, page)
        if not rows:
            break  # 빈 페이지 = 끝

        for row in rows:
            d    = row["date"]
            inst = row["inst_net_buy"]
            frn  = row["frn_net_buy"]

            if inst == 0 and frn == 0:
                continue  # 수급 없는 행 스킵

            # price_history에 OHLCV 행이 있는지 확인
            exists = conn.execute(
                "SELECT inst_net_buy, frn_net_buy FROM price_history "
                "WHERE stock_code=? AND date=?", (stock_code, d)
            ).fetchone()

            if not exists:
                continue  # OHLCV 없는 날짜는 스킵

            existing_inst = exists[0] or 0
            existing_frn  = exists[1] or 0

            if not force and (existing_inst != 0 or existing_frn != 0):
                consecutive_existing += 1
                if consecutive_existing >= 10:
                    return saved  # 이미 수집된 구간 도달 → 조기 종료
                continue
            else:
                consecutive_existing = 0

            conn.execute("""
                UPDATE price_history
                SET inst_net_buy = ?,
                    frn_net_buy  = ?
                WHERE stock_code = ? AND date = ?
                  AND (? OR inst_net_buy IS NULL OR inst_net_buy = 0)
            """, (inst, frn, stock_code, d, force))
            saved += conn.execute("SELECT changes()").fetchone()[0]

        time.sleep(SLEEP_PAGE)

    return saved


def get_universe_codes(conn: sqlite3.Connection, limit: int | None = None) -> list[str]:
    rows = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
          AND (stock_type IS NULL OR stock_type = '보통주')
        ORDER BY market_cap DESC NULLS LAST
    """).fetchall()
    codes = [r[0] for r in rows]
    return codes[:limit] if limit else codes


def years_to_pages(years: float) -> int:
    """연 단위를 페이지 수로 변환 (1페이지 ≈ 20거래일 ≈ 1개월)."""
    trading_days = int(years * 250)
    return max(1, (trading_days + 19) // 20)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years",  type=float, default=3.0,
                        help="수집 기간 (년, 기본 3)")
    parser.add_argument("--pages",  type=int,   default=0,
                        help="최대 페이지 수 (0=years 자동 계산)")
    parser.add_argument("--codes",  default="",
                        help="특정 종목: 005930,000660")
    parser.add_argument("--limit",  type=int,   default=0,
                        help="시총 상위 N종목만 (0=전체)")
    parser.add_argument("--force",  action="store_true",
                        help="기존 데이터도 덮어쓰기")
    parser.add_argument("--sleep-page",  type=float, default=SLEEP_PAGE)
    parser.add_argument("--sleep-stock", type=float, default=SLEEP_STOCK)
    args = parser.parse_args()

    sleep_page  = args.sleep_page
    sleep_stock = args.sleep_stock
    # 모듈 전역 변수 갱신 (collect_stock 내부에서 참조)
    import collect_naver_investor as _self
    _self.SLEEP_PAGE  = sleep_page
    _self.SLEEP_STOCK = sleep_stock
    max_pages = args.pages if args.pages > 0 else years_to_pages(args.years)

    sys.path.insert(0, "/Applications/stock_dashboard")
    os.chdir("/Applications/stock_dashboard")

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        codes = get_universe_codes(conn, limit=args.limit or None)

    session = _make_session()
    total_codes  = len(codes)
    total_saved  = 0
    total_skip   = 0
    total_err    = 0

    est_min = total_codes * max_pages * sleep_page / 60
    print(f"네이버 수급 수집 시작: {total_codes}종목 × {max_pages}페이지 ({args.years:.1f}년치)")
    print(f"예상 소요 시간: 약 {est_min:.0f}분  (--force={args.force})")
    print(f"기존 데이터가 있는 날짜는 {'덮어씁니다' if args.force else '건너뜁니다 (조기 종료 적용)'}.\n")

    for i, code in enumerate(codes):
        try:
            n = collect_stock(session, conn, code, max_pages, force=args.force)
            if n > 0:
                total_saved += n
                print(f"  [{i+1}/{total_codes}] {code} +{n}행")
            else:
                total_skip += 1
                if (i + 1) % 100 == 0:
                    print(f"  [{i+1}/{total_codes}] ... 스킵 누계 {total_skip}")
        except KeyboardInterrupt:
            print("\n사용자 중단")
            break
        except Exception as e:
            total_err += 1
            print(f"  [{i+1}/{total_codes}] {code} 오류: {e}")

        if (i + 1) % 30 == 0:
            conn.commit()
            print(f"  ── 진행 {i+1}/{total_codes} | 저장 {total_saved:,}행 | 오류 {total_err} ──")

        time.sleep(sleep_stock)

    conn.commit()
    conn.close()
    print(f"\n완료! 저장 {total_saved:,}행 / 스킵 {total_skip} / 오류 {total_err}")


if __name__ == "__main__":
    main()
