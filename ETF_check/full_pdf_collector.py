"""Collect complete ETF Portfolio Deposit Files from KRX.

The KRX PDF is a Portfolio Deposit File data table, not a document file.  Raw
JSON responses are retained as gzip files and normalized without discarding
cash, futures, overseas assets, or non-six-digit component identifiers.

A date becomes queryable as a complete snapshot only when every active ETF has
a non-empty successful response.  Partial runs remain auditable but can never
prove that a stock is not held by an ETF.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(__file__).with_name("etf_check.db")
RAW_ROOT = Path(__file__).with_name("raw_pdf")
BLD_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
BLD_ID = "dbms/MDC/STAT/standard/MDCSTAT05001"
LOGIN_URL = "https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd"
REFERER = (
    "https://data.krx.co.kr/contents/MDC/MDI/mdiLoader/"
    "index.cmd?menuId=MDC0201020103"
)
LOG = logging.getLogger("etf_full_pdf")


def number(value: Any) -> float | None:
    text = str(value or "").replace(",", "").replace("%", "").strip()
    if text in {"", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def load_env(path: Path = ROOT / ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def initialize(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        PRAGMA busy_timeout=120000;

        CREATE TABLE IF NOT EXISTS etf_pdf_full_run (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT,
            base_date TEXT NOT NULL,
            status TEXT NOT NULL,
            universe_count INTEGER NOT NULL,
            success_count INTEGER NOT NULL DEFAULT 0,
            empty_count INTEGER NOT NULL DEFAULT 0,
            error_count INTEGER NOT NULL DEFAULT 0,
            fetched_count INTEGER NOT NULL DEFAULT 0,
            skipped_count INTEGER NOT NULL DEFAULT 0,
            is_complete INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS etf_pdf_full_snapshot (
            base_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            etf_name TEXT NOT NULL,
            isin TEXT NOT NULL,
            status TEXT NOT NULL,
            component_count INTEGER NOT NULL DEFAULT 0,
            domestic_stock_count INTEGER NOT NULL DEFAULT 0,
            weight_sum REAL,
            raw_path TEXT,
            raw_sha256 TEXT,
            source TEXT NOT NULL DEFAULT 'KRX_MDCSTAT05001',
            error TEXT,
            collected_at TEXT NOT NULL,
            PRIMARY KEY(base_date, etf_ticker)
        );

        CREATE INDEX IF NOT EXISTS idx_pdf_full_snapshot_date_status
            ON etf_pdf_full_snapshot(base_date, status);

        CREATE TABLE IF NOT EXISTS etf_pdf_full_component (
            base_date TEXT NOT NULL,
            etf_ticker TEXT NOT NULL,
            component_order INTEGER NOT NULL,
            component_code TEXT NOT NULL,
            component_name TEXT NOT NULL,
            shares_per_cu REAL,
            valuation_amount REAL,
            component_amount REAL,
            weight REAL,
            is_domestic_stock INTEGER NOT NULL,
            raw_json TEXT NOT NULL,
            PRIMARY KEY(base_date, etf_ticker, component_order),
            FOREIGN KEY(base_date, etf_ticker)
                REFERENCES etf_pdf_full_snapshot(base_date, etf_ticker)
                ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_pdf_full_component_stock
            ON etf_pdf_full_component(base_date, component_code);
        CREATE INDEX IF NOT EXISTS idx_pdf_full_component_etf
            ON etf_pdf_full_component(etf_ticker, base_date);

        CREATE TABLE IF NOT EXISTS etf_pdf_full_publication (
            base_date TEXT PRIMARY KEY,
            universe_count INTEGER NOT NULL,
            snapshot_count INTEGER NOT NULL,
            component_count INTEGER NOT NULL,
            published_at TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'KRX_MDCSTAT05001'
        );
        """
    )


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=120)
    conn.row_factory = sqlite3.Row
    initialize(conn)
    return conn


@dataclass(frozen=True)
class ETF:
    ticker: str
    name: str
    isin: str


