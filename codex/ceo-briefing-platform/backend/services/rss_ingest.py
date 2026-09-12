from __future__ import annotations

import json
import sqlite3
import html
import re
import hashlib
import urllib.error
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from datetime import datetime, time as dt_time, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "backend" / "config" / "rss_sources.json"
def get_seoul_timezone():
    try:
        return ZoneInfo("Asia/Seoul")
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=9))


SEOUL = get_seoul_timezone()

CATEGORY_KAI = "kai"
CATEGORY_GOVERNMENT = "government"
CATEGORY_HANWHA = "hanwha"
CATEGORY_LIG = "lig"
CATEGORY_SPACE = "space"
CATEGORY_REFERENCE = "reference"
ALLOWED_CATEGORIES = {CATEGORY_KAI, CATEGORY_GOVERNMENT, CATEGORY_HANWHA, CATEGORY_LIG, CATEGORY_SPACE, CATEGORY_REFERENCE}

# 한화/LIG 경쟁사 브랜드 (단독 "한화"는 너무 광범위하므로 방산 계열사 명칭 위주로 사용)
HANWHA_KEYWORDS = [
    "한화에어로스페이스",
    "한화에어로",
    "한화시스템",
    "한화오션",
    "한화방산",
    "한화",
]

# 한화 고유 제품 — 한화 브랜드명 없어도 한화 기사로 판별
HANWHA_PRODUCT_KEYWORDS = [
    "천무",       # 다연장로켓
    "k9",         # 자주포
    "k-9",
    "레드백",     # 보병전투차
    "as-21",
    "비호",       # 자주 대공포 (한화)
]

LIG_KEYWORDS = [
    "lig넥스원",
    "lig d&a",
    "lig",
]

# LIG넥스원 고유 제품 — LIG 브랜드명 없어도 LIG 기사로 판별
LIG_PRODUCT_KEYWORDS = [
    "천궁",       # 중거리 지대공 미사일
    "천궁-ii",
    "천궁2",
    "비궁",       # 휴대용 대공 미사일
    "신궁",       # 휴대용 대공 미사일
    "해궁",       # 함대공 미사일
    "현무",       # 탄도미사일 (ADD·LIG 공동)
    "해성",       # 함대함 미사일
    "스파이크",   # 대전차 미사일 (LIG 면허생산)
]

SPACE_KEYWORDS = [
    "우주항공청",
    "kasa",
    "우주발사체",
    "인공위성",
    "위성 발사",
    "정지궤도위성",
    "저궤도위성",
    "소형위성",
    "누리호",
    "나로호",
    "스페이스x",
    "블루오리진",
    "우주탐사",
    "우주개발",
]
# 주의: "우주산업"은 "한국항공우주산업(KAI)"에 오매칭되므로 제거
# 주의: 단독 "위성"은 너무 광범위 — "인공위성", "위성 발사" 등 구체화

GOVERNMENT_KEYWORDS = [
    "국방부",
    "방위사업청",
    "방사청",
    "국방과학연구소",
    "add",
    "국방기술품질원",
    "기품원",
    "합동참모본부",
    "합참",
    "대통령실",
    "방위사업추진위원회",
    "방추위",
    "국방획득",
    "전력화",
    "방위력개선사업",
    "국방예산",
    "무기체계",
    "국방산업정책",
    "방위산업법",
    "산업통상자원부",
    "항공우주청",
]

KAI_STRONG_KEYWORDS = [
    "한국항공우주산업",
    "한국항공우주",
    "kai",
    "kf-21",
    "fa-50",
    "t-50",
    "수리온",
    "소형무장헬기",
    "차세대 공격헬기",
    "강구영",
]

# KAI CEO가 반드시 알아야 할 중요 비즈니스/전략 키워드
KAI_CEO_CRITICAL_KEYWORDS = [
    # 지분/M&A/민영화
    "지분 인수", "지분인수", "지분 매입", "지분매입", "지분 취득", "민영화", "민간화", "주주",
    "경영권", "이사회", "최대주주", "지배구조",
    # 수출/수주
    "수출 계약", "수출계약", "수주 확정", "수주확정", "납품 계약", "판매 계약",
    "해외 수주", "해외수주", "수출 성사", "수출성사",
    # 정부/의회 결정
    "국방예산 통과", "예산 통과", "예산안 통과", "국회 의결",
    "방위사업추진위원회", "방추위", "전력화 결정", "양산 결정",
    # 제품 비판/논란
    "결함", "추락", "사고 원인", "감항", "품질 문제", "납품 지연",
    "비행 중단", "비행중단", "운항 중단", "부식",
    # 경영진/정책
    "대통령 언급", "국방장관", "방위사업청장",
]

KAI_BUSINESS_KEYWORDS = [
    "사업",
    "수주",
    "매출",
    "실적",
    "영업이익",
    "수출",
    "개발",
    "생산",
    "양산",
    "납품",
    "제품",
    "기종",
    "전략",
    "투자",
    "인수",
    "합병",
    "m&a",
]

KAI_MARKET_IMPACT_KEYWORDS = [
    "주가",
    "급등",
    "급상승",
    "상승",
    "하락",
    "목표가",
    "투자의견",
    "증권",
    "리포트",
    "시총",
]

KAI_PRODUCT_KEYWORDS = [
    "kf-21",
    "kf21",
    "fa-50",
    "fa50",
    "t-50",
    "t50",
    "수리온",
    "kuh",
    "lah",
    "소형무장헬기",
    "차세대 공격헬기",
    "소해헬기",
    "고정익",
    "회전익",
    "상륙공격헬기",
    "마린온",
    "미르온",
    "미르온(lah)",
    "k-헬기",
    "해기",     # 해상기동헬기
    "차세대중형위성",
    "cas500",
    # 미래항공·메가프로젝트
    "uam",
    "도심항공교통",
    "에어모빌리티",
    "민항기공동개발",
    "차세대민항기",
    "aac",       # Advanced Air Craft / KAI 민항기 프로젝트 약어
]

KAI_COMPANY_KEYWORDS = [
    "한국항공우주산업",
    "한국항공우주",
    "kai",
    "강구영",
]

# 제목의 명확한 주체가 KAI가 아닌 항공·방산 기업이면 KAI 제품/산업 키워드가 있어도 KAI기사로 보지 않는다.
NON_KAI_PRIMARY_TITLE_KEYWORDS = [
    "대한항공",
    "korean air",
]

# 핵심 추적 협력사 — 모든 기사를 빠짐없이 수집 (비즈니스 이벤트 조건 없음)
KEY_TRACKED_PARTNERS = [
    "율곡",
    "아스트",
    "astk",
]

# KAI 핵심 협력사 (M&A·IPO·매각 등 비즈니스 변동이 CEO에게 중요한 업체들)
PARTNER_KEYWORDS = [
    "제노코",
    "켄코아에어로스페이스",
    "켄코아",
    "하이즈항공",
    "아스트",
    "퍼스텍",
    "휴니드",
    "코츠테크놀로지",
    "이엠코리아",
    "타임기술",
    "데크항공",
    "율곡",
    "astk",
    "에이엔에이치스트럭쳐",
    "에이엔에이치",
    "anh",
    "hvm",
    "에이치브이엠",
    "한국화이바",
    "한국항공서비스",
    "샘코",
    "한화테크윈",  # KAI 납품 분야 제한적으로
]

# 협력사 관련 CEO에게 중요한 비즈니스 이벤트 키워드
PARTNER_BUSINESS_EVENT_KEYWORDS = [
    "매각", "인수", "합병", "ipo", "상장", "상장 추진", "기업공개",
    "파산", "법정관리", "워크아웃", "부도", "도산",
    "대규모 수주", "수주 확정", "계약 체결",
]

INTERVIEW_KEYWORDS = ["인터뷰", "대담"]
EXECUTIVE_TITLE_KEYWORDS = ["대표", "ceo", "회장", "사장", "부회장"]

NON_KAI_CONTEXT_KEYWORDS = [
    "경상국립대",
    "대학교",
    "학과",
    "캠퍼스",
    "입시",
    "대학",
    "학생",
    "한국회계기준원",
    "회계기준원",
    "국제회계기준",
    "ifrs",
    "kssb",
    "issb",
    "회계기준",
    "회계사",
    "회계학",
    "지속가능성 공시",
    "지속가능성기준위원회",
    "재무보고",
    "회계 공시",
    "회계 처리",
    "감사원 감사",
]

# 저가치 기사 사전 차단 키워드 (KAI CEO 관점의 글로벌 차단 룰)
LOW_VALUE_KEYWORDS = [
    "현충원", "참배", "묘역", "정화활동", "현출원",
    "봉사활동", "기부", "무료급식", "사회공헌", "이글스",
    "취준생", "인턴십", "채용박람회", "채용설명회",
    "단신 모음", "산업 단신", "충용 모음", "[중화학", "부음",
    "오늘의 자산운용", "더뱸류 브리핑",
    "학교", "대학교", "연구소", "대학교수", "교수 제자", "제자가", "제자",
    "오너집 동정", "동정 고형식", "인물 동정",
    "우주의 조약돌", "미래모빌리티학교",
    "간담 협약", "업무협약", "업무협약 체결",
    "100년 기업", "공존 경영", "공존경영", "상생 경영", "상생경영",
    "윤희신", "태안군수", "군수 후보", "공개토론", "선거구", "표심",
    "kaist", "카이스트", "인재양성", "인재 양성",
    # 주가 단신 (장중 숫자% 상승/하락 패턴) — 브리핑에서 필터링
    "장중", "% 상승", "% 하락", "% 상승 마감", "% 하락 마감",
    "1% 초고수", "초고수의 개장선택",
]

EDUCATION_NOISE_KEYWORDS = [
    "대학교",
    "대학",
    "동의과학대",
    "경남정보대",
    "특강",
    "수상",
    "교육",
    "인재 양성",
    "인재양성",
    "캠퍼스",
    "학과",
    "학생",
]

POLITICAL_CAMPAIGN_KEYWORDS = [
    "선거",
    "선거전",
    "선거운동",
    "지방선거",
    "대선",
    "총선",
    "유세",
    "집중유세",
    "개별 유세",
    "후보",
    "표심",
    "민심",
    "투표",
    "막판 변수",
    "애도 메시지",
    "심판론",
    "당선인",
    "당선자",
    "구청장",
    "시장 당선",
    "국회의원 당선",
]

# 경쟁사 기사를 noise로 판별하는 키워드
COMPETITOR_NOISE_KEYWORDS = [
    # 선거/정치
    "선거", "표심", "여야", "후보", "지방선거", "대선", "당선인", "당선자",
    "구청장", "시의원", "도의원", "국회의원 후보",
    # 사고/재난/행정 수습 (방산과 무관한 화재·사고 행정 처리)
    "사고 수습", "화재 수습", "화재 대응", "산업재해 수습",
    "애도", "재난", "피해자", "지원센터", "화재 피해자",
    "전담 공무원", "복구", "구조대", "부상자 치료",
    # CSR/홍보/행사
    "봉사활동", "기부", "사회공헌", "장학금", "후원",
    "학술대회", "전시회 부스", "채용설명회", "채용 박람회",
    # 방산 무관 사업 (한화 에너지·금융·유통 등)
    "태양광", "수소 에너지", "수소차", "전기차 충전", "보험", "금융",
    "갤러리아", "아쿠아플라넷", "리조트", "호텔", "유통",
]

# 경쟁사가 방산/항공 분야에서 실제 비즈니스 중요 이슈인지 판별하는 키워드
COMPETITOR_DEFENSE_KEYWORDS = [
    # 수주/수출 (방산 분야 한정)
    "수주", "수출", "계약 체결", "공급 계약", "방산 수출",
    "전투기", "자주포", "헬기", "미사일", "유도무기", "잠수함",
    "레이더", "전자전", "무인기", "드론",
    # 재무/경영 (대규모)
    "실적 발표", "영업이익", "영업손실", "매출 목표",
    "방산 투자", "설비 투자", "공장 증설", "공장 건설",
    "인수", "합병", "m&a", "지분", "경영권", "ipo", "상장",
    # 개발/기술
    "개발 완료", "양산", "전력화", "납품", "기술 개발",
    "방산", "방위", "무기체계", "항공우주",
    # 경영진 법적·위기 이슈 (CEO 필수 인지 사항)
    "입건", "출국금지", "수사", "기소", "구속", "체포",
    "중대재해", "중처법", "산재", "폭발", "사망사고", "참사",
    "리콜", "결함", "납품 지연", "계약 해지", "손해배상",
    "노동청", "경찰", "검찰", "공정위", "제재",
]

TOPIC_STOPWORDS = {
    "속보", "단독", "종합", "인터뷰", "기자", "특파원", "today", "pick", "뉴스", "보도", "관련", "대한", "위한",
    "기사", "시장", "업계", "분석", "전망", "브리핑", "이슈", "중심",
}

MAJOR_PUBLISHER_SCORES = {
    # 정부 공식 소스 — 원문이므로 최우선
    "korea.kr": 200,
    "yna.co.kr": 120,
    "chosun.com": 110,
    "joongang.co.kr": 110,
    "donga.com": 110,
    "hani.co.kr": 105,
    "khan.co.kr": 105,
    "mk.co.kr": 105,
    "hankyung.com": 105,
    "sedaily.com": 100,
    "mt.co.kr": 100,
    "mtn.co.kr": 95,
    "fnnews.com": 95,
    "newsis.com": 95,
    "ytn.co.kr": 95,
    "sbs.co.kr": 95,
    "kbs.co.kr": 95,
    "mbc.co.kr": 95,
}


def has_any_keyword(text: str, keywords: List[str]) -> bool:
    lowered = (text or "").lower()
    return any((keyword or "").lower() in lowered for keyword in keywords)


def has_exact_kai_token(text: str) -> bool:
    lowered = (text or "").lower()
    return re.search(r"(?<![a-z0-9])kai(?![a-z0-9])", lowered) is not None


def has_kai_company_mention(text: str) -> bool:
    lowered = (text or "").lower()
    non_kai_keywords = [kw for kw in KAI_COMPANY_KEYWORDS if kw.lower() != "kai"]
    return has_any_keyword(lowered, non_kai_keywords) or has_exact_kai_token(lowered)


def has_kai_product_mention(text: str) -> bool:
    return has_any_keyword(text, KAI_PRODUCT_KEYWORDS)


def is_partner_or_supplier_story(title: str, text: str) -> bool:
    """KAI 주요 협력사 관련 기사인지 판별.

    - 핵심 추적 협력사(율곡·아스트·ASTK): 제목에 회사명만 있으면 무조건 reference
    - 그 외 협력사: M&A·IPO·매각 등 비즈니스 이벤트가 있어야 함
    """
    title_lower = (title or "").lower()
    text_lower = (text or "").lower()

    # 핵심 추적 협력사 — 제목에 이름이 있으면 비즈니스 이벤트 없어도 바로 통과
    if has_any_keyword(title_lower, KEY_TRACKED_PARTNERS):
        return True

    has_partner_brand = has_any_keyword(title_lower, PARTNER_KEYWORDS) or has_any_keyword(text_lower, PARTNER_KEYWORDS)
    if not has_partner_brand:
        return False

    # 협력사명이 있더라도 중요 비즈니스 이벤트 키워드가 있어야 reference로 분류
    has_business_event = has_any_keyword(text_lower, PARTNER_BUSINESS_EVENT_KEYWORDS)
    has_partner_in_title = has_any_keyword(title_lower, PARTNER_KEYWORDS)

    # 제목에 협력사명 + 비즈니스 이벤트 키워드 있으면 중요
    if has_partner_in_title and has_business_event:
        return True

    # 협력사 임원 인터뷰 (CEO 관점에서 알아야 할 협력사 동향)
    has_interview_format = has_any_keyword(title_lower, INTERVIEW_KEYWORDS) and has_any_keyword(title_lower, EXECUTIVE_TITLE_KEYWORDS)
    if has_partner_in_title and has_interview_format:
        return True

    return False


def is_generic_education_noise(title: str, summary: str) -> bool:
    text = " ".join([title or "", summary or ""]).lower()
    if not has_any_keyword(text, EDUCATION_NOISE_KEYWORDS):
        return False
    has_sector_word = has_any_keyword(text, ["방산", "항공", "우주", "국방"])
    has_real_anchor = (
        has_kai_company_mention(text)
        or has_kai_product_mention(text)
        or has_any_keyword(text, HANWHA_KEYWORDS + LIG_KEYWORDS + SPACE_KEYWORDS)
        or has_any_keyword(text, ["국방부", "방사청", "방위사업청", "국방과학연구소", "기품원", "우주항공청"])
    )
    return has_sector_word and not has_real_anchor


def is_stock_market_roundup(title: str) -> bool:
    """주가 시황·시세 종합 기사 여부 판별.

    복수 종목을 묶어 다루는 시황 라운드업 기사는 CEO 관점에서 가치 낮음 → trash.
    단, KAI 단독 주가 기사("[특징주] 한국항공우주 52주 신고가")는 trash로 안 함.
    """
    t = (title or "").lower()
    # "방산주/항공우주 관련주" + 시황 키워드 → 복수 종목 시황 기사
    if has_any_keyword(t, ["방산주", "방산 관련주", "항공우주 관련주", "k-방산 관련주"]) and \
       has_any_keyword(t, ["강세", "상승", "급등", "하락", "약세", "10년", "수혜", "호재", "테마"]):
        return True
    # [특징주]/[증시] + "종합" 또는 방산주 패턴 → 라운드업
    if has_any_keyword(t, ["[특징주]", "[증시]", "[코스피]", "[코스닥]"]) and \
       has_any_keyword(t, ["종합", "방산주", "방산 관련", "일제히", "줄줄이", "동반"]):
        return True
    return False


