import sqlite3

db = sqlite3.connect("stock.db")

# (Code, 04-09 Price, 04-10 Price)
prices = [
    ("005930", 204000, 206000), ("000660", 998000, 1027000), ("042700", 286000, 286000),
    ("034020", 100000, 100200), ("064350", 203000, 210000), ("006400", 480000, 481000),
    ("011070", 341000, 380000), ("139130", 16900, 18080), ("062040", 142500, 153500),
    ("086790", 117100, 119500), ("017670", 93800, 93000), ("036570", 234000, 240000),
    ("000990", 92600, 94800), ("267270", 158200, 162700), ("353200", 87400, 89900),
    ("079550", 887000, 921000), ("373220", 421000, 412000), ("272210", 137100, 133300),
    ("051910", 355000, 356000), ("375500", 94300, 94500), ("006800", 67700, 66500),
    ("055550", 96600, 98700), ("010950", 119600, 120700), ("483650", 177000, 179000),
    ("009150", 516000, 565000), ("454910", 91500, 89500), ("068270", 201500, 199700),
    ("028050", 51300, 51200), ("007660", 117900, 114900), ("005290", 45050, 49500),
    ("101490", 102100, 98100), ("356860", 80000, 74500)
]

def patch_prices(date_str, idx):
    print(f"Patching prices for {date_str}...")
    for code, p09, p10 in prices:
        price = p09 if idx == 0 else p10
        db.execute(f"UPDATE price_history SET close=? WHERE stock_code=? AND date LIKE ?", (price, code, date_str+"%"))

def compute_individual(date_str):
    print(f"Computing individual net buy for {date_str}...")
    # Ind = -(Inst + Frn)
    db.execute(f"""
        UPDATE price_history 
        SET ind_net_buy_amt = -(inst_net_buy_amt + frn_net_buy_amt)
        WHERE (date LIKE ?) AND (inst_net_buy_amt != 0 OR frn_net_buy_amt != 0)
    """, (date_str+"%",))

patch_prices("2026-04-09", 0)
patch_prices("2026-04-10", 1)
compute_individual("2026-04-09")
compute_individual("2026-04-10")

db.commit()
db.close()
print("Price and Individual data patches completed!")
