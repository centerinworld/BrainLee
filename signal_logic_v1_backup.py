"""
signal_logic.py — 모든 시그널/스크리너 로직의 단일 진실 원천 (Single Source of Truth)

■ 구조
  MARKET_LOGIC    : 시장(KOSPI/KOSDAQ/나스닥 등) 진입 판단 기준
  STOCK_LOGIC     : 개별 종목 시그널 판단 기준 (종목 분석 탭)
  SCREENER_REGISTRY : AI 스크리너 4종 (함수 + 설명 페어링)

■ 수정 규칙
  1. 로직 파라미터를 바꿀 때는 description 도 같이 수정한다.
  2. 새 스크리너 추가 시 SCREENER_REGISTRY 에 항목만 추가한다.
  3. 함수 본체는 signal_engine.py / screener.py 에 남기되,
     핵심 임계값(THRESHOLD)은 이 파일의 상수로 정의하고 import해서 사용한다.
  4. /api/screener/meta 엔드포인트가 이 파일을 읽어 프론트엔드로 전송한다.
     → 프론트는 설명을 하드코딩하지 않고 API로 받는다.
"""

# ══════════════════════════════════════════════════════════════════
# ① 임계값 상수 — 여기서만 바꾸면 로직+설명 동시 반영
# ══════════════════════════════════════════════════════════════════

# ── 추세 Leading (Minervini) ──────────────────────────────────────
TREND_MKTCAP_MIN      = 50_000_000_000   # 500억 (시총 하한)
TREND_TVOL_MIN_KRW    = 5_000_000_000    # 50억 (5일 평균 거래대금)
TREND_TVOL_MIN_SUOKU  = 50               # 50억 (stock_universe 단위: 억원)
TREND_HIGH52_PCT      = -20              # 52주 고점 대비 -20% 이내
TREND_VOL_RATIO_MIN   = 2.0              # 거래량 폭발 배수
TREND_RSI_MIN         = 60               # RSI 최소값
TREND_SCORE_STRONG    = 20               # 강력매수 점수
TREND_SCORE_BUY       = 14               # 매수 점수
TREND_SCORE_WATCH     = 8                # 관심 점수

# ── 진입트리거 TOP20 (3-트랙) ─────────────────────────────────────
TOP20_MKTCAP_MIN      = 50_000_000_000   # 500억
TOP20_TVOL_MIN_KRW    = 5_000_000_000    # 50억
TOP20_TVOL_MIN_SUOKU  = 50               # 50억
TOP20_TRACK_A_MIN     = 2                # 최소 Track A (장기 정배열)
TOP20_TRACK_B_MIN     = 2                # 최소 Track B (Graham 25% 할인)
TOP20_DISC_STRONG     = 40               # 강력 할인율 (3점)
TOP20_DISC_MID        = 25               # 중간 할인율 (2점)
TOP20_DISC_WEAK       = 15               # 약한 할인율 (1점)
TOP20_SECTOR_THRESH   = 0.5              # 섹터 활성화 기준

# ── 가치매수 (Graham) ─────────────────────────────────────────────
VALUE_DISC_DEEP       = 25               # 심층 저평가 할인율
VALUE_DISC_MIN        = 15               # 최소 할인율
VALUE_PBR_DEEP        = 0.7              # 심층 PBR
VALUE_PER_MAX         = 15               # 최대 PER
VALUE_SCORE_MIN       = 5                # 최소 점수

# ── 재무 스크리너 (8축) ───────────────────────────────────────────
FIN_SCORE_STRONG      = 22               # 강력매수 점수
FIN_SCORE_BUY         = 16               # 매수 점수
FIN_SCORE_WATCH       = 12               # 관심 점수

# ── AI 적극검토 (combo) ───────────────────────────────────────────
COMBO_TREND_SCORE_MIN = 10               # 추세 점수 최솟값 (8→10, v4 강화)
COMBO_VALUE_SCORE_MIN = 6                # 가치 점수 최솟값
COMBO_FIN_SCORE_MIN   = 18               # 재무 점수 최솟값
COMBO_METHODS_MIN     = 2                # 필요 방법론 수


