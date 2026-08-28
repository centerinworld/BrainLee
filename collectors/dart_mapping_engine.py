from __future__ import annotations

import json
import os
from typing import Any

_OVERRIDE_PATH = "/Applications/stock_dashboard/config/dart_account_overrides.json"
_cache: dict[str, Any] = {"mtime": 0.0, "data": {}}

# DART account_id → DB 필드 매핑
# 출처: config/dart_cf_account_catalog.json (top150 종목 DART API 전수조사, 2026-05-17)
#
# 규칙:
#   - 표준 IFRS/DART account_id는 여기서 매핑 (우선)
#   - 비표준(-표준계정코드 미사용-)은 dart_collector._CF_MAP 키워드로 낙하
#   - 종목별 예외는 config/dart_account_overrides.json 에 기록
DEFAULT_ACCOUNT_ID_MAP = {
    # ── 손익계산서 ──────────────────────────────────────────────────────────
    "ifrs-full_Revenue":                              "revenue",
    # operating_profit 우선순위:
    #   dart_OperatingIncomeLoss (한국 DART 표준, 영업이익) → 최우선
    #   ifrs-full_ProfitLossFromOperatingActivities → 다음
    #   ifrs-full_GrossProfit → LOW_PRIORITY fallback (_parse_fin_df에서 HIGH_PRIORITY가 있으면 덮어쓰기 금지)
    #   (SK하이닉스 사례: GrossProfit=58.69조가 OperatingIncomeLoss=47.21조를 덮어쓰는 버그 방지)
    "dart_OperatingIncomeLoss":                       "operating_profit",  # 한국 DART 표준
    "ifrs-full_ProfitLossFromOperatingActivities":    "operating_profit",
    "ifrs-full_GrossProfit":                          "operating_profit",  # LOW_PRIORITY fallback (HIGH_PRIORITY 없을 때만)
    "ifrs-full_ProfitLoss":                           "net_income",
    "dart_ProfitLoss":                                "net_income",
    # 추가 NI 관련 XBRL ID (비표준·구형 DART 공시 대응)
    "dart_NetIncome":                                 "net_income",
    "dart_ProfitLossForThePeriod":                    "net_income",
    "ifrs-full_BasicEarningsLossPerShare":            "eps",

    # ── 재무상태표 ──────────────────────────────────────────────────────────
    "ifrs-full_Assets":                               "total_assets",
    "ifrs-full_Liabilities":                          "total_liabilities",
    "ifrs-full_Equity":                               "total_equity",
    "ifrs-full_IssuedCapital":                        "capital_stock",
    "ifrs-full_CashAndCashEquivalents":               "cash",

    # ── 현금흐름표 — 합계행 ────────────────────────────────────────────────
    # OCF: ifrs-full_CashFlowsFromUsedInOperatingActivities
    #   실제 account_nm 목격: "영업활동현금흐름"(31), "영업활동으로 인한 현금흐름"(21),
    #                         "영업활동 현금흐름"(4), "영업활동으로 인한 순현금흐름"(4)
    "ifrs-full_CashFlowsFromUsedInOperatingActivities":  "operating_cf",

    # ICF: ifrs-full_CashFlowsFromUsedInInvestingActivities
    #   실제 account_nm 목격: "투자활동현금흐름"(29), "투자활동으로 인한 현금흐름"(21),
    #                         "투자활동 현금흐름"(4), "투자활동으로 인한 순현금흐름"(4)
    #   ⚠️ 두산(000150) 2021~2022: 이 ID가 "현금유입액"(단방향)에 오용됨
    #      → dart_collector._parse_cf_df 에서 "유입액"/"유출액" 포함 시 스킵 처리
    "ifrs-full_CashFlowsFromUsedInInvestingActivities":  "investing_cf",

    # FCF: ifrs-full_CashFlowsFromUsedInFinancingActivities
    #   실제 account_nm 목격: "재무활동현금흐름"(30), "재무활동으로 인한 현금흐름"(21),
    #                         "재무활동 현금흐름"(4), "재무활동으로 인한 순현금흐름"(4)
    "ifrs-full_CashFlowsFromUsedInFinancingActivities":  "financing_cf",

    # ── 현금흐름표 — CapEx (PP&E 우선, 무형자산은 PP&E 없을 때만) ──────────
    # 목격: "유형자산의 취득"(44건 표준 ID, 10건 비표준)
    # ⚠️ ifrs-full_PurchaseOfPropertyPlantAndEquipment 는 일부 기업에서 BS 행에 등장 →
    #    dart_collector._parse_cf_df 에서 현금흐름표 행만 허용
    # ⚠️ 우선순위: PP&E 계정 > 무형자산 계정 (_parse_cf_df 에서 CAPEX_PPE_IDS 체크)
    "ifrs-full_PurchaseOfPropertyPlantAndEquipment":             "capex",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "capex",
    "dart_PurchaseOfOtherPropertyPlantAndEquipment":             "capex",  # KT/비표준 PP&E
    "ifrs-full_PurchaseOfIntangibleAssets":                      "capex",
    "ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities": "capex",

    # ── 현금흐름표 — 기말현금 ──────────────────────────────────────────────
    # ★ 핵심 누락 ID (2026-05-17 추가)
    # 목격: "기말현금및현금성자산"(32), "기말의 현금및현금성자산"(17), "기말 현금및현금성자산"(8),
    #       "당기말 현금및현금성자산"(1), "기말의 현금"(2)
    "dart_CashAndCashEquivalentsAtEndOfPeriodCf":  "cash_end",

    # ── 현금흐름표 — 감가상각 ──────────────────────────────────────────────
    # ★ 핵심 누락 ID (2026-05-17 추가)
    # 목격: "감가상각비"(5), "유형자산감가상각비"(1)
    "dart_AdjustmentsForDepreciationExpense":      "depreciation",
    "ifrs-full_DepreciationAndAmortisationExpense": "depreciation",
    "ifrs-full_AdjustmentsForDepreciationAndAmortisationExpense": "depreciation",
    # ★ XBRL 확장 (2026-06-03 추가) — 기업별로 개별 계정으로 쪼개서 보고하는 경우
    # 주의: 유형/무형/사용권 각각 잡히면 합산 → _parse_cf_df에서 처리
    "ifrs-full_DepreciationPropertyPlantAndEquipment":       "depreciation",
    "ifrs-full_DepreciationRightofuseAssets":                "depreciation",
    "ifrs-full_AmortisationIntangibleAssets":                "depreciation",
    "ifrs-full_AdjustmentsForAmortisationExpense":           "depreciation",
    "ifrs-full_AdjustmentsForDepreciationExpense":           "depreciation",
    "dart_DepreciationAndAmortisation":                      "depreciation",
    "dart_AdjustmentsForDepreciationAndAmortisationExpense": "depreciation",
}

