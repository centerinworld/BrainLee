"""
check_financial_integrity.py — 재무제표/현금흐름표 무결점 자동 점검·보완

실행 방법:
  python3 check_financial_integrity.py              # 전체 점검 + 보완
  python3 check_financial_integrity.py --report-only # 점검만 (수정 안 함)
  python3 check_financial_integrity.py --quarters 2  # 최근 N개 분기만 점검

분기 공시 마감 후 자동 실행 일정 (scheduler.py에서 호출):
  - 사업보고서(Q4/Annual): 4월 7일 05:00
  - 분기보고서(Q1):        5월 22일 05:00
  - 반기보고서(Q2):        8월 21일 05:00
  - 분기보고서(Q3):        11월 21일 05:00
  - 월간 무결점 테스트:     매월 1일 05:00
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup

DB_PATH  = "/Applications/stock_dashboard/stock.db"
LOG_PATH = "/Applications/stock_dashboard/logs/financial_integrity.log"
SLEEP_FNGUIDE = 2.5

os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

try:
    from api_rate_limiter import api_limiter as _rl
    _USE_RL = True
except ImportError:
    _USE_RL = False

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://comp.fnguide.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
})

# FnGuide URL 패턴
FNGUIDE_CF_Q  = "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=B&NewMenuID=103&stkGb=701"
FNGUIDE_BS_Q  = "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=B&NewMenuID=102&stkGb=701"
FNGUIDE_IS_Q  = "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=B&NewMenuID=101&stkGb=701"
FNGUIDE_CF_A  = "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=103&stkGb=701"


# ─── DB 유틸 ──────────────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def _retry_exec(conn, sql, params=()):
    for attempt in range(5):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < 4:
                time.sleep(3 * (attempt + 1))
            else:
                raise


# ─── 분기 계산 ────────────────────────────────────────────────────────────────
def recent_quarters(n: int = 8) -> list[tuple[int, int]]:
    """최근 n개 분기 (year, quarter) 목록 — 오래된 순."""
    today = date.today()
    y, m = today.year, today.month
    cur_q = (m - 1) // 3 + 1
    result = []
    for _ in range(n):
        result.append((y, cur_q))
        cur_q -= 1
        if cur_q == 0:
            cur_q, y = 4, y - 1
    return list(reversed(result))


def latest_reported_quarter() -> tuple[int, int]:
    """현재 시점에서 공시 완료된 최신 분기."""
    today = date.today()
    y, m = today.year, today.month
    # 공시 마감: Q4=3/31, Q1=5/15, Q2=8/14, Q3=11/14
    # 여유 2주 추가
    if m >= 6:   return y, 1
    if m >= 10:  return y, 2
    if m >= 12:  return y, 3
    return y - 1, 4


# ─── 무결점 점검 ──────────────────────────────────────────────────────────────
class IntegrityReport:
    def __init__(self):
        self.gaps: list[dict] = []          # {stock_code, year, quarter, missing_cols}
        self.cf_gaps: list[dict] = []       # cash_flow_data 누락
        self.equity_errors: int = 0
        self.fixed_dart: int = 0
        self.fixed_fnguide: int = 0
        self.fixed_sql: int = 0

    def summary(self) -> str:
        return (
            f"재무공백={len(self.gaps)}종목, CF공백={len(self.cf_gaps)}종목 | "
            f"자동수정: DART={self.fixed_dart} FnGuide={self.fixed_fnguide} SQL={self.fixed_sql}"
        )


def check_financial_gaps(conn, quarters: list[tuple[int, int]]) -> list[dict]:
    """financial_data 공백 탐지: 최근 N분기 중 핵심 컬럼 NULL인 종목."""
    gaps = []
    for (y, q) in quarters:
        rows = conn.execute("""
            SELECT fd.stock_code, su.stock_name,
                   fd.revenue, fd.operating_profit, fd.net_income,
                   fd.total_assets, fd.total_equity, fd.eps
            FROM financial_data fd
            JOIN stock_universe su ON fd.stock_code = su.stock_code
            WHERE fd.is_annual = 0 AND fd.year = ? AND fd.quarter = ?
              AND su.stock_type = '보통주'
              AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
              AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
              AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
        """, (y, q)).fetchall()

        for r in rows:
            missing = []
            if r["revenue"] is None:          missing.append("revenue")
            if r["operating_profit"] is None:  missing.append("operating_profit")
            if r["net_income"] is None:        missing.append("net_income")
            if r["total_assets"] is None:      missing.append("total_assets")
            if r["total_equity"] is None:      missing.append("total_equity")
            if missing:
                gaps.append({
                    "stock_code": r["stock_code"],
                    "stock_name": r["stock_name"],
                    "year": y, "quarter": q,
                    "missing": missing,
                })
    return gaps


def check_equity_errors(conn) -> int:
    """total_equity가 derived(assets-liab)의 10% 미만인 명백 오류 수."""
    r = conn.execute("""
        SELECT COUNT(*) FROM financial_data
        WHERE total_assets IS NOT NULL AND total_liabilities IS NOT NULL
          AND (total_assets - total_liabilities) > 0
          AND total_equity IS NOT NULL AND total_equity != 0
          AND total_equity < (total_assets - total_liabilities) * 0.1
    """).fetchone()
    return r[0]


def check_cf_gaps(conn, quarters: list[tuple[int, int]]) -> list[dict]:
    """cash_flow_data 공백: 최근 N분기 중 depreciation IS NULL인 종목."""
    gaps = []
    for (y, q) in quarters:
        rows = conn.execute("""
            SELECT cfd.stock_code, su.stock_name
            FROM cash_flow_data cfd
            JOIN stock_universe su ON cfd.stock_code = su.stock_code
            WHERE cfd.is_annual = 0 AND cfd.year = ? AND cfd.quarter = ?
              AND cfd.depreciation IS NULL
              AND su.stock_type = '보통주'
              AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
              AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
              AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
        """, (y, q)).fetchall()
        for r in rows:
            gaps.append({"stock_code": r["stock_code"], "stock_name": r["stock_name"],
                         "year": y, "quarter": q})
    return gaps


def check_missing_cf_stocks(conn) -> list[str]:
    """financial_data에 있는데 cash_flow_data가 아예 없는 종목."""
    rows = conn.execute("""
        SELECT DISTINCT fd.stock_code
        FROM financial_data fd
        JOIN stock_universe su ON fd.stock_code = su.stock_code
        WHERE fd.is_annual = 0
          AND su.stock_type = '보통주'
          AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
          AND fd.stock_code NOT IN (SELECT DISTINCT stock_code FROM cash_flow_data)
    """).fetchall()
    return [r[0] for r in rows]


def _backup_table_name(conn) -> Optional[str]:
    """financial_data 백업 테이블명 1개 선택 (최신 suffix 우선)."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'financial_data_backup_%' ORDER BY name DESC"
    ).fetchall()
    if not rows:
        return None
    return rows[0][0]


