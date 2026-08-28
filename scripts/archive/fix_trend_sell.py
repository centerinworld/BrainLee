with open('/Applications/stock_dashboard/main.py', 'r') as f:
    content = f.read()

old = '@app.get("/api/trend/trades")'

new = '''@app.post("/api/trend/sell")
def trend_sell(payload: dict):
    import sqlite3 as _sl
    from datetime import datetime as _dt
    stock_name = payload.get("stock_name","")
    sell_price = float(payload.get("sell_price") or 0)
    profit     = float(payload.get("profit") or 0)
    profit_pct = float(payload.get("profit_pct") or 0)
    sold_at    = payload.get("sold_at") or _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = _sl.connect("stock.db")
    conn.execute(
        "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=CURRENT_TIMESTAMP WHERE stock_name=? AND is_active=1",
        (sell_price, sold_at, sell_price, profit_pct, stock_name))
    row = conn.execute(
        "SELECT id, quantity, strategy FROM peak_holding WHERE stock_name=? ORDER BY id DESC LIMIT 1",
        (stock_name,)).fetchone()
    if row:
        qty = row[1] or 0
        conn.execute(
            "INSERT INTO peak_trade (stock_name, tx_type, price, quantity, total_amount, profit, profit_pct, tx_at, strategy) VALUES (?,?,?,?,?,?,?,?,?)",
            (stock_name, "sell", sell_price, qty, round(sell_price*qty), profit, profit_pct, sold_at, row[2] or "peak"))
    conn.commit(); conn.close()
    return {"status":"ok","stock_name":stock_name}


@app.get("/api/trend/trades")'''

if old in content:
    content = content.replace(old, new)
    with open('/Applications/stock_dashboard/main.py', 'w') as f:
        f.write(content)
    print("OK")
else:
    print("NOT FOUND")
