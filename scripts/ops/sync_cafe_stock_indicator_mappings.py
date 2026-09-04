#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "stock.db"


PRODUCT_INDICATOR_RULES = [
    {
        "indicator_key": "cafe:11:4128",
        "fallback_name": "SK하이닉스 투자자 핵심 월간 지표",
        "terms": ["SK하이닉스", "HBM", "DRAM", "낸드", "NAND", "메모리 판가"],
        "allowed_sector_prefixes": ["IT"],
        "allowed_stock_names": ["SK하이닉스"],
    },
    {
        "indicator_key": "cafe:34:7611",
        "fallback_name": "통신 ARPU/가입자/5G 지표",
        "terms": ["ARPU", "5G 가입자", "무선 가입자", "가입자당매출", "해지율"],
        "allowed_sector_prefixes": ["커뮤니케이션서비스", "IT"],
        "allowed_stock_names": ["SK텔레콤", "KT", "LG유플러스"],
    },
    {
        "indicator_key": "cafe:34:7633",
        "fallback_name": "양돈/돈육 업황 지표",
        "terms": ["양돈", "돈육", "돼지고기 가격", "돼지 도매가격", "사육두수", "사료 원가"],
        "allowed_sector_prefixes": ["필수소비재", "경기소비재"],
    },
    {
        "indicator_key": "cafe:11:2668",
        "fallback_name": "게임주 업황 지표",
        "terms": ["PUBG", "배틀그라운드", "Black Desert", "검은사막", "Lies of P", "P의 거짓", "Stellar Blade", "스텔라 블레이드"],
        "allowed_sector_prefixes": ["IT", "커뮤니케이션서비스"],
        "allowed_stock_names": ["크래프톤", "펄어비스", "네오위즈", "시프트업"],
    },
    {
        "indicator_key": "cafe:11:4247",
        "fallback_name": "주택 착공 지표",
        "terms": ["주택 착공", "주택착공", "주택 인허가", "주택인허가", "주택 준공"],
        "allowed_sector_prefixes": ["산업재", "소재"],
        "min_post_mentions": 2,
    },
    {
        "indicator_key": "cafe:34:7616",
        "fallback_name": "항공 여객/운임/유류비 지표",
        "terms": ["항공사", "항공 여객", "국제선 여객", "국내선 여객", "탑승률", "항공운임", "항공유"],
        "allowed_sector_prefixes": ["산업재"],
        "allowed_stock_names": ["대한항공", "아시아나항공", "제주항공", "진에어", "에어부산", "티웨이항공"],
    },
    {
        "indicator_key": "cafe:11:2650",
        "fallback_name": "영화관/IPTV VOD 지표",
        "terms": ["영화관", "IPTV", "VOD", "박스오피스"],
        "allowed_sector_prefixes": ["커뮤니케이션서비스", "IT"],
    },
    {
        "indicator_key": "cafe:11:2805",
        "fallback_name": "현대차/기아 출하 + 미국 중고차 지수",
        "terms": ["현대차", "기아", "완성차 출하", "자동차 출하", "중고차 지수", "Manheim"],
        "allowed_sector_prefixes": ["경기소비재"],
    },
    {
        "indicator_key": "cafe:11:3475",
        "fallback_name": "건설기성액/건설수주액",
        "terms": ["건설기성", "건설수주", "건설 수주", "건설투자"],
        "allowed_sector_prefixes": ["산업재", "소재"],
    },
    {
        "indicator_key": "cafe:34:7690",
        "fallback_name": "호텔/면세점/백화점 소비 지표",
        "terms": ["호텔", "면세점", "백화점", "외국인 관광객", "객실점유율"],
        "allowed_sector_prefixes": ["경기소비재", "필수소비재"],
    },
    {
        "indicator_key": "public:23:41",
        "fallback_name": "칼륨 화학제품 수출입",
        "terms": ["가성칼륨", "탄산칼륨", "염화칼륨", "칼륨", "가성소다", "potash", "koh", "dac", "ccs", "탄소포집"],
        "allowed_sector_prefixes": ["소재", "에너지"],
    },
    {
        "indicator_key": "public:23:42",
        "fallback_name": "에폭시/NB라텍스 화학소재 수출입",
        "terms": ["ech", "에폭시", "nb라텍스", "라텍스", "글리세린", "epoxy"],
        "allowed_sector_prefixes": ["소재"],
    },
    {
        "indicator_key": "public:23:17",
        "fallback_name": "석유화학 합성수지 수출입",
        "terms": ["에틸렌", "프로필렌", "btx", "abs", "pvc", "sbr", "합성수지", "나프타", "폴리카보네이트", "폴리프로필렌", "석유화학 스프레드"],
        "allowed_sector_prefixes": ["소재", "에너지"],
    },
    {
        "indicator_key": "public:23:14",
        "fallback_name": "정유 석유제품 수출입",
        "terms": ["정제마진", "원유", "항공유", "윤활기유", "석유제품", "호르무즈"],
        "allowed_sector_prefixes": ["에너지", "소재"],
    },
    {
        "indicator_key": "public:23:28",
        "fallback_name": "비료 수출입",
        "terms": ["비료용 요소", "암모니아", "비료", "요소수"],
        "allowed_sector_prefixes": ["소재"],
    },
    {
        "indicator_key": "public:23:36",
        "fallback_name": "전력기기 수출입",
        "terms": ["변압기", "전력기기", "전선", "초고압", "배전"],
        "allowed_sector_prefixes": ["산업재", "IT"],
    },
    {
        "indicator_key": "public:23:37",
        "fallback_name": "방산/항공 수출입",
        "terms": ["방산", "항공우주", "항공기 부품", "탄약", "미사일", "전투기", "k9", "천무"],
        "allowed_sector_prefixes": ["산업재"],
    },
    {
        "indicator_key": "public:23:7",
        "fallback_name": "선박/조선 수출입",
        "terms": ["조선", "선박", "lng선", "컨테이너선", "탱커", "조선 수주잔고", "선박 수주잔고"],
        "allowed_sector_prefixes": ["산업재"],
    },
]


