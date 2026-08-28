import sqlite3, sys
sys.path.insert(0, '/Applications/stock_dashboard')

conn = sqlite3.connect('/Applications/stock_dashboard/stock.db')
cur = conn.cursor()

cur.execute("PRAGMA table_info(portfolio_snapshot)")
cols = [r[1] for r in cur.fetchall()]
print("현재 컬럼:", cols)

# models.py PortfolioSnapshot에 맞게 컬럼 추가
needed = [
    ('avg_price',   'REAL DEFAULT 0'),
    ('eval_amount', 'REAL DEFAULT 0'),
    ('profit_amt',  'REAL DEFAULT 0'),
    ('profit_pct',  'REAL DEFAULT 0'),
    ('total_value', 'REAL DEFAULT 0'),
    ('profit',      'REAL DEFAULT 0'),
]
for col, typ in needed:
    if col not in cols:
        cur.execute(f"ALTER TABLE portfolio_snapshot ADD COLUMN {col} {typ}")
        print(f"  추가: {col}")

conn.commit()
cur.execute("PRAGMA table_info(portfolio_snapshot)")
print("최종 컬럼:", [r[1] for r in cur.fetchall()])
conn.close()
print("OK")