def is_generic_event_noise(title: str, summary: str, article_text: str = "") -> bool:
    text = " ".join([title or "", summary or "", article_text or ""]).lower()
    if not has_kai_company_mention(text):
        return False
    event_noise_keywords = [
        "커넥팅데이",
        "행사",
        "참여했다",
        "참여기업",
        "참여했다",
        "스타트업",
        "중견기업",
        "공기업",
        "협력의 장",
        "기술 협력",
        "네트워킹",
        "개방형 혁신",
        "벤처기업",
    ]
    title_event_keywords = [
        "협력의 장",
        "행사",
        "커넥팅데이",
        "스타트업",
        "개방형 혁신",
        "기술 협력",
    ]
    has_event_noise = has_any_keyword(text, event_noise_keywords)
    title_has_event_noise = has_any_keyword((title or "").lower(), title_event_keywords)
    has_core_kai_anchor = has_kai_product_mention(text) or has_any_keyword(text, ["강구영", "한국항공우주산업 실적", "한국항공우주산업 수출"])
    return has_event_noise and title_has_event_noise and not has_core_kai_anchor


def is_non_industry_kai_noise(title: str, summary: str, article_text: str = "") -> bool:
    text = " ".join([title or "", summary or "", article_text or ""]).lower()
    if not has_exact_kai_token(text):
        return False
    if has_any_keyword(text, ["한국항공우주산업", "한국항공우주"]) or has_kai_product_mention(text):
        return False
    lifestyle_noise_keywords = [
        "패션",
        "스타일",
        "모델",
        "시인",
        "사회 운동가",
        "런웨이",
        "컬렉션",
        "뷰티",
        "elle",
        "엘르",
    ]
    sector_keywords = [
        "항공",
        "우주",
        "방산",
        "국방",
        "전투기",
        "헬기",
        "수출",
        "사업",
        "개발",
        "생산",
        "양산",
    ]
    has_lifestyle_noise = has_any_keyword(text, lifestyle_noise_keywords)
    has_sector_anchor = has_any_keyword(text, sector_keywords)
    return has_lifestyle_noise or not has_sector_anchor


def is_political_campaign_noise(title: str, summary: str, article_text: str = "") -> bool:
    title_lower = (title or "").lower()
    text = " ".join([title or "", summary or "", article_text or ""]).lower()
    if not has_any_keyword(text, POLITICAL_CAMPAIGN_KEYWORDS):
        return False
    has_real_anchor = (
        is_kai_core_story(title, summary, article_text)
        or is_real_competitor_story(title, summary)
        or has_any_keyword(text, ["국방부", "방사청", "방위사업청", "국방과학연구소", "기품원", "우주항공청"])
    )
    title_has_anchor = (
        has_kai_company_mention(title_lower)
        or has_kai_product_mention(title_lower)
        or has_any_keyword(title_lower, HANWHA_KEYWORDS + LIG_KEYWORDS)
    )
    return not has_real_anchor and not title_has_anchor


def is_competitor_noise_story(title: str, summary: str) -> bool:
    """경쟁사(한화/LIG) 기사 중 KAI CEO 관점에서 가치 없는 noise 기사 판별.

    방산/항공과 무관한 경쟁사 기사(선거, 사고수습, CSR, 비방산사업 등)를 차단.
    """
    title_lower = (title or "").lower()
    text = " ".join([title or "", summary or ""]).lower()

    has_competitor_brand = has_any_keyword(text, HANWHA_KEYWORDS + LIG_KEYWORDS)
    if not has_competitor_brand:
        return False

    # 1. 선거/정치 기사는 무조건 noise
    if has_any_keyword(text, ["선거", "당선", "당선인", "당선자", "유세", "표심",
                               "지방선거", "대선", "총선", "구청장", "시의원"]):
        return True

    # 2. 사고/재난 수습 행정 기사 (방산 기업 정체성과 무관)
    if has_any_keyword(text, ["사고 수습", "화재 수습", "화재 대응", "피해자", "지원센터",
                               "전담 공무원", "복구", "부상자", "사망자", "산업재해"]):
        # 단, 사고 원인·책임·안전 문제는 경영진이 알아야 할 수 있음
        if not has_any_keyword(title_lower, ["책임", "원인 조사", "처벌", "수사", "고발"]):
            return True

    # 3. CSR/후원/봉사 활동
    if has_any_keyword(text, ["봉사활동", "기부", "사회공헌", "장학금", "후원", "무료급식"]):
        return True

    # 4. 방산과 무관한 한화 계열 사업 (에너지·금융·유통·레저 등)
    if has_any_keyword(text, ["태양광", "수소 에너지", "수소차", "전기차 충전",
                               "보험", "금융", "갤러리아", "아쿠아플라넷",
                               "리조트", "호텔", "유통", "케미칼", "첨단소재"]):
        if not has_any_keyword(text, ["방산", "방위", "전투기", "헬기", "미사일",
                                       "수주", "수출", "무기", "전력화"]):
            return True

    # 5. 단순 홍보/PR 행사 (박람회 단순 참가, 학술대회 참관 등)
    if has_any_keyword(text, ["전시회 참가", "부스 운영", "학술대회 참가", "채용설명회",
                               "업무협약", "mou 체결", "세미나 개최"]):
        if not has_any_keyword(title_lower, ["수주", "수출", "계약", "방산", "무기", "전력화"]):
            return True

    # 6. 경쟁사 브랜드가 본문에만 스치듯 언급되고 제목에 없는 경우 + 방산 키워드 없음
    title_has_brand = has_any_keyword(title_lower, HANWHA_KEYWORDS + LIG_KEYWORDS)
    has_defense_focus = has_any_keyword(text, COMPETITOR_DEFENSE_KEYWORDS)

    if not title_has_brand and not has_defense_focus:
        return True

    return False


def is_real_competitor_story(title: str, summary: str) -> bool:
    """KAI CEO 관점에서 실제로 중요한 경쟁사(한화/LIG) 기사인지 판별.

    방산/항공 분야 수주·수출·M&A·실적·기술개발·CEO 전략 등에 집중.
    """
    title_lower = (title or "").lower()
    text = " ".join([title or "", summary or ""]).lower()

    title_has_brand = has_any_keyword(title_lower, HANWHA_KEYWORDS + LIG_KEYWORDS)
    text_has_brand = has_any_keyword(text, HANWHA_KEYWORDS + LIG_KEYWORDS)

    if not text_has_brand:
        return False

    if is_competitor_noise_story(title, summary):
        return False

    has_defense_focus = has_any_keyword(text, COMPETITOR_DEFENSE_KEYWORDS)

    # 한화에어로스페이스 / 한화에어로 / LIG넥스원 풀네임이 제목에 있으면
    # 방산 키워드 없이도 기업 뉴스로서 실제 경쟁사 기사로 인정
    FULL_BRAND_KW = ["한화에어로스페이스", "한화에어로", "lig넥스원"]
    if has_any_keyword(title_lower, FULL_BRAND_KW):
        return True

    # 제목에 경쟁사 브랜드 + 방산 키워드 있으면 핵심 기사
    if title_has_brand and has_defense_focus:
        return True

    # 제목에 브랜드만 있어도 방산 맥락 기사라면 수용
    if title_has_brand and has_any_keyword(text, ["방산", "항공", "우주", "국방", "방위"]):
        return True

    # 경쟁사 CEO/대표 인터뷰 또는 경영전략 기사 (방산 재편, 사업 전략 등)
    if title_has_brand and has_any_keyword(title_lower, ["대표", "ceo", "사장", "회장", "부회장"]):
        if has_any_keyword(text, ["방산", "전략", "인수", "합병", "수주", "수출", "경영"]):
            return True

    # K-방산 원팀 등 경쟁사가 주도하는 대형 수주전
    if text_has_brand and has_any_keyword(title_lower, ["k-방산", "방산 원팀", "방산원팀"]):
        return True

    return False


def is_kai_core_story(title: str, summary: str, article_text: str = "") -> bool:
    """KAI CEO가 반드시 알아야 할 자사 관련 핵심 기사인지 판별.

    CEO 관점 중요 기사 기준:
    - KAI 지분 변동·민영화·M&A
    - KAI 제품 수출 계약·국방예산 통과·양산 결정
    - 대통령·국방장관 등 고위층의 KAI 언급
    - 방위사업추진위원회 KAI 제품 관련 결정
    - KAI 제품 결함·품질 문제·비판
    - KAI 경영진 변동·사업 전략 발표
    """
    title_lower = (title or "").lower()
    text = " ".join([title or "", summary or "", article_text or ""]).lower()

    has_company_in_title = has_kai_company_mention(title_lower)
    has_company_anchor = has_kai_company_mention(text)
    has_product_in_title = has_kai_product_mention(title_lower)
    has_product_anchor = has_kai_product_mention(text)
    has_competitor_title = has_any_keyword(title_lower, HANWHA_KEYWORDS + LIG_KEYWORDS)
    has_non_kai_primary_title = has_any_keyword(title_lower, NON_KAI_PRIMARY_TITLE_KEYWORDS)

    if has_non_kai_primary_title and not has_company_in_title:
        return False

    # 경쟁사 고유 제품이 제목에 있고 KAI 언급이 없으면 KAI 기사 아님
    # (예: '천궁-II 인도네시아 수출' 기사 → KAI 제품 아님)
    has_competitor_product_in_title = has_any_keyword(
        title_lower, HANWHA_PRODUCT_KEYWORDS + LIG_PRODUCT_KEYWORDS
    )
    if has_competitor_product_in_title and not has_product_in_title and not has_company_in_title:
        return False

    if is_generic_event_noise(title, summary, article_text):
        return False

    # 경쟁사가 2개 이상 본문에 등장하고 제목에 KAI가 없으면 업계 종합기사 → False
    # 예: "한화에어로·KAI·현대로템·LIG넥스원 2분기 실적 전망"
    if not has_company_in_title and not has_product_in_title:
        _multi_competitor_count = sum([
            has_any_keyword(text, ["한화에어로스페이스", "한화에어로"]),
            has_any_keyword(text, ["lig넥스원"]),
            has_any_keyword(text, ["현대로템"]),
            has_any_keyword(text, ["한화오션"]),
        ])
        if _multi_competitor_count >= 2:
            return False

    # 1. CEO 관점 최우선 기사: KAI 지분·민영화·M&A 관련
    if has_company_anchor and has_any_keyword(text, ["지분", "민영화", "경영권", "인수", "합병", "최대주주", "주주"]):
        return True

    # 2. KAI 제품 수출·수주·예산 관련 (CEO 관점 핵심)
    if (has_company_anchor or has_product_anchor) and has_any_keyword(text, [
        "수출 계약", "수주 확정", "납품 계약", "예산 통과", "예산안 통과",
        "방추위", "방위사업추진위원회", "전력화 결정", "양산 결정",
    ]):
        return True

    # 3. 고위층 언급 (대통령·국방장관 등)
    if has_company_anchor and has_any_keyword(text, ["대통령", "국방장관", "방위사업청장", "합참의장"]):
        return True

    # 4. KAI 제품 결함·안전·비판 기사
    if (has_company_anchor or has_product_anchor) and has_any_keyword(text, [
        "결함", "추락", "사고", "품질 문제", "납품 지연", "감항", "불량",
        "비판", "논란", "문제점",
    ]):
        # 단순 경쟁사 기사에서 스치듯 언급된 경우 제외
        if has_company_in_title or has_product_in_title:
            return True

    # 5. 제목에 KAI 회사명/제품명 + 비즈니스 키워드
    if has_company_in_title and has_any_keyword(text, KAI_BUSINESS_KEYWORDS):
        if not has_competitor_title:
            return True

    # 5-b. KAI 제조공정/자동화 혁신 기사
    if has_company_in_title and has_any_keyword(text, [
        "제조 공정", "생산 공정", "공정 효율", "공정효율",
        "엔진 장착", "항공기 엔진", "전투기 엔진",
        "로봇", "로보틱스", "자동화", "무인화",
        "파손 방지", "품질 안정", "생산성",
    ]):
        if not has_competitor_title:
            return True

    # 6. 제품명이 제목에 있고 핵심 비즈니스/정책 내용
    if has_product_in_title and has_any_keyword(text, KAI_BUSINESS_KEYWORDS + ["예산", "전력화", "수출", "도입"]):
        if not has_competitor_title:
            return True

    # 7. KAI 회사명이 본문에 있고 + KAI 제품/헬기/전투기 심층 기사
    #    (K-헬기 시리즈, 수리온 특집 등 KAI 제품 중심 기사)
    if has_company_anchor and has_any_keyword(text, KAI_PRODUCT_KEYWORDS + ["헬기", "전투기"]):
        if has_any_keyword(text, ["개발", "양산", "운용", "임무", "성능", "기술", "수출", "납품", "전력화"]):
            if not is_generic_event_noise(title, summary):
                return True

    # 8. KAI 우주 사업 (차세대중형위성 등 KAI가 주체인 위성/우주 사업)
    if has_company_anchor and has_any_keyword(text, ["위성", "차세대중형위성", "cas500"]):
        if not is_generic_event_noise(title, summary):
            return True

    return False


def get_allowed_telegram_briefing_hours(now_seoul: datetime) -> Tuple[int, ...]:
    # 원칙: 평일(월~금) 12시·18시 / 주말(토~일) 18시만 허용
    is_weekend = now_seoul.weekday() >= 5
    return (18,) if is_weekend else (12, 18)


def is_clear_category(item: Dict[str, Any], category: str) -> bool:
    """분류 결과가 명확한(clear) 경우인지 판별. clear=True이면 published=1로 자동 게재."""
    title = str(item.get("title", "")).lower()
    summary = str(item.get("summary", "")).lower()
    article_text = str(item.get("article_text", "")).lower()
    text = " ".join([title, summary, article_text])

    if category == CATEGORY_REFERENCE:
        # 협력사 비즈니스 이벤트 키워드가 제목에 있을 때만 clear
        return has_any_keyword(title, PARTNER_KEYWORDS) and has_any_keyword(text, PARTNER_BUSINESS_EVENT_KEYWORDS)

    if category == CATEGORY_HANWHA:
        # 방산 관련 경쟁사 기사만 clear
        title_has_hanwha = has_any_keyword(title, ["한화에어로스페이스", "한화에어로", "한화시스템", "한화오션"])
        has_defense = has_any_keyword(text, COMPETITOR_DEFENSE_KEYWORDS)
        return title_has_hanwha and has_defense

    if category == CATEGORY_LIG:
        title_has_lig = has_any_keyword(title, ["lig넥스원", "lig d&a", "lig"])
        has_defense = has_any_keyword(text, COMPETITOR_DEFENSE_KEYWORDS)
        return title_has_lig and has_defense

    if category == CATEGORY_SPACE:
        return has_any_keyword(title, SPACE_KEYWORDS)

    if category == CATEGORY_GOVERNMENT:
        has_agency_in_title = has_any_keyword(title, [
            "국방부", "방사청", "방위사업청", "방추위", "방위사업추진위원회", "기품원", "add",
            "우주항공청", "kasa", "산업통상자원부", "산업부", "과학기술정보통신부", "과기부",
            "기획재정부", "합참", "합동참모본부", "방위사업청장", "국방장관",
        ])
        has_president_context = has_any_keyword(text, ["대통령", "대통령실"]) and is_aero_defense_space_relevant(text)
        has_briefing_context = is_government_briefing_item(title, summary) and is_aero_defense_space_relevant(text)
        # KAI·방산·항공우주 정책/예산/의결 관련 정부 기사는 무조건 발행
        has_kai_gov_context = (
            is_aero_defense_space_relevant(text) and
            has_any_keyword(text, ["보도자료", "브리핑", "의결", "예산", "사업", "협약", "협력", "지원", "추진", "계획", "정책", "발표"])
        )
        return has_agency_in_title or has_president_context or has_briefing_context or has_kai_gov_context

    if category == CATEGORY_KAI:
        non_kai_context_hit = has_any_keyword(text, NON_KAI_CONTEXT_KEYWORDS)
        generic_event_noise = is_generic_event_noise(str(item.get("title", "")), str(item.get("summary", "")), str(item.get("article_text", "")))
        kai_title_focus = has_any_keyword(title, ["한국항공우주산업", "강구영", "m&a", "인수", "합병", "민영화", "지분"] + KAI_PRODUCT_KEYWORDS) or has_exact_kai_token(title)
        business_focus = has_any_keyword(text, KAI_BUSINESS_KEYWORDS + KAI_CEO_CRITICAL_KEYWORDS)
        return kai_title_focus and business_focus and not non_kai_context_hit and not generic_event_noise

    return False


