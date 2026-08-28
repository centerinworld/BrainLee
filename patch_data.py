import sqlite3
from datetime import datetime

db = sqlite3.connect("stock.db")

# 2026-04-09 KOSPI Institutional Top (억 단위 -> 백만원 단위로 저장: * 100)
data_0409 = [
    ("000660", 492320.0), ("042700", 66750.0), ("229200", 57620.0), ("252670", 52050.0),
    ("373220", 51940.0), ("272210", 36360.0), ("051910", 34660.0), ("114800", 31260.0),
    ("375500", 29990.0), ("006800", 29750.0), ("079550", 25500.0), ("055550", 24640.0),
    ("017670", 23850.0), ("010950", 22680.0), ("483650", 22130.0), ("009150", 19580.0),
    ("454910", 18200.0), ("068270", 18140.0), ("028050", 17260.0), ("007660", 16930.0),
]

# 2026-04-10 KOSPI Institutional Top
data_0410 = [
    ("122630", 122050.0), ("229200", 92500.0), ("042700", 87620.0), ("034020", 70030.0),
    ("069500", 68460.0), ("064350", 62950.0), ("006400", 55830.0), ("011070", 55450.0),
    ("139130", 49940.0), ("494310", 30510.0), ("233740", 28140.0), ("488080", 24630.0),
    ("062040", 22100.0), ("086790", 20680.0), ("017670", 18740.0), ("036570", 18400.0),
    ("000990", 18210.0), ("267270", 17050.0), ("353200", 16980.0), ("079550", 16820.0),
]

def patch(date_str, list_data):
    print(f"Patching {date_str}...")
    for code, amt in list_data:
        # Check if row exists
        row = db.execute("SELECT 1 FROM price_history WHERE stock_code=? AND date LIKE ?", (code, date_str+"%")).fetchone()
        if row:
            db.execute("UPDATE price_history SET inst_net_buy_amt=? WHERE stock_code=? AND date LIKE ?", (amt, code, date_str+"%"))
        else:
            # Insert a sparse row if not exists
            db.execute("INSERT INTO price_history (stock_code, date, open, high, low, close, volume, inst_net_buy_amt) VALUES (?, ?, 0, 0, 0, 0, 0, ?)", (code, date_str, amt))
    db.commit()

patch("2026-04-09", data_0409)
patch("2026-04-10", data_0410)
db.close()
print("Patch completed!")