# ══════════════════════════════════════════════════════════════════
# ② 시장 진입 판단 로직 설명 (개별 종목 탭 — 시장 환경 섹션)
# ══════════════════════════════════════════════════════════════════
MARKET_LOGIC = {
    "id": "market",
    "title": "시장 환경 (Market Regime)",
    "subtitle": "코스피·나스닥·S&P500 복합 필터 — Step1 허가 조건",
    "accent_color": "#60a5fa",
    "items": [
        {
            "title": "MA200 필터 (나스닥/S&P500/KOSPI)",
            "desc": (
                f"3대 지수 현재가 > 200일 이평선 = 매매 허가 (폴 튜더 존스 Rule). "
                f"200일선 이하 = 하락 국면, 모든 매수 신호 보수적으로 해석."
            ),
        },
        {
            "title": "VIX 추세",
            "desc": (
                "VIX 현재가 vs 20일 이평선. 이평 위 = Risk-off(공포 상승) → 빨간불. "
                "이평 아래 = Risk-on(공포 완화) → 초록불."
            ),
        },
        {
            "title": "원/달러 추세",
            "desc": (
                "환율 현재가 vs 20일 이평선. 이평 위(원화 약세) = 외국인 이탈 위험 → 빨간불. "
                "이평 아래 = 외국인 유입 우호 환경."
            ),
        },
        {
            "title": "KOSPI ADR (등락비율 20일)",
            "desc": (
                "ADR ≥ 120 = 과매수 주의 / ADR ≤ 75 = 과매도 반등 구간. "
                "시장 전반의 온기를 수치화한 브레드스(breadth) 지표."
            ),
        },
        {
            "title": "KOSPI 이평 정배열 (MA20 > MA60)",
            "desc": (
                "단기 이평이 중기 이평 위 = 국내 시장 단기 상승 국면. "
                "역배열 시 신규 진입 자제 권장."
            ),
        },
        {
            "title": "수급 흐름 (외국인+기관 동반)",
            "desc": (
                "최근 3일 외국인·기관 동반 순매수 = 스마트머니 유입. "
                "동반 순매도 시 추세 신뢰도 하락."
            ),
        },
        {
            "title": "Fear & Greed (수동 입력)",
            "desc": (
                "CNN 공포탐욕지수 수동 입력. 55 이상 = 탐욕(초록), 30 이하 = 공포(빨강). "
                "극단적 공포(< 20) 구간은 역발상 매수 기회로도 해석 가능."
            ),
        },
    ],
}


# ══════════════════════════════════════════════════════════════════
# ③ 개별 종목 시그널 판단 로직 설명
# ══════════════════════════════════════════════════════════════════
STOCK_LOGIC = {
    "id": "stock_signals",
    "title": "개별 종목 시그널 (3-트랙 종합 판정)",
    "subtitle": "추세(Track A) + 가치(Track B) + 섹터 회복(Track C) 독립 판정",
    "accent_color": "#2dd4bf",
    "items": [
        {
            "title": "이평선 정배열 (Track A 기반)",
            "desc": (
                "MA5 > MA20 > MA60 = 단기 정배열(추세 신호 유효 조건). "
                "MA120 > MA200도 추가 확인 시 장기 추세 신뢰도 상승."
            ),
        },
        {
            "title": "MACD 시그널 (진입 트리거)",
            "desc": (
                "이평 정배열 상태에서 MACD 골든크로스 또는 오실레이터 상승 전환만 유효 신호. "
                "역배열 상태의 MACD는 신뢰 불가."
            ),
        },
        {
            "title": "RSI 시그널 (모멘텀 확인)",
            "desc": (
                f"RSI ≥ {TREND_RSI_MIN} = 진정한 상승 모멘텀. "
                f"RSI < {TREND_RSI_MIN}에서의 정배열은 약세 반등일 가능성 높음."
            ),
        },
        {
            "title": "Graham 내재가치 (Track B)",
            "desc": (
                f"√(22.5 × EPS × BPS) = Graham 내재가치. "
                f"할인율 ≥ {TOP20_DISC_STRONG}% + 영업흑자 = 강력 가치 매수. "
                f"할인율 ≥ {TOP20_DISC_MID}% = 분할매수 검토. "
                f"시장 하락과 무관하게 독립 판정 (가치는 시장을 타지 않는다)."
            ),
        },
        {
            "title": "섹터 회복 보너스 (Track C)",
            "desc": (
                f"섹터 내 주도종목의 {int(TOP20_SECTOR_THRESH*100)}%↑가 52주선 상회 = 섹터 활성화. "
                f"Top-Down 접근: 섹터가 먼저 살아야 개별주 진입 신뢰도 상승."
            ),
        },
        {
            "title": "ATR 손절 (리스크 관리)",
            "desc": (
                "ATR(14) 기반 기계적 손절 기준. "
                "진입가 - ATR × 2 = 손절선. ATR이 과도하게 크면 포지션 크기 줄임."
            ),
        },
        {
            "title": "외국인/기관 수급",
            "desc": (
                "5일 누적 외국인·기관 순매수. 동반 매수 = 스마트머니 개입. "
                "추세 신호와 결합 시 신뢰도 배가."
            ),
        },
        {
            "title": "종합 판정 기준",
            "desc": (
                "추세(A) + 가치(B) 동시 충족 = 최강 매수. "
                "추세만 = 모멘텀 플레이. 가치만(market_danger≤1) = 분할매수. "
                "시장 위험도는 가치 신호를 차단하지 않고 '경고 문구'만 추가."
            ),
        },
    ],
}