def refill_financial_from_backup(conn, codes: list[str], quarters: list[tuple[int, int]]) -> int:
    """백업 테이블에서 핵심 재무값 복원 (NULL/0 오염값 대상)."""
    if not codes:
        return 0
    bkup = _backup_table_name(conn)
    if not bkup:
        logger.warning("[백업복원] financial_data_backup_* 테이블 없음")
        return 0

    target_qs = set(quarters)
    total = 0
    for code in codes:
        for (y, q) in target_qs:
            cur = _retry_exec(conn, f"""
                UPDATE financial_data AS f
                SET
                    revenue = COALESCE(
                        CASE WHEN f.revenue IS NULL OR f.revenue = 0 THEN (SELECT b.revenue FROM {bkup} b
                         WHERE b.stock_code=f.stock_code AND b.year=f.year AND b.quarter=f.quarter AND b.is_annual=f.is_annual) END,
                        f.revenue
                    ),
                    operating_profit = COALESCE(
                        CASE WHEN f.operating_profit IS NULL OR f.operating_profit = 0 THEN (SELECT b.operating_profit FROM {bkup} b
                         WHERE b.stock_code=f.stock_code AND b.year=f.year AND b.quarter=f.quarter AND b.is_annual=f.is_annual) END,
                        f.operating_profit
                    ),
                    net_income = COALESCE(
                        CASE WHEN f.net_income IS NULL OR f.net_income = 0 THEN (SELECT b.net_income FROM {bkup} b
                         WHERE b.stock_code=f.stock_code AND b.year=f.year AND b.quarter=f.quarter AND b.is_annual=f.is_annual) END,
                        f.net_income
                    ),
                    eps = COALESCE(
                        CASE WHEN f.eps IS NULL OR f.eps = 0 THEN (SELECT b.eps FROM {bkup} b
                         WHERE b.stock_code=f.stock_code AND b.year=f.year AND b.quarter=f.quarter AND b.is_annual=f.is_annual) END,
                        f.eps
                    )
                WHERE f.stock_code=? AND f.year=? AND f.quarter=? AND f.is_annual=0
                  AND EXISTS (
                    SELECT 1 FROM {bkup} b
                    WHERE b.stock_code=f.stock_code AND b.year=f.year AND b.quarter=f.quarter AND b.is_annual=0
                  )
            """, (code, y, q))
            total += cur.rowcount

    conn.commit()
    logger.info(f"[백업복원] 테이블={bkup}, 업데이트={total}건")
    return total


