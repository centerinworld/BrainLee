import sys
import sqlite3
from datetime import datetime

sys.path.insert(0, '/Applications/stock_dashboard')
import config
from data_collector import DataCollector

DB='/Applications/stock_dashboard/stock.db'
LOG='/Applications/stock_dashboard/scratch/fill_2026q1_missing.log'

conn=sqlite3.connect(DB)
rows=conn.execute('''
SELECT u.stock_code
FROM stock_universe u
WHERE u.market IN ('유가증권','코스닥','KOSPI','KOSDAQ')
  AND u.stock_type='보통주'
  AND LENGTH(u.stock_code)=6
  AND u.stock_code GLOB '[0-9]*'
  AND u.stock_code NOT IN (
    SELECT DISTINCT stock_code FROM financial_data
    WHERE year=2026 AND quarter=1 AND is_annual=0
  )
ORDER BY u.stock_code
''').fetchall()
conn.close()
codes=[r[0] for r in rows]

collector=DataCollector(dart_api_key=config.DART_API_KEY)

with open(LOG,'a',encoding='utf-8') as f:
    f.write(f"\n[{datetime.now().isoformat(timespec='seconds')}] start missing={len(codes)}\n")

ok=0
for i,code in enumerate(codes,1):
    try:
        collector.collect_fundamentals(code, latest_only=True)
        # 확인
        conn=sqlite3.connect(DB)
        hit=conn.execute("SELECT 1 FROM financial_data WHERE stock_code=? AND year=2026 AND quarter=1 AND is_annual=0 LIMIT 1",(code,)).fetchone()
        conn.close()
        if hit:
            ok += 1
    except Exception as e:
        with open(LOG,'a',encoding='utf-8') as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] error {code}: {e}\n")
    if i % 50 == 0:
        with open(LOG,'a',encoding='utf-8') as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] progress {i}/{len(codes)} filled={ok}\n")

with open(LOG,'a',encoding='utf-8') as f:
    f.write(f"[{datetime.now().isoformat(timespec='seconds')}] done total={len(codes)} filled={ok}\n")
