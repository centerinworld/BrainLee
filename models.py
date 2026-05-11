from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Index, UniqueConstraint
from sqlalchemy.sql import func
from database import Base


class FinancialData(Base):
    __tablename__ = "financial_data"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String, index=True)
    year = Column(Integer)
    quarter = Column(Integer)

    revenue = Column(Float, nullable=True)
    operating_profit = Column(Float, nullable=True)
    net_income = Column(Float, nullable=True)
    total_assets = Column(Float, nullable=True)
    total_liabilities = Column(Float, nullable=True)
    total_equity = Column(Float, nullable=True)
    capital_stock = Column(Float, nullable=True)

    eps = Column(Float, nullable=True)
    bps = Column(Float, nullable=True)
    dps = Column(Float, nullable=True)

    cash = Column(Float, nullable=True)
    total_shares = Column(Float, nullable=True)
    depreciation_amortization = Column(Float, nullable=True)

    # roe 컬럼: 기존 DB에 없으면 init_db.py 또는 migrate_db.py로 추가해야 함
    # (add_column_if_not_exists는 migrate_db.py 참조)
    roe = Column(Float, nullable=True)

    is_annual = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String, index=True)
    date = Column(String, index=True)

    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)

    inst_net_buy     = Column(Float, default=0.0)   # 기관 순매수 수량 (주)
    frn_net_buy      = Column(Float, default=0.0)   # 외국인 순매수 수량 (주)
    ind_net_buy      = Column(Float, default=0.0)   # 개인 순매수 수량 (주)
    inst_net_buy_amt = Column(Float, default=0.0)   # 기관 순매수 금액 (백만원)
    frn_net_buy_amt  = Column(Float, default=0.0)   # 외국인 순매수 금액 (백만원)
    ind_net_buy_amt  = Column(Float, default=0.0)   # 개인 순매수 금액 (백만원)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # UniqueConstraint: 기존 DB에 없으면 migrate_db.py로 추가
        UniqueConstraint("stock_code", "date", name="uq_price_history_code_date"),
        Index("idx_price_history_code_date", "stock_code", "date"),
    )


class SectorInfo(Base):
    __tablename__ = "sector_info"
    id = Column(Integer, primary_key=True, index=True)
    sector_name = Column(String, index=True)
    stock_code = Column(String, index=True)


class AIAnalysisReport(Base):
    __tablename__ = "ai_analysis_reports"
    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String, index=True)
    report_date = Column(DateTime, index=True)
    content = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Watchlist(Base):
    __tablename__ = "watchlist"
    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String, unique=True, index=True)
    added_at = Column(DateTime(timezone=True), server_default=func.now())


class Portfolio(Base):
    """보유 종목 잔고 (NH + KIS 통합, 보유자별 관리)"""
    __tablename__ = "portfolio"
    id          = Column(Integer, primary_key=True, index=True)
    stock_code  = Column(String, index=True)
    stock_name  = Column(String)
    sector      = Column(String, nullable=True)
    quantity    = Column(Float, default=0.0)
    avg_price   = Column(Float, default=0.0)
    broker      = Column(String, nullable=True, default="NH")   # "NH" | "KIS"
    owner       = Column(String, nullable=True)                  # 보유자 (프론트 미표시)
    source      = Column(String, default="manual")  # "manual" | "kis" | "csv"
    bought_at   = Column(String, nullable=True)      # 매수일 "2026-03-25" (수동입력 or KIS)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class PortfolioSnapshot(Base):
    """보유종목 일별 평가총액 스냅샷 (전일 대비 손익 계산용)"""
    __tablename__ = "portfolio_snapshot"
    id            = Column(Integer, primary_key=True, index=True)
    snapshot_date = Column(String, index=True)   # "2026-03-25"
    stock_code    = Column(String, index=True)
    stock_name    = Column(String, nullable=True)
    close_price   = Column(Float, default=0.0)
    quantity      = Column(Float, default=0.0)
    avg_price     = Column(Float, default=0.0)
    eval_amount   = Column(Float, default=0.0)   # 평가총액
    profit_amt    = Column(Float, default=0.0)   # 평가손익
    profit_pct    = Column(Float, default=0.0)   # 수익률
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("snapshot_date", "stock_code", name="uq_snap_date_code"),
    )


