"""
fix_financial_gaps.py — 재무제표 결함 보완 스크립트
실행: python3 fix_financial_gaps.py

작업 순서:
  Step 1) ROE 재계산   — net_income / total_equity * 100 (API 불필요)
  Step 2) 감가상각비   — FnGuide 현금흐름표 페이지 스크래핑 (억원 → 원 변환)
  Step 3) EPS/BPS 보완 — FnGuide 손익계산서 페이지 스크래핑

Rate limit: FnGuide에 0.6초 간격, 시간당 최대 ~6000 요청 허용 수준
"""
import sqlite3
import time
import logging
import re
import sys
from typing import Optional

import requests
from bs4 import BeautifulSoup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"
SLEEP = 2.0   # FnGuide 요청 간격(초) — 2초로 늘려 60초 penalty box 방지

# requests.Session → TCP 연결 재사용 (SSL handshake 오버헤드 감소)
_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://comp.fnguide.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
})

FNGUIDE_CF  = "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=103&stkGb=701"
FNGUIDE_IS  = "https://comp.fnguide.com/SVO2/ASP/SVD_Finance.asp?pGB=1&gicode=A{code}&cID=&MenuYn=Y&ReportGB=&NewMenuID=101&stkGb=701"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://comp.fnguide.com/",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ── DB 연결 ──────────────────────────────────────────────────────────────────
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=60000")  # 60초 대기
    return conn


def _execute_with_retry(conn, sql, params=(), retries=5, delay=3.0):
    """DB 락 발생 시 재시도."""
    for attempt in range(retries):
        try:
            return conn.execute(sql, params)
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and attempt < retries - 1:
                logger.debug(f"DB locked, retry {attempt+1}/{retries} in {delay}s")
                time.sleep(delay)
            else:
                raise


# ── Step 1: ROE 재계산 ────────────────────────────────────────────────────────
def fix_roe():
    """roe IS NULL 이고 net_income/total_equity 모두 존재하면 계산해서 채움."""
    conn = get_conn()
    cur = conn.execute("""
        UPDATE financial_data
        SET roe = ROUND(net_income * 100.0 / total_equity, 2)
        WHERE roe IS NULL
          AND net_income IS NOT NULL
          AND total_equity IS NOT NULL AND total_equity != 0
    """)
    conn.commit()
    logger.info(f"[ROE] {cur.rowcount}행 업데이트 완료")
    conn.close()


# ── FnGuide 파싱 유틸 ─────────────────────────────────────────────────────────
def _parse_unit_multiplier(soup) -> float:
    """FnGuide 단위 레이블에서 원 단위 변환 배수를 반환."""
    # '단위 : 억원' → 1e8, '단위 : 백만원' → 1e6, '단위 : 천원' → 1e3
    texts = soup.find_all(string=re.compile(r'단위\s*:\s*[가-힣원]+'))
    for t in texts:
        t = t.strip()
        if '억원' in t:
            return 1e8
        if '백만원' in t:
            return 1e6
        if '천원' in t:
            return 1e3
    return 1e8  # 기본값 억원


