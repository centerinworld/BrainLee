import sqlite3, yfinance as yf
from datetime import date

conn = sqlite3.connect('/Applications/stock_dashboard/stock.db')
# 활성 보유종목 코드 조회
codes = conn.execute("""
    SELECT DISTINCT su.stock_code 
    FROM peak_holding ph
    JOIN stock_universe su ON su.stock_name = ph.stock_name
    WHERE ph.is_active=1
""").fetchall()

today = date.today().strftime('%Y-%m-%d')
for (code,) in codes:
    for suffix in ['.KS', '.KQ']:
        try:
            hist = yf.Ticker(f'{code}{suffix}').history(period='5d')
            if hist.empty: continue
            for dt, row in hist.iterrows():
                conn.execute('INSERT OR IGNORE INTO price_history (stock_code,date,open,high,low,close,volume,inst_net_buy,frn_net_buy) VALUES (?,?,?,?,?,?,?,0,0)',
                    (code, dt.strftime('%Y-%m-%d'), float(row.Open), float(row.High), float(row.Low), float(row.Close), int(row.Volume)))
            conn.commit()
            print(f'{code}: OK')
            break
        except: pass

conn.close()
print('완료')
