"""
collect_krx_investor_playwright.py — Playwright로 data.krx.co.kr 로그인 후
투자자별 순매수상위종목(MDCSTAT02401) 수집 → price_history 저장

invstTpCd:
  1000 = 기관합계  → inst_net_buy / inst_net_buy_amt
  9000 = 외국인합계 → frn_net_buy  / frn_net_buy_amt

단위:
  NETBID_TRDVOL → 주(株)
  NETBID_TRDVAL → 원(KRW) → ÷1,000,000 = 백만원 (DB 저장 단위)

실행:
    python3 scripts/collect_krx_investor_playwright.py --date 20260508
    python3 scripts/collect_krx_investor_playwright.py --days 30        # 최근 30일
    python3 scripts/collect_krx_investor_playwright.py --start 20220101 --end 20221231
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

DB_PATH  = Path(__file__).resolve().parent.parent / "stock.db"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

BLD_URL  = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
BLD_ID   = "dbms/MDC/STAT/standard/MDCSTAT02401"
HEADERS  = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://data.krx.co.kr/"}


def _load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                v = v.strip().strip('"').strip("'")
                env[k.strip()] = v
    return env


_ENV = _load_env()
KRX_DATA_ID   = _ENV.get("KRX_DATA_ID", "")
KRX_DATA_PASS = _ENV.get("KRX_DATA_PASS", "")


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, timeout=120)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=120000")
    return c


def _biz_dates(days: int) -> list[str]:
    result = []
    d = datetime.today()
    while len(result) < days:
        if d.weekday() < 5:
            result.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return result


def _date_range(start: str, end: str) -> list[str]:
    s = datetime.strptime(start, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    result = []
    d = s
    while d <= e:
        if d.weekday() < 5:
            result.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return result


def _already_collected(conn: sqlite3.Connection, date_str: str) -> bool:
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    cnt = conn.execute(
        "SELECT COUNT(*) FROM price_history WHERE date=? AND inst_net_buy_amt != 0 LIMIT 1",
        (date_fmt,)
    ).fetchone()[0]
    return cnt > 0


def _to_float(s: str) -> float | None:
    if not s:
        return None
    try:
        return float(str(s).replace(",", ""))
    except Exception:
        return None


def _fetch_investor(context, mkt_id: str, invst_tp: str, date_str: str) -> list[dict]:
    """MDCSTAT02401 API 호출 → [{stock_code, net_vol, net_amt_mwon}]"""
    r = context.request.post(BLD_URL, form={
        "bld": BLD_ID,
        "mktId": mkt_id,
        "invstTpCd": invst_tp,
        "strtDd": date_str,
        "endDd": date_str,
        "share": "1",
        "money": "1",
    }, headers=HEADERS)
    if r.status != 200:
        logger.warning(f"  HTTP {r.status} for {mkt_id}/{invst_tp}/{date_str}")
        return []
    data = r.json()
    rows = []
    for item in data.get("output", []):
        code = item.get("ISU_SRT_CD", "").strip()
        if not code or not code.isdigit():
            continue
        net_vol = _to_float(item.get("NETBID_TRDVOL"))
        net_amt_won = _to_float(item.get("NETBID_TRDVAL"))
        net_amt_mwon = round(net_amt_won / 1_000_000) if net_amt_won is not None else None
        rows.append({"stock_code": code, "net_vol": net_vol, "net_amt_mwon": net_amt_mwon})
    return rows


def _upsert(conn: sqlite3.Connection, date_str: str,
            inst_rows: list[dict], frn_rows: list[dict], dry_run: bool) -> int:
    """price_history에 기관/외국인 수급 데이터 upsert."""
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    # stock_code → {inst/frn} 매핑
    by_code: dict[str, dict] = {}
    for r in inst_rows:
        by_code.setdefault(r["stock_code"], {})["inst"] = r
    for r in frn_rows:
        by_code.setdefault(r["stock_code"], {})["frn"] = r

    updated = 0
    for code, vals in by_code.items():
        inst = vals.get("inst", {})
        frn  = vals.get("frn", {})
        if dry_run:
            updated += 1
            continue
        conn.execute("""
            UPDATE price_history
            SET inst_net_buy     = COALESCE(?, inst_net_buy),
                inst_net_buy_amt = COALESCE(?, inst_net_buy_amt),
                frn_net_buy      = COALESCE(?, frn_net_buy),
                frn_net_buy_amt  = COALESCE(?, frn_net_buy_amt)
            WHERE stock_code = ? AND date = ?
        """, (
            inst.get("net_vol"), inst.get("net_amt_mwon"),
            frn.get("net_vol"),  frn.get("net_amt_mwon"),
            code, date_fmt,
        ))
        updated += 1
    if not dry_run:
        conn.commit()
    return updated


def collect_with_playwright(dates: list[str], dry_run: bool = False,
                             headless: bool = True) -> dict:
    from playwright.sync_api import sync_playwright

    if not KRX_DATA_ID or not KRX_DATA_PASS:
        raise RuntimeError("KRX_DATA_ID / KRX_DATA_PASS 환경변수 없음")

    conn = _conn()
    stats = {"success": 0, "skipped": 0, "failed": 0, "records": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # ── 로그인 ──────────────────────────────────────────────
        logger.info("data.krx.co.kr 로그인...")
        page.goto("https://data.krx.co.kr/", wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)
        page.click("a[href*='MDCCOMS001']", timeout=10000)
        page.wait_for_timeout(3000)

        frame = page.frames[1]
        frame.fill("input[name='mbrId']", KRX_DATA_ID)
        frame.fill("input[name='pw']", KRX_DATA_PASS)
        frame.click("a.jsLoginBtn")
        page.wait_for_timeout(4000)

        for f in [page] + list(page.frames):
            try:
                f.click("button:visible:has-text('확인')", timeout=1500)
                break
            except Exception:
                pass

        try:
            page.wait_for_url("**/MAIN/**", timeout=10000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

        cookies = context.cookies()
        if not any("mdc.client_session" in c["name"] for c in cookies):
            logger.error("로그인 실패 — mdc.client_session 쿠키 없음")
            browser.close()
            conn.close()
            return stats

        logger.info(f"로그인 완료: {page.url}")

        # ── 날짜별 수집 ─────────────────────────────────────────
        for date_str in dates:
            if _already_collected(conn, date_str) and not dry_run:
                logger.info(f"{date_str} 이미 수집됨, 건너뜀")
                stats["skipped"] += 1
                continue

            logger.info(f"{date_str} 수집 중...")
            try:
                inst_rows: list[dict] = []
                frn_rows:  list[dict] = []

                for mkt_id, mkt_name in [("STK", "KOSPI"), ("KSQ", "KOSDAQ")]:
                    inst = _fetch_investor(context, mkt_id, "1000", date_str)
                    frn  = _fetch_investor(context, mkt_id, "9000", date_str)
                    inst_rows.extend(inst)
                    frn_rows.extend(frn)
                    logger.info(f"  {mkt_name}: 기관 {len(inst)}건, 외국인 {len(frn)}건")
                    time.sleep(0.5)

                if inst_rows or frn_rows:
                    cnt = _upsert(conn, date_str, inst_rows, frn_rows, dry_run)
                    stats["records"] += cnt
                    stats["success"] += 1
                    logger.info(f"  → {cnt}건 upsert")
                else:
                    logger.warning(f"  데이터 없음 (휴장일?)")
                    stats["failed"] += 1

            except Exception as e:
                logger.error(f"  오류: {e}")
                stats["failed"] += 1

            time.sleep(1.0)

        browser.close()
    conn.close()
    return stats


def main():
    ap = argparse.ArgumentParser(description="KRX 투자자별 순매수 수집 (Playwright)")
    ap.add_argument("--date",  help="특정일 (YYYYMMDD)")
    ap.add_argument("--days",  type=int, default=0, help="최근 N 영업일")
    ap.add_argument("--start", help="시작일 (YYYYMMDD)")
    ap.add_argument("--end",   help="종료일 (YYYYMMDD)")
    ap.add_argument("--dry-run",      action="store_true")
    ap.add_argument("--show-browser", action="store_true")
    ap.add_argument("--force",        action="store_true", help="이미 수집된 날도 재수집")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if args.date:
        dates = [args.date]
    elif args.start and args.end:
        dates = _date_range(args.start, args.end)
    elif args.days:
        dates = _biz_dates(args.days)
    else:
        dates = _biz_dates(30)

    logger.info(f"수집 대상: {len(dates)}일 ({dates[0]} ~ {dates[-1]})")

    stats = collect_with_playwright(
        dates=dates,
        dry_run=args.dry_run,
        headless=not args.show_browser,
    )

    print(f"\nKRX 투자자 수집 결과:")
    print(f"  성공: {stats['success']}일")
    print(f"  건너뜀: {stats['skipped']}일")
    print(f"  실패: {stats['failed']}일")
    print(f"  저장: {stats['records']}건")


if __name__ == "__main__":
    main()
