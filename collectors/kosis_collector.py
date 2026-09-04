"""
통계청 KOSIS API 수집기
무료 API 키: https://kosis.kr/openapi/
환경변수: KOSIS_API_KEY (Base64 인코딩된 키)

사용 통계표:
- DT_1C8016: 경기종합지수 (선행/동행/후행 CI + 산업생산/소매판매/고용 등 22종)
"""
import sqlite3, requests, logging, os, time, base64
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"

BASE_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
HOUSING_TABLES = [
    ("408", "DT_40803_N0001"),
    ("101", "DT_1YL13501E"),
]

# C1_NM → (our_indicator_code, 단위)
C1_TO_CODE = {
    " 선행종합지수(2020＝100)":          ("KR_CLI_LEADING",    "지수"),
    " 동행종합지수(2020＝100)":          ("KR_CLI_COINCIDENT", "지수"),
    " 후행종합지수(2020＝100)":          ("KR_CLI_LAGGING",    "지수"),
    " 광공업생산지수(2020＝100)":        ("KR_INDUSTRIAL_PROD","지수"),
    " 서비스업생산지수(도소매업제외)(2020＝100)": ("KR_SERVICE_PROD",  "지수"),
    " 소매판매액지수(2020＝100)":        ("KR_RETAIL_SALES",   "지수"),
    " 소비자물가지수변화율(서비스)":      ("KR_CPI_SERVICE",    "%"),
    " 비농림어업취업자수(천명)":          ("KR_NONFARM_EMPLOY", "천명"),
    " 취업자수(천명)":                   ("KR_EMPLOYMENT",     "천명"),
    " 장단기금리차(%p)":                 ("KR_YIELD_SPREAD",   "%p"),
    " 경제심리지수":                     ("KR_ECON_SENTIMENT", "지수"),
    " 건설수주액(실질)(십억원)":          ("KR_CONSTRUCTION_ORDER", "십억원"),
    " 건설기성액(실질)(십억원)":          ("KR_CONSTRUCTION_OUTPUT","십억원"),
    " 생산자제품재고지수(2020＝100)":    ("KR_INVENTORY",      "지수"),
    " 재고순환지표(%p)":                 ("KR_INVENTORY_CYCLE","%p"),
    " 내수출하지수(2020＝100)":          ("KR_DOMESTIC_SHIP",  "지수"),
    " 기계류내수출하지수(선박제외)(2020＝100)": ("KR_MACHINERY_SHIP", "지수"),
    " 수입액(실질)(백만불)":             ("KR_IMPORT_REAL",    "백만달러"),
    " 소비재수입액(실질)(백만불)":        ("KR_CONSUMER_IMPORT","백만달러"),
    "수출입물가비율(2020＝100)":         ("KR_EXPORT_IMPORT_PRICE_RATIO","지수"),
    " CP유통수익률(%p)":                 ("KR_CP_YIELD",       "%"),
    "코스피(1980.1.4＝100)":            ("KR_KOSPI_KOSIS",    "지수"),
}

