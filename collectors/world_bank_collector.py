"""
World Bank Open Data 수집기
무료, API 키 불필요
https://api.worldbank.org/v2/
"""
import sqlite3, requests, logging, time
from datetime import datetime

logger = logging.getLogger(__name__)
DB_PATH = "stock.db"

# (country_iso, wb_indicator, our_code)
WORLD_BANK_INDICATORS = [
    # ── 글로벌 ────────────────────────────────────────────────
    ("WLD", "NY.GDP.MKTP.KD.ZG", "GLOBAL_GDP_GROWTH"),
    ("WLD", "FP.CPI.TOTL.ZG",    "GLOBAL_INFLATION"),
    ("WLD", "NE.EXP.GNFS.KD.ZG", "GLOBAL_EXPORT_VOL"),
    ("WLD", "NE.IMP.GNFS.KD.ZG", "GLOBAL_IMPORT_VOL"),
    # ── 한국 ──────────────────────────────────────────────────
    ("KOR", "NY.GDP.MKTP.KD.ZG", "KR_GDP_GROWTH"),
    ("KOR", "FP.CPI.TOTL.ZG",    "KR_CPI"),
    ("KOR", "SL.UEM.TOTL.ZS",    "KR_UNEMPLOYMENT"),
    ("KOR", "BN.CAB.XOKA.GD.ZS", "KR_CURRENT_ACCOUNT"),
    # ── 미국 ──────────────────────────────────────────────────
    ("USA", "FP.CPI.TOTL.ZG",    "US_CPI"),
    ("USA", "SL.UEM.TOTL.ZS",    "US_UNEMPLOYMENT"),
    # ── 유럽 ──────────────────────────────────────────────────
    ("EUU", "NY.GDP.MKTP.KD.ZG", "EU_GDP_GROWTH"),
    ("EUU", "FP.CPI.TOTL.ZG",    "EU_CPI"),
    ("EUU", "SL.UEM.TOTL.ZS",    "EU_UNEMPLOYMENT"),
    # ── 중국 ──────────────────────────────────────────────────
    ("CHN", "NY.GDP.MKTP.KD.ZG", "CN_GDP_GROWTH"),
    ("CHN", "FP.CPI.TOTL.ZG",    "CN_CPI"),
    # ── 일본 ──────────────────────────────────────────────────
    ("JPN", "FP.CPI.TOTL.ZG",    "JP_CPI"),
]

BASE_URL = "https://api.worldbank.org/v2"


def _fetch_indicator(country: str, indicator: str, per_page: int = 20) -> list[dict]:
    url = f"{BASE_URL}/country/{country}/indicator/{indicator}"
    params = {"format": "json", "per_page": per_page, "mrv": per_page}
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not data or len(data) < 2 or not data[1]:
            return []
        return data[1]  # 실제 데이터 배열
    except Exception as e:
        logger.warning(f"WorldBank fetch failed [{country}/{indicator}]: {e}")
        return []


def _upsert(conn: sqlite3.Connection, code: str, date: str, value: float | None):
    conn.execute("""
        INSERT INTO global_macro_data (indicator_code, date, value)
        VALUES (?, ?, ?)
        ON CONFLICT(indicator_code, date) DO UPDATE SET value=excluded.value
    """, (code, date, value))


def collect_world_bank() -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT OR IGNORE INTO global_macro_categories
        (code,name,name_en,category,subcategory,unit,source,source_code,frequency,importance)
        VALUES
        ('GLOBAL_EXPORT_VOL','세계 수출물량 증가율','World Exports Volume Growth','GLOBAL','TRADE','%','WORLD_BANK','NE.EXP.GNFS.KD.ZG','ANNUAL',2),
        ('GLOBAL_IMPORT_VOL','세계 수입물량 증가율','World Imports Volume Growth','GLOBAL','TRADE','%','WORLD_BANK','NE.IMP.GNFS.KD.ZG','ANNUAL',2)
    """)
    total = 0
    trade_components: dict[str, dict[str, float]] = {}
    for country, wb_ind, our_code in WORLD_BANK_INDICATORS:
        rows = _fetch_indicator(country, wb_ind, per_page=15)
        values = []
        for r in rows:
            if r.get("value") is None:
                continue
            year = str(r.get("date", ""))
            if not year.isdigit():
                continue
            date_str = f"{year}-12-31"  # 연간 데이터 → 연말 기준
            values.append((date_str, float(r["value"])))
        values.sort(key=lambda x: x[0])
        for i, (date_str, val) in enumerate(values):
            prev = values[i - 1][1] if i > 0 else None
            chg = ((val - prev) / abs(prev) * 100) if prev else None
            conn.execute("""
                INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(indicator_code, date) DO UPDATE SET
                    value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
            """, (our_code, date_str, val, prev, chg))
            if our_code in ("GLOBAL_EXPORT_VOL", "GLOBAL_IMPORT_VOL"):
                trade_components.setdefault(date_str, {})[our_code] = val
            total += 1
        time.sleep(0.3)  # rate limit

    trade_values = []
    for date_str, parts in trade_components.items():
        if "GLOBAL_EXPORT_VOL" in parts and "GLOBAL_IMPORT_VOL" in parts:
            trade_values.append((date_str, (parts["GLOBAL_EXPORT_VOL"] + parts["GLOBAL_IMPORT_VOL"]) / 2.0))
    trade_values.sort(key=lambda x: x[0])
    for i, (date_str, val) in enumerate(trade_values):
        prev = trade_values[i - 1][1] if i > 0 else None
        chg = ((val - prev) / abs(prev) * 100) if prev else None
        conn.execute("""
            INSERT INTO global_macro_data (indicator_code, date, value, prev_value, change_pct)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(indicator_code, date) DO UPDATE SET
                value=excluded.value, prev_value=excluded.prev_value, change_pct=excluded.change_pct
        """, ("GLOBAL_TRADE_VOL", date_str, val, prev, chg))
        total += 1
    conn.commit()
    conn.close()
    _log(total)
    logger.info(f"WorldBank collected {total} records")
    return total


def _log(records: int, status: str = "ok", msg: str = ""):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute("""
            INSERT INTO global_macro_collection_log (source, status, records, message)
            VALUES ('world_bank', ?, ?, ?)
        """, (status, records, msg))
        conn.commit()
        conn.close()
    except Exception:
        pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    n = collect_world_bank()
    print(f"수집 완료: {n}건")
