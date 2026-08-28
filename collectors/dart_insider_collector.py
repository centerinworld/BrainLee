#!/usr/bin/env python3
"""
DART 임원·주요주주 특정증권 소유상황 수집기
API: elestock (임원·주요주주특정증권등소유상황보고서)
테이블: dart_insider_holdings
"""
from __future__ import annotations
import logging, os, requests, sqlite3, sys, time, zipfile, io, xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from dart_key_manager import get_dart_api_keys

logger = logging.getLogger(__name__)
DB_PATH = "/Applications/stock_dashboard/stock.db"
DART_BASE = "https://opendart.fss.or.kr/api"

KEYS = get_dart_api_keys()
_ki = 0
_exhausted: set = set()

def _next_key() -> Optional[str]:
    global _ki
    for _ in range(len(KEYS)):
        k = KEYS[_ki % len(KEYS)]
        _ki += 1
        if k not in _exhausted:
            return k
    return None

def _get_corp_map() -> dict[str, str]:
    """stock_code → corp_code 맵 (캐시 7일)"""
    cache = "/tmp/CORPCODE_map.xml"
    try:
        if Path(cache).exists() and (datetime.now() - datetime.fromtimestamp(Path(cache).stat().st_mtime)).days < 7:
            tree = ET.parse(cache)
        else:
            key = _next_key()
            r = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=30)
            zf = zipfile.ZipFile(io.BytesIO(r.content))
            with open(cache, "wb") as f:
                f.write(zf.read("CORPCODE.xml"))
            tree = ET.parse(cache)
        return {e.findtext("stock_code","").strip(): e.findtext("corp_code","").strip()
                for e in tree.getroot().iter("list")
                if e.findtext("stock_code","").strip()}
    except Exception as e:
        logger.warning("corpCode.xml 로드 실패, 로컬 DB corp_code 맵으로 fallback: %s", e)
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            """
            SELECT stock_code, corp_code FROM dart_insider_holdings
            WHERE stock_code <> '' AND corp_code <> ''
            """
        ).fetchall()
        conn.close()
        return {stock_code: corp_code for stock_code, corp_code in rows}

def collect_insider_holdings(
    bgn_de: str = None,
    end_de: str = None,
    limit: int = 0,
) -> dict:
    """
    임원·주요주주 특정증권 소유상황 수집
    bgn_de / end_de: YYYYMMDD (None이면 최근 1년)
    """
    if not bgn_de:
        bgn_de = (datetime.now() - timedelta(days=365)).strftime("%Y%m%d")
    if not end_de:
        end_de = datetime.now().strftime("%Y%m%d")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # 이미 수집된 최신 날짜 확인
    last = conn.execute("SELECT MAX(rcept_dt) FROM dart_insider_holdings").fetchone()[0]
    if last:
        bgn_de = max(bgn_de, last.replace("-",""))
    logger.info("임원매매 수집 기간: %s ~ %s", bgn_de, end_de)

    inserted = updated = errors = 0
    page = 1

    while True:
        key = _next_key()
        if not key:
            logger.error("DART API 키 모두 소진")
            break

        try:
            r = requests.get(f"{DART_BASE}/elestock.json", params={
                "crtfc_key": key,
                "bgn_de": bgn_de, "end_de": end_de,
                "page_no": page, "page_count": 100,
            }, timeout=20)
            data = r.json()
        except Exception as e:
            logger.warning("API 오류: %s", e)
            errors += 1
            time.sleep(2)
            continue

        if data.get("status") == "020":
            _exhausted.add(key)
            logger.warning("키 %s 한도초과, 다음 키로 전환", key[:8])
            continue

        if data.get("status") != "000":
            logger.info("status=%s, 종료", data.get("status"))
            break

        items = data.get("list", [])
        if not items:
            break

        for item in items:
            corp_code = item.get("corp_code","")
            # stock_code 찾기 (corp_code로)
            sc_row = conn.execute(
                "SELECT su.stock_code FROM stock_universe su WHERE su.stock_code IN "
                "(SELECT stock_code FROM dart_disclosures WHERE corp_code=? LIMIT 1)",
                (corp_code,)
            ).fetchone()
            stock_code = sc_row[0] if sc_row else ""

            # dart_disclosures에서 stock_code 조회
            if not stock_code:
                dd = conn.execute(
                    "SELECT stock_code FROM dart_disclosures WHERE rcept_no=? LIMIT 1",
                    (item.get("rcept_no",""),)
                ).fetchone()
                stock_code = dd[0] if dd else ""

            try:
                change = None
                sp_cnt = item.get("sp_stock_lmp_cnt","")
                try: change = float(str(sp_cnt).replace(",","")) if sp_cnt else None
                except: pass

                conn.execute("""
                    INSERT OR REPLACE INTO dart_insider_holdings
                    (rcept_no, rcept_dt, stock_code, corp_code, corp_name,
                     repror, isu_exctv_rgist, isu_exctv_ofcps, isu_main_shrholdr,
                     sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, sp_stock_lmp_irds_rate,
                     change_amount, raw_json, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """, (
                    item.get("rcept_no"), item.get("rcept_dt"), stock_code,
                    corp_code, item.get("corp_name"),
                    item.get("repror"), item.get("isu_exctv_rgist"),
                    item.get("isu_exctv_ofcps"), item.get("isu_main_shrholdr"),
                    item.get("sp_stock_lmp_cnt"), item.get("sp_stock_lmp_irds_cnt"),
                    item.get("sp_stock_lmp_irds_rate"),
                    change, str(item),
                ))
                inserted += 1
            except Exception as e:
                errors += 1

        conn.commit()
        logger.info("페이지 %d: %d건 처리 (누적 %d)", page, len(items), inserted)

        total_count = int(data.get("total_count", 0))
        if page * 100 >= total_count:
            break
        page += 1
        time.sleep(0.3)
        if limit and inserted >= limit:
            break

    conn.commit()
    conn.close()
    return {"inserted": inserted, "updated": updated, "errors": errors}


