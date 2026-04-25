"""
collect_naver_cashflow.py — WISEreport cF1001.aspx CF 수집기

DART에서 CF 항목이 누락된 종목을 위한 대체 수집기.
WISEreport cF1001.aspx에서 영업/투자/재무CF + CAPEX를 수집한다.
감가상각비(depreciation)는 이 소스에서 제공되지 않음.

데이터 소스:
  https://navercomp.wisereport.co.kr/v2/company/cF1001.aspx?cmp_cd={code}&fin_typ=0&freq_typ=A

단위: WISEreport는 억원 단위 → DB 저장 시 × 100,000,000 (원 변환)

사용법:
    python collect_naver_cashflow.py --code 078150          # 단일 종목
    python collect_naver_cashflow.py --missing              # NULL 전체
    python collect_naver_cashflow.py --code 078150 --force  # 덮어쓰기

외부 호출:
    from collect_naver_cashflow import scrape_and_save_cf
    n = scrape_and_save_cf("078150", db_path=DB_PATH, force=False)
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import time
from datetime import date
from typing import Optional

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"

# WISEreport 단위: 억원 → 원 변환 배수
_UNIT_MULT = 100_000_000

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# cF1001 table 행 레이블 → DB 필드
_CF1001_LABEL_MAP = {
    "영업활동현금흐름": "operating_cf",
    "투자활동현금흐름": "investing_cf",
    "재무활동현금흐름": "financing_cf",
    "CAPEX":           "capex",
}


def _parse_amount(text: str) -> Optional[float]:
    """금액 문자열 → float (억원 단위 raw값). 실패 시 None."""
    s = str(text).strip().replace(",", "").replace(" ", "").replace("\xa0", "")
    if not s or s in ("-", "—", "N/A", ""):
        return None
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.lstrip("(-").rstrip(")")
    try:
        val = float(s)
        return -val if neg else val
    except ValueError:
        return None


def _parse_period(text: str) -> Optional[tuple[int, int]]:
    """
    "2023/12(IFRS연결)" → (2023, 12)
    실패 시 None.
    """
    m = re.match(r"(\d{4})/(\d{1,2})", text.strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


# ═══════════════════════════════════════════════════════════════
# WISEreport cF1001.aspx 파서
# ═══════════════════════════════════════════════════════════════

def _scrape_cf1001(code: str) -> dict[tuple, dict]:
    """
    cF1001.aspx 파싱.
    반환: {(year, quarter, is_annual): {field: value_in_원}}
    감가상각비 미제공 — operating_cf/investing_cf/financing_cf/capex만 수집.
    """
    url = (
        f"https://navercomp.wisereport.co.kr/v2/company/cF1001.aspx"
        f"?cmp_cd={code}&fin_typ=0&freq_typ=A"
    )
    referer = f"https://finance.naver.com/item/main.naver?code={code}"

    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Referer": referer},
            timeout=15,
        )
        if resp.status_code != 200:
            logger.debug(f"[{code}] cF1001 HTTP {resp.status_code}")
            return {}
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        logger.debug(f"[{code}] cF1001 요청 실패: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        return {}

    table = tables[0]
    rows = table.find_all("tr")
    if len(rows) < 3:
        return {}

    # ── 기간 헤더 파싱 ────────────────────────────────────────
    # 헤더에서 YYYY/MM 패턴 셀을 모두 추출
    period_row_cells = []
    for row in rows:
        cells = row.find_all(["th", "td"])
        texts = [c.get_text(strip=True) for c in cells]
        date_cnt = sum(1 for t in texts if re.match(r"\d{4}/\d{2}", t))
        if date_cnt >= 4:
            period_row_cells = texts
            break

    if not period_row_cells:
        logger.debug(f"[{code}] cF1001: 기간 헤더 없음")
        return {}

    # 실제 데이터 행의 값 셀 수 확인 (레이블 + N값)
    data_col_count = 0
    for row in rows:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        label = cells[0].get_text(strip=True)
        if label in _CF1001_LABEL_MAP:
            data_col_count = len(cells) - 1  # 레이블 제외
            break

    if data_col_count == 0:
        logger.debug(f"[{code}] cF1001: CF 행 없음")
        return {}

    # 헤더에서 날짜 파싱 (순서 유지, 20개 중 실제 data_col_count개 매핑)
    current_year = date.today().year
    all_periods: list[Optional[tuple]] = []
    for text in period_row_cells:
        p = _parse_period(text)
        if p is None:
            continue
        yr, mo = p
        q = {12: 4, 9: 3, 6: 2, 3: 1}.get(mo)
        if q is None:
            all_periods.append(None)
            continue
        all_periods.append((yr, mo, q))

    # 연간 / 분기 구분:
    # 헤더 첫 번째 연속 December 그룹 → is_annual=True
    # 이후 → is_annual=False
    # 미래(current_year+1 이후) 연간 및 미보고 분기 → None(스킵)
    annual_cutoff = 0
    prev_yr = None
    for i, p in enumerate(all_periods):
        if p is None:
            break
        yr, mo, q = p
        if mo == 12:
            if prev_yr is None or yr == prev_yr + 1:
                annual_cutoff = i + 1
                prev_yr = yr
            else:
                break
        else:
            break

    mapped_periods: list[Optional[tuple]] = []
    for i, p in enumerate(all_periods):
        if len(mapped_periods) >= data_col_count:
            break
        if p is None:
            mapped_periods.append(None)
            continue
        yr, mo, q = p
        is_annual = (i < annual_cutoff)
        # 스킵: 미래 연간, 아직 미공시 분기
        if is_annual and yr > current_year - 1:
            mapped_periods.append(None)
            continue
        if not is_annual and yr > current_year:
            mapped_periods.append(None)
            continue
        mapped_periods.append((yr, q, is_annual))

    # data_col_count에 맞게 길이 맞추기
    while len(mapped_periods) < data_col_count:
        mapped_periods.append(None)

    # ── 데이터 행 파싱 ─────────────────────────────────────────
    result: dict[tuple, dict] = {}

    for row in rows:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        label = cells[0].get_text(strip=True)
        field = _CF1001_LABEL_MAP.get(label)
        if field is None:
            continue

        val_cells = cells[1:]
        for i, pk in enumerate(mapped_periods):
            if pk is None or i >= len(val_cells):
                continue
            raw = val_cells[i].get_text(strip=True)
            val_억 = _parse_amount(raw)
            if val_억 is None:
                continue

            # 억원 → 원 변환
            val = val_억 * _UNIT_MULT
            if field == "capex":
                val = abs(val)

            if pk not in result:
                result[pk] = {}
            result[pk][field] = val

    logger.info(f"[{code}] cF1001: {len(result)}기간 수집")
    return result


# ═══════════════════════════════════════════════════════════════
# DB upsert
# ═══════════════════════════════════════════════════════════════

def _upsert_cf(conn: sqlite3.Connection, code: str,
               year: int, quarter: int, is_annual: bool,
               fields: dict, force: bool = False) -> bool:
    """NULL 컬럼만 채우는 upsert (force=False)."""
    row = conn.execute(
        "SELECT operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation "
        "FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?",
        (code, year, quarter, is_annual)
    ).fetchone()

    cf_cols = ["operating_cf", "investing_cf", "financing_cf",
               "capex", "cash_end", "depreciation"]

    if row is None:
        vals = [code, year, quarter, is_annual] + [fields.get(c) for c in cf_cols]
        ph = ",".join("?" * len(vals))
        conn.execute(
            f"INSERT INTO cash_flow_data (stock_code,year,quarter,is_annual,"
            f"operating_cf,investing_cf,financing_cf,capex,cash_end,depreciation) "
            f"VALUES ({ph})",
            vals
        )
        conn.commit()
        return True

    updates, params = [], []
    for i, col in enumerate(cf_cols):
        new_val = fields.get(col)
        if new_val is None:
            continue
        if force or row[i] is None:
            updates.append(f"{col}=?")
            params.append(new_val)

    if not updates:
        return False
    params += [code, year, quarter, is_annual]
    conn.execute(
        f"UPDATE cash_flow_data SET {', '.join(updates)} "
        f"WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?",
        params
    )
    conn.commit()
    return True


# ═══════════════════════════════════════════════════════════════
# 공개 API
# ═══════════════════════════════════════════════════════════════

def scrape_and_save_cf(stock_code: str,
                        db_path: str = DB_PATH,
                        force: bool = False) -> int:
    """
    단일 종목 CF 수집 → DB 저장.
    WISEreport cF1001 사용 (operating_cf/investing_cf/financing_cf/capex).
    depreciation은 이 소스에서 제공 안 됨 — fill_missing_cf_dart.py로 수집.
    반환: 저장된 레코드 수
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    saved = 0

    try:
        data = _scrape_cf1001(stock_code)
        if not data:
            logger.info(f"[{stock_code}] cF1001: 데이터 없음")
            return 0

        for (yr, q, is_ann), fields in data.items():
            ok = _upsert_cf(conn, stock_code, yr, q, is_ann, fields, force)
            if ok:
                saved += 1
                logger.info(f"[{stock_code}] 저장: {yr}Q{q} is_annual={is_ann} {fields}")

    finally:
        conn.close()

    logger.info(f"[{stock_code}] 완료: {saved}건 저장")
    return saved


