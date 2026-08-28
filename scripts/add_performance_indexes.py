"""
add_performance_indexes.py — 조회 성능 개선용 복합 인덱스 추가
(PID 5452 수집 완료 후 실행)

실행:
    python3 scripts/add_performance_indexes.py
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "stock.db"

INDEXES = [
    # financial_data: 종목별 연간/보고서유형별 조회 최적화
    "CREATE INDEX IF NOT EXISTS idx_fin_data_code_annual ON financial_data(stock_code, is_annual, report_type, year DESC)",
    # cash_flow_data: 동일 패턴
    "CREATE INDEX IF NOT EXISTS idx_cf_data_code_annual ON cash_flow_data(stock_code, is_annual, report_type, year DESC)",
    # financial_source_snapshot: 검증 조회 최적화
    "CREATE INDEX IF NOT EXISTS idx_fss_code_year_rt ON financial_source_snapshot(stock_code, year, report_type, data_source)",
    # price_history: 수급 조회 (이미 idx_price_history_code_date 있으나 trade_amount 추가)
    "CREATE INDEX IF NOT EXISTS idx_ph_code_date_close ON price_history(stock_code, date DESC, close) WHERE close > 0",
    # stock_universe: 시장+섹터 필터링
    "CREATE INDEX IF NOT EXISTS idx_su_market_sector ON stock_universe(market, sector_large)",
]

conn = sqlite3.connect(DB, timeout=120)
conn.execute("PRAGMA busy_timeout=120000")
conn.execute("PRAGMA journal_mode=WAL")

print(f"DB: {DB}")
for sql in INDEXES:
    idx_name = sql.split("INDEX IF NOT EXISTS ")[1].split(" ON ")[0]
    try:
        conn.execute(sql)
        conn.commit()
        print(f"  ✓ {idx_name}")
    except Exception as e:
        print(f"  ✗ {idx_name}: {e}")

conn.close()
print("완료")
