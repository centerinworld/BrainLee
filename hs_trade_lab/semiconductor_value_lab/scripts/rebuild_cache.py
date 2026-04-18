from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile
from xml.etree import ElementTree as ET


PROJECT_DIR = Path("/Applications/stock_dashboard/hs_trade_lab/semiconductor_value_lab")
DATA_DIR = PROJECT_DIR / "data"
DB_PATH = DATA_DIR / "semiconductor_value_lab.db"
ROOT_STOCK_DB = Path("/Applications/stock_dashboard/stock.db")
EXPORT_DIR = Path("/Users/brainlee/Downloads")


def _find_workbook() -> Path:
    """Downloads 폴더에서 'value' 또는 '반도체' 키워드를 포함한 엑셀 파일을 자동 탐색."""
    search_dirs = [
        Path("/Users/brainlee/Downloads"),
        Path("/Users/brainlee/Desktop"),
        Path.home() / "Downloads",
        Path.home() / "Desktop",
    ]
    keywords = ["value", "반도체", "semiconductor"]
    for d in search_dirs:
        if not d.exists():
            continue
        for ext in ("*.xlsx", "*.xlsm", "*.xls"):
            for f in sorted(d.glob(ext), key=lambda p: p.stat().st_mtime, reverse=True):
                name_lower = f.name.lower()
                if any(kw in name_lower for kw in keywords):
                    return f
    # 기존 경로 fallback
    return Path("/Users/brainlee/Downloads/반도체 업종 Value Stream의 사본.xlsx")


WORKBOOK_PATH = _find_workbook()

DATA_DIR.mkdir(parents=True, exist_ok=True)

NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

WATCHLIST_DEFAULTS = [
    ("080220", "제주반도체"),
    ("000990", "DB하이텍"),
    ("348350", "위드텍"),
]


@dataclass
class ReferenceDates:
    current_label: str
    ref_a: str
    ref_b: str
    ref_c: str


def col_to_num(col: str) -> int:
    value = 0
    for ch in col:
        value = value * 26 + ord(ch) - 64
    return value


def excel_serial_to_yyyymmdd(serial_text: str) -> str:
    if not serial_text:
        return ""
    serial = float(serial_text)
    base = date(1899, 12, 30)
    target = base + timedelta(days=int(serial))
    return target.strftime("%Y%m%d")


def normalize_code(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"(\d{6})", value)
    if match:
        return match.group(1)
    return value.strip()


