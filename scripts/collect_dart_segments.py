"""
collect_dart_segments.py — DART fnlttSinglAcntAll IS 계정으로
기업별 연도별 매출/영업이익 수집 → segment_revenue 저장

실행:
    python3 scripts/collect_dart_segments.py --years 2021,2022,2023,2024
    python3 scripts/collect_dart_segments.py --years 2021 --limit 100
    python3 scripts/collect_dart_segments.py --resume
"""
from __future__ import annotations
import argparse, json, logging, sqlite3, sys, time, xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH   = Path(__file__).resolve().parent.parent / "stock.db"
PROG_PATH = Path("/tmp/dart_segment_progress.json")
DART_BASE = "https://opendart.fss.or.kr/api"
CORP_XML  = Path("/tmp/CORPCODE.xml")

def _load_env():
    env = {}
    for line in (Path(__file__).resolve().parent.parent / ".env").read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

ENV = _load_env()
API_KEYS = [k for k in [ENV.get("DART_API_KEY"), ENV.get("DART_API_KEY2"), ENV.get("DART_API_KEY3")] if k]
_key_idx = 0

def _dart_key():
    global _key_idx
    k = API_KEYS[_key_idx % len(API_KEYS)]
    _key_idx += 1
    return k

def _conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=300)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=300000")
    return conn

def _load_corp_map() -> dict[str, str]:
    """stock_code → corp_code"""
    if CORP_XML.exists():
        tree = ET.parse(str(CORP_XML))
        corp_map = {}
        for child in tree.getroot():
            sc = child.findtext('stock_code', '').strip()
            cc = child.findtext('corp_code', '').strip()
            if sc and len(sc) == 6 and sc.isdigit():
                corp_map[sc] = cc
        return corp_map
    # fallback: dart_insider_holdings
    conn = _conn()
    rows = conn.execute(
        "SELECT DISTINCT stock_code, corp_code FROM dart_insider_holdings WHERE corp_code IS NOT NULL"
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}

def _fetch(session, corp_code, year, reprt_code, fs_div):
    for attempt in range(len(API_KEYS)):
        key = _dart_key()
        try:
            r = session.get(f"{DART_BASE}/fnlttSinglAcntAll.json", params={
                "crtfc_key": key, "corp_code": corp_code,
                "bsns_year": year, "reprt_code": reprt_code, "fs_div": fs_div,
            }, timeout=15)
            d = r.json()
            if d.get("status") == "020":
                logger.warning(f"DART 일일한도 초과 (key{attempt+1})")
                time.sleep(2)
                continue
            if d.get("status") == "000":
                return d.get("list", [])
            return []
        except Exception as e:
            logger.debug(f"err: {e}")
            time.sleep(1)
    return []

def _parse(rows):
    REV_IDS = {"ifrs-full_Revenue", "dart_Revenue",
               "ifrs-full_RevenueFromContractsWithCustomers", "dart_OrdIncome"}
    OP_IDS  = {"dart_OperatingIncomeLoss",
               "ifrs-full_ProfitLossFromOperatingActivities"}
    ASSET_IDS = {"ifrs-full_Assets"}
    
    rev = op = assets = None
    for row in rows:
        sj = row.get("sj_div","")
        aid = row.get("account_id","")
        anm = row.get("account_nm","")
        val_str = row.get("thstrm_amount","")
        try:
            val = int(str(val_str).replace(",","")) / 1e8  # → 억원
        except:
            continue
        
        if sj in ("IS","CIS"):
            if aid in REV_IDS or anm in ("매출액","영업수익","순영업수익"):
                if rev is None:
                    rev = round(val, 2)
            if aid in OP_IDS or anm == "영업이익":
                if op is None:
                    op = round(val, 2)
        if sj == "BS" and aid in ASSET_IDS:
            if assets is None:
                assets = round(val, 2)
    return rev, op, assets

def _upsert(conn, stock_code, corp_code, year, quarter, seg_name, rev, op, assets):
    import time as _time
    for _r in range(5):
        try:
            conn.execute("""
                INSERT INTO segment_revenue (stock_code, corp_code, year, quarter, segment_name, revenue, operating_profit, assets, report_type)
                VALUES (?,?,?,?,?,?,?,?,'CFS')
                ON CONFLICT(stock_code, year, quarter, segment_name) DO UPDATE SET
                    revenue=excluded.revenue, operating_profit=excluded.operating_profit,
                    assets=excluded.assets, updated_at=DATETIME('now')
            """, (stock_code, corp_code, year, quarter, seg_name, rev, op, assets))
            return
        except sqlite3.OperationalError as e:
            if "locked" in str(e) and _r < 4:
                _time.sleep(10 * (_r + 1))
            else:
                raise

def collect(years, limit=0, resume=False):
    import requests
    
    corp_map = _load_corp_map()
    logger.info(f"corp_code 매핑: {len(corp_map)}종목")
    
    conn = _conn()
    session = requests.Session()
    
    stocks = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        ORDER BY market_cap DESC NULLS LAST
    """).fetchall()
    codes = [r[0] for r in stocks]
    if limit:
        codes = codes[:limit]
    
    done_set = set()
    if resume and PROG_PATH.exists():
        done_set = set(json.loads(PROG_PATH.read_text()).get("done", []))
        logger.info(f"체크포인트: {len(done_set)}건 완료")
    
    stats = {"ok":0,"skip":0,"fail":0}
    
    for idx, stock_code in enumerate(codes):
        corp_code = corp_map.get(stock_code)
        if not corp_code:
            stats["skip"] += 1
            continue
        
        for year in years:
            chk_key = f"{stock_code}_{year}"
            if chk_key in done_set:
                continue
            
            # 이미 수집된 경우 건너뜀
            exists = conn.execute(
                "SELECT 1 FROM segment_revenue WHERE stock_code=? AND year=? AND quarter=0 AND revenue IS NOT NULL LIMIT 1",
                (stock_code, int(year))
            ).fetchone()
            if exists:
                done_set.add(chk_key)
                continue
            
            # CFS 먼저, 실패하면 OFS
            rows = _fetch(session, corp_code, year, "11011", "CFS")
            if not rows:
                rows = _fetch(session, corp_code, year, "11011", "OFS")
            
            rev, op, assets = _parse(rows)
            if rev is not None:
                _upsert(conn, stock_code, corp_code, int(year), 0, "연결전체", rev, op, assets)
                conn.commit()
                stats["ok"] += 1
                done_set.add(chk_key)
            else:
                stats["skip"] += 1
            
            time.sleep(0.3)
        
        if (idx+1) % 100 == 0:
            PROG_PATH.write_text(json.dumps({"done": list(done_set)}))
            logger.info(f"  [{idx+1}/{len(codes)}] ok={stats['ok']} skip={stats['skip']}")
    
    conn.close()
    PROG_PATH.write_text(json.dumps({"done": list(done_set)}))
    logger.info(f"완료: {stats}")
    return stats

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2021,2022,2023,2024")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    years = [y.strip() for y in args.years.split(",")]
    collect(years, args.limit, args.resume)

if __name__ == "__main__":
    main()
