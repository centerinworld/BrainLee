"""
collect_krx_program_trading.py — Playwright로 data.krx.co.kr 로그인 후
프로그램매매(차익/비차익) 일별 데이터 수집 → program_trading_daily 저장

BLD: MDCSTAT05301 = 프로그램매매 추이(코스피)
     MDCSTAT05401 = 프로그램매매 추이(코스닥)

실행:
    python3 scripts/collect_krx_program_trading.py --date 20260613
    python3 scripts/collect_krx_program_trading.py --days 60
    python3 scripts/collect_krx_program_trading.py --start 20260101 --end 20260613
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH  = Path(__file__).resolve().parent.parent / "stock.db"
ENV_PATH = Path(__file__).resolve().parent.parent / ".env"

BLD_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
HEADERS = {"X-Requested-With": "XMLHttpRequest", "Referer": "https://data.krx.co.kr/"}


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
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _to_float(s) -> float | None:
    if s is None or str(s).strip() in ("", "-", "--"):
        return None
    try:
        return float(str(s).replace(",", "").replace("+", ""))
    except Exception:
        return None


def _business_days(start: str, end: str) -> list[str]:
    from datetime import date
    s = datetime.strptime(start, "%Y%m%d").date()
    e = datetime.strptime(end, "%Y%m%d").date()
    days = []
    cur = s
    while cur <= e:
        if cur.weekday() < 5:  # 월~금
            days.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return days


def _fetch_program(context, bld_id: str, date_str: str) -> dict | None:
    """MDCSTAT05301/05401 호출 → {prog, arb, non_arb} 억원 단위"""
    r = context.request.post(BLD_URL, form={
        "bld": bld_id,
        "trdDd": date_str,
        "share": "1",
        "money": "3",  # 억원
        "csvxls_isNo": "false",
    }, headers=HEADERS)
    if r.status != 200:
        logger.warning(f"  HTTP {r.status} for {bld_id}/{date_str}")
        return None
    try:
        data = r.json()
    except Exception:
        logger.warning(f"  JSON 파싱 실패 {bld_id}/{date_str}")
        return None

    output = data.get("output", [])
    if not output:
        return None

    # 응답 구조: 날짜별 1행 또는 합계행 탐색
    # 컬럼명 확인 후 파싱
    totals = {"prog": None, "arb": None, "non_arb": None}
    for row in output:
        # 키 확인 (BLD마다 다를 수 있음)
        for k, v in row.items():
            k_upper = k.upper()
            if "NETBID_TRDVAL" in k_upper or "NETBUY" in k_upper or "순매수" in str(k):
                # 프로그램 전체
                if "TOT" in k_upper or "PROGRAM" in k_upper or k_upper in ("NETBID_TRDVAL",):
                    if totals["prog"] is None:
                        totals["prog"] = _to_float(v)
                # 차익
                elif "ARB" in k_upper and "NON" not in k_upper:
                    if totals["arb"] is None:
                        totals["arb"] = _to_float(v)
                # 비차익
                elif "NONARB" in k_upper or "NON_ARB" in k_upper or "NONART" in k_upper:
                    if totals["non_arb"] is None:
                        totals["non_arb"] = _to_float(v)

    # 대안: 행이 차익/비차익으로 나뉘어 있는 경우
    if totals["arb"] is None:
        for row in output:
            tp = str(row.get("TRD_TP", row.get("PROG_TP", ""))).strip()
            val = _to_float(row.get("NETBID_TRDVAL") or row.get("NET_VAL") or row.get("NET_BUY"))
            if "차익" in tp and "비차익" not in tp:
                totals["arb"] = val
            elif "비차익" in tp:
                totals["non_arb"] = val
            elif "합계" in tp or "전체" in tp or tp == "":
                totals["prog"] = val

    # 합계 계산
    if totals["prog"] is None and totals["arb"] is not None and totals["non_arb"] is not None:
        totals["prog"] = (totals["arb"] or 0) + (totals["non_arb"] or 0)

    return totals if any(v is not None for v in totals.values()) else output


def _upsert(conn: sqlite3.Connection, date_str: str, market: str,
            prog: float | None, arb: float | None, non_arb: float | None) -> bool:
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    conn.execute("""
        INSERT INTO program_trading_daily (dt, market, prog_net_buy_amt, arb_net_buy_amt, non_arb_net_buy_amt, source)
        VALUES (?, ?, ?, ?, ?, 'krx_playwright')
        ON CONFLICT(dt, market) DO UPDATE SET
            prog_net_buy_amt  = excluded.prog_net_buy_amt,
            arb_net_buy_amt   = excluded.arb_net_buy_amt,
            non_arb_net_buy_amt = excluded.non_arb_net_buy_amt,
            source = 'krx_playwright',
            updated_at = DATETIME('now')
    """, (date_fmt, market, prog, arb, non_arb))
    return True


def _already_collected(conn, date_str: str) -> bool:
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    row = conn.execute(
        "SELECT 1 FROM program_trading_daily WHERE dt=? AND market='KOSPI' AND prog_net_buy_amt IS NOT NULL",
        (date_fmt,)
    ).fetchone()
    return row is not None


def collect_with_playwright(dates: list[str], headless: bool = True) -> dict:
    if not KRX_DATA_ID or not KRX_DATA_PASS:
        raise RuntimeError("KRX_DATA_ID / KRX_DATA_PASS 환경변수 없음")

    conn = _conn()
    stats = {"success": 0, "skipped": 0, "failed": 0, "records": 0}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("playwright 미설치: pip install playwright && playwright install chromium")
        return stats

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            locale="ko-KR",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        )
        page = context.new_page()

        # ── 로그인 (collect_krx_investor_playwright.py와 동일한 방식) ──
        logger.info("data.krx.co.kr 로그인 중...")
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

        logger.info("로그인 완료")

        # 프로그램매매 BLD 탐색 (MDCSTAT05301 코스피, MDCSTAT05401 코스닥)
        # 먼저 첫 날짜로 컬럼 구조 파악
        logger.info("BLD 컬럼 구조 파악 중...")
        for bld_id in ["dbms/MDC/STAT/standard/MDCSTAT05301",
                        "dbms/MDC/STAT/standard/MDCSTAT05401"]:
            r = context.request.post(BLD_URL, form={
                "bld": bld_id, "trdDd": dates[0],
                "share": "1", "money": "3", "csvxls_isNo": "false",
            }, headers=HEADERS)
            if r.status == 200:
                try:
                    d = r.json()
                    logger.info(f"{bld_id.split('/')[-1]}: keys={list(d.keys())}, output_len={len(d.get('output',[]))}")
                    if d.get("output"):
                        logger.info(f"  sample row: {d['output'][0]}")
                except Exception:
                    pass

        # ── 날짜별 수집 ──
        BLD_KOSPI  = "dbms/MDC/STAT/standard/MDCSTAT05301"
        BLD_KOSDAQ = "dbms/MDC/STAT/standard/MDCSTAT05401"

        for date_str in dates:
            if _already_collected(conn, date_str):
                logger.info(f"{date_str} 이미 수집됨")
                stats["skipped"] += 1
                continue

            try:
                # KOSPI
                kospi_raw = _fetch_program(context, BLD_KOSPI, date_str)
                # KOSDAQ
                kosdaq_raw = _fetch_program(context, BLD_KOSDAQ, date_str)

                logger.info(f"{date_str} KOSPI={kospi_raw}, KOSDAQ={kosdaq_raw}")

                if isinstance(kospi_raw, dict):
                    _upsert(conn, date_str, "KOSPI",
                            kospi_raw.get("prog"), kospi_raw.get("arb"), kospi_raw.get("non_arb"))
                    stats["records"] += 1
                elif isinstance(kospi_raw, list):
                    # 원본 출력이 리스트로 반환된 경우 — 컬럼 직접 저장
                    logger.warning(f"{date_str} KOSPI 파싱 필요 — 원본: {kospi_raw[0] if kospi_raw else 'empty'}")

                if isinstance(kosdaq_raw, dict):
                    _upsert(conn, date_str, "KOSDAQ",
                            kosdaq_raw.get("prog"), kosdaq_raw.get("arb"), kosdaq_raw.get("non_arb"))
                    stats["records"] += 1

                conn.commit()
                stats["success"] += 1
                page.wait_for_timeout(300)

            except Exception as e:
                logger.warning(f"{date_str} 실패: {e}")
                stats["failed"] += 1

        browser.close()
    conn.close()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="단일 날짜 YYYYMMDD")
    ap.add_argument("--days", type=int, help="최근 N 영업일")
    ap.add_argument("--start", help="시작일 YYYYMMDD")
    ap.add_argument("--end", help="종료일 YYYYMMDD")
    ap.add_argument("--no-headless", action="store_true")
    args = ap.parse_args()

    today = datetime.now().strftime("%Y%m%d")

    if args.date:
        dates = [args.date]
    elif args.days:
        end = today
        start_dt = datetime.now() - timedelta(days=args.days * 2)
        start = start_dt.strftime("%Y%m%d")
        dates = _business_days(start, end)[-args.days:]
    elif args.start and args.end:
        dates = _business_days(args.start, args.end)
    else:
        dates = [today]

    logger.info(f"수집 날짜: {len(dates)}일 ({dates[0] if dates else '-'} ~ {dates[-1] if dates else '-'})")
    stats = collect_with_playwright(dates, headless=not args.no_headless)
    logger.info(f"완료: {stats}")


if __name__ == "__main__":
    main()
