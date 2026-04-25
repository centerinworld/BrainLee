"""
collect_naver_cashflow.py — Naver Mobile Finance API 현금흐름 수집기

DART에서 감가상각비 등 CF 항목이 누락된 종목을 위한 대체 수집기.
Naver Mobile API (JSON)를 1차 시도, WISEreport HTML을 2차 시도.

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
    "Accept": "application/json, text/plain, */*",
}

# ── 계정명 → DB 필드 매핑 ─────────────────────────────────────────────────────
_CF_KEYWORD_MAP = [
    ("operating_cf",  ["영업활동으로인한현금흐름", "영업활동현금흐름",
                       "영업활동으로인한순현금흐름", "영업활동"]),
    ("investing_cf",  ["투자활동으로인한현금흐름", "투자활동현금흐름",
                       "투자활동으로인한순현금흐름", "투자활동"]),
    ("financing_cf",  ["재무활동으로인한현금흐름", "재무활동현금흐름",
                       "재무활동으로인한순현금흐름", "재무활동"]),
    ("capex",         ["유형자산의취득", "유형자산취득", "유형자산의증가",
                       "유형자산의구입"]),
    ("cash_end",      ["기말의현금및현금성자산", "현금및현금성자산의기말잔액",
                       "기말현금및현금성자산", "현금및현금성자산기말잔액",
                       "기말현금", "현금및현금성자산순증감"]),
    ("depreciation",  ["감가상각비및무형자산상각비", "감가상각비와무형자산상각비",
                       "유무형자산상각비", "유형및무형자산상각비",
                       "감가상각비(유무형자산)", "감가상각비및상각비",
                       "유형자산에대한감가상각비", "유형자산의감가상각비",
                       "사용권자산에대한감가상각비", "사용권자산상각비",
                       "유형자산감가상각비", "유형자산상각비",
                       "감가상각비"]),
]

# 오탐 방지: 이 키워드가 포함되면 매칭 제외
_EXCLUDE_KEYWORDS = ["처분", "손상", "취득원가", "장부금액", "기타상각", "대손"]


def _match_field(acc: str) -> Optional[str]:
    """계정명 → 필드명. 매칭 안되면 None. 긴 키워드 우선 매칭."""
    acc_clean = acc.replace(" ", "").replace("\xa0", "").replace("　", "")
    if any(ex in acc_clean for ex in _EXCLUDE_KEYWORDS):
        return None
    # 각 필드의 키워드는 긴 것부터 정렬되어 있으므로 순서대로 매칭
    for field, keywords in _CF_KEYWORD_MAP:
        for kw in keywords:
            if kw in acc_clean:
                return field
    return None


def _parse_amount(text: str) -> Optional[float]:
    """금액 문자열 → float. 실패 시 None."""
    s = str(text).strip().replace(",", "").replace(" ", "").replace("\xa0", "")
    if not s or s in ("-", "—", "N/A", "null", "None", ""):
        return None
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
    "2023.12" / "2023/12" → (2023, 4, True)
    "2023.03" → (2023, 1, False)
    """
    h = str(header).strip()
    m = re.match(r"(\d{4})[./](\d{1,2})", h)
    if m:
        yr, mo = int(m.group(1)), int(m.group(2))
        if mo == 12:  return (yr, 4, True)
        if mo == 3:   return (yr, 1, False)
        if mo == 6:   return (yr, 2, False)
        if mo == 9:   return (yr, 3, False)
    m2 = re.match(r"^(\d{4})$", h)
    if m2:
        return (int(m2.group(1)), 4, True)
    return None


# ═══════════════════════════════════════════════════════════════
# 1차: Naver Mobile Finance API (JSON)
# ═══════════════════════════════════════════════════════════════

def _fetch_naver_mobile_cf(code: str, rpt_type: str = "y") -> dict[tuple, dict]:
    """
    Naver Mobile API → CF dict.
    rpt_type: 'y'=연간, 'q'=분기
    반환: {(year, quarter, is_annual): {field: value}}
    """
    url = f"https://m.stock.naver.com/api/stock/{code}/finance/detail?finType=cf&rptType={rpt_type}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.debug(f"[{code}] Naver Mobile API HTTP {resp.status_code}")
            return {}
        data = resp.json()
    except Exception as e:
        logger.debug(f"[{code}] Naver Mobile API 오류: {e}")
        return {}

    return _parse_naver_mobile_json(code, data, rpt_type)


