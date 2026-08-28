with open('/Applications/stock_dashboard/main.py', 'r') as f:
    content = f.read()

old = '@app.post("/api/trend/sell")'

new = '''@app.post("/api/trend/buy")
def trend_buy(payload: dict):
    import sqlite3 as _sl
    from datetime import datetime as _dt
    stock_name  = payload.get("stock_name","")
    stock_code  = payload.get("stock_code","")
    sector      = payload.get("sector","")
    buy_price   = float(payload.get("current_price") or payload.get("buy_price") or 0)
    quantity    = int(payload.get("quantity") or 0)
    entry_date  = payload.get("entry_date") or _dt.now().strftime("%Y-%m-%d")
    strategy    = payload.get("strategy","peak")
    if not stock_name or not buy_price:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="stock_name, buy_price 필수")
    conn = _sl.connect("stock.db")
    # 이미 활성 보유중이면 업데이트만
    existing = conn.execute(
        "SELECT id FROM peak_holding WHERE stock_name=? AND is_active=1", (stock_name,)
    ).fetchone()
    if not existing:
        conn.execute("""
            INSERT INTO peak_holding
            (stock_name, sector, buy_price, current_price, quantity,
             entry_date, hold_days, profit_pct, is_active, strategy,
             detected_at, updated_at)
            VALUES (?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        """, (stock_name, sector, buy_price, buy_price, quantity, entry_date, strategy))
        conn.execute("""
            INSERT INTO peak_trade
            (stock_name, tx_type, price, quantity, total_amount,
             profit, profit_pct, tx_at, strategy)
            VALUES (?,?,?,?,?,0,0.0,CURRENT_TIMESTAMP,?)
        """, (stock_name, "buy", buy_price, quantity,
              round(buy_price*quantity), strategy))
    else:
        conn.execute(
            "UPDATE peak_holding SET current_price=?, updated_at=CURRENT_TIMESTAMP WHERE stock_name=? AND is_active=1",
            (buy_price, stock_name))
    conn.commit(); conn.close()
    return {"status":"ok","stock_name":stock_name,"buy_price":buy_price}


@app.post("/api/trend/sell")'''

if old in content:
    content = content.replace(old, new)
    with open('/Applications/stock_dashboard/main.py', 'w') as f:
        f.write(content)
    print("OK")
else:
    print("NOT FOUND")