def load_sources_from_config() -> Dict[str, List[Dict[str, Any]]]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def load_sources_from_db(conn: sqlite3.Connection) -> Dict[str, List[Dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT feed_type, name, url, include_keywords, exclude_keywords
        FROM rss_sources
        WHERE active = 1
        ORDER BY feed_type, id
        """
    ).fetchall()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row["feed_type"], []).append(dict(row))
    return grouped


def fetch_xml(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=15) as response:
        return response.read()


def get_setting(conn: sqlite3.Connection, key: str) -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return ""
    return row[0]


def get_ai_provider(conn: sqlite3.Connection) -> str:
    provider = (get_setting(conn, "ai_provider") or "openai").strip().lower()
    return provider if provider in {"openai", "gemini"} else "openai"


def get_ai_api_key(conn: sqlite3.Connection) -> str:
    provider = get_ai_provider(conn)
    key_name = "gemini_api_key" if provider == "gemini" else "openai_api_key"
    return get_setting(conn, key_name)


def normalize_ai_model(model: str, provider: str) -> str:
    normalized = (model or "").strip().lower().replace("\u2011", "-").replace(" ", "-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    if normalized:
        return normalized
    return "gemini-3.7-flash" if provider == "gemini" else "gpt-4o-mini"


def chat_completion_content(
    api_key: str,
    model: str,
    messages: List[Dict[str, str]],
    *,
    provider: str = "cascade",
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout: int = 30,
) -> str:
    """
    3단계 지능형 LLM 연쇄 호출:
    1차 (1순위): Google Gemini (gemini-3.6-flash)
    2차 (2순위): Groq / xAI Grok (qwen/qwen3.8-27b 또는 grok-2)
    3차 (3순위): DeepSeek ($0.14/1M 토큰 백업)
    4차 (안전망): OpenAI
    """
    import os
    import sqlite3
    
    # 1. 키 로딩 (환경변수 또는 DB settings)
    gemini_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_AI_STUDIO_KEY") or ""
    grok_key = os.getenv("GROK_API_KEY") or os.getenv("GROQ_API_KEY") or os.getenv("XAI_API_KEY") or ""
    deepseek_key = os.getenv("DEEPSEEK_API_KEY") or ""
    openai_key = os.getenv("OPENAI_API_KEY") or ""

    if not gemini_key or not grok_key:
        try:
            db_p = "/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/data/ceo_briefing.db"
            if os.path.exists(db_p):
                conn = sqlite3.connect(db_p)
                for r in conn.execute("SELECT key, value FROM app_settings").fetchall():
                    if r[0] == "gemini_api_key" and not gemini_key: gemini_key = r[1]
                    if (r[0] == "grok_api_key" or r[0] == "groq_api_key") and not grok_key: grok_key = r[1]
                    if r[0] == "deepseek_api_key" and not deepseek_key: deepseek_key = r[1]
                conn.close()
        except Exception:
            pass

    gemini_key = (gemini_key or (api_key if provider == "gemini" else "")).strip()
    grok_key = grok_key.strip()
    deepseek_key = deepseek_key.strip()
    openai_key = (openai_key or (api_key if provider == "openai" else "")).strip()

    tiers = []
    # 1차: Google Gemini
    if gemini_key and not gemini_key.startswith("your_"):
        tiers.append(("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-3.6-flash", gemini_key))

    # 2차: Groq / xAI Grok
    if grok_key and not grok_key.startswith("your_"):
        if grok_key.startswith("gsk_"):
            tiers.append(("groq", "https://api.groq.com/openai/v1/chat/completions", "qwen/qwen3.8-27b", grok_key))
        else:
            tiers.append(("grok", "https://api.x.ai/v1/chat/completions", "grok-2-latest", grok_key))

    # 3차: DeepSeek
    if deepseek_key and not deepseek_key.startswith("your_"):
        tiers.append(("deepseek", "https://api.deepseek.com/chat/completions", "deepseek-chat", deepseek_key))

    # 4차: OpenAI
    if openai_key and not openai_key.startswith("your_"):
        tiers.append(("openai", "https://api.openai.com/v1/chat/completions", "gpt-4o-mini", openai_key))

    last_error = None
    for p_name, endpoint, default_model, k in tiers:
        try:
            target_model = model if (model and p_name in model) else default_model
            payload_dict: Dict[str, Any] = {
                "model": target_model,
                "messages": messages,
            }
            if temperature is not None:
                payload_dict["temperature"] = temperature
            if max_tokens is not None:
                payload_dict["max_tokens"] = max_tokens
            
            payload = json.dumps(payload_dict).encode("utf-8")
            req = urllib.request.Request(
                endpoint,
                data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {k}"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = json.loads(response.read().decode("utf-8"))
            return str(raw["choices"][0]["message"]["content"]).strip()
        except Exception as e:
            last_error = e
            continue

    if last_error:
        raise last_error
    return ""


def strip_html(value: str) -> str:
    plain = re.sub(r"<[^>]+>", "", value or "")
    return html.unescape(plain).strip()


def compact_text(value: str, max_len: int = 5000) -> str:
    text = re.sub(r"\s+", " ", (value or "")).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len]


def normalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qsl(parsed.query)
        filtered_qs = []
        for k, v in qs:
            k_lower = k.lower()
            if k_lower.startswith("utm_") or k_lower in (
                "ref", "sid", "gdid", "nvid", "input", "source", "campaign", "medium",
                # korea.kr 수집일 기반 파라미터 — newsId만 유효한 식별자
                "startdate", "enddate", "pageindex", "repcodetype", "repcode", "srchword", "period",
            ):
                continue
            filtered_qs.append((k, v))
        
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
            
        new_query = urllib.parse.urlencode(filtered_qs)
        normalized = urllib.parse.ParseResult(
            scheme="https",
            netloc=netloc,
            path=parsed.path.rstrip("/"),
            params=parsed.params,
            query=new_query,
            fragment=""
        )
        return normalized.geturl()
    except Exception:
        return url


def normalize_topic_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    
    text = re.sub(r"\[[^\]]+\]", " ", text)
    text = re.sub(r"\([^\)]+\)", " ", text)
    text = re.sub(r"\<[^\>]+\>", " ", text)
    
    text = re.sub(r"-\s*[가-힣\w\s]+뉴스\b", " ", text)
    text = re.sub(r"-\s*뉴스1\b", " ", text)
    text = re.sub(r"-\s*뉴시스\b", " ", text)
    
    text = re.sub(r"[^0-9A-Za-z가-힣\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    
    # 동의어 및 브랜드 사명 표준화 처리
    text = text.replace("한화에어로스페이스", "한화에어로")
    text = text.replace("한국항공우주산업", "kai")
    text = text.replace("한국항공우주", "kai")
    text = text.replace("항공우주산업", "kai")
    text = text.replace("lig넥스원", "lig")
    text = text.replace("넥스원", "lig")
    text = text.replace("주식", "지분")
    text = text.replace("획득해", "매입")
    text = text.replace("획득", "매입")
    
    return text


def extract_topic_tokens(title: str, summary: str) -> List[str]:
    base = normalize_topic_text(f"{title} {summary}")
    raw_tokens = [token for token in base.split(" ") if len(token) >= 2]
    filtered = [token for token in raw_tokens if token not in TOPIC_STOPWORDS]
    # frequent-first without duplicates
    counts: Dict[str, int] = {}
    for token in filtered:
        counts[token] = counts.get(token, 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    return [token for token, _ in ranked[:10]]


def build_topic_signature(item: Dict[str, Any]) -> str:
    tokens = extract_topic_tokens(str(item.get("title", "")), str(item.get("summary", "")))
    if not tokens:
        tokens = extract_topic_tokens(str(item.get("title", "")), "")
    plain = "|".join(sorted(tokens))
    if not plain:
        plain = normalize_topic_text(str(item.get("title", "")))[:120]
    return hashlib.sha1(plain.encode("utf-8")).hexdigest()


def publisher_score(publisher: str, link: str) -> int:
    host = normalize_host(publisher_from_link(link) or publisher)
    if host in MAJOR_PUBLISHER_SCORES:
        return MAJOR_PUBLISHER_SCORES[host]
    # subdomain fallback
    for major_host, score in MAJOR_PUBLISHER_SCORES.items():
        if host.endswith("." + major_host) or host == major_host:
            return score
    return 10


def normalize_host(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("www."):
        text = text[4:]
    return text


def stem_korean_word(word: str) -> str:
    if len(word) <= 2:
        return word
    suffixes = [
        "하기로", "하기", "했다", "한다", "했다", "했다면", "했으며", "했다가", "했을", "했음",
        "이다", "이며", "이고", "였던", "였다", "였다가", "였음", "였고",
        "에서", "에게", "으로", "로써", "로서",
        "하고", "하며", "하여", "하다",
        "된다", "됐다", "되는", "됨", "되고", "되며", "된다면", "될", "된", "되",
        "은", "는", "이", "가", "을", "를", "의", "에", "로", "과", "와", "도", "만", "인", "일", "임", "기"
    ]
    for suffix in sorted(suffixes, key=len, reverse=True):
        if word.endswith(suffix):
            stemmed = word[:-len(suffix)]
            if len(stemmed) >= 2:
                return stemmed
    return word


def _extract_event_anchors(title: str, summary: str) -> set:
    """제목+요약에서 행사명/장소명 등 고유 이벤트 앵커를 추출."""
    text = (title + " " + (summary or "")).lower()
    anchors = set()
    # 전시회/박람회/대전/컨퍼런스 패턴 — 고유 이름이 있을 때만
    event_patterns = [
        r"[가-힣]+\s*국방산업발전대전",
        r"[가-힣]+\s*에어쇼",
        r"[가-힣]+\s*방산전시회",
        r"[가-힣]+\s*박람회",
        r"[가-힣]+\s*컨퍼런스",
        r"[가-힣]+\s*포럼",
        r"[가-힣]+\s*세미나",
        r"adex\s*\d{4}",
        r"dsei\s*\d{4}",
        r"euronaval\s*\d{4}",
    ]
    for pat in event_patterns:
        for m in re.finditer(pat, text):
            anchors.add(m.group(0).replace(" ", ""))
    # 고유 행사 키워드 직접 매칭
    fixed_events = [
        "국방산업발전대전", "서울adex", "부산에어쇼", "서울에어쇼",
        "대전컨벤션센터", "코엑스방산전", "킨텍스방산",
    ]
    for ev in fixed_events:
        if ev in text:
            anchors.add(ev)
    if ("kai" in text or "한국항공우주" in text) and (
        "2026 지속가능경영" in text
        or "지속가능경영 보고서" in text
        or "지속가능경영(esg) 보고서" in text
        or ("esg" in text and "보고서" in text and ("발간" in text or "공시" in text))
    ):
        anchors.add("kai-2026-esg-report")
    return anchors


def _extract_hanwha_story_markers(title: str, summary: str) -> set:
    text = normalize_topic_text(f"{title} {summary}")
    markers = set()
    if not any(keyword in text for keyword in ["한화", "한화에어로", "한화시스템", "한화오션"]):
        return markers

    # 대형 투자/우주 인프라 이벤트
    if "55조" in text:
        markers.add("hanwha-55")
    if "2040" in text:
        markers.add("hanwha-2040")
    if "영남" in text:
        markers.add("hanwha-yeongnam")
    if "우주" in text or "우주항공" in text:
        markers.add("hanwha-space")
    if "발사체" in text:
        markers.add("hanwha-launcher")
    if "위성" in text:
        markers.add("hanwha-satellite")
    if "데이터센터" in text or "국방ai" in text or "ai 데이터센터" in text:
        markers.add("hanwha-ai-dc")
    if "23조" in text:
        markers.add("hanwha-23tr")
    if "20조" in text:
        markers.add("hanwha-20tr")
    if "12조" in text:
        markers.add("hanwha-12tr")

    # 대전 사업장 폭발/사고 이벤트
    if "폭발" in text or "사망사고" in text or "중대재해" in text:
        markers.add("hanwha-incident")
    if "세척기" in text or "세척실" in text:
        markers.add("hanwha-cleaner")
    if "추진제" in text:
        markers.add("hanwha-propellant")
    if "정전기" in text:
        markers.add("hanwha-static")
    if "대전사업장" in text or "대전 사업장" in text:
        markers.add("hanwha-daejeon")

    return markers


def is_aero_defense_space_relevant(text: str) -> bool:
    lowered = (text or "").lower()
    return has_any_keyword(
        lowered,
        [
            "국방", "방산", "우주", "우주항공", "항공", "방위산업",
            "전투기", "헬기", "발사체", "위성", "잠수함", "미사일",
            "무기체계", "방위사업", "k-방산", "k방산",
        ],
    )


def is_government_briefing_item(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    has_source_context = has_any_keyword(text, ["국방부", "방사청", "방위사업청", "합참", "우주항공청", "대통령실"])
    has_briefing_shape = has_any_keyword(text, ["정례브리핑", "서면 브리핑", "브리핑", "보도자료"])
    return has_source_context and has_briefing_shape


def is_same_topic_rule(item_a: Dict[str, Any], item_b: Dict[str, Any]) -> bool:
    url_a = normalize_url(str(item_a.get("link", "")))
    url_b = normalize_url(str(item_b.get("link", "")))
    if url_a and url_b and url_a == url_b:
        return True

    sig_a = build_topic_signature(item_a)
    sig_b = build_topic_signature(item_b)
    if sig_a and sig_b and sig_a == sig_b:
        return True

    # 행사/장소 앵커 기반 중복 체크:
    # 같은 전시회·박람회·컨퍼런스를 다룬 기사는 제목이 달라도 동일 주제로 처리
    anchors_a = _extract_event_anchors(
        str(item_a.get("title", "")), str(item_a.get("summary", ""))
    )
    anchors_b = _extract_event_anchors(
        str(item_b.get("title", "")), str(item_b.get("summary", ""))
    )
    if anchors_a and anchors_b and anchors_a.intersection(anchors_b):
        return True

    hanwha_markers_a = _extract_hanwha_story_markers(
        str(item_a.get("title", "")), str(item_a.get("summary", ""))
    )
    hanwha_markers_b = _extract_hanwha_story_markers(
        str(item_b.get("title", "")), str(item_b.get("summary", ""))
    )
    common_hanwha_markers = hanwha_markers_a.intersection(hanwha_markers_b)
    if common_hanwha_markers:
        if "hanwha-55" in common_hanwha_markers and len(common_hanwha_markers) >= 3:
            return True
        if "hanwha-incident" in common_hanwha_markers and len(common_hanwha_markers) >= 3:
            return True

    title_a = normalize_topic_text(str(item_a.get("title", "")))
    title_b = normalize_topic_text(str(item_b.get("title", "")))
    if not title_a or not title_b:
        return False

    # [Semantics-based Proper Noun & Event Duplicate Detection Rules]
    # 1. '김종출' 경영진 개편/구조조정 관련 동시 출현 룰
    if "김종출" in title_a and "김종출" in title_b:
        keywords = ["조직", "개편", "슬림", "인사", "개혁", "구조", "체제", "변화", "재정비"]
        if any(kw in title_a for kw in keywords) and any(kw in title_b for kw in keywords):
            return True

    # 2. '전략사령부' 관련 군사/정책/협약 동시 출현 룰 (Strategic Command는 사명 매칭만으로 극도로 확실한 이벤트)
    if "전략사령부" in title_a and "전략사령부" in title_b:
        return True

    # 3. KAI 관련 조직개편/구조조정 통합 매칭 룰 (인물명 유무에 관계없이 동일 시기 개편은 동일 이벤트로 판정)
    if "kai" in title_a and "kai" in title_b:
        keywords = ["조직개편", "조직 개편", "조직 슬림화", "부문 체제", "본부 체제", "구조조정"]
        a_has = any(kw in title_a for kw in keywords) or ("조직" in title_a and any(x in title_a for x in ["개편", "슬림", "재정비", "단순화", "부문", "본부", "체제"]))
        b_has = any(kw in title_b for kw in keywords) or ("조직" in title_b and any(x in title_b for x in ["개편", "슬림", "재정비", "단순화", "부문", "본부", "체제"]))
        if a_has and b_has:
            return True

    # 4. '해룡산단' 방산 투자 관련 동시 출현 룰 (해룡산단 내 300억 규모 투자는 극히 드문 고유 이벤트)
    if "해룡산단" in title_a and "해룡산단" in title_b:
        return True

    # 5. 한화에어로 KAI 지분 관련 동시 출현 룰 (KAI 지분 변동은 항상 최신 마일스톤만 표시하고 과거 이력은 중복 차단)
    if "한화에어로" in title_a and "kai" in title_a and "지분" in title_a:
        if "한화에어로" in title_b and "kai" in title_b and "지분" in title_b:
            return True

    if title_a == title_b:
        return True
    if len(title_a) > 12 and title_a in title_b:
        return True
    if len(title_b) > 12 and title_b in title_a:
        return True

    # Title-only stemmed token overlap check for Korean agglutinative duplication
    t_tokens_a = {stem_korean_word(tk) for tk in title_a.split(" ") if len(tk) >= 2 and tk not in TOPIC_STOPWORDS}
    t_tokens_b = {stem_korean_word(tk) for tk in title_b.split(" ") if len(tk) >= 2 and tk not in TOPIC_STOPWORDS}
    if t_tokens_a and t_tokens_b:
        t_overlap = len(t_tokens_a.intersection(t_tokens_b))
        min_len = min(len(t_tokens_a), len(t_tokens_b))
        t_overlap_ratio = t_overlap / max(1, min_len)
        if min_len >= 2 and t_overlap_ratio >= 0.6:
            return True

    similarity = SequenceMatcher(None, title_a, title_b).ratio()
    if similarity >= 0.88:
        return True

    tokens_a = set(extract_topic_tokens(str(item_a.get("title", "")), str(item_a.get("summary", ""))))
    tokens_b = set(extract_topic_tokens(str(item_b.get("title", "")), str(item_b.get("summary", ""))))
    if not tokens_a or not tokens_b:
        return False

    overlap = len(tokens_a.intersection(tokens_b))
    overlap_ratio = overlap / max(1, min(len(tokens_a), len(tokens_b)))
    
    if similarity >= 0.75 and overlap_ratio >= 0.5:
        return True
    if overlap_ratio >= 0.7 and similarity >= 0.35:
        return True
    return False


def is_same_topic_with_openai(api_key: str, model: str, item_a: Dict[str, Any], item_b: Dict[str, Any], provider: str = "openai") -> bool:
    prompt = (
        "두 기사가 사실상 동일한 뉴스 주제이거나, 동일한 이벤트/행사/보도자료를 다루고 있어서 중복 기사로 처리해야 하는지 판단하세요.\n"
        "예를 들어, 한 기사는 특정 기업의 수상 소식이고 다른 기사는 그 수상식이 속한 전체 행사의 종합 스케치 기사라면, 핵심 이벤트가 중복되므로 'same'으로 판단해야 합니다.\n"
        "동일하거나 극히 중복되는 주제면 same, 서로 다른 별개의 독립된 뉴스 사건이면 different 만 답하세요.\n\n"
        f"[기사A] 제목: {item_a.get('title', '')}\n요약: {compact_text(str(item_a.get('summary', '')), 400)}\n\n"
        f"[기사B] 제목: {item_b.get('title', '')}\n요약: {compact_text(str(item_b.get('summary', '')), 400)}\n"
    )
    answer = chat_completion_content(
        api_key,
        model,
        [{"role": "user", "content": prompt}],
        provider=provider,
        temperature=0.0,
        timeout=20,
    ).lower()
    return "same" in answer and "different" not in answer


def ensure_topic_memory_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_topic_memory (
            signature TEXT PRIMARY KEY,
            representative_title TEXT NOT NULL,
            preferred_publisher TEXT NOT NULL DEFAULT '',
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def ensure_deleted_items_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_feed_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            feed_type TEXT NOT NULL,
            link TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(feed_type, link)
        )
        """
    )


def is_deleted_item(conn: sqlite3.Connection, feed_type: str, link: str) -> bool:
    if not link:
        return False
    row = conn.execute(
        "SELECT 1 FROM deleted_feed_items WHERE feed_type = ? AND link = ?",
        (feed_type, link),
    ).fetchone()
    return row is not None


def update_topic_memory(conn: sqlite3.Connection, signature: str, title: str, preferred_publisher: str) -> None:
    if not signature:
        return
    conn.execute(
        """
        INSERT INTO article_topic_memory(signature, representative_title, preferred_publisher, last_seen_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(signature) DO UPDATE SET
            representative_title = excluded.representative_title,
            preferred_publisher = excluded.preferred_publisher,
            last_seen_at = CURRENT_TIMESTAMP
        """,
        (signature, compact_text(title, 240), preferred_publisher),
    )


def get_memory_preferred_publisher(conn: sqlite3.Connection, signature: str) -> str:
    if not signature:
        return ""
    row = conn.execute(
        "SELECT preferred_publisher FROM article_topic_memory WHERE signature = ?",
        (signature,),
    ).fetchone()
    return str(row[0] or "") if row else ""


def choose_preferred_item(incoming: Dict[str, Any], existing: Dict[str, Any]) -> str:
    incoming_score = publisher_score(str(incoming.get("publisher", "")), str(incoming.get("link", "")))
    existing_score = publisher_score(str(existing.get("article_publisher", existing.get("publisher", ""))), str(existing.get("link", "")))
    if incoming_score > existing_score:
        return "incoming"
    if incoming_score < existing_score:
        return "existing"
    incoming_ts = parse_iso_datetime(str(incoming.get("published_at", "")))
    existing_ts = parse_iso_datetime(str(existing.get("article_published_at", existing.get("published_at", ""))))
    if incoming_ts and existing_ts:
        return "incoming" if incoming_ts > existing_ts else "existing"
    return "existing"


def fetch_article_text(link: str) -> str:
    if not link:
        return ""
    request = urllib.request.Request(
        link,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
    except Exception:
        return ""
    try:
        html_text = raw.decode(charset, errors="ignore")
    except Exception:
        html_text = raw.decode("utf-8", errors="ignore")
    html_text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html_text)
    html_text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html_text)
    plain = strip_html(html_text)
    return compact_text(plain, max_len=7000)


