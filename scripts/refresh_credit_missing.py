#!/usr/bin/env python3
"""
kiwoom_credit_balance 미수집 종목 보완 수집
- 전체 2693종목 중 미수집 종목 우선 수집
- fetch_credit_balance는 내부적으로 DB 저장 처리
"""
import sys, time, sqlite3, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from collectors.kiwoom_collector import KiwoomCollector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

DB_PATH = str(ROOT / "stock.db")

def get_missing_stocks(limit: int = 3000) -> list[str]:
    conn = sqlite3.connect(DB_PATH)
    all_stocks = [r[0] for r in conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
          AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND market_cap IS NOT NULL
        ORDER BY market_cap DESC LIMIT ?
    """, (limit,)).fetchall()]
    collected = set(r[0] for r in conn.execute(
        "SELECT DISTINCT stock_code FROM kiwoom_credit_balance"
    ).fetchall())
    conn.close()
    missing = [s for s in all_stocks if s not in collected]
    log.info(f"전체 {len(all_stocks)}종목 중 미수집 {len(missing)}종목")
    return missing


def main():
    missing = get_missing_stocks(limit=3000)
    if not missing:
        log.info("미수집 종목 없음 — 완료")
        return

    c = KiwoomCollector()
    total_saved = 0; errors = 0

    for i, sc in enumerate(missing):
        try:
            result = c.fetch_credit_balance(sc, qry_tp="3", max_pages=13)
            saved = result.get("saved", 0) if isinstance(result, dict) else 0
            total_saved += saved

            if (i+1) % 50 == 0:
                log.info(f"진행 {i+1}/{len(missing)}: 마지막={sc} saved={saved} 누적={total_saved}")
            time.sleep(0.4)
        except Exception as e:
            errors += 1
            log.warning(f"오류 {sc}: {e}")
            time.sleep(1)

    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT COUNT(*), COUNT(DISTINCT stock_code) FROM kiwoom_credit_balance").fetchone()
    conn.close()
    log.info(f"완료 — 저장 {total_saved}행, 오류 {errors}건, DB 총 {row[0]}행/{row[1]}종목")


if __name__ == "__main__":
    main()
