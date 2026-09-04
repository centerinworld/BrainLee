#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"


SECTOR_TO_INDICATORS = {
    "반도체": ["public:23:4", "public:23:5", "public:23:6", "public:23:12", "public:23:32", "public:23:40", "cafe:11:4128"],
    "전력기기": ["public:23:36"],
    "방산/항공": ["public:23:37", "cafe:34:7616"],
    "건설/건자재": ["public:23:38", "public:23:39", "cafe:11:4247", "cafe:11:3475"],
    "정유/화학": ["public:23:14", "public:23:17", "public:23:28", "public:23:35", "public:23:41", "public:23:42"],
    "철강/비철": ["public:23:8", "public:23:18", "public:23:19", "public:23:20", "public:23:34"],
    "자동차": ["public:23:1", "public:23:2", "public:23:27", "public:23:33", "cafe:11:2805"],
    "소비재": ["public:23:9", "public:23:29", "public:23:30", "public:23:31", "cafe:34:7690", "cafe:34:7633"],
    "바이오/의료": ["public:23:10", "public:23:24", "public:23:25", "public:23:26"],
    "게임/미디어": ["cafe:11:2716", "cafe:11:2668", "cafe:11:2650"],
    "통신": ["cafe:34:7611"],
    "조선/기계": ["public:23:7", "public:23:21", "public:23:22", "public:23:23", "public:23:43"],
}

MACRO_SECTOR_TO_INDICATORS = {
    "시장 레짐": [
        "macro:US_VIX",
        "macro:US_HY_SPREAD",
        "macro:US_BAA_SPREAD",
        "macro:US_NFCI",
        "macro:US_DXY",
        "macro:US_SP500",
        "macro:COMM_GOLD",
        "macro:KR_KOSPI",
        "macro:KR_KOSPI_KOSIS",
    ],
    "환율/수출주": [
        "macro:KR_USD_KRW",
        "macro:US_DXY",
        "macro:CN_USD_CNY",
        "macro:JP_USD_JPY",
        "macro:EU_EUR_USD",
        "macro:KR_EXPORT",
        "macro:KR_TRADE_BALANCE",
        "macro:KR_CURRENT_ACCOUNT",
        "macro:KR_EXPORT_IMPORT_PRICE_RATIO",
    ],
    "반도체": [
        "macro:KR_EXPORT",
        "macro:KR_TRADE_BALANCE",
        "macro:COMM_COPPER",
        "macro:US_DXY",
        "macro:KR_INDUSTRIAL_PROD",
        "macro:KR_INVENTORY_CYCLE",
        "macro:US_CLI_OECD",
        "macro:CN_CLI_OECD",
    ],
    "전력기기": [
        "macro:COMM_COPPER",
        "macro:KR_EXPORT",
        "macro:KR_TRADE_BALANCE",
    ],
    "정유/화학": [
        "macro:COMM_OIL_WTI",
        "macro:COMM_OIL_BRENT",
        "macro:COMM_NATURAL_GAS",
        "macro:OIL_STOCKS_EX_SPR",
        "macro:OIL_STOCKS_TOTAL",
    ],
    "철강/비철": [
        "macro:COMM_COPPER",
        "macro:COMM_OIL_WTI",
        "macro:KR_CONSTRUCTION_ORDER",
        "macro:KR_INDUSTRIAL_PROD",
        "macro:KR_MACHINERY_SHIP",
        "macro:US_HOUSING_START",
    ],
    "건설/건자재": [
        "macro:KR_CONSTRUCTION_ORDER",
        "macro:KR_CONSTRUCTION_OUTPUT",
        "macro:KR_HOUSING_PRICE",
        "macro:KR_MACHINERY_SHIP",
        "macro:KR_CP_YIELD",
        "macro:US_HOUSING_START",
    ],
    "금융": [
        "macro:KR_BASE_RATE",
        "macro:KR_YIELD_SPREAD",
        "macro:KR_CP_YIELD",
        "macro:US_FED_RATE",
        "macro:US_10Y_YIELD",
        "macro:US_10Y_YIELD_YH",
        "macro:US_2Y_YIELD",
        "macro:US_3M_YIELD",
        "macro:US_30Y_YIELD",
        "macro:US_10Y_BREAKEVEN",
        "macro:US_BAA_SPREAD",
        "macro:US_NFCI",
    ],
    "리츠/부동산": [
        "macro:KR_BASE_RATE",
        "macro:KR_HOUSING_PRICE",
        "macro:US_10Y_YIELD",
        "macro:US_HOUSING_START",
    ],
    "음식료": [
        "macro:GLOBAL_FOOD_PRICE",
        "macro:GLOBAL_FOOD_CEREALS",
        "macro:GLOBAL_FOOD_DAIRY",
        "macro:GLOBAL_FOOD_MEAT",
        "macro:GLOBAL_FOOD_OILS",
        "macro:GLOBAL_FOOD_SUGAR",
        "macro:COMM_WHEAT",
        "macro:KR_CPI",
        "macro:KR_RETAIL_SALES",
    ],
    "자동차": [
        "macro:KR_USD_KRW",
        "macro:US_RETAIL_SALES",
        "macro:US_CONSUMER_CONF",
        "macro:US_DXY",
        "macro:KR_RETAIL_SALES",
        "macro:JP_USD_JPY",
    ],
    "조선/해운": [
        "macro:KR_EXPORT",
        "macro:KR_TRADE_BALANCE",
        "macro:COMM_OIL_WTI",
        "macro:COMM_OIL_BRENT",
    ],
    "바이오": [
        "macro:US_HY_SPREAD",
        "macro:US_NFCI",
        "macro:US_VIX",
        "macro:US_10Y_YIELD",
        "macro:US_10Y_YIELD_YH",
    ],
    "SW/AI": [
        "macro:US_HY_SPREAD",
        "macro:US_NFCI",
        "macro:US_VIX",
        "macro:US_10Y_YIELD",
        "macro:US_10Y_YIELD_YH",
    ],
    "기계": [
        "macro:KR_MACHINERY_SHIP",
        "macro:KR_INDUSTRIAL_PROD",
        "macro:KR_CONSTRUCTION_ORDER",
        "macro:US_HOUSING_START",
    ],
    "유통": [
        "macro:KR_RETAIL_SALES",
        "macro:KR_CONSUMER_IMPORT",
        "macro:US_RETAIL_SALES",
        "macro:US_CONSUMER_CONF",
    ],
    "여행/레저": [
        "macro:JP_USD_JPY",
        "macro:CN_USD_CNY",
        "macro:KR_RETAIL_SALES",
        "macro:US_CONSUMER_CONF",
    ],
    "글로벌 경기/무역": [
        "macro:GLOBAL_GDP_GROWTH",
        "macro:GLOBAL_TRADE_VOL",
        "macro:GLOBAL_EXPORT_VOL",
        "macro:GLOBAL_IMPORT_VOL",
        "macro:GLOBAL_INFLATION",
        "macro:US_GDP_GROWTH",
        "macro:CN_GDP_GROWTH",
        "macro:EU_GDP_GROWTH",
        "macro:KR_GDP_GROWTH",
    ],
    "한국 경기": [
        "macro:KR_CLI_LEADING",
        "macro:KR_CLI_COINCIDENT",
        "macro:KR_CLI_LAGGING",
        "macro:KR_ECON_SENTIMENT",
        "macro:KR_INDUSTRIAL_PROD",
        "macro:KR_SERVICE_PROD",
        "macro:KR_CPI_SERVICE",
        "macro:KR_IMPORT",
        "macro:KR_IMPORT_REAL",
        "macro:KR_DOMESTIC_SHIP",
        "macro:KR_INVENTORY",
        "macro:KR_EMPLOYMENT",
        "macro:KR_NONFARM_EMPLOY",
        "macro:KR_UNEMPLOYMENT",
        "macro:KR_M2",
    ],
    "미국 매크로": [
        "macro:US_GDP_GROWTH",
        "macro:US_GDP_GROWTH_WEO",
        "macro:US_CPI",
        "macro:US_CORE_CPI",
        "macro:US_PCE",
        "macro:US_NONFARM",
        "macro:US_UNEMPLOYMENT",
        "macro:US_M2",
        "macro:US_CLI_OECD",
    ],
    "중국 매크로": [
        "macro:CN_GDP_GROWTH",
        "macro:CN_GDP_GROWTH_WEO",
        "macro:CN_CPI",
        "macro:CN_CLI_OECD",
        "macro:CN_USD_CNY",
    ],
    "유럽/일본 매크로": [
        "macro:EU_GDP_GROWTH",
        "macro:EU_GDP_GROWTH_WEO",
        "macro:EU_CPI",
        "macro:EU_UNEMPLOYMENT",
        "macro:EU_ECB_RATE",
        "macro:EU_DAX",
        "macro:EU_FTSE",
        "macro:JP_GDP_GROWTH_WEO",
        "macro:JP_CPI",
        "macro:JP_CLI_OECD",
        "macro:JP_BOJ_RATE",
        "macro:JP_NIKKEI",
    ],
}

