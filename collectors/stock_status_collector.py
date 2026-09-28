"""
collectors/stock_status_collector.py — 거래정지·관리종목·투자경고/위험/환기 수집

네이버 금융 목록 페이지 스크래핑:
  - https://finance.naver.com/sise/management.naver       (관리종목)
  - https://finance.naver.com/sise/trading_halt.naver     (거래정지)
  - https://finance.naver.com/sise/investWarning.naver    (투자경고/위험/환기)

stock_universe 테이블에 저장:
  is_halt    INTEGER DEFAULT 0   (거래정지)
  is_admin   INTEGER DEFAULT 0   (관리종목)
  warn_type  TEXT    DEFAULT NULL ('환기'|'경고'|'위험')
  status_updated TEXT             (마지막 수집 일시)

사용법:
  python3 -m collectors.stock_status_collector
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DB_PATH  = "/Applications/stock_dashboard/stock.db"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}
SLEEP    = 0.8


# ── DB 준비 ──────────────────────────────────────────────
def _ensure_columns(conn: sqlite3.Connection):
    """stock_universe에 상태 컨럼이 없으면 추가."""
    cur = conn.execute("PRAGMA table_info(stock_universe)")
    existing = {row[1] for row in cur.fetchall()}
    for col, ddl in [
        ("is_halt",        "INTEGER DEFAULT 0"),
        ("is_admin",       "INTEGER DEFAULT 0"),
        ("warn_type",      "TEXT"),
        ("status_updated", "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE stock_universe ADD COLUMN {col} {ddl}")
            logger.info(f"[Status] stock_universe.{col} 컨럼 추가")
    conn.commit()


# ── 스크래핑 헬퍼 ──────────────────────────────────────────────
def _fetch_codes_from_page(url: str) -> set[str]:
    """네이버 금융 목록 페이지에서 6자리 종목코드 추출."""
    codes: set[str] = set()
    page = 1
    while True:
        try:
            r = requests.get(url, params={"page": page}, headers=HEADERS, timeout=10)
            r.encoding = "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")

            found = False
            for a in soup.select("table a[href*='code=']"):
                href = a.get("href", "")
                code = href.split("code=")[-1][:6]
                if code.isdigit() and len(code) == 6:
                    codes.add(code)
                    found = True

            if not found:
                break
            # 페이지 네비게이션 확인 — "다음" 링크가 없으면 마지막 페이지
            next_link = soup.select_one("a.pgRR, td.pgRR a")
            if not next_link:
                break
            page += 1
            time.sleep(SLEEP)
        except Exception as e:
            logger.warning(f"[Status] {url} page={page} 오류: {e}")
            break
    return codes


def _fetch_warn_codes() -> dict[str, str]:
    """투자경고/위험/환기 종목 코드 → warn_type 반환."""
    url = "https://finance.naver.com/sise/investWarning.naver"
    result: dict[str, str] = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        r.encoding = "euc-kr"
        soup = BeautifulSoup(r.text, "html.parser")

        for tr in soup.select("table.type_1 tr, table tr"):
            tds = tr.find_all("td")
            if len(tds) < 3:
                continue
            # 종목코드 추출
            code = None
            for td in tds:
                a = td.find("a", href=True)
                if a and "code=" in a["href"]:
                    code = a["href"].split("code=")[-1][:6]
                    break
            if not code or not code.isdigit():
                continue

            # 구분 텍스트 (투자경고/투자위험/투자주의환기)
            for td in tds:
                txt = td.get_text(strip=True)
                if "위험" in txt:
                    result[code] = "위험"
                    break
                elif "경고" in txt:
                    result[code] = "경고"
                    break
                elif "환기" in txt or "주의" in txt:
                    result[code] = "환기"
                    break
    except Exception as e:
        logger.warning(f"[Status] 투자경고/환기 페이지 오류: {e}")
    return result


# ── DB 업데이트 ──────────────────────────────────────────────
def _update_db(
    conn: sqlite3.Connection,
    halt_codes: set[str],
    admin_codes: set[str],
    warn_map: dict[str, str],
) -> dict:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur = conn.cursor()

    # 1. 현재 지정 종목 업데이트
    all_affected = halt_codes | admin_codes | set(warn_map.keys())
    for code in all_affected:
        is_halt  = 1 if code in halt_codes  else 0
        is_admin = 1 if code in admin_codes else 0
        warn     = warn_map.get(code)

        # stock_universe에 있는 종목만 업데이트 (없으면 INSERT하지 않음)
        exists = cur.execute(
            "SELECT stock_code FROM stock_universe WHERE stock_code=? LIMIT 1", (code,)
        ).fetchone()
        if exists:
            cur.execute(
                """UPDATE stock_universe
                   SET is_halt=?, is_admin=?, warn_type=?, status_updated=?
                   WHERE stock_code=?""",
                (is_halt, is_admin, warn, now_str, code),
            )

    # 2. 이번 수집에서 해제된 종목 초기화 (이전에 지정돼 있었지만 이번에 없는 경우)
    cur.execute(
        """UPDATE stock_universe
           SET is_halt=0, is_admin=0, warn_type=NULL, status_updated=?
           WHERE (is_halt=1 OR is_admin=1 OR warn_type IS NOT NULL)
             AND stock_code NOT IN ({})""".format(
            ",".join("?" * len(all_affected)) if all_affected else "''"
        ),
        [now_str] + list(all_affected),
    )

    conn.commit()
    return {
        "halt":  len(halt_codes),
        "admin": len(admin_codes),
        "warn":  len(warn_map),
        "updated_at": now_str,
    }


# ── 메인 수집 ──────────────────────────────────────────────
def collect_stock_status() -> dict:
    """관리종목·거래정지·투자경고/위험/환기 수집 후 stock_universe 갱신."""
    logger.info("[Status] 거래정지·관리종목·투자경고 수집 시작")

    halt_codes  = _fetch_codes_from_page("https://finance.naver.com/sise/trading_halt.naver")
    logger.info(f"[Status] 거래정지 {len(halt_codes)}개")
    time.sleep(SLEEP)

    admin_codes = _fetch_codes_from_page("https://finance.naver.com/sise/management.naver")
    logger.info(f"[Status] 관리종목 {len(admin_codes)}개")
    time.sleep(SLEEP)

    warn_map    = _fetch_warn_codes()
    logger.info(f"[Status] 투자경고/위험/환기 {len(warn_map)}개")

    conn = sqlite3.connect(DB_PATH)
    try:
        _ensure_columns(conn)
        result = _update_db(conn, halt_codes, admin_codes, warn_map)
        logger.info(f"[Status] DB 업데이트 완료: {result}")
        return result
    finally:
        conn.close()


# ── 단일 종목 실시간 조회 (Naver 페이지 soup에서) ────────────────
def parse_status_from_soup(soup) -> dict:
    """
    이미 파싱된 Naver Finance BeautifulSoup에서 상태 추출.
    main.py get_market_info()에서 호출.
    반환: {"is_halt": bool, "is_admin": bool, "warn_type": str|None}
    """
    is_halt = is_admin = False
    warn_type = None
    try:
        # 페이지 전체 텍스트에서 상태 텍스트 탐색 (클래스 기반 + 텍스트 기반)
        status_selectors = [
            "em.ico_nt", "span.ico_nt", "em[class*='ico_']",
            "div.invest_caution", "div.invest_warning",
        ]
        all_tags = []
        for sel in status_selectors:
            all_tags.extend(soup.select(sel))

        # fallback: 페이지 내 특정 텍스트 패턴 검색
        page_text = soup.get_text()

        for tag in all_tags:
            txt = tag.get_text(strip=True)
            if "거래정지" in txt:    is_halt  = True
            if "관리종목" in txt:    is_admin = True
            if "투자위험" in txt:    warn_type = "위험"
            elif "투자경고" in txt:  warn_type = "경고"
            elif "환기" in txt or "투자주의" in txt: warn_type = "환기"

        # 텍스트 fallback (태그 탐색 실패 시)
        if not is_halt   and "거래정지" in page_text[:3000]:  is_halt  = True
        if not is_admin  and "관리종목" in page_text[:3000]:  is_admin = True
        if not warn_type:
            if "투자위험" in page_text[:3000]:   warn_type = "위험"
            elif "투자경고" in page_text[:3000]: warn_type = "경고"
            elif "투자주의환기" in page_text[:3000] or "환기종목" in page_text[:3000]:
                warn_type = "환기"
    except Exception:
        pass
    return {"is_halt": is_halt, "is_admin": is_admin, "warn_type": warn_type}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    print(collect_stock_status())
