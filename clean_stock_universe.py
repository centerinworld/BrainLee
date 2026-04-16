import sqlite3

db = sqlite3.connect("stock.db")

# 1. 중복 제거 및 이름 최적화
# 동일 코드가 여러 개일 때, 이름에 영문이 포함된 것을 우선하거나 ID가 큰 것을 남김.
rows = db.execute("SELECT stock_code, COUNT(*) as cnt FROM stock_universe GROUP BY stock_code HAVING cnt > 1").fetchall()
print(f"Found {len(rows)} duplicate stock codes.")

for code, cnt in rows:
    subs = db.execute("SELECT id, stock_name FROM stock_universe WHERE stock_code=?", (code,)).fetchall()
    # "SK하이닉스" vs "에스케이하이닉스" -> 영문 포함된 것을 선호
    best_id = subs[0][0]
    has_eng = False
    for sid, sname in subs:
        import re
        if re.search(r'[A-Za-z]', sname):
            best_id = sid
            has_eng = True
            break
    
    # 나머지는 제거
    to_delete = [sid for sid, sname in subs if sid != best_id]
    db.executemany("DELETE FROM stock_universe WHERE id=?", [(sid,) for sid in to_delete])

# 2. 특정 종목 이름 강제 보정 (사용자 요청: SK하이닉스)
corrections = [
    ("000660", "SK하이닉스"),
    ("005930", "삼성전자"),
    ("005380", "현대차"),
    ("000270", "기아"),
    ("005490", "POSCO홀딩스"),
]
for code, name in corrections:
    db.execute("UPDATE stock_universe SET stock_name=? WHERE stock_code=?", (name, code))

db.commit()
db.close()
print("Stock universe cleaned.")
