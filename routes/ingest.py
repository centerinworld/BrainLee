"""
routes/ingest.py — 데이터 수신(Ingest) API

  POST /api/ingest/fundamentals
  POST /api/ingest/market-price
  POST /api/ingest/sectors
  POST /api/ingest/investor-trends
"""

from price_integrity import PriceIntegrityError
import logging
import time
from datetime import datetime, timedelta as _td

import crud, models, schemas
from database import get_db
from db_utils import stock_db_write_lock
from trading_calendar import is_kr_trading_day
from macro_data_quality import filter_plausible_price_rows
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import OperationalError
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
    if not is_kr_trading_day(datetime.now().date()):
        return {"status": "skip", "reason": "kr_market_holiday"}
    # 2026-10-02: 장 시작 전 KIS 현재가는 직전 거래일 종가인데 오늘 날짜로 라벨링돼(kis_client.get_current_price)
    # 00:10 야간배치 등이 '전일 종가·거래량 0' 임시 행을 오늘 날짜로 만들었다. 국내 개별종목의 오늘 행은
    # 09:00 이후이고 체결(거래량>0)이 있을 때만 받는다 — 확정 일봉은 18:00 KIS일별수집이 채운다.
    if price_ingest.stock_code.isdigit() and len(price_ingest.stock_code) == 6:
        _now = datetime.now()
        _today = _now.date().isoformat()
        _pre_open = _now.hour < 9
        kept = [p for p in price_ingest.prices
                if not (str(p.date)[:10] == _today and (_pre_open or (p.volume or 0) <= 0))]
        if len(kept) != len(price_ingest.prices):
            if not kept:
                return {"status": "skip", "reason": "kr_today_row_before_first_trade"}
            price_ingest = price_ingest.model_copy(update={"prices": kept})
    valid_prices, rejected = filter_plausible_price_rows(
        price_ingest.stock_code, price_ingest.prices
    )
    if rejected:
        logger.warning(
            "거시 가격 범위 이탈 차단: symbol=%s rejected=%s total=%s",
            price_ingest.stock_code,
            rejected,
            len(price_ingest.prices),
        )
    if not valid_prices:
        return {
            "status": "rejected",
            "reason": "implausible_macro_value",
            "rejected": rejected,
        }
    if rejected:
        price_ingest = price_ingest.model_copy(update={"prices": valid_prices})
    lock_error = None
    try:
        for delay in (0, 0.5, 1.5, 3.0):
            if delay:
                time.sleep(delay)
            with stock_db_write_lock("api-market-price", timeout=15) as acquired:
                if not acquired:
                    lock_error = "stock.db writer lock timeout"
                    continue
                try:
                    crud.bulk_insert_price_history(db, price_ingest)
                    return {"status": "success", "count": len(price_ingest.prices)}
                except OperationalError as exc:
                    db.rollback()
                    if "database is locked" not in str(exc).lower():
                        raise
                    lock_error = str(exc)
                    continue
        logger.warning(f"주가 데이터 저장 지연: {lock_error}")
        raise HTTPException(status_code=503, detail="주가 DB 쓰기 작업이 진행 중입니다. 다음 수집 주기에 재시도합니다.")
    except PriceIntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(e))
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
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
    def apply_updates() -> int:
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
        return updated

    lock_error = None
    try:
        for delay in (0, 0.5, 1.5, 3.0):
            if delay:
                time.sleep(delay)
            with stock_db_write_lock("api-investor-trends", timeout=15) as acquired:
                if not acquired:
                    lock_error = "stock.db writer lock timeout"
                    continue
                try:
                    updated = apply_updates()
                    return {"status": "success", "updated": updated}
                except OperationalError as exc:
                    db.rollback()
                    if "database is locked" not in str(exc).lower():
                        raise
                    lock_error = str(exc)
                    continue
        logger.warning(f"수급 업데이트 지연: {lock_error}")
        raise HTTPException(status_code=503, detail="수급 DB 쓰기 작업이 진행 중입니다. 다음 수집 주기에 재시도합니다.")
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"수급 업데이트 오류: {e}")
        raise HTTPException(status_code=500, detail=str(e))
