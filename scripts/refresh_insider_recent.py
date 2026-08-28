#!/usr/bin/env python3
"""
DART 임원매매 최신 데이터 수집 (2026-06-29 이후)
1. DART list API로 최근 임원·주요주주 공시 기업 corp_code 목록 수집
2. 각 corp_code로 elestock.json 개별 조회 → DB 저장
"""
import sys, time, sqlite3, requests, logging
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dart_key_manager import get_dart_api_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = str(ROOT / "stock.db")
DART_BASE = "https://opendart.fss.or.kr/api"

KEYS = get_dart_api_keys()
_ki = 0
_exhausted: set = set()

def _next_key():
    global _ki
    for _ in range(len(KEYS)):
        k = KEYS[_ki % len(KEYS)]
        _ki += 1
        if k not in _exhausted:
            return k
    return None

def get_recent_corp_codes(bgn_de: str, end_de: str) -> list[str]:
    """DART list API로 최근 임원·주요주주 공시 기업 corp_code 목록"""
    corp_codes = []
    page = 1
    while True:
        key = _next_key()
        if not key:
            break
        try:
            r = requests.get(f"{DART_BASE}/list.json", params={
                "crtfc_key": key,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "pblntf_ty": "D",
                "pblntf_detail_ty": "H001",
                "page_no": page,
                "page_count": 100,
            }, timeout=20)
            data = r.json()
        except Exception as e:
            log.warning(f"list API 오류: {e}")
            time.sleep(1)
            break

        if data.get("status") == "020":
            _exhausted.add(key)
            continue
        if data.get("status") != "000":
            log.info(f"list API status={data.get('status')}, msg={data.get('message','')}")
            break

        items = data.get("list", [])
        for item in items:
            cc = item.get("corp_code", "")
            if cc and cc not in corp_codes:
                corp_codes.append(cc)

        total = int(data.get("total_count", 0))
        log.info(f"list API page {page}: {len(items)}건 (총 {total}건, 누적 unique corp {len(corp_codes)}개)")
        if page * 100 >= total:
            break
        page += 1
        time.sleep(0.2)

    return corp_codes


def fetch_elestock(corp_code: str) -> list[dict]:
    """corp_code로 elestock 조회"""
    key = _next_key()
    if not key:
        return []
    for attempt in range(3):
        try:
            r = requests.get(f"{DART_BASE}/elestock.json", params={
                "crtfc_key": key,
                "corp_code": corp_code,
            }, timeout=15)
            d = r.json()
            if d.get("status") == "020":
                _exhausted.add(key)
                key = _next_key()
                if not key:
                    return []
                continue
            if d.get("status") == "000":
                return d.get("list", [])
            return []
        except Exception as e:
            log.warning(f"elestock 오류 ({corp_code}, attempt {attempt+1}): {e}")
            time.sleep(1)
    return []


def get_stock_code_for_corp(conn: sqlite3.Connection, corp_code: str) -> str:
    """corp_code → stock_code 변환 (dart_insider_holdings 기존 데이터 활용)"""
    row = conn.execute(
        "SELECT stock_code FROM dart_insider_holdings WHERE corp_code=? AND stock_code!='' LIMIT 1",
        (corp_code,)
    ).fetchone()
    return row[0] if row else ""


def main():
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")

    # 마지막 수집 날짜로부터
    last = conn.execute("SELECT MAX(rcept_dt) FROM dart_insider_holdings").fetchone()[0]
    if last:
        # last 날짜 포함하여 다시 수집 (당일 추가 공시 가능)
        bgn_de = last.replace("-","")
    else:
        bgn_de = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    end_de = datetime.now().strftime("%Y%m%d")

    log.info(f"수집 기간: {bgn_de} ~ {end_de}")

    corp_codes = get_recent_corp_codes(bgn_de, end_de)
    log.info(f"대상 기업: {len(corp_codes)}개")

    total_inserted = 0
    errors = 0

    for i, corp_code in enumerate(corp_codes):
        items = fetch_elestock(corp_code)
        stock_code = get_stock_code_for_corp(conn, corp_code)

        # stock_code를 items에서 찾기 (없으면 빈 문자열)
        for item in items:
            # 최근 날짜 항목만 처리
            rcept_dt = item.get("rcept_dt", "")
            if rcept_dt and rcept_dt.replace("-","") < bgn_de:
                continue

            if not stock_code:
                dd = conn.execute(
                    "SELECT stock_code FROM dart_disclosures WHERE rcept_no=? LIMIT 1",
                    (item.get("rcept_no",""),)
                ).fetchone()
                sc = dd[0] if dd else ""
            else:
                sc = stock_code

            change = None
            try:
                irds = str(item.get("sp_stock_lmp_irds_cnt",""))
                val = float(irds.replace(",","").replace("-","").replace("△","") or 0)
                if irds.startswith("-") or irds.startswith("△"):
                    val = -val
                change = val
            except Exception:
                pass

            is_ceo = 1 if "대표" in str(item.get("isu_exctv_ofcps","")) else 0

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO dart_insider_holdings
                    (rcept_no, rcept_dt, stock_code, corp_code, corp_name,
                     repror, isu_exctv_rgist, isu_exctv_ofcps, isu_main_shrholdr,
                     sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, sp_stock_lmp_irds_rate,
                     is_ceo, change_amount, raw_json, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """, (
                    item.get("rcept_no"), rcept_dt, sc,
                    corp_code, item.get("corp_name"),
                    item.get("repror"), item.get("isu_exctv_rgist_at"),
                    item.get("isu_exctv_ofcps"), item.get("isu_main_shrholdr"),
                    item.get("sp_stock_lmp_cnt"), item.get("sp_stock_lmp_irds_cnt"),
                    item.get("sp_stock_lmp_rate"), is_ceo, change, str(item),
                ))
                total_inserted += 1
            except Exception as e:
                errors += 1

        if (i+1) % 20 == 0:
            conn.commit()
            log.info(f"진행 {i+1}/{len(corp_codes)}: 누적 {total_inserted}건 삽입")
        time.sleep(0.25)

    conn.commit()
    row = conn.execute("SELECT COUNT(*), MAX(rcept_dt) FROM dart_insider_holdings").fetchone()
    conn.close()
    log.info(f"완료 — 삽입 {total_inserted}건, 오류 {errors}건, DB 총 {row[0]}건, 최신일 {row[1]}")


if __name__ == "__main__":
    main()
