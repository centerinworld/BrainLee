"""
DartV22Builder 미수집 데이터 배치 수집:
  1. dart_employee_count   — DART empSttus (직원현황)
  2. dart_sga_annual       — DART fnlttSinglAcntAll IS → 판관비
  3. dart_bs_items         — DART fnlttSinglAcntAll BS → 매출채권
"""
import sqlite3, requests, time, json, sys, os, argparse, re
sys.path.insert(0, '/Volumes/Realtek_NVME/stock_dashboard/runtime')
from dart_key_manager import get_dart_api_keys

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
PROGRESS_FILE = "/tmp/collect_ch_extra_progress.json"
KEYS = get_dart_api_keys()
_key_idx = [0]

def _key():
    return KEYS[_key_idx[0] % len(KEYS)]

def _next_key():
    _key_idx[0] = (_key_idx[0] + 1) % len(KEYS)
    print(f"  [키전환] → {_key()[:8]}...", flush=True)

# ── DB 초기화 ──────────────────────────────────────────────────
def init_tables(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_employee_count (
            stock_code TEXT, year INTEGER, reprt_code TEXT,
            total_emp INTEGER, male_emp INTEGER, female_emp INTEGER,
            regular_emp INTEGER, contract_emp INTEGER,
            avg_tenure_years REAL, annual_salary_m INTEGER,
            acmtn_dscd TEXT,
            PRIMARY KEY (stock_code, year, reprt_code, acmtn_dscd)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_sga_annual (
            stock_code TEXT, year INTEGER, reprt_code TEXT,
            sga_total REAL, report_type TEXT,
            PRIMARY KEY (stock_code, year, report_type)
        )""")
    # dart_bs_items 확장 — 매출채권 추가
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_bs_items (
            stock_code TEXT, year INTEGER, reprt_code TEXT,
            item_key TEXT, value REAL, report_type TEXT,
            PRIMARY KEY (stock_code, year, report_type, item_key)
        )""")
    conn.commit()

def load_corp_code_map(conn):
    rows = conn.execute(
        "SELECT stock_code FROM stock_universe WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' AND market_cap > 0 ORDER BY market_cap DESC"
    ).fetchall()
    codes = [r[0] for r in rows]
    # corp_code 매핑 (XML 캐시)
    import xml.etree.ElementTree as ET
    xml_cache = "/tmp/CORPCODE.xml"
    corp_map = {}
    if os.path.exists(xml_cache) and time.time() - os.path.getmtime(xml_cache) < 86400*7:
        tree = ET.parse(xml_cache)
    else:
        r = requests.get(f"https://opendart.fss.or.kr/api/corpCode.xml?crtfc_key={_key()}", timeout=30)
        import io, zipfile
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            with z.open("CORPCODE.xml") as f:
                data = f.read()
        with open(xml_cache, "wb") as f: f.write(data)
        tree = ET.fromstring(data)
        tree = ET.ElementTree(tree)
    root = tree.getroot() if hasattr(tree, 'getroot') else tree
    for item in root.findall(".//list"):
        sc = (item.findtext("stock_code") or "").strip()
        cc = (item.findtext("corp_code") or "").strip()
        if sc and cc: corp_map[sc] = cc
    return codes, corp_map

# ── 1. 직원현황 ───────────────────────────────────────────────
SGA_ACCOUNT_IDS = {
    "dart_TotalSellingGeneralAdministrativeEx",
    "ifrs-full_SellingGeneralAndAdministrativeExpense",
    "dart_SellingExpenses",
    "dart_GeneralAndAdministrativeExpense",
    "dart_SGA",
}
AR_ACCOUNT_IDS = {
    "ifrs-full_CurrentTradeReceivables",
    "ifrs-full_TradeAndOtherCurrentReceivables",
    "dart_TradeReceivable",
    "dart_AccountsReceivableTrade",
}

def collect_employee(conn, stock_code, corp_code, years, reprt_codes):
    for year in years:
        for reprt_code in reprt_codes:
            try:
                r = requests.get("https://opendart.fss.or.kr/api/empSttus.json",
                    params={"crtfc_key": _key(), "corp_code": corp_code,
                            "bsns_year": str(year), "reprt_code": reprt_code},
                    timeout=10)
                d = r.json()
                if d.get("status") == "020": _next_key(); continue
                if d.get("status") != "000": continue
                rows = d.get("list") or []
                # fo_bbm='합 계' or '합계' 행이 합계
                totals = [x for x in rows if x.get("fo_bbm","").replace(" ","") in ("합계","")]
                if not totals:
                    # fo_bbm가 사업부문별인 경우 sexdstn 기준 합계
                    male = sum(int((x.get("sm","0") or "0").replace(",","")) for x in rows if x.get("sexdstn") == "남" and x.get("fo_bbm","").replace(" ","") not in ("합계",))
                    female = sum(int((x.get("sm","0") or "0").replace(",","")) for x in rows if x.get("sexdstn") == "여" and x.get("fo_bbm","").replace(" ","") not in ("합계",))
                    total = male + female
                    regular = sum(int((x.get("rgllbr_co","0") or "0").replace(",","")) for x in rows if x.get("sexdstn") == "남")
                    contract = sum(int((x.get("cnttk_co","0") or "0").replace(",","")) for x in rows if x.get("sexdstn") == "남")
                    if total == 0: continue
                    conn.execute("""INSERT OR REPLACE INTO dart_employee_count
                        (stock_code,year,reprt_code,total_emp,male_emp,female_emp,regular_emp,contract_emp,avg_tenure_years,annual_salary_m,acmtn_dscd)
                        VALUES(?,?,?,?,?,?,?,?,NULL,NULL,'연결')""",
                        (stock_code, year, reprt_code, total, male, female, regular, contract))
                else:
                    for t in totals:
                        total = int((t.get("sm","0") or "0").replace(",",""))
                        if total == 0: continue
                        male = sum(int((x.get("sm","0") or "0").replace(",","")) for x in rows if x.get("sexdstn") == "남" and x.get("fo_bbm","") == t.get("fo_bbm",""))
                        female = sum(int((x.get("sm","0") or "0").replace(",","")) for x in rows if x.get("sexdstn") == "여" and x.get("fo_bbm","") == t.get("fo_bbm",""))
                        regular = int((t.get("rgllbr_co","0") or "0").replace(",",""))
                        contract = int((t.get("cnttk_co","0") or "0").replace(",",""))
                        tenure_raw = t.get("avrg_cnwk_sdytrn","")
                        try: tenure = float(tenure_raw) if tenure_raw and tenure_raw != "-" else None
                        except: tenure = None
                        sal_raw = t.get("jan_salary_am","")
                        try: salary_m = int(float(sal_raw.replace(",","").replace("-","0") or "0") / 10000) if sal_raw and sal_raw != "-" else None
                        except: salary_m = None
                        acmtn = t.get("acmtn_dscd","") or "연결"
                        conn.execute("""INSERT OR REPLACE INTO dart_employee_count
                            (stock_code,year,reprt_code,total_emp,male_emp,female_emp,regular_emp,contract_emp,avg_tenure_years,annual_salary_m,acmtn_dscd)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            (stock_code, year, reprt_code, total, male, female, regular, contract, tenure, salary_m, acmtn))
                conn.commit()
                return True  # 첫 성공 연도만
            except Exception as e:
                pass
    return False

def collect_sga_ar(conn, stock_code, corp_code, years):
    """판관비 + 매출채권 수집 (fnlttSinglAcntAll)"""
    for year in years:
        for reprt_code, fs_div in [("11011", "CFS"), ("11011", "OFS")]:
            try:
                r = requests.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                    params={"crtfc_key": _key(), "corp_code": corp_code,
                            "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": fs_div},
                    timeout=15)
                d = r.json()
                if d.get("status") == "020": _next_key(); continue
                if d.get("status") != "000": break
                rows = d.get("list") or []
                # 판관비
                for row in rows:
                    aid = row.get("account_id","")
                    anm = row.get("account_nm","")
                    sj = row.get("sj_div","")
                    if sj in ("IS","IS1") and aid in SGA_ACCOUNT_IDS:
                        try:
                            val = float((row.get("thstrm_amount","") or "0").replace(",",""))
                            if val != 0:
                                conn.execute("""INSERT OR REPLACE INTO dart_sga_annual
                                    (stock_code,year,reprt_code,sga_total,report_type)
                                    VALUES(?,?,?,?,?)""",
                                    (stock_code, year, reprt_code, abs(val), fs_div))
                        except: pass
                    # 매출채권
                    if sj == "BS" and aid in AR_ACCOUNT_IDS:
                        try:
                            val = float((row.get("thstrm_amount","") or "0").replace(",",""))
                            if val != 0:
                                conn.execute("""INSERT OR REPLACE INTO dart_bs_items
                                    (stock_code,year,reprt_code,item_key,value,report_type)
                                    VALUES(?,?,?,'trade_receivable',?,?)""",
                                    (stock_code, year, reprt_code, abs(val), fs_div))
                        except: pass
                conn.commit()
                if fs_div == "CFS": break  # CFS 있으면 OFS 스킵
            except Exception as e:
                pass

def run(limit=500, resume=True):
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    init_tables(conn)
    
    # 진행상황 로드
    progress = {}
    if resume and os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE) as f: progress = json.load(f)
    
    codes, corp_map = load_corp_code_map(conn)
    total = min(limit, len(codes))
    done = 0
    
    YEARS = list(range(2026, 2019, -1))
    REPRT = ["11011", "11012"]  # 사업보고서, 반기보고서
    
    for i, sc in enumerate(codes[:total]):
        if sc in progress.get("done_emp", []) and sc in progress.get("done_sga", []):
            done += 1
            continue
        cc = corp_map.get(sc)
        if not cc: continue
        
        # 직원현황
        if sc not in progress.get("done_emp", []):
            ok = collect_employee(conn, sc, cc, YEARS, REPRT)
            progress.setdefault("done_emp", []).append(sc)
        
        # 판관비 + 매출채권
        if sc not in progress.get("done_sga", []):
            collect_sga_ar(conn, sc, cc, YEARS)
            progress.setdefault("done_sga", []).append(sc)
        
        done += 1
        if done % 20 == 0:
            with open(PROGRESS_FILE, "w") as f: json.dump(progress, f)
            print(f"[{done}/{total}] 완료", flush=True)
        time.sleep(0.15)
    
    with open(PROGRESS_FILE, "w") as f: json.dump(progress, f)
    
    # 요약
    emp_cnt = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM dart_employee_count").fetchone()
    sga_cnt = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM dart_sga_annual").fetchone()
    ar_cnt = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM dart_bs_items WHERE item_key='trade_receivable'").fetchone()
    print(f"\n=== 수집 완료 ===")
    print(f"직원현황: {emp_cnt[0]}건/{emp_cnt[1]}종목")
    print(f"판관비:   {sga_cnt[0]}건/{sga_cnt[1]}종목")
    print(f"매출채권: {ar_cnt[0]}건/{ar_cnt[1]}종목")
    conn.close()

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--no-resume", action="store_true")
    args = ap.parse_args()
    run(limit=args.limit, resume=not args.no_resume)