# ══════════════════════════════════════════════════════════════════
# ④ AI 스크리너 4종 레지스트리 — 함수 레퍼런스 + 설명 페어링
# ══════════════════════════════════════════════════════════════════

def _trigger_top20_desc():
    """진입트리거 TOP20 설명 — 상수 참조로 자동 동기화."""
    return [
        {
            "title": f"[Track A] 추세 점수 (0~4점)",
            "desc": (
                f"레벨 1: 단기 정배열(curr>MA20>MA60) | "
                f"레벨 2: 장기 정배열(curr>MA120>MA200) | "
                f"레벨 3: 52주 고점 {TOP20_TRACK_A_MIN}점 이상 = -20% 이내 | "
                f"레벨 4: RSI≥{TREND_RSI_MIN} AND 거래량 {TREND_VOL_RATIO_MIN}배↑ = Minervini 완성 = 4점."
            ),
        },
        {
            "title": f"[Track B] 가치 강도 (0~3점)",
            "desc": (
                f"Graham 내재가치 = √(22.5 × EPS × BPS) 대비 할인율. "
                f"할인 {TOP20_DISC_STRONG}%↑ + 영업흑자 = 3점(강력매수), "
                f"{TOP20_DISC_MID}%↑ = 2점(분할매수), "
                f"{TOP20_DISC_WEAK}%↑ = 1점(관심). "
                f"시장 하락과 무관하게 독립 판정."
            ),
        },
        {
            "title": f"[Track C] 섹터 보너스 (0~1점)",
            "desc": (
                f"섹터 내 주도종목 {int(TOP20_SECTOR_THRESH*100)}%↑가 52주선 상회 "
                f"→ 섹터 활성화 = +1점. 섹터 주도 테마 편승 여부 확인."
            ),
        },
        {
            "title": "[종합점수] A×2 + B×2 + C",
            "desc": (
                "추세와 가치에 동일한 가중치(×2)를 부여. "
                f"두 트랙 모두 충족 시 최고점(A4×2+B3×2+C1=15). "
                "어느 한 트랙만 우수해도 TOP20 진입 가능."
            ),
        },
        {
            "title": "[유동성 필터]",
            "desc": (
                f"시가총액 {TREND_MKTCAP_MIN//100_000_000}억 미만 제외 / "
                f"최근 5거래일 평균 거래대금 {TOP20_TVOL_MIN_SUOKU}억 미만 제외. "
                "잡주·저유동성 종목은 세력 진입 자체 불가 → 추세 신뢰 불가."
            ),
        },
        {
            "title": "[최소 진입 기준]",
            "desc": (
                f"Track A ≥ {TOP20_TRACK_A_MIN} (장기 정배열) OR "
                f"Track B ≥ {TOP20_TRACK_B_MIN} (Graham {TOP20_DISC_MID}%+ 할인) 중 하나 이상 필수. "
                "단기 정배열만(A=1) 또는 가치 관심만(B=1)은 TOP20 제외."
            ),
        },
        {
            "title": "[AI 적극검토 후보]",
            "desc": (
                f"Track A≥3 + Track B≥2 동시충족 OR 재무스크리너 교차 종목. "
                "두 방법론이 동시에 '매수' 판정 → AI 적극 검토 탭 이동 권장."
            ),
        },
    ]