MACRO_SECTOR_DIRECTION_RULES = [
    ("macro:COMM_COPPER", "전력기기", "higher_is_good", "구리 가격 상승은 전선/전력기기 판가 상승과 수주 사이클에 우호적일 수 있다."),
    ("macro:COMM_COPPER", "철강/비철", "higher_is_good", "구리 가격 상승은 비철금속 판가/재고평가와 광산·제련 업황에 우호적으로 해석한다."),
    ("macro:COMM_COPPER", "2차전지", "higher_is_bad", "구리 가격 상승은 동박/배터리 소재 원가 부담으로 작용할 수 있어 부정적으로 해석한다."),
    ("macro:COMM_COPPER", "음극재/소재", "higher_is_bad", "구리 가격 상승은 소재 원가 부담을 키울 수 있어 부정적으로 해석한다."),
    ("macro:COMM_OIL_WTI", "정유/화학", "ambiguous", "유가 상승은 판가에는 우호적일 수 있지만 원재료 비용과 정제마진 해석이 갈려 주의로 처리한다."),
    ("macro:COMM_OIL_BRENT", "정유/화학", "ambiguous", "브렌트유 상승은 판가와 원가가 함께 움직여 정제마진 확인 전까지 주의로 처리한다."),
    ("macro:COMM_NATURAL_GAS", "전력/에너지", "higher_is_bad", "천연가스 가격 상승은 발전/LNG 원가 부담으로 해석한다."),
    ("macro:COMM_NATURAL_GAS", "정유/화학", "higher_is_bad", "천연가스 가격 상승은 에너지·화학 원가 부담으로 해석한다."),
    ("macro:OIL_STOCKS_EX_SPR", "정유/화학", "higher_is_bad", "원유재고 증가는 유가/정제마진 부담 신호로 우선 해석한다."),
    ("macro:OIL_STOCKS_TOTAL", "정유/화학", "higher_is_bad", "원유재고 증가는 유가/정제마진 부담 신호로 우선 해석한다."),
    ("macro:KR_USD_KRW", "반도체", "higher_is_good", "원/달러 상승은 수출주의 원화 환산 매출에 우호적일 수 있다."),
    ("macro:KR_USD_KRW", "자동차", "higher_is_good", "원/달러 상승은 자동차 수출주의 원화 환산 매출에 우호적일 수 있다."),
    ("macro:KR_USD_KRW", "조선/해운", "higher_is_good", "원/달러 상승은 달러 매출 비중이 높은 조선/해운에 우호적으로 해석한다."),
    ("macro:KR_USD_KRW", "전력기기", "higher_is_good", "원/달러 상승은 수출 비중이 높은 전력기기 업체의 환산 매출에 우호적일 수 있다."),
    ("macro:KR_USD_KRW", "철강/비철", "ambiguous", "환율 상승은 수출에는 우호적이나 원재료 수입 부담도 있어 주의로 처리한다."),
    ("macro:US_DXY", "반도체", "ambiguous", "달러 강세는 환산 매출과 글로벌 유동성 효과가 엇갈려 주의로 처리한다."),
    ("macro:US_DXY", "자동차", "ambiguous", "달러 강세는 환율 수혜와 글로벌 소비 둔화 우려가 섞여 주의로 처리한다."),
    ("macro:KR_EXPORT", "반도체", "higher_is_good", "한국 수출 증가는 반도체/수출주 상위 업황에 우호적이다."),
    ("macro:KR_EXPORT", "자동차", "higher_is_good", "한국 수출 증가는 자동차 수출 업황에 우호적이다."),
    ("macro:KR_EXPORT", "조선/해운", "higher_is_good", "한국 수출 증가는 조선/해운 물동량과 수출 업황에 우호적이다."),
    ("macro:KR_EXPORT", "전력기기", "higher_is_good", "한국 수출 증가는 전력기기 수출 업황에 우호적이다."),
    ("macro:KR_TRADE_BALANCE", "반도체", "higher_is_good", "무역수지 개선은 수출주와 원화 수급 레짐에 우호적이다."),
    ("macro:KR_TRADE_BALANCE", "정유/화학", "higher_is_good", "무역수지 개선은 수출/원가 부담 완화 측면에서 우호적으로 해석한다."),
    ("macro:KR_CURRENT_ACCOUNT", "금융", "higher_is_good", "경상수지 개선은 원화/외국인 수급 안정 측면에서 금융주에 우호적일 수 있다."),
    ("macro:KR_CONSTRUCTION_ORDER", "건설/건자재", "higher_is_good", "건설수주 증가는 건설/건자재 수요 선행 신호로 우호적이다."),
    ("macro:KR_CONSTRUCTION_ORDER", "철강/비철", "higher_is_good", "건설수주 증가는 건설용 철강재 수요에 우호적이다."),
    ("macro:KR_CONSTRUCTION_OUTPUT", "건설/건자재", "higher_is_good", "건설기성 증가는 건설/건자재 실수요 동행 신호로 우호적이다."),
    ("macro:KR_HOUSING_PRICE", "건설/건자재", "higher_is_good", "주택가격 상승은 주택 경기와 건설 심리에 우호적이다."),
    ("macro:KR_HOUSING_PRICE", "리츠/부동산", "higher_is_good", "주택가격 상승은 부동산 자산가치와 담보 여건에 우호적이다."),
    ("macro:US_HOUSING_START", "건설/건자재", "higher_is_good", "미국 주택착공 증가는 글로벌 건자재/기계 수요에 우호적이다."),
    ("macro:KR_BASE_RATE", "금융", "ambiguous", "기준금리 상승은 NIM에는 우호적일 수 있으나 신용비용/할인율 부담도 있어 주의로 처리한다."),
    ("macro:KR_BASE_RATE", "리츠/부동산", "higher_is_bad", "기준금리 상승은 리츠/부동산 할인율과 조달비용 부담으로 부정적이다."),
    ("macro:KR_YIELD_SPREAD", "금융", "higher_is_good", "장단기금리차 확대는 은행 NIM과 경기 기대에 우호적으로 해석한다."),
    ("macro:US_10Y_YIELD", "리츠/부동산", "higher_is_bad", "미국 10년 금리 상승은 글로벌 할인율 상승으로 리츠/부동산에 부정적이다."),
    ("macro:US_10Y_YIELD", "SW/AI", "higher_is_bad", "미국 10년 금리 상승은 성장주 할인율 부담으로 부정적이다."),
    ("macro:US_HY_SPREAD", "바이오", "higher_is_bad", "하이일드 스프레드 확대는 자금조달 민감 성장 업종에 부정적이다."),
    ("macro:US_HY_SPREAD", "SW/AI", "higher_is_bad", "하이일드 스프레드 확대는 고멀티플 성장주 위험선호에 부정적이다."),
    ("macro:US_VIX", "바이오", "higher_is_bad", "VIX 상승은 위험회피로 고베타 성장 업종에 부정적이다."),
    ("macro:US_VIX", "SW/AI", "higher_is_bad", "VIX 상승은 위험회피로 고멀티플 성장주에 부정적이다."),
    ("macro:GLOBAL_FOOD_PRICE", "음식료", "higher_is_bad", "식품가격 상승은 음식료 원가 부담으로 우선 해석한다."),
    ("macro:GLOBAL_FOOD_CEREALS", "음식료", "higher_is_bad", "곡물가격 상승은 제분/제과/라면/사료 원가 부담으로 해석한다."),
    ("macro:GLOBAL_FOOD_OILS", "음식료", "higher_is_bad", "유지류 가격 상승은 식품 제조 원가 부담으로 해석한다."),
    ("macro:COMM_WHEAT", "음식료", "higher_is_bad", "소맥 가격 상승은 제분/제과/라면/사료 원가 부담으로 해석한다."),
    ("macro:US_RETAIL_SALES", "소비재", "higher_is_good", "미국 소매판매 증가는 소비재 수요에 우호적이다."),
    ("macro:US_CONSUMER_CONF", "소비재", "higher_is_good", "미국 소비자신뢰 개선은 소비재 수요에 우호적이다."),
    ("macro:US_CONSUMER_CONF", "자동차", "higher_is_good", "미국 소비자신뢰 개선은 자동차 수요에 우호적이다."),
    ("macro:US_BAA_SPREAD", "시장 레짐", "higher_is_bad", "BAA 스프레드 확대는 신용위험 확대와 위험자산 회피 신호로 해석한다."),
    ("macro:US_BAA_SPREAD", "바이오", "higher_is_bad", "BAA 스프레드 확대는 자금조달 민감 성장 업종에 부정적이다."),
    ("macro:US_BAA_SPREAD", "SW/AI", "higher_is_bad", "BAA 스프레드 확대는 고멀티플 성장주의 할인율/위험선호에 부정적이다."),
    ("macro:US_NFCI", "시장 레짐", "higher_is_bad", "NFCI 상승은 금융여건 긴축을 뜻해 위험자산에 부정적으로 해석한다."),
    ("macro:US_NFCI", "바이오", "higher_is_bad", "금융여건 긴축은 자금조달 민감 바이오 업종에 부정적이다."),
    ("macro:US_NFCI", "SW/AI", "higher_is_bad", "금융여건 긴축은 고멀티플 성장주에 부정적이다."),
    ("macro:US_NFCI", "금융", "ambiguous", "금융여건 긴축은 예대마진과 신용비용 효과가 엇갈려 주의로 처리한다."),
    ("macro:US_SP500", "시장 레짐", "higher_is_good", "S&P500 상승은 글로벌 위험선호 개선으로 해석한다."),
    ("macro:KR_KOSPI", "시장 레짐", "higher_is_good", "KOSPI 상승은 국내 위험선호 개선으로 해석한다."),
    ("macro:KR_KOSPI_KOSIS", "시장 레짐", "higher_is_good", "KOSPI 상승은 국내 위험선호 개선으로 해석한다."),
    ("macro:COMM_GOLD", "시장 레짐", "ambiguous", "금 가격 상승은 인플레 헤지와 위험회피가 섞여 있어 단독 신호로 쓰지 않는다."),
    ("macro:KR_EXPORT", "환율/수출주", "higher_is_good", "한국 수출 증가는 수출주 전반의 상위 업황에 우호적이다."),
    ("macro:KR_TRADE_BALANCE", "환율/수출주", "higher_is_good", "무역수지 개선은 수출/환율 레짐에 우호적이다."),
    ("macro:KR_CURRENT_ACCOUNT", "환율/수출주", "higher_is_good", "경상수지 개선은 원화 안정과 외국인 수급에 우호적이다."),
    ("macro:KR_EXPORT_IMPORT_PRICE_RATIO", "환율/수출주", "higher_is_good", "수출입물가비율 개선은 교역조건 개선 신호로 해석한다."),
    ("macro:CN_USD_CNY", "환율/수출주", "ambiguous", "위안화 약세는 가격 경쟁과 중국 수요 둔화가 엇갈려 주의로 처리한다."),
    ("macro:JP_USD_JPY", "환율/수출주", "ambiguous", "엔화 약세는 일본 경쟁사 가격경쟁과 수요 효과가 섞여 주의로 처리한다."),
    ("macro:EU_EUR_USD", "환율/수출주", "ambiguous", "유로/달러 변동은 지역별 매출·원가 노출에 따라 달라 주의로 처리한다."),
    ("macro:KR_INDUSTRIAL_PROD", "반도체", "higher_is_good", "산업생산 개선은 제조업 업황과 반도체 수요 회복 보조 신호다."),
    ("macro:KR_INDUSTRIAL_PROD", "기계", "higher_is_good", "산업생산 개선은 기계/설비투자 수요에 우호적이다."),
    ("macro:KR_INDUSTRIAL_PROD", "철강/비철", "higher_is_good", "산업생산 개선은 철강/비철 실수요에 우호적이다."),
    ("macro:KR_INVENTORY_CYCLE", "반도체", "ambiguous", "재고순환은 업종별 가격·출하 확인 전까지 단독 매수/매도 신호로 쓰지 않는다."),
    ("macro:KR_MACHINERY_SHIP", "기계", "higher_is_good", "기계류/선박 지표 개선은 기계·조선 수주/출하 사이클에 우호적이다."),
    ("macro:KR_MACHINERY_SHIP", "철강/비철", "higher_is_good", "기계류/선박 지표 개선은 후판·강재 수요에 우호적이다."),
    ("macro:KR_MACHINERY_SHIP", "건설/건자재", "higher_is_good", "기계류 지표 개선은 설비/건설 경기 보조 신호로 해석한다."),
    ("macro:KR_CP_YIELD", "금융", "ambiguous", "CP 금리 상승은 수익률과 신용위험 효과가 엇갈려 주의로 처리한다."),
    ("macro:KR_CP_YIELD", "건설/건자재", "higher_is_bad", "CP 금리 상승은 건설/건자재 조달비용과 신용위험 부담이다."),
    ("macro:US_FED_RATE", "금융", "ambiguous", "미국 기준금리 상승은 마진과 신용비용 효과가 엇갈려 주의로 처리한다."),
    ("macro:US_FED_RATE", "SW/AI", "higher_is_bad", "미국 기준금리 상승은 성장주 할인율 부담으로 해석한다."),
    ("macro:US_FED_RATE", "바이오", "higher_is_bad", "미국 기준금리 상승은 자금조달 민감 바이오에 부정적이다."),
    ("macro:US_10Y_BREAKEVEN", "금융", "ambiguous", "기대인플레이션 상승은 금리/마진과 경기 부담이 섞여 주의로 처리한다."),
    ("macro:US_10Y_BREAKEVEN", "SW/AI", "higher_is_bad", "기대인플레이션 상승은 할인율 부담으로 성장주에 부정적이다."),
    ("macro:US_10Y_BREAKEVEN", "바이오", "higher_is_bad", "기대인플레이션 상승은 할인율과 자금조달 부담으로 바이오에 부정적이다."),
    ("macro:US_2Y_YIELD", "금융", "ambiguous", "단기금리 상승은 NIM과 경기 부담 효과가 엇갈려 주의로 처리한다."),
    ("macro:US_3M_YIELD", "금융", "ambiguous", "단기금리 상승은 NIM과 경기 부담 효과가 엇갈려 주의로 처리한다."),
    ("macro:US_30Y_YIELD", "금융", "ambiguous", "장기금리 상승은 보험/은행에 우호적일 수 있으나 할인율 부담도 있어 주의로 처리한다."),
    ("macro:GLOBAL_FOOD_DAIRY", "음식료", "higher_is_bad", "유제품 가격 상승은 음식료 원가 부담으로 해석한다."),
    ("macro:GLOBAL_FOOD_MEAT", "음식료", "higher_is_bad", "육류 가격 상승은 식품/외식 원가 부담으로 해석한다."),
    ("macro:GLOBAL_FOOD_SUGAR", "음식료", "higher_is_bad", "설탕 가격 상승은 제과/음료 원가 부담으로 해석한다."),
    ("macro:KR_CPI", "음식료", "ambiguous", "소비자물가 상승은 판가 전가와 수요 둔화가 섞여 주의로 처리한다."),
    ("macro:KR_RETAIL_SALES", "유통", "higher_is_good", "소매판매 개선은 유통/소비재 수요에 우호적이다."),
    ("macro:KR_RETAIL_SALES", "자동차", "higher_is_good", "소매판매 개선은 내수 소비/자동차 수요 보조 신호다."),
    ("macro:KR_RETAIL_SALES", "여행/레저", "higher_is_good", "소매판매 개선은 소비심리와 레저 수요에 우호적이다."),
    ("macro:KR_CONSUMER_IMPORT", "유통", "higher_is_good", "소비재 수입 증가는 내수 소비와 유통 수요 보조 신호다."),
    ("macro:US_CLI_OECD", "반도체", "higher_is_good", "미국 경기선행지수 개선은 IT/반도체 수요 회복 보조 신호다."),
    ("macro:CN_CLI_OECD", "반도체", "higher_is_good", "중국 경기선행지수 개선은 IT/반도체 수요 회복 보조 신호다."),
    ("macro:US_10Y_YIELD_YH", "SW/AI", "higher_is_bad", "미국 10년 금리 상승은 성장주 할인율 부담으로 부정적이다."),
    ("macro:US_10Y_YIELD", "금융", "ambiguous", "미국 10년 금리 상승은 금융주 수익률과 경기 부담 효과가 엇갈려 주의로 처리한다."),
    ("macro:US_10Y_YIELD_YH", "금융", "ambiguous", "미국 10년 금리 상승은 금융주 수익률과 경기 부담 효과가 엇갈려 주의로 처리한다."),
    ("macro:US_BAA_SPREAD", "금융", "higher_is_bad", "BAA 스프레드 확대는 신용비용과 위험회피 확대로 금융주에 부정적으로 해석한다."),
    ("macro:KR_CONSTRUCTION_ORDER", "기계", "higher_is_good", "건설수주 증가는 건설기계/설비 수요에 우호적이다."),
    ("macro:US_HOUSING_START", "기계", "higher_is_good", "미국 주택착공 증가는 건설기계와 건자재 수요에 우호적이다."),
    ("macro:US_HOUSING_START", "리츠/부동산", "ambiguous", "주택착공 증가는 경기 개선과 공급 부담이 섞여 주의로 처리한다."),
    ("macro:US_10Y_YIELD", "바이오", "higher_is_bad", "미국 10년 금리 상승은 바이오 성장주 할인율 부담으로 부정적이다."),
    ("macro:US_10Y_YIELD_YH", "바이오", "higher_is_bad", "미국 10년 금리 상승은 바이오 성장주 할인율 부담으로 부정적이다."),
    ("macro:COMM_COPPER", "반도체", "ambiguous", "구리 가격 상승은 경기 회복과 원가 부담이 섞여 반도체 단독 신호로 쓰지 않는다."),
    ("macro:US_DXY", "시장 레짐", "ambiguous", "달러 강세는 위험회피와 환율 수혜가 섞여 시장 레짐에서는 주의로 처리한다."),
    ("macro:US_HY_SPREAD", "시장 레짐", "higher_is_bad", "하이일드 스프레드 확대는 신용위험 확대와 위험자산 회피 신호다."),
    ("macro:US_VIX", "시장 레짐", "higher_is_bad", "VIX 상승은 위험회피 확대 신호로 해석한다."),
    ("macro:CN_USD_CNY", "여행/레저", "ambiguous", "위안화 약세는 중국 관광 수요와 가격경쟁 효과가 섞여 주의로 처리한다."),
    ("macro:JP_USD_JPY", "여행/레저", "ambiguous", "엔화 약세는 일본 여행 수요와 국내 레저 대체수요 효과가 섞여 주의로 처리한다."),
    ("macro:US_CONSUMER_CONF", "여행/레저", "higher_is_good", "미국 소비자신뢰 개선은 글로벌 여행/레저 수요에 우호적이다."),
    ("macro:US_CONSUMER_CONF", "유통", "higher_is_good", "미국 소비자신뢰 개선은 소비재/유통 수요에 우호적이다."),
    ("macro:US_RETAIL_SALES", "유통", "higher_is_good", "미국 소매판매 개선은 유통/소비재 수요에 우호적이다."),
    ("macro:KR_RETAIL_SALES", "음식료", "higher_is_good", "한국 소매판매 개선은 음식료 내수 수요에 우호적이다."),
    ("macro:JP_USD_JPY", "자동차", "higher_is_bad", "엔화 약세는 일본 완성차 가격경쟁 심화로 국내 자동차에 부정적으로 해석한다."),
    ("macro:US_RETAIL_SALES", "자동차", "higher_is_good", "미국 소매판매 개선은 자동차 수요 보조 신호로 해석한다."),
    ("macro:KR_TRADE_BALANCE", "전력기기", "higher_is_good", "무역수지 개선은 전력기기 수출 레짐에 우호적이다."),
    ("macro:COMM_OIL_BRENT", "조선/해운", "ambiguous", "유가 상승은 선박 발주와 연료비 부담이 엇갈려 주의로 처리한다."),
    ("macro:COMM_OIL_WTI", "조선/해운", "ambiguous", "유가 상승은 선박 발주와 연료비 부담이 엇갈려 주의로 처리한다."),
    ("macro:KR_TRADE_BALANCE", "조선/해운", "higher_is_good", "무역수지 개선은 수출 물동량과 조선/해운 레짐에 우호적이다."),
    ("macro:COMM_OIL_WTI", "철강/비철", "ambiguous", "유가 상승은 원가와 경기민감 수요가 섞여 주의로 처리한다."),
    ("macro:US_HOUSING_START", "철강/비철", "higher_is_good", "미국 주택착공 증가는 철강/비철 수요에 우호적이다."),
    ("macro:KR_USD_KRW", "환율/수출주", "higher_is_good", "원/달러 상승은 수출주 원화 환산 매출에 우호적이다."),
    ("macro:US_DXY", "환율/수출주", "ambiguous", "달러 강세는 환율 수혜와 글로벌 위험회피가 섞여 주의로 처리한다."),
    ("macro:GLOBAL_GDP_GROWTH", "글로벌 경기/무역", "higher_is_good", "세계 GDP 성장률 개선은 글로벌 수요와 위험선호에 우호적이다."),
    ("macro:GLOBAL_TRADE_VOL", "글로벌 경기/무역", "higher_is_good", "세계 무역량 개선은 수출주와 물동량에 우호적이다."),
    ("macro:GLOBAL_EXPORT_VOL", "글로벌 경기/무역", "higher_is_good", "세계 수출물량 개선은 글로벌 제조업 수요에 우호적이다."),
    ("macro:GLOBAL_IMPORT_VOL", "글로벌 경기/무역", "higher_is_good", "세계 수입물량 개선은 글로벌 수요 회복 신호로 해석한다."),
    ("macro:GLOBAL_INFLATION", "글로벌 경기/무역", "higher_is_bad", "세계 인플레이션 상승은 금리/원가 부담으로 부정적으로 해석한다."),
    ("macro:US_GDP_GROWTH", "글로벌 경기/무역", "higher_is_good", "미국 성장률 개선은 글로벌 수요에 우호적이다."),
    ("macro:CN_GDP_GROWTH", "글로벌 경기/무역", "higher_is_good", "중국 성장률 개선은 소재/소비/수출 수요에 우호적이다."),
    ("macro:EU_GDP_GROWTH", "글로벌 경기/무역", "higher_is_good", "유럽 성장률 개선은 글로벌 수요에 우호적이다."),
    ("macro:KR_GDP_GROWTH", "글로벌 경기/무역", "higher_is_good", "한국 성장률 개선은 국내 경기와 기업 실적에 우호적이다."),
    ("macro:KR_CLI_LEADING", "한국 경기", "higher_is_good", "경기선행지수 개선은 국내 경기 회복 신호로 해석한다."),
    ("macro:KR_CLI_COINCIDENT", "한국 경기", "higher_is_good", "경기동행지수 개선은 국내 실물경기 개선으로 해석한다."),
    ("macro:KR_CLI_LAGGING", "한국 경기", "ambiguous", "경기후행지수는 선행성이 낮아 단독 신호로 쓰지 않는다."),
    ("macro:KR_ECON_SENTIMENT", "한국 경기", "higher_is_good", "경제심리지수 개선은 국내 경기와 위험선호에 우호적이다."),
    ("macro:KR_INDUSTRIAL_PROD", "한국 경기", "higher_is_good", "산업생산 개선은 국내 제조업 경기 회복 신호로 해석한다."),
    ("macro:KR_SERVICE_PROD", "한국 경기", "higher_is_good", "서비스업생산 개선은 내수 경기 회복 신호로 해석한다."),
    ("macro:KR_CPI_SERVICE", "한국 경기", "ambiguous", "서비스물가 상승은 내수 견조함과 비용/금리 부담이 섞여 주의로 처리한다."),
    ("macro:KR_IMPORT", "한국 경기", "ambiguous", "수입 증가는 내수 회복과 원가 부담이 섞여 주의로 처리한다."),
    ("macro:KR_IMPORT_REAL", "한국 경기", "higher_is_good", "실질 수입 증가는 국내 수요 회복 보조 신호로 해석한다."),
    ("macro:KR_DOMESTIC_SHIP", "한국 경기", "higher_is_good", "내수출하지수 개선은 국내 제조업 수요 회복 신호다."),
    ("macro:KR_INVENTORY", "한국 경기", "ambiguous", "재고 증가는 수요 부진 또는 재축적이 모두 가능해 주의로 처리한다."),
    ("macro:KR_EMPLOYMENT", "한국 경기", "higher_is_good", "취업자수 증가는 내수 경기와 소비 여건에 우호적이다."),
    ("macro:KR_NONFARM_EMPLOY", "한국 경기", "higher_is_good", "비농림취업자수 증가는 내수 경기와 소비 여건에 우호적이다."),
    ("macro:KR_UNEMPLOYMENT", "한국 경기", "higher_is_bad", "실업률 상승은 국내 경기와 소비에 부정적이다."),
    ("macro:KR_M2", "한국 경기", "higher_is_good", "M2 증가는 유동성 환경 개선 보조 신호로 해석한다."),
    ("macro:US_GDP_GROWTH", "미국 매크로", "higher_is_good", "미국 성장률 개선은 글로벌 수요에 우호적이다."),
    ("macro:US_GDP_GROWTH_WEO", "미국 매크로", "higher_is_good", "미국 IMF 성장률 전망 개선은 글로벌 수요에 우호적이다."),
    ("macro:US_CPI", "미국 매크로", "higher_is_bad", "미국 CPI 상승은 금리/할인율 부담으로 부정적으로 해석한다."),
    ("macro:US_CORE_CPI", "미국 매크로", "higher_is_bad", "미국 Core CPI 상승은 금리/할인율 부담으로 부정적으로 해석한다."),
    ("macro:US_PCE", "미국 매크로", "higher_is_bad", "미국 PCE 상승은 금리/할인율 부담으로 부정적으로 해석한다."),
    ("macro:US_NONFARM", "미국 매크로", "higher_is_good", "비농업고용 증가는 경기 체력에는 우호적이나 금리 부담과 함께 확인한다."),
    ("macro:US_UNEMPLOYMENT", "미국 매크로", "higher_is_bad", "미국 실업률 상승은 경기 둔화 신호로 해석한다."),
    ("macro:US_M2", "미국 매크로", "higher_is_good", "미국 M2 증가는 유동성 개선 보조 신호로 해석한다."),
    ("macro:US_CLI_OECD", "미국 매크로", "higher_is_good", "미국 경기선행지수 개선은 수요 회복 신호로 해석한다."),
    ("macro:CN_GDP_GROWTH", "중국 매크로", "higher_is_good", "중국 성장률 개선은 소재/소비/수출 수요에 우호적이다."),
    ("macro:CN_GDP_GROWTH_WEO", "중국 매크로", "higher_is_good", "중국 IMF 성장률 전망 개선은 중국 수요 민감 업종에 우호적이다."),
    ("macro:CN_CPI", "중국 매크로", "ambiguous", "중국 CPI는 수요 회복과 정책 부담이 섞여 주의로 처리한다."),
    ("macro:CN_CLI_OECD", "중국 매크로", "higher_is_good", "중국 경기선행지수 개선은 중국 수요 회복 신호로 해석한다."),
    ("macro:CN_USD_CNY", "중국 매크로", "ambiguous", "위안화 약세는 수출경쟁력과 자본유출 우려가 섞여 주의로 처리한다."),
    ("macro:EU_GDP_GROWTH", "유럽/일본 매크로", "higher_is_good", "유럽 성장률 개선은 글로벌 수요에 우호적이다."),
    ("macro:EU_GDP_GROWTH_WEO", "유럽/일본 매크로", "higher_is_good", "유럽 IMF 성장률 전망 개선은 글로벌 수요에 우호적이다."),
    ("macro:EU_CPI", "유럽/일본 매크로", "higher_is_bad", "유럽 CPI 상승은 금리/원가 부담으로 부정적으로 해석한다."),
    ("macro:EU_UNEMPLOYMENT", "유럽/일본 매크로", "higher_is_bad", "유럽 실업률 상승은 수요 둔화 신호로 해석한다."),
    ("macro:EU_ECB_RATE", "유럽/일본 매크로", "higher_is_bad", "ECB 기준금리 상승은 글로벌 할인율과 수요에 부담이다."),
    ("macro:EU_DAX", "유럽/일본 매크로", "higher_is_good", "DAX 상승은 유럽 위험선호 개선으로 해석한다."),
    ("macro:EU_FTSE", "유럽/일본 매크로", "higher_is_good", "FTSE 상승은 유럽 위험선호 개선으로 해석한다."),
    ("macro:JP_GDP_GROWTH_WEO", "유럽/일본 매크로", "higher_is_good", "일본 IMF 성장률 전망 개선은 지역 수요에 우호적이다."),
    ("macro:JP_CPI", "유럽/일본 매크로", "ambiguous", "일본 CPI 상승은 디플레 탈피와 금리 부담이 섞여 주의로 처리한다."),
    ("macro:JP_CLI_OECD", "유럽/일본 매크로", "higher_is_good", "일본 경기선행지수 개선은 지역 경기 회복 신호다."),
    ("macro:JP_BOJ_RATE", "유럽/일본 매크로", "ambiguous", "BOJ 금리 상승은 정상화와 할인율 부담이 섞여 주의로 처리한다."),
    ("macro:JP_NIKKEI", "유럽/일본 매크로", "higher_is_good", "닛케이 상승은 일본 위험선호 개선으로 해석한다."),
]


