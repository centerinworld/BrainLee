
import sqlite3
conn = sqlite3.connect("/Applications/stock_dashboard/stock.db")
# 5월 8일 (목) 종가 - 확인 후 수정하세요
data = [
    # (symbol, date, open, high, low, close, volume)
    # 아래 숫자는 확인이 필요합니다
    ("^IXIC", "2026-05-08", 25970.0, 26100.0, 25900.0, 26060.0, 0),
    ("^GSPC", "2026-05-08", 7378.0, 7420.0, 7360.0, 7415.0, 0),
]
for row in data:
    conn.execute("""
        INSERT INTO price_history (stock_code, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(stock_code, date) DO UPDATE SET
            close=excluded.close, open=excluded.open,
            high=excluded.high, low=excluded.low
    """, row)
conn.commit()
conn.close()
print("삽입 완료")
