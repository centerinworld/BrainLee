"""
DART API에서 CH시트 데이터 수집:
1. empSttus → dart_employee_count
2. fnlttSinglAcntAll BS → dart_bs_items (trade_receivable)
3. fnlttSinglAcntAll IS → segment_revenue (비표준 IS계정)

실행: python3 scripts/collect_dart_ch_data.py [--codes 005930] [--limit 500]
"""
import sqlite3, os, time, requests, sys, argparse, logging
from datetime import datetime

sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
from dart_key_manager import get_dart_api_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
DART_KEYS = get_dart_api_keys()
_key_idx = [0]
_key_exhausted = [False]

STANDARD_IS = {
    "매출액","수익","영업수익","매출","순매출액","총매출액","순영업수익",
    "매출원가","매출총이익","매출총이익(손실)",
    "판매비와관리비","판관비","영업비용","판매비및관리비","판매관리비",
    "영업이익","영업이익(손실)","영업손익",
    "금융수익","금융비용","금융원가","이자수익","이자비용",
    "기타이익","기타손실","기타수익","기타비용","기타영업외손익",
    "지분법이익","지분법손실","관계기업손익","지분법적용손익",
    "세전이익","법인세비용차감전순이익","세전계속사업이익",
    "법인세비용","법인세","소득세비용",
    "당기순이익","당기순손실","당기순이익(손실)","연결당기순이익",
    "지배기업소유주귀속","비지배지분귀속","지배주주귀속순이익",
    "기타포괄손익","총포괄손익","포괄손익",
    "계속영업손익","중단영업손익","희석주당이익","기본주당이익",
    "감가상각비","무형자산상각비","연구개발비","대손상각비",
}

def _dart_key():
    return DART_KEYS[_key_idx[0] % len(DART_KEYS)]

def _next_key():
    _key_idx[0] = (_key_idx[0] + 1) % len(DART_KEYS)

def _load_corp_code(conn, stock_code):
    for tbl in ["dart_insider_holdings", "dart_backlog_quarterly"]:
        try:
            row = conn.execute(
                f"SELECT corp_code FROM {tbl} WHERE stock_code=? AND corp_code IS NOT NULL LIMIT 1",
                (stock_code,)
            ).fetchone()
            if row and row[0]:
                return str(row[0]).zfill(8)
        except Exception:
            pass
    # dart_disclosures에서도 시도
    try:
        row = conn.execute(
            "SELECT corp_code FROM dart_disclosures WHERE stock_code=? AND corp_code IS NOT NULL LIMIT 1",
            (stock_code,)
        ).fetchone()
        if row and row[0]:
            return str(row[0]).zfill(8)
    except Exception:
        pass
    return None

def _fetch_dart(endpoint, params, timeout=12):
    """DART API 호출, 한도 초과 시 다음 키로 전환. 전체 소진 시 None."""
    for attempt in range(len(DART_KEYS)):
        try:
            params["crtfc_key"] = _dart_key()
            r = requests.get(f"https://opendart.fss.or.kr/api/{endpoint}",
                             params=params, timeout=timeout)
            d = r.json()
            if d.get("status") == "020":
                log.warning("API 한도 초과, 키 교체 (%d -> %d)", _key_idx[0], (_key_idx[0]+1)%len(DART_KEYS))
                _next_key()
                time.sleep(1)
                continue
            if d.get("status") == "000":
                return d
            if d.get("status") in ("013", "014"):
                return {**d, "list": []}
        except Exception as e:
            log.warning("API 오류 %s: %s", endpoint, e)
            _next_key()
    _key_exhausted[0] = True
    return None