def fill_missing_cf(db_path: str = DB_PATH,
                    limit: int = 0,
                    rate: float = 0.5) -> None:
    """
    operating_cf 또는 investing_cf가 NULL인 종목을 cF1001로 채운다.
    depreciation NULL은 fill_missing_cf_dart.py 사용.
    """
    conn = sqlite3.connect(db_path, timeout=30)
    rows = conn.execute("""
        SELECT DISTINCT stock_code FROM cash_flow_data
        WHERE (operating_cf IS NULL OR investing_cf IS NULL)
          AND LENGTH(stock_code) = 6
          AND stock_code GLOB '[0-9]*'
        ORDER BY stock_code
    """).fetchall()
    conn.close()

    codes = [r[0] for r in rows]
    if limit:
        codes = codes[:limit]

    logger.info(f"CF NULL 종목: {len(codes)}개")
    for i, code in enumerate(codes, 1):
        logger.info(f"[{i}/{len(codes)}] {code} 수집 중...")
        try:
            n = scrape_and_save_cf(code, db_path=db_path, force=False)
            logger.info(f"  → {n}건 업데이트")
        except Exception as e:
            logger.error(f"  → 실패: {e}")
        time.sleep(rate)


# ── CLI ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WISEreport cF1001 CF 수집기")
    parser.add_argument("--code",    help="종목코드 (6자리)")
    parser.add_argument("--missing", action="store_true",
                        help="operating_cf NULL 전체 채우기")
    parser.add_argument("--force",   action="store_true",
                        help="기존 데이터 덮어쓰기")
    parser.add_argument("--limit",   type=int, default=0)
    parser.add_argument("--db",      default=DB_PATH)
    args = parser.parse_args()

    if args.code:
        n = scrape_and_save_cf(args.code, db_path=args.db, force=args.force)
        print(f"저장: {n}건")
    elif args.missing:
        fill_missing_cf(db_path=args.db, limit=args.limit)
    else:
        parser.print_help()
