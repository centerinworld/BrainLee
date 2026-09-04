"""
collect_inventory_from_dart.py

DART fnlttSinglAcntAll API로 재고자산(Inventories)을 연도×분기별로 수집
→ dart_cost_quarterly.inventory_assets_krw 에 저장

실행:
    python3 scripts/collect_inventory_from_dart.py --year 2022 2023 2024 2025
    python3 scripts/collect_inventory_from_dart.py --year 2025 --quarter 1 2 3
"""
from __future__ import annotations

import argparse, logging, os, sqlite3, sys, time, zipfile
from pathlib import Path
from xml.etree import ElementTree

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dart_key_manager import get_dart_api_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "stock.db"

DART_KEYS = get_dart_api_keys()
DART_BASE = "https://opendart.fss.or.kr/api"

# 재고자산 XBRL account IDs
INVENTORY_IDS = {
    "ifrs-full_Inventories",
    "dart_Inventories",
    "ifrs-full_CurrentInventories",
}
INVENTORY_KW = ["재고자산", "재고", "Inventories", "재공품및재고"]

_key_idx = 0
_exhausted: set[str] = set()


def _next_key() -> str | None:
    global _key_idx
    for _ in range(len(DART_KEYS)):
        k = DART_KEYS[_key_idx % len(DART_KEYS)]
        _key_idx += 1
        if k not in _exhausted:
            return k
    return None


def _load_corp_map() -> dict[str, str]:
    xml_path = Path("/tmp/CORPCODE.xml")
    if not xml_path.exists():
        key = _next_key()
        r = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=30)
        zip_path = Path("/tmp/CORPCODE.zip")
        zip_path.write_bytes(r.content)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall("/tmp/")
    tree = ElementTree.parse(str(xml_path))
    mapping = {}
    for item in tree.getroot().findall("list"):
        sc = (item.findtext("stock_code") or "").strip()
        cc = (item.findtext("corp_code") or "").strip()
        if sc and len(sc) == 6 and sc.isdigit():
            mapping[sc] = cc
    return mapping


def _report_code(quarter: int) -> list[str]:
    # quarter=4 → 사업보고서, quarter=2 → 반기, quarter=1 → 1분기, quarter=3 → 3분기
    # 2026-09 수정: 기존 코드는 quarter=1과 quarter=3이 동일하게 ["11013","11014"]를
    # 반환했는데, _fetch_inventory가 리스트 순서대로 첫 성공값을 채택하는 구조라
    # quarter=3 요청도 항상 11013(1분기보고서)이 먼저 매칭돼 quarter=1 데이터를
    # 그대로 재사용하는 결과가 됨 — 실제로 다수 종목에서 Q1==Q3 값이 여러 해에
    # 걸쳐 완전히 동일하게 저장돼 있던 근본 원인.
    if quarter == 4:
        return ["11011"]
    elif quarter == 2:
        return ["11012"]
    elif quarter == 1:
        return ["11013"]
    elif quarter == 3:
        return ["11014"]
    return []


def _fetch_inventory(corp_code: str, year: int, quarter: int) -> float | None:
    for rpt_code in _report_code(quarter):
        for fs_div in ["CFS", "OFS"]:
            key = _next_key()
            if not key:
                return None
            try:
                r = requests.get(
                    f"{DART_BASE}/fnlttSinglAcntAll.json",
                    params={"crtfc_key": key, "corp_code": corp_code,
                            "bsns_year": str(year), "reprt_code": rpt_code, "fs_div": fs_div},
                    timeout=15,
                )
                d = r.json()
                if d.get("status") == "020":
                    _exhausted.add(key)
                    continue
                if d.get("status") != "000":
                    continue
                for item in d.get("list", []):
                    acct_id = item.get("account_id", "")
                    acct_nm = item.get("account_nm", "")
                    sj_nm   = item.get("sj_nm", "")
                    if sj_nm not in ("BS", "재무상태표", "연결재무상태표"):
                        continue
                    match = (acct_id in INVENTORY_IDS or
                             any(kw in acct_nm for kw in INVENTORY_KW))
                    if match:
                        raw = item.get("thstrm_amount", "").replace(",", "")
                        try:
                            v = float(raw)
                            # 100만원 미만은 DART 재고자산으로 보기 어려운 소액 오염값이다.
                            # 한 번 저장되면 이후 스킵 조건에 걸려 고착될 수 있어 fetch 단계에서 차단한다.
                            if 1_000_000 <= v <= 1e14:  # 100조원 상한 — 비현실적 값(파싱오류) 차단
                                return v
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"  fetch error corp={corp_code} y={year} q={quarter}: {e}")
    return None


