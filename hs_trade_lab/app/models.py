from __future__ import annotations

from sqlalchemy import DateTime, Float, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class DataSourceConfig(Base):
    __tablename__ = "data_source_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    provider_name: Mapped[str] = mapped_column(String(100), default="Public Data")
    base_url: Mapped[str] = mapped_column(String(500), default="")
    endpoint_path: Mapped[str] = mapped_column(String(300), default="")
    api_key: Mapped[str] = mapped_column(String(300), default="")
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[str] = mapped_column(String(20), default="manual")
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class HSCodeCompanyMap(Base):
    __tablename__ = "hs_code_company_map"
    __table_args__ = (
        UniqueConstraint("hs_code", "stock_code", name="uq_hs_stock"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hs_code: Mapped[str] = mapped_column(String(20), index=True)
    hs_name: Mapped[str] = mapped_column(String(200), default="")
    stock_code: Mapped[str] = mapped_column(String(20), index=True)
    stock_name: Mapped[str] = mapped_column(String(120), default="")
    sector_name: Mapped[str] = mapped_column(String(120), default="")
    flow_type: Mapped[str] = mapped_column(String(20), default="export")
    match_type: Mapped[str] = mapped_column(String(20), default="company")
    mapping_status: Mapped[str] = mapped_column(String(20), default="provisional")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TradeSeriesCache(Base):
    __tablename__ = "trade_series_cache"
    __table_args__ = (
        UniqueConstraint("hs_code", "period_ym", "flow_type", name="uq_hs_period_flow"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hs_code: Mapped[str] = mapped_column(String(20), index=True)
    period_ym: Mapped[str] = mapped_column(String(7), index=True)
    flow_type: Mapped[str] = mapped_column(String(20), default="total")
    export_value: Mapped[float] = mapped_column(Float, default=0.0)
    import_value: Mapped[float] = mapped_column(Float, default=0.0)
    trade_balance: Mapped[float] = mapped_column(Float, default=0.0)
    source_name: Mapped[str] = mapped_column(String(120), default="")
    raw_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class HSCodeMaster(Base):
    __tablename__ = "hs_codes"

    hs_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    description: Mapped[str] = mapped_column(String(300), default="")
    category_label: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class CompanyMaster(Base):
    __tablename__ = "companies"

    company_code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    market: Mapped[str] = mapped_column(String(40), default="")
    sector_name: Mapped[str] = mapped_column(String(120), default="")
    is_listed: Mapped[str] = mapped_column(String(10), default="Y")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SectorPreset(Base):
    __tablename__ = "sector_preset"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sector_key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class HSSectorMap(Base):
    __tablename__ = "hs_sector_map"
    __table_args__ = (
        UniqueConstraint("hs_code", "sector_key", name="uq_hs_sector"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    hs_code: Mapped[str] = mapped_column(String(20), index=True)
    hs_name: Mapped[str] = mapped_column(String(200), default="")
    sector_key: Mapped[str] = mapped_column(String(40), index=True)
    display_name: Mapped[str] = mapped_column(String(200), default="")
    mapping_status: Mapped[str] = mapped_column(String(20), default="provisional")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class CustomsMonthlyRecord(Base):
    __tablename__ = "customs_monthly_record"
    __table_args__ = (
        UniqueConstraint("row_key", name="uq_customs_monthly_row_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    endpoint: Mapped[str] = mapped_column(String(40), index=True)
    period_ym: Mapped[str] = mapped_column(String(7), index=True)
    hs_code: Mapped[str] = mapped_column(String(20), default="", index=True)
    hs_name: Mapped[str] = mapped_column(String(200), default="")
    country_code: Mapped[str] = mapped_column(String(10), default="", index=True)
    country_name: Mapped[str] = mapped_column(String(120), default="")
    sido_code: Mapped[str] = mapped_column(String(10), default="", index=True)
    sido_name: Mapped[str] = mapped_column(String(120), default="")
    imex_type_code: Mapped[str] = mapped_column(String(10), default="", index=True)
    nature_code: Mapped[str] = mapped_column(String(30), default="", index=True)
    nature_name: Mapped[str] = mapped_column(String(200), default="")
    unified_nature_code: Mapped[str] = mapped_column(String(30), default="", index=True)
    unified_nature_name: Mapped[str] = mapped_column(String(200), default="")
    export_value: Mapped[float] = mapped_column(Float, default=0.0)
    import_value: Mapped[float] = mapped_column(Float, default=0.0)
    trade_balance: Mapped[float] = mapped_column(Float, default=0.0)
    export_weight: Mapped[float] = mapped_column(Float, default=0.0)
    import_weight: Mapped[float] = mapped_column(Float, default=0.0)
    weight_total: Mapped[float] = mapped_column(Float, default=0.0)
    export_count: Mapped[float] = mapped_column(Float, default=0.0)
    import_count: Mapped[float] = mapped_column(Float, default=0.0)
    line_count: Mapped[float] = mapped_column(Float, default=0.0)
    row_key: Mapped[str] = mapped_column(String(500), index=True)
    source_file: Mapped[str] = mapped_column(String(500), default="")
    raw_json: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SyncRun(Base):
    __tablename__ = "sync_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class InsightCache(Base):
    __tablename__ = "insight_cache"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_key", name="uq_insight_scope"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[str] = mapped_column(String(30), index=True)
    scope_key: Mapped[str] = mapped_column(String(60), index=True)
    summary_text: Mapped[str] = mapped_column(Text, default="")
    model_name: Mapped[str] = mapped_column(String(80), default="")
    detail_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[str] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[str] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