def now_kst() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cafe_quant_indicator_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sector_name TEXT NOT NULL,
            mention_count INTEGER DEFAULT 0,
            indicator_key TEXT NOT NULL,
            indicator_name TEXT,
            status TEXT,
            source_system TEXT,
            confidence REAL DEFAULT 0.7,
            mapping_note TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(sector_name, indicator_key)
        );
        CREATE TABLE IF NOT EXISTS indicator_sector_direction_rules (
            indicator_key TEXT NOT NULL,
            sector_name TEXT NOT NULL,
            direction_mode TEXT NOT NULL,
            note TEXT,
            confidence REAL DEFAULT 0.7,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(indicator_key, sector_name)
        );
        """
    )
    conn.commit()


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    init_table(conn)
    ts = now_kst()
    sector_counts = {
        r["mention_name"]: int(r["cnt"])
        for r in conn.execute(
            """
            SELECT mention_name, COUNT(*) AS cnt
            FROM cafe_signal_mentions
            WHERE mention_type='sector'
            GROUP BY mention_name
            """
        ).fetchall()
    }
    catalog = {
        r["indicator_key"]: dict(r)
        for r in conn.execute(
            """
            SELECT indicator_key, epic_indicator_name, status, source_system
            FROM quant_major_indicator_catalog
            """
        ).fetchall()
    }
    conn.execute("DELETE FROM cafe_quant_indicator_mappings")
    conn.execute("DELETE FROM indicator_sector_direction_rules")
    upserted = 0
    merged_sector_to_indicators = {
        sector_name: list(dict.fromkeys(indicator_keys + MACRO_SECTOR_TO_INDICATORS.get(sector_name, [])))
        for sector_name, indicator_keys in SECTOR_TO_INDICATORS.items()
    }
    for sector_name, indicator_keys in MACRO_SECTOR_TO_INDICATORS.items():
        merged_sector_to_indicators.setdefault(sector_name, indicator_keys)

    for sector_name, indicator_keys in merged_sector_to_indicators.items():
        mention_count = sector_counts.get(sector_name, 0)
        for key in indicator_keys:
            meta = catalog.get(key, {})
            if key.startswith("public:23:"):
                confidence = 0.9
                note = "카페 섹터 언급과 관세청/후보 퀀트 지표를 연결한 자동 매핑"
            elif key.startswith("macro:"):
                confidence = 0.62
                note = "글로벌 매크로/원자재 지표를 민감 섹터 레짐 필터로 연결한 자동 매핑"
            else:
                confidence = 0.68
                note = "카페 섹터 언급과 후보 퀀트 지표를 연결한 자동 매핑"
            conn.execute(
                """
                INSERT INTO cafe_quant_indicator_mappings
                (sector_name, mention_count, indicator_key, indicator_name, status,
                 source_system, confidence, mapping_note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sector_name, indicator_key) DO UPDATE SET
                    mention_count=excluded.mention_count,
                    indicator_name=excluded.indicator_name,
                    status=excluded.status,
                    source_system=excluded.source_system,
                    confidence=excluded.confidence,
                    mapping_note=excluded.mapping_note,
                    updated_at=excluded.updated_at
                """,
                (
                    sector_name,
                    mention_count,
                    key,
                    meta.get("epic_indicator_name", ""),
                    meta.get("status", ""),
                    meta.get("source_system", ""),
                    confidence,
                    note,
                    ts,
                ),
            )
            upserted += 1
    for indicator_key, sector_name, direction_mode, note in MACRO_SECTOR_DIRECTION_RULES:
        conn.execute(
            """
            INSERT INTO indicator_sector_direction_rules
            (indicator_key, sector_name, direction_mode, note, confidence, updated_at)
            VALUES (?, ?, ?, ?, 0.72, ?)
            ON CONFLICT(indicator_key, sector_name) DO UPDATE SET
                direction_mode=excluded.direction_mode,
                note=excluded.note,
                confidence=excluded.confidence,
                updated_at=excluded.updated_at
            """,
            (indicator_key, sector_name, direction_mode, note, ts),
        )
    conn.commit()
    print(json.dumps({"upserted": upserted, "direction_rules": len(MACRO_SECTOR_DIRECTION_RULES), "db": str(DB_PATH)}, ensure_ascii=False))
    conn.close()


if __name__ == "__main__":
    main()