def classify_with_rules(item: Dict[str, Any], feed_type: str) -> str:
    title = str(item.get("title", ""))
    summary = str(item.get("summary", ""))
    article_text = str(item.get("article_text", ""))
    link = str(item.get("link", ""))
    publisher = str(item.get("publisher", "") or item.get("source", ""))
    text = " ".join(
        [
            title,
            summary,
            article_text,
            link,
        ]
    ).lower()
    title_lower = title.lower()
    title_temp = title_lower.replace("kaist", "").replace("카이스트", "")

    # [상호 배타성 규칙] 제목에 한화/LIG 경쟁사명(또는 고유 제품명)만 명시되고 KAI 언급이 없는 경우 절대 KAI로 오분류 금지
    has_hanwha_brand_in_title = has_any_keyword(title_lower, ["한화에어로", "한화시스템", "한화오션", "한화디펜스", "한화"])
    has_hanwha_product_in_title = has_any_keyword(title_lower, HANWHA_PRODUCT_KEYWORDS)
    has_lig_brand_in_title = has_any_keyword(title_lower, ["lig", "넥스원"])
    has_lig_product_in_title = has_any_keyword(title_lower, LIG_PRODUCT_KEYWORDS)
    has_kai_company_in_title = has_kai_company_mention(title_temp)
    has_kai_product_in_title = has_kai_product_mention(title_lower)
    has_kai_brand_in_title = has_kai_company_in_title or has_kai_product_in_title

    has_competitor_brand_in_title = has_hanwha_brand_in_title or has_lig_brand_in_title
    has_competitor_product_in_title = has_hanwha_product_in_title or has_lig_product_in_title
    has_non_kai_primary_title = has_any_keyword(title_lower, NON_KAI_PRIMARY_TITLE_KEYWORDS)
    mixed_hanwha_kai_ownership_story = (
        has_hanwha_brand_in_title
        and has_kai_brand_in_title
        and has_any_keyword(
            text,
            ["지분", "경영권", "경영 참여", "민영화", "인수", "합병", "최대주주", "주주", "협력", "매출 전환"],
        )
    )

    # 제목에 경쟁사명/경쟁사 제품명만 있고 KAI가 없는 경우 — noise 체크 후 분류
    if (has_competitor_brand_in_title or has_competitor_product_in_title) and not has_kai_brand_in_title:
        if is_competitor_noise_story(title, summary) or is_political_campaign_noise(title, summary):
            return "trash"
        if is_real_competitor_story(title, summary):
            # 한화 제품 또는 한화 브랜드 → hanwha
            if has_hanwha_brand_in_title or has_hanwha_product_in_title:
                return CATEGORY_HANWHA
            # LIG 제품(천궁·천무 등) 또는 LIG 브랜드 → lig
            if has_lig_brand_in_title or has_lig_product_in_title:
                return CATEGORY_LIG
        return "trash"

    # 한화와 KAI가 함께 언급되어도, 지분/경영권/민영화 맥락이면 한화 기사로 분류
    if mixed_hanwha_kai_ownership_story:
        return CATEGORY_HANWHA

    if has_non_kai_primary_title and not has_kai_company_in_title:
        if is_aero_defense_space_relevant(text):
            return CATEGORY_SPACE
        return CATEGORY_REFERENCE

    kai_mentioned = has_any_keyword(text, KAI_STRONG_KEYWORDS)
    kai_company_mentioned = has_kai_company_mention(text)
    kai_product_hit = has_kai_product_mention(text)
    kai_business_hit = has_any_keyword(text, KAI_BUSINESS_KEYWORDS)
    hanwha_hit = has_any_keyword(text, HANWHA_KEYWORDS)
    lig_hit = has_any_keyword(text, LIG_KEYWORDS)
    real_competitor_story = is_real_competitor_story(title, summary)
    real_kai_story = is_kai_core_story(title, summary, article_text)
    space_hit = has_any_keyword(text, SPACE_KEYWORDS)
    government_hit = has_any_keyword(text, GOVERNMENT_KEYWORDS)
    non_kai_context_hit = has_any_keyword(text, NON_KAI_CONTEXT_KEYWORDS)
    generic_event_noise = is_generic_event_noise(title, summary, article_text)
    partner_or_supplier_story = is_partner_or_supplier_story(title, text)
    stock_market_roundup = is_stock_market_roundup(title)
    kai_focus_in_title = has_any_keyword(
        title_lower,
        ["kf-21", "fa-50", "t-50", "수리온"],
    ) or has_kai_company_in_title
    kai_title_business_focus = (
        kai_focus_in_title
        and not (has_competitor_brand_in_title and not has_kai_brand_in_title)
        and has_any_keyword(
            text,
            KAI_BUSINESS_KEYWORDS
            + KAI_CEO_CRITICAL_KEYWORDS
            + [
                "실적", "매출", "영업이익", "수익성", "성장",
                "수출", "수주", "계약", "납품", "양산",
                "중동", "사우디", "u.a.e", "uae", "폴란드", "말레이시아",
                "kf-21", "fa-50", "t-50", "수리온",
            ],
        )
    )

    # 정부기관 및 군 조직(국방부, 방사청, 대통령실, 합참 등) 피드이거나 본문에 해당 기관이 언급된 경우
    is_gov_feed = any(kw in publisher.lower() for kw in ["국방부", "방사청", "방위사업청", "정부", "대통령실", "청와대", "국토교통부", "기획예산처"])
    has_gov_agency_in_text = has_any_keyword(text, ["국방부", "방사청", "방위사업청", "국방과학연구소", "add", "국방기술품질원", "기품원", "합참", "합동참모본부", "대통령실", "국방연구원", "방위사업추진위원회", "방추위"])
    has_president_aero_defense_context = has_any_keyword(text, ["대통령", "대통령실"]) and is_aero_defense_space_relevant(text)

    # 1) KAI 협력사 비즈니스 이벤트 기사 (M&A·IPO·매각 등) — KAI 보다 먼저 체크
    #    (율곡 인수전처럼 협력사 M&A는 KAI가 언급돼도 reference로 가야 함)
    if partner_or_supplier_story:
        return CATEGORY_REFERENCE

    # 1-b) 주가 시황·시세 종합 기사 → trash (방산주 강세, [특징주] 등)
    if stock_market_roundup:
        return "trash"

    # 2) 제목 주체가 KAI/KAI 제품이고 실적·수출·수주 문맥이면 대통령/정부기관/G2G 언급보다 KAI 우선
    if kai_title_business_focus:
        return CATEGORY_KAI

    # 2-b) 대통령/대통령실이 항공·방산·우주 산업을 언급한 경우는 정부기관 우선
    if has_president_aero_defense_context:
        return CATEGORY_GOVERNMENT

    # 3) KAI 핵심 기사 (CEO 관점 중요도 기준)
    if real_kai_story and not non_kai_context_hit:
        return CATEGORY_KAI

    # 4) 행사성/일반 노이즈
    if generic_event_noise:
        return CATEGORY_REFERENCE

    # 5) 우주항공 분야 기사 (단, KAI/경쟁사가 주체인 기사는 제외)
    if space_hit and not kai_focus_in_title and not kai_company_mentioned:
        # 기념식 수상자 목록 위주 기사는 space 아닌 trash
        is_award_ceremony_only = (
            has_any_keyword(title_lower, ["기념식", "정부포상", "유공자", "수상자", "훈장", "포장"])
            and not has_any_keyword(text, ["발사", "누리호", "나로호", "kasa", "우주항공청", "발사체", "위성 발사", "우주 정책", "우주 전략"])
        )
        if is_award_ceremony_only:
            return "trash"
        return CATEGORY_SPACE

    # 6) 군 장성 인사 기사 — 공군/육군항공/해군항공 관련 고위직 교체는 KAI 고객 변동
    if has_any_keyword(text, ["장성 인사", "장성급 인사", "장군 인사", "인사발령", "진급 인사"]):
        if has_any_keyword(text, ["공군", "육군", "해군", "해병대", "장성", "중장", "소장", "준장"]):
            return CATEGORY_GOVERNMENT

    # 6-c) 정부기관 관련 기사 (방산/항공 정책·KAI 관련 정부 결정)
    if is_gov_feed or (has_gov_agency_in_text and has_any_keyword(text, ["방산", "항공", "전투기", "헬기", "무기", "전력화", "방위사업", "국방예산"])):
        return CATEGORY_GOVERNMENT

    # 6-b) 방산 정책·산업 생태계·우주 인프라 관련 정부/기관 주도 논의
    #     (기획예산처 간담회, 저궤도 위성통신 TF, K-방산 생태계 구축 방안 등)
    if has_any_keyword(text, ["방산 생태계", "방산생태계", "방산 강국", "방산강국",
                               "방위산업 육성", "방위산업육성", "방산 수출 전략", "방산수출전략",
                               "저궤도 위성", "저궤도위성", "위성통신 tf", "위성통신tf",
                               "k-방산 전략", "k방산", "k-방산 육성"]):
        if has_any_keyword(text, ["정부", "국방부", "방사청", "기획예산처", "산업부", "과기부", "tf", "태스크포스", "간담회", "회의", "논의", "검토"]):
            return CATEGORY_GOVERNMENT

    # 6) 경쟁사(한화/LIG) 방산 핵심 기사
    if real_competitor_story and not kai_focus_in_title and not kai_product_hit:
        if hanwha_hit:
            return CATEGORY_HANWHA
        if lig_hit:
            return CATEGORY_LIG

    # KAI가 언급되더라도 대학/지역/일반 이슈 중심이면 참고
    if kai_mentioned and non_kai_context_hit:
        return CATEGORY_REFERENCE

    # 경쟁사 피드에서 들어온 일반 기사 fallback
    if feed_type == "competitor":
        if real_competitor_story:
            if hanwha_hit:
                return CATEGORY_HANWHA
            if lig_hit:
                return CATEGORY_LIG
        return "trash"

    # KAI 언급만 있고 핵심 주제가 아니면 참고
    if kai_mentioned:
        return CATEGORY_REFERENCE
    return CATEGORY_REFERENCE