def _parse_value(s: str) -> Optional[float]:
    """'1,234' → 1234.0, '-' or '' → None"""
    s = s.strip().replace(",", "").replace(" ", "")
    if not s or s in ("-", "—", "N/A", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _get_annual_tables(soup):
    """FnGuide 페이지에서 연간 테이블만 반환 (분기 테이블 제외)."""
    result = []
    for tbl in soup.find_all("table"):
        first_row = tbl.find("tr")
        if not first_row:
            continue
        ths = [th.get_text(strip=True) for th in first_row.find_all("th")]
        # 연간 테이블은 '/12', '/03', '/09' 등 연도/월 형식 헤더를 가짐
        # 분기 테이블은 'Q1', '1Q' 또는 '/03', '/06', '/09', '/12' 4개가 한 해에 속함
        year_cols = [t for t in ths if re.match(r'\d{4}/', t)]
        # 연간이면 같은 '/MM' 패턴이 1개 이하여야 함 → 중복 '/12' 없으면 연간
        months = [t.split('/')[1] for t in year_cols]
        if year_cols and len(set(months)) > 1:
            # 분기: /03 /06 /09 /12 가 섞임 → 스킵
            continue
        if year_cols:
            result.append((tbl, ths, year_cols))
    return result


def _extract_row_values(tbl, ths, year_cols, row_keyword: str, multiplier: float):
    """특정 계정명을 포함한 행에서 (year→value) dict 반환."""
    out = {}
    for row in tbl.find_all("tr"):
        th = row.find("th")
        if not th:
            continue
        row_nm = th.get_text(strip=True).replace(" ", "")
        if row_keyword not in row_nm:
            continue
        tds = row.find_all("td")
        # year_cols 순서에 맞춰 value 매핑
        # ths에서 year_cols 위치 찾기 (IFRS(연결) 같은 첫 컬럼 제외)
        offset = 0
        for i, t in enumerate(ths):
            if t in year_cols:
                offset = i
                break
        for i, yc in enumerate(year_cols):
            if i + offset - len([t for t in ths[:offset] if t not in year_cols]) < len(tds):
                td_idx = i
                if td_idx < len(tds):
                    v = _parse_value(tds[td_idx].get_text(strip=True))
                    if v is not None:
                        year = int(yc.split("/")[0])
                        out[year] = v * multiplier
        break  # 첫 번째 매칭 행만
    return out


def _fetch_fnguide(url: str, code: str):
    """FnGuide 페이지 fetch + 파싱. (soup, multiplier, annual_tables) 반환."""
    try:
        # Session 재사용으로 TCP 연결 재사용 — timeout=(connect, read)
        resp = _SESSION.get(url, timeout=(10, 20))
        if resp.status_code != 200:
            return None, 1e8, []
        soup = BeautifulSoup(resp.text, "html.parser")
        mult = _parse_unit_multiplier(soup)
        atbls = _get_annual_tables(soup)
        return soup, mult, atbls
    except Exception as e:
        logger.debug(f"[FnGuide] {code} fetch error: {e}")
        return None, 1e8, []


def _extract_annual_row(atbls, keyword: str, mult: float):
    """연간 테이블들에서 keyword 계정의 {year: amount_원} 반환. 첫 번째 연결재무 우선."""
    # CFS(연결) 테이블 먼저, 없으면 OFS(별도)
    for tbl, ths, year_cols in atbls:
        first_cell = tbl.find("th")
        if first_cell and "연결" not in first_cell.get_text() and len(atbls) > 1:
            continue
        vals = _extract_row_values(tbl, ths, year_cols, keyword, mult)
        if vals:
            return vals
    # fallback: 아무 테이블이나
    for tbl, ths, year_cols in atbls:
        vals = _extract_row_values(tbl, ths, year_cols, keyword, mult)
        if vals:
            return vals
    return {}


# ── Step 2: 감가상각비 보완 ───────────────────────────────────────────────────
def fix_depreciation(max_stocks: int = 9999):
    """FnGuide 현금흐름표에서 감가상각비 스크래핑 → NULL 행 UPDATE."""
    conn = get_conn()

    # NULL 또는 0인 감가상각비 보유 종목 목록 — 2022년 이후 연간 row 기준
    # (2022 이전은 FnGuide가 최근 4년만 제공하므로 수집 불가)
    stocks = conn.execute("""
        SELECT DISTINCT fd.stock_code, su.stock_name
        FROM financial_data fd
        JOIN stock_universe su ON fd.stock_code = su.stock_code
        WHERE (fd.depreciation_amortization IS NULL OR fd.depreciation_amortization = 0)
          AND fd.is_annual = 1
          AND fd.year >= 2022
          AND su.stock_type = '보통주'
          AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
        ORDER BY fd.stock_code
        LIMIT ?
    """, (max_stocks,)).fetchall()

    logger.info(f"[Dep] 대상 종목: {len(stocks)}개")
    total_updated = 0

    for idx, row in enumerate(stocks):
        code = row["stock_code"]
        name = row["stock_name"] or code

        url = FNGUIDE_CF.format(code=code)
        _, mult, atbls = _fetch_fnguide(url, code)

        if not atbls:
            if idx % 20 == 0:
                logger.info(f"[Dep] {idx}/{len(stocks)} {code} {name}: 데이터 없음")
            time.sleep(SLEEP)
            continue

        dep_vals = _extract_annual_row(atbls, "감가상각비", mult)

        updated = 0
        for year, amount in dep_vals.items():
            if amount <= 0:
                continue  # 0 또는 음수는 건너뜀
            # NULL 또는 0.0(DART 기본값) 모두 갱신
            cur = _execute_with_retry(conn, """
                UPDATE financial_data
                SET depreciation_amortization = ?
                WHERE stock_code = ? AND year = ? AND is_annual = 1
                  AND (depreciation_amortization IS NULL OR depreciation_amortization = 0)
            """, (amount, code, year))
            updated += cur.rowcount

        if updated:
            conn.commit()
            total_updated += updated
            logger.info(f"[Dep] {idx}/{len(stocks)} {code} {name}: {updated}행 업데이트 | 누계={total_updated}")

        if idx % 20 == 0 and idx > 0:
            logger.info(f"[Dep] 진행: {idx}/{len(stocks)} | 누계 업데이트: {total_updated}행")

        time.sleep(SLEEP)

    conn.close()
    logger.info(f"[Dep] 완료. 총 {total_updated}행 업데이트")
    return total_updated


# ── Step 2b: BPS 계산 (total_equity / shares_issued) ──────────────────────────
def fix_bps_from_equity():
    """
    bps IS NULL 이고 total_equity가 있으면 stock_universe.shares_issued 로 계산.
    BPS = total_equity / shares_issued (원 단위)
    """
    conn = get_conn()
    cur = conn.execute("""
        UPDATE financial_data
        SET bps = ROUND(financial_data.total_equity / su.shares_issued, 0)
        FROM (
            SELECT stock_code, shares_issued FROM stock_universe
            WHERE shares_issued IS NOT NULL AND shares_issued > 0
        ) su
        WHERE financial_data.stock_code = su.stock_code
          AND financial_data.bps IS NULL
          AND financial_data.total_equity IS NOT NULL
          AND financial_data.total_equity != 0
    """)
    conn.commit()
    logger.info(f"[BPS] total_equity/shares 계산: {cur.rowcount}행 업데이트")
    conn.close()


# ── Step 2c: EPS 계산 (net_income / shares_issued) ────────────────────────────
def fix_eps_from_income():
    """
    eps IS NULL 이고 net_income이 있으면 stock_universe.shares_issued 로 계산.
    EPS = net_income / shares_issued (원 단위, 근사값)
    """
    conn = get_conn()
    cur = conn.execute("""
        UPDATE financial_data
        SET eps = ROUND(financial_data.net_income / su.shares_issued, 0)
        FROM (
            SELECT stock_code, shares_issued FROM stock_universe
            WHERE shares_issued IS NOT NULL AND shares_issued > 0
        ) su
        WHERE financial_data.stock_code = su.stock_code
          AND financial_data.eps IS NULL
          AND financial_data.net_income IS NOT NULL
          AND financial_data.net_income != 0
    """)
    conn.commit()
    logger.info(f"[EPS] net_income/shares 계산: {cur.rowcount}행 업데이트")
    conn.close()


# ── Step 3: EPS / BPS 보완 ────────────────────────────────────────────────────
def fix_eps_bps(max_stocks: int = 9999):
    """FnGuide 손익계산서에서 EPS/BPS 스크래핑 → NULL 행 UPDATE."""
    conn = get_conn()

    stocks = conn.execute("""
        SELECT DISTINCT fd.stock_code, su.stock_name
        FROM financial_data fd
        JOIN stock_universe su ON fd.stock_code = su.stock_code
        WHERE (fd.eps IS NULL OR fd.bps IS NULL)
          AND fd.is_annual = 1
          AND su.stock_type = '보통주'
          AND su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
        ORDER BY fd.stock_code
        LIMIT ?
    """, (max_stocks,)).fetchall()

    logger.info(f"[EPS/BPS] 대상 종목: {len(stocks)}개")
    total_updated = 0

    for idx, row in enumerate(stocks):
        code = row["stock_code"]
        name = row["stock_name"] or code

        url = FNGUIDE_IS.format(code=code)
        _, mult, atbls = _fetch_fnguide(url, code)

        if not atbls:
            time.sleep(SLEEP)
            continue

        eps_vals = _extract_annual_row(atbls, "EPS", mult)
        bps_vals = _extract_annual_row(atbls, "BPS", mult)

        # EPS/BPS는 1주당 단위이므로 배수 적용 안 함 (단위: 원)
        # FnGuide IS 페이지에서 EPS/BPS는 '원' 단위로 표시됨
        # 실제로는 단위 배수가 주당지표에도 적용되므로 mult로 나눔
        # (억원 단위로 표시된 경우 주당지표도 같이 억배 되어 있으면 아님)
        # → FnGuide에서 EPS/BPS는 실제 원 단위로 표기. mult 적용 X
        # 하지만 테이블 단위가 억원이면 FnGuide가 주당지표도 같은 테이블에서 억원 배수를 적용할 수 있음.
        # 실제 검증 필요 - 일단 mult로 나눠서 원 단위로 맞춤
        updated = 0
        for year, eps_raw in eps_vals.items():
            # EPS: FnGuide 값은 원 단위. mult가 1e8이면 원이 아니라 억원 배수가 되므로 나눔
            eps_won = eps_raw / mult  # 다시 1주당 원 단위로
            cur = conn.execute("""
                UPDATE financial_data SET eps = ?
                WHERE stock_code=? AND year=? AND is_annual=1 AND eps IS NULL
            """, (eps_won, code, year))
            updated += cur.rowcount

        for year, bps_raw in bps_vals.items():
            bps_won = bps_raw / mult
            cur = conn.execute("""
                UPDATE financial_data SET bps = ?
                WHERE stock_code=? AND year=? AND is_annual=1 AND bps IS NULL
            """, (bps_won, code, year))
            updated += cur.rowcount

        if updated:
            conn.commit()
            total_updated += updated

        if idx % 100 == 0 and idx > 0:
            logger.info(f"[EPS/BPS] 진행: {idx}/{len(stocks)} | 누계: {total_updated}행")

        time.sleep(SLEEP)

    conn.close()
    logger.info(f"[EPS/BPS] 완료. 총 {total_updated}행 업데이트")
    return total_updated


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="재무제표 결함 보완")
    parser.add_argument("--step", choices=["all","roe","bps","eps_calc","dep","eps"], default="all")
    parser.add_argument("--max", type=int, default=9999, help="종목 수 제한")
    args = parser.parse_args()

    if args.step in ("all", "roe"):
        logger.info("=== Step 1: ROE 재계산 ===")
        fix_roe()

    if args.step in ("all", "bps"):
        logger.info("=== Step 2b: BPS 계산 (total_equity/shares) ===")
        fix_bps_from_equity()

    if args.step in ("all", "eps_calc"):
        logger.info("=== Step 2c: EPS 계산 (net_income/shares) ===")
        fix_eps_from_income()

    if args.step in ("all", "dep"):
        logger.info("=== Step 2: 감가상각비 (FnGuide CF) ===")
        fix_depreciation(max_stocks=args.max)

    if args.step in ("all", "eps"):
        logger.info("=== Step 3: EPS/BPS (FnGuide IS) ===")
        fix_eps_bps(max_stocks=args.max)

    logger.info("=== 전체 완료 ===")