def to_float(value: str) -> float | None:
    if value in ("", None, "#N/A", "N/A", "No"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round(((current - previous) / previous) * 100.0, 2)


def normalize_workbook_pct(value: float | None) -> float | None:
    if value is None:
        return None
    if abs(value) <= 1:
        return round(value * 100.0, 2)
    return round(value, 2)


class WorkbookReader:
    def __init__(self, path: Path):
        self.path = path
        self.shared_strings: list[str] = []
        self.sheet_map: dict[str, str] = {}
        self._load_workbook()

    def _load_workbook(self) -> None:
        with ZipFile(self.path) as zf:
            workbook = ET.fromstring(zf.read("xl/workbook.xml"))
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            rel_map = {row.attrib["Id"]: "xl/" + row.attrib["Target"] for row in rels}
            if "xl/sharedStrings.xml" in zf.namelist():
                root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
                self.shared_strings = [
                    "".join(node.text or "" for node in item.iterfind(".//a:t", NS))
                    for item in root.findall("a:si", NS)
                ]
            for sheet in workbook.find("a:sheets", NS):
                rel_id = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
                self.sheet_map[sheet.attrib["name"]] = rel_map[rel_id]

    def rows(self, sheet_name: str) -> list[list[str]]:
        with ZipFile(self.path) as zf:
            xml = ET.fromstring(zf.read(self.sheet_map[sheet_name]))
        rows: list[list[str]] = []
        for row in xml.findall(".//a:sheetData/a:row", NS):
            values: dict[int, str] = {}
            for cell in row.findall("a:c", NS):
                ref = cell.attrib["r"]
                col = re.match(r"[A-Z]+", ref).group(0)
                idx = col_to_num(col)
                cell_type = cell.attrib.get("t")
                value = ""
                inline = cell.find("a:is", NS)
                if inline is not None:
                    value = "".join(node.text or "" for node in inline.iterfind(".//a:t", NS))
                else:
                    val = cell.find("a:v", NS)
                    if val is not None:
                        raw = val.text or ""
                        if cell_type == "s":
                            value = self.shared_strings[int(raw)]
                        else:
                            value = raw
                values[idx] = value
            width = max(values) if values else 0
            rows.append([values.get(i, "") for i in range(1, width + 1)])
        return rows


def get_root_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(ROOT_STOCK_DB)
    conn.row_factory = sqlite3.Row
    return conn


def get_cache_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;

        DROP TABLE IF EXISTS meta;
        DROP TABLE IF EXISTS sector_summary;
        DROP TABLE IF EXISTS stock_cache;
        DROP TABLE IF EXISTS yearly_cache;
        DROP TABLE IF EXISTS quarterly_cache;
        DROP TABLE IF EXISTS watchlist;

        CREATE TABLE meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE sector_summary (
            sector_order INTEGER PRIMARY KEY,
            sector_name TEXT NOT NULL,
            stock_count INTEGER,
            rising_count INTEGER,
            rising_ratio REAL,
            avg_psr REAL,
            kospi_vs TEXT,
            nasdaq_vs TEXT,
            sample_stock TEXT,
            sample_lv1 TEXT,
            sample_industry TEXT,
            sample_market_cap_okr REAL,
            sample_price REAL
        );

        CREATE TABLE stock_cache (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT NOT NULL,
            lv1 TEXT,
            lv2 TEXT,
            customer TEXT,
            main_business TEXT,
            ticker TEXT,
            etf_flag TEXT,
            workbook_price REAL,
            workbook_change_pct REAL,
            workbook_market_cap_okr REAL,
            workbook_psr REAL,
            current_price REAL,
            current_market_cap REAL,
            current_pbr REAL,
            current_per REAL,
            current_base_date TEXT,
            ref_a_price REAL,
            ref_b_price REAL,
            ref_c_price REAL,
            ref_a_change_pct REAL,
            ref_b_change_pct REAL,
            ref_c_change_pct REAL,
            latest_revenue REAL,
            latest_operating_profit REAL,
            latest_net_income REAL,
            annual_yoy REAL,
            quarter_qoq REAL,
            last_quarter_label TEXT,
            status_complete TEXT,
            note TEXT,
            watch_enabled INTEGER DEFAULT 0,
            watch_lv2 TEXT,
            watch_customer TEXT
        );

        CREATE TABLE yearly_cache (
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            item_type TEXT NOT NULL,
            fiscal_year INTEGER NOT NULL,
            value REAL,
            PRIMARY KEY (stock_code, item_type, fiscal_year)
        );

        CREATE TABLE quarterly_cache (
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            item_type TEXT NOT NULL,
            period_label TEXT NOT NULL,
            sort_year INTEGER NOT NULL,
            sort_quarter INTEGER NOT NULL,
            value REAL,
            PRIMARY KEY (stock_code, item_type, period_label)
        );

        CREATE TABLE watchlist (
            stock_code TEXT PRIMARY KEY,
            stock_name TEXT NOT NULL,
            lv2_override TEXT,
            customer_override TEXT,
            memo TEXT DEFAULT ''
        );
        """
    )
    conn.commit()


def load_reference_dates(summary_rows: list[list[str]]) -> ReferenceDates:
    # Use fixed defaults requested by the user so cached values are stable and
    # immediately reusable until the reference dates are edited manually.
    return ReferenceDates(
        current_label="20260101",
        ref_a="20250801",
        ref_b="20250101",
        ref_c="20260101",
    )


def parse_sector_summary(rows: list[list[str]]) -> list[dict]:
    items: list[dict] = []
    for row in rows[9:28]:
        if not row or not row[0]:
            continue
        order = int(float(row[0]))
        items.append(
            {
                "sector_order": order,
                "sector_name": row[1],
                "stock_count": int(float(row[3])) if row[3] else None,
                "rising_count": int(float(row[4])) if row[4] else None,
                "rising_ratio": to_float(row[5]),
                "avg_psr": to_float(row[6]),
                "kospi_vs": row[7],
                "nasdaq_vs": row[8],
                "sample_stock": row[11],
                "sample_lv1": row[12],
                "sample_industry": row[13],
                "sample_market_cap_okr": to_float(row[14]),
                "sample_price": to_float(row[15]),
            }
        )
    return items


def parse_stock_rows(rows: list[list[str]]) -> list[dict]:
    items: list[dict] = []
    for row in rows[1:]:
        if not row or not row[1]:
            continue
        code = normalize_code(row[20] if len(row) > 20 else "")
        items.append(
            {
                "stock_code": code,
                "stock_name": row[1],
                "lv1": row[2],
                "lv2": row[3],
                "customer": row[4],
                "main_business": row[5],
                "workbook_price": to_float(row[6]),
                "workbook_change_pct": to_float(row[7]),
                "workbook_market_cap_okr": to_float(row[8]),
                "workbook_psr": to_float(row[13]),
                "ref_a_raw": to_float(row[14]),
                "ref_a_pct": to_float(row[15]),
                "ref_b_raw": to_float(row[16]),
                "ref_b_pct": to_float(row[17]),
                "ref_c_raw": to_float(row[18]),
                "ref_c_pct": to_float(row[19]),
                "ticker": row[20],
                "etf_flag": row[21] if len(row) > 21 else "",
                "status_complete": row[11] if len(row) > 11 else "",
            }
        )
    return items


def dedupe_stock_rows(items: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    ordered_keys: list[str] = []
    for item in items:
        key = item["stock_code"] or item["stock_name"]
        previous = deduped.get(key)
        if previous is None:
            deduped[key] = item
            ordered_keys.append(key)
            continue
        # Prefer the row that carries more workbook structure and richer labels.
        score_prev = sum(1 for field in ("lv1", "lv2", "customer", "main_business", "ticker") if previous.get(field))
        score_new = sum(1 for field in ("lv1", "lv2", "customer", "main_business", "ticker") if item.get(field))
        if score_new >= score_prev:
            deduped[key] = item
    return [deduped[key] for key in ordered_keys]


def parse_financial_rows(rows: list[list[str]], item_name: str) -> list[dict]:
    header = rows[1]
    periods = header[5:]
    items: list[dict] = []
    for row in rows[2:]:
        if not row or not row[1]:
            continue
        stock_name = row[1]
        item_type = row[4]
        for idx, period in enumerate(periods, start=5):
            if idx >= len(row):
                continue
            value = to_float(row[idx])
            if value is None:
                continue
            items.append(
                {
                    "sheet": item_name,
                    "stock_name": stock_name,
                    "item_type": item_type,
                    "period": period,
                    "value": value,
                }
            )
    return items


def dedupe_financial_rows(items: list[dict]) -> list[dict]:
    deduped: dict[tuple[str, str, str], dict] = {}
    ordered_keys: list[tuple[str, str, str]] = []
    for item in items:
        key = (item["stock_name"], item["item_type"], item["period"])
        previous = deduped.get(key)
        if previous is None:
            deduped[key] = item
            ordered_keys.append(key)
            continue
        prev_value = previous.get("value")
        new_value = item.get("value")
        if new_value is not None and prev_value is None:
            deduped[key] = item
    return [deduped[key] for key in ordered_keys]


def parse_workbook_year(period_text: str) -> int | None:
    if not period_text:
        return None
    text = period_text.replace("'", "").strip()
    match = re.search(r"(\d{2,4})\s*년?", text)
    if not match:
        return None
    value = int(match.group(1))
    if value < 100:
        return 2000 + value
    return value


def dedupe_yearly_rows(rows: list[tuple[str, str, str, int, float]]) -> list[tuple[str, str, str, int, float]]:
    deduped: dict[tuple[str, str, int], tuple[str, str, str, int, float]] = {}
    ordered_keys: list[tuple[str, str, int]] = []
    for row in rows:
        key = (row[0], row[2], row[3])
        if key not in deduped:
            ordered_keys.append(key)
        deduped[key] = row
    return [deduped[key] for key in ordered_keys]


def load_latest_stock_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    sql = """
    SELECT su.stock_code,
           su.stock_name,
           su.market_cap,
           su.pbr,
           su.per,
           sp.bas_dt,
           sp.close_price
      FROM stock_universe su
      JOIN (
            SELECT stock_code, MAX(bas_dt) AS bas_dt
              FROM stock_price_daily
             GROUP BY stock_code
           ) latest
        ON latest.stock_code = su.stock_code
      JOIN stock_price_daily sp
        ON sp.stock_code = latest.stock_code
       AND sp.bas_dt = latest.bas_dt
    """
    return {row["stock_code"]: row for row in conn.execute(sql)}


def get_price_on_or_before(conn: sqlite3.Connection, stock_code: str, yyyymmdd: str) -> float | None:
    if not stock_code or not yyyymmdd:
        return None
    row = conn.execute(
        """
        SELECT close_price
          FROM stock_price_daily
         WHERE stock_code = ?
           AND bas_dt <= ?
         ORDER BY bas_dt DESC
         LIMIT 1
        """,
        (stock_code, yyyymmdd),
    ).fetchone()
    return float(row["close_price"]) if row else None


def load_latest_fundamentals(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    sql = """
    SELECT f.stock_code,
           f.year,
           f.quarter,
           f.revenue,
           f.operating_profit,
           f.net_income
      FROM financial_data f
      JOIN (
            SELECT stock_code,
                   MAX(year * 10 + CASE WHEN quarter IS NULL THEN 0 ELSE quarter END) AS max_period
              FROM financial_data
             WHERE is_annual = 0
             GROUP BY stock_code
           ) latest
        ON latest.stock_code = f.stock_code
       AND latest.max_period = f.year * 10 + f.quarter
     WHERE f.is_annual = 0
    """
    return {row["stock_code"]: row for row in conn.execute(sql)}


def get_previous_quarter(conn: sqlite3.Connection, stock_code: str, year: int, quarter: int) -> sqlite3.Row | None:
    target = year * 10 + quarter
    return conn.execute(
        """
        SELECT *
          FROM financial_data
         WHERE stock_code = ?
           AND is_annual = 0
           AND year * 10 + quarter < ?
         ORDER BY year DESC, quarter DESC
         LIMIT 1
        """,
        (stock_code, target),
    ).fetchone()


def get_same_quarter_prev_year(conn: sqlite3.Connection, stock_code: str, year: int, quarter: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
          FROM financial_data
         WHERE stock_code = ?
           AND is_annual = 0
           AND year = ?
           AND quarter = ?
         LIMIT 1
        """,
        (stock_code, year - 1, quarter),
    ).fetchone()


