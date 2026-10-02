from sqlalchemy.orm import Session
import models, schemas
from config import IS_POSTGRES
from db_utils import connect_stock_db
from data_write_gate import (
    ensure_canonical_schema,
    gate_financial_row,
    upsert_canonical_financial,
)

if IS_POSTGRES:
    from sqlalchemy.dialects.postgresql import insert
else:
    from sqlalchemy.dialects.sqlite import insert

_FINANCIAL_MODEL_COLUMNS = {
    c.key for c in models.FinancialData.__table__.columns
    if c.key != "id"
}

# price_history 수급(공급) 6필드 — 순서는 아래 SELECT/튜플과 일치해야 한다.
_SUPPLY_FIELDS = (
    "inst_net_buy", "frn_net_buy", "ind_net_buy",
    "inst_net_buy_amt", "frn_net_buy_amt", "ind_net_buy_amt",
)


def merge_supply_fields(new_row: dict, existing) -> dict:
    """새 행의 빈(None/0) 수급 필드를 기존 값으로 되채운다.

    장중 1분 주가 갱신은 수급을 0으로 보내므로, 가격만 갱신할 때 기존에 확정된
    수급값(수량·금액 6필드)을 0·NULL로 덮어쓰면 안 된다. ``existing``는
    ``_SUPPLY_FIELDS`` 순서(inst, frn, ind, inst_amt, frn_amt, ind_amt)의 튜플.
    """
    for key, old in zip(_SUPPLY_FIELDS, existing):
        cur = new_row.get(key)
        if cur is None or cur == 0:
            if old not in (None, 0, 0.0):
                new_row[key] = old
    return new_row


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

    from price_integrity import ensure_schema, gate_price_batch, PriceIntegrityError
    gate_conn = connect_stock_db(timeout=30)
    try:
        ensure_schema(gate_conn)
        # 2026-10-02: 해외·거시 지표(yfinance 일봉=확정값)는 최근 7일 저장 행이 장중 스냅샷일 수 있어 정정을 허용한다.
        # 이전엔 마지막 장중 값과 0.5% 넘게 달라 배치 전체가 격리돼 JPY/EUR/HKD/TWDKRW·^DJI가 09-14~16 이후 멈췄다.
        # 국내 개별종목은 18:00 KIS 공식 일봉(collect_kis_ohlcv)이 정정하므로 여기선 기존 엄격 판정 유지.
        _is_kr_stock = price_ingest.stock_code.isdigit() and len(price_ingest.stock_code) == 6
        _prov_days = 0 if _is_kr_stock else 7
        if not _is_kr_stock:
            # yfinance가 가끔 종가<저가 같은 비정상 행을 섞어 보낸다 — 그 행만 버리고 나머지는 받는다(USDKRW 주간 640건 격리).
            from price_integrity import invalid_ohlcv as _invalid
            past_rows = [r for r in past_rows if not _invalid(r['open'], r['high'], r['low'], r['close'], r['volume'])]
            today_rows = [r for r in today_rows if not _invalid(r['open'], r['high'], r['low'], r['close'], r['volume'])]
        accepted = gate_price_batch(gate_conn, price_ingest.stock_code,
            [(r['date'],r['open'],r['high'],r['low'],r['close'],r['volume'])
             for r in past_rows+today_rows], 'market_price_api', provisional_days=_prov_days,
            # 해외·거시는 출처별 고시 시점 차이로 과거값이 0.5~2.6% 어긋난다(JPY/EUR/HKD/TWDKRW 실측) — 단위 변경·혼입만 막는다.
            overlap_tolerance=0.005 if _is_kr_stock else 0.03)
        gate_conn.commit()
    finally:
        gate_conn.close()
    if not accepted:
        raise PriceIntegrityError('Price batch quarantined: OHLC, historical overlap or boundary mismatch')

    if IS_POSTGRES:
        db.execute(text("SELECT set_config('app.price_basis_checked','1',true)"))
    # 과거 데이터: INSERT IGNORE
    if past_rows:
        from datetime import timedelta as _td
        _prov_from = (today - _td(days=_prov_days)).isoformat() if _prov_days else None
        if _prov_from:
            _last = db.execute(text("SELECT MAX(date) FROM price_history WHERE stock_code=:c"),
                               {"c": price_ingest.stock_code}).scalar()
            if _last and str(_last)[:10] < _prov_from:
                _prov_from = str(_last)[:10]
        recent = [r for r in past_rows if _prov_from and r["date"] >= _prov_from]
        older = [r for r in past_rows if not (_prov_from and r["date"] >= _prov_from)]
        if older:
            stmt = insert(models.PriceHistory).values(older)
            stmt = stmt.on_conflict_do_nothing(index_elements=["stock_code", "date"])
            db.execute(stmt)
        if recent:
            # 최근 구간은 확정 일봉으로 가격 필드만 갱신(수급 필드는 보존).
            stmt = insert(models.PriceHistory).values(recent)
            stmt = stmt.on_conflict_do_update(
                index_elements=["stock_code", "date"],
                set_={k: getattr(stmt.excluded, k) for k in ("open", "high", "low", "close", "volume")},
            )
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
            # 새 데이터에 수급값이 없으면(0/NULL) 기존 6개 수급 필드 보존
            merge_supply_fields(best, existing_sup)
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
        cconn = connect_stock_db(timeout=30)
        ensure_canonical_schema(cconn)
        ok, fixed, _ = gate_financial_row(cconn, payload)
        cconn.commit()
        cconn.close()
        if not ok:
            # The raw and canonical tables must not diverge: reject the write
            # before SQLAlchemy commits an unsafe financial statement row.
            db.rollback()
            raise ValueError(f"재무 데이터 검증 실패: {_}")
        # 게이트 보정값 반영
        for k, v in fixed.items():
            if hasattr(db_financial, k):
                setattr(db_financial, k, v)
    except ValueError:
        raise
    except Exception:
        # Gate telemetry must not make a valid primary write unavailable.
        logger.exception("재무 데이터 검증 게이트 실행 오류")

    db.commit()
    db.refresh(db_financial)

    # canonical 동기화
    try:
        cconn = connect_stock_db(timeout=30)
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
