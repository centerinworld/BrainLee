"""
routes/ingest.py — 데이터 수신(Ingest) API

  POST /api/ingest/fundamentals
  POST /api/ingest/market-price
  POST /api/ingest/sectors
  POST /api/ingest/investor-trends
"""

import logging
from datetime import datetime, timedelta as _td

import crud, models, schemas
from database import get_db
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/fundamentals", response_model=schemas.FinancialIngest)
def ingest_fundamentals(financial: schemas.FinancialIngest, db: Session = Depends(get_db)):
    """재무제표 원시 데이터를 수신하여 저장합니다."""
    try:
        return crud.upsert_financial_data(db, financial)
    except Exception as e:
        logger.error(f"재무 데이터 수신 중 오류 발생: {e}")
        raise HTTPException(status_code=500, detail="데이터 저장 중 오류가 발생했습니다.")


@router.post("/market-price")
def ingest_market_price(price_ingest: schemas.PriceIngest, db: Session = Depends(get_db)):
    """일일 주가 마감 데이터를 수신하여 일괄 저장합니다."""
    if datetime.now().weekday() >= 5:  # 5=토, 6=일
        return {"status": "skip", "reason": "weekend"}
    try:
        crud.bulk_insert_price_history(db, price_ingest)
        return {"status": "success", "count": len(price_ingest.prices)}
    except Exception as e:
        logger.error(f"주가 데이터 수신 중 오류 발생: {e}")
        raise HTTPException(status_code=500, detail="데이터 일괄 저장 중 오류가 발생했습니다.")


@router.post("/sectors")
def ingest_sectors(sector: schemas.SectorMapping, db: Session = Depends(get_db)):
    """섹터별 소속 종목 맵핑 데이터를 수신합니다."""
    try:
        crud.update_sector_mapping(db, sector)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"섹터 데이터 수신 중 오류 발생: {e}")
        raise HTTPException(status_code=500, detail="데이터 저장 중 오류가 발생했습니다.")


@router.post("/investor-trends")
def ingest_investor_trends(payload: dict, db: Session = Depends(get_db)):
    """KIS 수급 데이터를 기존 주가 레코드에 업데이트합니다."""
    try:
        stock_code = payload.get("stock_code")
        trends     = payload.get("trends", [])
        updated    = 0
        for t in trends:
            try:
                date_str = datetime.strptime(t["date"], "%Y-%m-%d").strftime("%Y-%m-%d")
            except ValueError:
                continue
            # 한국 휴장일(공휴일/주말) 수급 데이터는 KIS 오류 — 저장 금지
            from trading_calendar import is_kr_trading_day as _is_kr_td
            from datetime import date as _date
            try:
                if not _is_kr_td(_date.fromisoformat(date_str)):
                    continue
            except Exception:
                pass
            row = db.query(models.PriceHistory).filter(
                models.PriceHistory.stock_code == stock_code,
                models.PriceHistory.date == date_str,
            ).first()
            fields = {
                "inst_net_buy":     t.get("inst_net_buy", 0),
                "frn_net_buy":      t.get("frn_net_buy",  0),
                "ind_net_buy":      t.get("ind_net_buy",  0),
                "inst_net_buy_amt": t.get("inst_net_buy_amt", 0),
                "frn_net_buy_amt":  t.get("frn_net_buy_amt",  0),
                "ind_net_buy_amt":  t.get("ind_net_buy_amt",  0),
            }
            if row:
                for k, v in fields.items():
                    setattr(row, k, v)
            else:
                # 가격 레코드가 없는 날짜의 수급은 건너뜀 (close=0 행 생성 방지)
                continue
            updated += 1
        db.commit()
        return {"status": "success", "updated": updated}
    except Exception as e:
        logger.error(f"수급 업데이트 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
