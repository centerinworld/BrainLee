#!/usr/bin/env python3
"""
Backfill 2020~2023 insider holdings from DART disclosure documents.

OpenDART elestock currently returns recent rows only for many companies, while
dart_disclosures already contains historical "임원ㆍ주요주주특정증권등소유상황보고서"
filings. This parser fetches document.xml and extracts the detail-change table.
"""

from __future__ import annotations

import argparse
import html
import io
import json
import logging
import re
import sqlite3
import time
import warnings
import zipfile
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from bs4 import XMLParsedAsHTMLWarning

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dart_key_manager import get_dart_api_keys

ROOT = Path("/Applications/stock_dashboard")
DB_PATH = ROOT / "stock.db"
PROGRESS_PATH = ROOT / "run" / "dart_insider_doc_backfill_2020_2023.json"
LOG_PATH = ROOT / "logs" / "dart_insider_doc_backfill_2020_2023.log"
DART_BASE = "https://opendart.fss.or.kr/api"

logger = logging.getLogger("dart_insider_doc_backfill")
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding="utf-8")],
    )


def load_progress() -> dict:
    if PROGRESS_PATH.exists():
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    return {"done": [], "failed": []}


def save_progress(state: dict) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROGRESS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROGRESS_PATH)


def num(value, default=None):
    if value is None:
        return default
    text = str(value).strip().replace(",", "").replace(" ", "")
    if not text or text == "-":
        return default
    negative = text.startswith("-") or text.startswith("△")
    text = text.replace("△", "").replace("+", "").replace("%", "")
    m = re.search(r"\d+(?:\.\d+)?", text)
    if not m:
        return default
    out = float(m.group(0))
    return -out if negative else out


class Keys:
    def __init__(self):
        self.keys = get_dart_api_keys()
        if not self.keys:
            raise RuntimeError("DART API keys are not configured")
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
        logger.warning("quota exhausted: ...%s", key[-4:])


def fetch_doc(rcept_no: str, keys: Keys) -> str:
    while True:
        key = keys.next()
        if not key:
            raise RuntimeError("All DART API keys exhausted")
        resp = requests.get(f"{DART_BASE}/document.xml", params={"crtfc_key": key, "rcept_no": rcept_no}, timeout=35)
        raw = resp.content
        if raw[:1] == b"{":
            try:
                data = resp.json()
            except Exception:
                data = {}
            if str(data.get("status")) == "020":
                keys.exhaust(key)
                continue
            return ""
        if raw[:2] == b"PK":
            try:
                zf = zipfile.ZipFile(io.BytesIO(raw))
                chunks = []
                for name in zf.namelist():
                    chunks.append(decode_bytes(zf.read(name)))
                return "\n".join(chunks)
            except Exception as exc:
                logger.debug("zip parse failed %s: %s", rcept_no, exc)
                return ""
        return decode_bytes(raw)


