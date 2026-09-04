#!/usr/bin/env python3
"""
Backfill DART elestock insider/major-holder specific securities reports.

The regular collector advances bgn_de to the latest stored rcept_dt, which is
right for daily collection but wrong for historical backfills. This script walks
date chunks from 2020 onward and stores every listed-company row with a corp_code
to stock_code map from CORPCODE.xml.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import sqlite3
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

import requests

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dart_key_manager import get_dart_api_keys

ROOT = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime")
DB_PATH = ROOT / "stock.db"
DART_BASE = "https://opendart.fss.or.kr/api"
PROGRESS_PATH = ROOT / "run" / "dart_insider_2020_backfill.json"
LOG_PATH = ROOT / "logs" / "dart_insider_2020_backfill.log"

logger = logging.getLogger("dart_insider_backfill")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding="utf-8")],
    )


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def _date_chunks(start: date, end: date, chunk_days: int) -> list[tuple[date, date]]:
    chunks: list[tuple[date, date]] = []
    cur = start
    while cur <= end:
        nxt = min(end, cur + timedelta(days=chunk_days - 1))
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return chunks


def _load_progress() -> dict:
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    return {"done_chunks": []}


def _save_progress(state: dict) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROGRESS_PATH)


def _to_number(value, default=None):
    if value is None:
        return default
    text = str(value).strip().replace(",", "")
    if not text or text in ("-", "None", "null"):
        return default
    negative = text.startswith("-") or text.startswith("△")
    text = text.replace("△", "").replace("+", "").replace("%", "")
    try:
        num = float(text)
    except Exception:
        return default
    if negative and num > 0:
        num = -num
    return num


def _corp_map(keys: list[str]) -> dict[str, tuple[str, str]]:
    cache = Path("/tmp/CORPCODE_map_insider.xml")
    if not cache.exists() or (datetime.now() - datetime.fromtimestamp(cache.stat().st_mtime)).days >= 7:
        key = keys[0]
        resp = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": key}, timeout=40)
        resp.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
        cache.write_bytes(zf.read("CORPCODE.xml"))

    tree = ET.parse(cache)
    out: dict[str, tuple[str, str]] = {}
    for item in tree.getroot().iter("list"):
        corp_code = (item.findtext("corp_code") or "").strip()
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_name = (item.findtext("corp_name") or "").strip()
        if corp_code and stock_code:
            out[corp_code] = (stock_code, corp_name)
    return out


class DartKeys:
    def __init__(self, keys: list[str]):
        if not keys:
            raise RuntimeError("DART API keys are not configured")
        self.keys = keys
        self.idx = 0
        self.exhausted: set[str] = set()

    def next(self) -> str | None:
        for _ in range(len(self.keys)):
            key = self.keys[self.idx % len(self.keys)]
            self.idx += 1
            if key not in self.exhausted:
                return key
        return None

    def exhaust(self, key: str) -> None:
        self.exhausted.add(key)
        logger.warning("DART key quota exhausted: ...%s", key[-4:])


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dart_insider_holdings (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            rcept_no          VARCHAR(20) NOT NULL,
            rcept_dt          DATE        NOT NULL,
            stock_code        VARCHAR(10) NOT NULL,
            corp_code         VARCHAR(20),
            corp_name         VARCHAR(200),
            repror            VARCHAR(200),
            isu_exctv_rgist   VARCHAR(50),
            isu_exctv_ofcps   VARCHAR(200),
            isu_main_shrholdr VARCHAR(100),
            sp_stock_lmp_cnt  INTEGER,
            sp_stock_lmp_irds_cnt  INTEGER,
            sp_stock_lmp_irds_rate REAL,
            is_ceo            INTEGER DEFAULT 0,
            is_significant    INTEGER DEFAULT 0,
            change_amount     REAL,
            raw_json          TEXT,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rcept_no, repror, sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_insider_code_dt ON dart_insider_holdings(stock_code, rcept_dt DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_insider_dt ON dart_insider_holdings(rcept_dt DESC)")


def _save_items(conn: sqlite3.Connection, items: list[dict], corp_to_stock: dict[str, tuple[str, str]]) -> int:
    saved = 0
    for item in items:
        corp_code = str(item.get("corp_code") or "").strip()
        stock_code, mapped_name = corp_to_stock.get(corp_code, ("", ""))
        if not stock_code:
            continue

        delta_qty = _to_number(item.get("sp_stock_lmp_irds_cnt"), 0)
        holding_qty = _to_number(item.get("sp_stock_lmp_cnt"), None)
        delta_rate = _to_number(item.get("sp_stock_lmp_irds_rate"), None)
        is_ceo = 1 if "대표" in str(item.get("isu_exctv_ofcps") or "") else 0
        is_significant = 1 if abs(delta_qty or 0) >= 10_000 or abs(delta_rate or 0) >= 0.1 else 0

        conn.execute(
            """
            INSERT OR REPLACE INTO dart_insider_holdings
            (rcept_no, rcept_dt, stock_code, corp_code, corp_name,
             repror, isu_exctv_rgist, isu_exctv_ofcps, isu_main_shrholdr,
             sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, sp_stock_lmp_irds_rate,
             is_ceo, is_significant, change_amount, raw_json, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            """,
            (
                item.get("rcept_no"),
                item.get("rcept_dt"),
                stock_code,
                corp_code,
                item.get("corp_name") or mapped_name,
                item.get("repror"),
                item.get("isu_exctv_rgist") or item.get("isu_exctv_rgist_at"),
                item.get("isu_exctv_ofcps"),
                item.get("isu_main_shrholdr"),
                holding_qty,
                delta_qty,
                delta_rate,
                is_ceo,
                is_significant,
                delta_qty,
                json.dumps(item, ensure_ascii=False),
            ),
        )
        saved += 1
    return saved


def collect_stock(
    conn: sqlite3.Connection,
    keys: DartKeys,
    stock_code: str,
    corp_code: str,
    since: date,
    sleep: float,
) -> tuple[int, int]:
    page = 1
    saved_total = 0
    fetched_total = 0

    while True:
        key = keys.next()
        if not key:
            raise RuntimeError("All DART API keys exhausted")
        resp = requests.get(
            f"{DART_BASE}/elestock.json",
            params={
                "crtfc_key": key,
                "corp_code": corp_code,
                "page_no": page,
                "page_count": 100,
            },
            timeout=25,
        )
        data = resp.json()
        status = str(data.get("status", ""))
        if status == "020":
            keys.exhaust(key)
            continue
        if status in ("013", "014"):
            return saved_total, fetched_total
        if status != "000":
            logger.debug("DART status=%s message=%s for %s", status, data.get("message"), stock_code)
            return saved_total, fetched_total

        items = [
            item for item in (data.get("list") or [])
            if item.get("rcept_dt") and datetime.strptime(item["rcept_dt"].replace("-", ""), "%Y%m%d").date() >= since
        ]
        fetched_total += len(items)
        saved_total += _save_items(conn, items, {corp_code: (stock_code, "")})
        conn.commit()

        total_count = int(data.get("total_count") or 0)
        if page * 100 >= total_count:
            break
        page += 1
        time.sleep(sleep)

    return saved_total, fetched_total


def _stock_targets(conn: sqlite3.Connection, corp_to_stock: dict[str, tuple[str, str]], limit: int = 0) -> list[tuple[str, str, str]]:
    stock_to_corp = {stock_code: corp_code for corp_code, (stock_code, _name) in corp_to_stock.items()}
    rows = conn.execute(
        """
        SELECT stock_code, stock_name
        FROM stock_universe
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND market IN ('KOSPI','KOSDAQ','KONEX','유가증권','코스닥','코넥스')
        ORDER BY COALESCE(market_cap, 0) DESC, stock_code
        """
    ).fetchall()
    targets = []
    seen = set()
    for row in rows:
        stock_code = row["stock_code"]
        corp_code = stock_to_corp.get(stock_code)
        if not corp_code or stock_code in seen:
            continue
        seen.add(stock_code)
        targets.append((stock_code, corp_code, row["stock_name"] or stock_code))
        if limit and len(targets) >= limit:
            break
    return targets


def normalize_existing_change_amount(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT id, sp_stock_lmp_irds_cnt, sp_stock_lmp_irds_rate, isu_exctv_ofcps
        FROM dart_insider_holdings
        """
    ).fetchall()
    updated = 0
    for row in rows:
        delta_qty = _to_number(row["sp_stock_lmp_irds_cnt"], 0)
        delta_rate = _to_number(row["sp_stock_lmp_irds_rate"], None)
        is_ceo = 1 if "대표" in str(row["isu_exctv_ofcps"] or "") else 0
        is_significant = 1 if abs(delta_qty or 0) >= 10_000 or abs(delta_rate or 0) >= 0.1 else 0
        conn.execute(
            "UPDATE dart_insider_holdings SET change_amount=?, is_ceo=?, is_significant=? WHERE id=?",
            (delta_qty, is_ceo, is_significant, row["id"]),
        )
        updated += 1
    conn.commit()
    return updated


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20200101")
    parser.add_argument("--end", default=date.today().strftime("%Y%m%d"))
    parser.add_argument("--chunk-days", type=int, default=31)
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--reset-progress", action="store_true")
    args = parser.parse_args()

    keys_list = get_dart_api_keys()
    keys = DartKeys(keys_list)
    corp_to_stock = _corp_map(keys_list)
    logger.info("corp_code map loaded: %d listed companies", len(corp_to_stock))

    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _ensure_table(conn)

    normalized = normalize_existing_change_amount(conn)
    logger.info("normalized existing insider rows: %d", normalized)

    state = {"done_stocks": []} if args.reset_progress else _load_progress()
    done = set(state.get("done_stocks") or [])
    since = _parse_date(args.start)
    targets = _stock_targets(conn, corp_to_stock, args.limit)
    logger.info("backfill stocks: %d listed targets since %s", len(targets), args.start)

    total_saved = total_fetched = 0
    for idx, (stock_code, corp_code, stock_name) in enumerate(targets, start=1):
        if stock_code in done:
            continue
        saved, fetched = collect_stock(conn, keys, stock_code, corp_code, since, args.sleep)
        total_saved += saved
        total_fetched += fetched
        done.add(stock_code)
        state.update({
            "start": args.start,
            "end": args.end,
            "last_stock": stock_code,
            "done_stocks": sorted(done),
            "total_saved_this_run": total_saved,
            "total_fetched_this_run": total_fetched,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        })
        _save_progress(state)
        logger.info("[%d/%d] %s %s fetched=%d saved=%d run_saved=%d", idx, len(targets), stock_code, stock_name, fetched, saved, total_saved)
        time.sleep(args.sleep)

    conn.close()
    logger.info("completed insider backfill: fetched=%d saved=%d", total_fetched, total_saved)


if __name__ == "__main__":
    main()