def get_manual_training_examples(conn: sqlite3.Connection, limit: int = 20) -> List[Dict[str, str]]:
    rows = conn.execute(
        """
        SELECT title, summary, article_category
        FROM feed_items
        WHERE category_manual = 1
        ORDER BY ROWID DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [
        {
            "title": str(row[0] or ""),
            "summary": compact_text(str(row[1] or ""), 200),
            "category": str(row[2] or CATEGORY_REFERENCE),
        }
        for row in rows
    ]


def classify_with_openai(api_key: str, model: str, item: Dict[str, Any], training_examples: List[Dict[str, str]], feed_type: str = "company", provider: str = "openai") -> Tuple[str, bool, bool]:
    model = normalize_ai_model(model, provider)
    examples_text = ""
    if training_examples:
        lines = []
        for ex in training_examples[:12]:
            lines.append(f"- 제목: {ex['title']} | 요약: {ex['summary']} | 카테고리: {ex['category']}")
        examples_text = "참고할 사용자 수동 분류 예시 (최우선 반영):\n" + "\n".join(lines) + "\n\n"

    # feed_type별 소스 컨텍스트 힌트 — AI가 출처를 고려해 분류하도록 유도
    _SOURCE_HINT = {
        "competitor": (
            "⚠️ 이 기사는 [경쟁사 모니터링] 소스(한화·LIG 추적 피드)에서 수집됐습니다.\n"
            "→ 기사 주인공이 한화 또는 LIG 계열사이면 hanwha/lig로 분류하세요.\n"
            "→ KAI가 기사 제목의 주어가 아닌 한 kai로 분류하지 마세요.\n"
            "→ KAI 지분 매각·인수전처럼 KAI가 직접 영향받는 내용이면 kai 허용.\n"
        ),
        "government": (
            "⚠️ 이 기사는 [정부/정책] 소스(국방부·방사청·정부 브리핑 등)에서 수집됐습니다.\n"
            "→ 정부 정책·예산·규정이 기사 본론이면 government로 분류하세요.\n"
            "→ KAI 제품명(KF-21·FA-50·수리온 등)이 제목의 주어이거나 방추위가 KAI 제품을 직접 결정한 내용이어야 kai 허용.\n"
            "→ 단순히 KAI가 언급만 된 정부 보도자료라면 government를 유지하세요.\n"
        ),
        "company": (
            "이 기사는 [KAI 관련 언론] 소스(KAI·항공우주 키워드 뉴스 피드)에서 수집됐습니다.\n"
            "→ 기사 내용에 따라 kai/hanwha/lig/government/space/reference/trash 중 가장 적합한 카테고리를 선택하세요.\n"
        ),
    }.get(feed_type, "")

    prompt = (
        "당신은 KAI(한국항공우주산업) CEO 전용 뉴스 필터입니다.\n"
        "CEO가 읽어야 할 기사만 남기고 나머지는 반드시 trash로 버려야 합니다.\n"
        "분류가 애매하면 trash를 선택하세요. 쓸데없는 기사를 남기는 것이 놓치는 것보다 더 나쁩니다.\n\n"
        + (_SOURCE_HINT + "\n" if _SOURCE_HINT else "") +
        "=== 분류 기준 ===\n\n"
        "[kai] — KAI CEO에게 직접적·즉각적 영향이 있는 기사만\n"
        "  ✅ KAI 지분 변동, 민영화, 경영권 분쟁, 최대주주 변경\n"
        "  ✅ KAI 제품(KF-21·FA-50·T-50·수리온·LAH) 수출 계약 성사/무산\n"
        "  ✅ 방위사업추진위원회(방추위)의 KAI 제품 전력화·양산 결정\n"
        "  ✅ 국방예산안에서 KAI 제품 예산 통과·삭감\n"
        "  ✅ 대통령·국방장관·방사청장이 KAI 또는 KAI 제품을 직접 언급\n"
        "  ✅ KAI 제품 결함·추락사고·품질 논란·감항 문제\n"
        "  ✅ KAI CEO 교체, 대규모 조직개편, 핵심 전략 발표\n"
        "  ✅ KAI 헬기(수리온·마린온·LAH) 개발·양산·운용 심층 기사\n"
        "  ✅ KAI 우주사업 (차세대중형위성 등) 주요 성과\n"
        "  ❌ 단순 MOU·업무협약·행사 참가\n"
        "  ❌ KAI 주가 등락 단독 기사\n"
        "  ❌ '한국항공우주산업'이 다른 회사 사례로 스치듯 언급된 기사\n\n"
        "[government] — 방산/항공 정책에서 KAI에 실질 영향 있는 정부·기관 주도 결정\n"
        "  ✅ 국방부·방사청·방추위의 방산 예산·사업·정책 결정\n"
        "  ✅ K-방산 수출 규정, 방위산업법 개정, K-방산 생태계 구축 방안\n"
        "  ✅ KAI 제품이 포함된 전력증강사업 정부 결정\n"
        "  ✅ 정부 주도 저궤도 위성통신 TF, 우주산업 육성 전략 발표\n"
        "  ❌ 일반 안보 뉴스, 군사 훈련, 전작권 이슈 (KAI 무관)\n"
        "  ❌ AI·반도체·산업정책 (방산과 무관)\n\n"
        "[hanwha] — KAI와 경쟁 구도에서 중요한 한화 방산 뉴스\n"
        "  ✅ 한화에어로스페이스·한화시스템의 방산 수주·수출·M&A·대규모 실적\n"
        "  ✅ K9 자주포·천무·천궁 등 한화 방산 제품의 해외 계약\n"
        "  ✅ 한화가 참여하는 K-방산 원팀 대형 수주전 (캐나다·폴란드 등)\n"
        "  ✅ 한화 방산 부문 CEO·대표의 전략 발표·인터뷰\n"
        "  ❌ 한화 폭발사고·화재 수습·산업재해·노동부 조사 (경쟁 구도와 무관)\n"
        "  ❌ 선거·정치 뉴스에서 '한화'가 언급된 기사\n"
        "  ❌ 한화의 수소에너지·보험·금융·호텔·유통 사업\n"
        "  ❌ 한화 봉사활동·사회공헌·장학금\n\n"
        "[lig] — KAI와 경쟁 구도에서 중요한 LIG 방산 뉴스\n"
        "  ✅ LIG넥스원·LIG D&A의 방산 수주·수출·M&A·핵심 기술개발\n"
        "  ✅ LIG D&A 대표(신익현 등) CEO 전략·인수전 등 경영 행보\n"
        "  ❌ LIG 봉사·CSR·채용행사·학술대회\n\n"
        "[space] — 우주항공청·누리호 등 순수 우주분야 (KAI가 주체 아닌 경우)\n"
        "  ✅ 우주항공청(KASA), 누리호, 스페이스X, 위성 발사 관련\n"
        "  ✅ 정부의 우주 정책·산업 전략 (KAI가 주체 아닌 것)\n"
        "  ❌ KAI가 주요 주체인 우주/위성 기사는 kai로 분류\n"
        "  ❌ 우주항공의 날 기념식 수상자 나열 기사 → trash\n\n"
        "[reference] — KAI 핵심 협력사 관련 기사\n"
        "  ✅ 율곡·아스트·ASTK: 제목에 이 회사 이름이 나오면 무조건 reference (이벤트 여부 무관)\n"
        "  ✅ 기타 협력사(켄코아·하이즈항공·에이엔에이치 등)의 M&A·IPO·매각·파산\n"
        "  ❌ 협력사명이 스치듯 언급된 기사 (율곡·아스트·ASTK 제외)\n"
        "  ❌ 방산 일반 뉴스, 업계 동향 종합 기사\n\n"
        "[trash] — 아래에 해당하면 무조건 trash\n"
        "  • 선거·정치 기사 (어떤 회사가 언급되든)\n"
        "  • 한화/LIG 사고수습·노동쟁의·경찰 수사·유가족 관련\n"
        "  • 증시·주가·코스피·환율 단독 기사\n"
        "  • 젠슨황·엔비디아·삼성전자 등 방산 무관 IT/대기업 기사\n"
        "  • 봉사·기부·장학금·사회공헌\n"
        "  • 대학교·스승의날·교수·학생 관련\n"
        "  • 여러 기업 단신 모음, 인사·부음 종합\n"
        "  • 회계기준원(KAI)·IFRS·회계공시 (방산 무관)\n"
        "  • 일반 경제/정치/사회 뉴스\n\n"
        "[is_breaking: true] — 극히 엄격하게 적용\n"
        "  • KAI 지분 인수/민영화 '확정' 뉴스\n"
        "  • KAI 제품 조 단위 이상 수출 '계약 체결 확정'\n"
        "  • 방추위 KAI 제품 전력화·양산 '결정'\n"
        "  • KAI 제품(KF-21·FA-50·T-50·수리온·LAH/차세대 공격헬기)의 중대 결함·비행중단·감항/품질 리스크\n"
        "  ※ '추진', '검토', '논의', '전망'은 false\n\n"
        "답변 형식: 카테고리|clear or ambiguous|true or false\n"
        "예시: kai|clear|false\n\n"
        + examples_text +
        f"제목: {item.get('title', '')}\n"
        f"요약: {compact_text(str(item.get('summary', '')), 500)}\n"
        f"본문: {compact_text(str(item.get('article_text', '')), 2500)}\n"
    )
    answer = chat_completion_content(
        api_key,
        model,
        [{"role": "user", "content": prompt}],
        provider=provider,
        temperature=0.0,
        timeout=30,
    ).lower()
    answer = re.sub(r"[^a-z|]", "", answer)
    parts = answer.split("|")
    category = parts[0] if len(parts) > 0 else CATEGORY_REFERENCE
    if category not in ALLOWED_CATEGORIES and category != "trash":
        category = CATEGORY_REFERENCE
    is_clear = (len(parts) > 1 and parts[1] == "clear")
    is_breaking = (len(parts) > 2 and parts[2] == "true")
    return category, is_clear, is_breaking


def attach_article_text(items: List[Dict[str, Any]], max_fetch: int = 20) -> None:
    for index, item in enumerate(items):
        if index >= max_fetch:
            item["article_text"] = ""
            continue
        item["article_text"] = fetch_article_text(item.get("link", ""))


def publisher_from_link(link: str) -> str:
    if not link:
        return ""
    try:
        host = (urllib.parse.urlparse(link).hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def has_hangul(text: str) -> bool:
    return bool(re.search(r"[가-힣]", text or ""))


def keyword_matches_text(text: str, keyword: str) -> bool:
    candidate = (keyword or "").strip()
    if not candidate:
        return False
    if re.fullmatch(r"[A-Za-z0-9]+", candidate):
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(candidate)}(?![A-Za-z0-9])", re.IGNORECASE)
        return bool(pattern.search(text))
    return candidate.lower() in text.lower()


def filter_naver_news_items(items: List[Dict[str, Any]], source_include_keywords: List[str]) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for item in items:
        combined_text = " ".join([item.get("title", ""), item.get("summary", "")]).strip()
        if not has_hangul(combined_text):
            continue
        if source_include_keywords and not any(keyword_matches_text(combined_text, keyword) for keyword in source_include_keywords):
            continue
        filtered.append(item)
    return filtered


def fetch_naver_news_items(conn: sqlite3.Connection, source_url: str) -> List[Dict[str, Any]]:
    client_id = get_setting(conn, "naver_client_id")
    client_secret = get_setting(conn, "naver_client_secret")
    if not client_id or not client_secret:
        raise RuntimeError("Naver News API credentials are missing.")

    parsed = urllib.parse.urlparse(source_url)
    params = urllib.parse.parse_qs(parsed.query)
    query = (params.get("query") or [""])[0].strip()
    if not query:
        raise RuntimeError("Naver News source query is empty.")

    request_params = {
        "query": query,
        "display": (params.get("display") or ["100"])[0],
        "start": (params.get("start") or ["1"])[0],
        "sort": (params.get("sort") or ["date"])[0],
    }
    request_url = f"https://openapi.naver.com/v1/search/news.json?{urllib.parse.urlencode(request_params)}"
    request = urllib.request.Request(
        request_url,
        headers={
            "X-Naver-Client-Id": client_id,
            "X-Naver-Client-Secret": client_secret,
        },
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))

    source_include_keywords = parse_keywords((params.get("include") or [""])[0])
    items: List[Dict[str, Any]] = []
    for item in payload.get("items", []):
        published_at = parse_pub_datetime(item.get("pubDate") or "")
        original_link = (item.get("originallink") or "").strip()
        article_link = original_link or (item.get("link") or "").strip()
        items.append(
            {
                "title": strip_html(item.get("title") or ""),
                "link": article_link,
                "summary": strip_html(item.get("description") or ""),
                "published_at": published_at.isoformat() if published_at else "",
                "publisher": publisher_from_link(article_link),
            }
        )
    items = [item for item in items if item["title"] and item["link"]]
    return filter_naver_news_items(items, source_include_keywords)


def fetch_korea_gov_items(conn: sqlite3.Connection, source_url: str) -> List[Dict[str, Any]]:
    """
    korea-gov://pressRelease?repCode=B00008 형식 URL을 파싱해
    korea.kr 보도자료 목록을 스크래핑한다.
    RSS가 폐지되어 HTML POST 방식으로 대체.
    """
    import html as _html_mod
    import urllib.parse as _urlparse
    import urllib.request as _urlreq

    parsed = _urlparse.urlparse(source_url)
    params = dict(_urlparse.parse_qsl(parsed.query))
    rep_code = params.get("repCode", "")

    base_url = "https://www.korea.kr/briefing/pressReleaseList.do"
    # 최근 30일치 요청
    form_data = _urlparse.urlencode({
        "pageIndex": "1",
        "repCode": rep_code,
        "period": "month",
        "srchWord": "",
    }).encode()
    req = _urlreq.Request(
        base_url,
        data=form_data,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": "https://www.korea.kr/briefing/pressReleaseList.do",
        },
    )
    try:
        with _urlreq.urlopen(req, timeout=15) as resp:
            raw_html = resp.read().decode("utf-8", "ignore")
    except Exception as exc:
        print(f"[korea-gov] fetch failed for repCode={rep_code}: {exc}")
        return []

    html_text = _html_mod.unescape(raw_html)

    # 기사 href + 제목 + 날짜 파싱
    # <a href="/briefing/pressReleaseView.do?newsId=156769508&pageIndex=...">
    #   <strong>제목</strong> ... 2026-07-03
    pattern = re.compile(
        r'href=["\'](?:https?://www\.korea\.kr)?(/briefing/pressReleaseView\.do\?newsId=(\d+)[^"\']*)["\']'
        r'.*?<strong>(.*?)</strong>'
        r'.*?(\d{4}[-\.]\d{2}[-\.]\d{2})',
        re.DOTALL,
    )

    items: List[Dict[str, Any]] = []
    seen_ids: set = set()
    for m in pattern.finditer(html_text):
        path, news_id, title_raw, date_raw = m.group(1), m.group(2), m.group(3), m.group(4)
        if news_id in seen_ids:
            continue
        seen_ids.add(news_id)

        title = re.sub(r"<[^>]+>", "", title_raw).strip()
        if not title:
            continue

        date_str = date_raw.replace(".", "-")
        pub_dt = datetime.fromisoformat(date_str + "T09:00:00+09:00").astimezone(timezone.utc)
        # newsId만 남긴 정규화 URL — 수집 날짜(startDate/endDate)가 달라도 동일 기사로 인식
        article_url = f"https://www.korea.kr/briefing/pressReleaseView.do?newsId={news_id}"

        items.append({
            "title": title,
            "link": article_url,
            "summary": "",
            "published_at": pub_dt.isoformat(),
            "publisher": "대한민국 정책브리핑",
        })

    return items


def parse_feed(xml_bytes: bytes) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_bytes)
    if root.tag.endswith("feed"):
        return parse_atom(root)
    return parse_rss(root)


def parse_iso_datetime(raw: str) -> datetime | None:
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    try:
        if candidate.endswith("Z"):
            candidate = candidate[:-1] + "+00:00"
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def parse_pub_datetime(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw.strip())
    except (TypeError, ValueError, IndexError):
        return parse_iso_datetime(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_iso_date(raw: str | None) -> str:
    """RFC 822 또는 ISO 날짜 문자열을 UTC +00:00 ISO 포맷으로 정규화. 실패하면 현재 시각."""
    if raw:
        dt = parse_pub_datetime(str(raw))
        if dt:
            return dt.astimezone(timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


def extract_rss_published_at(item: ET.Element) -> str:
    dc_date = item.findtext("{http://purl.org/dc/elements/1.1/}date") or ""
    parsed = parse_iso_datetime(dc_date) or parse_pub_datetime(item.findtext("pubDate") or "")
    return parsed.isoformat() if parsed else ""


def parse_rss(root: ET.Element) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for item in root.findall("./channel/item"):
        title_node = item.find("title")
        description_node = item.find("description")
        title = "".join(title_node.itertext()).strip() if title_node is not None else (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_tag = strip_html(item.findtext("source") or "")
        summary_text = "".join(description_node.itertext()).strip() if description_node is not None else (item.findtext("description") or "").strip()
        summary = strip_html(summary_text) or title
        title = strip_html(title)
        if not title:
            continue
        items.append(
            {
                "title": title,
                "link": link,
                "summary": summary,
                "published_at": extract_rss_published_at(item),
                "publisher": source_tag or publisher_from_link(link),
            }
        )
    return items


def parse_atom(root: ET.Element) -> List[Dict[str, Any]]:
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    items: List[Dict[str, Any]] = []
    for entry in root.findall("atom:entry", namespace):
        title = (entry.findtext("atom:title", default="", namespaces=namespace) or "").strip()
        summary = (
            entry.findtext("atom:summary", default="", namespaces=namespace)
            or entry.findtext("atom:content", default="", namespaces=namespace)
            or ""
        ).strip() or title
        link = ""
        for link_node in entry.findall("atom:link", namespace):
            href = (link_node.attrib.get("href") or "").strip()
            rel = (link_node.attrib.get("rel") or "alternate").strip()
            if href and rel == "alternate":
                link = href
                break
            if href and not link:
                link = href
        if not title:
            continue
        published_raw = (
            entry.findtext("atom:updated", default="", namespaces=namespace)
            or entry.findtext("atom:published", default="", namespaces=namespace)
            or ""
        )
        published_at = parse_iso_datetime(published_raw)
        items.append(
            {
                "title": title,
                "link": link,
                "summary": summary,
                "published_at": published_at.isoformat() if published_at else "",
                "publisher": publisher_from_link(link),
            }
        )
    return items


def parse_keywords(raw: str) -> List[str]:
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


def load_keywords(conn: sqlite3.Connection, feed_type: str) -> tuple[str, List[str], List[str]]:
    mode_row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (f"rss_filter_mode_{feed_type}",)).fetchone()
    include_row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (f"rss_keywords_{feed_type}",)).fetchone()
    exclude_row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (f"rss_exclude_keywords_{feed_type}",)).fetchone()
    mode = mode_row[0] if mode_row else "all"
    include_keywords = parse_keywords(include_row[0] if include_row else "")
    exclude_keywords = parse_keywords(exclude_row[0] if exclude_row else "")
    return mode, include_keywords, exclude_keywords


def get_incremental_window_start(conn: sqlite3.Connection, feed_type: str) -> datetime:
    key = f"rss_last_checked_{feed_type}"
    last_row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    last_checked = parse_iso_datetime(last_row[0] if last_row else "")
    if last_checked is None:
        return datetime.now(timezone.utc) - timedelta(days=30)
    # Re-scan a small overlap so transient API/RSS failures do not permanently skip articles.
    return last_checked - timedelta(hours=6)


def save_incremental_checkpoint(conn: sqlite3.Connection, feed_type: str, checked_at: datetime) -> None:
    conn.execute(
        """
        INSERT INTO app_settings(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (f"rss_last_checked_{feed_type}", checked_at.astimezone(timezone.utc).isoformat()),
    )


def resolve_article_category_and_clarity(conn: sqlite3.Connection, feed_type: str, item: Dict[str, Any]) -> Tuple[str, bool, bool]:
    default_category = classify_with_rules(item, feed_type)
    default_clear = is_clear_category(item, default_category)
    provider = get_ai_provider(conn)
    api_key = get_ai_api_key(conn)
    model = get_setting(conn, "classification_model") or ("gemini-3.7-flash" if provider == "gemini" else "gpt-4o-mini")
    
    category = default_category
    is_clear = default_clear
    is_breaking = False

    # 규칙 분류가 명확하지 않을 때만 OpenAI 호출
    # default_clear=True이면 규칙 결과가 확실하므로 OpenAI 생략 (오버라이드 방지)
    if api_key and not default_clear:
        examples = get_manual_training_examples(conn)
        try:
            category, is_clear, is_breaking = classify_with_openai(api_key, model, item, examples, feed_type, provider)
        except Exception:
            pass

    # [feed_type 기반 사후 보정] — AI 분류가 소스 출처와 맞지 않을 때 강제 교정
    title_temp2 = str(item.get("title", "")).lower().replace("kaist", "").replace("카이스트", "")
    _has_kai_in_title = has_kai_company_mention(title_temp2) or has_kai_product_mention(title_temp2)

    if feed_type == "competitor" and category == CATEGORY_KAI and not _has_kai_in_title:
        # 경쟁사 소스이고 AI가 kai로 분류했지만 제목 주어가 KAI가 아닌 경우
        # → 한화/LIG 브랜드가 있으면 해당 카테고리로, 없으면 space/reference로 교정
        _title_l = str(item.get("title", "")).lower()
        _text_l = str(item.get("summary", "") or "").lower()
        if has_any_keyword(_title_l, ["한화에어로", "한화시스템", "한화오션", "한화디펜스", "한화"]):
            category = CATEGORY_HANWHA
            is_clear = True
        elif has_any_keyword(_title_l, ["lig", "넥스원"]):
            category = CATEGORY_LIG
            is_clear = True
        elif is_aero_defense_space_relevant(_title_l + " " + _text_l):
            category = CATEGORY_SPACE
            is_clear = True
        else:
            category = CATEGORY_REFERENCE
            is_clear = True

    elif feed_type == "government" and category == CATEGORY_KAI and not _has_kai_in_title:
        # 정부 소스이고 AI가 kai로 분류했지만 제목 주어가 KAI가 아닌 경우
        # → government로 교정 (방사청 보도자료 등에서 KAI가 스치듯 언급된 경우)
        _is_core_kai = is_kai_core_story(
            str(item.get("title", "")),
            str(item.get("summary", "")),
            str(item.get("article_text", "")),
        )
        if not _is_core_kai:
            category = CATEGORY_GOVERNMENT
            is_clear = True

    # [강제 상호 배타성 및 카테고리 강제 보정 정책]
    title_lower = str(item.get("title", "")).lower()
    title_temp = title_lower.replace("kaist", "").replace("카이스트", "")
    has_hanwha_brand_in_title = has_any_keyword(title_lower, ["한화에어로", "한화시스템", "한화오션", "한화디펜스", "한화"])
    has_lig_brand_in_title = has_any_keyword(title_lower, ["lig", "넥스원"])
    has_kai_company_in_title = has_kai_company_mention(title_temp)
    has_kai_product_in_title = has_kai_product_mention(title_lower)
    has_kai_brand_in_title = has_kai_company_in_title or has_kai_product_in_title
    real_kai_story = is_kai_core_story(str(item.get("title", "")), str(item.get("summary", "")), str(item.get("article_text", "")))
    generic_event_noise = is_generic_event_noise(str(item.get("title", "")), str(item.get("summary", "")), str(item.get("article_text", "")))
    has_space_brand_in_title = any(kw in title_lower for kw in ["위성", "발사체", "누리호", "우주항공청", "kasa", "스페이스x", "우주산업", "우주개발", "우주탐사", "우주항공산업"])
    has_gov_brand_in_title = has_any_keyword(title_lower, ["국방부", "방사청", "방위사업청", "국방과학연구소", "add", "국방기술품질원", "기품원", "합참", "합동참모본부", "대통령실"])
    partner_or_supplier_story = is_partner_or_supplier_story(str(item.get("title", "")), " ".join([str(item.get("title", "")), str(item.get("summary", "")), str(item.get("article_text", ""))]))
    has_partner_brand_in_title = has_any_keyword(title_lower, PARTNER_KEYWORDS)
    item_text = " ".join([str(item.get("title", "")), str(item.get("summary", "")), str(item.get("article_text", ""))]).lower()
    has_president_aero_defense_context = has_any_keyword(item_text, ["대통령", "대통령실"]) and has_any_keyword(
        item_text,
        ["항공", "우주", "우주항공", "우주산업", "발사체", "위성", "방산", "국방", "방위산업", "전투기", "헬기", "k-방산", "k방산"],
    )
    mixed_hanwha_kai_ownership_story = (
        has_hanwha_brand_in_title
        and has_kai_brand_in_title
        and has_any_keyword(
            item_text,
            ["지분", "경영권", "경영 참여", "민영화", "인수", "합병", "최대주주", "주주", "협력", "매출 전환"],
        )
    )
    
    # ── 후처리 강제 분류 규칙 (AI 분류 결과보다 우선 적용) ──
    _item_title = str(item.get("title", ""))
    _item_summary = str(item.get("summary", ""))

    # 0. 주가 시황 종합 기사 → 즉시 trash (AI 분류보다 우선)
    if is_stock_market_roundup(_item_title):
        return "trash", True, False

    # 핵심 추적 협력사(율곡·아스트·ASTK)가 제목에 있으면 KAI보다 reference 우선
    # 예: "율곡, KAI에 납품하는데...홍콩계 사모펀드 인수" → 기사 주인공은 율곡
    has_key_partner_in_title = has_any_keyword(title_lower, KEY_TRACKED_PARTNERS)
    if has_key_partner_in_title and partner_or_supplier_story:
        category = CATEGORY_REFERENCE
        is_clear = True
        return category, is_clear, is_breaking

    # 비KAI 방산업체 제목 출현 → KAI 분류 방지
    # (현대로템·한화오션·LIG 등이 제목 주어인데 KAI로 잘못 분류되는 오류 차단)
    NON_KAI_COMPANY_TITLE_KW = [
        "현대로템", "한화", "한화오션", "한화시스템", "한화디펜스",
        "대한항공", "korean air",
        "현대차", "기아", "포스코", "삼성전자", "sk하이닉스",
        "kddx", "차기구축함",  # 차기구축함은 한화오션 프로그램
    ]
    has_non_kai_company_in_title = has_any_keyword(title_lower, NON_KAI_COMPANY_TITLE_KW)

    # 방산 종합 라운드업 기사 패턴 ("[방산 & Now]", "[방산브리핑]" 등)
    is_defense_roundup = has_any_keyword(title_lower, [
        "[방산 &", "[방산&", "방산 & now", "방산&now",
        "[방산 브리핑]", "[방산브리핑]", "[방산뉴스]",
    ])

    # 1. 제목 주체가 KAI가 아닌 항공·방산 기업이면 범용 UAM/우주 키워드만으로 KAI기사로 보지 않음
    if (
        has_non_kai_company_in_title
        and not has_kai_company_in_title
        and not has_hanwha_brand_in_title
        and not has_lig_brand_in_title
    ):
        category = CATEGORY_SPACE if is_aero_defense_space_relevant(item_text) else CATEGORY_REFERENCE
        is_clear = True
    # 2. 제목에 KAI/KAI 제품이 있고 실제 KAI 핵심 기사이면 대통령/정부기관 문맥보다 KAI 우선
    elif (
        real_kai_story
        and has_kai_brand_in_title
        and not (has_non_kai_company_in_title and not has_kai_company_in_title)
        and not mixed_hanwha_kai_ownership_story
    ):
        category = CATEGORY_KAI
        is_clear = True
    # 3. 정부기관 명칭이 제목에 있으면 government로 분류
    #    단, KAI 제품/회사명이 제목에 있으면 KAI 우선
    elif has_president_aero_defense_context or (has_gov_brand_in_title and not has_kai_brand_in_title):
        category = CATEGORY_GOVERNMENT
        is_clear = True
    # 2-a. 비KAI 기업이 제목 주인공이거나 방산 라운드업 → KAI 분류 불가
    #      경쟁사 브랜드 제목 체크를 real_kai_story보다 먼저 수행
    elif (has_non_kai_company_in_title or is_defense_roundup) and not has_kai_brand_in_title:
        # 한화/LIG 브랜드 있으면 competitor 분류 시도
        if has_hanwha_brand_in_title or has_lig_brand_in_title:
            _is_real = is_real_competitor_story(_item_title, _item_summary)
            if _is_real:
                category = CATEGORY_HANWHA if has_hanwha_brand_in_title else CATEGORY_LIG
            else:
                category = "trash"
        else:
            category = CATEGORY_SPACE if is_aero_defense_space_relevant(item_text) else "trash"
        is_clear = True
    # 2-b. 제목에 경쟁사명만 있고 KAI 없는 경우 — real_kai_story보다 먼저 체크
    elif (has_hanwha_brand_in_title or has_lig_brand_in_title) and not has_kai_brand_in_title:
        _competitor_noise = is_competitor_noise_story(_item_title, _item_summary)
        _political_noise = is_political_campaign_noise(_item_title, _item_summary)
        if _competitor_noise or _political_noise:
            category = "trash"
            is_clear = True
        else:
            _is_real = is_real_competitor_story(_item_title, _item_summary)
            if _is_real:
                if has_hanwha_brand_in_title:
                    category = CATEGORY_HANWHA
                elif has_lig_brand_in_title:
                    category = CATEGORY_LIG
                is_clear = True
            else:
                category = "trash"
                is_clear = True
    # 2-c. 한화+KAI 혼합 제목이라도 지분/경영권 이슈면 한화 경쟁사 기사로 우선 분류
    elif mixed_hanwha_kai_ownership_story:
        category = CATEGORY_HANWHA
        is_clear = True
    # 3. 실제 KAI 핵심 기사 (CEO 관점 중요도 기준)
    elif real_kai_story:
        category = CATEGORY_KAI
        is_clear = True
    # 4. 협력사 비즈니스 이벤트 기사 (M&A·IPO·매각 등)
    elif partner_or_supplier_story:
        category = CATEGORY_REFERENCE
        is_clear = True
    # 5. 행사성/일반 노이즈는 reference
    elif generic_event_noise:
        category = CATEGORY_REFERENCE
        is_clear = True
    # 6. 우주 관련 핵심 단어가 제목에 있고 KAI/경쟁사가 없으면 space
    elif has_space_brand_in_title and not has_kai_brand_in_title and not has_hanwha_brand_in_title and not has_lig_brand_in_title:
        category = CATEGORY_SPACE
        is_clear = True

    # ── 최종 안전망: kai 카테고리지만 실제 KAI 핵심 기사가 아닌 경우 강제 재분류 ──
    # (OpenAI 오분류 방지: 항공·방산 키워드만으로 kai를 반환한 경우 차단)
    if category == CATEGORY_KAI and not real_kai_story:
        # 제목에 KAI 회사/제품명이 없으면 완전히 무관한 기사 → trash
        category = "trash" if not has_kai_brand_in_title else CATEGORY_REFERENCE
        is_clear = True

    # ── LIG 브랜드 추가 안전망: 제목에 LIG/넥스원이 있는데 kai로 분류된 경우 강제 수정 ──
    # (real_kai_story가 True여도 제목에 경쟁사 브랜드가 있으면 KAI 기사가 아님)
    if category == CATEGORY_KAI and has_lig_brand_in_title and not has_kai_brand_in_title:
        category = CATEGORY_LIG
        is_clear = True

    # ── 한화 브랜드 추가 안전망: 동일 원칙 적용 ──
    if category == CATEGORY_KAI and has_hanwha_brand_in_title and not has_kai_brand_in_title:
        category = CATEGORY_HANWHA
        is_clear = True

    return category, is_clear, is_breaking


def send_telegram_alert(conn: sqlite3.Connection, item: Dict[str, Any]) -> None:
    """긴급/속보 기사 즉시 전송 — 발행 후 60분 이내 신선한 기사만 발송.

    서버 복구 등으로 오래된 기사가 재처리될 때 stale 알림을 방지한다.
    """
    token = get_setting(conn, "telegram_bot_token")
    chat_id = get_setting(conn, "telegram_chat_id")
    if not token or not chat_id:
        return
    # 신선도 체크: 발행 후 60분 이내 기사만 즉시 발송
    # (서버 다운 후 복구 시 오래된 기사 재발송 방지)
    pub_at_str = item.get("published_at") or item.get("article_published_at", "")
    if pub_at_str:
        try:
            import dateutil.parser as _dp
            pub_dt = _dp.parse(str(pub_at_str))
            now_utc = datetime.now(timezone.utc)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            age_minutes = (now_utc - pub_dt).total_seconds() / 60
            if age_minutes > 60:
                return  # 60분 초과 기사는 다음 정규 브리핑에 포함
        except Exception:
            pass

    import html as _html
    title = _html.escape(item.get("title", "제목 없음"))
    summary = item.get("summary", "내용 없음")
    link = item.get("link", "")
    category = item.get("article_category") or item.get("category", "")

    category_emoji = {
        "kai": "✈️ [KAI기사]",
        "government": "🏛️ [정부기관]",
        "hanwha": "🔥 [경쟁사 - 한화]",
        "lig": "🔥 [경쟁사 - LIG]",
        "space": "🚀 [항공/방산/우주]",
        "reference": "🤝 [협력사]",
    }.get(category, "📢")

    summary_lines = [line.strip() for line in summary.split("\n") if line.strip()][:2]
    formatted_summary = _html.escape("\n→ ".join(summary_lines))

    text = (
        f"🚨 <b>[긴급/속보] {category_emoji}</b>\n\n"
        f"📣 <a href='{link}'>{title}</a>\n"
        f"→ {formatted_summary}"
    )
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10):
            pass
        print(f"Telegram alert sent: category={category} title={compact_text(item.get('title', ''), 120)}")
    except Exception as e:
        print(f"Telegram alert failed: {e}")