INDICATOR_ALIASES = {
    "public:23:1": ["완성차", "자동차 판매", "자동차 수출"],
    "public:23:2": ["자동차부품", "차부품", "전장부품"],
    "public:23:4": ["메모리반도체", "dram", "nand", "낸드", "hbm"],
    "public:23:5": ["시스템반도체", "파운드리", "팹리스", "비메모리"],
    "public:23:6": ["반도체장비", "반도체 장비", "전공정장비", "후공정장비"],
    "public:23:8": ["철강재", "철강제품", "열연", "냉연", "철근"],
    "public:23:9": ["화장품", "k뷰티", "k-뷰티", "코스메틱"],
    "public:23:10": ["의약품", "제약", "원료의약품", "완제의약품"],
    "public:23:12": ["인쇄회로기판", "pcb", "fpcb", "회로기판"],
    "public:23:18": ["구리", "동박", "전기동", "동가격"],
    "public:23:19": ["알루미늄", "알루미늄박", "알루미늄 가격"],
    "public:23:20": ["후판", "열연강판", "열연", "hrc"],
    "public:23:21": ["선박엔진", "선박용 엔진", "디젤엔진"],
    "public:23:22": ["공작기계", "머시닝센터", "cnc"],
    "public:23:23": ["산업용로봇", "산업용 로봇", "협동로봇"],
    "public:23:24": ["의료기기", "미용기기", "임플란트"],
    "public:23:25": ["진단시약", "체외진단", "진단키트"],
    "public:23:26": ["백신", "바이오의약품", "바이오시밀러"],
    "public:23:27": ["타이어", "타이어 수출"],
    "public:23:29": ["의류", "의류 수출", "봉제"],
    "public:23:30": ["가공식품", "식품 수출", "k푸드", "k-푸드"],
    "public:23:31": ["맥주", "소주", "주류 수출"],
    "public:23:32": ["특수가스", "희귀가스", "네온", "제논", "크립톤"],
    "public:23:33": ["타이어코드", "타이어 코드"],
    "public:23:34": ["스테인리스", "스테인레스", "sts 판재"],
    "public:23:35": ["윤활기유", "윤활유", "base oil"],
    "public:23:38": ["건설기계", "굴착기", "중장비"],
    "public:23:39": ["건설 철강재", "형강", "철근"],
    "public:23:40": ["반도체기판", "반도체 기판", "패키지기판", "pcb"],
    "public:23:43": ["피팅", "밸브", "산업용 밸브", "산업용 피팅"],
}

GENERIC_INDICATOR_WORDS = {
    "수출입", "수출", "수입", "제품", "산업용", "원재료", "완성차", "화학제품",
}

CAFE_SECTOR_TO_STOCK_PREFIXES = {
    "반도체": ["IT"],
    "전력기기": ["산업재", "IT"],
    "방산/항공": ["산업재"],
    "건설/건자재": ["산업재", "소재"],
    "정유/화학": ["에너지", "소재"],
    "철강/비철": ["소재"],
    "자동차": ["경기소비재"],
    "소비재": ["경기소비재", "필수소비재"],
    "바이오/의료": ["의료"],
    "게임/미디어": ["IT", "커뮤니케이션서비스"],
    "조선/기계": ["산업재"],
}

DIRECT_STOCK_MAPPINGS = [
    {"indicator_key": "cafe:11:4128", "stock_code": "000660", "stock_name": "SK하이닉스", "evidence": "SK하이닉스 전용 메모리/HBM 월간 지표"},
    {"indicator_key": "cafe:34:7611", "stock_code": "017670", "stock_name": "SK텔레콤", "evidence": "통신사 가입자/ARPU 직접 관계"},
    {"indicator_key": "cafe:34:7611", "stock_code": "030200", "stock_name": "KT", "evidence": "통신사 가입자/ARPU 직접 관계"},
    {"indicator_key": "cafe:34:7611", "stock_code": "032640", "stock_name": "LG유플러스", "evidence": "통신사 가입자/ARPU 직접 관계"},
    {"indicator_key": "cafe:11:2668", "stock_code": "259960", "stock_name": "크래프톤", "evidence": "PUBG Steam app 578080 publisher KRAFTON"},
    {"indicator_key": "cafe:11:2668", "stock_code": "263750", "stock_name": "펄어비스", "evidence": "Black Desert Steam app 582660 developer/publisher Pearl Abyss"},
    {"indicator_key": "cafe:11:2668", "stock_code": "095660", "stock_name": "네오위즈", "evidence": "Lies of P Steam app 1627720 developer/publisher NEOWIZ"},
    {"indicator_key": "cafe:11:2668", "stock_code": "462870", "stock_name": "시프트업", "evidence": "Stellar Blade Steam app 3489700 developer SHIFT UP"},
]

DIRECT_SEGMENT_ALIASES = {
    ("277810", "public:23:23"): ["로봇"],
    ("025860", "public:23:28"): ["비료화학사업부문"],
    ("014830", "public:23:41"): ["화학사업부"],
    ("064350", "public:23:37"): ["디펜스솔루션 부문"],
    ("023160", "public:23:43"): ["플랜트용 기자재"],
    ("007340", "public:23:2"): ["자동차용 부품", "자동차용 축전지", "자동차용 튜브"],
    ("183190", "cafe:11:3475"): ["시멘트부문"],
    ("183190", "cafe:11:4247"): ["시멘트부문"],
    ("039130", "cafe:34:7690"): ["여행알선서비스", "숙박시설 운영수탁업", "여객자동차 운수업"],
}

