"""
collect_order_contracts.py — DART 수주(단일판매·공급계약) 공시 히스토리 백필

order_contracts 테이블은 스케줄러(scheduler.py `_job_order_contracts_daily`)가
매일 19:00 "오늘자" 공시만 쌓아가므로, 과거 데이터는 이 스크립트로 별도 백필해야
"예전 수주잔고 대비 급증 여부" 스크리닝(routes/order_contracts.py `/screener/surge`)이
의미를 가진다.

사용법:
  python3 collect_order_contracts.py --months 24                # 전종목(price_history 보유) 최근 24개월
  python3 collect_order_contracts.py --months 36 --limit 300     # 상위 300종목만
  python3 collect_order_contracts.py --codes 005930,000660       # 특정 종목만
  python3 collect_order_contracts.py --watchlist                 # watchlist+portfolio+buy_candidates만
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import config
from collectors.dart_collector import DARTCollector
from routes.order_contracts import _save_disclosure, _db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = str(Path(__file__).resolve().parent / "stock.db")
RATE_LIMIT_SECS = 0.3  # DART list() 호출 사이 최소 간격


def get_target_codes(watchlist_only: bool, limit: int | None) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    try:
        if watchlist_only:
            codes: set[str] = set()
            for table in ("watchlist", "portfolio", "buy_candidates"):
                try:
                    rows = conn.execute(
                        f"SELECT DISTINCT stock_code FROM {table} "
                        f"WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'"
                    ).fetchall()
                    codes.update(r[0] for r in rows)
                except sqlite3.OperationalError:
                    pass
            result = sorted(codes)
        else:
            rows = conn.execute("""
                SELECT DISTINCT stock_code FROM stock_universe
                WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  AND (stock_type IS NULL OR stock_type = '보통주')
                ORDER BY market_cap DESC NULLS LAST
            """).fetchall()
            result = [r[0] for r in rows]
    finally:
        conn.close()

    if limit:
        result = result[:limit]
    return result


async def collect_for_code(dart: DARTCollector, conn: sqlite3.Connection, code: str, months: int) -> tuple[int, int]:
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=months * 30)).strftime("%Y%m%d")
    items = await dart.get_contract_disclosures(code, start, end)
    saved = 0
    for item in items:
        item["stock_code"] = code
        try:
            if await _save_disclosure(conn, dart, item):
                saved += 1
                conn.commit()
        except Exception as e:
            logger.warning(f"[{code}] {item.get('rcept_no')} 저장 오류: {e}")
    return len(items), saved


async def main(months: int, codes: list[str]) -> None:
    dart = DARTCollector(api_key=config.DART_API_KEY)
    conn = _db()

    total_scanned = total_saved = 0
    try:
        for i, code in enumerate(codes):
            try:
                scanned, saved = await collect_for_code(dart, conn, code, months)
                total_scanned += scanned
                total_saved += saved
                if scanned:
                    logger.info(f"[{i+1}/{len(codes)}] {code}: 공시 {scanned}건 중 신규 {saved}건 저장")
            except Exception as e:
                logger.warning(f"[{i+1}/{len(codes)}] {code} 처리 오류: {e}")

            if (i + 1) % 50 == 0:
                logger.info(f"진행: {i+1}/{len(codes)} — 누적 스캔 {total_scanned}건, 저장 {total_saved}건")

            await asyncio.sleep(RATE_LIMIT_SECS)
    finally:
        conn.close()

    logger.info(f"완료 — 총 {len(codes)}종목, 스캔 {total_scanned}건, 신규저장 {total_saved}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--months", type=int, default=24, help="백필 기간(개월), 기본 24")
    parser.add_argument("--limit", type=int, default=None, help="대상 종목 수 제한 (시총순)")
    parser.add_argument("--codes", type=str, default=None, help="쉼표구분 종목코드 지정 (지정 시 --limit 무시)")
    parser.add_argument("--watchlist", action="store_true", help="watchlist+portfolio+buy_candidates 종목만")
    args = parser.parse_args()

    if args.codes:
        target_codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        target_codes = get_target_codes(watchlist_only=args.watchlist, limit=args.limit)

    logger.info(f"수주공시 백필 시작 — 대상 {len(target_codes)}종목, 최근 {args.months}개월")
    asyncio.run(main(args.months, target_codes))