def derive_quarterly_from_cumulative(conn, codes: list[str], quarters: list[tuple[int, int]]) -> int:
    """
    분기값 보정:
    - Q4(분기) = 같은연도 연간(quarter=0,is_annual=1)
    - Q2(분기) = 반기(quarter=2,is_annual=1) - Q1(분기)
    - Q3(분기) = 3Q누적(quarter=3,is_annual=1) - 반기(quarter=2,is_annual=1)
    """
    if not codes:
        return 0
    target_qs = set(quarters)
    total = 0

    for code in codes:
        years = sorted({y for (y, _) in target_qs})
        for y in years:
            # Q4 분기값을 연간값으로 보정
            if (y, 4) in target_qs:
                cur = _retry_exec(conn, """
                    UPDATE financial_data AS q4
                    SET revenue = COALESCE(q4.revenue, (SELECT a.revenue FROM financial_data a WHERE a.stock_code=q4.stock_code AND a.year=q4.year AND a.quarter=0 AND a.is_annual=1)),
                        operating_profit = COALESCE(q4.operating_profit, (SELECT a.operating_profit FROM financial_data a WHERE a.stock_code=q4.stock_code AND a.year=q4.year AND a.quarter=0 AND a.is_annual=1)),
                        net_income = COALESCE(q4.net_income, (SELECT a.net_income FROM financial_data a WHERE a.stock_code=q4.stock_code AND a.year=q4.year AND a.quarter=0 AND a.is_annual=1)),
                        eps = COALESCE(q4.eps, (SELECT a.eps FROM financial_data a WHERE a.stock_code=q4.stock_code AND a.year=q4.year AND a.quarter=0 AND a.is_annual=1))
                    WHERE q4.stock_code=? AND q4.year=? AND q4.quarter=4 AND q4.is_annual=0
                      AND EXISTS (SELECT 1 FROM financial_data a WHERE a.stock_code=q4.stock_code AND a.year=q4.year AND a.quarter=0 AND a.is_annual=1)
                """, (code, y))
                total += cur.rowcount

            # Q2 = 반기 - Q1 (net_income / revenue / operating_profit)
            if (y, 2) in target_qs:
                cur = _retry_exec(conn, """
                    UPDATE financial_data AS q2
                    SET revenue = COALESCE(q2.revenue, (SELECT h.revenue - q1.revenue FROM financial_data h JOIN financial_data q1
                       ON q1.stock_code=h.stock_code AND q1.year=h.year AND q1.quarter=1 AND q1.is_annual=0
                       WHERE h.stock_code=q2.stock_code AND h.year=q2.year AND h.quarter=2 AND h.is_annual=1)),
                        operating_profit = COALESCE(q2.operating_profit, (SELECT h.operating_profit - q1.operating_profit FROM financial_data h JOIN financial_data q1
                       ON q1.stock_code=h.stock_code AND q1.year=h.year AND q1.quarter=1 AND q1.is_annual=0
                       WHERE h.stock_code=q2.stock_code AND h.year=q2.year AND h.quarter=2 AND h.is_annual=1)),
                        net_income = COALESCE(q2.net_income, (SELECT h.net_income - q1.net_income FROM financial_data h JOIN financial_data q1
                       ON q1.stock_code=h.stock_code AND q1.year=h.year AND q1.quarter=1 AND q1.is_annual=0
                       WHERE h.stock_code=q2.stock_code AND h.year=q2.year AND h.quarter=2 AND h.is_annual=1))
                    WHERE q2.stock_code=? AND q2.year=? AND q2.quarter=2 AND q2.is_annual=0
                """, (code, y))
                total += cur.rowcount

            # Q3 = 3Q누적 - 반기
            if (y, 3) in target_qs:
                cur = _retry_exec(conn, """
                    UPDATE financial_data AS q3
                    SET revenue = COALESCE(q3.revenue, (SELECT c3.revenue - h.revenue FROM financial_data c3 JOIN financial_data h
                       ON h.stock_code=c3.stock_code AND h.year=c3.year AND h.quarter=2 AND h.is_annual=1
                       WHERE c3.stock_code=q3.stock_code AND c3.year=q3.year AND c3.quarter=3 AND c3.is_annual=1)),
                        operating_profit = COALESCE(q3.operating_profit, (SELECT c3.operating_profit - h.operating_profit FROM financial_data c3 JOIN financial_data h
                       ON h.stock_code=c3.stock_code AND h.year=c3.year AND h.quarter=2 AND h.is_annual=1
                       WHERE c3.stock_code=q3.stock_code AND c3.year=q3.year AND c3.quarter=3 AND c3.is_annual=1)),
                        net_income = COALESCE(q3.net_income, (SELECT c3.net_income - h.net_income FROM financial_data c3 JOIN financial_data h
                       ON h.stock_code=c3.stock_code AND h.year=c3.year AND h.quarter=2 AND h.is_annual=1
                       WHERE c3.stock_code=q3.stock_code AND c3.year=q3.year AND c3.quarter=3 AND c3.is_annual=1))
                    WHERE q3.stock_code=? AND q3.year=? AND q3.quarter=3 AND q3.is_annual=0
                """, (code, y))
                total += cur.rowcount

    conn.commit()
    logger.info(f"[누적파생복원] 업데이트={total}건")
    return total


