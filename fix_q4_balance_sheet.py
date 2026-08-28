"""
fix_q4_balance_sheet.py — Q4 balance sheet NULL 보완 (FnGuide 분기 BS 스크래핑)

사용법:
  python3 fix_q4_balance_sheet.py           # 자동 탐색 (is_annual=0, Q4, total_assets IS NULL)
  python3 fix_q4_balance_sheet.py --year 2024  # 특정 연도만
"""
import argparse
import re
import sqlite3
import sys
import time
import logging
from typing import Optional

import requests
from bs4 import BeautifulSoup

DB_PATH = "/Applications/stock_dashboard/stock.db"
SLEEP = 2.5

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://comp.fnguide.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
})

# FnGuide 분기 재무상태표 URL (ReportGB=B)
FNGUIDE_BS_Q = "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=B&NewMenuID=102&stkGb=701"
# FnGuide 분기 현금흐름표
FNGUIDE_CF_Q = "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=B&NewMenuID=103&stkGb=701"


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


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


def _fetch(url: str):
    try:
        resp = _SESSION.get(url, timeout=(10, 20))
        if resp.status_code != 200:
            return None
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        logger.debug(f"fetch error: {e}")
        return None


def _extract_quarterly_bs(soup, mult: float) -> dict:
    """분기 재무상태표에서 {(year, quarter): {col: val}} 파싱."""
    result = {}
    for tbl in soup.find_all("table"):
        first_row = tbl.find("tr")
        if not first_row:
            continue
        ths = [th.get_text(strip=True) for th in first_row.find_all("th")]
        # 분기 헤더: '2024/03', '2024/06', '2024/09', '2024/12' 형식
        q_cols = [t for t in ths if re.match(r'\d{4}/\d{2}$', t)]
        if not q_cols:
            continue
        # 헤더 파싱: (year, quarter)
        qmap = {}  # col_text → (year, quarter)
        for qc in q_cols:
            y, m = int(qc[:4]), int(qc[5:])
            q = {3: 1, 6: 2, 9: 3, 12: 4}.get(m, None)
            if q:
                qmap[qc] = (y, q)

        # 원하는 계정 키워드
        keywords = {
            '자산총계': 'total_assets',
            '부채총계': 'total_liabilities',
            '자본총계': 'total_equity',
            '자본금':   'capital_stock',
        }
        for row in tbl.find_all("tr"):
            th = row.find("th")
            if not th:
                continue
            nm = th.get_text(strip=True).replace(" ", "")
            matched_col = None
            for kw, col in keywords.items():
                if nm.endswith(kw) or nm == kw:
                    matched_col = col
                    break
            if not matched_col:
                continue
            tds = row.find_all("td")
            for i, qc in enumerate(q_cols):
                if i >= len(tds):
                    break
                v = _parse_val(tds[i].get_text(strip=True))
                if v is not None:
                    yk = qmap.get(qc)
                    if yk:
                        if yk not in result:
                            result[yk] = {}
                        result[yk][matched_col] = v * mult
        if result:
            break  # 첫 번째 유효 테이블만
    return result


def fix_q4_bs(target_year: Optional[int] = None):
    conn = get_conn()

    # 대상 종목: Q4 total_assets IS NULL
    where_year = f"AND f.year = {target_year}" if target_year else ""
    stocks = conn.execute(f"""
        SELECT DISTINCT f.stock_code, s.stock_name
        FROM financial_data f
        LEFT JOIN stock_universe s ON f.stock_code = s.stock_code
        WHERE f.is_annual = 0 AND f.quarter = 4 AND f.total_assets IS NULL
          {where_year}
        ORDER BY f.stock_code
    """).fetchall()

    logger.info(f"[Q4 BS] 대상: {len(stocks)}종목")
    total_updated = 0

    for idx, row in enumerate(stocks):
        code = row["stock_code"]
        name = row["stock_name"] or code

        url = FNGUIDE_BS_Q.format(code=code)
        soup = _fetch(url)
        if not soup:
            logger.warning(f"[Q4 BS] {code} {name}: fetch 실패")
            time.sleep(SLEEP)
            continue

        mult = _parse_unit(soup)
        data = _extract_quarterly_bs(soup, mult)

        if not data:
            logger.info(f"[Q4 BS] {code} {name}: 분기 BS 데이터 없음")
            time.sleep(SLEEP)
            continue

        updated = 0
        for (yr, q), vals in data.items():
            if q != 4:
                continue  # Q4만
            # 해당 row가 있는지 확인
            existing = conn.execute(
                "SELECT id, total_assets FROM financial_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0",
                (code, yr, q)
            ).fetchone()
            if not existing:
                continue
            if existing["total_assets"] is not None:
                continue  # 이미 있으면 건너뜀

            sets = []
            params = []
            for col in ['total_assets', 'total_liabilities', 'total_equity', 'capital_stock']:
                if col in vals:
                    sets.append(f"{col} = ?")
                    params.append(vals[col])
            if not sets:
                continue
            # total_equity 파생
            if 'total_assets' in vals and 'total_liabilities' in vals and 'total_equity' not in vals:
                derived_eq = vals['total_assets'] - vals['total_liabilities']
                if derived_eq > 0:
                    sets.append("total_equity = ?")
                    params.append(derived_eq)

            params.append(code); params.append(yr); params.append(q)
            cur = conn.execute(
                f"UPDATE financial_data SET {', '.join(sets)} WHERE stock_code=? AND year=? AND quarter=? AND is_annual=0",
                params
            )
            updated += cur.rowcount

        if updated:
            conn.commit()
            total_updated += updated
            logger.info(f"[Q4 BS] {code} {name}: {updated}행 업데이트")
        else:
            logger.info(f"[Q4 BS] {code} {name}: 새 데이터 없음 (data={list(data.keys())})")

        time.sleep(SLEEP)

    # 업데이트 후 equity 파생값 정리
    cur = conn.execute("""
        UPDATE financial_data
        SET total_equity = total_assets - total_liabilities
        WHERE is_annual = 0 AND quarter = 4
          AND total_assets IS NOT NULL AND total_liabilities IS NOT NULL
          AND total_equity IS NULL AND (total_assets - total_liabilities) > 0
    """)
    if cur.rowcount:
        conn.commit()
        logger.info(f"[Q4 BS] equity 파생 계산: {cur.rowcount}건")

    conn.close()
    logger.info(f"[Q4 BS] 완료. 총 {total_updated}행 업데이트")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=None)
    args = ap.parse_args()
    fix_q4_bs(target_year=args.year)