def collect_employee(conn, stock_code, corp_code):
    """empSttus → dart_employee_count"""
    saved = 0
    for year in range(datetime.now().year - 1, datetime.now().year - 6, -1):
        for reprt_code in ["11011", "11012"]:  # 사업보고서, 반기
            d = _fetch_dart("empSttus.json", {"corp_code": corp_code,
                            "bsns_year": str(year), "reprt_code": reprt_code})
            if d is None:
                return saved  # API 한도 소진
            groups = {}
            for row in (d.get("list") or []):
                fo_bbm = row.get("fo_bbm", "")
                if fo_bbm not in ("합 계", "합계", ""):
                    continue
                acmtn = row.get("acmtn_dscd", "") or "연결"
                group = groups.setdefault(acmtn, {
                    "total_emp": 0,
                    "male_emp": 0,
                    "female_emp": 0,
                    "regular_emp": 0,
                    "contract_emp": 0,
                    "avg_tenure_years": None,
                    "annual_salary_m": None,
                })
                try:
                    emp_count = int((row.get("sm", "0") or "0").replace(",", ""))
                    if row.get("sexdstn") == "남":
                        group["male_emp"] += emp_count
                    elif row.get("sexdstn") == "여":
                        group["female_emp"] += emp_count
                    else:
                        group["total_emp"] = max(group["total_emp"], emp_count)
                    group["regular_emp"] += int((row.get("rgllbr_co", "0") or "0").replace(",", "") or 0)
                    group["contract_emp"] += int((row.get("cnttk_co", "0") or "0").replace(",", "") or 0)
                    tenure_raw = row.get("avrg_cnwk_sdytrn", "")
                    try:
                        avg_tenure = float(tenure_raw) if tenure_raw and tenure_raw != "-" else None
                    except (ValueError, TypeError):
                        avg_tenure = None
                    if avg_tenure is not None:
                        group["avg_tenure_years"] = avg_tenure
                    salary_raw = row.get("jan_salary_am", "")
                    try:
                        annual_salary_m = int(float(salary_raw.replace(",", "").replace("-", "0") or "0") / 10000) if salary_raw else None
                    except (ValueError, TypeError):
                        annual_salary_m = None
                    if annual_salary_m is not None:
                        group["annual_salary_m"] = annual_salary_m
                except (ValueError, TypeError):
                    pass
            for acmtn, group in groups.items():
                total_emp = group["total_emp"] or group["male_emp"] + group["female_emp"]
                if total_emp <= 0:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO dart_employee_count
                    (stock_code, year, reprt_code, total_emp, male_emp, female_emp,
                     regular_emp, contract_emp, avg_tenure_years, annual_salary_m, acmtn_dscd)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    stock_code, year, reprt_code, total_emp,
                    group["male_emp"] or None,
                    group["female_emp"] or None,
                    group["regular_emp"] or None,
                    group["contract_emp"] or None,
                    group["avg_tenure_years"],
                    group["annual_salary_m"],
                    acmtn,
                ))
                conn.commit()
                saved += 1
            if saved > 0 and reprt_code == "11011":
                break  # 사업보고서 있으면 반기는 스킵
    return saved

def collect_bs_ar(conn, stock_code, corp_code):
    """fnlttSinglAcntAll BS → dart_bs_items.trade_receivable"""
    saved = 0
    AR_AIDS = [
        "ifrs-full_TradeAndOtherCurrentReceivables",
        "dart_ShortTermTradeReceivable",
        "ifrs-full_CurrentTradeReceivables",
    ]
    for year in range(datetime.now().year - 1, datetime.now().year - 5, -1):
        d = _fetch_dart("fnlttSinglAcntAll.json", {"corp_code": corp_code,
                        "bsns_year": str(year), "reprt_code": "11011", "fs_div": "CFS"})
        if d is None:
            return saved
        bs_rows = [r for r in (d.get("list") or []) if r.get("sj_div") == "BS"]
        ar_val = None
        for aid in AR_AIDS:
            for r in bs_rows:
                if r.get("account_id") == aid:
                    raw = r.get("thstrm_amount", "").replace(",", "")
                    try:
                        ar_val = float(raw)
                        break
                    except (ValueError, TypeError):
                        pass
            if ar_val is not None:
                break
        if ar_val is not None:
            conn.execute("""
                INSERT OR REPLACE INTO dart_bs_items
                (stock_code, year, quarter, item_key, value, report_type)
                VALUES (?,?,4,'trade_receivable',?,'CFS')
            """, (stock_code, year, ar_val))
            conn.commit()
            saved += 1
    return saved

