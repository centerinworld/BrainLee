import sys
import sqlite3
from datetime import datetime, timedelta
from kis_client import KISClient
import time

kis = KISClient()
db = sqlite3.connect("stock.db")
db.row_factory = sqlite3.Row

# Target dates
target_dates = ["2026-04-09", "2026-04-10"]

# Get KOSPI 200 + KOSDAQ 150 codes
stocks = db.execute("SELECT stock_code, stock_name FROM stock_universe ORDER BY market_cap DESC LIMIT 500").fetchall()

print(f"Fetching investor data for {len(stocks)} stocks for dates: {target_dates}")

for i, r in enumerate(stocks):
    code = r["stock_code"]
    name = r["stock_name"]
    try:
        trends = kis.get_investor_trends_bulk(code)
        if not trends:
            continue
            
        for t in trends:
            if t["date"] in target_dates:
                db.execute("""
                    UPDATE price_history 
                    SET inst_net_buy=?, frn_net_buy=?, ind_net_buy=?, 
                        inst_net_buy_amt=?, frn_net_buy_amt=?, ind_net_buy_amt=?
                    WHERE stock_code=? AND date LIKE ?
                """, (
                    t.get("inst_net_buy",0), t.get("frn_net_buy",0), t.get("ind_net_buy",0),
                    t.get("inst_net_buy_amt",0), t.get("frn_net_buy_amt",0), t.get("ind_net_buy_amt",0),
                    code, t["date"] + "%"
                ))
    except Exception as e:
        print(f"Error {code}: {e}")
        
    if i % 20 == 0:
        db.commit()
        print(f"  {i}/{len(stocks)} processed...")
    time.sleep(0.05) # Rate limit protection

db.commit()
print("Done!")
