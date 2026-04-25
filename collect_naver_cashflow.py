"""
collect_naver_cashflow.py — Naver Finance(WISEreport) 현금흐름 스크래퍼

DART에서 감가상각비 등 CF 항목이 누락된 종목을 위한 대체 수집기.
Naver Finance 내부에서 쓰는 WISEreport 페이지를 파싱한다.

사용법 (단독 실행):
    python collect_naver_cashflow.py --code 078150          # 에이엘티 개별
    python collect_naver_cashflow.py --missing              # depreciation NULL 전체
    python collect_naver_cashflow.py --code 078150 --force  # 기존 데이터 덮어쓰기

함수 외부 호출:
    from collect_naver_cashflow import scrape_and_save_cf
    n = scrape_and_save_cf(stock_code="078150", db_path=DB_PATH, force=False)
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9",
}

# ── 계정명 → DB 필드 매핑 (공백 제거 후 부분 매칭) ──────────────────────────
_CF_KEYWORD_MAP = [
    ("operating_cf",  ["영업활동으로인한현금흐름", "영업활동현금흐름",
                       "영업활동으로인한순현금흐름"]),
    ("investing_cf",  ["투자활동으로인한현금흐름", "투자활동현금흐름",
                       "투자활동으로인한순현금흐름"]),
    ("financing_cf",  ["재무활동으로인한현금흐름", "재무활동현금흐름",
                       "재무활동으로인한순현금흐름"]),
    ("capex",         ["유형자산의취득", "유형자산취득", "유형자산의증가",
                       "유형자산의구입"]),
    ("cash_end",      ["기말의현금및현금성자산", "현금및현금성자산의기말잔액",
                       "기말현금및현금성자산", "현금및현금성자산기말잔액",
                       "기말현금"]),
    ("depreciation",  ["감가상각비", "유형자산감가상각비", "유형자산상각비",
                       "감가상각비및상각비", "유무형자산상각비",
                       "감가상각비와무형자산상각비",
                       "사용권자산에대한감가상각비", "사용권자산상각비",
                       "유형자산의감가상각비", "유형및무형자산상각비",
                       "감가상각비(유무형자산)", "감가상각비및무형자산상각비",
                       "감가상각"]),
]

# 부분 매칭 시 제외할 키워드 (오탐 방지)
_EXCLUDE_KEYWORDS = ["처분", "손상", "취득원가", "장부금액", "기타"]


def _match_field(acc: str) -> Optional[str]:
    """계정명 → 필드명 매핑. 매칭 안되면 None."""
    acc_clean = acc.replace(" ", "").replace("\xa0", "").replace("　", "")
    for field, keywords in _CF_KEYWORD_MAP:
        for kw in keywords:
            if kw in acc_clean:
                # 오탐 방지
                if any(ex in acc_clean for ex in _EXCLUDE_KEYWORDS):
                    continue
                return field
    return None


def _parse_amount(text: str) -> Optional[float]:
    """금액 문자열 → float (단위: 원). 실패 시 None."""
    s = text.strip().replace(",", "").replace(" ", "").replace("\xa0", "")
    if not s or s in ("-", "—", "N/A", ""):
        return None
    # 음수 표기 처리 (괄호 표기 등)
    neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
    s = s.lstrip("(-").rstrip(")")
    try:
        val = float(s)
        return -val if neg else val
    except ValueError:
        return None


def _parse_period(header: str) -> Optional[tuple[int, int, bool]]:
    """
    기간 헤더 → (year, quarter, is_annual).
    "2023.12" → (2023, 4, True)
    "2023.03" → (2023, 1, False)
    """
    h = header.strip()
    # YYYY.MM 또는 YYYY/MM
    m = re.match(r"(\d{4})[./](\d{1,2})", h)
    if m:
        yr, mo = int(m.group(1)), int(m.group(2))
        if mo == 12:   return (yr, 4, True)
        elif mo == 3:  return (yr, 1, False)
        elif mo == 6:  return (yr, 2, False)
        elif mo == 9:  return (yr, 3, False)
    # YYYY 단독 (연간)
    m2 = re.match(r"^(\d{4})$", h)
    if m2:
        return (int(m2.group(1)), 4, True)
    return None


def _scrape_wisereport(code: str, freq: str = "A") -> dict[tuple, dict]:
    """
    WISEreport CF 페이지 스크래핑.
    freq: "A"=연간, "Q"=분기
    반환: {(year, quarter, is_annual): {field: value_in_won}}
    """
    url = (
        f"https://navercomp.wisereport.co.kr/v2/company/c1020001.aspx"
        f"?cmp_cd={code}&fin_typ=0&freq_typ={freq}"
    )
    referer = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        resp = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=15)
        resp.encoding = "euc-kr"
        if resp.status_code != 200:
            logger.warning(f"[{code}] WISEreport HTTP {resp.status_code}")
            return {}
    except Exception as e:
        logger.warning(f"[{code}] WISEreport 요청 실패: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    # 현금흐름 데이터가 있는 테이블 탐색
    tables = soup.find_all("table")
    target_table = None
    for tbl in tables:
        text = tbl.get_text()
        if "영업활동" in text and ("투자활동" in text or "재무활동" in text):
            target_table = tbl
            break

    if target_table is None:
        logger.warning(f"[{code}] WISEreport: 현금흐름 테이블 없음")
        return {}

    rows = target_table.find_all("tr")
    if len(rows) < 2:
        return {}

    # 헤더 행에서 기간 추출
    header_row = rows[0]
    header_cells = header_row.find_all(["th", "td"])
    periods = []
    for cell in header_cells[1:]:   # 첫 셀은 "항목" 레이블
        parsed = _parse_period(cell.get_text(strip=True))
        periods.append(parsed)  # None 포함 (컬럼 수 맞춤)

    if not any(periods):
        logger.warning(f"[{code}] WISEreport: 기간 헤더 파싱 실패")
        return {}

    # 결과: {(year, q, is_annual): {field: val}}
    result: dict[tuple, dict] = {}

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        label = cells[0].get_text(strip=True).replace("\xa0", "").replace(" ", "")
        field = _match_field(label)
        if field is None:
            continue

        for col_idx, period_key in enumerate(periods):
            if period_key is None:
                continue
            if col_idx + 1 >= len(cells):
                continue
            val = _parse_amount(cells[col_idx + 1].get_text(strip=True))
            if val is None:
                continue

            if period_key not in result:
                result[period_key] = {}

            # capex는 절댓값
            if field == "capex":
                val = abs(val)

            # depreciation: 이미 값이 있으면 합산 (여러 상각비 항목 합계)
            if field == "depreciation" and field in result[period_key]:
                result[period_key][field] += val
            else:
                result[period_key][field] = val

    logger.info(f"[{code}] WISEreport freq={freq}: {len(result)}기간 수집")
    return result


def _upsert_cf(conn: sqlite3.Connection, code: str,
               year: int, quarter: int, is_annual: bool,
               fields: dict, force: bool = False) -> bool:
    """
    cash_flow_data 테이블에 upsert.
    force=False이면 NULL 컬럼만 업데이트 (기존 DART 값 보존).
    반환: 실제 저장 여부
    """
    # 기존 레코드 조회
    row = conn.execute(
        "SELECT operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation "
        "FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?",
        (code, year, quarter, is_annual)
    ).fetchone()

    if row is None:
        # 새로 INSERT
        cols = ["stock_code", "year", "quarter", "is_annual",
                "operating_cf", "investing_cf", "financing_cf",
                "capex", "cash_end", "depreciation"]
        vals = [code, year, quarter, is_annual,
                fields.get("operating_cf"),
                fields.get("investing_cf"),
                fields.get("financing_cf"),
                fields.get("capex"),
                fields.get("cash_end"),
                fields.get("depreciation")]
        ph = ",".join("?" * len(cols))
        conn.execute(f"INSERT INTO cash_flow_data ({','.join(cols)}) VALUES ({ph})", vals)
        conn.commit()
        return True
    else:
        # 기존 레코드: force=True면 전체 덮어씌우기, False면 NULL만 채우기
        col_names = ["operating_cf", "investing_cf", "financing_cf",
                     "capex", "cash_end", "depreciation"]
        updates = []
        params  = []
        for i, col in enumerate(col_names):
            new_val = fields.get(col)
            if new_val is None:
                continue
            existing = row[i]
            if force or existing is None:
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


def scrape_and_save_cf(stock_code: str,
                        db_path: str = DB_PATH,
                        force: bool = False) -> int:
    """
    단일 종목 CF 스크래핑 후 DB 저장.
    force=False: NULL 컬럼만 채움 (DART 값 보존)
    force=True:  모든 컬럼 덮어쓰기
    반환: 저장된 레코드 수
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    saved = 0
    try:
        for freq, is_annual_flag in [("A", True), ("Q", False)]:
            data = _scrape_wisereport(stock_code, freq)
            for (yr, q, is_ann), fields in data.items():
                if is_ann != is_annual_flag:
                    continue  # 혼재 방지
                ok = _upsert_cf(conn, stock_code, yr, q, is_ann, fields, force)
                if ok:
                    saved += 1
                    logger.info(f"[{stock_code}] 저장: {yr}Q{q} is_annual={is_ann} {fields}")
            time.sleep(0.3)
    finally:
        conn.close()
    logger.info(f"[{stock_code}] 완료: {saved}건 저장")
    return saved