# ─── SQL 즉시 수정 ────────────────────────────────────────────────────────────
def sql_fix_derived(conn) -> int:
    """assets-liab으로 equity 파생, ROE/BPS 재계산."""
    total = 0

    # equity 파생 (NULL or 명백 오류)
    cur = _retry_exec(conn, """
        UPDATE financial_data
        SET total_equity = total_assets - total_liabilities
        WHERE total_assets IS NOT NULL AND total_liabilities IS NOT NULL
          AND (total_assets - total_liabilities) > 0
          AND (total_equity IS NULL OR
               total_equity < (total_assets - total_liabilities) * 0.1)
    """)
    total += cur.rowcount

    # Q4 balance sheet → 연간에서 복사
    cur = _retry_exec(conn, """
        UPDATE financial_data AS q
        SET total_assets = (SELECT a.total_assets FROM financial_data a
                            WHERE a.stock_code=q.stock_code AND a.year=q.year AND a.is_annual=1 LIMIT 1),
            total_liabilities = (SELECT a.total_liabilities FROM financial_data a
                            WHERE a.stock_code=q.stock_code AND a.year=q.year AND a.is_annual=1 LIMIT 1),
            total_equity = (SELECT a.total_equity FROM financial_data a
                            WHERE a.stock_code=q.stock_code AND a.year=q.year AND a.is_annual=1 LIMIT 1),
            capital_stock = (SELECT a.capital_stock FROM financial_data a
                            WHERE a.stock_code=q.stock_code AND a.year=q.year AND a.is_annual=1 LIMIT 1)
        WHERE q.is_annual = 0 AND q.quarter = 4 AND q.total_assets IS NULL
          AND EXISTS (SELECT 1 FROM financial_data a
                      WHERE a.stock_code=q.stock_code AND a.year=q.year
                        AND a.is_annual=1 AND a.total_assets IS NOT NULL)
    """)
    total += cur.rowcount

    # ROE 재계산
    cur = _retry_exec(conn, """
        UPDATE financial_data
        SET roe = ROUND(net_income * 100.0 / total_equity, 2)
        WHERE net_income IS NOT NULL AND total_equity IS NOT NULL AND total_equity != 0
          AND (roe IS NULL OR ABS(roe - net_income * 100.0 / total_equity) > 1)
    """)
    total += cur.rowcount

    # BPS 재계산
    cur = _retry_exec(conn, """
        UPDATE financial_data
        SET bps = ROUND(financial_data.total_equity / su.shares_issued, 0)
        FROM (SELECT stock_code, shares_issued FROM stock_universe
              WHERE shares_issued IS NOT NULL AND shares_issued > 0) su
        WHERE financial_data.stock_code = su.stock_code
          AND financial_data.total_equity IS NOT NULL AND financial_data.total_equity > 0
          AND (financial_data.bps IS NULL OR
               ABS(financial_data.bps - financial_data.total_equity / su.shares_issued)
               > financial_data.total_equity / su.shares_issued * 0.5)
    """)
    total += cur.rowcount

    conn.commit()
    return total


# ─── DART 재수집 ──────────────────────────────────────────────────────────────
def dart_refill_stocks(codes: list[str]) -> int:
    """특정 종목 코드에 대해 DART 재무 재수집 (data_collector 사용)."""
    if not codes:
        return 0
    try:
        import config as _cfg
        from data_collector import DataCollector
        collector = DataCollector(dart_api_key=_cfg.DART_API_KEY)
        updated = 0
        for code in codes:
            try:
                collector.collect_fundamentals(code, latest_only=False)
                updated += 1
                time.sleep(0.5)
            except Exception as e:
                logger.warning(f"[DART] {code} 재수집 오류: {e}")
        return updated
    except Exception as e:
        logger.error(f"[DART] 재수집 초기화 오류: {e}")
        return 0


def dart_refill_cashflow(years: int = 5) -> bool:
    """DART 현금흐름표 --fill-missing 배치 실행."""
    try:
        result = subprocess.run(
            ["venv/bin/python3", "collect_dart_cashflow_batch.py",
             "--fill-missing", "--years", str(years)],
            capture_output=True, text=True, timeout=14400,
            cwd="/Applications/stock_dashboard",
        )
        logger.info(f"[DART CF] 배치 완료 (rc={result.returncode})")
        return result.returncode == 0
    except Exception as e:
        logger.error(f"[DART CF] 배치 오류: {e}")
        return False