def _trend_leading_desc():
    """추세 Leading 설명 — 상수 참조로 자동 동기화."""
    return [
        {
            "title": "[1단계] 유동성 필터 — 잡주 제거",
            "desc": (
                f"시가총액 {TREND_MKTCAP_MIN//100_000_000}억 이상 + "
                f"최근 5거래일 평균 거래대금 {TREND_TVOL_MIN_SUOKU}억 이상. "
                "유동성 없는 잡주는 세력이 진입 자체를 못함 → 추세 신뢰 불가."
            ),
        },
        {
            "title": "[2단계] 미너비니 추세 템플릿 (ALL AND)",
            "desc": (
                "① 현재가 > MA120 & MA200 ② MA120 > MA200 (장기 정배열) "
                f"③ 52주 최고가 {TOP20_HIGH52_PCT if False else TREND_HIGH52_PCT}% 이내 "
                "④ 현재가 > MA5 > MA20 > MA60 단기정배열. 4개 조건 모두 충족 필수."
            ),
        },
        {
            "title": f"[3단계] RSI + 거래량 폭발 (필수 AND)",
            "desc": (
                f"① RSI(14) ≥ {TREND_RSI_MIN} "
                f"② 오늘 거래량 > 20일 평균 × {TREND_VOL_RATIO_MIN}배 (필수). "
                "오닐·미너비니의 '진짜 돌파' 기준."
            ),
        },
        {
            "title": "볼린저 밴드 상단 돌파 (가산점)",
            "desc": (
                "BB 상단 돌파 = +1점 가산. 스퀴즈(밴드폭 최소 구간) 후 돌파 = +3점. "
                "BB는 필수 조건에서 가산점으로 변경 → Track A=4 종목도 포함 가능."
            ),
        },
        {
            "title": "RS 상대강도 (최대 3점)",
            "desc": (
                "KOSPI 대비 3개월 수익률 초과분. +15%p↑ = 강력 아웃퍼폼(+3). "
                "+5%p↑ = 아웃퍼폼(+2). 시장 역풍에도 강한 종목이 주도주."
            ),
        },
        {
            "title": "섹터 활성화 (최대 3점)",
            "desc": (
                "섹터 내 주요 종목의 추세 강도. 60%↑ = 🔥주도섹터(+3). "
                "40%↑ = 활성화(+2). 섹터가 먼저 살아야 개별주도 산다."
            ),
        },
        {
            "title": "등급 기준 (점수제)",
            "desc": (
                f"강력매수 ≥ {TREND_SCORE_STRONG}점, "
                f"매수 ≥ {TREND_SCORE_BUY}점, "
                f"관심 ≥ {TREND_SCORE_WATCH}점. "
                "기본 5점(템플릿 통과) + 정배열(2) + 신고가(3) + RSI(2) "
                "+ 거래량(3) + BB(3) + MA200(2) + 섹터(3) + RS(3) + 수급(2)."
            ),
        },
    ]


