"""
2021-2022년 수주잔고 소급 수집
- dart_api 소스로 이미 2023-2025가 수집된 422개 종목 대상
- 동일 fnlttSinglAcntAll API로 2021-2022년 데이터 소급
- order_backlog 테이블에 병합
"""
import sqlite3, time, requests, os, logging, sys

sys.path.insert(0, "/Applications/stock_dashboard")
from dart_key_manager import get_dart_api_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DB = "/Applications/stock_dashboard/stock.db"
DART_BASE = "https://opendart.fss.or.kr/api"
KEYS = get_dart_api_keys()
BACKLOG_KW = ["수주잔고", "미완성공사금액", "잔여수주", "수주액잔액", "수주잔액", "미청구공사"]
NEW_ORDER_KW = ["신규수주", "수주액", "당기수주"]
RPRT = {1: "11013", 2: "11012", 3: "11014", 4: "11011"}
TARGET_YEARS = [2020, 2021, 2022]

_exhausted: set = set()
_ki = 0

def _next_key():
    global _ki
    for _ in range(len(KEYS)):
        k = KEYS[_ki % len(KEYS)]; _ki += 1
        if k not in _exhausted:
            return k
    return None

def _best(items, keywords):
    for kw in keywords:
        for item in items:
            nm = item.get("account_nm") or ""
            if kw in nm:
                raw = (item.get("thstrm_amount") or "").replace(",", "")
                try:
                    v = float(raw)
                    if abs(v) > 0:
                        return v, nm
                except Exception:
                    pass
    return None, None

def main():
    conn = sqlite3.connect(DB, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    # 2023-2025에 dart_api 수주잔고가 있는 종목 추출 (corp_code 포함)
    targets = conn.execute("""
        SELECT DISTINCT ob.stock_code, su.stock_name
        FROM order_backlog ob
        JOIN stock_universe su ON su.stock_code=ob.stock_code
        WHERE ob.data_source='dart_api' AND ob.year >= 2023
        ORDER BY su.market_cap DESC
    """).fetchall()
    log.info("소급 대상 종목: %d개", len(targets))

    # corp_code 맵 (CORPCODE.xml에서)
    import xml.etree.ElementTree as ET
    xml_path = "/tmp/CORPCODE.xml"
    corp_map = {}
    if os.path.exists(xml_path):
        tree = ET.parse(xml_path)
        for item in tree.getroot():
            sc = (item.findtext("stock_code") or "").strip()
            cc = (item.findtext("corp_code") or "").strip()
            if sc:
                corp_map[sc] = cc
    else:
        # DART API에서 다운로드
        import zipfile, io
        key = _next_key()
        resp = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=30)
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        xml_data = zf.read("CORPCODE.xml")
        with open(xml_path, "wb") as f:
            f.write(xml_data)
        tree = ET.fromstring(xml_data)
        for item in tree:
            sc = (item.findtext("stock_code") or "").strip()
            cc = (item.findtext("corp_code") or "").strip()
            if sc:
                corp_map[sc] = cc
        log.info("CORPCODE.xml 다운로드 완료: %d건", len(corp_map))

    ok = skip = err = no_data = 0

    for i, row in enumerate(targets):
        sc = row["stock_code"]
        sname = row["stock_name"]
        corp_code = corp_map.get(sc)
        if not corp_code:
            no_data += 1
            continue

        for year in TARGET_YEARS:
            for qtr in (1, 2, 3, 4):
                # 이미 있으면 건너뜀
                exists = conn.execute(
                    "SELECT 1 FROM order_backlog WHERE stock_code=? AND year=? AND quarter=?",
                    (sc, year, qtr)
                ).fetchone()
                if exists:
                    skip += 1
                    continue

                key = _next_key()
                if not key:
                    conn.commit()
                    log.error("API 키 소진 — 중단. ok=%d skip=%d", ok, skip)
                    return

                rprt = RPRT[qtr]
                for fs_div in ("CFS", "OFS"):
                    try:
                        resp = requests.get(
                            f"{DART_BASE}/fnlttSinglAcntAll.json",
                            params={"crtfc_key": key, "corp_code": corp_code,
                                    "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                            timeout=12
                        )
                        time.sleep(0.25)
                        data = resp.json()
                        if data.get("status") == "020":
                            _exhausted.add(key)
                            key = _next_key()
                            if not key:
                                conn.commit()
                                log.error("API 키 소진(한도)")
                                return
                            resp = requests.get(
                                f"{DART_BASE}/fnlttSinglAcntAll.json",
                                params={"crtfc_key": key, "corp_code": corp_code,
                                        "bsns_year": str(year), "reprt_code": rprt, "fs_div": fs_div},
                                timeout=12
                            )
                            time.sleep(0.25)
                            data = resp.json()

                        if data.get("status") != "000":
                            break

                        items = data.get("list", [])
                        backlog, acct_nm = _best(items, BACKLOG_KW)
                        new_order, _ = _best(items, NEW_ORDER_KW)
                        if backlog is None:
                            break

                        conn.execute("""
                            INSERT INTO order_backlog
                                (stock_code, stock_name, year, quarter, report_type,
                                 backlog_amount, backlog_unit, backlog_normalized,
                                 data_source, collected_at)
                            VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                            ON CONFLICT(stock_code, year, quarter) DO UPDATE SET
                                backlog_amount=excluded.backlog_amount,
                                backlog_normalized=excluded.backlog_normalized,
                                data_source='dart_api',
                                collected_at=CURRENT_TIMESTAMP
                        """, (sc, sname, year, qtr, fs_div,
                              backlog, "원", round(backlog / 1e8, 1),
                              "dart_api"))
                        ok += 1
                        break  # CFS 성공하면 OFS 건너뜀
                    except Exception as e:
                        err += 1
                        log.debug("[%s] %dQ%d %s", sc, year, qtr, e)

        if i % 50 == 0:
            conn.commit()
            log.info("[%d/%d] %s | ok=%d skip=%d err=%d",
                     i+1, len(targets), sname, ok, skip, err)

    conn.commit()
    conn.close()
    log.info("완료 — ok=%d skip=%d err=%d no_data=%d", ok, skip, err, no_data)

if __name__ == "__main__":
    main()
