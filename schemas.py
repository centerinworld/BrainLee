from pydantic import BaseModel, validator, Field
from typing import Any, List, Optional
from datetime import datetime

# 규칙 4: Pydantic 모델에서 기본값(Default) 및 유효성 검사를 철저히 함

class FinancialIngest(BaseModel):
    stock_code: str
    year: int
    quarter: int
    revenue: Optional[float] = 0.0
    operating_profit: Optional[float] = 0.0
    net_income: Optional[float] = 0.0
    total_assets: Optional[float] = 0.0
    total_liabilities: Optional[float] = 0.0
    total_equity: Optional[float] = 0.0
    capital_stock: Optional[float] = 0.0
    eps: Optional[float] = 0.0
    bps: Optional[float] = 0.0
    dps: Optional[float] = 0.0
    cash: Optional[float] = 0.0
    total_shares: Optional[float] = 0.0
    depreciation_amortization: Optional[float] = 0.0
    is_annual: bool = False

class CashFlowIngest(BaseModel):
    stock_code: str
    year: int
    quarter: int
    is_annual: bool = False
    operating_cf:  Optional[float] = None
    investing_cf:  Optional[float] = None
    financing_cf:  Optional[float] = None
    capex:         Optional[float] = None
    cash_end:      Optional[float] = None
    depreciation:  Optional[float] = None


class PriceData(BaseModel):
    date: Any  # datetime 또는 'YYYY-MM-DD' 문자열 모두 허용
    open: float
    high: float
    low: float
    close: float
    volume: float
    inst_net_buy: Optional[float] = 0.0
    frn_net_buy: Optional[float] = 0.0

    @validator('date', pre=True, always=True)
    def normalize_date(cls, v):
        from datetime import datetime as _dt, date as _d
        if isinstance(v, str):
            return _dt.strptime(v[:10], '%Y-%m-%d')
        if isinstance(v, _d) and not isinstance(v, _dt):
            return _dt.combine(v, _dt.min.time())
        return v

class PriceIngest(BaseModel):
    stock_code: str
    prices: List[PriceData]

class SectorMapping(BaseModel):
    sector_name: str
    stock_codes: List[str]

# [설계 수정 ⑥] ChatRequest는 현재 main.py에 대응하는 엔드포인트가 없는 미완성 스키마.
# 향후 대화형 쿼리 API(/api/commands/query 등) 구현 시 사용 예정.
# 현재는 임시 보류 상태로 주석 처리하여 혼란을 방지.
# class ChatRequest(BaseModel):
#     query: str
#     stock_code: str
#     data_type: str
#     last_date: datetime