class PortfolioTx(Base):
    """매수/매도 거래 내역"""
    __tablename__ = "portfolio_tx"
    id          = Column(Integer, primary_key=True, index=True)
    stock_code  = Column(String, index=True)
    stock_name  = Column(String)
    tx_type     = Column(String)   # "buy" | "sell"
    quantity    = Column(Float)
    price       = Column(Float)
    tx_date     = Column(DateTime, index=True)
    memo        = Column(String, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class StockMeta(Base):
    """종목 메타정보 — 시장구분(KOSPI/KOSDAQ) 최초 1회 저장"""
    __tablename__ = "stock_meta"
    id                 = Column(Integer, primary_key=True, index=True)
    stock_code         = Column(String, unique=True, index=True)
    stock_name         = Column(String, nullable=True)
    market             = Column(String, nullable=True)   # "KOSPI" | "KOSDAQ" | "KONEX"
    sector             = Column(String, nullable=True)
    float_shares       = Column(Float, nullable=True)    # 유통주식수 (대주주 제외)
    shares_outstanding = Column(Float, nullable=True)    # 총 발행주식수
    float_updated_at   = Column(DateTime(timezone=True), nullable=True)  # 마지막 유통주식수 업데이트
    created_at         = Column(DateTime(timezone=True), server_default=func.now())
    updated_at         = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class PeakHolding(Base):
    """추세추종 전략 보유종목 (Peak / 모멘텀 / 벨류)
    ★ 컬럼 순서는 실제 DB 스키마와 일치 (ALTER TABLE로 추가된 순서 유지)
    """
    __tablename__ = "peak_holding"
    id            = Column(Integer, primary_key=True, index=True)
    stock_name    = Column(String, index=True)
    sector        = Column(String, nullable=True)
    sector_score  = Column(Integer, default=0)
    buy_price     = Column(Float, default=0.0)
    current_price = Column(Float, default=0.0)
    sell_price    = Column(Float, nullable=True)    # 기존 컬럼 (sold_price와 별개)
    quantity      = Column(Integer, default=0)
    entry_date    = Column(String, nullable=True)
    hold_days     = Column(Integer, default=0)
    profit_pct    = Column(Float, default=0.0)
    detected_at   = Column(String, nullable=True)
    updated_at    = Column(String, nullable=True)
    sold_at       = Column(String, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    strategy      = Column(String, default="peak", index=True)
    is_active     = Column(Integer, default=1)
    sold_price    = Column(Float, nullable=True)


class CashFlowData(Base):
    """현금흐름표 (DART 공시 기준, 연간/분기)"""
    __tablename__ = "cash_flow_data"

    id = Column(Integer, primary_key=True, index=True)
    stock_code = Column(String, index=True)
    year = Column(Integer)
    quarter = Column(Integer)   # 0 = 연간(사업보고서), 1~4 = 분기
    is_annual = Column(Boolean, default=False)

    operating_cf   = Column(Float, nullable=True)   # 영업활동현금흐름 (누적값 가능)
    investing_cf   = Column(Float, nullable=True)   # 투자활동현금흐름 (누적값 가능)
    financing_cf   = Column(Float, nullable=True)   # 재무활동현금흐름 (누적값 가능)
    capex          = Column(Float, nullable=True)   # 유형자산취득(설비투자)
    cash_end       = Column(Float, nullable=True)   # 기말현금및현금성자산
    depreciation   = Column(Float, nullable=True)   # 감가상각비
    # 분기 증분값 (_q): convert_cf_cumulative_to_quarterly.py 변환 결과
    operating_cf_q = Column(Float, nullable=True)   # 영업CF 분기 순값
    investing_cf_q = Column(Float, nullable=True)   # 투자CF 분기 순값
    financing_cf_q = Column(Float, nullable=True)   # 재무CF 분기 순값
    capex_q        = Column(Float, nullable=True)   # CapEx 분기 순값
    value_type     = Column(String, nullable=True)  # NULL|cumulative→derived|derived_q4

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("stock_code", "year", "quarter", "is_annual",
                         name="uq_cashflow_code_year_quarter_annual"),
    )


class PeakTrade(Base):
    """추세추종 전략 매매 이력"""
    __tablename__ = "peak_trade"
    id            = Column(Integer, primary_key=True, index=True)
    strategy      = Column(String, default="peak", index=True)
    stock_name    = Column(String, index=True)
    tx_type       = Column(String)   # "buy" | "sell"
    price         = Column(Float, default=0.0)
    quantity      = Column(Integer, default=0)
    amount        = Column(Float, default=0.0)
    profit_pct    = Column(Float, nullable=True)
    entry_date    = Column(String, nullable=True)
    tx_date       = Column(String, nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
