from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

PROJECT_DIR = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_DIR / "static"
DB_PATH = PROJECT_DIR / "data" / "semiconductor_value_lab.db"
ROOT_STOCK_DB = PROJECT_DIR.parent.parent / "stock.db"
REBUILD_SCRIPT = PROJECT_DIR / "scripts" / "rebuild_cache.py"


def db_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def root_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(ROOT_STOCK_DB)
    conn.row_factory = sqlite3.Row
    return conn


def load_meta_map(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}


def load_index_change_pct(conn: sqlite3.Connection, stock_code: str) -> float | None:
    row = conn.execute(
        """
        SELECT change_pct
          FROM stock_price_daily
         WHERE stock_code = ?
         ORDER BY bas_dt DESC
         LIMIT 1
        """,
        (stock_code,),
    ).fetchone()
    return float(row["change_pct"]) if row and row["change_pct"] is not None else None


def load_latest_stock_rows(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    sql = """
    SELECT su.stock_code,
           su.stock_name,
           su.market_cap AS universe_market_cap,
           su.pbr AS universe_pbr,
           su.per AS universe_per,
           su.bps,
           sp.bas_dt,
           sp.close_price,
           sp.change_pct,
           sp.market_cap AS daily_market_cap
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


def pick_representative_stock(
    conn: sqlite3.Connection,
    latest_rows: dict[str, sqlite3.Row],
    lv1: str,
) -> tuple[str, str]:
    if lv1 == "종합":
        sql = "SELECT stock_code, stock_name, lv1, main_business FROM stock_cache"
        args: tuple = ()
    elif lv1:
        sql = "SELECT stock_code, stock_name, lv1, main_business FROM stock_cache WHERE lv1 = ?"
        args = (lv1,)
    else:
        return "-", "-"
    rows = [dict(row) for row in conn.execute(sql, args)]
    if not rows:
        return "-", "-"

    def sort_key(row: dict) -> tuple[float, float, str]:
        live = latest_rows.get(row["stock_code"])
        if live:
            raw = live["daily_market_cap"] if live["daily_market_cap"] is not None else live["universe_market_cap"]
            market_cap = float(raw) if raw is not None else 0.0
            close_price = float(live["close_price"]) if live["close_price"] is not None else 0.0
        else:
            market_cap = 0.0
            close_price = 0.0
        return (market_cap, close_price, row["stock_name"])

    picked = max(rows, key=sort_key)
    return picked["stock_name"], picked.get("main_business") or "-"


def load_fallback_prices(conn: sqlite3.Connection, codes: list[str]) -> dict[str, dict]:
    """stock_price_daily에 없는 종목을 price_history에서 보완."""
    if not codes:
        return {}
    ph = ",".join("?" for _ in codes)
    try:
        rows = conn.execute(f"""
            WITH latest AS (
                SELECT stock_code, MAX(date) AS d
                  FROM price_history
                 WHERE stock_code IN ({ph}) AND close > 0
                 GROUP BY stock_code
            )
            SELECT p.stock_code, p.close AS close_price, p.date AS bas_dt
              FROM price_history p
              JOIN latest l ON p.stock_code = l.stock_code AND p.date = l.d
        """, codes).fetchall()
        return {r["stock_code"]: dict(r) for r in rows}
    except Exception:
        return {}


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
    if row:
        return float(row["close_price"])
    # Fallback: price_history (YYYYMMDD → YYYY-MM-DD)
    iso = f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}" if len(yyyymmdd) == 8 else yyyymmdd
    row = conn.execute(
        "SELECT close FROM price_history WHERE stock_code=? AND date<=? AND close>0 ORDER BY date DESC LIMIT 1",
        (stock_code, iso),
    ).fetchone()
    return float(row["close"]) if row else None


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


def enrich_rows_with_live_prices(rows: list[dict], meta_map: dict[str, str]) -> list[dict]:
    refs = {
        "ref_a": meta_map.get("ref_a", ""),
        "ref_b": meta_map.get("ref_b", ""),
        "ref_c": meta_map.get("ref_c", ""),
    }
    rc = root_conn()
    try:
        latest = load_latest_stock_rows(rc)
        # Fallback: price_history for stocks missing from stock_price_daily
        missing = [r.get("stock_code") for r in rows if r.get("stock_code") and r.get("stock_code") not in latest]
        fallback = load_fallback_prices(rc, missing) if missing else {}
        enriched: list[dict] = []
        for row in rows:
            code = row.get("stock_code", "")
            live = (latest.get(code) or fallback.get(code)) if code else None
            current_price = float(live["close_price"]) if live and live.get("close_price") is not None else row.get("current_price")
            ref_a_price = get_price_on_or_before(rc, code, refs["ref_a"]) if code else None
            ref_b_price = get_price_on_or_before(rc, code, refs["ref_b"]) if code else None
            ref_c_price = get_price_on_or_before(rc, code, refs["ref_c"]) if code else None
            payload = dict(row)
            ref_a_pct = pct_change(current_price, ref_a_price)
            ref_b_pct = pct_change(current_price, ref_b_price)
            ref_c_pct = pct_change(current_price, ref_c_price)
            live_market_cap = None
            if live:
                live_market_cap = live["daily_market_cap"] if live["daily_market_cap"] is not None else live["universe_market_cap"]

            live_pbr = None
            if live:
                if live["universe_pbr"] is not None:
                    live_pbr = float(live["universe_pbr"])
                elif current_price is not None and live["bps"] not in (None, 0):
                    live_pbr = round(float(current_price) / float(live["bps"]), 2)

            live_per = None
            if live and live["universe_per"] is not None:
                live_per = float(live["universe_per"])

            payload.update(
                {
                    "current_price": current_price,
                    "price_change_pct": float(live["change_pct"]) if live and live["change_pct"] is not None else row.get("price_change_pct"),
                    "current_market_cap": float(live_market_cap) if live_market_cap is not None else row.get("current_market_cap"),
                    "market_cap": float(live_market_cap) if live_market_cap is not None else row.get("current_market_cap"),
                    "current_pbr": live_pbr if live_pbr is not None else row.get("current_pbr"),
                    "pbr": live_pbr if live_pbr is not None else row.get("current_pbr"),
                    "current_per": live_per if live_per is not None else row.get("current_per"),
                    "per": live_per if live_per is not None else row.get("current_per"),
                    "psr": row.get("workbook_psr"),
                    "current_base_date": live["bas_dt"] if live else row.get("current_base_date", ""),
                    "ref_a_price": ref_a_price if ref_a_price is not None else row.get("ref_a_price"),
                    "ref_b_price": ref_b_price if ref_b_price is not None else row.get("ref_b_price"),
                    "ref_c_price": ref_c_price if ref_c_price is not None else row.get("ref_c_price"),
                    "ref_a_change_pct": ref_a_pct if ref_a_pct is not None else normalize_workbook_pct(row.get("ref_a_change_pct")),
                    "ref_b_change_pct": ref_b_pct if ref_b_pct is not None else normalize_workbook_pct(row.get("ref_b_change_pct")),
                    "ref_c_change_pct": ref_c_pct if ref_c_pct is not None else normalize_workbook_pct(row.get("ref_c_change_pct")),
                }
            )
            enriched.append(payload)
        return enriched
    finally:
        rc.close()


def resolve_sector_key(row: dict, available_lv1: set[str]) -> str:
    sector_name = (row.get("sector_name") or "").strip()
    sample_lv1 = (row.get("sample_lv1") or "").strip()
    if sector_name == "종합":
        return "종합"
    if sector_name in available_lv1:
        return sector_name
    if sample_lv1 in available_lv1:
        return sample_lv1
    return sector_name or sample_lv1 or "기타"


def build_summary_rows(conn: sqlite3.Connection) -> list[dict]:
    summary_rows = [dict(row) for row in conn.execute("SELECT * FROM sector_summary ORDER BY sector_order")]
    rc = root_conn()
    try:
        latest_rows = load_latest_stock_rows(rc)
        kospi_chg = load_index_change_pct(rc, "^KS11")
        nasdaq_chg = load_index_change_pct(rc, "^IXIC")
    finally:
        rc.close()

    # Load all stocks with their sector (lv1) and PSR for dynamic aggregation
    all_stocks = [
        dict(row)
        for row in conn.execute(
            "SELECT stock_code, stock_name, lv1, workbook_psr, main_business FROM stock_cache"
        )
    ]
    stocks_by_lv1: dict[str, list[dict]] = {}
    for s in all_stocks:
        lv1 = (s.get("lv1") or "").strip()
        stocks_by_lv1.setdefault(lv1, []).append(s)
    available_lv1 = set(stocks_by_lv1.keys())

    rows: list[dict] = []
    for row in summary_rows:
        sector_key = resolve_sector_key(row, available_lv1)
        sector_name = row.get("sector_name") or row.get("sample_lv1") or "-"

        # Aggregate stocks for this sector
        sector_stocks = all_stocks if sector_key == "종합" else stocks_by_lv1.get(sector_key, [])

        # Dynamically compute stock_count, rising_count, rising_ratio
        stock_count = len(sector_stocks)
        rising_count = 0
        for s in sector_stocks:
            live = latest_rows.get(s["stock_code"])
            if live and live["change_pct"] is not None and float(live["change_pct"]) > 0:
                rising_count += 1
        rising_ratio = round(rising_count / stock_count, 4) if stock_count > 0 else row.get("rising_ratio")

        # Dynamically compute avg_psr from workbook data
        psrs = [s["workbook_psr"] for s in sector_stocks if s.get("workbook_psr") is not None]
        avg_psr = round(sum(psrs) / len(psrs), 2) if psrs else row.get("avg_psr")

        # Dynamically compute outperform/underperform vs KOSPI and NASDAQ
        sector_changes = [
            float(latest_rows[s["stock_code"]]["change_pct"])
            for s in sector_stocks
            if s["stock_code"] in latest_rows and latest_rows[s["stock_code"]]["change_pct"] is not None
        ]
        sector_avg_chg = sum(sector_changes) / len(sector_changes) if sector_changes else None
        if sector_avg_chg is not None and kospi_chg is not None:
            kospi_vs = "아웃퍼폼" if sector_avg_chg > kospi_chg else "언더퍼폼"
        else:
            kospi_vs = row.get("kospi_vs") or ""
        if sector_avg_chg is not None and nasdaq_chg is not None:
            nasdaq_vs = "아웃퍼폼" if sector_avg_chg > nasdaq_chg else "언더퍼폼"
        else:
            nasdaq_vs = row.get("nasdaq_vs") or ""

        # Pick representative stock by live market cap for all sectors
        rep_name, rep_industry = pick_representative_stock(conn, latest_rows, sector_key)

        rows.append(
            {
                **row,
                "sector_name": sector_name,
                "resolved_sector_key": sector_key if sector_key != "기타" else (sector_name if sector_name != "-" else "기타"),
                "stock_count": stock_count if stock_count > 0 else row.get("stock_count"),
                "rising_count": rising_count if stock_count > 0 else row.get("rising_count"),
                "rising_ratio": rising_ratio,
                "avg_psr": avg_psr,
                "kospi_vs": kospi_vs,
                "nasdaq_vs": nasdaq_vs,
                "sample_stock": rep_name if rep_name != "-" else (row.get("sample_stock") or "-"),
                "sample_industry": rep_industry if rep_industry != "-" else (row.get("sample_industry") or "-"),
            }
        )
    return rows


def build_stock_rows(conn: sqlite3.Connection, lv1: str = "", watch_only: bool = False) -> list[dict]:
    meta_map = load_meta_map(conn)
    sql = "SELECT * FROM stock_cache WHERE 1=1"
    args: list[object] = []
    if lv1:
        sql += " AND lv1 = ?"
        args.append(lv1)
    if watch_only:
        sql += " AND stock_code IN (SELECT stock_code FROM watchlist)"
    sql += " ORDER BY lv1, stock_name"
    rows = [dict(row) for row in conn.execute(sql, args)]
    return enrich_rows_with_live_prices(rows, meta_map)


def build_watchlist_rows(conn: sqlite3.Connection) -> list[dict]:
    meta_map = load_meta_map(conn)
    rows = [
        dict(row)
        for row in conn.execute(
            """
            SELECT w.stock_code, w.stock_name, w.lv2_override, w.customer_override, w.memo,
                   s.lv1, s.lv2, s.customer, s.main_business, s.current_price,
                   s.ref_a_change_pct, s.ref_b_change_pct, s.ref_c_change_pct,
                   s.annual_yoy, s.quarter_qoq
              FROM watchlist w
              LEFT JOIN stock_cache s ON s.stock_code = w.stock_code
             ORDER BY w.stock_name
            """
        )
    ]
    return enrich_rows_with_live_prices(rows, meta_map)


class WatchlistPayload(BaseModel):
    stock_code: str
    stock_name: str
    lv2_override: str = ""
    customer_override: str = ""
    memo: str = ""


class ReferenceDatesPayload(BaseModel):
    ref_a: str | None = None
    ref_b: str | None = None
    ref_c: str | None = None


def _ensure_schema() -> None:
    """DB 파일이 없거나 테이블이 없을 때만 CREATE TABLE IF NOT EXISTS로 초기화."""
    try:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        conn.executescript("""
            PRAGMA journal_mode=WAL;

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sector_summary (
                sector_order            INTEGER PRIMARY KEY,
                sector_name             TEXT NOT NULL,
                stock_count             INTEGER,
                rising_count            INTEGER,
                rising_ratio            REAL,
                avg_psr                 REAL,
                kospi_vs                TEXT,
                nasdaq_vs               TEXT,
                sample_stock            TEXT,
                sample_lv1              TEXT,
                sample_industry         TEXT,
                sample_market_cap_okr   REAL,
                sample_price            REAL
            );

            CREATE TABLE IF NOT EXISTS stock_cache (
                stock_code              TEXT PRIMARY KEY,
                stock_name              TEXT NOT NULL,
                lv1                     TEXT,
                lv2                     TEXT,
                customer                TEXT,
                main_business           TEXT,
                ticker                  TEXT,
                etf_flag                TEXT,
                workbook_price          REAL,
                workbook_change_pct     REAL,
                workbook_market_cap_okr REAL,
                workbook_psr            REAL,
                current_price           REAL,
                current_market_cap      REAL,
                current_pbr             REAL,
                current_per             REAL,
                current_base_date       TEXT,
                ref_a_price             REAL,
                ref_b_price             REAL,
                ref_c_price             REAL,
                ref_a_change_pct        REAL,
                ref_b_change_pct        REAL,
                ref_c_change_pct        REAL,
                latest_revenue          REAL,
                latest_operating_profit REAL,
                latest_net_income       REAL,
                annual_yoy              REAL,
                quarter_qoq             REAL,
                last_quarter_label      TEXT,
                status_complete         TEXT,
                note                    TEXT,
                watch_enabled           INTEGER DEFAULT 0,
                watch_lv2               TEXT,
                watch_customer          TEXT
            );

            CREATE TABLE IF NOT EXISTS yearly_cache (
                stock_code  TEXT NOT NULL,
                stock_name  TEXT NOT NULL,
                item_type   TEXT NOT NULL,
                fiscal_year INTEGER NOT NULL,
                value       REAL,
                PRIMARY KEY (stock_code, item_type, fiscal_year)
            );

            CREATE TABLE IF NOT EXISTS quarterly_cache (
                stock_code   TEXT NOT NULL,
                stock_name   TEXT NOT NULL,
                item_type    TEXT NOT NULL,
                period_label TEXT NOT NULL,
                sort_year    INTEGER NOT NULL,
                sort_quarter INTEGER NOT NULL,
                value        REAL,
                PRIMARY KEY (stock_code, item_type, period_label)
            );

            CREATE TABLE IF NOT EXISTS watchlist (
                stock_code        TEXT PRIMARY KEY,
                stock_name        TEXT NOT NULL,
                lv2_override      TEXT,
                customer_override TEXT,
                memo              TEXT DEFAULT ''
            );
        """)
        # 기본 watchlist 시드 (최초 1회)
        for code, name in (("080220", "제주반도체"), ("000990", "DB하이텍"), ("348350", "위드텍")):
            conn.execute(
                "INSERT OR IGNORE INTO watchlist(stock_code, stock_name) VALUES (?,?)",
                (code, name),
            )
        conn.commit()
        conn.close()
        logger.info("[semiconductor-lab] DB 스키마 초기화 완료")
    except Exception as e:
        logger.warning(f"[semiconductor-lab] DB 스키마 초기화 실패: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_schema()
    yield


app = FastAPI(title="Semiconductor Value Lab", lifespan=lifespan)


STATIC_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}


@app.get("/api/health")
def health():
    return {"message": "ok"}


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html", headers=STATIC_HEADERS)


@app.get("/styles.css")
def styles():
    return FileResponse(STATIC_DIR / "styles.css", headers=STATIC_HEADERS)


@app.get("/app.js")
def app_js():
    return FileResponse(STATIC_DIR / "app.js", headers=STATIC_HEADERS)


@app.get("/api/meta")
def meta():
    conn = db_conn()
    try:
        payload = load_meta_map(conn)
        return payload
    finally:
        conn.close()


@app.get("/api/summary")
def summary():
    conn = db_conn()
    try:
        return {"items": build_summary_rows(conn)}
    except Exception as e:
        logger.warning(f"[/api/summary] {e}")
        return {"items": []}
    finally:
        conn.close()


@app.get("/api/stocks")
def stocks(lv1: str = "", watch_only: bool = False):
    conn = db_conn()
    try:
        return {"items": build_stock_rows(conn, lv1=lv1, watch_only=watch_only)}
    except Exception as e:
        logger.warning(f"[/api/stocks] {e}")
        return {"items": []}
    finally:
        conn.close()


@app.get("/api/watchlist")
def watchlist():
    conn = db_conn()
    try:
        return {"items": build_watchlist_rows(conn)}
    except Exception as e:
        logger.warning(f"[/api/watchlist] {e}")
        return {"items": []}
    finally:
        conn.close()


@app.get("/api/bootstrap")
def bootstrap():
    conn = db_conn()
    try:
        try:
            meta_data = load_meta_map(conn)
        except Exception:
            meta_data = {}
        try:
            summary_data = {"items": build_summary_rows(conn)}
        except Exception as e:
            logger.warning(f"[bootstrap/summary] {e}")
            summary_data = {"items": []}
        try:
            stocks_data = {"items": build_stock_rows(conn)}
        except Exception as e:
            logger.warning(f"[bootstrap/stocks] {e}")
            stocks_data = {"items": []}
        try:
            watchlist_data = {"items": build_watchlist_rows(conn)}
        except Exception as e:
            logger.warning(f"[bootstrap/watchlist] {e}")
            watchlist_data = {"items": []}
        return {
            "meta": meta_data,
            "summary": summary_data,
            "stocks": stocks_data,
            "watchlist": watchlist_data,
        }
    finally:
        conn.close()


@app.get("/api/stock/{stock_code}")
def stock_detail(stock_code: str):
    conn = db_conn()
    try:
        stock = conn.execute("SELECT * FROM stock_cache WHERE stock_code = ?", (stock_code,)).fetchone()
        if not stock:
            raise HTTPException(status_code=404, detail="not found")
        yearly = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM yearly_cache WHERE stock_code = ? ORDER BY fiscal_year, item_type",
                (stock_code,),
            )
        ]
        quarterly = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM quarterly_cache WHERE stock_code = ? ORDER BY sort_year, sort_quarter, item_type",
                (stock_code,),
            )
        ]
        return {"stock": dict(stock), "yearly": yearly, "quarterly": quarterly}
    finally:
        conn.close()


@app.post("/api/watchlist")
def save_watchlist(payload: WatchlistPayload):
    conn = db_conn()
    try:
        conn.execute(
            """
            INSERT INTO watchlist(stock_code, stock_name, lv2_override, customer_override, memo)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(stock_code) DO UPDATE SET
                stock_name = excluded.stock_name,
                lv2_override = excluded.lv2_override,
                customer_override = excluded.customer_override,
                memo = excluded.memo
            """,
            (
                payload.stock_code,
                payload.stock_name,
                payload.lv2_override,
                payload.customer_override,
                payload.memo,
            ),
        )
        conn.commit()
        return {"message": "saved"}
    finally:
        conn.close()


@app.delete("/api/watchlist/{stock_code}")
def delete_watchlist(stock_code: str):
    conn = db_conn()
    try:
        conn.execute("DELETE FROM watchlist WHERE stock_code = ?", (stock_code,))
        conn.commit()
        return {"message": "deleted"}
    finally:
        conn.close()


@app.post("/api/reference-dates")
def update_reference_dates(payload: ReferenceDatesPayload):
    conn = db_conn()
    try:
        for key in ("ref_a", "ref_b", "ref_c"):
            value = getattr(payload, key)
            if value:
                conn.execute(
                    "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
        conn.commit()
        return {"message": "saved", "note": "기준일 저장 완료. 재계산은 재빌드 버튼으로 실행하세요."}
    finally:
        conn.close()


@app.post("/api/rebuild")
def rebuild_cache():
    result = subprocess.run(
        ["python3", str(REBUILD_SCRIPT)],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return JSONResponse(
            status_code=500,
            content={"message": "rebuild failed", "stderr": result.stderr[-4000:], "stdout": result.stdout[-2000:]},
        )
    return {"message": "rebuilt", "stdout": result.stdout[-2000:]}
