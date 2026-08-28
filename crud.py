from sqlalchemy.orm import Session
from sqlalchemy.dialects.sqlite import insert
import models, schemas
import sqlite3
from data_write_gate import (
    ensure_canonical_schema,
    gate_financial_row,
    upsert_canonical_financial,
)

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
    report_type = getattr(financial, "report_type", None) or "CFS"
    db_financial = db.query(models.FinancialData).filter(
        models.FinancialData.stock_code == financial.stock_code,
        models.FinancialData.year == financial.year,
        models.FinancialData.quarter == financial.quarter,
        models.FinancialData.is_annual == financial.is_annual,
        models.FinancialData.report_type == report_type,
    ).first()

    pk_fields = {"stock_code", "year", "quarter"}
    schema_dict = financial.dict()
    safe_fields = {
        k: v for k, v in schema_dict.items()
        if k not in pk_fields and k in _FINANCIAL_MODEL_COLUMNS
    }

    if db_financial:
        # FnGuide 레코드 보호: data_source='fnguide'인 레코드는 NULL/0 컬럼만 채움
        # (DART 재수집이 FnGuide 데이터를 덮어쓰는 것을 방지)
        is_fnguide = getattr(db_financial, 'data_source', None) == 'fnguide'

        for key, value in safe_fields.items():
            if key == 'data_source':
                continue  # data_source는 fnguide_financial_collector가 직접 관리
            existing = getattr(db_financial, key, None)
            # 새 값이 None이면 기존 값을 유지
            if value is None:
                continue
            # FnGuide 보호: 기존에 유효한 값이 있으면 덮어쓰지 않음
            if is_fnguide and existing not in (None, 0, 0.0):
                continue
            # 기존 값이 유효한데 새 값이 0이면 덮어쓰지 않음
            if value == 0 and existing not in (None, 0, 0.0):
                continue
            setattr(db_financial, key, value)
    else:
        insert_data = {k: v for k, v in schema_dict.items() if k in _FINANCIAL_MODEL_COLUMNS}
        db_financial = models.FinancialData(**insert_data)
        db.add(db_financial)

    # write-gate: 저장 전 불변식 보정/검증
    try:
        payload = {
            "stock_code": getattr(db_financial, "stock_code", financial.stock_code),
            "year": getattr(db_financial, "year", financial.year),
            "quarter": getattr(db_financial, "quarter", financial.quarter),
            "is_annual": getattr(db_financial, "is_annual", financial.is_annual),
            "report_type": getattr(db_financial, "report_type", report_type),
            "revenue": getattr(db_financial, "revenue", None),
            "operating_profit": getattr(db_financial, "operating_profit", None),
            "net_income": getattr(db_financial, "net_income", None),
            "total_assets": getattr(db_financial, "total_assets", None),
            "total_liabilities": getattr(db_financial, "total_liabilities", None),
            "total_equity": getattr(db_financial, "total_equity", None),
            "capital_stock": getattr(db_financial, "capital_stock", None),
            "eps": getattr(db_financial, "eps", None),
            "bps": getattr(db_financial, "bps", None),
            "dps": getattr(db_financial, "dps", None),
            "roe": getattr(db_financial, "roe", None),
            "data_source": getattr(db_financial, "data_source", None),
        }
        cconn = sqlite3.connect("/Applications/stock_dashboard/stock.db")
        ensure_canonical_schema(cconn)
        ok, fixed, _ = gate_financial_row(cconn, payload)
        cconn.commit()
        cconn.close()
        if ok:
            # 게이트 보정값 반영
            for k, v in fixed.items():
                if hasattr(db_financial, k):
                    setattr(db_financial, k, v)
    except Exception:
        pass

    db.commit()
    db.refresh(db_financial)

    # canonical 동기화
    try:
        cconn = sqlite3.connect("/Applications/stock_dashboard/stock.db")
        ensure_canonical_schema(cconn)
        upsert_canonical_financial(cconn, {
            "stock_code": db_financial.stock_code,
            "year": db_financial.year,
            "quarter": db_financial.quarter,
            "is_annual": db_financial.is_annual,
            "report_type": db_financial.report_type or "CFS",
            "revenue": db_financial.revenue,
            "operating_profit": db_financial.operating_profit,
            "net_income": db_financial.net_income,
            "total_assets": db_financial.total_assets,
            "total_liabilities": db_financial.total_liabilities,
            "total_equity": db_financial.total_equity,
            "capital_stock": db_financial.capital_stock,
            "eps": db_financial.eps,
            "bps": db_financial.bps,
            "dps": db_financial.dps,
            "roe": db_financial.roe,
            "data_source": db_financial.data_source,
        }, source_row_id=getattr(db_financial, "id", None), decision_reason="crud.upsert_financial_data")
        cconn.commit()
        cconn.close()
    except Exception:
        pass

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