MACRO_STOCK_SECTOR_RULES = [
    {
        "indicator_key": "macro:COMM_COPPER",
        "sector_names": ["전력기기", "철강/비철", "2차전지", "음극재/소재"],
        "evidence": "구리 가격은 전선/전력기기 판가·원가와 비철/배터리 소재 사이클에 민감",
    },
    {
        "indicator_key": "macro:COMM_OIL_WTI",
        "sector_names": ["정유/화학", "전력/에너지", "조선/해운", "해운"],
        "evidence": "WTI는 정유·석유화학 판가/원가, 해운 연료비, 에너지 업종 레짐에 민감",
    },
    {
        "indicator_key": "macro:COMM_OIL_BRENT",
        "sector_names": ["정유/화학", "전력/에너지", "조선/해운", "해운"],
        "evidence": "브렌트유는 정유·석유화학 판가/원가, 해운 연료비, 에너지 업종 레짐에 민감",
    },
    {
        "indicator_key": "macro:COMM_NATURAL_GAS",
        "sector_names": ["전력/에너지", "정유/화학", "유틸리티"],
        "evidence": "천연가스 가격은 발전 원가, LNG/화학 원가, 유틸리티 마진에 민감",
    },
    {
        "indicator_key": "macro:OIL_STOCKS_EX_SPR",
        "sector_names": ["정유/화학", "전력/에너지"],
        "evidence": "미국 원유재고는 유가/정제마진과 에너지 수급 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:OIL_STOCKS_TOTAL",
        "sector_names": ["정유/화학", "전력/에너지"],
        "evidence": "미국 원유재고 총계는 유가/정제마진과 에너지 수급 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:KR_USD_KRW",
        "sector_names": ["반도체", "자동차", "조선/해운", "전력기기", "철강/비철"],
        "evidence": "원/달러 환율은 수출주 원화 매출 환산과 외국인 수급 레짐에 민감",
    },
    {
        "indicator_key": "macro:US_DXY",
        "sector_names": ["반도체", "자동차", "조선/해운", "전력기기", "2차전지"],
        "evidence": "달러인덱스는 수출주 환율 환경과 글로벌 위험선호에 민감",
    },
    {
        "indicator_key": "macro:KR_EXPORT",
        "sector_names": ["반도체", "자동차", "조선/해운", "전력기기", "철강/비철"],
        "evidence": "한국 수출은 수출 주도 업종의 상위 레짐 지표",
    },
    {
        "indicator_key": "macro:KR_TRADE_BALANCE",
        "sector_names": ["반도체", "자동차", "조선/해운", "전력기기", "정유/화학"],
        "evidence": "무역수지는 수출 업황과 수입 원가 부담을 함께 보는 상위 레짐 지표",
    },
    {
        "indicator_key": "macro:KR_CURRENT_ACCOUNT",
        "sector_names": ["반도체", "자동차", "조선/해운", "전력기기", "금융"],
        "evidence": "경상수지는 원화/외국인 수급과 수출주 상위 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:KR_CONSTRUCTION_ORDER",
        "sector_names": ["건설", "건자재", "건설/건자재", "철강/비철", "기계"],
        "evidence": "건설수주는 건설·건자재·철강·건설기계 수요 선행 지표",
    },
    {
        "indicator_key": "macro:KR_CONSTRUCTION_OUTPUT",
        "sector_names": ["건설", "건자재", "건설/건자재", "철강/비철"],
        "evidence": "건설기성은 건설·건자재·철강 실수요 동행 지표",
    },
    {
        "indicator_key": "macro:KR_HOUSING_PRICE",
        "sector_names": ["건설", "건자재", "리츠/부동산", "금융"],
        "evidence": "주택가격은 건설/부동산/금융 위험선호와 담보가치 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:US_HOUSING_START",
        "sector_names": ["건설", "건자재", "철강/비철", "기계"],
        "evidence": "미국 주택착공은 건자재·철강·기계 글로벌 수요 보조 지표",
    },
    {
        "indicator_key": "macro:KR_BASE_RATE",
        "sector_names": ["은행", "보험", "증권", "금융", "리츠/부동산"],
        "evidence": "한국 기준금리는 금융 순이자마진, 할인율, 리츠/부동산 레짐에 민감",
    },
    {
        "indicator_key": "macro:KR_YIELD_SPREAD",
        "sector_names": ["은행", "보험", "증권", "금융"],
        "evidence": "장단기금리차는 은행 NIM과 경기/금융 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:US_10Y_YIELD",
        "sector_names": ["금융", "증권", "보험", "리츠/부동산", "SW/AI"],
        "evidence": "미국 10년 금리는 글로벌 할인율과 성장주/금융주 멀티플에 민감",
    },
    {
        "indicator_key": "macro:US_10Y_YIELD_YH",
        "sector_names": ["금융", "증권", "보험", "리츠/부동산", "SW/AI"],
        "evidence": "미국 10년 금리(Yahoo)는 글로벌 할인율과 성장주/금융주 멀티플에 민감",
    },
    {
        "indicator_key": "macro:US_HY_SPREAD",
        "sector_names": ["증권", "금융", "바이오", "SW/AI", "2차전지"],
        "evidence": "하이일드 스프레드는 위험자산 선호와 고멀티플/자금조달 민감 업종에 영향",
    },
    {
        "indicator_key": "macro:US_VIX",
        "sector_names": ["증권", "금융", "바이오", "SW/AI", "2차전지"],
        "evidence": "VIX는 글로벌 위험회피와 고베타 업종 수급 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:GLOBAL_FOOD_PRICE",
        "sector_names": ["음식료", "소비재"],
        "evidence": "FAO 식품가격은 음식료 원가와 판가 전가 환경에 민감",
    },
    {
        "indicator_key": "macro:GLOBAL_FOOD_CEREALS",
        "sector_names": ["음식료", "소비재"],
        "evidence": "곡물가격은 제분/제과/라면/사료 원가에 민감",
    },
    {
        "indicator_key": "macro:GLOBAL_FOOD_OILS",
        "sector_names": ["음식료", "소비재"],
        "evidence": "유지류 가격은 식품 제조 원가에 민감",
    },
    {
        "indicator_key": "macro:COMM_WHEAT",
        "sector_names": ["음식료", "소비재"],
        "evidence": "소맥 가격은 제분/제과/라면/사료 원가에 민감",
    },
    {
        "indicator_key": "macro:US_RETAIL_SALES",
        "sector_names": ["자동차", "소비재", "유통", "패션/의류"],
        "evidence": "미국 소매판매는 소비재/자동차 수요 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:US_CONSUMER_CONF",
        "sector_names": ["자동차", "소비재", "유통", "패션/의류"],
        "evidence": "미국 소비자신뢰지수는 글로벌 소비재 수요 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:US_BAA_SPREAD",
        "sector_names": ["증권", "금융", "바이오", "SW/AI", "2차전지"],
        "evidence": "BAA 스프레드는 신용위험과 고멀티플/자금조달 민감 업종의 위험선호 보조 지표",
    },
    {
        "indicator_key": "macro:US_NFCI",
        "sector_names": ["증권", "금융", "바이오", "SW/AI", "2차전지"],
        "evidence": "NFCI는 금융여건 긴축/완화와 성장주·금융주 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:US_SP500",
        "sector_names": ["증권", "금융", "SW/AI", "반도체", "2차전지"],
        "evidence": "S&P500은 글로벌 위험선호와 성장/수출주 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:COMM_GOLD",
        "sector_names": ["금", "귀금속", "비철", "철강/비철", "방산/항공"],
        "evidence": "금 가격은 안전자산 선호와 귀금속/비철금속 관련 업종의 보조 지표",
    },
    {
        "indicator_key": "macro:CN_USD_CNY",
        "sector_names": ["반도체", "자동차", "화장품/뷰티", "소비재", "여행/레저"],
        "evidence": "위안화 환율은 중국 수요·가격경쟁·인바운드 소비 관련 종목의 매크로 보조 지표",
    },
    {
        "indicator_key": "macro:JP_USD_JPY",
        "sector_names": ["자동차", "화장품/뷰티", "여행/레저", "소비재"],
        "evidence": "엔화 환율은 일본 경쟁사 가격경쟁과 여행/소비 수요에 영향을 주는 보조 지표",
    },
    {
        "indicator_key": "macro:EU_EUR_USD",
        "sector_names": ["자동차", "반도체", "화장품/뷰티", "전력기기"],
        "evidence": "유로/달러 환율은 유럽 수출 비중 또는 유럽 경쟁사 노출 종목의 보조 지표",
    },
    {
        "indicator_key": "macro:KR_EXPORT_IMPORT_PRICE_RATIO",
        "sector_names": ["반도체", "자동차", "조선/해운", "전력기기", "정유/화학"],
        "evidence": "수출입물가비율은 교역조건과 수출마진 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:KR_INDUSTRIAL_PROD",
        "sector_names": ["반도체", "기계", "철강/비철", "전력기기", "자동차"],
        "evidence": "산업생산은 제조업 실수요와 재고/출하 사이클 보조 지표",
    },
    {
        "indicator_key": "macro:KR_INVENTORY_CYCLE",
        "sector_names": ["반도체", "자동차", "철강/비철", "화학", "기계"],
        "evidence": "재고순환은 경기민감 제조업의 출하·재고 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:KR_MACHINERY_SHIP",
        "sector_names": ["기계", "조선/해운", "철강/비철", "건설/건자재"],
        "evidence": "기계류/선박 지표는 설비투자·조선·후판 수요 사이클 보조 지표",
    },
    {
        "indicator_key": "macro:KR_CP_YIELD",
        "sector_names": ["건설", "건자재", "건설/건자재", "증권", "금융", "리츠/부동산"],
        "evidence": "CP 금리는 조달비용·신용위험에 민감한 건설/부동산/금융 보조 지표",
    },
    {
        "indicator_key": "macro:US_FED_RATE",
        "sector_names": ["금융", "증권", "보험", "리츠/부동산", "바이오", "SW/AI"],
        "evidence": "미국 기준금리는 글로벌 할인율과 성장주/금융주 멀티플에 민감",
    },
    {
        "indicator_key": "macro:US_10Y_BREAKEVEN",
        "sector_names": ["금융", "증권", "보험", "리츠/부동산", "바이오", "SW/AI"],
        "evidence": "미국 기대인플레이션은 할인율·실질금리·위험선호 보조 지표",
    },
    {
        "indicator_key": "macro:US_2Y_YIELD",
        "sector_names": ["금융", "증권", "보험", "리츠/부동산", "바이오", "SW/AI"],
        "evidence": "미국 2년 금리는 단기 할인율과 성장주/금융주 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:US_3M_YIELD",
        "sector_names": ["금융", "증권", "보험", "리츠/부동산", "바이오", "SW/AI"],
        "evidence": "미국 3개월 금리는 단기 유동성/할인율 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:US_30Y_YIELD",
        "sector_names": ["금융", "증권", "보험", "리츠/부동산"],
        "evidence": "미국 30년 금리는 장기 할인율과 보험/금융 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:GLOBAL_FOOD_DAIRY",
        "sector_names": ["음식료", "소비재"],
        "evidence": "유제품 가격은 식품 제조 원가와 판가 전가 환경에 민감",
    },
    {
        "indicator_key": "macro:GLOBAL_FOOD_MEAT",
        "sector_names": ["음식료", "소비재", "외식"],
        "evidence": "육류 가격은 식품/외식 원가와 판가 전가 환경에 민감",
    },
    {
        "indicator_key": "macro:GLOBAL_FOOD_SUGAR",
        "sector_names": ["음식료", "소비재"],
        "evidence": "설탕 가격은 제과/음료 원가와 판가 전가 환경에 민감",
    },
    {
        "indicator_key": "macro:KR_CPI",
        "sector_names": ["음식료", "소비재", "유통", "금융"],
        "evidence": "소비자물가는 판가 전가, 수요 둔화, 금리 경로를 함께 보는 보조 지표",
    },
    {
        "indicator_key": "macro:KR_RETAIL_SALES",
        "sector_names": ["유통", "소비재", "자동차", "여행/레저", "화장품/뷰티"],
        "evidence": "국내 소매판매는 내수 소비주와 레저/화장품 수요 보조 지표",
    },
    {
        "indicator_key": "macro:KR_CONSUMER_IMPORT",
        "sector_names": ["유통", "소비재", "화장품/뷰티", "여행/레저"],
        "evidence": "소비재 수입은 내수 소비와 유통/소비재 수요 보조 지표",
    },
    {
        "indicator_key": "macro:US_CLI_OECD",
        "sector_names": ["반도체", "자동차", "전력기기", "기계", "소비재"],
        "evidence": "미국 경기선행지수는 글로벌 수요 회복/둔화 레짐 보조 지표",
    },
    {
        "indicator_key": "macro:CN_CLI_OECD",
        "sector_names": ["반도체", "자동차", "화장품/뷰티", "철강/비철", "정유/화학"],
        "evidence": "중국 경기선행지수는 중국 수요 민감 업종의 레짐 보조 지표",
    },
]


