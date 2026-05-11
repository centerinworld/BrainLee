"""
collectors/hankyung_consensus_collector.py — 한국경제 컨센서스 수집기

수집 대상: https://consensus.hankyung.com/analysis/list
  · skinType=business     — 기업 리포트 (목표주가 포함)
  · skinType=stock_good   — 목표주가 상향 종목
  · skinType=stock_bad    — 목표주가 하향 종목

수집 필드:
  report_date, stock_code, stock_name, securities_firm, analyst,
  opinion, target_price, prev_target_price, skin_type, report_idx, report_title

저장: stock.db → consensus_targets 테이블
  · report_idx UNIQUE → 중복 UPSERT (INSERT OR IGNORE) 로 멱등 처리
  · 초기 2년치 전체 수집 / 일별 증분은 최근 3일치만

HTML 파싱 전략:
  · BeautifulSoup4 (lxml 파서 우선, html.parser 폴백)
  · 종목코드: 제목 내 r'\((\d{6})\)' 정규식 추출
  · rate limit: 페이지당 1.5초 대기
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE_URL   = "https://consensus.hankyung.com/analysis/list"
_PAGE_SIZE  = 80
_SKIN_TYPES = ["business", "stock_good", "stock_bad"]
_HEADERS    = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://consensus.hankyung.com/",
}
_CODE_RE    = re.compile(r"\((\d{6})\)")
_PRICE_RE   = re.compile(r"[\d,]+")


# ─────────────────────────────────────────────────────────────────────────────
# HTML 파싱 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _parse_price(text: str) -> Optional[float]:
    text = text.strip().replace(",", "")
    m = re.fullmatch(r"[-]?\d+", text)
    if m:
        try:
            return float(m.group())
        except ValueError:
            pass
    m2 = _PRICE_RE.search(text)
    if m2:
        try:
            return float(m2.group().replace(",", ""))
        except ValueError:
            pass
    return None


def _try_parsers(html: str) -> BeautifulSoup:
    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(html, parser)
        except Exception:
            continue
    return BeautifulSoup(html, "html.parser")


def _last_page(soup: BeautifulSoup) -> int:
    """'끝으로' 링크에서 마지막 페이지 번호 추출."""
    # <a href="?...now_page=91...">끝으로</a>
    for a in soup.select("a"):
        text = a.get_text(strip=True)
        if "끝으로" in text or "마지막" in text:
            href = a.get("href", "")
            m = re.search(r"now_page=(\d+)", href)
            if m:
                return int(m.group(1))
    # onclick 형태
    for a in soup.select("a"):
        onclick = a.get("onclick", "")
        if "now_page" in onclick:
            m = re.search(r"now_page.*?(\d+)", onclick)
            if m:
                return int(m.group(1))
    # 페이지 번호 링크 중 가장 큰 값
    nums = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        m = re.search(r"now_page=(\d+)", href)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) if nums else 1


def _parse_rows(soup: BeautifulSoup, skin_type: str) -> list[dict]:
    """테이블 행 파싱 → dict 리스트.

    실측 컬럼 구조:
      business   (9열): 날짜 | 제목 | 목표주가 | 투자의견 | 애널리스트 | 증권사 | ... (빈칸 3개)
      stock_good (6열): 날짜 | 제목 | 애널리스트 | 증권사 | 신규목표 | 이전목표
      stock_bad  (6열): 날짜 | 제목 | 애널리스트 | 증권사 | 신규목표 | 이전목표
    """
    rows = []

    # 리포트 목록 테이블 — class 패턴이 사이트마다 다르므로 첫 번째 tbody 시도
    tbody = soup.find("tbody")
    if not tbody:
        return rows

    for tr in tbody.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 4:
            continue

        try:
            if skin_type in ("stock_good", "stock_bad"):
                # 6열: 날짜(0) | 제목(1) | 애널리스트(2) | 증권사(3) | 신규목표(4) | 이전목표(5)
                date_txt     = tds[0].get_text(strip=True)
                title_txt    = tds[1].get_text(strip=True)
                analyst      = tds[2].get_text(strip=True)
                firm         = tds[3].get_text(strip=True)
                target_price = _parse_price(tds[4].get_text(strip=True)) if len(tds) > 4 else None
                prev_target  = _parse_price(tds[5].get_text(strip=True)) if len(tds) > 5 else None
                opinion      = ""
            else:
                # 9열: 날짜(0) | 제목(1) | 목표주가(2) | 투자의견(3) | 애널리스트(4) | 증권사(5) | ...
                date_txt     = tds[0].get_text(strip=True)
                title_txt    = tds[1].get_text(strip=True)
                target_price = _parse_price(tds[2].get_text(strip=True))
                prev_target  = None
                opinion      = tds[3].get_text(strip=True) if len(tds) > 3 else ""
                analyst      = tds[4].get_text(strip=True) if len(tds) > 4 else ""
                firm         = tds[5].get_text(strip=True) if len(tds) > 5 else ""

            # 종목코드 추출
            code_m = _CODE_RE.search(title_txt)
            if not code_m:
                continue
            stock_code = code_m.group(1)
            stock_name = title_txt[:code_m.start()].strip()

            # report_idx 추출 (PDF 링크 href 또는 onclick)
            report_idx = None
            for a in tds[1].find_all("a"):
                href = a.get("href", "")
                m = re.search(r"report_idx=(\d+)", href)
                if m:
                    report_idx = int(m.group(1))
                    break
                onclick = a.get("onclick", "")
                m2 = re.search(r"report_idx.*?(\d+)", onclick)
                if m2:
                    report_idx = int(m2.group(1))
                    break

            # 날짜 정규화
            report_date = date_txt.strip()
            if len(report_date) == 8 and report_date.isdigit():
                report_date = f"{report_date[:4]}-{report_date[4:6]}-{report_date[6:]}"

            rows.append({
                "report_idx":       report_idx,
                "stock_code":       stock_code,
                "stock_name":       stock_name,
                "report_date":      report_date,
                "securities_firm":  firm,
                "analyst":          analyst,
                "opinion":          opinion,
                "target_price":     target_price,
                "prev_target_price": prev_target,
                "skin_type":        skin_type,
                "report_title":     title_txt,
            })
        except Exception as e:
            logger.debug(f"[컨센서스] 행 파싱 오류: {e}")
            continue

    return rows


# ─────────────────────────────────────────────────────────────────────────────
# 메인 수집 함수
# ─────────────────────────────────────────────────────────────────────────────

def collect_consensus(
    db_path: str = "stock.db",
    days: int = 3,
    full: bool = False,
) -> int:
    """
    한경 컨센서스 수집.

    Args:
        db_path: SQLite DB 경로
        days:    증분 수집 일수 (full=False 시 사용)
        full:    True → 2년치 전체 수집 (초기 1회)

    Returns:
        저장된 신규 레코드 수
    """
    edate = date.today()
    if full:
        sdate = edate - timedelta(days=730)   # 2년
    else:
        sdate = edate - timedelta(days=days)

    sdate_str = sdate.strftime("%Y-%m-%d")
    edate_str = edate.strftime("%Y-%m-%d")

    session  = requests.Session()
    session.headers.update(_HEADERS)
    total_saved = 0

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")

    try:
        for skin_type in _SKIN_TYPES:
            logger.info(f"[컨센서스] {skin_type} 수집 시작 ({sdate_str}~{edate_str})")
            saved = _collect_skin(session, conn, skin_type, sdate_str, edate_str)
            total_saved += saved
            logger.info(f"[컨센서스] {skin_type} 완료 → {saved}건 저장")
    finally:
        conn.close()

    logger.info(f"[컨센서스] 전체 {total_saved}건 저장 완료")
    return total_saved


def _collect_skin(
    session: requests.Session,
    conn: sqlite3.Connection,
    skin_type: str,
    sdate_str: str,
    edate_str: str,
) -> int:
    """단일 skinType 전체 페이지 수집."""
    saved  = 0
    page   = 1

    while True:
        url = (
            f"{_BASE_URL}?skinType={skin_type}"
            f"&sdate={sdate_str}&edate={edate_str}"
            f"&now_page={page}&pagenum={_PAGE_SIZE}"
        )
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(f"[컨센서스] {skin_type} p{page} 요청 실패: {e}")
            break

        soup     = _try_parsers(resp.text)
        last_pg  = _last_page(soup) if page == 1 else last_pg  # noqa: F821
        rows     = _parse_rows(soup, skin_type)

        if not rows:
            logger.debug(f"[컨센서스] {skin_type} p{page}: 빈 페이지 → 종료")
            break

        saved += _upsert_rows(conn, rows)

        logger.debug(f"[컨센서스] {skin_type} p{page}/{last_pg}: {len(rows)}행")

        if page >= last_pg:
            break

        page += 1
        time.sleep(1.5)   # rate limit

    return saved


def _upsert_rows(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """INSERT OR IGNORE — report_idx UNIQUE 기준 중복 방지."""
    sql = """
        INSERT OR IGNORE INTO consensus_targets
          (report_idx, stock_code, stock_name, report_date, securities_firm,
           analyst, opinion, target_price, prev_target_price, skin_type, report_title)
        VALUES
          (:report_idx, :stock_code, :stock_name, :report_date, :securities_firm,
           :analyst, :opinion, :target_price, :prev_target_price, :skin_type, :report_title)
    """
    cursor = conn.cursor()
    count  = 0
    for row in rows:
        try:
            cursor.execute(sql, row)
            if cursor.rowcount > 0:
                count += 1
        except Exception as e:
            logger.debug(f"[컨센서스] upsert 오류: {e} | {row.get('report_idx')}")
    conn.commit()
    return count


# ─────────────────────────────────────────────────────────────────────────────
# 개별 종목 컨센서스 조회 (API용)
# ─────────────────────────────────────────────────────────────────────────────

def get_consensus_for_stock(
    stock_code: str,
    db_path:    str = "stock.db",
    months:     int = 24,
) -> list[dict]:
    """
    특정 종목의 컨센서스 이력 조회.
    반환: [{report_date, securities_firm, analyst, opinion, target_price,
            prev_target_price, skin_type, report_title}, ...]
    최신순 정렬
    """
    since = (date.today() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    conn  = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT report_date, securities_firm, analyst, opinion,
                   target_price, prev_target_price, skin_type, report_title, report_idx
            FROM   consensus_targets
            WHERE  stock_code = ?
              AND  report_date >= ?
              AND  target_price > 0
            ORDER  BY report_date DESC, id DESC
            """,
            (stock_code, since),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_consensus_summary(
    stock_code: str,
    db_path:    str = "stock.db",
    months:     int = 3,
) -> dict:
    """
    최근 N개월 컨센서스 요약.
    반환: {
      avg_target: float,          평균 목표주가
      max_target: float,          최고 목표주가
      min_target: float,          최저 목표주가
      count: int,                 리포트 수
      firms: [str],               증권사 목록
      latest_date: str,           최신 리포트 날짜
      latest_target: float,       최신 목표주가
      buy_count: int,             매수 의견 수
      hold_count: int,            보유 의견 수
      sell_count: int,            매도 의견 수
    }
    """
    since = (date.today() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
    conn  = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT report_date, securities_firm, opinion, target_price
            FROM   consensus_targets
            WHERE  stock_code = ?
              AND  report_date >= ?
              AND  target_price > 0
            ORDER  BY report_date DESC
            """,
            (stock_code, since),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {"count": 0, "avg_target": 0, "max_target": 0, "min_target": 0,
                "firms": [], "latest_date": None, "latest_target": 0,
                "buy_count": 0, "hold_count": 0, "sell_count": 0}

    prices = [r["target_price"] for r in rows if r["target_price"]]
    firms  = list(dict.fromkeys(r["securities_firm"] for r in rows if r["securities_firm"]))

    buy_kw  = {"매수", "buy", "strong buy", "적극매수"}
    hold_kw = {"중립", "보유", "hold", "neutral", "시장수익률"}
    sell_kw = {"매도", "sell", "underperform"}

    buy_cnt  = sum(1 for r in rows if r["opinion"] and r["opinion"].lower() in buy_kw)
    hold_cnt = sum(1 for r in rows if r["opinion"] and r["opinion"].lower() in hold_kw)
    sell_cnt = sum(1 for r in rows if r["opinion"] and r["opinion"].lower() in sell_kw)

    return {
        "avg_target":   round(sum(prices) / len(prices)) if prices else 0,
        "max_target":   max(prices) if prices else 0,
        "min_target":   min(prices) if prices else 0,
        "count":        len(rows),
        "firms":        firms[:10],
        "latest_date":  rows[0]["report_date"] if rows else None,
        "latest_target": rows[0]["target_price"] if rows else 0,
        "buy_count":    buy_cnt,
        "hold_count":   hold_cnt,
        "sell_count":   sell_cnt,
    }


# ─────────────────────────────────────────────────────────────────────────────
# CLI 직접 실행 (초기 2년치 백필)
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(description="한경 컨센서스 수집기")
    p.add_argument("--full",    action="store_true", help="2년치 전체 수집")
    p.add_argument("--days",    type=int, default=3,  help="증분 수집 일수 (기본 3)")
    p.add_argument("--db",      default="stock.db",   help="DB 경로")
    args = p.parse_args()
    n = collect_consensus(db_path=args.db, days=args.days, full=args.full)
    print(f"저장 완료: {n}건")