def _value_desc():
    """가치매수 설명 — 상수 참조로 자동 동기화."""
    return [
        {
            "title": "Graham 내재가치 공식",
            "desc": (
                "√(22.5 × EPS × BPS) — 벤저민 그레이엄의 주식 내재가치 산출식. "
                "22.5는 PER 15 × PBR 1.5의 곱. "
                "EPS(주당순이익) × BPS(주당순자산)의 기하평균에 해당."
            ),
        },
        {
            "title": f"안전마진 {VALUE_DISC_MIN}%~{VALUE_DISC_DEEP}%",
            "desc": (
                f"내재가치 대비 현재가가 {VALUE_DISC_MIN}% 이상 싸야 최소 진입. "
                f"{VALUE_DISC_DEEP}% 이상 = 강력 저평가. "
                "그레이엄: '가격은 단기에 투표기계, 장기에 체중계'."
            ),
        },
        {
            "title": f"PBR < 1 / PER < {VALUE_PER_MAX} 조건",
            "desc": (
                f"PBR < 1 = 장부가 미만 거래. PER < {VALUE_PER_MAX} = "
                f"{VALUE_PER_MAX}년 이내 원금 회수. 이익 지속가능성 확인 필수."
            ),
        },
        {
            "title": "현재 분기 영업흑자 필수",
            "desc": (
                "최신 분기 EPS > 0 AND 영업이익 > 0 동시 충족 종목만 포함. "
                "과거 흑자·현재 적자 종목은 Graham 계산 대상 제외."
            ),
        },
        {
            "title": "MA20 완만화 / MACD 강세 다이버전스",
            "desc": (
                "이동평균 기울기 평탄화 = 하락 추세 완화 신호. "
                "MACD 다이버전스 = 매도 모멘텀 약화. "
                "가치 종목도 기술적 바닥 확인 후 진입 시 타이밍 최적화."
            ),
        },
        {
            "title": "스마트머니 3/5",
            "desc": (
                "5거래일 중 3일 이상 기관·외국인 동시 순매수 "
                "= 정보력 있는 투자자들의 저점 매집. 가치 매수의 촉매."
            ),
        },
        {
            "title": "복합 점수 9점",
            "desc": (
                "위 조건들을 점수화(9점 만점). "
                f"6점↑ = 강력매수, 4점↑ = 매수. "
                "단일 조건보다 복합 충족이 훨씬 신뢰도 높음."
            ),
        },
    ]


def _fin_screener_desc():
    """재무 스크리너 설명 — 상수 참조로 자동 동기화."""
    return [
        {
            "title": "축1. 주가 소외도",
            "desc": (
                f"PBR<0.5 (+2), PBR<1 (+1), PER<6 (+2), PER<12 (+1), 소형주 (+1). "
                "원리: 낮을수록 시장이 외면 중. 실적 반전 시 '재발견'으로 급격한 정상화."
            ),
        },
        {
            "title": "축2. 매출 성장 기울기",
            "desc": (
                "분기 선형회귀 기울기 >20%/분기 (+2), >8% (+1). "
                "+ YoY >50% (+2), >20% (+1). "
                "원리: 주가보다 매출이 먼저 달라진다. 속도와 가속도가 핵심 선행지표."
            ),
        },
        {
            "title": "축3. 주가 낙폭 과다",
            "desc": (
                "52주 고점 대비 -60% (+3), -40% (+2), -25% (+1). "
                "+ 저점 대비 +15% 반등 (+1) — 바닥 확인. "
                "원리: 과도 하락 + 반등 조짐 = 턴어라운드 진입 구간."
            ),
        },
        {
            "title": "축4. 턴어라운드 시그널 [가장 강력]",
            "desc": (
                "과거 적자 → 최신 흑자 전환 (+4 MAX). "
                "적자지만 3분기 연속 개선 (+2), 매출 급증 중인 적자기업 (+1). "
                "원리: 적자→흑자 전환은 시장이 가장 늦게 인식. 1~2분기 선행 포착이 핵심."
            ),
        },
        {
            "title": "축5. 장기 실적 추세",
            "desc": (
                "연간 매출 기울기 >15%/년 (+2), 성장 가속화 (+1), 연간 흑자전환 (+1). "
                "원리: 분기 노이즈 제거 후 구조적 실적 개선인지 연간 데이터로 검증."
            ),
        },
        {
            "title": "축6. 섹터 소외",
            "desc": (
                "섹터 리더는 52주선 위인데 개별주는 -30% 이하 (+3). "
                "원리: 섹터 순환 시 소외주에 탄력. 리더 먼저, 졸개 나중 패턴."
            ),
        },
        {
            "title": "축7. 거시 낙폭 기회",
            "desc": (
                "KOSPI 대비 RS -15%p (+3), -8% (+2), -3% (+1). "
                "시장 급락+실적 견고+매출 성장 → 매수 기회 (+1). "
                "원리: 전쟁·금리 등 거시 이슈로 시장 폭락 시 실적 종목은 부당하게 빠진다."
            ),
        },
        {
            "title": "축8. 실적 가속",
            "desc": (
                "영업이익 증가폭 3분기 연속 확대 (+2), 레버리지 발현 (+2). "
                "원리: 고정비 커버 후 추가 매출은 전액 이익. 이 구간에서 EPS가 폭발."
            ),
        },
        {
            "title": "등급 기준",
            "desc": (
                f"{FIN_SCORE_STRONG}점↑ 강력매수 | "
                f"{FIN_SCORE_BUY}점↑ 매수 | "
                f"{FIN_SCORE_WATCH}점↑ 관심 (최대 32점)"
            ),
        },
    ]