# 추가 등록이 필요한 카테고리 메타데이터
NEW_CATEGORIES = [
    ("KR_CLI_LEADING",    "경기선행종합지수",      "Leading Composite Index",       "KOREA","SENTIMENT",   "지수",   "KOSIS","DT_1C8016","MONTHLY",3),
    ("KR_CLI_COINCIDENT", "경기동행종합지수",      "Coincident Composite Index",    "KOREA","SENTIMENT",   "지수",   "KOSIS","DT_1C8016","MONTHLY",3),
    ("KR_CLI_LAGGING",    "경기후행종합지수",      "Lagging Composite Index",       "KOREA","SENTIMENT",   "지수",   "KOSIS","DT_1C8016","MONTHLY",2),
    ("KR_SERVICE_PROD",   "한국 서비스업생산지수", "Service Industry Production",   "KOREA","GROWTH",      "지수",   "KOSIS","DT_1C8016","MONTHLY",2),
    ("KR_CPI_SERVICE",    "소비자물가(서비스)",    "CPI Services YoY",              "KOREA","INFLATION",   "%",      "KOSIS","DT_1C8016","MONTHLY",2),
    ("KR_NONFARM_EMPLOY", "비농림취업자수",        "Nonfarm Employment",            "KOREA","EMPLOYMENT",  "천명",   "KOSIS","DT_1C8016","MONTHLY",2),
    ("KR_EMPLOYMENT",     "한국 취업자수",         "Total Employment",              "KOREA","EMPLOYMENT",  "천명",   "KOSIS","DT_1C8016","MONTHLY",3),
    ("KR_YIELD_SPREAD",   "장단기금리차",          "Yield Curve Spread",            "KOREA","BOND",        "%p",     "KOSIS","DT_1C8016","MONTHLY",3),
    ("KR_ECON_SENTIMENT", "경제심리지수(ESI)",     "Economic Sentiment Index",      "KOREA","SENTIMENT",   "지수",   "KOSIS","DT_1C8016","MONTHLY",2),
    ("KR_CONSTRUCTION_ORDER","건설수주액(실질)",   "Construction Orders (Real)",    "KOREA","REALESTATE",  "십억원", "KOSIS","DT_1C8016","MONTHLY",1),
    ("KR_CONSTRUCTION_OUTPUT","건설기성액(실질)",  "Construction Output (Real)",    "KOREA","REALESTATE",  "십억원", "KOSIS","DT_1C8016","MONTHLY",1),
    ("KR_INVENTORY",      "생산자재고지수",        "Producer Inventory Index",      "KOREA","PRODUCTION",  "지수",   "KOSIS","DT_1C8016","MONTHLY",1),
    ("KR_INVENTORY_CYCLE","재고순환지표",          "Inventory Cycle Indicator",     "KOREA","PRODUCTION",  "%p",     "KOSIS","DT_1C8016","MONTHLY",2),
    ("KR_DOMESTIC_SHIP",  "내수출하지수",          "Domestic Shipment Index",       "KOREA","PRODUCTION",  "지수",   "KOSIS","DT_1C8016","MONTHLY",1),
    ("KR_MACHINERY_SHIP", "기계류내수출하지수",    "Machinery Domestic Shipment",   "KOREA","PRODUCTION",  "지수",   "KOSIS","DT_1C8016","MONTHLY",1),
    ("KR_IMPORT_REAL",    "수입액(실질)",          "Imports (Real)",                "KOREA","TRADE",       "백만달러","KOSIS","DT_1C8016","MONTHLY",2),
    ("KR_CONSUMER_IMPORT","소비재수입액(실질)",    "Consumer Goods Imports (Real)", "KOREA","TRADE",       "백만달러","KOSIS","DT_1C8016","MONTHLY",1),
    ("KR_EXPORT_IMPORT_PRICE_RATIO","수출입물가비율","Export/Import Price Ratio",  "KOREA","TRADE",       "지수",   "KOSIS","DT_1C8016","MONTHLY",1),
    ("KR_CP_YIELD",       "CP유통수익률",          "CP Distribution Yield",         "KOREA","MONETARY",    "%",      "KOSIS","DT_1C8016","MONTHLY",1),
    ("KR_KOSPI_KOSIS",    "KOSPI(KOSIS)",         "KOSPI Index (KOSIS)",           "KOREA","MARKET",      "지수",   "KOSIS","DT_1C8016","MONTHLY",1),
    # 추가 Yahoo 기반 누락 코드
    ("EU_EUR_USD",        "유로/달러 환율",        "EUR/USD Exchange Rate",         "EU",   "FX",          "달러",   "YAHOO","EURUSD=X", "DAILY",  2),
    ("EU_DAX",            "독일 DAX 지수",         "Germany DAX Index",             "EU",   "MARKET",      "포인트", "YAHOO","^GDAXI",   "DAILY",  2),
    ("EU_FTSE",           "영국 FTSE 100",         "UK FTSE 100 Index",             "EU",   "MARKET",      "포인트", "YAHOO","^FTSE",    "DAILY",  2),
    ("JP_NIKKEI",         "일본 닛케이 225",        "Japan Nikkei 225",              "JP",   "MARKET",      "포인트", "YAHOO","^N225",    "DAILY",  2),
    ("US_10Y_YIELD_YH",   "미국 10년 국채(Yahoo)", "10Y Treasury Yield (Yahoo)",    "US",   "BOND",        "%",      "YAHOO","^TNX",     "DAILY",  3),
]


def _get_api_key() -> str | None:
    key = os.getenv("KOSIS_API_KEY", "")
    if not key:
        logger.warning("KOSIS_API_KEY not set. Skipping KOSIS collection.")
        return None
    key = key.strip()
    # 일부 환경에서는 KOSIS 키를 base64로 보관한다.
    try:
        decoded = base64.b64decode(key).decode("utf-8").strip()
        if decoded and decoded.isprintable():
            key = decoded
    except Exception:
        pass
    return key or None