def filter_items_by_time(items: List[Dict[str, Any]], window_start: datetime) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for item in items:
        published_at = parse_iso_datetime(item.get("published_at", ""))
        if published_at is None:
            continue
        if published_at >= window_start:
            filtered.append(item)
    return filtered


def filter_items_by_keywords(
    items: List[Dict[str, Any]],
    mode: str,
    include_keywords: List[str],
    exclude_keywords: List[str],
    source_include_keywords: List[str],
    source_exclude_keywords: List[str],
) -> List[Dict[str, Any]]:
    filtered: List[Dict[str, Any]] = []
    for item in items:
        title = item.get("title", "")
        summary = item.get("summary", "")
        
        # [종합 인사/동정/부음 기사 사전 차단]
        title_stripped = title.strip()
        if title_stripped.startswith(("[인사]", "[부음]", "(인사)", "(부음)", "◇")):
            continue
        if any(keyword in title_stripped for keyword in [" 종합 인사", "종합인사", "부고", "동정 "]):
            continue

        # [종합·브리핑성 묶음 기사 사전 차단] – 수집 단계에서 원천 차단
        AGGREGATE_TITLE_PREFIXES = (
            "[중화학", "[중화학 ON]", "[단신]", "[산업 단신]",
            "[오늘의 방산]", "[방산 브리핑]", "[주간 방산]",
        )
        if title_stripped.startswith(AGGREGATE_TITLE_PREFIXES):
            continue
        # '外' 로 끝나는 묶음 기사 (예: 'A사, B사 소식 外') 도 차단
        if title_stripped.endswith("外") and "한화큐셀" in title_stripped:
            continue

        combined_text = " ".join([title, summary]).strip()

        if is_generic_education_noise(title, summary):
            continue
        if is_generic_event_noise(title, summary, item.get("article_text", "")):
            continue
        if is_non_industry_kai_noise(title, summary, item.get("article_text", "")):
            continue
        if is_political_campaign_noise(title, summary, item.get("article_text", "")):
            continue
        if is_competitor_noise_story(title, summary):
            continue

        if not has_hangul(combined_text):
            continue

        CORE_FILTER_KEYWORDS = [
            "한국항공우주", "항공우주", "방산", "방위산업", "국방", "방위사업청", "방사청",
            "전투기", "헬기", "수리온", "미르온", "미르온(lah)", "kf-21", "fa-50", "t-50", "lah", "마린온",
            "한화에어로스페이스", "한화에어로", "한화시스템", "한화오션",
            "lig넥스원", "lig d&a",
            "위성", "발사체", "누리호", "우주항공청", "kasa",
            "무기체계", "전력화", "방추위", "방위사업추진위원회",
            "항공시찰", "군 시찰", "방산시찰",
            # UAM / 드론 / 미래항공 메가프로젝트
            "uam", "도심항공", "도심항공교통", "에어택시", "에어모빌리티",
            "드론", "무인기", "무인항공기", "무인비행체",
            "민항기", "민간항공기", "민항기공동개발", "차세대민항기",
            "aac", "항공기개발", "항공기사업",
            # 협력사 핵심
            "제노코", "켄코아", "하이즈항공", "아스트", "퍼스텍", "율곡",
            "에이엔에이치스트럭쳐",
        ]
        combined_lower = combined_text.lower()
        has_core_keyword = any(kw in combined_lower for kw in CORE_FILTER_KEYWORDS) or has_exact_kai_token(combined_lower)
        if not has_core_keyword:
            continue

        if exclude_keywords and any(keyword_matches_text(combined_text, keyword) for keyword in exclude_keywords):
            continue

        if source_exclude_keywords and any(keyword_matches_text(combined_text, keyword) for keyword in source_exclude_keywords):
            continue

        has_include_rule = False
        matched_include = False

        if source_include_keywords:
            has_include_rule = True
            if any(keyword_matches_text(combined_text, keyword) for keyword in source_include_keywords):
                matched_include = True

        if mode == "keywords" and include_keywords:
            has_include_rule = True
            if any(keyword_matches_text(combined_text, keyword) for keyword in include_keywords):
                matched_include = True

        if has_include_rule and not matched_include:
            continue

        filtered.append(item)
    return filtered


def resolve_article_category(conn: sqlite3.Connection, feed_type: str, item: Dict[str, Any]) -> str:
    default_category = classify_with_rules(item, feed_type)
    provider = get_ai_provider(conn)
    api_key = get_ai_api_key(conn)
    model = get_setting(conn, "classification_model") or ("gemini-3.7-flash" if provider == "gemini" else "gpt-4o-mini")
    if not api_key:
        return default_category
    examples = get_manual_training_examples(conn)
    try:
        category, _, _ = classify_with_openai(api_key, model, item, examples, feed_type, provider)
    except Exception:
        return default_category
    if category in ALLOWED_CATEGORIES:
        return category
    return default_category


def normalize_id(feed_type: str, link: str, index: int) -> str:
    safe = "".join(ch for ch in link if ch.isalnum())[-24:]
    return f"{feed_type}-{safe or index}"