def fill_missing_depreciation(db_path: str = DB_PATH,
                               limit: int = 0,
                               rate: float = 0.5) -> None:
    """
    cash_flow_data.depreciation이 NULL인 종목 전체를 순회하며 채운다.
    limit=0 → 전체, limit=N → 상위 N개 종목
    """
    conn = sqlite3.connect(db_path, timeout=30)
    rows = conn.execute("""
        SELECT DISTINCT stock_code FROM cash_flow_data
        WHERE depreciation IS NULL
          AND LENGTH(stock_code) = 6
          AND stock_code GLOB '[0-9]*'
        ORDER BY stock_code
    """).fetchall()
    conn.close()

    codes = [r[0] for r in rows]
    if limit:
        codes = codes[:limit]

    logger.info(f"감가상각비 NULL 종목: {len(codes)}개")
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
    parser = argparse.ArgumentParser(description="Naver CF 스크래퍼")
    parser.add_argument("--code",    help="종목코드 (6자리)")
    parser.add_argument("--missing", action="store_true",
                        help="depreciation NULL 전체 채우기")
    parser.add_argument("--force",   action="store_true",
                        help="기존 데이터 덮어쓰기")
    parser.add_argument("--limit",   type=int, default=0,
                        help="처리할 최대 종목 수 (0=전체)")
    parser.add_argument("--db",      default=DB_PATH,
                        help="DB 경로")
    args = parser.parse_args()

    if args.code:
        n = scrape_and_save_cf(args.code, db_path=args.db, force=args.force)
        print(f"저장: {n}건")
    elif args.missing:
        fill_missing_depreciation(db_path=args.db, limit=args.limit)
    else:
        parser.print_help()