def _parse_naver_mobile_json(code: str, data: dict | list,
                              rpt_type: str) -> dict[tuple, dict]:
    """
    Naver Mobile API JSON 파싱 (다양한 응답 구조 대응).

    알려진 구조 패턴:
    A) {"financeInfo": {"term": [...], "financeItemList": [{"title":..., "valueList":[...]}, ...]}}
    B) {"header": [...], "body": [{"label":..., "values":[...]}, ...]}
    C) [{"category":..., "data": [{"term":..., "value":...}, ...]}]
    D) {"financeDataList": [{"financeItemList": [...], "term": [...]}]}
    """
    result: dict[tuple, dict] = {}
    is_annual = (rpt_type == "y")

    try:
        # ── 패턴 A: financeInfo.financeItemList ──────────────────
        if isinstance(data, dict) and "financeInfo" in data:
            fi = data["financeInfo"]
            terms = fi.get("term", []) or fi.get("terms", [])
            items = fi.get("financeItemList", []) or fi.get("itemList", [])
            if terms and items:
                return _parse_pattern_terms_items(terms, items, is_annual)

        # ── 패턴 D: financeDataList ───────────────────────────────
        if isinstance(data, dict) and "financeDataList" in data:
            for entry in data["financeDataList"]:
                terms = entry.get("term", [])
                items = entry.get("financeItemList", [])
                if terms and items:
                    partial = _parse_pattern_terms_items(terms, items, is_annual)
                    result.update(partial)
            return result

        # ── 패턴 B: header / body ─────────────────────────────────
        if isinstance(data, dict) and "header" in data and "body" in data:
            terms = data["header"]
            for row in data["body"]:
                label = row.get("label", "") or row.get("title", "")
                field = _match_field(label)
                if field is None:
                    continue
                vals = row.get("values", []) or row.get("valueList", [])
                for i, term in enumerate(terms):
                    pk = _parse_period(term)
                    if pk is None or i >= len(vals):
                        continue
                    raw = vals[i]
                    v = _parse_amount(raw.get("value", raw) if isinstance(raw, dict) else raw)
                    if v is None:
                        continue
                    if pk not in result:
                        result[pk] = {}
                    _set_field(result[pk], field, v)
            return result

        # ── 패턴 C: list of category dicts ────────────────────────
        if isinstance(data, list):
            for item in data:
                label = item.get("category", "") or item.get("title", "")
                field = _match_field(label)
                if field is None:
                    continue
                for entry in item.get("data", []):
                    pk = _parse_period(entry.get("term", ""))
                    if pk is None:
                        continue
                    v = _parse_amount(entry.get("value", ""))
                    if v is None:
                        continue
                    if pk not in result:
                        result[pk] = {}
                    _set_field(result[pk], field, v)
            return result

        # ── 알 수 없는 구조: 디버그 로그 ──────────────────────────
        logger.debug(f"[{code}] Naver Mobile JSON 구조 미인식: {str(data)[:200]}")

    except Exception as e:
        logger.debug(f"[{code}] Naver Mobile JSON 파싱 오류: {e}")

    return result


def _parse_pattern_terms_items(terms: list, items: list,
                                is_annual: bool) -> dict[tuple, dict]:
    """패턴 A/D 공통 파서: terms 리스트 + financeItemList."""
    result: dict[tuple, dict] = {}
    for row in items:
        label = row.get("title", "") or row.get("label", "")
        field = _match_field(label)
        # 최상위 항목만 우선 파싱; 하위 항목(children)은 depreciation 합산용
        val_list = row.get("valueList", []) or row.get("values", [])
        for i, term in enumerate(terms):
            pk = _parse_period(term)
            if pk is None or i >= len(val_list):
                continue
            raw = val_list[i]
            v = _parse_amount(raw.get("value", raw) if isinstance(raw, dict) else raw)
            if v is None:
                continue
            if field is not None:
                if pk not in result:
                    result[pk] = {}
                _set_field(result[pk], field, v)

        # 하위 항목 재귀 처리 (감가상각비 sub-line 합산)
        for child in row.get("children", []) or row.get("subList", []):
            child_label = child.get("title", "") or child.get("label", "")
            child_field = _match_field(child_label)
            if child_field is None:
                continue
            child_vals = child.get("valueList", []) or child.get("values", [])
            for i, term in enumerate(terms):
                pk = _parse_period(term)
                if pk is None or i >= len(child_vals):
                    continue
                raw = child_vals[i]
                v = _parse_amount(raw.get("value", raw) if isinstance(raw, dict) else raw)
                if v is None:
                    continue
                if pk not in result:
                    result[pk] = {}
                _set_field(result[pk], child_field, v)

    return result


def _set_field(d: dict, field: str, val: float) -> None:
    """필드 설정 (capex는 절댓값, depreciation은 합산)."""
    if field == "capex":
        val = abs(val)
    if field == "depreciation" and field in d:
        d[field] += abs(val)  # 여러 상각비 합산
    else:
        if field == "depreciation":
            val = abs(val)
        d[field] = val


# ═══════════════════════════════════════════════════════════════
# 2차 fallback: WISEreport HTML (개선)
# ═══════════════════════════════════════════════════════════════