def store_items(
    conn: sqlite3.Connection,
    feed_type: str,
    source_name: str,
    items: List[Dict[str, Any]],
) -> tuple[int, List[Dict[str, str]]]:
    ensure_topic_memory_table(conn)
    ensure_deleted_items_table(conn)
    imported = 0
    stored_items: List[Dict[str, str]] = []
    
    # 글로벌 중복 제거: 특정 피드 타입에 국한하지 않고 최근 1000개의 기사들을 모두 로드하여 비교
    recent_rows = conn.execute(
        """
        SELECT id, title, summary, link, article_published_at, article_publisher, article_category, category_manual, selected, published, feed_type
        FROM feed_items
        ORDER BY COALESCE(article_published_at, published_at) DESC, id DESC
        LIMIT 1000
        """
    ).fetchall()
    recent_items: List[Dict[str, Any]] = [dict(row) for row in recent_rows]

    ordered_items = sorted(
        items,
        key=lambda it: (
            publisher_score(str(it.get("publisher", "")), str(it.get("link", ""))),
            parse_iso_datetime(str(it.get("published_at", ""))) or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )

    provider = get_ai_provider(conn)
    api_key = get_ai_api_key(conn)
    dedupe_model = get_setting(conn, "classification_model") or ("gemini-3.7-flash" if provider == "gemini" else "gpt-4o-mini")
    ai_dedupe_calls = 0
    max_ai_dedupe_calls = 20

    # 현재 DB에 있는 전체 링크 셋 — URL 완전 일치 중복을 O(1)로 차단
    existing_links: set[str] = set(
        row[0] for row in conn.execute("SELECT link FROM feed_items WHERE link IS NOT NULL").fetchall()
    )

    for index, item in enumerate(ordered_items, start=1):
        item_link = str(item.get("link", "")).strip()

        # 1순위: URL 완전 일치 → 즉시 스킵 (가장 빠른 중복 차단)
        if item_link and item_link in existing_links:
            continue

        # 1-b순위: 행사 앵커 일치 → 기존 기사보다 언론사 점수가 높으면 교체, 낮으면 스킵
        item_anchors = _extract_event_anchors(str(item.get("title", "")), str(item.get("summary", "")))
        _skip_item = False
        if item_anchors:
            for existing in recent_items:
                ex_anchors = _extract_event_anchors(
                    str(existing.get("title", "")), str(existing.get("summary", ""))
                )
                if item_anchors.intersection(ex_anchors):
                    # 같은 행사 기사 발견 — 더 좋은 언론사면 교체, 아니면 스킵
                    incoming_score = publisher_score(
                        str(item.get("publisher", "")), str(item.get("link", ""))
                    )
                    existing_score = publisher_score(
                        str(existing.get("article_publisher", "")), str(existing.get("link", ""))
                    )
                    if incoming_score > existing_score:
                        # 더 좋은 언론사 → 기존 삭제 후 새 기사로 교체
                        conn.execute("DELETE FROM feed_items WHERE id = ?", (existing["id"],))
                        existing_links.discard(str(existing.get("link", "")))
                        recent_items[:] = [r for r in recent_items if r.get("id") != existing["id"]]
                    else:
                        # 기존이 더 좋거나 동급 → outer loop에서 스킵
                        _skip_item = True
                    break
        if _skip_item:
            continue

        if is_deleted_item(conn, feed_type, item_link):
            continue
        signature = build_topic_signature(item)
        preferred_from_memory = get_memory_preferred_publisher(conn, signature)
        if preferred_from_memory:
            # Stale memory safeguard: only skip if the topic is actually present in our database
            has_existing_topic = False
            for r_item in recent_items:
                if is_same_topic_rule(item, {"title": r_item.get("title", ""), "summary": r_item.get("summary", ""), "link": r_item.get("link", "")}):
                    has_existing_topic = True
                    break
            if has_existing_topic:
                incoming_host = normalize_host(item.get("publisher", "") or publisher_from_link(str(item.get("link", ""))))
                if incoming_host and incoming_host != normalize_host(preferred_from_memory):
                    if publisher_score(incoming_host, str(item.get("link", ""))) < publisher_score(preferred_from_memory, ""):
                        continue

        duplicate_existing: Dict[str, Any] | None = None
        for existing in recent_items:
            existing_for_compare = {
                "title": existing.get("title", ""),
                "summary": existing.get("summary", ""),
                "link": existing.get("link", ""),
                "article_published_at": existing.get("article_published_at", ""),
                "article_publisher": existing.get("article_publisher", ""),
            }
            if is_same_topic_rule(item, existing_for_compare):
                duplicate_existing = existing
                break
            if api_key and ai_dedupe_calls < max_ai_dedupe_calls:
                similarity = SequenceMatcher(
                    None,
                    normalize_topic_text(str(item.get("title", ""))),
                    normalize_topic_text(str(existing.get("title", ""))),
                ).ratio()
                tokens_item = set(extract_topic_tokens(str(item.get("title", "")), ""))
                tokens_existing = set(extract_topic_tokens(str(existing.get("title", "")), ""))
                overlap = len(tokens_item.intersection(tokens_existing))
                
                if (0.3 <= similarity < 0.9) or (overlap >= 1 and similarity < 0.9):
                    try:
                        ai_dedupe_calls += 1
                        same = is_same_topic_with_openai(
                            api_key,
                            dedupe_model,
                            item,
                            {"title": existing.get("title", ""), "summary": existing.get("summary", "")},
                            provider,
                        )
                    except Exception:
                        same = False
                    if same:
                        duplicate_existing = existing
                        break

        if duplicate_existing:
            preferred = choose_preferred_item(item, duplicate_existing)
            preferred_host = (
                normalize_host(item.get("publisher", "") or publisher_from_link(str(item.get("link", ""))))
                if preferred == "incoming"
                else normalize_host(str(duplicate_existing.get("article_publisher", "")))
            )
            update_topic_memory(conn, signature, str(item.get("title", "")), preferred_host)
            if preferred == "incoming":
                conn.execute(
                    """
                    UPDATE feed_items
                    SET title = ?, summary = ?, link = ?, source = ?, article_published_at = ?, article_publisher = ?
                    WHERE id = ?
                    """,
                    (
                        item.get("title", ""),
                        item.get("summary", ""),
                        item.get("link", ""),
                        source_name,
                        _to_iso_date(item.get("published_at")),
                        item.get("publisher") or source_name,
                        duplicate_existing["id"],
                    ),
                )
                # 캐시 메모리 갱신
                for r in recent_items:
                    if r["id"] == duplicate_existing["id"]:
                        r["title"] = item.get("title", "")
                        r["summary"] = item.get("summary", "")
                        r["link"] = item.get("link", "")
                        r["article_published_at"] = item.get("published_at") or None
                        r["article_publisher"] = item.get("publisher") or source_name
            continue

        item_id = normalize_id(feed_type, item["link"], index)
        existing = conn.execute(
            "SELECT article_category, category_manual, selected, published FROM feed_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        
        is_clear = False
        is_breaking = False
        if existing and int(existing[1] or 0) == 1 and existing[0] in ALLOWED_CATEGORIES:
            article_category = existing[0]
            is_clear = True
        else:
            article_category, is_clear, is_breaking = resolve_article_category_and_clarity(conn, feed_type, item)
            
        # 한화/LIG 키워드가 포함된 기사인데 trash로 분류되어 유실될 뻔한 기사는,
        # 검증을 돕기 위해 버리지 않고 'reference'(참고) 카테고리의 검토 대기(published=0) 상태로 우회 삽입함
        is_competitor_mention = has_any_keyword(f"{item.get('title', '')} {item.get('summary', '')}", HANWHA_KEYWORDS + LIG_KEYWORDS)
        
        if article_category == "trash":
            if is_competitor_mention:
                article_category = CATEGORY_REFERENCE
                is_clear = False
            else:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO deleted_feed_items(feed_type, link, title)
                    VALUES (?, ?, ?)
                    """,
                    (feed_type, item["link"], item.get("title", "")),
                )
                continue
            
        # 긴급/속보 판정 — KAI 직접 관련 기사만 즉시 발송
        _title_str = item.get("title", "")
        _summary_str = item.get("summary", "")
        _text_combined = (_title_str + " " + _summary_str).lower()

        _has_breaking_keyword = any(kw in _title_str for kw in ["단독", "속보", "긴급", "breaking", "BREAKING"])
        _is_pr_noise = any(kw in _title_str for kw in ["협약", "MOU", "체결식", "참석", "참가", "방문", "수상"])

        # KAI 직접 관련 여부 확인
        # "한국항공우주" 풀네임뿐 아니라 "KAI" 영문 약칭(단독 토큰)도 인식해야 함 —
        # 실제 기사 제목 대부분은 "KAI"로 표기되므로 풀네임만 체크하면 누락됨
        _is_kai_direct = (
            "한국항공우주" in _text_combined
            or has_kai_company_mention(_text_combined)
            or any(kw in _text_combined for kw in [
                "kf-21", "fa-50", "t-50", "수리온", "kuh",
                "lah", "소형무장헬기", "차세대 공격헬기",
                "마린온", "미르온", "미르온(lah)",
                "uam", "도심항공교통", "민항기공동개발",
            ])
        )
        _kai_product_safety_risk = _is_kai_direct and any(kw in _text_combined for kw in [
            "추락", "사고", "결함", "리콜", "감항", "품질 문제", "품질문제",
            "비행 중단", "비행중단", "운항 중단", "운항중단", "부식", "불량",
        ])

        # CEO 즉각 인지 필요 패턴
        _ceo_urgent_patterns = [
            # KAI 위기 상황 (사고·결함·법적 리스크·경영권 변동)
            _kai_product_safety_risk or (_is_kai_direct and any(kw in _text_combined for kw in [
                "파산", "압수수색", "구속", "수사",
                "지분 인수", "지분매입", "경영권", "민영화",
            ])),
            # 방추위가 KAI 제품을 직접 결정
            any(kw in _text_combined for kw in ["방추위", "방위사업추진위원회"])
            and _is_kai_direct
            and any(kw in _text_combined for kw in ["전력화 결정", "양산 결정", "사업 취소", "예산 삭감", "예산 통과"]),
            # KAI 제품 수출 계약 확정
            _is_kai_direct and any(kw in _text_combined for kw in [
                "수출 계약", "수주 확정", "수출 성사", "계약 체결 확정",
            ]),
            # 공군 핵심 직위 인사 — KAI 주요 고객(KF-21/FA-50/T-50 운용) 라인 변동
            # 공군참모총장·작전사령관·항공사령관·사관학교장은 KAI 사업에 직접 영향
            article_category == CATEGORY_GOVERNMENT
            and any(kw in _text_combined for kw in ["장성 인사", "장성급 인사", "인사발령", "진급 인사"])
            and any(kw in _text_combined for kw in [
                "공군참모총장", "공군작전사령관", "공군항공사령관", "공군사관학교장",
                "공군 중장", "공군 소장",
                "공군참모차장", "방위사업청장", "방사청장",
            ]),
        ]
        _is_ceo_urgent = any(_ceo_urgent_patterns)

        # 정해진 시간 외 즉시 발송: 제목에 속보/단독/긴급이 명시된 기사만 허용
        # 의미 기반 긴급 패턴은 중요도 판단 보조로만 사용하고, 제목 키워드 없는 즉시발송은 금지한다.
        _kai_breaking = (
            _is_kai_direct
            and not _is_pr_noise
            and _has_breaking_keyword
            and (is_breaking or any(_ceo_urgent_patterns[:3]) or article_category == CATEGORY_KAI)
        )
        _military_appointment_alert = (
            article_category == CATEGORY_GOVERNMENT
            and _has_breaking_keyword
            and _ceo_urgent_patterns[3]  # 공군 핵심 직위 인사
        )
        is_breaking_rule = _kai_breaking or _military_appointment_alert
        _mark_sent_after_insert = False  # 즉시 발송 후 sent_briefing_at 마킹 플래그
        if _has_breaking_keyword and (is_breaking or is_breaking_rule) and not existing:
            # 예전 기사들은 속보(Breaking News)로 다시 전송하지 않고, 오직 오늘자 기사만 전송되도록 날짜 방어막 적용
            import dateutil.parser as _parser
            is_today_article = False
            try:
                pub_at_str = item.get("published_at")
                if pub_at_str:
                    pub_dt = _parser.parse(pub_at_str)
                    seoul_tz = get_seoul_timezone()
                    pub_seoul = pub_dt.astimezone(seoul_tz)
                    now_seoul = datetime.now(seoul_tz)
                    today_start = now_seoul.replace(hour=0, minute=0, second=0, microsecond=0)
                    if pub_seoul >= today_start:
                        is_today_article = True
            except Exception:
                is_today_article = False

            if is_today_article:
                # [의미 기반 실시간 속보 중복 전송 방어 조치]
                # 오늘 이미 전송된 속보 기사들과 현재 기사가 동일 주제인지 의미 분석기(is_same_topic_rule)로 대조
                is_duplicate_breaking = False
                try:
                    seoul_tz = get_seoul_timezone()
                    now_seoul = datetime.now(seoul_tz)
                    today_start_iso = now_seoul.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
                    # DB에서 오늘 발행/수집된 기사들 전체 조회
                    today_items = conn.execute(
                        "SELECT title, summary, link, article_category FROM feed_items WHERE published = 1 AND COALESCE(article_published_at, '') >= ?",
                        (today_start_iso,)
                    ).fetchall()
                    
                    for today_item in today_items:
                        # SQLite Row를 Dict로 변환하여 동일 주제 판단
                        if is_same_topic_rule(dict(item), dict(today_item)):
                            is_duplicate_breaking = True
                            break
                except Exception as e:
                    print(f"Breaking news deduplication check failed: {e}")

                if not is_duplicate_breaking:
                    send_telegram_alert(conn, item)
                    # 즉시 발송된 기사는 sent_briefing_at을 마킹해 정규 브리핑에서 제외
                    # (INSERT 전이므로 아래 INSERT 직후 별도 UPDATE로 처리)
                    _mark_sent_after_insert = True

        if existing:
            selected_value = int(existing[2] or 0)
            published_value = int(existing[3] or 0)
            manual_value = int(existing[1] or 0)
        else:
            published_value = 1 if is_clear else 0
            selected_value = 1 if is_clear else 0
            manual_value = 0
        conn.execute(
            """
            INSERT OR REPLACE INTO feed_items(
                id, feed_type, title, summary, link, source, article_published_at, article_publisher, article_category, category_manual, selected, published
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                item_id,
                feed_type,
                item.get("title", ""),
                item.get("summary", ""),
                item.get("link", ""),
                source_name,
                _to_iso_date(item.get("published_at")),
                item.get("publisher") or source_name,
                article_category,
                manual_value,
                selected_value,
                published_value,
            ),
        )
        
        # 즉시 발송된 속보 기사는 sent_briefing_at 마킹 → 정규 브리핑에서 중복 발송 차단
        if _mark_sent_after_insert:
            conn.execute(
                "UPDATE feed_items SET sent_briefing_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), item_id),
            )

        # URL 셋에도 즉시 추가 — 동일 배치 내 같은 URL 재수집 차단
        if item_link:
            existing_links.add(item_link)

        # 신규 기사 저장 후 recent_items 캐시에 즉시 추가하여 동일 배치 내의 추가 중복 차단 보장
        new_recent_item = {
            "id": item_id,
            "feed_type": feed_type,
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "link": item.get("link", ""),
            "article_published_at": item.get("published_at") or None,
            "article_publisher": item.get("publisher") or source_name,
            "article_category": article_category,
            "category_manual": manual_value,
            "selected": selected_value,
            "published": published_value,
        }
        recent_items.append(new_recent_item)

        if not existing:
            imported += 1
            stored_items.append({
                "id": item_id,
                "title": item.get("title", ""),
                "summary": item.get("summary", ""),
                "link": item.get("link", ""),
                "category": article_category,
            })
    conn.commit()
    return imported, stored_items


def summarize_item(api_key: str, model: str, title: str, summary: str, link: str, provider: str = "openai") -> str:
    model = normalize_ai_model(model, provider)
    prompt = (
        "아래 뉴스 기사를 분석하여, 바쁜 경영진(CEO)이 모바일 앱과 대시보드에서 핵심을 즉시 파악할 수 있도록 **한국어 2~3줄**로 간결하게 요약해 주세요.\n"
        "- 기사의 핵심 사실과 비즈니스/정책적 의의를 위주로 담아주세요.\n"
        "- 각 요약 줄 사이에는 반드시 줄바꿈(엔터 키)을 입력하여 물리적으로 행을 분리해 주세요.\n"
        "- 번호 매기기나 글머리 기호(예: -, *, 1., 2.)는 절대 사용하지 마세요.\n"
        "- 각각의 줄은 독립된 한 문장이어야 합니다.\n\n"
        f"제목: {title}\n"
        f"요약: {summary[:3000]}\n"
    )
    return chat_completion_content(
        api_key,
        model,
        [{"role": "user", "content": prompt}],
        provider=provider,
        timeout=30,
    )


def maybe_summarize_items(conn: sqlite3.Connection, stored_items: List[Dict[str, str]]) -> None:
    model_row = conn.execute("SELECT value FROM app_settings WHERE key = 'openai_model'").fetchone()
    provider = get_ai_provider(conn)
    api_key = get_ai_api_key(conn)
    model = model_row[0] if model_row else ("gemini-3.7-flash" if provider == "gemini" else "gpt-4o-mini")
    if not api_key:
        for item in stored_items:
            fallback_summary = (item.get("summary") or "").strip() or (item.get("title") or "").strip()
            conn.execute("UPDATE feed_items SET summary = ? WHERE id = ?", (fallback_summary, item["id"]))
        conn.commit()
        return
    for item in stored_items:
        try:
            summary = summarize_item(api_key, model, item.get("title", ""), (item.get("summary") or ""), item.get("link", ""), provider)
            conn.execute("UPDATE feed_items SET summary = ? WHERE id = ?", (summary, item["id"]))
            conn.commit()
        except Exception:
            fallback_summary = (item.get("summary") or "").strip() or (item.get("title") or "").strip()
            conn.execute("UPDATE feed_items SET summary = ? WHERE id = ?", (fallback_summary, item["id"]))
            conn.commit()


def import_sources(conn: sqlite3.Connection) -> Dict[str, Any]:
    sources = load_sources_from_db(conn) or load_sources_from_config()
    results: List[Dict[str, Any]] = []
    total_imported = 0

    for feed_type, entries in sources.items():
        window_start = get_incremental_window_start(conn, feed_type)
        mode, include_keywords, exclude_keywords = load_keywords(conn, feed_type)
        for source in entries:
            try:
                if source["url"].startswith("naver-news://"):
                    items = fetch_naver_news_items(conn, source["url"])
                elif source["url"].startswith("korea-gov://"):
                    items = fetch_korea_gov_items(conn, source["url"])
                    # korea.kr 날짜는 일(day) 단위라 incremental window에 걸림 → 7일 전으로 오버라이드
                    korea_gov_window = datetime.now(timezone.utc) - timedelta(days=7)
                    items = filter_items_by_time(items, korea_gov_window)
                else:
                    xml_bytes = fetch_xml(source["url"])
                    items = parse_feed(xml_bytes)
                if not source["url"].startswith("korea-gov://"):
                    items = filter_items_by_time(items, window_start)
                source_include = parse_keywords(source.get("include_keywords", ""))
                source_exclude = parse_keywords(source.get("exclude_keywords", ""))
                items = filter_items_by_keywords(items, mode, include_keywords, exclude_keywords, source_include, source_exclude)
                attach_article_text(items)
                imported, stored_items = store_items(conn, feed_type, source["name"], items)
                maybe_summarize_items(conn, stored_items)
                total_imported += imported
                results.append({"feed_type": feed_type, "source_name": source["name"], "item_count": imported})
            except Exception as exc:
                print(f"RSS import failed: feed={feed_type} source={source['name']} error={exc}")
                results.append({"feed_type": feed_type, "source_name": source["name"], "item_count": 0, "error": str(exc)})
        save_incremental_checkpoint(conn, feed_type, datetime.now(timezone.utc))

    conn.commit()
    return {"total_imported": total_imported, "results": results}


def send_telegram_briefing(conn: sqlite3.Connection, force: bool = False, dry_run: bool = False) -> None:
    try:
        token = get_setting(conn, "telegram_bot_token")
        chat_id = get_setting(conn, "telegram_chat_id")
        if not token or not chat_id:
            return

        seoul_tz = get_seoul_timezone()
        now_seoul = datetime.now(seoul_tz)
        
        current_hour = now_seoul.hour
        current_minute = now_seoul.minute
        allowed_hours = get_allowed_telegram_briefing_hours(now_seoul)

        # 자동 발송은 지정 시간에만 제한하되, 장애 복구/수동 보강 발송은 force=True로 허용한다.
        if not force and current_hour not in allowed_hours:
            return

        # 정각 기준 30분 이내에만 발송 (12:00~12:29 / 18:00~18:29)
        # 서버가 복구되어도 12:30 이후라면 18시까지 기다림
        if not force and current_minute >= 30:
            return

        last_briefing_key = f"telegram_last_briefing_{now_seoul.strftime('%Y%m%d')}_{current_hour}"
        if not force:
            last_run = get_setting(conn, last_briefing_key)
            if last_run == "sent":
                return

        conn.execute(
            """
            INSERT INTO app_settings(key, value)
            VALUES (?, 'sent')
            ON CONFLICT(key) DO UPDATE SET value = 'sent'
            """,
            (last_briefing_key,)
        )
        try:
            conn.commit()
        except Exception:
            pass

        # 현재 요일 (0: 월, 1: 화, 2: 수, 3: 목, 4: 금, 5: 토, 6: 일) 및 시간 확인
        weekday = now_seoul.weekday()
        current_hour = now_seoul.hour

        # 브리핑 전송 주기(평일 12시/18시, 주말 18시)에 맞춘 정확한 lookback_hours 동적 산출
        if weekday in (5, 6):  # 토요일, 일요일 브리핑 (18시)
            # 이전 브리핑은 어제 18시이므로 24시간 전
            lookback_hours = 24
        else:  # 평일 브리핑 (월~금)
            if current_hour == 12:
                # 이전 브리핑은 전날 18시(일요일 18시인 경우도 포함)이므로 18시간 전
                lookback_hours = 18
            else:
                # 18시 브리핑의 경우 이전 브리핑은 오늘 12시이므로 6시간 전
                lookback_hours = 6

        # force(수동 강제 테스트) 시에는 24시간 범위를 넓게 조회
        if force:
            lookback_hours = 24

        # DB의 article_published_at 컬럼은 UTC(+00:00) 문자열 포맷이므로, 비교를 위해 UTC 기준으로 time_window_start 생성
        now_utc = datetime.now(timezone.utc)
        time_window_start = (now_utc - timedelta(hours=lookback_hours)).isoformat()
        rows = conn.execute(
            """
            SELECT id, title, summary, link, article_category, article_publisher
            FROM feed_items
            WHERE published = 1
              AND COALESCE(article_published_at, '') >= ?
              AND (sent_briefing_at IS NULL OR sent_briefing_at = '')
            ORDER BY
              CASE article_category
                WHEN 'kai' THEN 0
                WHEN 'government' THEN 1
                WHEN 'hanwha' THEN 2
                WHEN 'lig' THEN 3
                WHEN 'space' THEN 4
                ELSE 5
              END,
              article_published_at DESC
            """,
            (time_window_start,)
        ).fetchall()

        if not rows:
            return

        # 저가치 기사 사전 차단 키워드 (구칙 기반)
        def _is_low_value(title: str, summary: str) -> bool:
            """KAI CEO 관점에서 거의 가치 없는 저등급 기사 판별"""
            text = (title + " " + (summary or "")).lower()
            return any(kw in text for kw in LOW_VALUE_KEYWORDS)

        def _dedup_by_topic(items: List[sqlite3.Row], max_items: int = 50) -> List[sqlite3.Row]:
            """동일 주제 중복 제거 시 메이저 신문사/방송사 기사 우선 선정"""
            # 메이저 언론사 도메인 리스트 (우선 선출)
            MAJOR_PUBLISHERS = [
                "chosun.com", "donga.com", "joongang.co.kr", "yna.co.kr", 
                "sbs.co.kr", "kbs.co.kr", "mbn.co.kr", "imbc.com", 
                "ytn.co.kr", "hankyung.com", "mk.co.kr", "khan.co.kr", 
                "seoul.co.kr", "hankookilbo.com", "segye.com", "munhwa.com"
            ]

            def _publisher_priority(row: sqlite3.Row) -> int:
                publisher = str(row["article_publisher"] or "").lower()
                # 메이저 언론사인 경우 우선순위를 높임 (0이 가장 높은 우선순위)
                if any(major in publisher for major in MAJOR_PUBLISHERS):
                    return 0
                return 1

            # 메이저사 기사를 최선두로 배치하기 위해 Stable Sort 정렬
            sorted_items = sorted(items, key=_publisher_priority)

            result: List[sqlite3.Row] = []
            for row in sorted_items:
                is_dup = False
                for seen_row in result:
                    if is_same_topic_rule(dict(row), dict(seen_row)):
                        is_dup = True
                        break
                if not is_dup:
                    result.append(row)
                if len(result) >= max_items:
                    break
            return result

        def _ai_select_top_n(
            api_key: str, model: str, provider: str,
            items: List[sqlite3.Row], n: int, context: str
        ) -> List[sqlite3.Row]:
            """기사 목록에서 KAI CEO 관점 중요도 순 Top-N 선별"""
            if len(items) <= n:
                return list(items)
            numbered = ""
            for i, row in enumerate(items, 1):
                numbered += f"{i}. {row['title']}\n"
            prompt = (
                f"KAI(한국항공우주산업) CEO 입장에서 나누는 '{context}' 카테고리 기사 목록 중,\n"
                f"반드시 읽어야 할 중요도 순으로 차례대로 {n}개 번호만 콤마로 구분해서 답하세요.\n"
                f"예시: 3,1,5,2,4\n"
                f"수주/수출 성사, M&A, 기술 개발, 전략 변화, 경영 리스크 등 KAI 경영에 실질 영향 있는 기사 우선.\n"
                f"주가, 봉사, 현충원, 단순 MOU, PR 행사, 주가 등락은 제외.\n\n"
                f"기사 목록:\n{numbered}"
            )
            try:
                ans = chat_completion_content(
                    api_key,
                    model,
                    [{"role": "user", "content": prompt}],
                    provider=provider,
                    temperature=0.0,
                    max_tokens=50,
                    timeout=20,
                )
                indices = [int(x.strip()) - 1 for x in re.split(r"[,\s]+", ans) if x.strip().isdigit()]
                selected = [items[i] for i in indices if 0 <= i < len(items)]
                # 선택된 수가 n보다 적으면 나머지로 보충
                if len(selected) < n:
                    for item in items:
                        if item not in selected:
                            selected.append(item)
                        if len(selected) >= n:
                            break
                return selected[:n]
            except Exception:
                return list(items[:n])

        # 카테고리별 분류
        categorized: Dict[str, List[sqlite3.Row]] = {
            "kai": [], "government": [], "hanwha": [], "lig": [], "space": [], "partner": []
        }

        for row in rows:
            # 저가치 기사 제외
            cat = row["article_category"]
            _gov_briefing_keep = (
                cat == "government"
                and is_government_briefing_item(row["title"], row["summary"] or "")
                and is_aero_defense_space_relevant(f"{row['title']} {row['summary'] or ''}")
            )
            if _is_low_value(row["title"], row["summary"] or "") and not _gov_briefing_keep:
                continue
            if cat == "government" and is_government_briefing_item(row["title"], row["summary"] or "") and not is_aero_defense_space_relevant(f"{row['title']} {row['summary'] or ''}"):
                continue
            if cat == "kai":
                categorized["kai"].append(row)
            elif cat == "government":
                categorized["government"].append(row)
            elif cat == "hanwha":
                categorized["hanwha"].append(row)
            elif cat == "lig":
                categorized["lig"].append(row)
            elif cat == "space":
                categorized["space"].append(row)
            elif cat == "reference":
                # 협력사 섹션: 실제 방산/항공 협력사 기사만 포함 (무관 노이즈 차단)
                _ref_title = (row["title"] or "").lower()
                _ref_summary = (row["summary"] or "").lower()
                _ref_text = _ref_title + " " + _ref_summary
                _is_relevant = has_any_keyword(_ref_text, [
                    "율곡", "아스트", "astk", "파이어니어", "한국화이바",
                    "방산", "항공", "헬기", "전투기", "무인기", "위성", "우주",
                    "수리온", "kf-21", "fa-50", "kai", "한국항공우주",
                    "국방", "군", "수주", "수출", "방위",
                ])
                if _is_relevant:
                    categorized["partner"].append(row)

        # 각 카테고리 내 주제중복 제거
        for key in categorized:
            categorized[key] = _dedup_by_topic(categorized[key])

        # 모든 카테고리: AI가 KAI CEO 관점에서 중요도순 Top-5 선별 (최대 5개 제한)
        provider = get_ai_provider(conn)
        api_key = get_ai_api_key(conn)
        model = get_setting(conn, "classification_model") or ("gemini-3.7-flash" if provider == "gemini" else "gpt-4o-mini")
        LIMIT = 5

        contexts = {
            "kai": "KAI(한국항공우주산업) 관련 주요 뉴스",
            "government": "국방부, 방사청 등 정부기관의 방산 및 항공 정책/동향 소식",
            "hanwha": "경쟁사인 한화에어로스페이스/방산 동향 및 전략 소식",
            "lig": "경쟁사인 LIG넥스원 동향 및 전략 소식",
            "space": "우주항공, 위성, 누리호 및 국내외 방산/우주 산업 주요 뉴스",
            "partner": "KAI 주요 협력사 및 부품 공급업체 동향"
        }

        for key in categorized:
            if len(categorized[key]) > LIMIT:
                if api_key:
                    categorized[key] = _ai_select_top_n(api_key, model, provider, categorized[key], LIMIT, contexts.get(key, ""))
                else:
                    categorized[key] = categorized[key][:LIMIT]

        # 한글 카테고리 명칭 정의
        category_labels = {
            "kai":        "✈️ [KAI기사]",
            "government": "🏛️ [정부기관]",
            "hanwha":     "🔥 [경쟁사 - 한화]",
            "lig":        "🔥 [경쟁사 - LIG]",
            "space":      "🚀 [항공/방산/우주]",
            "partner":    "🤝 [협력사]"
        }

        # 중복 제거 및 AI 선별이 완료된 categorized 결과를 그대로 연동
        categorized_split = categorized

        def _clean_summary(text: str, title: str = "", category: str = "") -> str:
            """텔레그램용 핵심 문장 요약.

            정례브리핑/보도자료처럼 앞부분이 형식문인 경우에는 KAI 제품·정부정책
            키워드가 들어간 문장을 우선 사용한다.
            """
            import html as _html
            text = re.sub(r"<[^>]+>", "", text or "")
            text = _html.unescape(text)
            text = re.sub(r"\s+", " ", text).strip()
            title_text = re.sub(r"\s+", " ", title or "").strip()
            source_text = text

            summary_focus_keywords = (
                KAI_PRODUCT_KEYWORDS
                + KAI_COMPANY_KEYWORDS
                + KAI_CEO_CRITICAL_KEYWORDS
                + GOVERNMENT_KEYWORDS
                + [
                    "실전 배치", "전력화", "양산", "수출", "수주", "계약",
                    "북한 발사체", "발사체", "미사일", "방공식별구역",
                    "전투기", "헬기", "시뮬레이터", "훈련체계",
                ]
            )
            boilerplate_keywords = [
                "정례브리핑 시작", "장관께서는", "차관께서는", "오늘 제공해 드릴 자료",
                "자세한 내용은 보도자료", "질문·답변", "마이크 미사용",
            ]

            if source_text:
                sentences = [
                    s.strip(" -")
                    for s in re.split(r"(?<=[.!?。다])\s+|[\n\r]+", source_text)
                    if s.strip()
                ]

                def _sentence_score(sentence: str) -> int:
                    lowered = sentence.lower()
                    score = 0
                    for keyword in summary_focus_keywords:
                        if (keyword or "").lower() in lowered:
                            score += 3
                    if category == "government" and has_any_keyword(lowered, GOVERNMENT_KEYWORDS):
                        score += 4
                    if has_any_keyword(lowered, KAI_PRODUCT_KEYWORDS):
                        score += 6
                    if has_any_keyword(lowered, KAI_CEO_CRITICAL_KEYWORDS):
                        score += 5
                    if has_any_keyword(lowered, boilerplate_keywords):
                        score -= 8
                    if title_text and sentence in title_text:
                        score -= 2
                    return score

                best_sentence = max(sentences[:30], key=_sentence_score, default="")
                if best_sentence and _sentence_score(best_sentence) > 0:
                    text = best_sentence

            if len(text) > 60:
                text = text[:58] + "…"
            return text

        def _send(text: str) -> None:
            _url = f"https://api.telegram.org/bot{token}/sendMessage"
            _payload = json.dumps({
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }).encode("utf-8")
            _req = urllib.request.Request(_url, data=_payload, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(_req, timeout=15):
                pass

        time_str = now_seoul.strftime("%Y년 %m월 %d일 %H시")
        header = f"🌐 <b>KAI 뉴스 브리핑 ({time_str})</b>\n이전 브리핑 이후 신규 뉴스입니다."

        has_any_item = any(categorized_split[k] for k in category_labels)
        if not has_any_item:
            return

        full_msg_parts = [header, ""]
        
        for cat_code, label in category_labels.items():
            items = categorized_split.get(cat_code, [])
            if not items:
                continue

            full_msg_parts.append(f"<b>{label}</b>")
            for idx, item in enumerate(items, 1):
                import html as _html
                title = _html.escape(item["title"])
                link  = item["link"]
                summary = _html.escape(_clean_summary(item["summary"] or "", item["title"] or "", cat_code))
                entry = f"{idx}. <a href='{link}'>{title}</a>\n→ {summary}\n"
                full_msg_parts.append(entry)
            
        full_message = "\n".join(full_msg_parts).strip()
        
        # 4000자 이내인 경우 단일 메시지로 전송
        if len(full_message) <= 4000:
            if dry_run:
                print("--- [DRY RUN] Telegram Message ---")
                print(full_message)
                print("----------------------------------")
            else:
                _send(full_message)
        else:
            # 4000자 초과 시에만 분할 전송
            current_chunk = []
            for part in full_msg_parts:
                if len("\n".join(current_chunk)) + len(part) + 1 > 4000:
                    if dry_run:
                        print("--- [DRY RUN] Telegram Message (Chunk) ---")
                        print("\n".join(current_chunk))
                        print("------------------------------------------")
                    else:
                        _send("\n".join(current_chunk))
                    current_chunk = [part]
                else:
                    current_chunk.append(part)
            if current_chunk:
                if dry_run:
                    print("--- [DRY RUN] Telegram Message (Chunk) ---")
                    print("\n".join(current_chunk))
                    print("------------------------------------------")
                else:
                    _send("\n".join(current_chunk))

        # 전송 완료 후 — 이번 브리핑에 포함된 기사 모두 sent_briefing_at 마킹
        # (다음 브리핑에서 동일 기사 중복 발송 방지)
        sent_ids = [row["id"] for key in categorized_split.values() for row in key]
        if sent_ids and not dry_run:
            sent_at_str = datetime.now(timezone.utc).isoformat()
            conn.executemany(
                "UPDATE feed_items SET sent_briefing_at = ? WHERE id = ?",
                [(sent_at_str, aid) for aid in sent_ids],
            )
            try:
                conn.commit()
            except Exception:
                pass

        print(f"Telegram periodic briefing sent successfully for hour {now_seoul.hour}.")
    except Exception as e:
        print(f"Telegram periodic briefing failed: {e}")