def dart_refill_cashflow_depr() -> bool:
    """DART 현금흐름표 감가상각비 --refill-depr 실행."""
    try:
        result = subprocess.run(
            ["venv/bin/python3", "collect_dart_cashflow_batch.py",
             "--refill-depr", "--years", "5"],
            capture_output=True, text=True, timeout=7200,
            cwd="/Applications/stock_dashboard",
        )
        logger.info(f"[DART CF depr] 완료 (rc={result.returncode})")
        return result.returncode == 0
    except Exception as e:
        logger.error(f"[DART CF depr] 오류: {e}")
        return False


# ─── FnGuide 파싱 유틸 ────────────────────────────────────────────────────────
def _parse_unit(soup) -> float:
    for t in soup.find_all(string=re.compile(r'단위\s*:\s*[가-힣원]+')):
        t = t.strip()
        if '억원' in t:  return 1e8
        if '백만원' in t: return 1e6
        if '천원' in t:  return 1e3
    return 1e8


def _parse_val(s: str) -> Optional[float]:
    s = s.strip().replace(",", "").replace(" ", "")
    if not s or s in ("-", "—", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fetch_soup(url: str) -> Optional[BeautifulSoup]:
    """FnGuide 페이지 가져오기 — rate limiter 적용 (jitter + 쿼터 체크)."""
    if _USE_RL:
        if not _rl.wait("FNGUIDE"):
            logger.warning("[FnGuide] 일일 쿼터 소진 — 스킵")
            return None
    else:
        time.sleep(SLEEP_FNGUIDE)
    try:
        resp = _SESSION.get(url, timeout=(10, 25))
        if resp.status_code == 429:
            if _USE_RL:
                _rl.report_block("FNGUIDE", cooldown=3600)
            return None
        if resp.status_code != 200:
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception:
        return None


def _extract_quarterly(soup, mult: float, keywords: dict) -> dict:
    """
    FnGuide 분기 테이블에서 {(year, quarter): {col: value}} 파싱.
    keywords: {한글계정명: db컬럼명}
    """
    result = {}
    for tbl in soup.find_all("table"):
        first_row = tbl.find("tr")
        if not first_row:
            continue
        ths = [th.get_text(strip=True) for th in first_row.find_all("th")]
        q_cols = [t for t in ths if re.match(r'\d{4}/\d{2}$', t)]
        if not q_cols:
            continue

        qmap = {}
        for qc in q_cols:
            y, m = int(qc[:4]), int(qc[5:])
            q = {3: 1, 6: 2, 9: 3, 12: 4}.get(m)
            if q:
                qmap[qc] = (y, q)

        found_any = False
        for row in tbl.find_all("tr"):
            th = row.find("th")
            if not th:
                continue
            nm = th.get_text(strip=True).replace(" ", "")
            for kw, col in keywords.items():
                if kw in nm:
                    tds = row.find_all("td")
                    for i, qc in enumerate(q_cols):
                        if i >= len(tds):
                            break
                        v = _parse_val(tds[i].get_text(strip=True))
                        if v is not None:
                            yk = qmap.get(qc)
                            if yk:
                                result.setdefault(yk, {})[col] = v * mult
                    found_any = True
                    break
        if found_any:
            break
    return result


def _extract_annual(soup, mult: float, keywords: dict) -> dict:
    """FnGuide 연간 테이블에서 {year: {col: value}} 파싱."""
    result = {}
    for tbl in soup.find_all("table"):
        first_row = tbl.find("tr")
        if not first_row:
            continue
        ths = [th.get_text(strip=True) for th in first_row.find_all("th")]
        year_cols = [t for t in ths if re.match(r'\d{4}/', t)]
        months = [t.split('/')[1] for t in year_cols]
        if not year_cols or len(set(months)) > 1:
            continue  # 분기 테이블 스킵

        found_any = False
        for row in tbl.find_all("tr"):
            th = row.find("th")
            if not th:
                continue
            nm = th.get_text(strip=True).replace(" ", "")
            for kw, col in keywords.items():
                if kw in nm:
                    tds = row.find_all("td")
                    for i, yc in enumerate(year_cols):
                        if i >= len(tds):
                            break
                        v = _parse_val(tds[i].get_text(strip=True))
                        if v is not None:
                            yr = int(yc[:4])
                            result.setdefault(yr, {})[col] = v * mult
                    found_any = True
                    break
        if found_any:
            break
    return result


# ─── FnGuide 보완 ────────────────────────────────────────────────────────────
def fnguide_fill_financial(codes: list[str], quarters: list[tuple[int, int]]) -> int:
    """FnGuide 분기 IS+BS로 financial_data 공백 채우기."""
    if not codes:
        return 0

    conn = get_conn()
    total_updated = 0
    target_qs = set(quarters)

    BS_KW = {"자산총계": "total_assets", "부채총계": "total_liabilities",
              "자본총계": "total_equity", "자본금": "capital_stock"}
    IS_KW = {"매출액": "revenue", "영업이익": "operating_profit",
              "당기순이익": "net_income", "기본주당이익(손실)": "eps"}

    for idx, code in enumerate(codes):
        updated = 0

        # 분기 IS
        soup_is = _fetch_soup(FNGUIDE_IS_Q.format(code=code))
        if soup_is:
            mult = _parse_unit(soup_is)
            data = _extract_quarterly(soup_is, mult, IS_KW)
            for (y, q), vals in data.items():
                if (y, q) not in target_qs:
                    continue
                for col, val in vals.items():
                    needs_fix_cond = f"{col} IS NULL"
                    if col in ("net_income", "eps"):
                        # 과거 수집에서 0 오염된 값도 FnGuide 값으로 복구
                        needs_fix_cond = f"({col} IS NULL OR {col} = 0)"
                    cur = _retry_exec(conn, f"""
                        UPDATE financial_data SET {col} = ?
                        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
                          AND {needs_fix_cond}
                    """, (val, code, y, q))
                    updated += cur.rowcount

        # 분기 BS  (rate limit은 _fetch_soup 내 api_rate_limiter가 처리)
        soup_bs = _fetch_soup(FNGUIDE_BS_Q.format(code=code))
        if soup_bs:
            mult = _parse_unit(soup_bs)
            data = _extract_quarterly(soup_bs, mult, BS_KW)
            for (y, q), vals in data.items():
                if (y, q) not in target_qs:
                    continue
                for col, val in vals.items():
                    cur = _retry_exec(conn, f"""
                        UPDATE financial_data SET {col} = ?
                        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
                          AND {col} IS NULL
                    """, (val, code, y, q))
                    updated += cur.rowcount

        if updated:
            conn.commit()
            total_updated += updated
            logger.info(f"[FnGuide IS/BS] {idx+1}/{len(codes)} {code}: {updated}행 업데이트")
        elif idx % 20 == 0:
            logger.info(f"[FnGuide IS/BS] {idx+1}/{len(codes)} {code}: 추가 없음")

    conn.close()
    return total_updated


def fnguide_fill_cashflow(codes: list[str]) -> int:
    """FnGuide 분기 CF로 cash_flow_data.depreciation 채우기."""
    if not codes:
        return 0

    conn = get_conn()
    total_updated = 0

    CF_KW = {
        "영업활동현금흐름": "operating_cf",
        "투자활동현금흐름": "investing_cf",
        "재무활동현금흐름": "financing_cf",
        "감가상각비":        "depreciation",
        "유형자산상각비":    "depreciation",
        "설비투자(CapEx)":   "capex",
        "CAPEX":             "capex",
        "기말현금":          "cash_end",
    }

    for idx, code in enumerate(codes):
        soup = _fetch_soup(FNGUIDE_CF_Q.format(code=code))
        if not soup:
            continue

        mult = _parse_unit(soup)
        data = _extract_quarterly(soup, mult, CF_KW)

        updated = 0
        for (y, q), vals in data.items():
            for col, val in vals.items():
                if col == "depreciation" and val <= 0:
                    continue
                existing = conn.execute(
                    "SELECT id FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0",
                    (code, y, q)
                ).fetchone()
                if existing:
                    cur = _retry_exec(conn, f"""
                        UPDATE cash_flow_data SET {col} = ?
                        WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
                          AND {col} IS NULL
                    """, (val, code, y, q))
                    updated += cur.rowcount
                else:
                    # 행이 없으면 insert
                    try:
                        _retry_exec(conn, """
                            INSERT OR IGNORE INTO cash_flow_data
                            (stock_code, year, quarter, is_annual, created_at)
                            VALUES (?, ?, ?, 0, datetime('now'))
                        """, (code, y, q))
                        _retry_exec(conn, f"""
                            UPDATE cash_flow_data SET {col} = ?
                            WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0
                        """, (val, code, y, q))
                        updated += 1
                    except Exception:
                        pass

        if updated:
            conn.commit()
            total_updated += updated
            logger.info(f"[FnGuide CF] {idx+1}/{len(codes)} {code}: {updated}행 업데이트")
        elif idx % 30 == 0:
            logger.info(f"[FnGuide CF] {idx+1}/{len(codes)} {code}: 추가 없음")
        # rate limit은 _fetch_soup 내 api_rate_limiter가 처리

    conn.close()
    return total_updated


def fnguide_fill_depreciation_annual(max_stocks: int = 9999) -> int:
    """FnGuide 연간 CF에서 financial_data.depreciation_amortization 채우기."""
    conn = get_conn()
    stocks = conn.execute("""
        SELECT DISTINCT fd.stock_code
        FROM financial_data fd
        JOIN stock_universe su ON fd.stock_code = su.stock_code
        WHERE (fd.depreciation_amortization IS NULL OR fd.depreciation_amortization = 0)
          AND fd.is_annual = 1 AND fd.year >= 2022
          AND su.stock_type = '보통주'
          AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETF%'
          AND COALESCE(su.stock_name,'') NOT LIKE '%ETN%'
        LIMIT ?
    """, (max_stocks,)).fetchall()
    codes = [r[0] for r in stocks]
    conn.close()

    if not codes:
        return 0

    result = subprocess.run(
        ["venv/bin/python3", "fix_financial_gaps.py", "--step", "dep"],
        capture_output=True, text=True, timeout=7200,
        cwd="/Applications/stock_dashboard",
    )
    logger.info(f"[FnGuide 연간감가] 완료 (rc={result.returncode}, 대상 {len(codes)}종목)")
    return len(codes)


# ─── Telegram 리포트 ─────────────────────────────────────────────────────────
def send_telegram_report(report: IntegrityReport, trigger: str = "자동"):
    try:
        import config as _cfg
        import httpx
        msg = (
            f"📊 재무 무결점 점검 완료 [{trigger}]\n"
            f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"🔍 발견: 재무공백={len(report.gaps)}종목, "
            f"CF공백={len(report.cf_gaps)}종목\n"
            f"🔧 수정: DART={report.fixed_dart}종목, "
            f"FnGuide={report.fixed_fnguide}종목, "
            f"SQL={report.fixed_sql}건\n"
        )
        if report.gaps:
            top5 = report.gaps[:5]
            msg += "\n주요 미수집 종목:\n"
            for g in top5:
                msg += f"  • {g['stock_name']}({g['stock_code']}) {g['year']}Q{g['quarter']}: {','.join(g['missing'])}\n"
            if len(report.gaps) > 5:
                msg += f"  ...외 {len(report.gaps)-5}종목\n"

        token = getattr(_cfg, "TELEGRAM_BOT_TOKEN", None)
        chat_id = getattr(_cfg, "TELEGRAM_CHAT_ID", None)
        if token and chat_id:
            httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": msg, "parse_mode": ""},
                timeout=10,
            )
    except Exception as e:
        logger.warning(f"[Telegram] 리포트 전송 오류: {e}")