def collect_major_holders(bgn_de: str = None, end_de: str = None) -> dict:
    """주요주주 지분공시 (5% 이상 보고)"""
    if not bgn_de:
        bgn_de = (datetime.now() - timedelta(days=365*3)).strftime("%Y%m%d")
    if not end_de:
        end_de = datetime.now().strftime("%Y%m%d")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    corp_map = _get_corp_map()
    corp_to_stock = {corp: stock for stock, corp in corp_map.items() if corp and stock}
    inserted = errors = 0
    page = 1

    def _to_int(value):
        text = str(value or "").replace(",", "").strip()
        if not text or text == "-":
            return None
        try:
            return int(float(text))
        except Exception:
            return None

    def _to_float(value):
        text = str(value or "").replace(",", "").replace("%", "").strip()
        if not text or text == "-":
            return None
        try:
            return float(text)
        except Exception:
            return None

    while True:
        key = _next_key()
        if not key:
            break
        try:
            r = requests.get(f"{DART_BASE}/majorstock.json", params={
                "crtfc_key": key,
                "bgn_de": bgn_de, "end_de": end_de,
                "page_no": page, "page_count": 100,
            }, timeout=20)
            data = r.json()
        except Exception as e:
            errors += 1
            break

        if data.get("status") == "020":
            _exhausted.add(key)
            continue
        if data.get("status") != "000":
            break

        items = data.get("list", [])
        if not items:
            break

        for item in items:
            dd = conn.execute(
                "SELECT stock_code FROM dart_disclosures WHERE rcept_no=? LIMIT 1",
                (item.get("rcept_no",""),)
            ).fetchone()
            stock_code = dd[0] if dd else corp_to_stock.get(item.get("corp_code", ""), "")

            try:
                conn.execute("""
                    INSERT OR REPLACE INTO dart_major_holders
                    (rcept_no, rcept_dt, stock_code, corp_code, corp_name,
                     repror, stkqy, stkrt, ctr_stkqy, ctr_stkrt, report_tp,
                     stk_diff, rt_diff, raw_json, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """, (
                    item.get("rcept_no"), item.get("rcept_dt"), stock_code,
                    item.get("corp_code"), item.get("corp_name"),
                    item.get("repror"),
                    _to_int(item.get("stkqy")), _to_float(item.get("stkrt")),
                    _to_int(item.get("ctr_stkqy")), _to_float(item.get("ctr_stkrt")),
                    item.get("report_tp"), _to_int(item.get("stk_diff")), _to_float(item.get("rt_diff")),
                    str(item),
                ))
                inserted += 1
            except Exception as e:
                errors += 1

        conn.commit()
        logger.info("majorstock 페이지 %d: %d건", page, len(items))
        total_count = int(data.get("total_count", 0))
        if page * 100 >= total_count:
            break
        page += 1
        time.sleep(0.3)

    conn.commit()
    conn.close()
    return {"inserted": inserted, "errors": errors}


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--bgn-de", default=None)
    ap.add_argument("--end-de", default=None)
    ap.add_argument("--mode", choices=["insider","major","all"], default="all")
    args = ap.parse_args()
    if args.mode in ("insider","all"):
        r = collect_insider_holdings(args.bgn_de, args.end_de)
        print("임원매매:", r)
    if args.mode in ("major","all"):
        r = collect_major_holders(args.bgn_de, args.end_de)
        print("주요주주:", r)


