from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DataSourceConfigIn(BaseModel):
    provider_name: str = "Public Data"
    base_url: str = ""
    endpoint_path: str = ""
    api_key: str = ""
    params_json: str = "{}"
    enabled: str = "manual"


class DataSourceConfigOut(DataSourceConfigIn):
    masked_api_key: str = ""


class CompanyReference(BaseModel):
    stock_code: str
    stock_name: str
    market: str = ""
    sector_large: str = ""
    sector_mid: str = ""
    sector_small: str = ""


class HSCodeMapIn(BaseModel):
    hs_code: str = Field(min_length=4, max_length=20)
    hs_name: str = ""
    stock_code: str
    stock_name: str = ""
    sector_name: str = ""
    match_type: str = "company"
    mapping_status: str = "provisional"
    confidence: float = 0.5
    note: str = ""


class HSCodeMapOut(HSCodeMapIn):
    id: int


class HSCodeMapPatch(BaseModel):
    """부분 수정용 — 지정된 필드만 변경 (기업 매핑 이동/재분류 도구)."""

    hs_code: str | None = Field(default=None, min_length=4, max_length=20)
    hs_name: str | None = None
    stock_code: str | None = None
    stock_name: str | None = None
    sector_name: str | None = None
    flow_type: str | None = None
    match_type: str | None = None
    mapping_status: str | None = None
    confidence: float | None = None
    note: str | None = None


class SectorPresetOut(BaseModel):
    sector_key: str
    label: str
    description: str
    sort_order: int


class HSSectorMapIn(BaseModel):
    hs_code: str = Field(min_length=4, max_length=20)
    hs_name: str = ""
    sector_key: str
    display_name: str = ""
    mapping_status: str = "provisional"
    note: str = ""


class HSSectorMapOut(HSSectorMapIn):
    id: int


class TradeFetchRequest(BaseModel):
    hs_code: str
    start_ym: str
    end_ym: str
    flow_type: str = "total"
    overwrite: bool = True


class TradeSeriesPoint(BaseModel):
    period_ym: str
    export_value: float
    import_value: float
    trade_balance: float
    source_name: str = ""


class TrendSummary(BaseModel):
    hs_code: str
    points: list[TradeSeriesPoint]
    export_latest: float = 0.0
    export_yoy: float | None = None
    import_latest: float = 0.0
    import_yoy: float | None = None
    trade_balance_latest: float = 0.0
    mapped_companies: list[HSCodeMapOut]
    inference: str


class FeatureItem(BaseModel):
    name: str
    item_type: str
    key: str
    export_latest: float = 0.0
    export_yoy: float | None = None
    feature_score: float = 0.0
    extra: dict[str, Any] = {}


class SectorDashboardPoint(BaseModel):
    period_ym: str
    export_value: float
    import_value: float
    trade_balance: float


class SectorDashboardResponse(BaseModel):
    sector_key: str
    label: str
    description: str
    points: list[SectorDashboardPoint]
    hs_mappings: list[dict[str, Any]]
    top_hs_codes: list[dict[str, Any]]
    mapped_companies: list[dict[str, Any]]


class AIInsightResponse(BaseModel):
    scope_type: str
    scope_key: str
    summary_text: str


class HSCodeSuggestRequest(BaseModel):
    product_name: str
    api_key: str | None = None
    model: str = "gemini-2.5-flash"


class HSCodeSuggestResponse(BaseModel):
    provider: str = "gemini"
    raw_text: str
    parsed_hs_code: str | None = None
    parsed_description: str | None = None


class GenericMessage(BaseModel):
    message: str
    detail: Any | None = None