# ─── 메인 실행 ────────────────────────────────────────────────────────────────
def run_integrity_check(
    report_only: bool = False,
    n_quarters: int = 8,
    trigger: str = "수동",
) -> IntegrityReport:
    report = IntegrityReport()
    conn = get_conn()

    qs = recent_quarters(n_quarters)
    logger.info(f"[무결점] 점검 시작 | 최근 {n_quarters}분기: {qs[0]}~{qs[-1]} | report_only={report_only}")

    # ── 1. 공백 탐지 ──────────────────────────────────────────────────────────
    logger.info("[무결점] Step 1: 재무공백 탐지")
    gaps = check_financial_gaps(conn, qs)
    cf_gaps = check_cf_gaps(conn, qs)
    missing_cf_stocks = check_missing_cf_stocks(conn)
    equity_errors = check_equity_errors(conn)

    report.gaps = gaps
    report.cf_gaps = cf_gaps
    report.equity_errors = equity_errors

    # 종목별로 집계
    gap_stocks = list({g["stock_code"] for g in gaps})
    cf_gap_stocks = list({g["stock_code"] for g in cf_gaps})

    logger.info(
        f"[무결점] 탐지 결과: "
        f"재무공백={len(gaps)}건({len(gap_stocks)}종목), "
        f"CF공백={len(cf_gaps)}건({len(cf_gap_stocks)}종목), "
        f"CF미수집={len(missing_cf_stocks)}종목, "
        f"equity오류={equity_errors}건"
    )

    conn.close()

    if report_only:
        logger.info("[무결점] report_only 모드 — 수정 생략")
        return report

    # ── 2. SQL 즉시 수정 (파생값) ─────────────────────────────────────────────
    logger.info("[무결점] Step 2: SQL 즉시 수정 (equity 파생, ROE, BPS)")
    conn2 = get_conn()
    sql_fixed = sql_fix_derived(conn2)
    conn2.close()
    report.fixed_sql = sql_fixed
    logger.info(f"[무결점] SQL 수정: {sql_fixed}건")

    # ── 3. DART 재수집 (data_source='fnguide' 아닌 종목만) ───────────────────
    # crud.upsert_financial_data에서 fnguide 레코드는 NULL/0 컬럼만 채워지므로
    # 여기서 fnguide 종목을 걸러내면 불필요한 DART API 호출을 줄일 수 있음
    logger.info(f"[무결점] Step 3: DART 재수집 ({len(gap_stocks)}종목)")
    if gap_stocks:
        # fnguide 데이터가 이미 있는 종목은 DART 호출 스킵 (crud.py가 보호하므로 불필요)
        conn_chk = get_conn()
        fnguide_coded = set(r[0] for r in conn_chk.execute("""
            SELECT DISTINCT stock_code FROM financial_data WHERE data_source='fnguide'
        """).fetchall())
        conn_chk.close()
        dart_targets = [c for c in gap_stocks if c not in fnguide_coded]
        logger.info(f"[무결점] DART 대상: {len(dart_targets)}종목 (fnguide 보유 {len(gap_stocks)-len(dart_targets)}종목 제외)")
        dart_fixed = dart_refill_stocks(dart_targets[:100])
        report.fixed_dart = dart_fixed
        logger.info(f"[무결점] DART 재수집: {dart_fixed}종목 완료")

    # DART 현금흐름표 fill-missing
    logger.info("[무결점] Step 3b: DART 현금흐름표 fill-missing")
    dart_refill_cashflow(years=5)

    # DART 현금흐름표 depreciation refill
    logger.info("[무결점] Step 3c: DART 감가상각비 refill-depr")
    dart_refill_cashflow_depr()

    # ── 4. SQL 재점검 후 백업/누적 파생 보완 ───────────────────────────────────
    logger.info("[무결점] Step 4: 재점검 후 백업/누적 파생 보완 (FnGuide 보호됨 — NULL만 채움)")
    conn3 = get_conn()
    # DART 수집 후에도 여전히 공백인 종목만
    gaps_after = check_financial_gaps(conn3, qs)
    cf_gaps_after = check_cf_gaps(conn3, qs)
    conn3.close()

    remaining_stocks = list({g["stock_code"] for g in gaps_after})
    remaining_cf_stocks = list({g["stock_code"] for g in cf_gaps_after})

    logger.info(
        f"[무결점] DART 후 잔여: 재무={len(remaining_stocks)}종목, CF={len(remaining_cf_stocks)}종목"
    )

    # 백업 테이블 우선 복원
    if remaining_stocks:
        conn4a = get_conn()
        bkup_fixed = refill_financial_from_backup(conn4a, remaining_stocks[:200], qs)
        derived_fixed = derive_quarterly_from_cumulative(conn4a, remaining_stocks[:200], qs)
        conn4a.close()
        report.fixed_sql += (bkup_fixed + derived_fixed)
        logger.info(f"[백업/누적파생] 보완: backup={bkup_fixed}, derived={derived_fixed}")

    # CF는 DART 재수집 재실행 (외부 스크래핑 우회)
    if remaining_cf_stocks or missing_cf_stocks:
        logger.info("[무결점] Step 4b: DART 현금흐름 재보완")
        dart_refill_cashflow(years=5)
        dart_refill_cashflow_depr()

    # ── 5. 최종 SQL 재정비 ────────────────────────────────────────────────────
    logger.info("[무결점] Step 5: 최종 SQL 재정비")
    conn4 = get_conn()
    sql_fix_derived(conn4)
    conn4.close()

    # ── 6. 최종 리포트 ────────────────────────────────────────────────────────
    conn5 = get_conn()
    final_gaps = check_financial_gaps(conn5, qs)
    final_cf_gaps = check_cf_gaps(conn5, qs)
    conn5.close()

    logger.info(
        f"[무결점] 최종: 재무공백={len(final_gaps)}건, CF공백={len(final_cf_gaps)}건 | "
        f"수정: DART={report.fixed_dart} FnGuide={report.fixed_fnguide} SQL={report.fixed_sql}"
    )

    # Telegram 리포트
    send_telegram_report(report, trigger=trigger)

    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="재무 무결점 점검·보완")
    ap.add_argument("--report-only", action="store_true", help="점검만 (수정 안 함)")
    ap.add_argument("--quarters", type=int, default=8, help="점검할 최근 분기 수")
    ap.add_argument("--trigger", default="수동", help="실행 트리거 이름 (Telegram 표시용)")
    args = ap.parse_args()

    report = run_integrity_check(
        report_only=args.report_only,
        n_quarters=args.quarters,
        trigger=args.trigger,
    )
    print(f"\n결과: {report.summary()}")