def collect_recent_disclosures(days: int = 2) -> dict:
    """최근 N일 임원·주요주주 공시 수집 (스케줄러에서 호출)"""
    bgn = (datetime.now() - timedelta(days=days)).strftime("%Y%m%d")
    end = datetime.now().strftime("%Y%m%d")
    r_insider = collect_insider_holdings(bgn, end)
    r_major = collect_major_holders(bgn, end)
    return {
        "insider": r_insider.get("inserted", 0),
        "major": r_major.get("inserted", 0),
        "errors": r_insider.get("errors", 0) + r_major.get("errors", 0),
    }


def collect_insider_holdings_bulk(limit: int = 2200) -> dict:
    """
    corp_code 기반 종목별 elestock 전량 수집 (주간 bulk용).
    시총 상위 limit 종목을 순서대로 수집하며, 3-key 라운드로빈으로 한도 초과 회피.
    """
    global _ki, _exhausted
    _ki = 0; _exhausted = set()

    corp_map = _get_corp_map()
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")

    stocks = [r[0] for r in conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND market_cap IS NOT NULL
        ORDER BY market_cap DESC LIMIT ?
    """, (limit,)).fetchall()]

    total_inserted = 0; errors = 0
    for i, sc in enumerate(stocks):
        corp_code = corp_map.get(sc)
        if not corp_code:
            continue
        key = _next_key()
        if not key:
            logger.error("[임원매매bulk] API 키 모두 소진")
            break
        try:
            r = requests.get(f"{DART_BASE}/elestock.json",
                params={"crtfc_key": key, "corp_code": corp_code}, timeout=15)
            d = r.json()
            if d.get("status") == "020":
                _exhausted.add(key)
                key2 = _next_key()
                if key2:
                    r = requests.get(f"{DART_BASE}/elestock.json",
                        params={"crtfc_key": key2, "corp_code": corp_code}, timeout=15)
                    d = r.json()
            if d.get("status") != "000":
                time.sleep(0.3); continue
            for item in (d.get("list") or []):
                change = None
                try:
                    change = float(str(item.get("sp_stock_lmp_irds_cnt","")).replace(",","").replace("-","").replace("△","") or 0)
                except Exception:
                    pass
                irds = str(item.get("sp_stock_lmp_irds_cnt",""))
                if irds.startswith("-") or irds.startswith("△"):
                    change = -(change or 0)
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
                        item.get("rcept_no"), item.get("rcept_dt"), sc,
                        corp_code, item.get("corp_name"),
                        item.get("repror"), item.get("isu_exctv_rgist_at"),
                        item.get("isu_exctv_ofcps"), item.get("isu_main_shrholdr"),
                        item.get("sp_stock_lmp_cnt"), item.get("sp_stock_lmp_irds_cnt"),
                        item.get("sp_stock_lmp_rate"), is_ceo, change, str(item),
                    ))
                    total_inserted += 1
                except Exception:
                    pass
            if i % 100 == 0:
                conn.commit()
                logger.info(f"[임원매매bulk] {i+1}/{len(stocks)} {sc}: 누적 {total_inserted}건")
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"[임원매매bulk] {sc} 오류: {e}")
            errors += 1
            time.sleep(1)

    conn.commit()
    row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM dart_insider_holdings").fetchone()
    conn.close()
    return {"inserted": total_inserted, "errors": errors, "total_rows": row[0], "stocks": row[1]}
