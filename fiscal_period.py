"""비12월 결산 회사의 재무 기간 키 정규화(2026-10-04, docs/FINANCIAL_STATEMENTS.md §2-7).

DB 규칙(정본): financial_data·cash_flow_data 의 (year, quarter)는 **회계 기준**이다.
  year    = 회계연도 = 결산 종료 연도(사업보고서 (YYYY.MM)의 YYYY — §2-4 사업부문 규칙과 같다)
  quarter = 회계 분기(1분기·반기·3분기 보고서 위치, 4 = 연간 − 1~3분기)
12월 결산이면 회계 = 달력이라 기존과 같다.

DART API 표기와의 차이: DART `bsns_year`는 **기간이 끝나는 달력 연도**다. 그래서 3월 결산사의 (2025, 1분기)=2025년 4~6월은
회계연도 2026의 1분기인데, (2025, 사업보고서)=회계연도 2025다. DART 표기를 그대로 저장하면 같은 '2025'에 두 회계연도가 섞여
4분기(연간 − 1~3분기)·전년 대비·TTM이 틀린다. → DART 표기 (bsns_year, 분기)를 받는 저장 경로는 반드시 to_fiscal()을 거친다.
결산월은 stock_collection_config 의 fs_quirk:fiscal_month(정수 1~12)에서 읽는다(없으면 12월).
"""
import threading
import time

_CACHE = {"at": 0.0, "map": {}}
_LOCK = threading.Lock()


def fiscal_month_map(conn=None) -> dict:
    with _LOCK:
        if time.time() - _CACHE["at"] < 3600 and _CACHE["map"]:
            return _CACHE["map"]
        own = conn is None
        if own:
            from db_compat import connect_primary_db
            conn = connect_primary_db(timeout=30, readonly=True)
        try:
            rows = conn.execute("SELECT stock_code, config_value FROM stock_collection_config WHERE config_key='fs_quirk:fiscal_month'").fetchall()
            _CACHE["map"] = {r[0]: int(r[1]) for r in rows if str(r[1]).isdigit()}
            _CACHE["at"] = time.time()
        finally:
            if own:
                conn.close()
        return _CACHE["map"]


def fiscal_month(stock_code: str, conn=None) -> int:
    return fiscal_month_map(conn).get(stock_code, 12)


def to_fiscal(stock_code: str, bsns_year: int, quarter: int, is_annual: bool = False, conn=None) -> tuple:
    """DART 표기(bsns_year=기간 종료 달력 연도, quarter=1·2·3 보고서 위치) → 회계 기준 (year, quarter).
    연간·4분기는 기간 종료 월이 결산월이라 연도가 그대로다."""
    f = fiscal_month(stock_code, conn)
    if f == 12 or is_annual or quarter not in (1, 2, 3):
        return int(bsns_year), int(quarter)
    end_month = (f + 3 * quarter - 1) % 12 + 1
    return (int(bsns_year) + 1 if end_month > f else int(bsns_year)), int(quarter)


def period_end(stock_code: str, fiscal_year: int, quarter: int, conn=None) -> str:
    """회계 키 → 기간 종료 'YYYY-MM'(FnGuide 열 표기와 같은 기준). quarter 0·4 = 결산월."""
    f = fiscal_month(stock_code, conn)
    q = 4 if quarter in (0, 4) else quarter
    m = (f + 3 * q - 1) % 12 + 1
    y = fiscal_year if m <= f else fiscal_year - 1
    return f"{y:04d}-{m:02d}"
