"""
DART fnlttSinglAcntAll IS계정에서 사업부문별 매출을 추출해 segment_revenue 테이블에 저장.
DartV22Builder의 write_ch_sheet 부문별 매출 섹션 재현.

실행: python3 scripts/collect_dart_segment_breakdown.py [--codes 005930,000660]
"""
import sqlite3, os, time, requests, sys, argparse, logging
from datetime import datetime

sys.path.insert(0, "/Applications/stock_dashboard")
from dart_key_manager import get_dart_api_keys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"
DART_KEYS = get_dart_api_keys()
_key_idx = [0]

# 표준 IS 계정명 (부문 계정이 아닌 것들)
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
            row = conn.execute(f"SELECT corp_code FROM {tbl} WHERE stock_code=? AND corp_code IS NOT NULL LIMIT 1", (stock_code,)).fetchone()
            if row and row[0]:
                return str(row[0]).zfill(8)
        except Exception:
            pass
    return None

def _fetch_fnltt(corp_code, year, reprt_code):
    for attempt in range(len(DART_KEYS)):
        try:
            r = requests.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key": _dart_key(), "corp_code": corp_code,
                        "bsns_year": str(year), "reprt_code": reprt_code, "fs_div": "CFS"},
                timeout=15,
            )
            d = r.json()
            if d.get("status") == "020":
                log.warning("API 한도 초과, 키 교체")
                _next_key()
                time.sleep(2)
                continue
            if d.get("status") == "000":
                return d.get("list", [])
        except Exception as e:
            log.warning("fnltt 오류 %s %s %s: %s", corp_code, year, reprt_code, e)
            _next_key()
    return []

def _is_segment_account(account_nm, account_id):
    nm = (account_nm or "").strip()
    aid = (account_id or "")
    if nm in STANDARD_IS:
        return False
    if nm.endswith(("합계", "소계", "계", "총계", "합 계")):
        return False
    if nm.startswith(("(", "①", "②", "③", "ⅰ", "•")):
        return False
    # 표준 ifrs 단일ID는 제외 (하이픈 없는 것)
    if aid.startswith("ifrs-full_") and aid.count("_") == 1:
        return False
    # 매출/수익 키워드 있어야 세그먼트 매출
    rev_kw = any(k in nm for k in ["매출", "수익", "Revenue", "부문"])
    if not rev_kw:
        return False
    return True

def collect_segments_for_stock(conn, stock_code, years=None):
    corp_code = _load_corp_code(conn, stock_code)
    if not corp_code:
        return 0

    if years is None:
        years = list(range(datetime.now().year - 1, datetime.now().year - 5, -1))

    saved = 0
    for year in years:
        for reprt_code, quarter in [("11011", 4), ("11014", 3), ("11012", 2), ("11013", 1)]:
            rows = _fetch_fnltt(corp_code, year, reprt_code)
            if not rows:
                continue  # API 한도 초과 → 다음 연도로 넘어가지 않고 중단
            
            is_rows = [r for r in rows if r.get("sj_div", "") in ("IS", "IS1")]
            
            # 총 매출액
            total_rev = None
            for r in is_rows:
                if r.get("account_id") == "ifrs-full_Revenue":
                    raw = r.get("thstrm_amount", "").replace(",", "")
                    try:
                        total_rev = float(raw)
                    except (ValueError, TypeError):
                        pass
                    break
            
            if total_rev is None or total_rev == 0:
                break  # 사업보고서가 없으면 다른 report type도 불필요
            
            # 세그먼트 후보 추출
            seg_found = []
            for r in is_rows:
                nm = r.get("account_nm", "")
                aid = r.get("account_id", "")
                if not _is_segment_account(nm, aid):
                    continue
                raw = r.get("thstrm_amount", "").replace(",", "")
                try:
                    amount = float(raw)
                except (ValueError, TypeError):
                    continue
                # 총 매출의 0.1% ~ 130% 범위
                if total_rev > 0:
                    ratio = amount / total_rev
                    if ratio < 0.001 or ratio > 1.3:
                        continue
                seg_found.append({
                    "segment_name": nm,
                    "revenue": amount,
                    "account_id": aid,
                })

            if seg_found:
                log.info("  %s %d 세그먼트 %d개 발견: %s", stock_code, year, len(seg_found),
                         [s["segment_name"] for s in seg_found])
                for seg in seg_found:
                    conn.execute("""
                        INSERT OR REPLACE INTO segment_revenue
                        (stock_code, corp_code, year, quarter, segment_name,
                         revenue, operating_profit, assets, report_type)
                        VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 'CFS')
                    """, (stock_code, corp_code, year, quarter,
                          seg["segment_name"], seg["revenue"]))
                conn.commit()
                saved += len(seg_found)
            break  # 사업보고서(11011) 성공 시 다음 연도로
    return saved

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--codes", help="종목코드 comma-separated (없으면 전종목)")
    p.add_argument("--years", help="연도 범위 예) 2022,2023,2024")
    p.add_argument("--limit", type=int, default=200, help="처리 종목 수 제한")
    args = p.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",")]
    else:
        codes = [r[0] for r in conn.execute("""
            SELECT su.stock_code FROM stock_universe su
            WHERE su.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
              AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
              AND su.market_cap IS NOT NULL
            ORDER BY su.market_cap DESC
            LIMIT ?
        """, (args.limit,)).fetchall()]

    years = None
    if args.years:
        years = [int(y.strip()) for y in args.years.split(",")]

    total_saved = 0
    for i, code in enumerate(codes):
        log.info("[%d/%d] %s", i+1, len(codes), code)
        try:
            n = collect_segments_for_stock(conn, code, years)
            total_saved += n
            time.sleep(0.3)  # API rate limit
        except Exception as e:
            log.error("오류 %s: %s", code, e)

    log.info("완료: %d건 저장", total_saved)
    conn.close()

if __name__ == "__main__":
    main()