def _combo_desc():
    """AI 적극검토 설명 — 상수 참조로 자동 동기화."""
    return [
        {
            "title": "교집합 기준",
            "desc": (
                f"추세점수 ≥ {COMBO_TREND_SCORE_MIN}, 가치점수 ≥ {COMBO_VALUE_SCORE_MIN}, "
                f"재무점수 ≥ {COMBO_FIN_SCORE_MIN} 중 {COMBO_METHODS_MIN}개 이상 동시 충족. "
                "각 스크리너 점수 미달 시 해당 방법론 플래그 해제."
            ),
        },
        {
            "title": "모멘텀 필수 조건",
            "desc": (
                "가치+재무 두 가지만 충족 (추세 없음) → 제외. "
                "추세+가치 또는 추세+재무 또는 3가지 모두 충족해야 포함. "
                "원리: 아무리 싸도 주가가 움직이지 않으면 의미없다."
            ),
        },
        {
            "title": "종합 점수 계산",
            "desc": (
                "combo_score = (추세/14 + 가치/9 + 재무/30) × 10 (정규화 합산). "
                "match_count 우선 정렬 → combo_score 차순위."
            ),
        },
        {
            "title": "🏆 3개 모두 필터",
            "desc": (
                "추세·가치·재무 3가지 방법론 전부 충족 = 최고 신뢰도 종목. "
                "세 개의 독립적인 시각이 모두 '매수'를 가리키는 극히 드문 경우."
            ),
        },
    ]