def decode_bytes(data: bytes) -> str:
    for enc in ("utf-8", "euc-kr", "cp949"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return data.decode("utf-8", errors="ignore")


def fetch_viewer_doc(rcept_no: str) -> str:
    sess = requests.Session()
    main = sess.get(f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}", timeout=35).text
    m = re.search(r"viewDoc\(\"%s\",\s*\"(\d+)\",\s*\"(\d+)\",\s*\"(\d+)\",\s*\"(\d+)\",\s*\"([^\"]+)\"" % re.escape(rcept_no), main)
    if not m:
        m = re.search(r"node1\['dcmNo'\]\s*=\s*\"(\d+)\";.*?node1\['eleId'\]\s*=\s*\"(\d+)\"", main, re.S)
        if not m:
            return ""
        dcm_no, ele_id = m.group(1), m.group(2)
        offset, length, dtd = "0", "0", "dart3.xsd"
    else:
        dcm_no, ele_id, offset, length, dtd = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
    viewer = sess.get(
        "https://dart.fss.or.kr/report/viewer.do",
        params={"rcpNo": rcept_no, "dcmNo": dcm_no, "eleId": ele_id, "offset": offset, "length": length, "dtd": dtd},
        timeout=35,
    ).text
    return html.unescape(viewer)


def cells_from_row(row) -> list[str]:
    vals = []
    for c in row.find_all(["TD", "TU", "TH", "td", "tu", "th"], recursive=False):
        vals.append(re.sub(r"\s+", " ", c.get_text(" ", strip=True)).strip())
    return vals


def extract_profile(text: str) -> tuple[str, str, str]:
    repror = ""
    pos = ""
    main_holder = ""
    m = re.search(r"보고자\s*:\s*([^\n]+)", text)
    if m:
        repror = m.group(1).strip()
    if not repror:
        m = re.search(r"성명\(명칭\)\s*한\s*글\s*([^\n]+)", text)
        if m:
            repror = m.group(1).strip()
    m = re.search(r"직위명\s*([^\n]+)", text)
    if m:
        pos = m.group(1).strip()
    m = re.search(r"주요주주\s*([^\n]+)", text)
    if m:
        main_holder = m.group(1).strip()
    return repror[:200], pos[:200], main_holder[:100]


def parse_doc(xml: str) -> dict | None:
    if not xml:
        return None
    soup = BeautifulSoup(xml, "html.parser")
    text = soup.get_text("\n")
    repror, pos, main_holder = extract_profile(text)

    best = None
    for table in soup.find_all(["TABLE", "table"]):
        table_text = table.get_text(" ", strip=True)
        if "세부변동내역" not in table_text and "보고사유" not in table_text:
            continue
        for row in table.find_all(["TR", "tr"]):
            cells = [c for c in cells_from_row(row) if c != ""]
            if len(cells) < 4:
                continue
            joined = " ".join(cells)
            if "합" in joined and "계" in joined:
                nums = [num(c) for c in cells]
                nums = [n for n in nums if n is not None]
                if len(nums) >= 2:
                    best = {"delta": nums[-2], "holding": nums[-1], "reason": "합계"}
            elif re.search(r"\d{4}년|\d{4}-\d{2}-\d{2}", joined) and any(kind in joined for kind in ("보통주", "주권", "우선주")):
                # Typical row: reason, date, security type, before, delta, after, price, note
                if len(cells) >= 6:
                    delta = num(cells[4])
                    holding = num(cells[5])
                    if delta is not None:
                        best = best or {"delta": delta, "holding": holding, "reason": cells[0]}

    if not best:
        m = re.search(r"증\s*감\s*\n\s*([+-]?\d[\d,]*)", text)
        if m:
            best = {"delta": num(m.group(1)), "holding": None, "reason": "regex"}
    if not best or best.get("delta") is None:
        return None

    return {
        "repror": repror,
        "position": pos,
        "main_holder": main_holder,
        "delta": best.get("delta"),
        "holding": best.get("holding"),
        "reason": best.get("reason"),
        "is_ceo": 1 if "대표" in pos else 0,
    }


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dart_insider_holdings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rcept_no VARCHAR(20) NOT NULL,
            rcept_dt DATE NOT NULL,
            stock_code VARCHAR(10) NOT NULL,
            corp_code VARCHAR(20),
            corp_name VARCHAR(200),
            repror VARCHAR(200),
            isu_exctv_rgist VARCHAR(50),
            isu_exctv_ofcps VARCHAR(200),
            isu_main_shrholdr VARCHAR(100),
            sp_stock_lmp_cnt INTEGER,
            sp_stock_lmp_irds_cnt INTEGER,
            sp_stock_lmp_irds_rate REAL,
            is_ceo INTEGER DEFAULT 0,
            is_significant INTEGER DEFAULT 0,
            change_amount REAL,
            raw_json TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(rcept_no, repror, sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_insider_code_dt ON dart_insider_holdings(stock_code, rcept_dt DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_insider_dt ON dart_insider_holdings(rcept_dt DESC)")
    conn.commit()


def load_targets(conn: sqlite3.Connection, start: str, end: str, limit: int) -> list[sqlite3.Row]:
    sql = """
        SELECT d.rcept_no, d.rcept_dt, d.stock_code, NULL AS corp_code, d.corp_name, d.report_nm
        FROM dart_disclosures d
        LEFT JOIN dart_insider_holdings h ON h.rcept_no = d.rcept_no
        WHERE h.rcept_no IS NULL
          AND d.rcept_dt BETWEEN ? AND ?
          AND d.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND d.report_nm LIKE '%특정증권%'
        ORDER BY d.rcept_dt, d.rcept_no
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, (start, end)).fetchall()


def save_row(conn: sqlite3.Connection, row: sqlite3.Row, parsed: dict) -> None:
    delta = parsed["delta"]
    holding = parsed.get("holding")
    is_sig = 1 if abs(delta or 0) >= 10_000 else 0
    params = (
        row["rcept_no"], row["rcept_dt"], row["stock_code"], row["corp_code"], row["corp_name"],
        parsed.get("repror") or row["corp_name"],
        None, parsed.get("position"), parsed.get("main_holder"),
        holding, delta, None,
        parsed.get("is_ceo", 0), is_sig, delta,
        json.dumps({"source": "document.xml", "reason": parsed.get("reason"), "report_nm": row["report_nm"]}, ensure_ascii=False),
    )
    for attempt in range(8):
        try:
            conn.execute(
                """
                INSERT OR REPLACE INTO dart_insider_holdings
                (rcept_no, rcept_dt, stock_code, corp_code, corp_name,
                 repror, isu_exctv_rgist, isu_exctv_ofcps, isu_main_shrholdr,
                 sp_stock_lmp_cnt, sp_stock_lmp_irds_cnt, sp_stock_lmp_irds_rate,
                 is_ceo, is_significant, change_amount, raw_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
                """,
                params,
            )
            return
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or attempt == 7:
                raise
            time.sleep(1.5 * (attempt + 1))


def main() -> None:
    setup_logging()
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2023-12-31")
    ap.add_argument("--sleep", type=float, default=0.12)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--reset-progress", action="store_true")
    args = ap.parse_args()

    conn = sqlite3.connect(DB_PATH, timeout=180)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=180000")
    ensure_table(conn)
    keys = Keys()
    state = {"done": [], "failed": []} if args.reset_progress else load_progress()
    done = set(state.get("done") or [])
    failed = set(state.get("failed") or [])
    targets = load_targets(conn, args.start, args.end, args.limit)
    logger.info("targets=%d range=%s~%s done=%d", len(targets), args.start, args.end, len(done))

    saved = parsed_fail = fetch_fail = 0
    for i, row in enumerate(targets, start=1):
        rno = row["rcept_no"]
        if rno in done:
            continue
        try:
            xml = fetch_doc(rno, keys)
        except Exception as exc:
            logger.warning("fetch failed %s: %s", rno, exc)
            failed.add(rno)
            fetch_fail += 1
            continue
        parsed = parse_doc(xml)
        if not parsed:
            try:
                parsed = parse_doc(fetch_viewer_doc(rno))
            except Exception as exc:
                logger.debug("viewer fallback failed %s: %s", rno, exc)
        if not parsed:
            parsed_fail += 1
            failed.add(rno)
        else:
            save_row(conn, row, parsed)
            saved += 1
        done.add(rno)
        if i % 25 == 0:
            conn.commit()
            state.update({"done": sorted(done), "failed": sorted(failed), "last": rno, "saved": saved, "parsed_fail": parsed_fail, "fetch_fail": fetch_fail, "updated_at": datetime.now().isoformat(timespec="seconds")})
            save_progress(state)
            logger.info("[%d/%d] saved=%d parse_fail=%d fetch_fail=%d last=%s", i, len(targets), saved, parsed_fail, fetch_fail, rno)
        time.sleep(args.sleep)

    conn.commit()
    state.update({"done": sorted(done), "failed": sorted(failed), "saved": saved, "parsed_fail": parsed_fail, "fetch_fail": fetch_fail, "updated_at": datetime.now().isoformat(timespec="seconds"), "complete": True})
    save_progress(state)
    conn.close()
    logger.info("complete saved=%d parse_fail=%d fetch_fail=%d", saved, parsed_fail, fetch_fail)


if __name__ == "__main__":
    main()