class KRXUnavailable(RuntimeError):
    pass


class KRXSession:
    def __init__(self, username: str, password: str, headless: bool = True):
        self.username = username
        self.password = password
        self.headless = headless
        self._playwright = None
        self.browser = None
        self.context = None

    def __enter__(self) -> "KRXSession":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(headless=self.headless)
        self.context = self.browser.new_context(
            locale="ko-KR",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        self._login()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self.browser:
            self.browser.close()
        if self._playwright:
            self._playwright.stop()

    def _login(self) -> None:
        page = self.context.new_page()
        response = page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        body = page.locator("body").inner_text()
        if "서비스 제공 불가능" in body or "일시적 접근 불안정" in body:
            raise KRXUnavailable("KRX login service is temporarily unavailable")
        if response and response.status >= 400:
            raise KRXUnavailable(f"KRX login returned HTTP {response.status}")

        candidates = [page] + [frame for frame in page.frames if frame != page.main_frame]
        login_frame = next(
            (
                frame
                for frame in candidates
                if frame.locator("input[name='mbrId']").count()
                and frame.locator("input[name='pw']").count()
            ),
            None,
        )
        if login_frame is None:
            raise KRXUnavailable("KRX login form was not available")
        login_frame.fill("input[name='mbrId']", self.username)
        login_frame.fill("input[name='pw']", self.password)
        if login_frame.locator("a.jsLoginBtn").count():
            login_frame.click("a.jsLoginBtn")
        else:
            login_frame.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_timeout(4000)
        # 2026-08-30 수정: "이미 로그인된 계정입니다. 로그아웃하고 새로 로그인하시겠습니까?"
        # 확인 다이얼로그가 <button>이 아니라 <a>/<input>으로 렌더링돼 기존
        # "button:visible:has-text('확인')" 셀렉터가 매칭 실패 → 확인창이 닫히지 않아
        # 로그인이 끝까지 완료되지 못하고 매번 "client session 없음"으로 실패하던 것을
        # 실측 확인(Playwright로 로그인 재현, 스크린샷/DOM 덤프로 원인 특정). 태그 무관하게
        # "확인" 텍스트를 가진 클릭 가능한 요소를 폭넓게 찾도록 셀렉터 확장.
        confirm_selector = (
            "a:visible:has-text('확인'), button:visible:has-text('확인'), "
            "input[type='button'][value='확인']:visible, input[type='submit'][value='확인']:visible"
        )
        for frame in [page] + list(page.frames):
            try:
                frame.click(confirm_selector, timeout=1500)
                page.wait_for_timeout(500)
            except Exception:
                continue
        page.wait_for_timeout(1500)
        cookies = self.context.cookies()
        if not any("mdc.client_session" in item["name"] for item in cookies):
            raise KRXUnavailable("KRX authentication did not issue a client session")

    def fetch(self, day: str, isin: str) -> list[dict[str, Any]]:
        response = self.context.request.post(
            BLD_URL,
            form={"bld": BLD_ID, "trdDd": day, "isuCd": isin},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": REFERER},
        )
        text = response.text()
        if response.status in {401, 403} or text.strip() == "LOGOUT":
            raise KRXUnavailable("KRX session expired")
        if response.status != 200:
            raise RuntimeError(f"KRX PDF HTTP {response.status}")
        payload = response.json()
        output = payload.get("output")
        if not isinstance(output, list):
            raise RuntimeError(f"KRX PDF response has no output list: {list(payload)[:10]}")
        return output


def active_etfs(conn: sqlite3.Connection) -> list[ETF]:
    rows = conn.execute(
        """
        SELECT etf_ticker,etf_name,isin FROM etf_meta
        WHERE is_active=1 AND LENGTH(isin)=12
        ORDER BY etf_ticker
        """
    ).fetchall()
    return [ETF(row[0], row[1], row[2]) for row in rows]


def raw_bytes(rows: list[dict[str, Any]]) -> bytes:
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def store_raw(day: str, ticker: str, rows: list[dict[str, Any]], root: Path = RAW_ROOT) -> tuple[str, str]:
    data = raw_bytes(rows)
    digest = hashlib.sha256(data).hexdigest()
    directory = root / day
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{ticker}.json.gz"
    temporary = path.with_suffix(".json.gz.tmp")
    with gzip.open(temporary, "wb") as stream:
        stream.write(data)
    temporary.replace(path)
    return str(path), digest


def normalized(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for order, row in enumerate(rows, 1):
        code = str(row.get("COMPST_ISU_CD") or "").strip()
        name = str(row.get("COMPST_ISU_NM") or "").strip()
        result.append(
            {
                "order": order,
                "code": code,
                "name": name,
                "shares": number(row.get("COMPST_ISU_CU1_SHRS")),
                "valuation": number(row.get("VALU_AMT")),
                "component_amount": number(row.get("COMPST_AMT")),
                "weight": number(row.get("COMPST_RTO")),
                "is_domestic": int(bool(re.fullmatch(r"\d{6}", code))),
                "raw_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
            }
        )
    return result


def save_snapshot(
    conn: sqlite3.Connection,
    day: str,
    etf: ETF,
    rows: list[dict[str, Any]],
    raw_path: str,
    digest: str,
) -> None:
    items = normalized(rows)
    weight_values = [item["weight"] for item in items if item["weight"] is not None]
    now = datetime.now().isoformat(timespec="seconds")
    with conn:
        conn.execute(
            "DELETE FROM etf_pdf_full_component WHERE base_date=? AND etf_ticker=?",
            (day, etf.ticker),
        )
        conn.execute(
            """
            INSERT INTO etf_pdf_full_snapshot(
                base_date,etf_ticker,etf_name,isin,status,component_count,
                domestic_stock_count,weight_sum,raw_path,raw_sha256,error,collected_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,NULL,?)
            ON CONFLICT(base_date,etf_ticker) DO UPDATE SET
                etf_name=excluded.etf_name,isin=excluded.isin,status=excluded.status,
                component_count=excluded.component_count,
                domestic_stock_count=excluded.domestic_stock_count,
                weight_sum=excluded.weight_sum,raw_path=excluded.raw_path,
                raw_sha256=excluded.raw_sha256,error=NULL,collected_at=excluded.collected_at
            """,
            (
                day,etf.ticker,etf.name,etf.isin,"success",len(items),
                sum(item["is_domestic"] for item in items),
                sum(weight_values) if weight_values else None,raw_path,digest,now,
            ),
        )
        conn.executemany(
            """
            INSERT INTO etf_pdf_full_component(
                base_date,etf_ticker,component_order,component_code,component_name,
                shares_per_cu,valuation_amount,component_amount,weight,
                is_domestic_stock,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    day,etf.ticker,item["order"],item["code"],item["name"],
                    item["shares"],item["valuation"],item["component_amount"],
                    item["weight"],item["is_domestic"],item["raw_json"],
                )
                for item in items
            ],
        )


def save_failure(conn: sqlite3.Connection, day: str, etf: ETF, status: str, error: str) -> None:
    with conn:
        conn.execute(
            """
            INSERT INTO etf_pdf_full_snapshot(
                base_date,etf_ticker,etf_name,isin,status,error,collected_at
            ) VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(base_date,etf_ticker) DO UPDATE SET
                status=excluded.status,error=excluded.error,collected_at=excluded.collected_at
            """,
            (day,etf.ticker,etf.name,etf.isin,status,error[:1000],datetime.now().isoformat(timespec="seconds")),
        )


def assess_and_publish(conn: sqlite3.Connection, day: str, universe_count: int) -> dict[str, int | bool]:
    row = conn.execute(
        """
        SELECT COUNT(*) snapshots,
               SUM(status='success' AND component_count>0) successes,
               SUM(status='empty' OR (status='success' AND component_count=0)) empty_count,
               SUM(status='error') errors,
               COALESCE(SUM(CASE WHEN status='success' THEN component_count ELSE 0 END),0) components
        FROM etf_pdf_full_snapshot WHERE base_date=?
        """,
        (day,),
    ).fetchone()
    snapshots = int(row["snapshots"] or 0)
    successes = int(row["successes"] or 0)
    empty_count = int(row["empty_count"] or 0)
    errors = int(row["errors"] or 0)
    complete = snapshots == universe_count and successes == universe_count and not empty_count and not errors
    if complete:
        with conn:
            conn.execute(
                """
                INSERT INTO etf_pdf_full_publication(
                    base_date,universe_count,snapshot_count,component_count,published_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(base_date) DO UPDATE SET
                    universe_count=excluded.universe_count,
                    snapshot_count=excluded.snapshot_count,
                    component_count=excluded.component_count,
                    published_at=excluded.published_at
                """,
                (day,universe_count,snapshots,int(row["components"]),datetime.now().isoformat(timespec="seconds")),
            )
    return {
        "snapshots": snapshots,
        "successes": successes,
        "empty": empty_count,
        "errors": errors,
        "components": int(row["components"]),
        "complete": complete,
    }


def collect(
    day: str,
    db_path: Path = DB_PATH,
    raw_root: Path = RAW_ROOT,
    delay: float = 0.35,
    retries: int = 3,
    limit: int | None = None,
    force: bool = False,
    headless: bool = True,
) -> dict[str, Any]:
    env = load_env()
    username = env.get("KRX_DATA_ID") or os.getenv("KRX_DATA_ID", "")
    password = env.get("KRX_DATA_PASS") or os.getenv("KRX_DATA_PASS", "")
    if not username or not password:
        raise KRXUnavailable("KRX_DATA_ID/KRX_DATA_PASS are not configured")

    conn = connect(db_path)
    universe = active_etfs(conn)
    if limit:
        universe = universe[:limit]
    if not universe:
        raise RuntimeError("ETF universe is empty; collect etf_meta first")

    started = datetime.now().isoformat(timespec="seconds")
    run_id = conn.execute(
        """
        INSERT INTO etf_pdf_full_run(base_date,status,universe_count,started_at)
        VALUES(?,?,?,?)
        """,
        (day,"running",len(universe),started),
    ).lastrowid
    conn.commit()
    stats: dict[str, Any] = {
        "run_id": run_id,"base_date": day,"universe": len(universe),"fetched": 0,
        "skipped": 0,"errors": 0,"empty": 0,"error_samples": [],
    }
    try:
        with KRXSession(username, password, headless=headless) as source:
            for index, etf in enumerate(universe, 1):
                existing = conn.execute(
                    """
                    SELECT status,component_count FROM etf_pdf_full_snapshot
                    WHERE base_date=? AND etf_ticker=?
                    """,
                    (day,etf.ticker),
                ).fetchone()
                if not force and existing and existing["status"] == "success" and existing["component_count"] > 0:
                    stats["skipped"] += 1
                    continue
                last_error: Exception | None = None
                for attempt in range(max(retries, 1)):
                    try:
                        rows = source.fetch(day, etf.isin)
                        if not rows:
                            save_failure(conn,day,etf,"empty","KRX returned an empty PDF")
                            stats["empty"] += 1
                        else:
                            path,digest = store_raw(day,etf.ticker,rows,raw_root)
                            save_snapshot(conn,day,etf,rows,path,digest)
                            stats["fetched"] += 1
                        last_error = None
                        break
                    except KRXUnavailable:
                        raise
                    except Exception as exc:
                        last_error = exc
                        if attempt + 1 < max(retries, 1):
                            time.sleep(min(2 ** attempt, 4))
                if last_error:
                    save_failure(conn,day,etf,"error",str(last_error))
                    stats["errors"] += 1
                    if len(stats["error_samples"]) < 30:
                        stats["error_samples"].append({"ticker":etf.ticker,"error":str(last_error)})
                if index % 50 == 0:
                    LOG.info("PDF progress %s/%s",index,len(universe))
                time.sleep(max(delay,0))
        assessment = assess_and_publish(conn,day,len(universe))
        stats.update(assessment)
        status = "complete" if assessment["complete"] else "partial"
        with conn:
            conn.execute(
                """
                UPDATE etf_pdf_full_run SET status=?,success_count=?,empty_count=?,
                    error_count=?,fetched_count=?,skipped_count=?,is_complete=?,finished_at=?
                WHERE run_id=?
                """,
                (status,assessment["successes"],assessment["empty"],assessment["errors"],
                 stats["fetched"],stats["skipped"],int(assessment["complete"]),
                 datetime.now().isoformat(timespec="seconds"),run_id),
            )
        return stats
    except Exception as exc:
        with conn:
            conn.execute(
                """
                UPDATE etf_pdf_full_run SET status='source_unavailable',error=?,finished_at=?
                WHERE run_id=?
                """,
                (str(exc)[:1000],datetime.now().isoformat(timespec="seconds"),run_id),
            )
        raise
    finally:
        conn.close()


def membership(conn: sqlite3.Connection, stock_code: str, day: str | None = None) -> dict[str, Any]:
    selected = day or conn.execute(
        "SELECT MAX(base_date) FROM etf_pdf_full_publication"
    ).fetchone()[0]
    if not selected:
        return {
            "stock_code":stock_code,"base_date":None,"verdict":"source_unavailable",
            "is_confirmed":False,"etf_count":None,"holdings":[],
        }
    published = conn.execute(
        "SELECT * FROM etf_pdf_full_publication WHERE base_date=?",(selected,)
    ).fetchone()
    if not published:
        return {
            "stock_code":stock_code,"base_date":selected,"verdict":"snapshot_incomplete",
            "is_confirmed":False,"etf_count":None,"holdings":[],
        }
    holdings = [dict(row) for row in conn.execute(
        """
        SELECT c.etf_ticker,s.etf_name,c.component_name,c.shares_per_cu,
               c.valuation_amount,c.component_amount,c.weight
        FROM etf_pdf_full_component c
        JOIN etf_pdf_full_snapshot s
          ON s.base_date=c.base_date AND s.etf_ticker=c.etf_ticker
        WHERE c.base_date=? AND c.component_code=?
        ORDER BY c.component_amount DESC NULLS LAST,c.weight DESC NULLS LAST
        """,
        (selected,stock_code),
    )]
    return {
        "stock_code":stock_code,"base_date":selected,
        "verdict":"included" if holdings else "confirmed_not_included",
        "is_confirmed":True,"etf_count":len(holdings),"holdings":holdings,
        "universe_count":int(published["universe_count"]),
    }


def main() -> None:
    parser=argparse.ArgumentParser()
    parser.add_argument("--date")
    parser.add_argument("--db",default=str(DB_PATH))
    parser.add_argument("--raw-root",default=str(RAW_ROOT))
    parser.add_argument("--delay",type=float,default=0.35)
    parser.add_argument("--retries",type=int,default=3)
    parser.add_argument("--limit",type=int)
    parser.add_argument("--force",action="store_true")
    parser.add_argument("--show-browser",action="store_true")
    parser.add_argument("--stock")
    args=parser.parse_args()
    sys.path.insert(0,str(Path(__file__).parent))
    from direct_etf_pipeline import trading_date
    logging.basicConfig(level=logging.INFO,format="%(asctime)s %(levelname)s %(message)s")
    day=trading_date(args.date)
    if args.stock:
        conn=connect(Path(args.db))
        print(json.dumps(membership(conn,args.stock,day if args.date else None),ensure_ascii=False,indent=2))
        conn.close()
        return
    result=collect(day,Path(args.db),Path(args.raw_root),args.delay,args.retries,args.limit,args.force,not args.show_browser)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