# ══════════════════════════════════════════════════════════════════
# ⑤ 스크리너 레지스트리 — 메타 API가 이 딕셔너리를 직렬화
# ══════════════════════════════════════════════════════════════════
SCREENER_REGISTRY = {
    "trigger": {
        "id":            "trigger",
        "tab_label":     "🎯 진입트리거 TOP20",
        "title":         "3-트랙 종합 진입 TOP20",
        "subtitle":      f"추세(0~4) + 가치(0~3) + 섹터(0~1) 종합점수 전종목 스캔 → 상위 20개",
        "accent_color":  "#f59e0b",
        "badge_text":    f"Track A 추세 0~4점 (Minervini 완성=4)  |  Track B 가치 0~3점 (Graham {TOP20_DISC_STRONG}%+ 할인=3)  |  Track C 섹터 0~1점  |  종합점수 = A×2 + B×2 + C",
        "footnote":      "★AI = Track A(추세) + Track B(가치) 동시 충족 OR 재무스크리너 포함 종목 → AI 적극검토 후보",
        "endpoint":      "/api/trigger-ranking",
        "items":         _trigger_top20_desc(),
    },
    "value": {
        "id":            "value",
        "tab_label":     "💎 가치매수",
        "title":         "Graham 가치투자 스캔",
        "subtitle":      f"Graham 내재가치 √(22.5×EPS×BPS) 대비 {VALUE_DISC_MIN}%↑ 저평가 + 영업흑자 필수",
        "accent_color":  "#f59e0b",
        "badge_text":    f"Graham 할인율 {VALUE_DISC_DEEP}%↑ = 강력 / {VALUE_DISC_MIN}%↑ = 관심 | PBR<1 + PER<{VALUE_PER_MAX} 심층 조건",
        "footnote":      "최신 분기 EPS>0 + 영업이익>0 동시 충족 종목만 대상 (과거 흑자·현재 적자 제외)",
        "endpoint":      "/api/signals/value-candidates",
        "items":         _value_desc(),
    },
    "ai": {
        "id":            "ai",
        "tab_label":     "📊 재무 스크리너",
        "title":         "소외 턴어라운드 + 성장 기울기 스크리너",
        "subtitle":      "8축 점수 — 소외도·매출기울기·낙폭·턴어라운드·장기추세·섹터소외·거시기회·실적가속",
        "accent_color":  "#a78bfa",
        "badge_text":    f"강력매수 ≥ {FIN_SCORE_STRONG}점  |  매수 ≥ {FIN_SCORE_BUY}점  |  관심 ≥ {FIN_SCORE_WATCH}점  (최대 32점)",
        "footnote":      "소외 + 턴어라운드 + 성장 기울기 3요소 중첩 시 최고 등급. 단순 저PBR 제외.",
        "endpoint":      "/api/signals/fin-screener",
        "items":         _fin_screener_desc(),
    },
    "trend": {
        "id":            "trend",
        "tab_label":     "📈 추세 Leading",
        "title":         "미너비니(Minervini) 3단계 추세 필터",
        "subtitle":      f"시총 {TREND_MKTCAP_MIN//100_000_000}억↑ × 거래대금 {TREND_TVOL_MIN_SUOKU}억↑ × MA120/200 정배열 × 52주고점-20% × RSI{TREND_RSI_MIN}↑ × 거래량{TREND_VOL_RATIO_MIN}배↑",
        "accent_color":  "#2dd4bf",
        "badge_text":    f"오늘의 거래량 폭발({TREND_VOL_RATIO_MIN}배↑) 주도주 — 전 조건 AND",
        "footnote":      "거래량 2배↑ + MA 정배열은 실제 돌파일에만 발생. 시장 약세기 결과 없음이 정상.",
        "endpoint":      "/api/signals/trend-candidates",
        "items":         _trend_leading_desc(),
    },
    "combo": {
        "id":            "combo",
        "tab_label":     "⭐ AI 적극 검토",
        "title":         "AI 적극 검토 — 복수 방법론 교집합",
        "subtitle":      f"추세·가치·재무 스크리너 중 {COMBO_METHODS_MIN}개 이상 동시 충족 (추세 필수 포함)",
        "accent_color":  "#ef4444",
        "badge_text":    f"추세점수 ≥ {COMBO_TREND_SCORE_MIN}  |  가치점수 ≥ {COMBO_VALUE_SCORE_MIN}  |  재무점수 ≥ {COMBO_FIN_SCORE_MIN}",
        "footnote":      "가치+재무만 충족(추세 없음)은 제외. 주가가 움직이지 않으면 아무리 싸도 의미없다.",
        "endpoint":      "/api/signals/combo-candidates",
        "items":         _combo_desc(),
    },
}


# ══════════════════════════════════════════════════════════════════
# ⑥ 외부 호출용 헬퍼
# ══════════════════════════════════════════════════════════════════

def get_all_meta() -> dict:
    """
    /api/screener/meta 가 반환할 전체 메타 딕셔너리.
    함수 레퍼런스를 제외한 JSON 직렬화 가능한 형태로 반환.
    """
    result = {}
    for key, reg in SCREENER_REGISTRY.items():
        result[key] = {k: v for k, v in reg.items() if k != "fn"}
    return {
        "screeners": result,
        "market":    MARKET_LOGIC,
        "stock":     STOCK_LOGIC,
    }


def get_screener_meta(screener_id: str) -> dict:
    """특정 스크리너의 메타 반환."""
    reg = SCREENER_REGISTRY.get(screener_id)
    if not reg:
        return {}
    return {k: v for k, v in reg.items() if k != "fn"}
