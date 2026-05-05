"""
collect_naver_fundamentals.py — 네이버금융 PER/PBR/EPS 전종목 배치 수집

financial_data 테이블에 per/pbr/eps를 저장 (is_annual=True 최신 연도 레코드 업데이트).
저장 위치: financial_data.per, pbr, eps (최신 연도 연간 레코드)

사용법:
  python3 collect_naver_fundamentals.py           # 전종목
  python3 collect_naver_fundamentals.py --limit 500  # 상위 N종목만
  python3 collect_naver_fundamentals.py --missing    # DB에 per/pbr 없는 종목만
"""

import argparse
import logging
import sqlite3
import time
import sys

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}
RATE_LIMIT = 0.5   # 초당 2건


def scrape_naver(code: str) -> dict:
    try:
        res = requests.get(
            f"https://finance.naver.com/item/main.naver?code={code}",
            headers=HEADERS, timeout=8,
        )
        res.encoding = "euc-kr"
        soup = BeautifulSoup(res.text, "html.parser")
        def _n(sel):
            t = soup.select_one(sel)
            if not t: return None
            s = t.text.strip().replace(",", "").replace("N/A", "").strip()
            try: return float(s) if s else None
            except: return None
        per = _n("em#_per")
        pbr = _n("em#_pbr")
        eps = _n("em#_eps")
        return {"per": per, "pbr": pbr, "eps": eps}
    except Exception as e:
        logger.warning(f"[{code}] 오류: {e}")
        return {}


def get_target_codes(conn: sqlite3.Connection, missing_only: bool, limit: int | None) -> list[str]:
    """price_history에서 국내 6자리 코드 + 거래량 있는 종목 추출."""
    rows = conn.execute("""
        SELECT DISTINCT stock_code
        FROM price_history
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND LENGTH(stock_code) = 6
          AND close > 0
    """).fetchall()
    codes = [r[0] for r in rows]

    if missing_only:
        # stock_universe에 per/pbr 모두 있는 종목 제외
        have = set(r[0] for r in conn.execute("""
            SELECT DISTINCT stock_code FROM stock_universe
            WHERE per IS NOT NULL AND pbr IS NOT NULL
        """).fetchall())
        codes = [c for c in codes if c not in have]

    if limit:
        codes = codes[:limit]
    return codes


def save(conn: sqlite3.Connection, code: str, data: dict):
    if not data or (data.get("per") is None and data.get("pbr") is None):
        return

    # stock_universe에 반영 (섹터지표 등 실시간 조회용)
    # financial_data에는 per/pbr 컬럼이 없으므로 stock_universe만 업데이트
    conn.execute("""
        UPDATE stock_universe
        SET per=?, pbr=?, eps=COALESCE(?, eps), updated_at=datetime('now')
        WHERE stock_code=?
    """, (data.get("per"), data.get("pbr"), data.get("eps"), code))


def run(missing_only: bool = False, limit: int | None = None):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")

    codes = get_target_codes(conn, missing_only, limit)
    total = len(codes)
    logger.info(f"대상 종목: {total:,}개 (missing_only={missing_only})")

    ok = 0
    for i, code in enumerate(codes, 1):
        data = scrape_naver(code)
        save(conn, code, data)

        if data.get("per") is not None or data.get("pbr") is not None:
            ok += 1

        if i % 100 == 0:
            conn.commit()
            logger.info(f"  {i}/{total} 완료 ({ok}건 수집)")

        time.sleep(RATE_LIMIT)

    conn.commit()
    conn.close()
    logger.info(f"완료: {total}종목 처리, {ok}건 수집")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--missing", action="store_true", help="per/pbr 없는 종목만")
    ap.add_argument("--limit",   type=int, default=None, help="최대 처리 종목수")
    args = ap.parse_args()
    run(missing_only=args.missing, limit=args.limit)
