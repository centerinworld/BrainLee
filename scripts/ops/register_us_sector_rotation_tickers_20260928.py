"""
scripts/ops/register_us_sector_rotation_tickers_20260928.py

2026-09-27 신설한 routes/us_sector_rotation.py(미국 주도섹터 판정)이 쓰는 13개 ETF(SPY/QQQ +
SPDR 섹터 11종)와, market_radar.py _SECTOR_REP_BASKETS가 쓰는 대표종목 중 us_stock_meta에
없던 11개는 daily 미국 시세 수집 잡(scheduler.py `_job_us_daily_quotes_and_factors` →
scripts/ops/sync_us_daily_quotes_and_factors.py)이 us_stock_meta 테이블에서 티커 목록을
가져오기 때문에, 등록돼 있지 않으면 자동 갱신 대상에서 빠져 2026-09-25 시점으로 계속 멈춰
있게 된다(2026-09-28 사용자 질문 "미국 주식 데이터는 100% 완료된거야?"로 점검 중 발견).

index_name='ETF'로 등록해 기존 sector_large 기반 쿼리(index_name IN ('S&P500','NASDAQ'))·
섹터폭/리더종목 계산에는 섞이지 않으면서, 일별 시세 자동 수집 대상에는 포함되게 한다.

실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. venv/bin/python3 scripts/ops/register_us_sector_rotation_tickers_20260928.py
재실행해도 안전(ON CONFLICT DO NOTHING — 이미 있으면 건너뜀).
"""
from __future__ import annotations

from db_compat import connect_primary_db

ETFS = [
    ("SPY", "SPDR S&P 500 ETF Trust"), ("QQQ", "Invesco QQQ Trust"),
    ("XLK", "Technology Select Sector SPDR"), ("XLF", "Financial Select Sector SPDR"),
    ("XLE", "Energy Select Sector SPDR"), ("XLI", "Industrial Select Sector SPDR"),
    ("XLP", "Consumer Staples Select Sector SPDR"), ("XLY", "Consumer Discretionary Select Sector SPDR"),
    ("XLV", "Health Care Select Sector SPDR"), ("XLB", "Materials Select Sector SPDR"),
    ("XLU", "Utilities Select Sector SPDR"), ("XLRE", "Real Estate Select Sector SPDR"),
    ("XLC", "Communication Services Select Sector SPDR"),
]
STOCKS = [
    ("CCJ", "Cameco Corporation", "Basic Materials"), ("ZIM", "ZIM Integrated Shipping Services", "Industrials"),
    ("FRO", "Frontline plc", "Industrials"), ("GOGL", "Golden Ocean Group", "Industrials"),
    ("MATX", "Matson, Inc.", "Industrials"), ("KEX", "Kirby Corporation", "Industrials"),
    ("CLF", "Cleveland-Cliffs Inc.", "Basic Materials"), ("ASX", "ASE Technology Holding", "Technology"),
    ("CLS", "Celestica Inc.", "Technology"), ("COTY", "Coty Inc.", "Consumer Defensive"),
    ("ELF", "e.l.f. Beauty, Inc.", "Consumer Defensive"),
]


def main() -> None:
    conn = connect_primary_db(timeout=30)
    try:
        rows = [(t, n, "ETF", "ETF", "Index/Sector ETF") for t, n in ETFS] + \
               [(t, n, "ETF", sec, "Supplement (market_radar 대표종목)") for t, n, sec in STOCKS]
        added = 0
        for ticker, name, index_name, sector, industry in rows:
            cur = conn.execute(
                """INSERT INTO us_stock_meta (ticker, company_name, exchange, index_name, sector, industry, country, currency)
                   VALUES (?, ?, 'US', ?, ?, ?, 'US', 'USD')
                   ON CONFLICT (ticker) DO NOTHING""",
                (ticker, name, index_name, sector, industry),
            )
            added += 1
        conn.commit()
        check = [r[0] for r in ETFS] + [r[0] for r in STOCKS]
        found = conn.execute(
            f"SELECT COUNT(*) FROM us_stock_meta WHERE ticker IN ({','.join('?' * len(check))})", check
        ).fetchone()[0]
        print(f"등록 확인: {found}/{len(check)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
