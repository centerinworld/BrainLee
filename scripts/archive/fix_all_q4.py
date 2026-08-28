import sys
sys.path.insert(0, '/Applications/stock_dashboard')
from database import SessionLocal
import models, schemas, crud

db = SessionLocal()

def sub(a, b, c, d):
    if d is None: return None
    return (d or 0) - (a or 0) - (b or 0) - (c or 0)

annual_rows = db.query(models.FinancialData).filter(
    models.FinancialData.is_annual == True
).all()

targets = set((r.stock_code, r.year) for r in annual_rows)
print(f"연간 데이터 보유: {len(targets)}건")

saved = 0; skipped = 0; no_q = 0

for stock_code, year in sorted(targets):
    qs = {}
    for q in [1, 2, 3]:
        qs[q] = db.query(models.FinancialData).filter(
            models.FinancialData.stock_code == stock_code,
            models.FinancialData.year == year,
            models.FinancialData.quarter == q,
            models.FinancialData.is_annual == False,
        ).first()

    if not (qs[1] and qs[2] and qs[3]):
        no_q += 1; continue

    ann = db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == stock_code,
        models.FinancialData.year == year,
        models.FinancialData.quarter == 4,
        models.FinancialData.is_annual == True,
    ).first()
    if not ann:
        no_q += 1; continue

    existing = db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == stock_code,
        models.FinancialData.year == year,
        models.FinancialData.quarter == 4,
        models.FinancialData.is_annual == False,
    ).first()
    if existing:
        skipped += 1; continue

    try:
        q4 = schemas.FinancialIngest(
            stock_code=stock_code, year=year, quarter=4, is_annual=False,
            revenue=sub(qs[1].revenue, qs[2].revenue, qs[3].revenue, ann.revenue),
            operating_profit=sub(qs[1].operating_profit, qs[2].operating_profit, qs[3].operating_profit, ann.operating_profit),
            net_income=sub(qs[1].net_income, qs[2].net_income, qs[3].net_income, ann.net_income),
            total_assets=ann.total_assets, total_liabilities=ann.total_liabilities,
            total_equity=ann.total_equity, capital_stock=ann.capital_stock,
            eps=ann.eps, bps=ann.bps, dps=ann.dps, cash=ann.cash,
        )
        crud.upsert_financial_data(db, q4)
        saved += 1
        print(f"  {stock_code} {year}Q4: 매출={( q4.revenue or 0)/1e8:.0f}억 영업={(q4.operating_profit or 0)/1e8:.0f}억")
    except Exception as e:
        print(f"  오류 {stock_code} {year}: {e}")

db.commit()
db.close()
print(f"\n완료 저장={saved} 이미존재={skipped} 분기없음={no_q}")