def _register_categories(conn: sqlite3.Connection):
    """신규 지표 카테고리 등록"""
    conn.executemany("""
        INSERT OR IGNORE INTO global_macro_categories
        (code,name,name_en,category,subcategory,unit,source,source_code,frequency,importance)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, NEW_CATEGORIES)
    conn.commit()


def _fetch_ci(api_key: str, start_ym: str, end_ym: str) -> list[dict]:
    """경기종합지수 전체 데이터 가져오기"""
    params = {
        "method": "getList",
        "apiKey": api_key,
        "itmId": "ALL",
        "objL1": "ALL",
        "format": "json",
        "jsonVD": "Y",
        "prdSe": "M",
        "startPrdDe": start_ym,
        "endPrdDe": end_ym,
        "orgId": "101",
        "tblId": "DT_1C8016",
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        logger.warning(f"KOSIS CI response: {data}")
        return []
    except Exception as e:
        logger.warning(f"KOSIS CI fetch failed: {e}")
        return []


def _fetch_table(api_key: str, org_id: str, tbl_id: str, start_ym: str, end_ym: str) -> list[dict]:
    params = {
        "method": "getList",
        "apiKey": api_key,
        "itmId": "ALL",
        "objL1": "ALL",
        "format": "json",
        "jsonVD": "Y",
        "prdSe": "M",
        "startPrdDe": start_ym,
        "endPrdDe": end_ym,
        "orgId": org_id,
        "tblId": tbl_id,
    }
    try:
        resp = requests.get(BASE_URL, params=params, timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return data
        logger.warning(f"KOSIS housing response [{tbl_id}]: {data}")
        return []
    except Exception as e:
        logger.warning(f"KOSIS housing fetch failed [{tbl_id}]: {e}")
        return []


def _pick_housing_series(rows: list[dict]) -> list[tuple[str, float]]:
    picked: list[tuple[str, float]] = []
    for row in rows:
        region = " ".join([
            str(row.get("C1_NM", "")),
            str(row.get("C2_NM", "")),
            str(row.get("OBJ_L1_NM", "")),
        ])
        label = " ".join([
            str(row.get("ITM_NM", "")),
            str(row.get("C3_NM", "")),
            str(row.get("C4_NM", "")),
        ])
        if "전국" not in region:
            continue
        if not any(token in label for token in ("종합주택", "주택종합", "매매가격지수", "주택매매가격지수")):
            continue
        prd = str(row.get("PRD_DE", ""))
        val_str = str(row.get("DT", ""))
        if not val_str or val_str in ("", "-", ".."):
            continue
        try:
            val = float(val_str.replace(",", ""))
            if len(prd) == 6 and prd.isdigit():
                picked.append((f"{prd[:4]}-{prd[4:6]}-01", val))
        except (TypeError, ValueError):
            continue
    return picked


def collect_kosis(lookback_years: int = 5) -> int:
    api_key = _get_api_key()
    if not api_key:
        return 0

    now = datetime.now()
    start_dt = now - timedelta(days=lookback_years * 365)
    start_ym = start_dt.strftime("%Y%m")
    end_ym = now.strftime("%Y%m")

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")

    # 신규 카테고리 등록
    _register_categories(conn)

    # 경기종합지수 수집
    rows = _fetch_ci(api_key, start_ym, end_ym)
    by_code: dict[str, list[tuple[str, float]]] = {}
    if rows:
        for row in rows:
            c1_nm = row.get("C1_NM", "")
            mapping = C1_TO_CODE.get(c1_nm)
            if not mapping:
                continue
            our_code, _ = mapping
            prd = row.get("PRD_DE", "")
            val_str = row.get("DT", "")
            if not val_str or val_str in ("", "-", ".."):
                continue
            try:
                val = float(str(val_str).replace(",", ""))
                if len(prd) == 6 and prd.isdigit():
                    date = f"{prd[:4]}-{prd[4:6]}-01"
                else:
                    date = prd
                by_code.setdefault(our_code, []).append((date, val))
            except (ValueError, TypeError):
                continue

    for org_id, tbl_id in HOUSING_TABLES:
        housing_rows = _fetch_table(api_key, org_id, tbl_id, start_ym, end_ym)
        housing_series = _pick_housing_series(housing_rows)
        if housing_series:
            by_code["KR_HOUSING_PRICE"] = housing_series
            break

    if not by_code:
        conn.close()
        return 0

    # DB 저장
    total = 0
    for our_code, values in by_code.items():
        values.sort(key=lambda x: x[0])
        for i, (date, val) in enumerate(values):
            prev = values[i - 1][1] if i > 0 else None
            chg = ((val - prev) / abs(prev) * 100) if prev and prev != 0 else None
            conn.execute("""
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value,
                    prev_value=excluded.prev_value,
                    change_pct=excluded.change_pct
            """, (our_code, date, val, prev, chg))
            total += 1

    conn.commit()
    conn.close()
    _log(total)
    logger.info(f"KOSIS collected {total} records ({len(by_code)} indicators)")
    return total


def _log(records: int, status: str = "ok", msg: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("""
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('kosis', ?, ?, ?)
        """, (status, records, msg))
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    import dotenv
    dotenv.load_dotenv("/Volumes/Realtek_NVME/stock_dashboard/runtime/.env")
    logging.basicConfig(level=logging.INFO)
    n = collect_kosis()
    print(f"KOSIS 수집 완료: {n}건")