def collect_segments(conn, stock_code, corp_code):
    """fnlttSinglAcntAll IS → segment_revenue (비표준 IS계정)"""
    saved = 0
    for year in range(datetime.now().year - 1, datetime.now().year - 5, -1):
        for reprt_code, quarter in [("11011",4),("11014",3),("11012",2),("11013",1)]:
            d = _fetch_dart("fnlttSinglAcntAll.json", {"corp_code": corp_code,
                            "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": "CFS"})
            if d is None:
                return saved
            rows = d.get("list") or []
            is_rows = [r for r in rows if r.get("sj_div","") in ("IS","IS1")]
            # 총 매출액
            total_rev = None
            for r in is_rows:
                if r.get("account_id") == "ifrs-full_Revenue":
                    raw = r.get("thstrm_amount","").replace(",","")
                    try:
                        total_rev = float(raw)
                    except (ValueError, TypeError):
                        pass
                    break
            if not total_rev:
                break  # 이 연도/분기는 없음
            # 비표준 IS 계정 (세그먼트 후보)
            for r in is_rows:
                nm = (r.get("account_nm","") or "").strip()
                aid = r.get("account_id","")
                if nm in STANDARD_IS:
                    continue
                if nm.endswith(("합계","소계","계","총계","합 계")):
                    continue
                if nm.startswith(("(","①","②","③")):
                    continue
                if aid.startswith("ifrs-full_") and aid.count("_") == 1:
                    continue
                if not any(k in nm for k in ["매출","수익","Revenue","부문"]):
                    continue
                raw = r.get("thstrm_amount","").replace(",","")
                try:
                    amount = float(raw)
                except (ValueError, TypeError):
                    continue
                ratio = amount / total_rev
                if ratio < 0.001 or ratio > 1.3:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO segment_revenue
                    (stock_code, corp_code, year, quarter, segment_name,
                     revenue, operating_profit, assets, report_type)
                    VALUES (?,?,?,?,?,?,NULL,NULL,'CFS')
                """, (stock_code, corp_code, year, quarter, nm, amount))
                saved += 1
            if saved > 0 or reprt_code == "11011":
                conn.commit()
                break  # 사업보고서 성공
    return saved

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--codes", help="종목코드 comma-separated")
    p.add_argument("--limit", type=int, default=500)
    p.add_argument("--skip-existing", action="store_true", help="이미 수집된 종목 스킵")
    p.add_argument("--employee-only", action="store_true", help="dart_employee_count만 수집")
    args = p.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=60000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS dart_employee_count (
            stock_code TEXT, year INTEGER, reprt_code TEXT,
            total_emp INTEGER, male_emp INTEGER, female_emp INTEGER,
            regular_emp INTEGER, contract_emp INTEGER,
            avg_tenure_years REAL, annual_salary_m INTEGER,
            acmtn_dscd TEXT,
            PRIMARY KEY (stock_code, year, reprt_code, acmtn_dscd)
        )
    """)
    conn.commit()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
    else:
        codes = [r[0] for r in conn.execute("""
            SELECT su.stock_code FROM stock_universe su
            WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
              AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND su.market_cap IS NOT NULL
            ORDER BY su.market_cap DESC LIMIT ?
        """, (args.limit,)).fetchall()]

    total = 0
    for i, code in enumerate(codes):
        if _key_exhausted[0]:
            log.warning("API 키 전체 소진. 내일 다시 실행하세요.")
            break

        corp_code = _load_corp_code(conn, code)
        if not corp_code:
            continue

        if args.skip_existing:
            existing = conn.execute(
                "SELECT 1 FROM dart_employee_count WHERE stock_code=? LIMIT 1", (code,)
            ).fetchone()
            if existing:
                continue

        log.info("[%d/%d] %s (corp=%s)", i+1, len(codes), code, corp_code)
        n_emp = collect_employee(conn, code, corp_code)
        n_ar = 0 if args.employee_only else collect_bs_ar(conn, code, corp_code)
        n_seg = 0 if args.employee_only else collect_segments(conn, code, corp_code)
        log.info("  → 직원수=%d, 매출채권=%d, 세그먼트=%d", n_emp, n_ar, n_seg)
        total += n_emp + n_ar + n_seg
        time.sleep(0.2)

    log.info("완료: 총 %d건 저장", total)
    conn.close()

if __name__ == "__main__":
    main()