def _scrape_wisereport(code: str, freq: str = "A") -> dict[tuple, dict]:
    """
    WISEreport CF 페이지 HTML 파싱 (JavaScript 렌더 없이 접근 가능한 경우).
    freq: "A"=연간, "Q"=분기
    """
    url = (
        f"https://navercomp.wisereport.co.kr/v2/company/c1020001.aspx"
        f"?cmp_cd={code}&fin_typ=0&freq_typ={freq}"
    )
    referer = f"https://finance.naver.com/item/main.naver?code={code}"
    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Referer": referer},
            timeout=15,
        )
        # WISEreport는 EUC-KR 또는 UTF-8 둘 다 가능
        if resp.status_code != 200:
            return {}
        # 인코딩 자동 감지
        resp.encoding = resp.apparent_encoding or "utf-8"
    except Exception as e:
        logger.debug(f"[{code}] WISEreport 요청 실패: {e}")
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")
    tables = soup.find_all("table")
    target_table = None
    for tbl in tables:
        text = tbl.get_text()
        if "영업활동" in text and ("투자활동" in text or "재무활동" in text):
            target_table = tbl
            break

    if target_table is None:
        logger.debug(f"[{code}] WISEreport: 현금흐름 테이블 없음 (freq={freq})")
        return {}

    rows = target_table.find_all("tr")
    if len(rows) < 2:
        return {}

    header_cells = rows[0].find_all(["th", "td"])
    periods = [_parse_period(c.get_text(strip=True)) for c in header_cells[1:]]
    if not any(periods):
        return {}

    is_annual = (freq == "A")
    result: dict[tuple, dict] = {}

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue
        label = cells[0].get_text(strip=True).replace("\xa0", "")
        field = _match_field(label)
        if field is None:
            continue
        for col_idx, pk in enumerate(periods):
            if pk is None or col_idx + 1 >= len(cells):
                continue
            v = _parse_amount(cells[col_idx + 1].get_text(strip=True))
            if v is None:
                continue
            if pk not in result:
                result[pk] = {}
            _set_field(result[pk], field, v)

    logger.debug(f"[{code}] WISEreport freq={freq}: {len(result)}기간 수집")
    return result


# ═══════════════════════════════════════════════════════════════
# DB upsert
# ═══════════════════════════════════════════════════════════════

def _upsert_cf(conn: sqlite3.Connection, code: str,
               year: int, quarter: int, is_annual: bool,
               fields: dict, force: bool = False) -> bool:
    """
    cash_flow_data 테이블에 upsert.
    force=False → NULL 컬럼만 업데이트 (기존 DART 값 보존).
    """
    row = conn.execute(
        "SELECT operating_cf, investing_cf, financing_cf, capex, cash_end, depreciation "
        "FROM cash_flow_data WHERE stock_code=? AND year=? AND quarter=? AND is_annual=?",
        (code, year, quarter, is_annual)
    ).fetchone()

    if row is None:
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

    col_names = ["operating_cf", "investing_cf", "financing_cf",
                 "capex", "cash_end", "depreciation"]
    updates, params = [], []
    for i, col in enumerate(col_names):
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
    1차: Naver Mobile API (JSON) — 연간 + 분기
    2차: WISEreport HTML — 연간 + 분기
    force=False: NULL 컬럼만 채움 (DART 값 보존)
    반환: 저장된 레코드 수
    """
    conn = sqlite3.connect(db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    saved = 0

    try:
        # ── 1차: Naver Mobile API ────────────────────────────────
        all_data: dict[tuple, dict] = {}
        for rpt_type in ["y", "q"]:
            partial = _fetch_naver_mobile_cf(stock_code, rpt_type)
            for pk, fields in partial.items():
                if pk not in all_data:
                    all_data[pk] = {}
                all_data[pk].update(fields)
            time.sleep(0.3)

        if all_data:
            logger.info(f"[{stock_code}] Naver Mobile: {len(all_data)}기간 수집")
            for (yr, q, is_ann), fields in all_data.items():
                ok = _upsert_cf(conn, stock_code, yr, q, is_ann, fields, force)
                if ok:
                    saved += 1
                    logger.info(f"[{stock_code}] 저장: {yr}Q{q} is_annual={is_ann} {fields}")
        else:
            logger.info(f"[{stock_code}] Naver Mobile: 데이터 없음 → WISEreport fallback")

        # ── 2차: WISEreport (누락 기간 보완) ─────────────────────
        for freq, is_annual_flag in [("A", True), ("Q", False)]:
            # Naver Mobile로 이미 충분히 수집했으면 연간만 보완
            wise_data = _scrape_wisereport(stock_code, freq)
            for (yr, q, is_ann), fields in wise_data.items():
                if is_ann != is_annual_flag:
                    continue
                # Naver Mobile에 없었던 필드만 채우기 (force 무시하고 NULL 보완)
                ok = _upsert_cf(conn, stock_code, yr, q, is_ann, fields,
                                force=force)
                if ok:
                    saved += 1
                    logger.info(f"[{stock_code}] WISEreport 보완: {yr}Q{q} {fields}")
            time.sleep(0.3)

    finally:
        conn.close()

    logger.info(f"[{stock_code}] 완료: {saved}건 저장")
    return saved


def fill_missing_depreciation(db_path: str = DB_PATH,
                               limit: int = 0,
                               rate: float = 0.5) -> None:
    """
    cash_flow_data.depreciation이 NULL인 종목 전체 순회.
    limit=0 → 전체
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
    parser = argparse.ArgumentParser(description="Naver CF 수집기")
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
