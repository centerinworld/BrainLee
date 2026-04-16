from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert
import models, schemas

_FINANCIAL_MODEL_COLUMNS = {
    c.key for c in models.FinancialData.__table__.columns
    if c.key != "id"
}


def bulk_insert_price_history(db: Session, price_ingest: schemas.PriceIngest):
    """
    주가 데이터 대량 삽입.

    정책:
    - 당일 데이터: 오늘 날짜 기존 레코드 DELETE 후 최신 close 1건 INSERT
      (LIKE '2026-03-24%' 로 삭제 → 00:00:00 이든 현재시각이든 모두 삭제)
    - 과거 데이터: INSERT IGNORE (확정된 과거 데이터 보존)
    """
    from datetime import date as date_type
    from sqlalchemy import text
    today     = date_type.today()
    today_str = today.isoformat()   # "2026-03-24"

    today_rows = []
    past_rows  = []

    for p in price_ingest.prices:
        # datetime → 'YYYY-MM-DD' 문자열 변환 (DB 날짜 형식 통일)
        _d = p.date
        _date_str = _d.strftime('%Y-%m-%d') if hasattr(_d, 'strftime') else str(_d)[:10]
        row = {
            "stock_code":   price_ingest.stock_code,
            "date":         _date_str,
            "open":         p.open,
            "high":         p.high,
            "low":          p.low,
            "close":        p.close,
            "volume":       p.volume,
            "inst_net_buy": p.inst_net_buy,
            "frn_net_buy":  p.frn_net_buy,
        }
        row_date = p.date.date() if hasattr(p.date, "date") else p.date
        if row_date >= today:
            today_rows.append(row)
        else:
            past_rows.append(row)

    # 과거 데이터: INSERT IGNORE
    if past_rows:
        stmt = insert(models.PriceHistory).values(past_rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["stock_code", "date"])
        db.execute(stmt)

    # 당일 데이터: 기존 수급 데이터 보존 후 가격만 갱신
    # (1분마다 실행되는 _realtime_fetch_price 가 supply=0 으로 덮어쓰는 것을 방지)
    if today_rows:
        best = max(today_rows, key=lambda r: r["close"])
        # 기존 오늘 레코드의 수급 필드 읽기
        existing_sup = db.execute(
            text("SELECT inst_net_buy, frn_net_buy, ind_net_buy, "
                 "inst_net_buy_amt, frn_net_buy_amt, ind_net_buy_amt "
                 "FROM price_history WHERE stock_code=:code AND date LIKE :pat"),
            {"code": price_ingest.stock_code, "pat": f"{today_str}%"}
        ).fetchone()
        if existing_sup:
            # 새 데이터에 수급값이 없으면(0) 기존 값 보존
            if not best.get("inst_net_buy"):
                best["inst_net_buy"] = existing_sup[0] or 0.0
            if not best.get("frn_net_buy"):
                best["frn_net_buy"]  = existing_sup[1] or 0.0
        db.execute(
            text("DELETE FROM price_history WHERE stock_code = :code AND date LIKE :pat"),
            {"code": price_ingest.stock_code, "pat": f"{today_str}%"}
        )
        db.execute(insert(models.PriceHistory).values([best]))

    db.commit()


def upsert_financial_data(db: Session, financial: schemas.FinancialIngest):
    """
    재무 데이터를 삽입하거나 이미 존재하면 업데이트합니다.
    """
    db_financial = db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == financial.stock_code,
        models.FinancialData.year == financial.year,
        models.FinancialData.quarter == financial.quarter,
    ).first()

    pk_fields = {"stock_code", "year", "quarter"}
    schema_dict = financial.dict()
    safe_fields = {
        k: v for k, v in schema_dict.items()
        if k not in pk_fields and k in _FINANCIAL_MODEL_COLUMNS
    }

    if db_financial:
        for key, value in safe_fields.items():
            setattr(db_financial, key, value)
    else:
        insert_data = {k: v for k, v in schema_dict.items() if k in _FINANCIAL_MODEL_COLUMNS}
        db_financial = models.FinancialData(**insert_data)
        db.add(db_financial)

    db.commit()
    db.refresh(db_financial)
    return db_financial


def update_sector_mapping(db: Session, sector: schemas.SectorMapping):
    db.query(models.SectorInfo).filter(
        models.SectorInfo.sector_name == sector.sector_name
    ).delete()
    for code in sector.stock_codes:
        new_info = models.SectorInfo(sector_name=sector.sector_name, stock_code=code)
        db.add(new_info)
        add_to_watchlist(db, code)
    db.commit()


def add_to_watchlist(db: Session, stock_code: str):
    exists = db.query(models.Watchlist).filter(
        models.Watchlist.stock_code == stock_code
    ).first()
    if not exists:
        db_watch = models.Watchlist(stock_code=stock_code)
        db.add(db_watch)
    return True


from ticker_utils import ticker_mapper

def get_watchlist(db: Session):
    watchlist = db.query(models.Watchlist).all()
    return [
        {
            "stock_code": item.stock_code,
            "stock_name": ticker_mapper.get_name(item.stock_code),
        }
        for item in watchlist
    ]