# operating_profit 저우선순위 fallback: HIGH_PRIORITY_OP가 이미 세팅된 경우 절대 덮어쓰지 않음
# (매출총이익=영업이익인 특수업종 — 유통/소매 일부 — 에만 사용)
HIGH_PRIORITY_OP_IDS = frozenset({
    "dart_OperatingIncomeLoss",
    "ifrs-full_ProfitLossFromOperatingActivities",
})
LOW_PRIORITY_OP_IDS = frozenset({
    "ifrs-full_GrossProfit",
})


def _load_overrides() -> dict[str, dict[str, str]]:
    if not os.path.exists(_OVERRIDE_PATH):
        return {}
    try:
        mtime = os.path.getmtime(_OVERRIDE_PATH)
        if _cache["mtime"] == mtime and _cache["data"]:
            return _cache["data"]
        with open(_OVERRIDE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            _cache["mtime"] = mtime
            _cache["data"] = data
            return data
    except Exception:
        return {}
    return {}


def resolve_field(stock_code: str, account_id: str, account_nm: str) -> str | None:
    """회사별 override → 전역 override → DEFAULT_ACCOUNT_ID_MAP 순으로 필드 결정."""
    if not account_id:
        return None
    overrides = _load_overrides()
    account_id = str(account_id).strip()
    stock_code = str(stock_code or "").strip().zfill(6)

    per_stock = overrides.get(stock_code, {})
    if isinstance(per_stock, dict) and account_id in per_stock:
        return per_stock[account_id]

    global_map = overrides.get("*", {})
    if isinstance(global_map, dict) and account_id in global_map:
        return global_map[account_id]

    return DEFAULT_ACCOUNT_ID_MAP.get(account_id)