def insert_meta(conn: sqlite3.Connection, pairs: dict[str, str]) -> None:
    conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)", pairs.items())


def rebuild() -> None:
    wb_path = _find_workbook()
    if not wb_path.exists():
        raise FileNotFoundError(
            f"엑셀 파일을 찾을 수 없습니다.\n"
            f"탐색 경로: Downloads, Desktop 폴더에서 value/반도체/semiconductor 키워드 포함 파일\n"
            f"마지막 시도 경로: {wb_path}"
        )
    print(f"[rebuild] 엑셀 파일: {wb_path}")
    reader = WorkbookReader(wb_path)
    summary_rows = reader.rows("종합")
    stock_rows = reader.rows("전종목")
    annual_rows = reader.rows("실적Y")
    quarterly_rows = reader.rows("실적Q")

    refs = load_reference_dates(summary_rows)
    sectors = parse_sector_summary(summary_rows)
    stocks = dedupe_stock_rows(parse_stock_rows(stock_rows))
    annual_items = dedupe_financial_rows(parse_financial_rows(annual_rows, "실적Y"))
    quarterly_items = dedupe_financial_rows(parse_financial_rows(quarterly_rows, "실적Q"))

    root_conn = get_root_conn()
    latest_stock = load_latest_stock_rows(root_conn)
    latest_fin = load_latest_fundamentals(root_conn)

    cache_conn = get_cache_conn()
    init_schema(cache_conn)

    insert_meta(
        cache_conn,
        {
            "workbook_path": str(wb_path),
            "rebuilt_at": datetime.now().isoformat(timespec="seconds"),
            "current_label": refs.current_label,
            "ref_a": refs.ref_a,
            "ref_b": refs.ref_b,
            "ref_c": refs.ref_c,
        },
    )

    cache_conn.executemany(
        """
        INSERT INTO sector_summary (
            sector_order, sector_name, stock_count, rising_count, rising_ratio,
            avg_psr, kospi_vs, nasdaq_vs, sample_stock, sample_lv1,
            sample_industry, sample_market_cap_okr, sample_price
        ) VALUES (
            :sector_order, :sector_name, :stock_count, :rising_count, :rising_ratio,
            :avg_psr, :kospi_vs, :nasdaq_vs, :sample_stock, :sample_lv1,
            :sample_industry, :sample_market_cap_okr, :sample_price
        )
        """,
        sectors,
    )

    stock_insert_rows = []
    for item in stocks:
        code = item["stock_code"]
        latest = latest_stock.get(code) if code else None
        latest_q = latest_fin.get(code) if code else None
        current_price = float(latest["close_price"]) if latest else item["workbook_price"]
        ref_a_price = (get_price_on_or_before(root_conn, code, refs.ref_a) if code else None) or item["ref_a_raw"]
        ref_b_price = (get_price_on_or_before(root_conn, code, refs.ref_b) if code else None) or item["ref_b_raw"]
        ref_c_price = (get_price_on_or_before(root_conn, code, refs.ref_c) if code else None) or item["ref_c_raw"]

        annual_yoy = None
        quarter_qoq = None
        latest_revenue = None
        latest_operating_profit = None
        latest_net_income = None
        last_quarter_label = ""
        if latest_q:
            latest_revenue = float(latest_q["revenue"]) if latest_q["revenue"] is not None else None
            latest_operating_profit = (
                float(latest_q["operating_profit"]) if latest_q["operating_profit"] is not None else None
            )
            latest_net_income = float(latest_q["net_income"]) if latest_q["net_income"] is not None else None
            prev = get_previous_quarter(root_conn, code, int(latest_q["year"]), int(latest_q["quarter"]))
            prev_year = get_same_quarter_prev_year(root_conn, code, int(latest_q["year"]), int(latest_q["quarter"]))
            quarter_qoq = pct_change(latest_revenue, float(prev["revenue"])) if prev and prev["revenue"] else None
            annual_yoy = (
                pct_change(latest_revenue, float(prev_year["revenue"]))
                if prev_year and prev_year["revenue"]
                else None
            )
            last_quarter_label = f"{latest_q['year']}-Q{latest_q['quarter']}"

        stock_insert_rows.append(
            {
                "stock_code": code or item["stock_name"],
                "stock_name": item["stock_name"],
                "lv1": item["lv1"],
                "lv2": item["lv2"],
                "customer": item["customer"],
                "main_business": item["main_business"],
                "ticker": item["ticker"],
                "etf_flag": item["etf_flag"],
                "workbook_price": item["workbook_price"],
                "workbook_change_pct": item["workbook_change_pct"],
                "workbook_market_cap_okr": item["workbook_market_cap_okr"],
                "workbook_psr": item["workbook_psr"],
                "current_price": current_price,
                "current_market_cap": float(latest["market_cap"]) if latest and latest["market_cap"] is not None else None,
                "current_pbr": float(latest["pbr"]) if latest and latest["pbr"] is not None else None,
                "current_per": float(latest["per"]) if latest and latest["per"] is not None else None,
                "current_base_date": latest["bas_dt"] if latest else "",
                "ref_a_price": ref_a_price,
                "ref_b_price": ref_b_price,
                "ref_c_price": ref_c_price,
                "ref_a_change_pct": pct_change(current_price, ref_a_price) or normalize_workbook_pct(item["ref_a_pct"]),
                "ref_b_change_pct": pct_change(current_price, ref_b_price) or normalize_workbook_pct(item["ref_b_pct"]),
                "ref_c_change_pct": pct_change(current_price, ref_c_price) or normalize_workbook_pct(item["ref_c_pct"]),
                "latest_revenue": latest_revenue,
                "latest_operating_profit": latest_operating_profit,
                "latest_net_income": latest_net_income,
                "annual_yoy": annual_yoy,
                "quarter_qoq": quarter_qoq,
                "last_quarter_label": last_quarter_label,
                "status_complete": item["status_complete"],
                "note": "",
                "watch_enabled": 1 if code in {"080220", "000990", "348350"} else 0,
                "watch_lv2": item["lv2"],
                "watch_customer": item["customer"],
            }
        )

    cache_conn.executemany(
        """
        INSERT INTO stock_cache (
            stock_code, stock_name, lv1, lv2, customer, main_business, ticker, etf_flag,
            workbook_price, workbook_change_pct, workbook_market_cap_okr, workbook_psr,
            current_price, current_market_cap, current_pbr, current_per, current_base_date,
            ref_a_price, ref_b_price, ref_c_price, ref_a_change_pct, ref_b_change_pct, ref_c_change_pct,
            latest_revenue, latest_operating_profit, latest_net_income, annual_yoy, quarter_qoq,
            last_quarter_label, status_complete, note, watch_enabled, watch_lv2, watch_customer
        ) VALUES (
            :stock_code, :stock_name, :lv1, :lv2, :customer, :main_business, :ticker, :etf_flag,
            :workbook_price, :workbook_change_pct, :workbook_market_cap_okr, :workbook_psr,
            :current_price, :current_market_cap, :current_pbr, :current_per, :current_base_date,
            :ref_a_price, :ref_b_price, :ref_c_price, :ref_a_change_pct, :ref_b_change_pct, :ref_c_change_pct,
            :latest_revenue, :latest_operating_profit, :latest_net_income, :annual_yoy, :quarter_qoq,
            :last_quarter_label, :status_complete, :note, :watch_enabled, :watch_lv2, :watch_customer
        )
        """,
        stock_insert_rows,
    )

    cache_conn.executemany(
        "INSERT INTO watchlist(stock_code, stock_name, lv2_override, customer_override, memo) VALUES (?, ?, '', '', '')",
        WATCHLIST_DEFAULTS,
    )

    stock_name_to_code = {row["stock_name"]: row["stock_code"] or row["stock_name"] for row in stock_insert_rows}

    yearly_rows = []
    for item in annual_items:
        year_value = parse_workbook_year(item["period"])
        if year_value is None:
            continue
        yearly_rows.append(
            (
                stock_name_to_code.get(item["stock_name"], item["stock_name"]),
                item["stock_name"],
                item["item_type"],
                year_value,
                item["value"],
            )
        )
    yearly_rows = dedupe_yearly_rows(yearly_rows)
    cache_conn.executemany(
        "INSERT INTO yearly_cache(stock_code, stock_name, item_type, fiscal_year, value) VALUES (?, ?, ?, ?, ?)",
        yearly_rows,
    )

    quarterly_rows_insert = []
    for item in quarterly_items:
        match = re.match(r"(\d{2})\.(\d)Q", item["period"])
        if not match:
            continue
        year = 2000 + int(match.group(1))
        quarter = int(match.group(2))
        quarterly_rows_insert.append(
            (
                stock_name_to_code.get(item["stock_name"], item["stock_name"]),
                item["stock_name"],
                item["item_type"],
                item["period"],
                year,
                quarter,
                item["value"],
            )
        )
    cache_conn.executemany(
        """
        INSERT INTO quarterly_cache(stock_code, stock_name, item_type, period_label, sort_year, sort_quarter, value)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        quarterly_rows_insert,
    )

    cache_conn.commit()
    export_csvs(cache_conn)
    root_conn.close()
    cache_conn.close()


def export_csvs(conn: sqlite3.Connection) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    exports = {
        EXPORT_DIR / "semiconductor_value_lab_sector_summary.csv": "SELECT * FROM sector_summary ORDER BY sector_order",
        EXPORT_DIR / "semiconductor_value_lab_stocks.csv": "SELECT * FROM stock_cache ORDER BY lv1, stock_name",
        EXPORT_DIR / "semiconductor_value_lab_watchlist.csv": """
            SELECT w.stock_code, w.stock_name, w.lv2_override, w.customer_override, w.memo,
                   s.lv1, s.lv2, s.customer, s.main_business
              FROM watchlist w
              LEFT JOIN stock_cache s ON s.stock_code = w.stock_code
             ORDER BY w.stock_name
        """,
    }
    for path, query in exports.items():
        rows = conn.execute(query).fetchall()
        with path.open("w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.writer(fp)
            if rows:
                writer.writerow(rows[0].keys())
                for row in rows:
                    writer.writerow(list(row))


if __name__ == "__main__":
    rebuild()
    print(f"rebuilt {DB_PATH}")
