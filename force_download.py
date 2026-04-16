import sys
import sqlite3
from datetime import datetime
from kis_client import KISClient

kis = KISClient()
db = sqlite3.connect("stock.db")
db.row_factory = sqlite3.Row

# Get top 300 stocks by market cap
rows = db.execute("SELECT stock_code, stock_name FROM stock_universe ORDER BY market_cap DESC LIMIT 300").fetchall()

# Indices
indices = [("^KS11", "0001"), ("^KQ11", "1001")]

print("Fetching index data...")
for idx_code, kis_idx in indices:
    supply = kis.get_index_investor(kis_idx)
    if supply:
        print(f"Got {idx_code}")
        # Insert or update
        date_str = supply["date"]
        # In sqlite, price_history holds the date
        db.execute("""
            INSERT INTO price_history (stock_code, date, open, high, low, close, volume, 
                                       inst_net_buy, frn_net_buy, ind_net_buy,
                                       inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(stock_code, date) DO UPDATE SET
                inst_net_buy=excluded.inst_net_buy,
                frn_net_buy=excluded.frn_net_buy,
                ind_net_buy=excluded.ind_net_buy,
                inst_net_buy_amt=excluded.inst_net_buy_amt,
                frn_net_buy_amt=excluded.frn_net_buy_amt,
                ind_net_buy_amt=excluded.ind_net_buy_amt
        """, (idx_code, date_str, 0.0, 0.0, 0.0, supply.get("close", 0.0), 0.0,
              supply["inst_net_buy"], supply["frn_net_buy"], supply.get("ind_net_buy", 0),
              supply["inst_net_buy"], supply["frn_net_buy"], supply.get("ind_net_buy", 0)))
db.commit()

print(f"Fetching investor data for {len(rows)} stocks...")
for i, r in enumerate(rows):
    code = r["stock_code"]
    trends = kis.get_investor_trends_bulk(code)
    if not trends:
        continue
    for t in trends:
        # SQLite doesn't have partial update with UPSERT, so this assumes date is exactly matched like '2026-04-10 00:00:00'
        # In our DB they are like '2026-04-10 00:00:00.000000'
        # Let's search exactly
        ds = t["date"] + " 00:00:00.000000"
        
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
    if i % 10 == 0:
        db.commit()
        print(f"  {i}/{len(rows)} {code} done")
        
db.commit()
print("Done!")