def now_kst() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def init_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cafe_stock_indicator_mappings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            sector_name TEXT,
            indicator_key TEXT NOT NULL,
            indicator_name TEXT,
            mention_count INTEGER DEFAULT 0,
            evidence_terms TEXT,
            example_posts TEXT,
            latest_collected_at TEXT,
            revenue_exposure_pct REAL,
            profit_exposure_pct REAL,
            cost_exposure_pct REAL,
            exposure_basis TEXT,
            importance_level TEXT,
            confidence REAL DEFAULT 0.6,
            mapping_status TEXT DEFAULT 'candidate_context',
            mapping_note TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(stock_code, indicator_key)
        );
        CREATE INDEX IF NOT EXISTS idx_cafe_stock_indicator_stock
            ON cafe_stock_indicator_mappings(stock_code, mention_count DESC);
        """
    )
    existing = {r[1] for r in conn.execute("PRAGMA table_info(cafe_stock_indicator_mappings)").fetchall()}
    for name, ddl in {
        "revenue_exposure_pct": "REAL",
        "profit_exposure_pct": "REAL",
        "cost_exposure_pct": "REAL",
        "exposure_basis": "TEXT",
        "importance_level": "TEXT",
        "mapping_status": "TEXT DEFAULT 'candidate_context'",
    }.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE cafe_stock_indicator_mappings ADD COLUMN {name} {ddl}")
    conn.commit()


def catalog_rules(conn: sqlite3.Connection) -> list[dict]:
    configured = {rule["indicator_key"]: rule for rule in PRODUCT_INDICATOR_RULES}
    rows = conn.execute(
        """
        SELECT DISTINCT q.indicator_key, q.indicator_name, q.sector_name
        FROM cafe_quant_indicator_mappings q
        JOIN quant_major_indicator_series s ON s.indicator_key=q.indicator_key
        WHERE q.status='ready_existing' AND q.indicator_key LIKE 'public:23:%'
        ORDER BY q.indicator_key
        """
    ).fetchall()
    generated = []
    for row in rows:
        if row["indicator_key"] in configured:
            continue
        name = row["indicator_name"] or ""
        base_name = re.sub(r"\s*수출입\s*$", "", name).strip()
        # Keep catalog phrases intact. Splitting creates false links such as
        # every semiconductor company matching every semiconductor sub-index.
        name_terms = [base_name] if len(base_name) >= 3 and base_name not in GENERIC_INDICATOR_WORDS else []
        terms = list(dict.fromkeys(INDICATOR_ALIASES.get(row["indicator_key"], []) + name_terms))
        if not terms:
            continue
        generated.append(
            {
                "indicator_key": row["indicator_key"],
                "fallback_name": name,
                "terms": terms,
                "allowed_sector_prefixes": CAFE_SECTOR_TO_STOCK_PREFIXES.get(row["sector_name"], []),
            }
        )
    return PRODUCT_INDICATOR_RULES + generated


def compile_rules(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for rule in catalog_rules(conn):
        terms = sorted(set(rule["terms"]), key=len, reverse=True)
        pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE)
        out.append({**rule, "pattern": pattern})
    return out


def close_context_hits(text: str, stock_name: str, pattern: re.Pattern, window: int = 120) -> list[str]:
    stock_positions = [m.start() for m in re.finditer(re.escape(stock_name), text, re.IGNORECASE)]
    if not stock_positions:
        return []
    hits = []
    for m in pattern.finditer(text):
        if any(abs(m.start() - pos) <= window for pos in stock_positions):
            hits.append(text[m.start():m.end()])
    return hits


def sector_allowed(stock_sector: str | None, stock_name: str, rule: dict) -> bool:
    allowed_names = rule.get("allowed_stock_names") or []
    if allowed_names and stock_name not in allowed_names:
        return False
    allowed = rule.get("allowed_sector_prefixes") or []
    if not allowed:
        return True
    sector = stock_sector or ""
    return any(sector.startswith(prefix) for prefix in allowed)


def safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den * 100.0


def segment_term_match(segment_name: str, term: str) -> bool:
    name = segment_name.lower()
    needle = term.lower().strip()
    if not needle:
        return False
    if f"비{needle}" in name or f"비-{needle}" in name:
        return False
    if needle.isascii() and len(needle) <= 3:
        return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", name) is not None
    return needle in name


def invalid_segment_name(segment_name: str) -> bool:
    compact = re.sub(r"[\s·._()\-]", "", (segment_name or "").lower())
    exact = {
        "계", "합계", "총계", "소계", "매출액", "영업손익", "영업이익", "연결조정",
        "연결조정액", "연결후금액", "총매출액", "순매출액", "전사매출", "전사영업이익",
        "내수", "수출", "해외", "국내", "금액", "총자산", "유무형자산", "외부매출액",
        "부문간내부매출액", "기타영업외수익",
    }
    return compact in exact or compact.startswith(("합계", "총합계", "매출액단순합산"))


def latest_segment_exposure(conn: sqlite3.Connection, stock_code: str, rule: dict) -> tuple[float | None, float | None, str | None]:
    rows = conn.execute(
        """
        SELECT year, quarter, segment_name, revenue, operating_profit, revenue_pct
        FROM segment_revenue
        WHERE stock_code=? AND COALESCE(segment_name,'') <> '연결전체'
          AND revenue IS NOT NULL
        ORDER BY year DESC, quarter DESC
        LIMIT 80
        """,
        (stock_code,),
    ).fetchall()
    if not rows:
        return None, None, None
    periods = list(dict.fromkeys((r["year"], r["quarter"]) for r in rows))
    direct_aliases = DIRECT_SEGMENT_ALIASES.get((stock_code, rule.get("indicator_key")), [])
    terms = list(rule.get("terms", [])) + direct_aliases
    for year, quarter in periods:
        current = [
            r for r in rows
            if r["year"] == year
            and r["quarter"] == quarter
            and float(r["revenue"] or 0) > 0
            and not invalid_segment_name(r["segment_name"] or "")
        ]
        if len(current) < 2:
            continue

        consolidated = conn.execute(
            """
            SELECT revenue FROM segment_revenue
            WHERE stock_code=? AND year=? AND quarter=? AND segment_name='연결전체'
              AND revenue IS NOT NULL AND revenue > 0
            LIMIT 1
            """,
            (stock_code, year, quarter),
        ).fetchone()
        segment_total = sum(float(r["revenue"]) for r in current)
        if consolidated:
            coverage = segment_total / float(consolidated["revenue"])
            if coverage < 0.55 or coverage > 1.45:
                continue

        published_pcts = [float(r["revenue_pct"]) for r in current if r["revenue_pct"] is not None and float(r["revenue_pct"]) > 0]
        pct_sum = sum(published_pcts)
        published_pct_valid = len(published_pcts) >= 2 and 70 <= pct_sum <= 130
        matched = [
            r for r in current
            if any(segment_term_match(r["segment_name"] or "", term) for term in terms)
        ]
        if not matched:
            continue
        total_op = sum(abs(float(r["operating_profit"] or 0)) for r in current if r["operating_profit"] is not None) or None
        rev = sum(float(r["revenue"] or 0) for r in matched)
        op = sum(abs(float(r["operating_profit"] or 0)) for r in matched if r["operating_profit"] is not None)
        rev_pct = (
            sum(float(r["revenue_pct"] or 0) for r in matched if float(r["revenue_pct"] or 0) > 0)
            if published_pct_valid
            else safe_div(rev, segment_total)
        )
        op_pct = safe_div(op, total_op)
        names = ", ".join(r["segment_name"] for r in matched[:4])
        basis = "공시 비중" if published_pct_valid else "양수 사업부문 매출 합계 기준 추정"
        if direct_aliases and any(segment_term_match(r["segment_name"] or "", term) for r in matched for term in direct_aliases):
            basis += "; 회사별 사업부문 proxy"
        return rev_pct, op_pct, f"사업부문 매출 매칭({basis}): {year}Q{quarter} {names}"
    return None, None, None


def latest_cost_exposure(conn: sqlite3.Connection, stock_code: str) -> tuple[float | None, str | None]:
    row = conn.execute(
        """
        SELECT year, quarter, raw_material_ratio, cogs_ratio, raw_material_cost, total_cogs, revenue
        FROM cost_structure
        WHERE stock_code=? AND revenue IS NOT NULL AND revenue > 0
        ORDER BY year DESC, quarter DESC
        LIMIT 1
        """,
        (stock_code,),
    ).fetchone()
    if row:
        raw = row["raw_material_ratio"]
        cogs = row["cogs_ratio"]
        pct = float(raw) if raw is not None and raw > 0 else (float(cogs) if cogs is not None and cogs > 0 else None)
        if pct is not None:
            return pct, f"원가구조: {row['year']}Q{row['quarter']} 매출원가/원재료 비중"
    row = conn.execute(
        """
        SELECT m.year, m.material_purchase_krw, f.revenue
        FROM dart_material_purchase m
        LEFT JOIN financial_data f
          ON f.stock_code=m.stock_code AND f.year=m.year AND f.is_annual=1 AND f.report_type=m.report_type
        WHERE m.stock_code=? AND m.material_purchase_krw IS NOT NULL
        ORDER BY m.year DESC
        LIMIT 1
        """,
        (stock_code,),
    ).fetchone()
    if row:
        pct = safe_div(row["material_purchase_krw"], row["revenue"])
        return pct, f"원재료 매입액/매출: {row['year']}년"
    return None, None


def importance_from_exposure(revenue_pct: float | None, profit_pct: float | None, cost_pct: float | None, mention_count: int) -> str:
    known = [v for v in (revenue_pct, profit_pct) if v is not None]
    if known:
        mx = max(known)
        if mx >= 30:
            return "high"
        if mx >= 10:
            return "medium"
        return "low"
    if mention_count >= 5:
        return "unknown_core_candidate"
    if cost_pct is not None and cost_pct >= 30 and mention_count >= 3:
        return "unknown_cost_sensitive"
    return "unknown"


def upsert_macro_sensitive_stock_mappings(conn: sqlite3.Connection, catalog: dict, ts: str, per_indicator_limit: int = 18) -> int:
    inserted = 0
    for rule in MACRO_STOCK_SECTOR_RULES:
        meta = catalog.get(rule["indicator_key"], {})
        if not meta:
            continue
        placeholders = ",".join("?" for _ in rule["sector_names"])
        rows = conn.execute(
            f"""
            WITH latest_universe AS (
                SELECT stock_code, MAX(base_date) AS base_date
                FROM stock_universe
                GROUP BY stock_code
            ),
            ranked AS (
                SELECT s.stock_code, s.stock_name, s.sector_name,
                       COALESCE(u.market_cap, 0) AS market_cap,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.stock_code
                           ORDER BY CASE s.sector_level WHEN 'middle' THEN 0 ELSE 1 END,
                                    COALESCE(u.market_cap, 0) DESC
                       ) AS rn
                FROM stockeasy_sector_membership s
                LEFT JOIN latest_universe lu ON lu.stock_code=s.stock_code
                LEFT JOIN stock_universe u
                  ON u.stock_code=lu.stock_code AND u.base_date=lu.base_date
                WHERE s.sector_name IN ({placeholders})
                  AND s.stock_code IS NOT NULL
                  AND s.stock_code <> ''
            )
            SELECT stock_code, stock_name, sector_name, market_cap
            FROM ranked
            WHERE rn=1
            ORDER BY market_cap DESC, stock_code
            LIMIT ?
            """,
            [*rule["sector_names"], per_indicator_limit],
        ).fetchall()
        for row in rows:
            evidence_terms = [
                {"term": rule["evidence"], "count": 1},
                {"term": f"민감 섹터: {row['sector_name']}", "count": 1},
            ]
            conn.execute(
                """
                INSERT INTO cafe_stock_indicator_mappings
                (stock_code, stock_name, sector_name, indicator_key, indicator_name,
                 mention_count, evidence_terms, example_posts, latest_collected_at,
                 revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct,
                 exposure_basis, importance_level, confidence, mapping_status, mapping_note, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, '[]', ?,
                        NULL, NULL, NULL,
                        '매크로/원자재 민감 섹터 기반 후보. 종목별 매출/이익 노출도 미확정',
                        'unknown_macro_sensitive', ?, 'candidate_macro_context', ?, ?)
                ON CONFLICT(stock_code, indicator_key) DO UPDATE SET
                    stock_name=excluded.stock_name,
                    sector_name=excluded.sector_name,
                    indicator_name=excluded.indicator_name,
                    evidence_terms=excluded.evidence_terms,
                    latest_collected_at=excluded.latest_collected_at,
                    exposure_basis=CASE
                        WHEN cafe_stock_indicator_mappings.mapping_status='confirmed_macro_signal'
                        THEN cafe_stock_indicator_mappings.exposure_basis
                        ELSE excluded.exposure_basis
                    END,
                    importance_level=CASE
                        WHEN cafe_stock_indicator_mappings.mapping_status='confirmed_macro_signal'
                        THEN cafe_stock_indicator_mappings.importance_level
                        ELSE excluded.importance_level
                    END,
                    confidence=CASE
                        WHEN cafe_stock_indicator_mappings.mapping_status='confirmed_macro_signal'
                        THEN CASE
                            WHEN COALESCE(cafe_stock_indicator_mappings.confidence, 0) >= COALESCE(excluded.confidence, 0)
                            THEN cafe_stock_indicator_mappings.confidence
                            ELSE excluded.confidence
                        END
                        ELSE excluded.confidence
                    END,
                    mapping_status=CASE
                        WHEN cafe_stock_indicator_mappings.mapping_status='confirmed_macro_signal'
                        THEN cafe_stock_indicator_mappings.mapping_status
                        ELSE excluded.mapping_status
                    END,
                    mapping_note=CASE
                        WHEN cafe_stock_indicator_mappings.mapping_status='confirmed_macro_signal'
                        THEN cafe_stock_indicator_mappings.mapping_note
                        ELSE excluded.mapping_note
                    END,
                    updated_at=excluded.updated_at
                """,
                (
                    row["stock_code"],
                    row["stock_name"],
                    row["sector_name"],
                    rule["indicator_key"],
                    meta.get("epic_indicator_name") or rule["indicator_key"],
                    json.dumps(evidence_terms, ensure_ascii=False),
                    ts,
                    0.58,
                    rule["evidence"],
                    ts,
                ),
            )
            inserted += 1
    return inserted


def reapply_latest_macro_promotions(conn: sqlite3.Connection, ts: str) -> int:
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='macro_signal_backtest_results'"
    ).fetchone()
    if not table_exists:
        return 0
    latest = conn.execute(
        """
        SELECT run_id
        FROM macro_signal_backtest_results
        GROUP BY run_id
        ORDER BY MAX(created_at) DESC
        LIMIT 1
        """
    ).fetchone()
    if not latest:
        return 0
    rows = conn.execute(
        """
        SELECT indicator_key, sector_name
        FROM macro_signal_backtest_results
        WHERE run_id=? AND pass_flag=1
        """,
        (latest["run_id"],),
    ).fetchall()
    updated = 0
    for row in rows:
        cur = conn.execute(
            """
            UPDATE cafe_stock_indicator_mappings
            SET mapping_status='confirmed_macro_signal',
                importance_level='macro_backtested',
                mapping_note='거시/퀀트 후보 중 가격 히스토리 백테스트 기준 통과',
                updated_at=?
            WHERE indicator_key=? AND sector_name=? AND mapping_status='candidate_macro_context'
            """,
            (ts, row["indicator_key"], row["sector_name"]),
        )
        updated += cur.rowcount or 0
    return updated


def main() -> None:
    conn = sqlite3.connect(DB_PATH, timeout=120)
    conn.execute("PRAGMA busy_timeout=120000")
    conn.row_factory = sqlite3.Row
    init_table(conn)

    catalog = {
        r["indicator_key"]: dict(r)
        for r in conn.execute(
            "SELECT indicator_key, epic_indicator_name FROM quant_major_indicator_catalog"
        ).fetchall()
    }

    rows = conn.execute(
        """
        SELECT p.id AS post_id, p.title, p.url, p.collected_at,
               COALESCE(b.content_text, p.excerpt, '') AS text,
               m.stock_code, m.stock_name, m.sector_name
        FROM cafe_signal_mentions m
        JOIN cafe_signal_posts p ON p.id=m.cafe_post_id
        LEFT JOIN cafe_signal_post_bodies b
          ON b.cafe_id=p.cafe_id AND b.article_id=p.article_id
        WHERE m.mention_type='stock'
          AND m.stock_code IS NOT NULL
          AND m.stock_code <> ''
        """
    ).fetchall()

    rules = compile_rules(conn)
    acc: dict[tuple[str, str], dict] = {}
    for row in rows:
        text = f"{row['title']} {row['text'] or ''}"
        for rule in rules:
            if not sector_allowed(row["sector_name"], row["stock_name"], rule):
                continue
            hits = close_context_hits(text, row["stock_name"], rule["pattern"])
            if not hits:
                continue
            key = (row["stock_code"], rule["indicator_key"])
            item = acc.setdefault(
                key,
                {
                    "stock_code": row["stock_code"],
                    "stock_name": row["stock_name"],
                    "sector_name": row["sector_name"] or "",
                    "indicator_key": rule["indicator_key"],
                    "indicator_name": catalog.get(rule["indicator_key"], {}).get("epic_indicator_name") or rule["fallback_name"],
                    "post_ids": set(),
                    "terms": {},
                    "examples": [],
                    "latest_collected_at": row["collected_at"],
                },
            )
            item["post_ids"].add(row["post_id"])
            for hit in hits[:8]:
                normalized = hit.strip()
                item["terms"][normalized] = item["terms"].get(normalized, 0) + 1
            if len(item["examples"]) < 5:
                item["examples"].append(
                    {
                        "title": row["title"],
                        "url": row["url"],
                        "collected_at": row["collected_at"],
                        "evidence": ", ".join(list(dict.fromkeys(hits))[:6]),
                    }
                )
            if row["collected_at"] and (not item["latest_collected_at"] or row["collected_at"] > item["latest_collected_at"]):
                item["latest_collected_at"] = row["collected_at"]

    ts = now_kst()
    conn.execute("DELETE FROM cafe_stock_indicator_mappings")
    inserted = 0
    for item in acc.values():
        mention_count = len(item["post_ids"])
        top_terms = sorted(item["terms"].items(), key=lambda x: (-x[1], x[0]))[:12]
        rule = next((r for r in rules if r["indicator_key"] == item["indicator_key"]), {})
        if mention_count < int(rule.get("min_post_mentions") or 1):
            continue
        revenue_exposure, profit_exposure, segment_basis = latest_segment_exposure(conn, item["stock_code"], rule)
        cost_exposure, cost_basis = latest_cost_exposure(conn, item["stock_code"])
        if revenue_exposure is None and profit_exposure is None and cost_exposure is None:
            exposure_basis = "매출/이익 비중 미공시: 카페 문맥 기반 관련도만 사용"
        else:
            exposure_basis = " / ".join(x for x in [segment_basis, cost_basis] if x)
        importance_level = importance_from_exposure(revenue_exposure, profit_exposure, cost_exposure, mention_count)
        confidence = min(0.95, 0.55 + mention_count * 0.05 + len(top_terms) * 0.015)
        conn.execute(
            """
            INSERT INTO cafe_stock_indicator_mappings
            (stock_code, stock_name, sector_name, indicator_key, indicator_name,
             mention_count, evidence_terms, example_posts, latest_collected_at,
             revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct,
             exposure_basis, importance_level, confidence, mapping_status, mapping_note, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["stock_code"],
                item["stock_name"],
                item["sector_name"],
                item["indicator_key"],
                item["indicator_name"],
                mention_count,
                json.dumps([{"term": t, "count": c} for t, c in top_terms], ensure_ascii=False),
                json.dumps(item["examples"], ensure_ascii=False),
                item["latest_collected_at"],
                round(revenue_exposure, 3) if revenue_exposure is not None else None,
                round(profit_exposure, 3) if profit_exposure is not None else None,
                round(cost_exposure, 3) if cost_exposure is not None else None,
                exposure_basis,
                importance_level,
                round(confidence, 3),
                "confirmed_exposure" if revenue_exposure is not None or profit_exposure is not None else "candidate_context",
                "네이버 카페 본문에서 종목명과 품목/퀀트 지표 키워드가 함께 등장한 관계",
                ts,
            ),
        )
        inserted += 1

    for direct in DIRECT_STOCK_MAPPINGS:
        meta = catalog.get(direct["indicator_key"], {})
        universe = conn.execute(
            "SELECT sector_large FROM stock_universe WHERE stock_code=? ORDER BY base_date DESC LIMIT 1", (direct["stock_code"],)
        ).fetchone()
        conn.execute(
            """
            INSERT OR REPLACE INTO cafe_stock_indicator_mappings
            (stock_code, stock_name, sector_name, indicator_key, indicator_name,
             mention_count, evidence_terms, example_posts, latest_collected_at,
             revenue_exposure_pct, profit_exposure_pct, cost_exposure_pct,
             exposure_basis, importance_level, confidence, mapping_status, mapping_note, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, '[]', ?, NULL, NULL, NULL,
                    '공식 원천 또는 사업 정의상 직접 연결; 해당 사업 매출 비중 미공시',
                    'unknown_core_candidate', 0.98, 'confirmed_relationship', ?, ?)
            """,
            (
                direct["stock_code"],
                direct["stock_name"],
                universe["sector_large"] if universe else "",
                direct["indicator_key"],
                meta.get("epic_indicator_name") or "게임주 업황 지표",
                json.dumps([{"term": direct["evidence"], "count": 1}], ensure_ascii=False),
                ts,
                direct["evidence"],
                ts,
            ),
        )
        inserted += 1

    inserted += upsert_macro_sensitive_stock_mappings(conn, catalog, ts)
    promoted = reapply_latest_macro_promotions(conn, ts)

    conn.commit()
    print(json.dumps({"candidates": len(acc), "upserted": inserted, "macro_promotions_reapplied": promoted, "db": str(DB_PATH)}, ensure_ascii=False))
    conn.close()


if __name__ == "__main__":
    main()