def _upsert(conn: sqlite3.Connection, stock_code: str, year: int, quarter: int, inv: float) -> None:
    conn.execute("""
        INSERT INTO dart_cost_quarterly
            (stock_code, fiscal_year, fiscal_quarter, report_type, inventory_assets_krw,
             parser_version, updated_at)
        VALUES (?, ?, ?, 'CFS', ?, 'inv_api_v1', CURRENT_TIMESTAMP)
        ON CONFLICT(stock_code, fiscal_year, fiscal_quarter, report_type) DO UPDATE SET
            inventory_assets_krw = CASE
                WHEN excluded.inventory_assets_krw IS NOT NULL AND excluded.inventory_assets_krw >= 1000000
                THEN excluded.inventory_assets_krw
                ELSE inventory_assets_krw END,
            updated_at = CURRENT_TIMESTAMP
    """, (stock_code, year, quarter, inv))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, nargs="+", default=[2022, 2023, 2024, 2025, 2026])
    ap.add_argument("--quarter", type=int, nargs="+", default=[1, 2, 3, 4])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--min-cap", type=float, default=500, help="최소 시가총액(억원)")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH), timeout=300)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=300000")  # 5분 대기

    # 대상 종목 (시총 기준)
    stocks = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market_cap >= ? AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND stock_name NOT LIKE '%우%' AND stock_name NOT LIKE '%스팩%'
          AND market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
        ORDER BY market_cap DESC
    """, (args.min_cap,)).fetchall()
    stock_list = [r[0] for r in stocks]
    if args.limit:
        stock_list = stock_list[:args.limit]

    logger.info(f"대상 종목: {len(stock_list)}개, 연도: {args.year}, 분기: {args.quarter}")

    corp_map = _load_corp_map()
    logger.info(f"corp_code 매핑: {len(corp_map)}종목")

    saved = skipped = failed = 0
    total = len(stock_list) * len(args.year) * len(args.quarter)
    idx = 0

    for sc in stock_list:
        corp_code = corp_map.get(sc)
        if not corp_code:
            continue

        for year in args.year:
            for quarter in args.quarter:
                idx += 1
                # 이미 수집된 경우 건너뜀
                existing = conn.execute("""
                    SELECT inventory_assets_krw FROM dart_cost_quarterly
                    WHERE stock_code=? AND fiscal_year=? AND fiscal_quarter=?
                      AND report_type='CFS' AND inventory_assets_krw IS NOT NULL AND inventory_assets_krw>=1000000
                """, (sc, year, quarter)).fetchone()
                if existing:
                    skipped += 1
                    continue

                inv = _fetch_inventory(corp_code, year, quarter)
                if inv and inv >= 1000000:
                    for _retry in range(5):
                        try:
                            _upsert(conn, sc, year, quarter, inv)
                            conn.commit()
                            saved += 1
                            break
                        except sqlite3.OperationalError as e:
                            if "locked" in str(e) and _retry < 4:
                                time.sleep(10 * (_retry + 1))
                            else:
                                logger.warning(f"upsert failed {sc}/{year}Q{quarter}: {e}")
                                failed += 1
                                break
                else:
                    failed += 1

                if idx % 200 == 0:
                    logger.info(f"[{idx}/{total}] saved={saved} skipped={skipped} failed={failed}")

                time.sleep(0.05)

                if len(_exhausted) >= len(DART_KEYS):
                    logger.error("DART API 키 전부 소진, 종료")
                    conn.commit()
                    conn.close()
                    return

    conn.commit()
    conn.close()
    logger.info(f"완료: saved={saved}, skipped={skipped}, failed={failed}")


if __name__ == "__main__":
    main()
