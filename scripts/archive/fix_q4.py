import sys
sys.path.insert(0, '/Applications/stock_dashboard')
from database import SessionLocal
import models, schemas, crud

db = SessionLocal()

# Q1~Q3 조회
qs = {}
for q in [1, 2, 3]:
    qs[q] = db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == '172670',
        models.FinancialData.year       == 2025,
        models.FinancialData.quarter    == q,
        models.FinancialData.is_annual  == False,
    ).first()
    print(f"Q{q}: revenue={qs[q].revenue if qs[q] else 'NONE'}")

# 연간 조회
ann = db.query(models.FinancialData).filter(
    models.FinancialData.stock_code == '172670',
    models.FinancialData.year       == 2025,
    models.FinancialData.quarter    == 4,
    models.FinancialData.is_annual  == True,
).first()
print(f"연간: revenue={ann.revenue if ann else 'NONE'}")

if not (qs[1] and qs[2] and qs[3] and ann):
    print("ERROR: Q1~Q3 또는 연간 데이터 없음")
    db.close()
    sys.exit(1)

def sub(a, b, c, d):
    if d is None:
        return None
    return (d or 0) - (a or 0) - (b or 0) - (c or 0)

# 기존 Q4 분기 삭제 후 재저장
existing = db.query(models.FinancialData).filter(
    models.FinancialData.stock_code == '172670',
    models.FinancialData.year       == 2025,
    models.FinancialData.quarter    == 4,
    models.FinancialData.is_annual  == False,
).first()
if existing:
    db.delete(existing)
    db.commit()
    print("기존 Q4 분기 삭제")

q4 = schemas.FinancialIngest(
    stock_code       = '172670',
    year             = 2025,
    quarter          = 4,
    is_annual        = False,
    revenue          = sub(qs[1].revenue,          qs[2].revenue,          qs[3].revenue,          ann.revenue),
    operating_profit = sub(qs[1].operating_profit, qs[2].operating_profit, qs[3].operating_profit, ann.operating_profit),
    net_income       = sub(qs[1].net_income,        qs[2].net_income,        qs[3].net_income,        ann.net_income),
    total_assets     = ann.total_assets,
    total_liabilities= ann.total_liabilities,
    total_equity     = ann.total_equity,
    capital_stock    = ann.capital_stock,
    eps              = ann.eps,
    bps              = ann.bps,
    dps              = ann.dps,
    cash             = ann.cash,
)

crud.upsert_financial_data(db, q4)
db.commit()
print(f"2025Q4 분기 저장 완료")
print(f"  매출  = {q4.revenue/1e8:.0f}억")
print(f"  영업이익 = {q4.operating_profit/1e8:.0f}억")
print(f"  순이익  = {q4.net_income/1e8:.0f}억")
db.close()
