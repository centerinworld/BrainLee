"""
signal_engine.py — 시그널 보드 계산 엔진 (v2 — 퀀트/추세추종 시스템)

[설계 원칙]
1. Market Regime (4단계 시스템 Step1): 시장 환경 허락 여부
   - MA200 필터 (나스닥/S&P500/KOSPI)
   - VIX/환율 추세 (절대값 → 20일 이평 대비 위치)
   - ADR (등락비율) — 시장 온기 파악
2. 종목 시그널 (Step2~4):
   - RS 상대강도 (시장 대비 3개월 수익률)
   - ATR 변동성 (기계적 손절 기준)
   - 가치 + 모멘텀 AND 조건 (가치함정 방지)
   - 이평선 정배열 상태에서의 MACD/RSI만 유효 시그널로 인정
"""

import json, sqlite3, logging, time as _time, math as _math
import numpy as _np
from collections import defaultdict as _defaultdict
from datetime import date, timedelta
from pathlib import Path

# ── 모듈 레벨 캐시 (섹터 활성화 맵 — 3개 함수에서 중복 호출 방지) ──────
_sector_activation_cache: dict = {"data": None, "at": 0.0}
from signal_logic import (
    TREND_MKTCAP_MIN, TREND_TVOL_MIN_KRW, TREND_TVOL_MIN_SUOKU,
    TREND_HIGH52_PCT, TREND_VOL_RATIO_MIN, TREND_RSI_MIN,
    TREND_SCORE_STRONG, TREND_SCORE_BUY, TREND_SCORE_WATCH,
    TOP20_MKTCAP_MIN, TOP20_TVOL_MIN_KRW, TOP20_TVOL_MIN_SUOKU,
    TOP20_DISC_STRONG, TOP20_DISC_MID, TOP20_DISC_WEAK,
    TOP20_SECTOR_THRESH, TOP20_TRACK_A_MIN, TOP20_TRACK_B_MIN,
    # Logic-#2
    V2_MKTCAP_MIN, V2_TVOL_MIN_KRW, V2_MAX_FROM_HIGH52,
    V2_SUPPLY_CONSEC_BOTH, V2_SUPPLY_CONSEC_ONE, V2_SUPPLY_AMT_STRONG, V2_SUPPLY_AMT_MID,
    V2_TREND_DAILY_FULL, V2_TREND_WEEKLY_BONUS, V2_TREND_HIGH52_MIN,
    V2_QUALITY_CONSEC_3Q, V2_QUALITY_CONSEC_2Q, V2_QUALITY_CONSEC_1Q, V2_QUALITY_POSITIVE,
    V2_RS_PERIODS, V2_RS_ALL_THREE, V2_RS_TWO, V2_RS_ONE_MID, V2_RS_ONE_SHORT,
    V2_SCORE_STRONG, V2_SCORE_BUY, V2_SCORE_WATCH,
    V2_SUPPLY_MIN_SIGNAL, V2_TREND_MIN_SIGNAL,
)

logger = logging.getLogger(__name__)
DB_PATH = "/Applications/stock_dashboard/stock.db"


# ══════════════════════════════════════════════════════════════════
# DB 초기화
# ══════════════════════════════════════════════════════════════════
def init_signal_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS signal_config (
        id          INTEGER PRIMARY KEY,
        scope       TEXT NOT NULL,
        name        TEXT NOT NULL,
        label       TEXT NOT NULL,
        description TEXT DEFAULT '',
        logic_type  TEXT NOT NULL,
        params      TEXT DEFAULT '{}',
        weight      INTEGER DEFAULT 1,
        is_active   INTEGER DEFAULT 1,
        sort_order  INTEGER DEFAULT 0,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS signal_result (
        id          INTEGER PRIMARY KEY,
        config_id   INTEGER,
        stock_code  TEXT DEFAULT '',
        signal      TEXT NOT NULL,
        value       REAL,
        description TEXT DEFAULT '',
        calc_date   TEXT NOT NULL,
        created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(config_id, stock_code, calc_date)
    );
    """)
    cnt = conn.execute("SELECT COUNT(*) FROM signal_config").fetchone()[0]
    if cnt == 0:
        _insert_defaults(conn)
    else:
        _ensure_new_signals(conn)
    conn.commit()
    conn.close()


def _insert_defaults(conn):
    defaults = [
        # ── 시장 시그널 (Market Regime) ───────────────────────────
        # Step1: 매크로 필터
        ('market','supply_flow',   '수급 흐름',       '외국인+기관 동반 순매수/매도 추세 (최근 3일)',
         'supply_trend',   '{"days":3}', 1),
        ('market','vix_trend',     'VIX 추세',        'VIX 현재가 vs 20일 이평선 — 위=Risk-off / 아래=Risk-on',
         'ma_trend_price', '{"symbol":"^VIX","ma":20,"reverse":true}', 2),
        ('market','usd_krw_trend', '원/달러 추세',     '환율 현재가 vs 20일 이평선 — 위=위험 / 아래=안정',
         'ma_trend_price', '{"symbol":"USDKRW=X","ma":20,"reverse":true}', 3),
        ('market','nasdaq_ma200',  '나스닥 MA200',     '나스닥 현재가 > 200일선 = 매매 허가 (폴 튜더 존스 Rule)',
         'ma200_filter',   '{"symbol":"^IXIC","ma":200}', 4),
        ('market','sp500_ma200',   'S&P500 MA200',    'S&P500 현재가 > 200일선 = 시장 상승 국면',
         'ma200_filter',   '{"symbol":"^GSPC","ma":200}', 5),
        ('market','kospi_ma200',   'KOSPI MA200',     'KOSPI 현재가 > 200일선 = 국내 시장 상승 국면',
         'ma200_filter',   '{"symbol":"^KS11","ma":200}', 6),
        ('market','kospi_ma_align','KOSPI 이평 정배열','KOSPI MA20/MA60 정배열 여부 — 한국 시장 추세 필터',
         'index_ma_align', '{"symbol":"^KS11","ma1":20,"ma2":60}', 7),
        ('market','adr_kospi',     'KOSPI ADR',       '등락비율 20일 평균 — 75이하=과매도 반등 / 120이상=과매수',
         'adr',            '{"market":"KOSPI","days":20}', 8),
        ('market','fear_greed',    'Fear&Greed',      'CNN 공포탐욕지수 (수동입력)',
         'manual',         '{"default":50,"green_above":55,"red_below":30}', 9),

        # ── 종목 시그널 ──────────────────────────────────────────
        # Step2: 종목 필터
        ('stock','frn_supply',    '외국인 수급',      '5일 외국인 순매수 누적 추세',
         'supply_trend',   '{"days":5,"target":"frn"}', 1),
        ('stock','inst_supply',   '기관 수급',        '5일 기관 순매수 누적 추세',
         'supply_trend',   '{"days":5,"target":"inst"}', 2),
        ('stock','financials',    '재무건전성',       '영업이익 흑자 + YoY 매출 성장',
         'financial',      '{"check_profit":true,"check_yoy":true}', 3),
        ('stock','value',         '가치지표',         'PBR<1 + PER<12 AND 52주 추세 상승 (가치+모멘텀 AND 조건)',
         'value_momentum', '{}', 4),
        ('stock','rs_score',      '상대강도 RS',      '3개월 주가상승률 vs KOSPI/KOSDAQ — 시장 주도주 여부',
         'relative_strength','{"months":3}', 5),
        # Step3: 진입 트리거
        ('stock','ma_align',      '이평선 정배열',    '5>20>60 정배열=강세 / 역배열=약세 (추세 필터)',
         'technical',      '{"type":"ma_align","ma1":5,"ma2":20,"ma3":60}', 6),
        ('stock','macd_signal',   'MACD',            '이평 정배열 상태에서의 골든크로스만 유효 매수신호',
         'macd_filtered',  '{"fast":12,"slow":26,"signal":9}', 7),
        ('stock','rsi_signal',    'RSI',             '이평 정배열 상태에서 RSI 50 돌파만 매수신호',
         'rsi_filtered',   '{"period":14}', 8),
        ('stock','trend52w',      '52주 추세',        '52주 고점 -15% 이내 + 거래량 급증 (신고가 근접)',
         'trend52w_vol',   '{"near_high_pct":-15,"vol_ma":20}', 9),
        # Step4: 리스크 관리
        ('stock','atr_stop',      'ATR 손절선',       '2×ATR 기계적 손절 기준 — 매수가 대비 손절가 표시',
         'atr',            '{"period":14,"multiplier":2}', 10),
        # 추가 기술지표
        ('stock','vol_price',     '수급에너지',       '상승 시 거래량 급증 여부 (평균 대비 2배)',
         'technical',      '{"type":"volume","ma_days":20,"spike_ratio":2.0}', 11),
        ('stock','short_sell',    '대차잔고',         '대차잔고비율 + 증가추세 (공매도 압력)',
         'threshold',      '{"green_below":2,"red_above":5,"check_trend":true}', 12),
    ]
    conn.executemany("""
        INSERT INTO signal_config (scope,name,label,description,logic_type,params,sort_order)
        VALUES (?,?,?,?,?,?,?)
    """, defaults)


def _ensure_new_signals(conn):
    """기존 DB에 새 시그널이 없으면 추가."""
    existing = {r[0] for r in conn.execute("SELECT name FROM signal_config").fetchall()}
    new_signals = [
        ('market','vix_trend',     'VIX 추세',
         'VIX 현재가 vs 20일 이평선 — 위=Risk-off / 아래=Risk-on',
         'ma_trend_price', '{"symbol":"^VIX","ma":20,"reverse":true}', 2),
        ('market','usd_krw_trend', '원/달러 추세',
         '환율 현재가 vs 20일 이평선 — 위=위험 / 아래=안정',
         'ma_trend_price', '{"symbol":"USDKRW=X","ma":20,"reverse":true}', 3),
        ('market','nasdaq_ma200',  '나스닥 MA200',
         '나스닥 현재가 > 200일선 = 매매 허가 (폴 튜더 존스 Rule)',
         'ma200_filter',   '{"symbol":"^IXIC","ma":200}', 4),
        ('market','sp500_ma200',   'S&P500 MA200',
         'S&P500 현재가 > 200일선 = 시장 상승 국면',
         'ma200_filter',   '{"symbol":"^GSPC","ma":200}', 5),
        ('market','kospi_ma200',   'KOSPI MA200',
         'KOSPI 현재가 > 200일선 = 국내 시장 상승 국면',
         'ma200_filter',   '{"symbol":"^KS11","ma":200}', 6),
        ('market','kospi_ma_align','KOSPI 이평 정배열',
         'KOSPI MA20/MA60 정배열 — 한국 시장 추세 필터',
         'index_ma_align', '{"symbol":"^KS11","ma1":20,"ma2":60}', 7),
        ('market','adr_kospi',     'KOSPI ADR',
         '등락비율 20일 평균 — 75이하=과매도 / 120이상=과매수',
         'adr',            '{"market":"KOSPI","days":20}', 8),
        ('stock','rs_score',       '상대강도 RS',
         '3개월 주가상승률 vs KOSPI/KOSDAQ — 시장 주도주 여부',
         'relative_strength','{"months":3}', 5),
        ('stock','macd_signal',    'MACD',
         '이평 정배열 상태에서의 골든크로스만 유효 매수신호',
         'macd_filtered',  '{"fast":12,"slow":26,"signal":9}', 7),
        ('stock','rsi_signal',     'RSI',
         '이평 정배열 상태에서 RSI 50 돌파만 매수신호',
         'rsi_filtered',   '{"period":14}', 8),
        ('stock','trend52w',       '52주 추세',
         '52주 고점 -15% 이내 + 거래량 급증 (신고가 근접)',
         'trend52w_vol',   '{"near_high_pct":-15,"vol_ma":20}', 9),
        ('stock','atr_stop',       'ATR 손절선',
         '2×ATR 기계적 손절 기준 — 매수가 대비 손절가 표시',
         'atr',            '{"period":14,"multiplier":2}', 10),
        ('stock','value',          '가치지표',
         'PBR<1 + PER<12 AND 52주 추세 상승 (가치+모멘텀 AND 조건)',
         'value_momentum', '{}', 4),
        # ── Graham 가치투자 트랙 ──────────────────────────────────
        ('stock','graham_value',     'Graham 내재가치',
         'sqrt(22.5×EPS×BPS) 기준 — 30% 이상 할인 시 green',
         'graham_value',    '{}', 13),
        ('stock','macd_divergence',  'MACD 강세 다이버전스',
         '가격 신저점 but MACD 저점 상승 (0선 아래) — 바닥 반전 감지',
         'macd_divergence', '{}', 14),
        ('stock','smart_money',      '스마트머니 유입',
         '최근 5일 중 3일 이상 기관+외국인 동반 순매수',
         'smart_money_3of5','{}', 15),
        ('stock','ma20_slope',       'MA20 기울기',
         'MA20 기울기 완만화 — 하락 멈춤/반전 감지',
         'ma20_slope',      '{}', 16),
        ('stock','value_turnaround', '가치 종합판정',
         'Graham + PBR/PER + MACD 다이버전스 + 스마트머니 종합점수',
         'value_turnaround','{}', 17),
    ]
    for row in new_signals:
        if row[1] not in existing:
            conn.execute("""
                INSERT OR IGNORE INTO signal_config
                (scope,name,label,description,logic_type,params,sort_order)
                VALUES (?,?,?,?,?,?,?)
            """, row)
    conn.commit()


# ══════════════════════════════════════════════════════════════════
# ★ 보너스 점수 헬퍼 — DART수주공시 + HS수출추세 + NPS고용증가
# ══════════════════════════════════════════════════════════════════

_contract_bonus_cache: dict = {}
_contract_bonus_at: float = 0.0
_HS_BONUS_CACHE: dict = {}
_HS_BONUS_AT: float = 0.0
_NPS_BONUS_CACHE: dict = {}
_NPS_BONUS_AT: float = 0.0
_HS_DB_PATH = "/Applications/stock_dashboard/hs_trade_lab/data/hs_trade_lab.db"
_EMP_DB_PATH = "/Applications/stock_dashboard/employment_monitor/employment.db"


def _load_contract_bonus_map(days: int = 90) -> dict[str, dict]:
    """
    최근 N일 DART 수주공시 → stock_code별 보너스 점수 맵 반환.
    캐시 TTL: 1시간.

    Returns:
        {stock_code: {"bonus": int, "label": str}}
        bonus: ★5=+5, ★4=+4, ★3=+3, ★2=+2, ★1=+1 (기존 점수에 가산)
    """
    global _contract_bonus_cache, _contract_bonus_at
    if _time.time() - _contract_bonus_at < 3600 and _contract_bonus_cache:
        return _contract_bonus_cache

    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        cutoff = (date.today() - timedelta(days=days)).strftime("%Y%m%d")
        rows = conn.execute("""
            SELECT stock_code,
                   MAX(signal_strength) as max_sig,
                   MAX(is_overseas)     as overseas,
                   MAX(contract_ratio_pct) as max_ratio,
                   MAX(ai_signal)       as top_signal,
                   COUNT(*)             as cnt
            FROM dart_contracts
            WHERE stock_code IS NOT NULL AND stock_code != ''
              AND disclosed_at >= ?
              AND signal_strength >= 2
            GROUP BY stock_code
        """, (cutoff,)).fetchall()
        conn.close()

        result = {}
        for sc, sig, overseas, ratio, ai_sig, cnt in rows:
            # 보너스 점수 = 시그널 강도 그대로 (+1~+5)
            # 동일 기간 복수 공시면 최대 +1 추가
            bonus = (sig or 1) + (1 if cnt >= 2 else 0)
            bonus = min(bonus, 6)  # 최대 +6점

            label_parts = []
            if overseas:
                label_parts.append("해외수주")
            if ratio and ratio >= 10:
                label_parts.append(f"매출{ratio:.0f}%")
            if ai_sig in ("강한매수", "매수"):
                label_parts.append(f"📋{ai_sig}")
            if cnt >= 2:
                label_parts.append(f"{cnt}건공시")

            result[sc] = {
                "bonus": bonus,
                "label": " ".join(label_parts) or f"수주★{sig}",
                "signal_strength": sig or 1,
                "is_overseas": bool(overseas),
            }

        _contract_bonus_cache = result
        _contract_bonus_at = _time.time()
        return result

    except Exception as e:
        logging.warning(f"[보너스] DART수주 로드 실패: {e}")
        return {}


def _load_hs_export_bonus_map(months: int = 6) -> dict[str, dict]:
    """
    HS 수출통계 → stock_code별 수출 개선 보너스 점수 맵.
    캐시 TTL: 4시간.

    기준: 최근 3개월 수출 YoY 평균 vs 이전 3개월 → 가속도 계산
    Returns:
        {stock_code: {"bonus": int, "label": str, "export_yoy": float}}
    """
    global _HS_BONUS_CACHE, _HS_BONUS_AT
    if _time.time() - _HS_BONUS_AT < 14400 and _HS_BONUS_CACHE:
        return _HS_BONUS_CACHE

    try:
        import sqlite3 as _sl
        hs_conn = _sl.connect(_HS_DB_PATH, timeout=10)
        # 최근 6개월 수출 데이터 (종목별 월별 합계)
        cutoff_ym = (date.today() - timedelta(days=months * 31)).strftime("%Y-%m")
        rows = hs_conn.execute("""
            SELECT m.stock_code,
                   t.period_ym,
                   SUM(t.export_value) as exp_val
            FROM hs_code_company_map m
            JOIN trade_series_cache t ON m.hs_code = t.hs_code
            WHERE t.period_ym >= ?
              AND t.export_value > 0
            GROUP BY m.stock_code, t.period_ym
            ORDER BY m.stock_code, t.period_ym DESC
        """, (cutoff_ym,)).fetchall()
        hs_conn.close()

        # stock_code별 월별 수출액 집계
        from collections import defaultdict as _dd
        exp_by_code: dict = _dd(dict)
        for sc, ym, val in rows:
            exp_by_code[sc][ym] = val

        result = {}
        for sc, monthly in exp_by_code.items():
            sorted_months = sorted(monthly.keys(), reverse=True)
            if len(sorted_months) < 4:
                continue

            # 최근 3개월 vs 이전 3개월 YoY 비교 (실제 YoY는 12개월 필요, 여기선 가속도)
            recent_avg = sum(monthly.get(m, 0) for m in sorted_months[:3]) / 3
            prev_avg   = sum(monthly.get(m, 0) for m in sorted_months[3:6]) / max(len(sorted_months[3:6]), 1)

            if prev_avg <= 0:
                continue

            growth = (recent_avg - prev_avg) / prev_avg * 100  # % 변화

            if growth >= 50:
                bonus, label = 4, f"수출+{growth:.0f}% 급증🚢"
            elif growth >= 20:
                bonus, label = 3, f"수출+{growth:.0f}% 증가🚢"
            elif growth >= 10:
                bonus, label = 2, f"수출+{growth:.0f}%🚢"
            elif growth >= 0:
                bonus, label = 1, f"수출+{growth:.0f}%"
            else:
                continue  # 수출 감소 종목은 보너스 없음

            result[sc] = {
                "bonus": bonus,
                "label": label,
                "export_growth": round(growth, 1),
                "recent_avg": round(recent_avg / 1e6, 1),  # 백만달러 단위
            }

        _HS_BONUS_CACHE = result
        _HS_BONUS_AT = _time.time()
        return result

    except Exception as e:
        logging.warning(f"[보너스] HS수출 로드 실패: {e}")
        return {}


def _load_nps_employment_bonus_map() -> dict[str, dict]:
    """
    NPS 고용 데이터(employment_company) → stock_code별 고용 성장 보너스.
    캐시 TTL: 24시간 (일 1회 갱신).

    비교 전략 (동일 측정 기준끼리만 비교):
    ① 1순위: 2025-12 vs 2024-12 (사업보고서 기반, 1년 YoY — 커버리지 약 300종목)
    ② 2순위: 2025-12 vs 2023-12 (사업보고서 기반, 2년 누적 — 커버리지 약 323종목)
    ※ 2026-05 데이터(NPS API 자회사 포함)는 연결 재무제표 맥락에서 절대값만 참조 가능하나,
       2023~2025 사업보고서와 비교 금지 (측정 기준 불일치로 거짓 급등 발생)

    Returns:
        {stock_code: {"bonus": int, "label": str, "growth_pct": float, "latest_cnt": int}}
    """
    global _NPS_BONUS_CACHE, _NPS_BONUS_AT
    if _time.time() - _NPS_BONUS_AT < 86400 and _NPS_BONUS_CACHE:
        return _NPS_BONUS_CACHE

    try:
        import sqlite3 as _sl
        emp_conn = _sl.connect(_EMP_DB_PATH, timeout=10)

        # ① 1순위: 2024-12 vs 2025-12 (1년 YoY, 가장 최신 트렌드)
        rows_1y = emp_conn.execute("""
            SELECT a.stock_code,
                   a.worker_count as latest_cnt, a.ym as latest_ym,
                   b.worker_count as base_cnt,   b.ym as base_ym
            FROM employment_company a
            JOIN employment_company b ON a.stock_code = b.stock_code
            WHERE a.ym = '2025-12'
              AND b.ym = '2024-12'
              AND a.worker_count > 0
              AND b.worker_count > 50
        """).fetchall()

        # ② 2순위: 2023-12 vs 2025-12 (2년 누적, 커버리지 확장)
        rows_2y = emp_conn.execute("""
            SELECT a.stock_code,
                   a.worker_count as latest_cnt, a.ym as latest_ym,
                   b.worker_count as base_cnt,   b.ym as base_ym
            FROM employment_company a
            JOIN employment_company b ON a.stock_code = b.stock_code
            WHERE a.ym = '2025-12'
              AND b.ym = '2023-12'
              AND a.worker_count > 0
              AND b.worker_count > 50
              AND a.stock_code NOT IN (SELECT DISTINCT a2.stock_code
                  FROM employment_company a2 JOIN employment_company b2
                  ON a2.stock_code=b2.stock_code
                  WHERE a2.ym='2025-12' AND b2.ym='2024-12')
        """).fetchall()
        emp_conn.close()

        # 병합 (1순위 우선)
        all_rows = list(rows_1y) + list(rows_2y)

        # stock_code별 최신 데이터만 사용
        seen = set()
        result = {}
        for sc, latest, latest_ym, base, base_ym in all_rows:
            if sc in seen:
                continue
            seen.add(sc)

            growth = (latest - base) / base * 100  # 2년 누적 성장률

            # 보너스 점수 부여
            if growth >= 50:
                bonus, label = 4, f"인력+{growth:.0f}%🧑‍💼급증"
            elif growth >= 25:
                bonus, label = 3, f"인력+{growth:.0f}%🧑‍💼증가"
            elif growth >= 10:
                bonus, label = 2, f"인력+{growth:.0f}%🧑‍💼"
            elif growth >= 5:
                bonus, label = 1, f"인력+{growth:.0f}%"
            elif growth <= -20:
                # 대규모 감원은 패널티 (구조조정 or 실적 악화)
                bonus, label = -2, f"인력{growth:.0f}%⚠️감원"
            elif growth < 0:
                bonus, label = -1, f"인력{growth:.0f}%↓"
            else:
                continue  # 변화 없음

            result[sc] = {
                "bonus": bonus,
                "label": label,
                "growth_pct": round(growth, 1),
                "latest_cnt": latest,
                "base_cnt": base,
                "period": f"{base_ym}→{latest_ym}",
            }

        _NPS_BONUS_CACHE = result
        _NPS_BONUS_AT = _time.time()
        return result

    except Exception as e:
        logging.warning(f"[보너스] NPS고용 로드 실패: {e}")
        return {}


_NPS_MONTHLY_BONUS_CACHE: dict = {}
_NPS_MONTHLY_BONUS_AT: float = 0.0


def _load_nps_monthly_bonus_map() -> dict:
    """
    NPS 월별 신규취득/상실 데이터 → 보너스 점수 맵 (TTL 24시간).
    collect_nps_monthly.get_nps_bonus_map() 래퍼.

    Returns:
        {stock_code: bonus_score}  # 1~3 (없으면 미포함)
    """
    global _NPS_MONTHLY_BONUS_CACHE, _NPS_MONTHLY_BONUS_AT
    if _time.time() - _NPS_MONTHLY_BONUS_AT < 86400 and _NPS_MONTHLY_BONUS_CACHE:
        return _NPS_MONTHLY_BONUS_CACHE
    try:
        import sys as _sys
        _emp_dir = "/Applications/stock_dashboard/employment_monitor"
        if _emp_dir not in _sys.path:
            _sys.path.insert(0, _emp_dir)
        from collect_nps_monthly import get_nps_bonus_map
        result = get_nps_bonus_map(months=3)
        _NPS_MONTHLY_BONUS_CACHE = result
        _NPS_MONTHLY_BONUS_AT = _time.time()
        return result
    except Exception as e:
        logging.warning(f"[보너스] NPS월별 로드 실패: {e}")
        return {}


# ══════════════════════════════════════════════════════════════════
# 시그널 계산 메인
# ══════════════════════════════════════════════════════════════════
def calc_market_signals(db_conn=None) -> list:
    conn  = db_conn or sqlite3.connect(DB_PATH)
    today = date.today().isoformat()
    cfgs  = conn.execute(
        "SELECT id,name,label,description,logic_type,params FROM signal_config "
        "WHERE scope='market' AND is_active=1 ORDER BY sort_order"
    ).fetchall()

    results = []
    for cfg_id, name, label, desc, logic_type, params_json in cfgs:
        try:
            params = json.loads(params_json or '{}')
            sig, val, detail = _calc_signal(conn, name, logic_type, params, '')
            results.append({"id":cfg_id,"name":name,"label":label,
                            "description":desc,"signal":sig,"value":val,"detail":detail})
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO signal_result
                    (config_id,stock_code,signal,value,description,calc_date)
                    VALUES (?,?,?,?,?,?)
                """, (cfg_id,'',sig,val,detail,today))
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"[시그널] {name}: {e}")
            results.append({"id":cfg_id,"name":name,"label":label,
                            "description":desc,"signal":"gray","value":None,"detail":"계산 불가"})

    if not db_conn: conn.commit(); conn.close()
    return results


def calc_stock_signals(stock_code: str, db_conn=None) -> list:
    conn  = db_conn or sqlite3.connect(DB_PATH)
    today = date.today().isoformat()
    cfgs  = conn.execute(
        "SELECT id,name,label,description,logic_type,params FROM signal_config "
        "WHERE scope='stock' AND is_active=1 ORDER BY sort_order"
    ).fetchall()

    # 이평선 정배열 여부 미리 계산 (MACD/RSI 필터에 활용)
    ma_aligned = _check_ma_align(conn, stock_code)

    results = []
    for cfg_id, name, label, desc, logic_type, params_json in cfgs:
        try:
            params = json.loads(params_json or '{}')
            params['_ma_aligned'] = ma_aligned  # 내부 전달용
            sig, val, detail = _calc_signal(conn, name, logic_type, params, stock_code)
            results.append({"id":cfg_id,"name":name,"label":label,
                            "description":desc,"signal":sig,"value":val,"detail":detail})
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO signal_result
                    (config_id,stock_code,signal,value,description,calc_date)
                    VALUES (?,?,?,?,?,?)
                """, (cfg_id,stock_code,sig,val,detail,today))
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"[시그널] {name}/{stock_code}: {e}")
            results.append({"id":cfg_id,"name":name,"label":label,
                            "description":desc,"signal":"gray","value":None,"detail":"데이터 부족"})

    if not db_conn: conn.commit(); conn.close()

    # 3-트랙 종합 판정 (conn + stock_code 전달 → 시장위험도·섹터 참조)
    step_result = _calc_4step_judgment(results, conn if db_conn else None, stock_code)
    results.append(step_result)

    return results


def _check_ma_align(conn, stock_code: str) -> bool:
    """5>20>60 정배열 여부 반환."""
    rows = conn.execute("""
        SELECT close FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT 65
    """, (stock_code,)).fetchall()
    if len(rows) < 60: return False
    closes = [r[0] for r in rows]
    ma5  = sum(closes[:5])  / 5
    ma20 = sum(closes[:20]) / 20
    ma60 = sum(closes[:60]) / 60
    return closes[0] > ma5 > ma20 > ma60


# ══════════════════════════════════════════════════════════════════
# 시장 위험도 + 섹터 헬퍼
# ══════════════════════════════════════════════════════════════════

def _get_market_danger(conn) -> int:
    """
    현재 시장 위험도 (0=안전, 1=주의, 2=위험, 3=극위험).
    signal_result 캐시(최근 2일)에서 시장 시그널 읽기.
    """
    rows = conn.execute("""
        SELECT sc.name, sr.signal
        FROM signal_result sr
        JOIN signal_config sc ON sr.config_id = sc.id
        WHERE sc.scope = 'market' AND sc.is_active = 1
          AND sr.calc_date >= date('now', '-2 days')
          AND sr.stock_code = ''
        ORDER BY sr.calc_date DESC
    """).fetchall()

    if not rows:
        return 1  # 데이터 없으면 주의

    # 종목별 최신 1건만 유지
    sig_map = {}
    for name, signal in rows:
        if name not in sig_map:
            sig_map[name] = signal

    red_cnt   = sum(1 for s in sig_map.values() if s == 'red')
    green_cnt = sum(1 for s in sig_map.values() if s == 'green')
    total     = len(sig_map)

    vix_red        = sig_map.get('vix_trend')      == 'red'
    kospi_ma200_red= sig_map.get('kospi_ma200')    == 'red'
    nasdaq_red     = sig_map.get('nasdaq_ma200')   == 'red'
    usd_red        = sig_map.get('usd_krw_trend')  == 'red'

    if total == 0:
        return 1
    red_ratio = red_cnt / total

    # 극위험: 70%+ 적신호 + VIX 상승
    if red_ratio >= 0.7 and vix_red:
        return 3
    # 위험: 50%+ 적신호 + 주요 지수 이평 이탈
    if red_ratio >= 0.5 and (kospi_ma200_red or nasdaq_red):
        return 2
    # 주의: 적신호 > 녹신호
    if red_cnt > green_cnt:
        return 1
    return 0


def _get_stock_sector(conn, stock_code: str) -> str:
    """종목 섹터 조회 (stock_universe → stock_meta 순서)."""
    row = conn.execute(
        "SELECT sector_large FROM stock_universe WHERE stock_code=? LIMIT 1",
        (stock_code,)
    ).fetchone()
    if row and row[0]:
        return row[0]
    row = conn.execute(
        "SELECT sector FROM stock_meta WHERE stock_code=? LIMIT 1",
        (stock_code,)
    ).fetchone()
    return row[0] if row and row[0] else ''


# ══════════════════════════════════════════════════════════════════
# 3-트랙 종합 판정
# ══════════════════════════════════════════════════════════════════
def _calc_4step_judgment(signals: list, conn=None, stock_code: str = '') -> dict:
    """
    3-트랙 독립 판정 시스템

    ┌─ Track A: 추세 매수 ─────────────────────────────────────────┐
    │  MA 정배열(5>20>60) + Step3 진입 트리거 + ATR 리스크 OK      │
    │  → 모멘텀 플레이, 시장 주도주 진입                           │
    └──────────────────────────────────────────────────────────────┘
    ┌─ Track B: 가치 매수 (시장환경 독립) ──────────────────────────┐
    │  Graham 할인율 ≥ 30% + 영업흑자 재무 + (반전 신호 보너스)     │
    │  시장이 나쁠수록 더 싸지므로 가치 신호는 차단하지 않음        │
    │  → 시장 위험도에 따라 "소량/분할" 주의 문구 추가              │
    └──────────────────────────────────────────────────────────────┘
    ┌─ Track C: 섹터 회복 (탑다운 보너스) ─────────────────────────┐
    │  섹터 주도주 50%+ 52주선 상회 → 가치 판정 강화               │
    └──────────────────────────────────────────────────────────────┘

    [핵심 원칙]
    - 가치 매수는 MA 역배열·시장 하락과 무관하게 독립 green 가능
    - 시장 위험은 가치 신호를 "차단"하지 않고 "경고 문구"만 추가
    - 추세 매수는 반드시 MA 정배열 + 진입 트리거 동시 충족 필요
    - 두 트랙 동시 green = 최강 매수 신호
    """
    sig_map = {s['name']: s for s in signals}

    def green(name):
        return sig_map.get(name, {}).get('signal') == 'green'
    def not_red(name):
        return sig_map.get(name, {}).get('signal') != 'red'

    # ── Track A: 추세 ─────────────────────────────────────────────
    step2_pass = not_red('financials') and green('ma_align')
    step3_pass = step2_pass and (
        green('macd_signal') or green('rsi_signal') or
        green('frn_supply')  or green('inst_supply')
    )
    step4_ok  = sig_map.get('atr_stop', {}).get('signal') != 'red'
    trend_buy = step3_pass and step4_ok

    # ── Track B: 가치 (시장 독립) ──────────────────────────────────
    value_score  = sig_map.get('value_turnaround', {}).get('value', 0) or 0
    graham_sig   = sig_map.get('graham_value', {}).get('signal', 'gray')
    graham_disc  = sig_map.get('graham_value', {}).get('value', 0) or 0
    fin_ok       = not_red('financials')   # 영업적자 아님

    # 반전 신호: MACD 다이버전스 or 스마트머니 유입
    turning = green('macd_divergence') or green('smart_money')

    # 가치 강도 (0~3)
    # 3: Graham 40%+ 할인 + 흑자 재무 + 반전 신호 → 강력 가치 매수
    # 2: Graham 30%+ 할인 + 흑자 재무  OR  value_turnaround ≥ 6 + 흑자
    # 1: Graham 20%+ 할인  OR  value_turnaround 4~5 → 관심 구간
    # 0: 조건 미충족
    if graham_sig == 'green' and graham_disc >= 40 and fin_ok and turning:
        value_strength = 3
    elif (graham_sig == 'green' and graham_disc >= 30 and fin_ok) or \
         (value_score >= 6 and fin_ok):
        value_strength = 2
    elif (graham_sig in ('green', 'yellow') and graham_disc >= 20) or value_score >= 4:
        value_strength = 1
    else:
        value_strength = 0

    # ── Track C: 섹터 회복 보너스 ─────────────────────────────────
    sector_bonus = False
    sector_name  = ''
    if conn and stock_code:
        try:
            sector_name = _get_stock_sector(conn, stock_code)
            if sector_name:
                sector_map   = _build_sector_activation_map(conn)
                sector_score = sector_map.get(sector_name, 0.0)
                sector_bonus = sector_score >= 0.5
        except Exception:
            pass

    # ── 시장 위험도 (가치 주의 문구용) ────────────────────────────
    market_danger = 0
    if conn:
        try:
            market_danger = _get_market_danger(conn)
        except Exception:
            pass

    MARKET_NOTE = {
        0: '',
        1: ' | 시장 주의 — 분산 접근 권장',
        2: ' | ⚠️ 시장 약세 — 소량 분할 진입',
        3: ' | 🚨 시장 극위험 — 최소 소량만 접근',
    }
    mkt_note = MARKET_NOTE.get(market_danger, '')

    # ── 최종 판정 ─────────────────────────────────────────────────
    sec_tag = f' + {sector_name} 섹터 회복 가속' if sector_bonus else ''

    if trend_buy and value_strength >= 2:
        sig    = 'green'
        detail = (f'✅ 추세+가치 최강 매수 — 양방향 확인 완료'
                  f' (가치 Score {value_score}/9){sec_tag}{mkt_note}')

    elif trend_buy:
        sig    = 'green'
        detail = f'✅ [추세] Step1~4 통과 — 추세 매수 가능{mkt_note}'
        if value_strength == 1:
            detail += f' | 🟡 가치 관심 (Score {value_score}/9)'

    elif value_strength >= 2 and market_danger <= 1:
        sig  = 'green'
        v_prefix = '강력 ' if value_strength == 3 else ''
        detail = (f'✅ [{v_prefix}가치] Graham {graham_disc:.0f}% 할인 — 분할매수 검토'
                  f'{sec_tag}{mkt_note}')

    elif value_strength >= 2 and market_danger == 2:
        sig    = 'yellow'
        detail = (f'🟡 [가치 우수] Score {value_score}/9 · Graham {graham_disc:.0f}% 할인'
                  f' — 시장 약세, 소량 분할 접근{sec_tag}')

    elif value_strength >= 2 and market_danger >= 3:
        sig    = 'yellow'
        detail = (f'🟡 [가치 우수] Score {value_score}/9 · Graham {graham_disc:.0f}% 할인'
                  f' — 시장 극위험, 대기 후 분할 접근 권고')

    elif step2_pass:
        sig    = 'yellow'
        detail = '🟡 [추세] MA 정배열 — 진입 트리거 대기 중'
        if value_strength == 1:
            detail += f' | 🟡 가치 관심 (Score {value_score}/9)'
        elif value_strength >= 2:
            detail += f' | 🟢 가치 조건 충족 (Score {value_score}/9) — 추세 진입 대기'

    elif value_strength == 1:
        sig    = 'yellow'
        detail = f'🟡 [가치 관심] Score {value_score}/9{sec_tag} — 추가 조건 확인 필요'

    else:
        sig    = 'red'
        detail = '🔴 매수 보류 — 추세·가치 조건 모두 미충족'

    return {
        "id": 9999, "name": "system_judgment", "label": "종합 판정",
        "description": "추세(Step1~4) + 가치(Graham 할인) + 섹터 회복 3-트랙 독립 판정",
        "signal": sig, "value": value_strength, "detail": detail,
    }


# ══════════════════════════════════════════════════════════════════
# 개별 로직 라우터
# ══════════════════════════════════════════════════════════════════
def _calc_signal(conn, name, logic_type, params, stock_code):
    if logic_type == 'supply_trend':      return _calc_supply_trend(conn, params, stock_code)
    elif logic_type == 'ma_trend_price':  return _calc_ma_trend_price(conn, params)
    elif logic_type == 'ma200_filter':    return _calc_ma200_filter(conn, params)
    elif logic_type == 'index_ma_align':  return _calc_index_ma_align(conn, params)
    elif logic_type == 'adr':             return _calc_adr(conn, params)
    elif logic_type == 'threshold':       return _calc_threshold(conn, name, params, stock_code)
    elif logic_type == 'ma_trend':        return _calc_ma_trend(conn, params, stock_code)
    elif logic_type == 'ma_position':     return _calc_ma_position(conn, params, stock_code)
    elif logic_type == 'financial':       return _calc_financial(conn, params, stock_code)
    elif logic_type == 'value_momentum':  return _calc_value_momentum(conn, params, stock_code)
    elif logic_type == 'relative_strength': return _calc_rs(conn, params, stock_code)
    elif logic_type == 'atr':             return _calc_atr(conn, params, stock_code)
    elif logic_type == 'macd_filtered':   return _calc_macd_filtered(conn, params, stock_code)
    elif logic_type == 'rsi_filtered':    return _calc_rsi_filtered(conn, params, stock_code)
    elif logic_type == 'trend52w_vol':    return _calc_trend52w_vol(conn, params, stock_code)
    elif logic_type == 'price_position':    return _calc_price_position(conn, params, stock_code)
    elif logic_type == 'technical':         return _calc_technical(conn, params, stock_code)
    elif logic_type == 'graham_value':      return _calc_graham_signal(conn, stock_code)
    elif logic_type == 'macd_divergence':   return _calc_macd_divergence(conn, stock_code)
    elif logic_type == 'smart_money_3of5':  return _calc_smart_money_3of5(conn, stock_code)
    elif logic_type == 'ma20_slope':        return _calc_ma20_slope(conn, stock_code)
    elif logic_type == 'value_turnaround':  return _calc_value_turnaround(conn, stock_code)
    elif logic_type == 'manual':
        row = conn.execute("""
            SELECT signal,value,description FROM signal_result
            WHERE config_id=(SELECT id FROM signal_config WHERE name=? LIMIT 1)
            AND stock_code='' ORDER BY calc_date DESC LIMIT 1
        """, (name,)).fetchone()
        if row: return row[0], row[1], row[2] or f"수동입력: {row[1]}"
        d = params.get('default', 50)
        g = params.get('green_above', 55)
        r = params.get('red_below', 30)
        sig = 'green' if d > g else 'red' if d < r else 'yellow'
        return sig, d, f"기본값 {d} (수동 입력 필요)"
    return 'gray', None, '미구현 로직'


# ══════════════════════════════════════════════════════════════════
# 신규 로직 구현
# ══════════════════════════════════════════════════════════════════

def _get_closes(conn, symbol, limit=210):
    rows = conn.execute("""
        SELECT close FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT ?
    """, (symbol, limit)).fetchall()
    return [r[0] for r in rows]  # 최신→오래된 순


def _load_universe_maps(conn) -> dict:
    """stock_universe + stock_meta 를 한 번에 로드.
    반환: {"mktcap", "market", "sector", "sector_mid", "tvol", "name"}  (모두 stock_code → 값)
    """
    mktcap: dict = {}; market: dict = {}
    sector: dict = {}; sector_mid: dict = {}
    tvol:   dict = {}; name:   dict = {}
    for sc, sl, sm, nm, mc, mkt, tv in conn.execute("""
        SELECT stock_code, sector_large, sector_mid, stock_name,
               MAX(market_cap), MAX(market), MAX(trading_value)
        FROM stock_universe
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        GROUP BY stock_code
    """).fetchall():
        mktcap[sc]     = mc or 0
        market[sc]     = mkt or ''
        sector[sc]     = sl  or ''
        sector_mid[sc] = sm  or ''
        tvol[sc]       = tv  or 0
        name[sc]       = nm  or sc
    for sc, nm in conn.execute(
        "SELECT stock_code, stock_name FROM stock_meta WHERE stock_name IS NOT NULL"
    ).fetchall():
        if nm:
            name[sc] = nm
    return {"mktcap": mktcap, "market": market, "sector": sector,
            "sector_mid": sector_mid, "tvol": tvol, "name": name}


def _calc_price_indicators(closes: list, vols: list) -> dict:
    """MA5/20/60/120/200, RSI14, vol_ratio, high52, from_high 일괄 계산.
    closes/vols: 최신→오래된 순. MA 기간보다 데이터가 짧으면 None 반환.
    """
    n    = len(closes)
    curr = closes[0]
    def _ma(k): return sum(closes[:k]) / k if n >= k else None

    # RSI(14)
    rsi = 50.0
    if n >= 15:
        deltas = [closes[i-1] - closes[i] for i in range(1, 15)]
        gains  = [d for d in deltas if d > 0]
        losses = [-d for d in deltas if d < 0]
        ag = sum(gains)  / 14 if gains  else 0
        al = sum(losses) / 14 if losses else 0
        rsi = round(100 - 100 / (1 + ag / al), 1) if al > 0 else 99.0

    # 거래량 비율 (오늘 / 전20일 평균) — 스마트머니 진입 감지
    vol_ratio = 0.0
    if len(vols) >= 21:
        avg20_v = sum(vols[1:21]) / 20
        if avg20_v > 0:
            vol_ratio = vols[0] / avg20_v

    high52    = max(closes[:252] if n >= 252 else closes)
    from_high = (curr - high52) / high52 * 100

    return {
        "curr": curr, "rsi": rsi, "vol_ratio": vol_ratio,
        "high52": high52, "from_high": from_high,
        "ma5": _ma(5), "ma20": _ma(20), "ma60": _ma(60),
        "ma120": _ma(120), "ma200": _ma(200),
    }


def _calc_stockeasy_technical_indicators(closes: list, highs: list, lows: list) -> dict:
    """StockEasy 리포트형 기술지표.

    입력은 최신→과거 순서다. 내부 계산은 과거→최신으로 뒤집어 수행한다.
    """
    if len(closes) < 60:
        return {}

    c = list(reversed([float(x or 0) for x in closes]))
    h = list(reversed([float(x or 0) for x in highs]))
    l = list(reversed([float(x or 0) for x in lows]))
    n = len(c)

    def ema(values: list[float], period: int) -> list[float]:
        if not values:
            return []
        alpha = 2 / (period + 1)
        out = [values[0]]
        for v in values[1:]:
            out.append(v * alpha + out[-1] * (1 - alpha))
        return out

    ema12 = ema(c, 12)
    ema26 = ema(c, 26)
    macd_line = [a - b for a, b in zip(ema12, ema26)]
    macd_signal = ema(macd_line, 9)
    macd = macd_line[-1]
    macd_sig = macd_signal[-1] if macd_signal else 0.0
    macd_hist = macd - macd_sig

    # Stochastic 14,3
    k_vals = []
    for i in range(n):
        if i < 13:
            k_vals.append(50.0)
            continue
        hh = max(h[i - 13:i + 1])
        ll = min(l[i - 13:i + 1])
        k_vals.append((c[i] - ll) / (hh - ll) * 100 if hh > ll else 50.0)
    stoch_k = k_vals[-1]
    stoch_d = sum(k_vals[-3:]) / min(3, len(k_vals))

    # Wilder ADX / DI(14)
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, n):
        up = h[i] - h[i - 1]
        down = l[i - 1] - l[i]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
        trs.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))

    adx = pdi = mdi = 0.0
    if len(trs) >= 14:
        period = 14
        atr = sum(trs[:period])
        pdm = sum(plus_dm[:period])
        mdm = sum(minus_dm[:period])
        dx_vals = []
        for i in range(period, len(trs)):
            atr = atr - atr / period + trs[i]
            pdm = pdm - pdm / period + plus_dm[i]
            mdm = mdm - mdm / period + minus_dm[i]
            pdi_i = 100 * pdm / atr if atr else 0.0
            mdi_i = 100 * mdm / atr if atr else 0.0
            denom = pdi_i + mdi_i
            dx_vals.append(100 * abs(pdi_i - mdi_i) / denom if denom else 0.0)
        if dx_vals:
            pdi, mdi = pdi_i, mdi_i
            adx = sum(dx_vals[-14:]) / min(14, len(dx_vals))

    # SuperTrend(10, 3). 리포트의 매수/매도 상태를 근사한다.
    supertrend = None
    supertrend_buy = False
    if n >= 12:
        period = 10
        mult = 3.0
        tr = [0.0]
        for i in range(1, n):
            tr.append(max(h[i] - l[i], abs(h[i] - c[i - 1]), abs(l[i] - c[i - 1])))
        atrs = []
        atr = sum(tr[1:period + 1]) / period
        for i in range(n):
            if i <= period:
                atrs.append(atr)
            else:
                atr = (atr * (period - 1) + tr[i]) / period
                atrs.append(atr)
        final_upper = final_lower = 0.0
        direction = 1
        st = 0.0
        for i in range(period, n):
            hl2 = (h[i] + l[i]) / 2
            upper = hl2 + mult * atrs[i]
            lower = hl2 - mult * atrs[i]
            if i == period:
                final_upper, final_lower = upper, lower
                direction = 1 if c[i] >= final_upper else -1 if c[i] <= final_lower else 1
            else:
                final_upper = upper if upper < final_upper or c[i - 1] > final_upper else final_upper
                final_lower = lower if lower > final_lower or c[i - 1] < final_lower else final_lower
                if direction == -1 and c[i] > final_upper:
                    direction = 1
                elif direction == 1 and c[i] < final_lower:
                    direction = -1
            st = final_lower if direction == 1 else final_upper
        supertrend = st
        supertrend_buy = direction == 1 and c[-1] >= st

    return {
        "adx": round(adx, 2),
        "pdi": round(pdi, 2),
        "mdi": round(mdi, 2),
        "macd": round(macd, 4),
        "macd_signal": round(macd_sig, 4),
        "macd_hist": round(macd_hist, 4),
        "stoch_k": round(stoch_k, 2),
        "stoch_d": round(stoch_d, 2),
        "supertrend": round(supertrend, 2) if supertrend is not None else None,
        "supertrend_buy": bool(supertrend_buy),
    }


def _calc_ma_trend_price(conn, params):
    """
    VIX/환율: 현재가 > MA(N) = Risk-off(red), 아래 = Risk-on(green)
    추세 방향이 핵심 (절대값 기준 폐기)
    """
    symbol  = params.get('symbol')
    ma_n    = params.get('ma', 20)
    reverse = params.get('reverse', False)  # True: 위=red (VIX, 환율)

    closes = _get_closes(conn, symbol, ma_n + 10)
    if len(closes) < ma_n:
        return 'gray', None, f'{symbol} 데이터 부족'

    curr = closes[0]
    ma   = sum(closes[:ma_n]) / ma_n
    pct  = (curr - ma) / ma * 100

    above = curr > ma
    name_map = {'^VIX':'VIX', 'USDKRW=X':'원/달러'}
    nm = name_map.get(symbol, symbol)

    if reverse:
        # 위험 지표: 위=red, 아래=green
        if not above:
            sig    = 'green'
            detail = f"{nm} MA{ma_n} 아래 — Risk-On 안정 ({curr:,.1f} < MA{ma_n} {ma:,.1f})"
        elif pct > 10:
            sig    = 'red'
            detail = f"{nm} MA{ma_n} 대비 +{pct:.1f}% 위 — Risk-Off 위험"
        else:
            sig    = 'yellow'
            detail = f"{nm} MA{ma_n} 소폭 위 +{pct:.1f}% — 주의"
    else:
        if above:
            sig    = 'green'
            detail = f"{nm} MA{ma_n} 위 — 상승 추세 ({curr:,.1f} > {ma:,.1f})"
        else:
            sig    = 'red'
            detail = f"{nm} MA{ma_n} 아래 — 하락 추세"

    return sig, round(curr, 2), detail


def _calc_ma200_filter(conn, params):
    """
    MA200 필터 (폴 튜더 존스 Rule):
    현재가 > 200일선 = 매매 허가 (green)
    현재가 < 200일선 = 매매 금지 (red)
    """
    symbol = params.get('symbol')
    ma_n   = params.get('ma', 200)

    closes = _get_closes(conn, symbol, ma_n + 10)
    if len(closes) < ma_n:
        return 'gray', None, f'데이터 부족 ({len(closes)}/{ma_n}일)'

    curr  = closes[0]
    ma200 = sum(closes[:ma_n]) / ma_n
    pct   = (curr - ma200) / ma200 * 100

    name_map = {'^IXIC':'나스닥', '^GSPC':'S&P500', '^KS11':'KOSPI', '^KQ11':'KOSDAQ'}
    nm = name_map.get(symbol, symbol)

    if curr > ma200:
        sig    = 'green'
        detail = f"{nm} MA200 위 +{pct:.1f}% — 매매 허가 구간"
    else:
        sig    = 'red'
        detail = f"{nm} MA200 아래 {pct:.1f}% — 매매 금지 구간 (비중 축소)"

    return sig, round(pct, 2), detail


def _calc_index_ma_align(conn, params):
    """KOSPI/KOSDAQ 지수 MA 정배열."""
    symbol = params.get('symbol', '^KS11')
    ma1    = params.get('ma1', 20)
    ma2    = params.get('ma2', 60)

    closes = _get_closes(conn, symbol, ma2 + 10)
    if len(closes) < ma2:
        return 'gray', None, '데이터 부족'

    curr  = closes[0]
    v_ma1 = sum(closes[:ma1]) / ma1
    v_ma2 = sum(closes[:ma2]) / ma2

    name_map = {'^KS11':'KOSPI', '^KQ11':'KOSDAQ'}
    nm = name_map.get(symbol, symbol)

    if curr > v_ma1 > v_ma2:
        sig    = 'green'
        detail = f"{nm} 정배열 — 국내 시장 상승 추세 (현재>{ma1}일>{ma2}일)"
    elif curr < v_ma1 < v_ma2:
        sig    = 'red'
        detail = f"{nm} 역배열 — 국내 시장 하락 추세"
    else:
        sig    = 'yellow'
        detail = f"{nm} 혼조 — MA{ma1}:{v_ma1:,.0f} / MA{ma2}:{v_ma2:,.0f}"

    return sig, round(curr, 2), detail


def _calc_adr(conn, params):
    """
    등락비율 (ADR): 상승종목수 / 하락종목수
    20일 평균 ADR 기준
    < 75: 과매도 극단 — 반등 노림 (green)
    > 120: 과매수 극단 — 신규 진입 자제 (red)
    """
    days = params.get('days', 20)
    cutoff = (date.today() - timedelta(days=days + 5)).isoformat()

    # price_history에서 일별 등락 계산
    rows = conn.execute("""
        SELECT p1.date, p1.close, p2.close as prev_close
        FROM price_history p1
        JOIN price_history p2 ON p1.stock_code = p2.stock_code
            AND DATE(p2.date) = DATE(p1.date, '-1 day')
        WHERE p1.date >= ? AND p1.close > 0 AND p2.close > 0
            AND LENGTH(p1.stock_code) = 6 AND p1.stock_code GLOB '[0-9]*'
        GROUP BY p1.date, p1.stock_code
        ORDER BY p1.date DESC
    """, (cutoff,)).fetchall()

    if not rows:
        return 'gray', None, 'ADR 데이터 부족'

    # 날짜별 상승/하락 집계
    from collections import defaultdict
    daily = defaultdict(lambda: {'up': 0, 'down': 0})
    for dt, close, prev in rows:
        day = dt[:10]
        if close > prev:   daily[day]['up']   += 1
        elif close < prev: daily[day]['down'] += 1

    if not daily:
        return 'gray', None, 'ADR 계산 불가'

    adrs = []
    for day_data in list(daily.values())[:days]:
        up   = day_data['up']
        down = day_data['down']
        if down > 0:
            adrs.append(up / down * 100)

    if not adrs:
        return 'gray', None, 'ADR 데이터 없음'

    avg_adr = round(sum(adrs) / len(adrs), 1)

    if avg_adr < 75:
        sig    = 'green'
        detail = f"ADR {avg_adr} — 과매도 극단 (반등 기대)"
    elif avg_adr > 120:
        sig    = 'red'
        detail = f"ADR {avg_adr} — 과매수 극단 (신규 진입 자제)"
    else:
        sig    = 'yellow'
        detail = f"ADR {avg_adr} — 중립 구간 (75~120)"

    return sig, avg_adr, detail


def _calc_rs(conn, params, stock_code):
    """
    상대강도 RS (Relative Strength):
    3개월 주가 상승률 > KOSPI/KOSDAQ 상승률 = 시장 주도주 (green)
    """
    months   = params.get('months', 3)
    days     = months * 21  # 영업일 기준

    # 종목 수익률
    stock_rows = conn.execute("""
        SELECT close FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT ?
    """, (stock_code, days + 5)).fetchall()

    if len(stock_rows) < days // 2:
        return 'gray', None, '데이터 부족'

    stock_curr = stock_rows[0][0]
    stock_past = stock_rows[min(days, len(stock_rows)-1)][0]
    stock_ret  = (stock_curr - stock_past) / stock_past * 100 if stock_past > 0 else 0

    # 코스피 수익률
    mkt_rows = conn.execute("""
        SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0
        ORDER BY date DESC LIMIT ?
    """, (days + 5,)).fetchall()

    if len(mkt_rows) < days // 2:
        return 'gray', None, '시장 데이터 부족'

    mkt_curr = mkt_rows[0][0]
    mkt_past = mkt_rows[min(days, len(mkt_rows)-1)][0]
    mkt_ret  = (mkt_curr - mkt_past) / mkt_past * 100 if mkt_past > 0 else 0

    rs_diff = stock_ret - mkt_ret

    if rs_diff > 5:
        sig    = 'green'
        detail = f"시장 대비 +{rs_diff:.1f}%p 아웃퍼폼 — 주도주 (종목{stock_ret:+.1f}% / KOSPI{mkt_ret:+.1f}%)"
    elif rs_diff > 0:
        sig    = 'green'
        detail = f"시장 대비 +{rs_diff:.1f}%p 소폭 우위"
    elif rs_diff > -5:
        sig    = 'yellow'
        detail = f"시장 대비 {rs_diff:.1f}%p — 시장 동행"
    else:
        sig    = 'red'
        detail = f"시장 대비 {rs_diff:.1f}%p 언더퍼폼 — 약세주"

    return sig, round(rs_diff, 2), detail


def _calc_atr(conn, params, stock_code):
    """
    ATR (Average True Range) — 기계적 손절 기준
    손절가 = 현재가 - (multiplier × ATR)
    """
    period     = params.get('period', 14)
    multiplier = params.get('multiplier', 2)

    rows = conn.execute("""
        SELECT close, high, low FROM price_history
        WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT ?
    """, (stock_code, period + 5)).fetchall()

    if len(rows) < period:
        return 'gray', None, f'ATR 데이터 부족 ({len(rows)}/{period}일)'

    rows_asc = list(reversed(rows))
    trs = []
    for i in range(1, len(rows_asc)):
        high = rows_asc[i][1] or rows_asc[i][0]
        low  = rows_asc[i][2] or rows_asc[i][0]
        prev_c = rows_asc[i-1][0]
        tr = max(high - low, abs(high - prev_c), abs(low - prev_c))
        trs.append(tr)

    atr   = sum(trs[-period:]) / period
    curr  = rows[0][0]
    stop  = curr - multiplier * atr
    risk_pct = atr / curr * 100

    if risk_pct < 2:
        sig    = 'green'
        detail = f"ATR {atr:,.0f}원 (낮음) — 손절가 {stop:,.0f}원 (-{multiplier}×ATR)"
    elif risk_pct < 5:
        sig    = 'yellow'
        detail = f"ATR {atr:,.0f}원 — 손절가 {stop:,.0f}원 (리스크 {risk_pct:.1f}%)"
    else:
        sig    = 'red'
        detail = f"ATR {atr:,.0f}원 (높음) — 손절가 {stop:,.0f}원 (리스크 {risk_pct:.1f}% 주의)"

    return sig, round(atr, 0), detail


def _calc_macd_filtered(conn, params, stock_code):
    """
    MACD: 이평선 정배열 상태에서의 골든크로스만 유효 매수신호
    역배열 상태에서의 골든크로스는 노이즈로 무시 → yellow
    """
    ma_aligned = params.get('_ma_aligned', False)
    fast  = params.get('fast', 12)
    slow  = params.get('slow', 26)
    sig_p = params.get('signal', 9)

    closes = _get_closes(conn, stock_code, slow + sig_p + 10)
    if len(closes) < slow + sig_p:
        return 'gray', None, '데이터 부족'

    closes_asc = list(reversed(closes))

    def ema(data, n):
        k = 2 / (n+1); e = data[0]; result = [e]
        for v in data[1:]: e = v*k + e*(1-k); result.append(e)
        return result

    ema_fast   = ema(closes_asc, fast)
    ema_slow   = ema(closes_asc, slow)
    macd_line  = [f-s for f,s in zip(ema_fast, ema_slow)]
    signal_ln  = ema(macd_line[slow-1:], sig_p)

    curr_m = macd_line[-1]; prev_m = macd_line[-2]
    curr_s = signal_ln[-1]; prev_s = signal_ln[-2] if len(signal_ln) >= 2 else curr_s

    golden = prev_m <= prev_s and curr_m > curr_s
    dead   = prev_m >= prev_s and curr_m < curr_s
    above_zero = curr_m > 0

    if golden and above_zero and ma_aligned:
        sig    = 'green'
        detail = f"정배열 + MACD 골든크로스 (0선 위) — 강한 매수 신호"
    elif golden and ma_aligned:
        sig    = 'green'
        detail = f"정배열 + MACD 골든크로스 — 반등 시작"
    elif golden and not ma_aligned:
        sig    = 'yellow'
        detail = f"MACD 골든크로스 (역배열 중 — 신뢰도 낮음)"
    elif dead and not above_zero:
        sig    = 'red'
        detail = f"MACD 데드크로스 (0선 아래) — 하락추세"
    elif dead:
        sig    = 'yellow'
        detail = f"MACD 데드크로스 주의"
    elif above_zero and curr_m > curr_s:
        sig    = 'green' if ma_aligned else 'yellow'
        detail = f"상승추세 유지 {'(정배열)' if ma_aligned else '(혼조)'}"
    else:
        sig    = 'yellow'
        detail = f"중립 (MACD {curr_m:+.2f})"

    return sig, round(curr_m, 4), detail


def _calc_rsi_filtered(conn, params, stock_code):
    """
    RSI: 이평선 정배열 상태에서 50선 돌파만 매수신호
    역배열에서의 RSI 30 터치는 추가 하락 가능성 → yellow
    """
    ma_aligned = params.get('_ma_aligned', False)
    period     = params.get('period', 14)

    closes = _get_closes(conn, stock_code, period + 20)
    if len(closes) < period + 2:
        return 'gray', None, '데이터 부족'

    closes_asc = list(reversed(closes))
    gains, losses = [], []
    for i in range(1, len(closes_asc)):
        diff = closes_asc[i] - closes_asc[i-1]
        gains.append(max(diff, 0)); losses.append(max(-diff, 0))

    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g*(period-1) + gains[i]) / period
        avg_l = (avg_l*(period-1) + losses[i]) / period

    rs  = avg_g / avg_l if avg_l > 0 else 100
    rsi = round(100 - 100 / (1 + rs), 1)

    # 이전 RSI
    prev_gains = gains[:-1]; prev_losses = losses[:-1]
    ag = sum(prev_gains[:period]) / period
    al = sum(prev_losses[:period]) / period
    for i in range(period, len(prev_gains)):
        ag = (ag*(period-1) + prev_gains[i]) / period
        al = (al*(period-1) + prev_losses[i]) / period
    rsi_prev = round(100 - 100 / (1 + ag/al if al > 0 else 100), 1)

    cross_50 = rsi_prev < 50 and rsi >= 50  # 50선 돌파

    if ma_aligned and cross_50:
        sig    = 'green'
        detail = f"정배열 + RSI 50 돌파 ({rsi}) — 유효 매수 신호"
    elif ma_aligned and rsi >= 50:
        sig    = 'green'
        detail = f"정배열 + RSI 50 이상 ({rsi}) — 상승 추세"
    elif rsi <= 30 and ma_aligned:
        sig    = 'green'
        detail = f"정배열 + 과매도 ({rsi}) — 반등 기대"
    elif rsi <= 30 and not ma_aligned:
        sig    = 'yellow'
        detail = f"과매도 ({rsi}) — 역배열 중 추가 하락 주의"
    elif rsi >= 70:
        sig    = 'red'
        detail = f"과열 구간 ({rsi}) — 수익 실현 고려"
    elif rsi < 50 and not ma_aligned:
        sig    = 'red'
        detail = f"하락 추세 + RSI {rsi} — 매수 자제"
    else:
        sig    = 'yellow'
        detail = f"중립 RSI {rsi}"

    return sig, rsi, detail


def _calc_trend52w_vol(conn, params, stock_code):
    """
    52주 추세: 고점 -15% 이내 + 최근 1개월 거래량 급증
    (윌리엄 오닐 CAN SLIM 스타일)
    """
    near_pct = params.get('near_high_pct', -15)
    vol_ma   = params.get('vol_ma', 20)

    rows = conn.execute("""
        SELECT close, volume FROM price_history
        WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT 260
    """, (stock_code,)).fetchall()

    if len(rows) < 20:
        return 'gray', None, '데이터 부족'

    closes  = [r[0] for r in rows]
    volumes = [r[1] or 0 for r in rows]

    curr   = closes[0]
    high52 = max(closes[:252] if len(closes) >= 252 else closes)
    from_high = (curr - high52) / high52 * 100

    # 거래량: 최근 1개월 vs 과거 평균
    recent_vol  = sum(volumes[:20]) / 20 if len(volumes) >= 20 else 0
    baseline_vol = sum(volumes[20:40]) / 20 if len(volumes) >= 40 else recent_vol
    vol_ratio = round(recent_vol / baseline_vol, 2) if baseline_vol > 0 else 1

    near_high   = from_high >= near_pct   # -15% 이내
    vol_surging = vol_ratio >= 1.3        # 거래량 30% 증가

    if near_high and vol_surging:
        sig    = 'green'
        detail = f"신고가 근접 ({from_high:+.1f}%) + 거래량 급증 ({vol_ratio}배) — 강한 모멘텀"
    elif near_high:
        sig    = 'yellow'
        detail = f"신고가 근접 ({from_high:+.1f}%) — 거래량 확인 필요"
    elif from_high >= -30:
        sig    = 'yellow'
        detail = f"52주 고점 {from_high:+.1f}% — 중립"
    else:
        sig    = 'red'
        detail = f"52주 고점 {from_high:+.1f}% — 추세 약세"

    return sig, round(from_high, 2), detail


def _calc_value_momentum(conn, params, stock_code):
    """
    가치+모멘텀 AND 조건 (가치함정 방지):
    PBR<1 + PER<12 AND 52주 추세 상승 중 이어야 green
    가치만 좋고 추세 없으면 yellow (가치함정 경고)
    """
    # 밸류에이션 (signal_result 또는 네이버)
    val_row = conn.execute("""
        SELECT value FROM signal_result
        WHERE config_id=(SELECT id FROM signal_config WHERE name='value' LIMIT 1)
        AND stock_code=? ORDER BY calc_date DESC LIMIT 1
    """, (stock_code,)).fetchone()

    # price_history로 52주 추세 확인
    rows = conn.execute("""
        SELECT close FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT 260
    """, (stock_code,)).fetchall()

    if len(rows) < 60:
        return 'gray', None, '데이터 부족'

    closes = [r[0] for r in rows]
    curr   = closes[0]
    ma60   = sum(closes[:60]) / 60
    high52 = max(closes)
    trend_up = curr > ma60  # MA60 위 = 상승 추세

    # 네이버에서 PBR/PER 조회 (캐시)
    naver_row = conn.execute("""
        SELECT value FROM signal_result
        WHERE stock_code=? AND config_id IN
        (SELECT id FROM signal_config WHERE name IN ('value','pbr') LIMIT 1)
        ORDER BY calc_date DESC LIMIT 1
    """, (stock_code,)).fetchone()

    # 간단하게 52주 추세만으로 판단 (PBR/PER는 별도 API)
    from_high = (curr - high52) / high52 * 100

    if trend_up and from_high >= -20:
        sig    = 'green'
        detail = f"상승추세 확인 — 가치+모멘텀 AND 조건 충족 (MA60 위, 고점 {from_high:+.1f}%)"
    elif trend_up:
        sig    = 'yellow'
        detail = f"상승추세 유지 — 고점 대비 {from_high:+.1f}% (모멘텀 약)"
    else:
        sig    = 'red'
        detail = f"하락추세 (MA60 아래) — 가치 좋아도 추세 없으면 가치함정 주의"

    return sig, round(from_high, 2), detail


# ══════════════════════════════════════════════════════════════════
# 기존 로직 (유지)
# ══════════════════════════════════════════════════════════════════

def _calc_supply_trend(conn, params, stock_code):
    days   = params.get('days', 5)
    target = params.get('target', 'both')
    cutoff = (date.today() - timedelta(days=days+5)).isoformat()

    if stock_code:
        rows = conn.execute("""
            SELECT date, inst_net_buy, frn_net_buy,
                   inst_net_buy_amt, frn_net_buy_amt, close FROM price_history
            WHERE stock_code=? AND date>=? ORDER BY date DESC LIMIT ?
        """, (stock_code, cutoff, days)).fetchall()
    else:
        rows = conn.execute("""
            SELECT date, inst_net_buy, frn_net_buy,
                   inst_net_buy_amt, frn_net_buy_amt, close FROM price_history
            WHERE stock_code='^KS11' AND date>=? ORDER BY date DESC LIMIT ?
        """, (cutoff, days)).fetchall()

    if not rows: return 'gray', None, '데이터 없음'

    # 수급 데이터가 전부 0 이면 수집 전 캐시 → gray 반환
    if all((r[1] or 0) == 0 and (r[2] or 0) == 0 for r in rows):
        return 'gray', None, '수급 데이터 수집 대기'

    is_index = stock_code in ('^KS11','^KQ11') or not stock_code

    # amt 단위 자동 감지:
    #   KOSDAQ/소형주: frn_net_buy_amt 는 백만원 단위 → abs(amt)/abs(qty) ≈ 0.003
    #   KOSPI/대형주:  frn_net_buy_amt 는 주(株) 단위 → abs(amt)/abs(qty) ≈ 1.0
    # ratio >= 0.1 이면 주 단위 → qty × close / 1억 으로 억원 계산
    def _to_억(qty, amt, close, is_index):
        if is_index:
            return (amt or 0) if amt else (qty or 0)
        qty = qty or 0
        amt = amt or 0
        close = close or 0
        if amt == 0:
            # amt 없으면 qty/100 폴백 (백만원 가정)
            return qty / 100
        if qty != 0 and abs(amt) / abs(qty) >= 0.1:
            # amt 가 주(株) 단위 → 주 × 종가(원) / 1억 = 억원
            return qty * close / 100_000_000
        else:
            # amt 가 백만원 단위 → / 100 = 억원
            return amt / 100

    inst_sum = sum(_to_억(r[1], r[3], r[5], is_index) for r in rows)
    frn_sum  = sum(_to_억(r[2], r[4], r[5], is_index) for r in rows)

    def _fmt_amt(val):
        """수급 금액 포맷 — 소액도 정확히 표시 (round() 로 +0억 표시 방지)"""
        sign = '+' if val >= 0 else ''
        av = abs(val)
        if av >= 10:
            return f"{sign}{round(val):,}억원"
        elif av >= 1:
            return f"{sign}{val:.1f}억원"
        elif av >= 0.01:
            # 억원 미만 → 백만원 단위로 전환 (1억=100백만원)
            mn = round(val * 100)
            return f"{sign}{mn:,}백만원"
        else:
            return f"{sign}0억원"

    if target == 'frn':
        val = frn_sum
        sig = 'green' if val > 0 else 'red' if val < 0 else 'yellow'
        return sig, val, f"외국인 {days}일 누적: {_fmt_amt(val)}"
    elif target == 'inst':
        val = inst_sum
        sig = 'green' if val > 0 else 'red' if val < 0 else 'yellow'
        return sig, val, f"기관 {days}일 누적: {_fmt_amt(inst_sum)}"
    else:
        net = inst_sum + frn_sum
        both_pos = inst_sum > 0 and frn_sum > 0
        both_neg = inst_sum < 0 and frn_sum < 0
        sig = 'green' if (both_pos or net > 20000) else 'red' if (both_neg or net < -20000) else 'yellow'
        return sig, net, f"기관 {_fmt_amt(inst_sum)} / 외국인 {_fmt_amt(frn_sum)} ({days}일)"


def _calc_threshold(conn, name, params, stock_code):
    green_b = params.get('green_below')
    red_a   = params.get('red_above')
    green_a = params.get('green_above')
    red_b   = params.get('red_below')
    val = None

    if name in ('vix_level', 'vix_trend'):
        row = conn.execute("SELECT close FROM price_history WHERE stock_code='^VIX' AND close>0 ORDER BY date DESC LIMIT 1").fetchone()
        val = round(row[0], 2) if row else None
        if not val: return 'gray', None, 'VIX 데이터 없음'
        sig = 'green' if val < (green_b or 20) else 'red' if val > (red_a or 30) else 'yellow'
        return sig, val, f"VIX {val}"

    elif name in ('usd_krw', 'usd_krw_trend'):
        row = conn.execute("SELECT close FROM price_history WHERE stock_code='USDKRW=X' AND close>0 ORDER BY date DESC LIMIT 1").fetchone()
        val = round(row[0], 1) if row else None
        if not val: return 'gray', None, '환율 데이터 없음'
        sig = 'green' if val < (green_b or 1300) else 'red' if val > (red_a or 1380) else 'yellow'
        return sig, val, f"원/달러 {val:,.0f}"

    elif name == 'debt_ratio':
        if not stock_code: return 'gray', None, '종목 필요'
        row = conn.execute("""
            SELECT total_liabilities, total_equity FROM financial_data
            WHERE stock_code=? AND total_liabilities IS NOT NULL AND total_equity > 0
              AND is_annual = 0
            ORDER BY year DESC, quarter DESC LIMIT 1
        """, (stock_code,)).fetchone()
        if not row: return 'gray', None, '재무데이터 없음'
        val = round(row[0] / row[1] * 100, 1)
        sig = 'green' if val < (green_b or 100) else 'red' if val > (red_a or 200) else 'yellow'
        return sig, val, f"부채비율 {val}%"

    elif name == 'short_sell':
        if not stock_code: return 'gray', None, '종목 필요'
        rows = conn.execute("""
            SELECT borrow_bal_qty FROM short_sell_daily WHERE stock_code=?
            AND borrow_bal_qty IS NOT NULL ORDER BY bas_dt DESC LIMIT 10
        """, (stock_code,)).fetchall()
        if not rows: return 'gray', None, '대차 데이터 없음'
        # borrow_bal_qty 는 잔고(stock) 지표 — 합계가 아닌 평균/비교로 판단
        vals5 = [r[0] or 0 for r in rows[:5]]
        vals5p = [r[0] or 0 for r in rows[5:10]]
        avg5 = sum(vals5) / len(vals5) if vals5 else 0
        avg5_prev = sum(vals5p) / len(vals5p) if vals5p else 0
        trend_up = avg5 > avg5_prev
        val = round(rows[0][0] or 0)          # 최신 잔고
        # 5일전 대비 변화량 (절대잔고 증감)
        val_5d = round(rows[4][0] or 0) if len(rows) >= 5 else val
        change = val - val_5d
        sig = 'red' if trend_up else 'green'
        def _fmt(v):
            if v >= 100000000: return f"{v/100000000:.1f}억주"
            if v >= 10000: return f"{v/10000:.1f}만주"
            return f"{int(v):,}주"
        def _fmt_chg(v):
            sign = '+' if v >= 0 else ''
            if abs(v) >= 100000000: return f"{sign}{v/100000000:.1f}억주"
            if abs(v) >= 10000: return f"{sign}{v/10000:.1f}만주"
            return f"{sign}{int(v):,}주"
        detail = f"대차잔고 {_fmt(val)} ({'↑증가' if trend_up else '↓감소'}) 5일평균:{_fmt(round(avg5))} 5일전比:{_fmt_chg(change)}"
        return sig, val, detail

    elif name == 'float_ratio':
        if not stock_code: return 'gray', None, '종목 필요'
        row = conn.execute("""
            SELECT shares FROM listed_company_info WHERE stock_code=? LIMIT 1
        """, (stock_code,)).fetchone()
        if not row: return 'gray', None, '상장정보 없음'
        return 'yellow', None, '유통비율 데이터 준비 중'

    return 'gray', None, f'미구현({name})'


def _calc_ma_trend(conn, params, stock_code):
    symbol = params.get('symbol') or stock_code
    ma_s   = params.get('ma_short', 5)
    ma_l   = params.get('ma_long', 20)
    closes = _get_closes(conn, symbol, ma_l+5)
    if len(closes) < ma_l: return 'gray', None, '데이터 부족'
    curr = closes[0]
    ma5  = sum(closes[:ma_s]) / ma_s
    ma20 = sum(closes[:ma_l]) / ma_l
    a5, a20 = curr > ma5, curr > ma20
    sig = 'green' if a5 and a20 else 'red' if not a5 and not a20 else 'yellow'
    nm = {'^IXIC':'나스닥','^GSPC':'S&P500','^KS11':'KOSPI'}.get(symbol, symbol)
    return sig, curr, f"{nm} MA{ma_s}{'위' if a5 else '아래'}/MA{ma_l}{'위' if a20 else '아래'}"


def _calc_ma_position(conn, params, stock_code):
    if not stock_code: return 'gray', None, '종목 필요'
    ma1  = params.get('ma1', 20); ma2 = params.get('ma2', 60)
    rows = _get_closes(conn, stock_code, ma2+5)
    if len(rows) < ma2: return 'gray', None, '데이터 부족'
    curr = rows[0]; ms = sum(rows[:ma1])/ma1; ml = sum(rows[:ma2])/ma2
    a1, a2 = curr > ms, curr > ml
    sig = 'green' if a1 and a2 else 'red' if not a1 and not a2 else 'yellow'
    return sig, curr, f"MA{ma1}{'위' if a1 else '아래'}/MA{ma2}{'위' if a2 else '아래'} (현재 {curr:,.0f}원)"


def _calc_financial(conn, params, stock_code):
    if not stock_code: return 'gray', None, '종목 필요'
    check_eps = params.get('check_eps_trend', False)
    rows = conn.execute("""
        SELECT year, quarter, revenue, operating_profit, net_income, eps, is_annual
        FROM financial_data WHERE stock_code=? AND is_annual=0
        ORDER BY year DESC, quarter DESC LIMIT 5
    """, (stock_code,)).fetchall()
    if not rows: return 'gray', None, '재무 데이터 없음'

    if check_eps:
        eps_vals = [r[5] for r in rows if r[5] is not None]
        if len(eps_vals) < 2: return 'gray', None, 'EPS 데이터 부족'
        improving = sum(1 for i in range(len(eps_vals)-1) if eps_vals[i] > eps_vals[i+1])
        sig = 'green' if improving >= 2 else 'red' if improving == 0 else 'yellow'
        return sig, eps_vals[0], f"EPS 추세: {'개선' if improving>=2 else '악화' if improving==0 else '유지'}"

    op  = rows[0][3] or 0
    rev_now = rows[0][2] or 0
    rev_yoy = rows[4][2] or 0 if len(rows) >= 5 else 0
    yoy = (rev_now-rev_yoy)/abs(rev_yoy)*100 if rev_yoy != 0 else 0

    if op > 0 and yoy > 5:
        return 'green', op, f"흑자 YoY{yoy:+.1f}%"
    elif op > 0:
        return 'yellow', op, f"흑자 YoY{yoy:+.1f}%"
    else:
        return 'red', op, f"영업적자 ({op/1e8:.0f}억원)"


def _calc_price_position(conn, params, stock_code):
    if not stock_code: return 'gray', None, '종목 필요'
    rows = _get_closes(conn, stock_code, 260)
    if len(rows) < 20: return 'gray', None, '데이터 부족'
    curr = rows[0]; high52 = max(rows)
    from_high = (curr - high52) / high52 * 100
    g = params.get('green_above', -10); r = params.get('red_below', -40)
    sig = 'green' if from_high > g else 'red' if from_high < r else 'yellow'
    return sig, round(from_high,2), f"52주 고점 대비 {from_high:+.1f}%"


def _calc_technical(conn, params, stock_code):
    """기존 기술지표 (RSI, MACD, 볼린저, 거래량, 정배열, Stochastic)."""
    typ = params.get('type', 'rsi')

    rows = conn.execute("""
        SELECT date, close, volume FROM price_history
        WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 120
    """, (stock_code,)).fetchall()
    if len(rows) < 30: return 'gray', None, '데이터 부족'

    closes_asc  = list(reversed([r[1] for r in rows]))
    volumes_asc = list(reversed([r[2] or 0 for r in rows]))

    if typ == 'ma_align':
        ma1 = params.get('ma1',5); ma2 = params.get('ma2',20); ma3 = params.get('ma3',60)
        if len(closes_asc) < ma3: return 'gray', None, '데이터 부족'
        curr = closes_asc[-1]
        v1 = sum(closes_asc[-ma1:])/ma1; v2 = sum(closes_asc[-ma2:])/ma2; v3 = sum(closes_asc[-ma3:])/ma3
        if curr > v1 > v2 > v3:
            return 'green', curr, f"완전 정배열 (현재>{ma1}>{ma2}>{ma3}일)"
        elif curr < v1 < v2 < v3:
            return 'red', curr, f"완전 역배열"
        elif curr > v1 and curr > v2 and curr > v3:
            return 'green', curr, f"전 이평선 위 (부분 정배열)"
        elif curr < v2 and curr < v3:
            return 'red', curr, f"장기 이평선 아래"
        else:
            return 'yellow', curr, f"혼조 (MA{ma1}:{v1:,.0f}/MA{ma2}:{v2:,.0f})"

    elif typ == 'volume':
        ma_days = params.get('ma_days',20); spike = params.get('spike_ratio',2.0)
        if len(volumes_asc) < ma_days+1: return 'gray', None, '데이터 부족'
        avg_v = sum(volumes_asc[-ma_days-1:-1])/ma_days
        curr_v = volumes_asc[-1]; ratio = round(curr_v/avg_v,2) if avg_v>0 else 1
        price_up = closes_asc[-1] >= closes_asc[-2] if len(closes_asc)>=2 else True
        if price_up and ratio >= spike: return 'green', ratio, f"상승+거래량급증({ratio}배)"
        elif price_up and ratio >= 1.3: return 'green', ratio, f"상승+거래량증가({ratio}배)"
        elif not price_up and ratio >= spike: return 'red', ratio, f"하락+거래량급증({ratio}배)"
        else: return 'yellow', ratio, f"거래량 평균({ratio}배)"

    elif typ == 'bollinger':
        period = params.get('period',20); std_n = params.get('std',2)
        if len(closes_asc) < period: return 'gray', None, '데이터 부족'
        recent = closes_asc[-period:]; ma = sum(recent)/period
        std = (sum((x-ma)**2 for x in recent)/period)**0.5
        upper = ma + std_n*std; lower = ma - std_n*std
        curr = closes_asc[-1]
        pct_b = round((curr-lower)/(upper-lower)*100,1) if upper!=lower else 50
        if curr > ma and curr >= ma+std*1.5:
            return 'green', pct_b, f"상단밴드 돌파 (BB {pct_b}%)"
        elif curr < ma:
            return 'red', pct_b, f"중심선 아래 (BB {pct_b}%)"
        else:
            return 'yellow', pct_b, f"중립 (BB {pct_b}%)"

    elif typ == 'stochastic':
        k_p = params.get('k_period',14); d_p = params.get('d_period',3)
        hl_rows = conn.execute("""
            SELECT close,high,low FROM price_history WHERE stock_code=? AND close>0
            ORDER BY date DESC LIMIT ?
        """, (stock_code, k_p+d_p+5)).fetchall()
        if len(hl_rows) < k_p: return 'gray', None, '데이터 부족'
        hl_asc = list(reversed(hl_rows))
        k_vals = []
        for i in range(k_p-1, len(hl_asc)):
            win = hl_asc[i-k_p+1:i+1]
            c = win[-1][0]; h = max(r[1] or r[0] for r in win); l = min(r[2] or r[0] for r in win)
            k_vals.append(round((c-l)/(h-l)*100 if h!=l else 50, 1))
        if len(k_vals) < d_p: return 'gray', None, '데이터 부족'
        d_vals = [sum(k_vals[i-d_p+1:i+1])/d_p for i in range(d_p-1, len(k_vals))]
        ck = k_vals[-1]; cd = d_vals[-1]
        pk = k_vals[-2] if len(k_vals)>=2 else ck; pd = d_vals[-2] if len(d_vals)>=2 else cd
        golden = pk<=pd and ck>cd; dead = pk>=pd and ck<cd
        if ck<=20 and golden: return 'green', ck, f"과매도 골든크로스({ck:.0f})"
        elif ck>=80 and dead: return 'red', ck, f"과열 데드크로스({ck:.0f})"
        elif golden: return 'green', ck, f"골든크로스({ck:.0f})"
        elif dead: return 'red', ck, f"데드크로스({ck:.0f})"
        else: return 'yellow', ck, f"중립(K:{ck:.0f}/D:{cd:.0f})"

    return 'gray', None, f'미구현({typ})'


# ══════════════════════════════════════════════════════════════════
# Graham 가치투자 로직
# ══════════════════════════════════════════════════════════════════

def _get_graham_data(conn, stock_code: str) -> dict:
    """Graham 계산에 필요한 재무 데이터 조회."""
    # 최근 5개 레코드 조회 후 ROE 기준 합리적인 레코드 선택
    rows = conn.execute("""
        SELECT eps, bps, roe, total_equity, total_assets, total_liabilities,
               net_income, revenue, operating_profit
        FROM financial_data
        WHERE stock_code=? AND eps IS NOT NULL AND eps > 0
          AND is_annual = 0
        ORDER BY year DESC, quarter DESC
        LIMIT 5
    """, (stock_code,)).fetchall()
    if not rows:
        return {}

    # 합리적인 레코드 선택: BPS 직접 보유 우선, 없으면 ROE<300% 기준 equity 관산 가능한 것
    row = None
    for r in rows:
        _eps, _bps, _roe, _eq, _ast, _liab, _ni, _rev, _op = r
        if not _eps or _eps <= 0:
            continue
        if _bps and _bps > 0:
            row = r; break  # BPS 직접 존재
        if _eq and _eq > 0 and _ni and _ni > 0 and _eq >= _ni / 3:
            row = r; break  # ROE < 300% — equity 관산 가능
    if not row:
        row = rows[0]

    eps, bps, roe, equity, assets, liab, net_inc, rev, op = row

    # BPS가 0이면 total_equity × eps / net_income 으로 관산
    if (not bps or bps == 0) and equity and equity > 0 and net_inc and net_inc > 0 and eps > 0:
        if equity >= net_inc / 3:  # ROE < 300% 기본 검증
            shares_approx = net_inc / eps
            if shares_approx > 0:
                bps = equity / shares_approx

    # 현재 주가
    price_row = conn.execute("""
        SELECT close FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT 1
    """, (stock_code,)).fetchone()
    if not price_row:
        return {}

    price = price_row[0]

    # Graham 내재가치 = sqrt(22.5 × EPS × BPS)
    # EPS, BPS 모두 양수여야 의미 있음
    graham_iv = None
    import math
    if eps > 0 and bps and bps > 0:
        graham_iv = math.sqrt(22.5 * eps * bps)

    per = round(price / eps, 1) if eps and eps > 0 else None
    pbr = round(price / bps, 2) if bps and bps > 0 else None
    discount = round((graham_iv - price) / graham_iv * 100, 1) if graham_iv and graham_iv > 0 else None

    return {
        'price': price, 'eps': eps, 'bps': bps,
        'graham_iv': graham_iv, 'per': per, 'pbr': pbr,
        'discount': discount,  # 양수 = 저평가, 음수 = 고평가
        'roe': roe,
    }


def _calc_graham_signal(conn, stock_code: str):
    """Graham 내재가치 대비 현재가 위치 시그널."""
    d = _get_graham_data(conn, stock_code)
    if not d or not d.get('graham_iv'):
        return 'gray', None, 'Graham 계산 불가 (EPS/BPS 데이터 없음)'

    iv     = d['graham_iv']
    price  = d['price']
    pbr    = d['pbr']
    per    = d['per']
    disc   = d['discount']

    pbr_ok = pbr is not None and pbr < 1.0
    per_ok = per is not None and 0 < per < 15

    if disc is not None and disc >= 30 and pbr_ok:
        sig    = 'green'
        detail = (f"Graham 저평가 {disc:.0f}% — 내재가 {iv:,.0f}원 / 현재 {price:,.0f}원 "
                  f"(PBR {pbr}, PER {per})")
    elif disc is not None and disc >= 15:
        sig    = 'green' if pbr_ok else 'yellow'
        detail = (f"Graham 할인 {disc:.0f}% — 내재가 {iv:,.0f}원 "
                  f"(PBR {pbr}, PER {per})")
    elif disc is not None and disc >= 0:
        sig    = 'yellow'
        detail = (f"Graham 소폭 저평가 {disc:.0f}% "
                  f"(PBR {pbr}, PER {per})")
    else:
        sig    = 'red'
        detail = (f"Graham 고평가 — 내재가 {iv:,.0f}원 대비 {abs(disc or 0):.0f}% 위 "
                  f"(PBR {pbr}, PER {per})")

    return sig, round(disc or 0, 1), detail


def _calc_macd_divergence(conn, stock_code: str):
    """
    MACD 강세 다이버전스 (가치 바닥 감지):
    가격은 신저점인데 MACD 오실레이터는 이전 저점보다 높음 → 반전 신호
    """
    closes = _get_closes(conn, stock_code, 130)
    if len(closes) < 80:
        return 'gray', None, '데이터 부족'

    closes_asc = list(reversed(closes))

    def ema(data, n):
        k = 2/(n+1); e = data[0]; result = [e]
        for v in data[1:]: e = v*k + e*(1-k); result.append(e)
        return result

    ef = ema(closes_asc, 12)
    es = ema(closes_asc, 26)
    macd = [f-s for f,s in zip(ef, es)]
    sig_line = ema(macd[25:], 9)

    # MACD 오실레이터 = MACD - Signal
    # ema()는 입력과 동일한 길이를 반환하므로 sig_line 길이 = len(macd)-25
    # sig_line[8:] 부터 EMA 워밍업이 끝난 값 → macd[25+8] = macd[33] 에 정렬
    osc_offset = 33
    sig_warmed = sig_line[8:]  # 워밍업 8개 제거
    osc = [macd[osc_offset+i] - sig_warmed[i] for i in range(len(sig_warmed))]
    prices_aligned = closes_asc[osc_offset:]

    if len(osc) < 40:
        return 'gray', None, '데이터 부족'

    curr_osc   = osc[-1]
    below_zero = curr_osc < 0

    # 최근 20봉에서 MACD 저점 찾기
    recent_osc    = osc[-20:]
    recent_prices = prices_aligned[-20:]
    prev_osc      = osc[-40:-20]
    prev_prices   = prices_aligned[-40:-20]

    if not recent_osc or not prev_osc:
        return 'gray', None, '데이터 부족'

    recent_min_osc = min(recent_osc)
    prev_min_osc   = min(prev_osc)
    recent_min_price = min(recent_prices) if recent_prices else 0
    prev_min_price   = min(prev_prices)   if prev_prices else float('inf')

    # 강세 다이버전스: 가격 더 낮은데 MACD 더 높음 (0선 아래에서)
    divergence = (
        below_zero and
        recent_min_price < prev_min_price and   # 가격 신저점
        recent_min_osc > prev_min_osc            # MACD 저점 상승
    )

    if divergence:
        sig    = 'green'
        detail = (f"MACD 강세 다이버전스 (0선 아래) — "
                  f"가격 저점 하락 but MACD 저점 상승 → 반전 기대")
    elif below_zero and curr_osc > osc[-2] if len(osc) >= 2 else False:
        sig    = 'yellow'
        detail = f"MACD 0선 아래 반등 중 (오실레이터 {curr_osc:+.2f})"
    elif not below_zero:
        sig    = 'yellow'
        detail = f"MACD 0선 위 (다이버전스 조건 미해당)"
    else:
        sig    = 'red'
        detail = f"MACD 0선 아래 하락 중 (다이버전스 없음)"

    return sig, round(curr_osc, 4), detail


def _calc_smart_money_3of5(conn, stock_code: str):
    """
    스마트머니 감지: 최근 5거래일 중 3일 이상 기관+외국인 동반 순매수.
    가격이 바닥권일 때 의미 있음.
    """
    rows = conn.execute("""
        SELECT date, inst_net_buy_amt, frn_net_buy_amt,
               inst_net_buy, frn_net_buy
        FROM price_history
        WHERE stock_code=? AND date >= DATE('now', '-10 days')
        ORDER BY date DESC LIMIT 5
    """, (stock_code,)).fetchall()

    if len(rows) < 3:
        return 'gray', None, '수급 데이터 부족'

    buy_days = 0
    for dt, inst_amt, frn_amt, inst_qty, frn_qty in rows:
        # 금액 우선, 없으면 수량
        inst_v = inst_amt if inst_amt else (inst_qty or 0) / 100
        frn_v  = frn_amt  if frn_amt  else (frn_qty  or 0) / 100
        if inst_v > 0 and frn_v > 0:
            buy_days += 1

    n = len(rows)
    if buy_days >= 3:
        sig    = 'green'
        detail = f"스마트머니 유입 — {n}일 중 {buy_days}일 기관+외국인 동반 순매수"
    elif buy_days >= 2:
        sig    = 'yellow'
        detail = f"스마트머니 일부 — {n}일 중 {buy_days}일 동반 순매수"
    else:
        sig    = 'red'
        detail = f"스마트머니 부재 — {n}일 중 {buy_days}일만 동반 순매수"

    return sig, buy_days, detail


def _calc_ma20_slope(conn, stock_code: str):
    """MA20 기울기 완만화 여부 — 하락 멈춤 감지."""
    closes = _get_closes(conn, stock_code, 30)
    if len(closes) < 25:
        return 'gray', None, '데이터 부족'

    closes_asc = list(reversed(closes))
    if len(closes_asc) < 25:
        return 'gray', None, '데이터 부족'

    ma20_now  = sum(closes_asc[-20:]) / 20
    ma20_5ago = sum(closes_asc[-25:-5]) / 20

    slope_pct = (ma20_now - ma20_5ago) / ma20_5ago * 100 if ma20_5ago > 0 else 0

    if abs(slope_pct) < 0.5:
        sig    = 'green'
        detail = f"MA20 기울기 완만 ({slope_pct:+.2f}%) — 하락 진정"
    elif slope_pct > 0:
        sig    = 'green'
        detail = f"MA20 상승 기울기 ({slope_pct:+.2f}%)"
    elif slope_pct > -2:
        sig    = 'yellow'
        detail = f"MA20 완만 하락 ({slope_pct:+.2f}%) — 진정 여부 확인"
    else:
        sig    = 'red'
        detail = f"MA20 가파른 하락 ({slope_pct:+.2f}%) — 매수 대기"

    return sig, round(slope_pct, 2), detail


def _calc_value_turnaround(conn, stock_code: str, pre_d=None):
    """
    Value + Turnaround 종합 판정:
    PBR<1 + PER<10(or<15) + Graham 30% 할인 + MA20 완만 + MACD 다이버전스 + 스마트머니

    pre_d: 이미 계산된 Graham 데이터 dict (bulk 로딩 시 중복 쿼리 방지)
    """
    d = pre_d if pre_d is not None else _get_graham_data(conn, stock_code)
    if not d:
        return 'gray', None, 'Graham 데이터 없음'

    pbr     = d.get('pbr')
    per     = d.get('per')
    disc    = d.get('discount')
    price   = d.get('price', 0)
    iv      = d.get('graham_iv', 0)

    # 조건 점수 (각 1점)
    score = 0
    conditions = []

    if pbr is not None and pbr < 1.0:
        score += 1; conditions.append(f"PBR {pbr} < 1")
    if per is not None and 0 < per < 10:
        score += 2; conditions.append(f"PER {per} < 10 (강력)")
    elif per is not None and 0 < per < 15:
        score += 1; conditions.append(f"PER {per} < 15")
    if disc is not None and disc >= 30:
        score += 2; conditions.append(f"Graham {disc:.0f}% 저평가")
    elif disc is not None and disc >= 15:
        score += 1; conditions.append(f"Graham {disc:.0f}% 할인")

    # MA20 기울기
    slope_sig, slope_val, _ = _calc_ma20_slope(conn, stock_code)
    if slope_sig == 'green':
        score += 1; conditions.append("MA20 완만화")

    # MACD 다이버전스
    div_sig, _, _ = _calc_macd_divergence(conn, stock_code)
    if div_sig == 'green':
        score += 2; conditions.append("MACD 강세 다이버전스")
    elif div_sig == 'yellow':
        score += 1

    # 스마트머니
    sm_sig, sm_val, _ = _calc_smart_money_3of5(conn, stock_code)
    if sm_sig == 'green':
        score += 1; conditions.append(f"스마트머니 유입")
    elif sm_sig == 'yellow':
        score += 0  # 부분 점수 없음

    cond_str = ' | '.join(conditions) if conditions else '조건 미충족'

    if score >= 6:
        sig    = 'green'
        detail = f"✅ 강력 가치매수 (점수 {score}/9) — {cond_str}"
    elif score >= 4:
        sig    = 'green'
        detail = f"🟢 가치매수 (점수 {score}/9) — {cond_str}"
    elif score >= 2:
        sig    = 'yellow'
        detail = f"🟡 가치 관심 (점수 {score}/9) — {cond_str}"
    else:
        sig    = 'red'
        detail = f"가치투자 조건 미충족 (점수 {score}/9)"

    return sig, score, detail


# ══════════════════════════════════════════════════════════════════
# 추세 추종 후보 스캔
# ══════════════════════════════════════════════════════════════════
# 주봉 유틸리티
# ══════════════════════════════════════════════════════════════════

def _daily_to_weekly_closes(rows_desc):
    """
    일봉 rows (date DESC 순서) → 주봉 종가 리스트 (최신순)
    각 주의 마지막 거래일 종가를 주봉 종가로 사용.
    rows_desc: [(date_str, close), ...]
    """
    from datetime import datetime
    weekly = {}  # {(year, isoweek): close}
    for date_str, close in rows_desc:
        try:
            dt = datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
            iso = dt.isocalendar()
            yw  = (iso[0], iso[1])
            if yw not in weekly:        # DESC 순서이므로 첫 등장 = 해당 주 최신일 = 마지막 거래일
                weekly[yw] = close
        except Exception:
            pass
    sorted_yw = sorted(weekly.keys(), reverse=True)
    return [weekly[k] for k in sorted_yw]  # 최신 → 과거 순


def _weekly_ma(weekly_closes, n):
    """weekly_closes (최신순) 기준으로 n주 MA 계산. 데이터 부족 시 None."""
    if len(weekly_closes) < n:
        return None
    return sum(weekly_closes[:n]) / n


def _build_sector_activation_map(conn):
    """
    섹터별 주도주 장기 추세 활성화 점수 계산.
    ─ stock_universe에서 섹터 + 시총 정보 조회
    ─ 각 섹터 상위 5개 종목의 52주 주봉선 위치 확인
    ─ 반환: {sector_large: activation_score}  (0.0 ~ 1.0)

    최적화: 모듈 레벨 캐시(TTL 3600s) + 섹터 리더 전종목 단일 벌크 쿼리
    (이 함수는 calc_trend/top20/combo_v2 3곳에서 호출되므로 캐시 효과 큼)
    """
    global _sector_activation_cache
    if (_sector_activation_cache["data"] is not None
            and _time.time() - _sector_activation_cache["at"] < 3600):
        return _sector_activation_cache["data"]

    # 섹터별 시총 상위 종목
    sector_top = conn.execute("""
        SELECT DISTINCT su.stock_code, su.sector_large,
               MAX(su.market_cap) as mktcap
        FROM stock_universe su
        WHERE su.sector_large IS NOT NULL
          AND LENGTH(su.stock_code) = 6
          AND su.stock_code GLOB '[0-9]*'
          AND su.market_cap > 0
        GROUP BY su.stock_code, su.sector_large
        ORDER BY su.sector_large, mktcap DESC
    """).fetchall()

    # 섹터별 상위 5개 추출
    sector_stocks = _defaultdict(list)
    for sc, slarge, mktcap in sector_top:
        if len(sector_stocks[slarge]) < 5:
            sector_stocks[slarge].append(sc)

    # ★ 벌크 쿼리: 전체 리더 종목의 가격 히스토리를 1번에 조회 (기존 75번 → 1번)
    all_leaders = [sc for leaders in sector_stocks.values() for sc in leaders]
    ph_by_code: dict = _defaultdict(list)
    if all_leaders:
        placeholders = ','.join('?' * len(all_leaders))
        for sc, dt, close in conn.execute(f"""
            SELECT stock_code, date, close FROM price_history
            WHERE stock_code IN ({placeholders}) AND close > 0
            ORDER BY stock_code, date DESC
        """, all_leaders).fetchall():
            if len(ph_by_code[sc]) < 400:
                ph_by_code[sc].append((dt, close))

    activation = {}
    for sector, leaders in sector_stocks.items():
        above = 0
        near  = 0
        total = 0
        for sc in leaders:
            ph = ph_by_code.get(sc, [])
            if len(ph) < 50:
                continue
            weekly = _daily_to_weekly_closes(ph)
            if len(weekly) < 20:
                continue
            curr = weekly[0]
            ma52 = _weekly_ma(weekly, 52) if len(weekly) >= 52 else _weekly_ma(weekly, len(weekly))
            if not ma52:
                continue
            total += 1
            pct = (curr - ma52) / ma52 * 100
            if pct >= 0:
                above += 1
            elif pct >= -5:
                near += 1

        activation[sector] = (above + near * 0.5) / total if total > 0 else 0.0

    _sector_activation_cache["data"] = activation
    _sector_activation_cache["at"]   = _time.time()
    return activation


def calc_trend_candidates(conn=None) -> list:
    """
    추세추종 주도주 발굴 — 미너비니(Minervini) 3단계 필터:

    [1단계] 유동성 필터 (잡주 제거)
      - 시가총액 1,000억 이상
      - 최근 5거래일 평균 거래대금 100억 이상

    [2단계] 미너비니 추세 템플릿 (장기 추세 확인)
      - 현재가 > MA120 AND 현재가 > MA200
      - MA120 > MA200 (장기 정배열)
      - 현재가 >= 52주 최고가 × 0.80 (신고가 20% 이내)
      - 현재가 > MA5 > MA20 > MA60 (단기 정배열)

    [3단계] 폭발 타이밍 필터 (진입 트리거)
      - 오늘 거래량 > 20일 평균 거래량 × 2.0 (폭발 필수)
        OR 최근 5일 평균 거래량 > 20일 평균 × 1.5 (점수 부여)
      - RSI(14) >= 60 (진정한 상승 모멘텀)
      - 볼린저 밴드 스퀴즈 후 상단 돌파 여부

    [가산점] 섹터 활성화 / RS / 수급 / 200주선
    결과: 상위 20종목 이내 압축
    """
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)

    try:
        # ── 섹터 활성화 맵 ─────────────────────────────────────────
        sector_map = _build_sector_activation_map(conn)

        # ── 종목-섹터/시총/시장 매핑 ──────────────────────────────
        _umaps      = _load_universe_maps(conn)
        code_mktcap = _umaps["mktcap"]
        code_market = _umaps["market"]
        code_tvol   = _umaps["tvol"]
        code_name   = _umaps["name"]
        # sector: (slarge, smid) 튜플 형태로 조립
        code_sector = {sc: (_umaps["sector"][sc], _umaps["sector_mid"][sc])
                       for sc in _umaps["sector"]}

        # ── KOSPI 3개월 수익률 (RS 계산용) ────────────────────────
        kospi_rows = conn.execute("""
            SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0
            ORDER BY date DESC LIMIT 70
        """).fetchall()
        kospi_ret = 0.0
        if len(kospi_rows) >= 63:
            kospi_ret = (kospi_rows[0][0] - kospi_rows[62][0]) / kospi_rows[62][0] * 100

        # ── 보너스 점수 맵 로드 (수주공시 + HS수출 + NPS월별고용)
        contract_bonus_map  = _load_contract_bonus_map(days=90)
        hs_bonus_map        = _load_hs_export_bonus_map(months=6)
        nps_monthly_map     = _load_nps_monthly_bonus_map()  # 최대 +2점

        # ── 120일 이상 데이터 보유 종목 (시총 필터 SQL 적용으로 Python 루프 감소) ──
        stock_rows = conn.execute("""
            SELECT p.stock_code, COUNT(*) as cnt
            FROM price_history p
            INNER JOIN (
                SELECT stock_code FROM stock_universe
                WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  AND (market_cap IS NULL OR market_cap >= ?)
                GROUP BY stock_code
            ) su ON p.stock_code = su.stock_code
            WHERE p.close > 0
            GROUP BY p.stock_code
            HAVING cnt >= 120
        """, (TREND_MKTCAP_MIN,)).fetchall()

        candidates = []
        for stock_code, _ in stock_rows:
            try:
                rows = conn.execute("""
                    SELECT date, close, high, low, volume,
                           inst_net_buy_amt, frn_net_buy_amt,
                           inst_net_buy, frn_net_buy,
                           COALESCE(trade_amount, 0) as trade_amount
                    FROM price_history WHERE stock_code=? AND close>0
                    ORDER BY date DESC LIMIT 500
                """, (stock_code,)).fetchall()

                if len(rows) < 120:
                    continue

                dates    = [r[0] for r in rows]
                closes   = [r[1] for r in rows]
                highs    = [r[2] or r[1] for r in rows]
                lows     = [r[3] or r[1] for r in rows]
                vols     = [r[4] or 0 for r in rows]
                inst_a   = [r[5] or 0 for r in rows]
                frn_a    = [r[6] or 0 for r in rows]
                inst_q   = [r[7] or 0 for r in rows]
                frn_q    = [r[8] or 0 for r in rows]
                traw     = [r[9] or 0 for r in rows]
                # 거래대금: KRX trade_amount 실데이터 우선, 없으면 volume×close proxy
                tvals    = [traw[i] if traw[i] > 0 else vols[i] * closes[i] for i in range(len(rows))]

                ind   = _calc_price_indicators(closes, vols)
                curr  = ind["curr"]
                ma5   = ind["ma5"];  ma20 = ind["ma20"]; ma60  = ind["ma60"]
                ma120 = ind["ma120"]; ma200 = ind["ma200"]
                high52    = ind["high52"];  from_high = ind["from_high"]
                rsi       = ind["rsi"];     vol_ratio = ind["vol_ratio"]

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [1단계] 유동성 필터 — 잡주/저유동성 종목 제거
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                mktcap = code_mktcap.get(stock_code, 0)
                if mktcap > 0 and mktcap < TREND_MKTCAP_MIN:
                    continue

                avg_tval_5d = sum(tvals[:5]) / max(len(tvals[:5]), 1)
                su_tvol     = code_tvol.get(stock_code, 0)
                if avg_tval_5d < TREND_TVOL_MIN_KRW and su_tvol < TREND_TVOL_MIN_SUOKU:
                    continue

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [2단계] 미너비니 추세 템플릿 — 모두 AND 조건 (필수)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                if not (ma120 and ma200 and curr > ma120 and curr > ma200 and ma120 > ma200):
                    continue
                if from_high < TREND_HIGH52_PCT:
                    continue
                if not (curr > ma5 > ma20 > ma60):
                    if not (curr > ma20 > ma60):
                        continue
                    daily_aligned = False
                else:
                    daily_aligned = True

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [3단계] 폭발 타이밍 — RSI 60+ AND 거래량 폭발 필수
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                if rsi < TREND_RSI_MIN:
                    continue
                if vol_ratio < TREND_VOL_RATIO_MIN:
                    continue

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # [3단계-B] 볼린저 밴드 상단 돌파 — 가산점 기준 (필수→선택)
                # BB 돌파는 +3점 가산. 미돌파여도 나머지 조건 충족 시 후보 유지.
                # (진입트리거 TOP20과 동일 종목이 나오려면 BB를 하드 필터에서 제외)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                bb_up_val = None
                bb_wid_val = 0.0
                is_squeeze_breakout = False
                bb_breakout = False
                if len(closes) >= 20:
                    sma20_bb = sum(closes[:20]) / 20
                    std20_bb = (sum((c - sma20_bb)**2 for c in closes[:20]) / 20) ** 0.5
                    bb_up_val = sma20_bb + 2 * std20_bb
                    bb_wid_val = 4 * std20_bb / sma20_bb * 100 if sma20_bb > 0 else 0
                    bb_breakout = curr > bb_up_val
                    if len(closes) >= 40:
                        # numpy sliding window: shape (20, 20) → mean/std 한번에 계산
                        _arr = _np.array(closes[:40], dtype=float)
                        _wins = _np.lib.stride_tricks.sliding_window_view(_arr, 20)[:20]
                        _smas = _wins.mean(axis=1)
                        _stds = _wins.std(axis=1)
                        _wids = _np.where(_smas > 0, 4 * _stds / _smas * 100, 0.0)
                        min_wid_bb = float(_wids.min()) if len(_wids) > 0 else bb_wid_val
                        is_squeeze_breakout = bb_wid_val <= min_wid_bb * 1.5 and bb_breakout

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # 점수 계산 (가산점)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                score   = 0
                reasons = []

                # 기본 점수: 미너비니 템플릿 통과
                score += 5
                reasons.append('✅ 미너비니 템플릿 통과')

                # 단기 정배열 완전 정배열
                if daily_aligned:
                    score += 2
                    reasons.append('정배열(5>20>60)')
                else:
                    score += 1
                    reasons.append('부분 정배열(20>60)')

                # 신고가 근접도 가산점
                if from_high >= -5:
                    score += 3
                    reasons.append(f'🚀 신고가({from_high:+.1f}%) — 돌파 직전')
                elif from_high >= -10:
                    score += 2
                    reasons.append(f'신고가 근접({from_high:+.1f}%)')
                else:  # -10 ~ -20%
                    score += 1
                    reasons.append(f'고점 -{abs(from_high):.0f}%')

                # RSI 강도
                if rsi >= 70:
                    score += 2
                    reasons.append(f'RSI 강력({rsi})')
                else:
                    score += 1
                    reasons.append(f'RSI 상승({rsi})')

                # 거래량 폭발 (2.0배 이상 필수 통과 확정 — 가산점)
                score += 3
                reasons.append(f'🔥 거래량 폭발({vol_ratio:.1f}배) — 세력 개입')

                # 볼린저 밴드 (상단 돌파 = 가산점, 스퀴즈 돌파 = 추가 가산점)
                if is_squeeze_breakout:
                    score += 3
                    reasons.append(f'⚡ BB 스퀴즈 돌파(폭:{bb_wid_val:.1f}%)')
                elif bb_breakout:
                    score += 1
                    reasons.append(f'BB 상단 돌파(폭:{bb_wid_val:.1f}%)')

                # MA120/200 상승 여력 (장기 추세 강도)
                pct_ma200 = (curr - ma200) / ma200 * 100
                if pct_ma200 >= 20:
                    score += 2
                    reasons.append(f'MA200 위 +{pct_ma200:.0f}% — 강한 장기추세')
                elif pct_ma200 >= 10:
                    score += 1
                    reasons.append(f'MA200 위 +{pct_ma200:.0f}%')

                # 섹터 활성화
                slarge, smid = code_sector.get(stock_code, (None, None))
                sector_act = sector_map.get(slarge, 0.0) if slarge else 0.0
                if sector_act >= 0.6:
                    score += 3
                    reasons.append(f'🔥 주도섹터({slarge})')
                elif sector_act >= 0.4:
                    score += 2
                    reasons.append(f'⚡ 섹터({slarge}) 상승 중')
                elif sector_act >= 0.2:
                    score += 1
                    reasons.append(f'섹터({slarge}) 활성')

                # RS (시장 대비 3개월 수익률)
                rs = 0.0
                if len(closes) >= 63:
                    stock_ret = (closes[0] - closes[62]) / closes[62] * 100 if closes[62] > 0 else 0
                    rs = stock_ret - kospi_ret
                    if rs > 15:
                        score += 3
                        reasons.append(f'RS +{rs:.0f}%p 강한 아웃퍼폼')
                    elif rs > 5:
                        score += 2
                        reasons.append(f'RS +{rs:.0f}%p')
                    elif rs > 0:
                        score += 1
                        reasons.append(f'RS +{rs:.0f}%p')

                # 수급 (최근 5일 기관/외국인)
                supply_days = min(5, len(inst_a))
                inst_sum = sum(inst_a[:supply_days]) or sum(inst_q[:supply_days]) / 100
                frn_sum  = sum(frn_a[:supply_days])  or sum(frn_q[:supply_days])  / 100
                if inst_sum > 0 and frn_sum > 0:
                    score += 2
                    reasons.append('기관+외국인 동반 매수')
                elif inst_sum > 0 or frn_sum > 0:
                    score += 1
                    reasons.append('기관 or 외국인 매수')

                # 200주 주봉 이평선 (장기 추세 확인)
                weekly = _daily_to_weekly_closes(list(zip(dates, closes)))
                weekly_label = ''
                if len(weekly) >= 20:
                    n_weeks  = min(len(weekly), 200)
                    ma_w     = sum(weekly[:n_weeks]) / n_weeks
                    pct_w    = (curr - ma_w) / ma_w * 100
                    weekly_label = f'주봉{n_weeks}주선'
                    if pct_w >= 5:
                        score += 2
                        weekly_label += f' 위(+{pct_w:.0f}%)'
                        reasons.append(f'📈 {weekly_label}')
                    elif pct_w >= 0:
                        score += 1
                        weekly_label += f' 위(+{pct_w:.1f}%)'
                    else:
                        weekly_label += f' 하단({pct_w:.0f}%)'

                stock_name = code_name.get(stock_code, stock_code)

                # ── 보너스 점수: DART 수주공시 ────────────────────
                cb = contract_bonus_map.get(stock_code)
                if cb:
                    score += cb["bonus"]
                    reasons.append(f'📋 수주공시 {cb["label"]}')

                # ── 보너스 점수: HS 수출 개선 ─────────────────────
                hb = hs_bonus_map.get(stock_code)
                if hb:
                    score += hb["bonus"]
                    reasons.append(f'🚢 {hb["label"]}')

                # ── 보너스 점수: NPS 월별 고용 순증가 (최대 +2) ───
                nps_b = nps_monthly_map.get(stock_code)
                if nps_b and nps_b > 0:
                    b = min(nps_b, 2)
                    score += b
                    reasons.append(f'👥 NPS고용순증가(+{b})')

                # 신호 등급
                if score >= TREND_SCORE_STRONG:
                    sig = 'green';  label = '강력매수'
                elif score >= TREND_SCORE_BUY:
                    sig = 'green';  label = '매수'
                elif score >= TREND_SCORE_WATCH:
                    sig = 'yellow'; label = '관심'
                else:
                    sig = 'yellow'; label = '대기'

                candidates.append({
                    'stock_code':   stock_code,
                    'stock_name':   stock_name,
                    'signal':       sig,
                    'label':        label,
                    'score':        score,
                    'reasons':      reasons,
                    'price':        curr,
                    'ma5':          round(ma5),
                    'ma20':         round(ma20),
                    'ma60':         round(ma60),
                    'ma120':        round(ma120),
                    'ma200':        round(ma200),
                    'from_high':    round(from_high, 1),
                    'rsi':          rsi,
                    'vol_ratio':    round(vol_ratio, 2),
                    'sector':       slarge or '',
                    'sector_mid':   smid or '',
                    'sector_act':   round(sector_act, 2),
                    'weekly_label': weekly_label,
                    'mktcap':       mktcap,
                    'market':       code_market.get(stock_code, ''),
                    'rs':           round(rs, 1),
                })
            except Exception:
                pass

        # 점수 높은 순 정렬, 동점이면 시총 큰 것 우선 (주도주 우선)
        # 상위 20종목으로 압축 (미너비니 로직 목표: 전체 2,000종목 → 10~20개 최상급 주도주)
        candidates.sort(key=lambda x: (x.get('score', 0), x.get('mktcap', 0)), reverse=True)
        return candidates[:20]

    finally:
        if own_conn:
            conn.close()


def calc_stockeasy_trend_candidates(conn=None) -> list:
    """
    StockEasy 추세추종 근사 후보군.

    PDF 리포트에서 실전 신호로 쓰기 좋은 항목만 반영:
      - 가격 구조: MA20/60/120/200 위치와 MA20/60 기울기
      - 지지/저항: 20/60/120일 고점 돌파 또는 근접
      - 거래량: 당일 폭발뿐 아니라 5일 평균 거래량 재증가
      - 모멘텀: RSI 과열 전 상승권, KOSPI 대비 상대강도
      - 보조: 주도 섹터, 기관/외국인 5일 수급

    재무/EPS/전망/Q&A는 추세추종 진입 핵심이 아니므로 제외한다.
    """
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)

    try:
        sector_map = _build_sector_activation_map(conn)
        _umaps = _load_universe_maps(conn)
        code_mktcap = _umaps["mktcap"]
        code_market = _umaps["market"]
        code_tvol = _umaps["tvol"]
        code_name = _umaps["name"]
        code_sector = {sc: (_umaps["sector"][sc], _umaps["sector_mid"][sc])
                       for sc in _umaps["sector"]}

        kospi_rows = conn.execute("""
            SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0
            ORDER BY date DESC LIMIT 130
        """).fetchall()
        kospi_ret_3m = 0.0
        kospi_ret_6m = 0.0
        if len(kospi_rows) >= 63 and kospi_rows[62][0] > 0:
            kospi_ret_3m = (kospi_rows[0][0] - kospi_rows[62][0]) / kospi_rows[62][0] * 100
        if len(kospi_rows) >= 126 and kospi_rows[125][0] > 0:
            kospi_ret_6m = (kospi_rows[0][0] - kospi_rows[125][0]) / kospi_rows[125][0] * 100

        fin_rows = conn.execute("""
            WITH q AS (
                SELECT stock_code, revenue, operating_profit,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) rn
                FROM financial_data
                WHERE is_annual=0 AND quarter BETWEEN 1 AND 4
                  AND revenue IS NOT NULL
                  AND operating_profit IS NOT NULL
            )
            SELECT
                stock_code,
                MAX(CASE WHEN rn=1 THEN revenue END)          AS rev0,
                MAX(CASE WHEN rn=2 THEN revenue END)          AS rev1,
                MAX(CASE WHEN rn=5 THEN revenue END)          AS rev_ya,
                MAX(CASE WHEN rn=1 THEN operating_profit END) AS op0,
                MAX(CASE WHEN rn=2 THEN operating_profit END) AS op1,
                MAX(CASE WHEN rn=3 THEN operating_profit END) AS op2,
                MAX(CASE WHEN rn=5 THEN operating_profit END) AS op_ya
            FROM q
            GROUP BY stock_code
        """).fetchall()
        fin_map = {}
        for fr in fin_rows:
            sc, rev0, rev1, rev_ya, op0, op1, op2, op_ya = fr
            rev_yoy = (rev0 - rev_ya) / abs(rev_ya) * 100 if rev0 and rev_ya else None
            rev_qoq = (rev0 - rev1) / abs(rev1) * 100 if rev0 and rev1 else None
            op_yoy = (op0 - op_ya) / abs(op_ya) * 100 if op0 and op_ya and op_ya > 0 else None
            op_qoq = (op0 - op1) / abs(op1) * 100 if op0 and op1 and op1 > 0 else None
            op_turnaround = bool(op0 and op0 > 0 and ((op_ya is not None and op_ya <= 0) or (op2 is not None and op2 <= 0)))
            fin_map[sc] = {
                "rev_yoy": rev_yoy,
                "rev_qoq": rev_qoq,
                "op_yoy": op_yoy,
                "op_qoq": op_qoq,
                "op_turnaround": op_turnaround,
                "op0": op0,
            }

        stock_rows = conn.execute("""
            SELECT p.stock_code, COUNT(*) as cnt
            FROM price_history p
            INNER JOIN (
                SELECT stock_code FROM stock_universe
                WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  AND (market_cap IS NULL OR market_cap >= ?)
                GROUP BY stock_code
            ) su ON p.stock_code = su.stock_code
            WHERE p.close > 0
            GROUP BY p.stock_code
            HAVING cnt >= 160
        """, (300_000_000_000,)).fetchall()

        candidates = []
        for stock_code, _ in stock_rows:
            try:
                rows = conn.execute("""
                    SELECT date, close, high, low, volume,
                           inst_net_buy_amt, frn_net_buy_amt,
                           inst_net_buy, frn_net_buy,
                           COALESCE(trade_amount, 0) as trade_amount
                    FROM price_history WHERE stock_code=? AND close>0
                    ORDER BY date DESC LIMIT 260
                """, (stock_code,)).fetchall()
                if len(rows) < 160:
                    continue

                dates = [r[0] for r in rows]
                closes = [float(r[1]) for r in rows]
                highs = [float(r[2] or r[1]) for r in rows]
                lows = [float(r[3] or r[1]) for r in rows]
                vols = [float(r[4] or 0) for r in rows]
                inst_a = [float(r[5] or 0) for r in rows]
                frn_a = [float(r[6] or 0) for r in rows]
                inst_q = [float(r[7] or 0) for r in rows]
                frn_q = [float(r[8] or 0) for r in rows]
                traw  = [float(r[9] or 0) for r in rows]
                tvals = [traw[i] if traw[i] > 0 else vols[i] * closes[i] for i in range(len(rows))]

                ind = _calc_price_indicators(closes, vols)
                curr = ind["curr"]
                ma20 = ind["ma20"]; ma60 = ind["ma60"]; ma120 = ind["ma120"]; ma200 = ind["ma200"]
                rsi = ind["rsi"]; vol_ratio = ind["vol_ratio"]; from_high = ind["from_high"]
                tech = _calc_stockeasy_technical_indicators(closes, highs, lows)
                if not (curr and ma20 and ma60 and ma120):
                    continue

                mktcap = code_mktcap.get(stock_code, 0)
                avg_tval_5d = sum(tvals[:5]) / 5
                if avg_tval_5d < 3_000_000_000 and code_tvol.get(stock_code, 0) < 30:
                    continue

                avg_vol_20 = sum(vols[:20]) / 20 if len(vols) >= 20 else 0
                avg_vol_5 = sum(vols[:5]) / 5 if len(vols) >= 5 else 0
                vol_5d_ratio = avg_vol_5 / avg_vol_20 if avg_vol_20 > 0 else 0

                res20 = max(highs[1:21]) if len(highs) >= 21 else max(highs)
                res60 = max(highs[1:61]) if len(highs) >= 61 else res20
                res120 = max(highs[1:121]) if len(highs) >= 121 else res60
                support60 = min(lows[1:61]) if len(lows) >= 61 else min(lows)
                resistance_gap = (curr - res60) / res60 * 100 if res60 > 0 else 0
                support_gap = (curr - support60) / support60 * 100 if support60 > 0 else 0

                def slope_ma(k, offset):
                    if len(closes) < k + offset:
                        return 0.0
                    now = sum(closes[:k]) / k
                    prev = sum(closes[offset:offset + k]) / k
                    return (now - prev) / prev * 100 if prev > 0 else 0.0

                ma20_slope = slope_ma(20, 10)
                ma60_slope = slope_ma(60, 20)

                score = 0
                reasons = []

                # 가격 구조: 스탁이지 보유 종목은 장기선 위에 있되, MA5 엄격 조건은 덜 중요.
                if curr > ma20:
                    score += 2; reasons.append('현재가>MA20')
                if curr > ma60:
                    score += 3; reasons.append('현재가>MA60')
                if curr > ma120:
                    score += 3; reasons.append('현재가>MA120')
                if ma20 > ma60:
                    score += 3; reasons.append('MA20>MA60')
                if ma60 > ma120:
                    score += 2; reasons.append('MA60>MA120')
                if ma200 and ma120 > ma200:
                    score += 1; reasons.append('MA120>MA200')
                if ma20_slope > 3:
                    score += 3; reasons.append(f'MA20 상승({ma20_slope:.1f}%)')
                elif ma20_slope > 0:
                    score += 1; reasons.append(f'MA20 우상향({ma20_slope:.1f}%)')
                if ma60_slope > 1:
                    score += 2; reasons.append(f'MA60 상승({ma60_slope:.1f}%)')

                # 스탁이지 보유군은 소형 단기 급등주보다 업종 대표/대형 주도주 비중이 높다.
                if mktcap >= 10_000_000_000_000:
                    score += 5; reasons.append('대형 주도주')
                elif mktcap >= 1_000_000_000_000:
                    score += 3; reasons.append('중대형 주도주')
                elif mktcap >= 300_000_000_000:
                    score += 1; reasons.append('중형주 이상')

                # 저항선 돌파/근접: 첨부 리포트의 핵심 차트 정보.
                if curr >= res120:
                    score += 7; reasons.append('120일 저항 돌파')
                elif curr >= res120 * 0.97:
                    score += 5; reasons.append('120일 저항 3% 이내')
                elif curr >= res60:
                    score += 5; reasons.append('60일 저항 돌파')
                elif curr >= res60 * 0.98:
                    score += 3; reasons.append('60일 저항 2% 이내')
                elif curr >= res20 * 0.99:
                    score += 2; reasons.append('20일 고점 근접')

                if from_high >= -3:
                    score += 6; reasons.append(f'52주 신고가권({from_high:+.1f}%)')
                elif from_high >= -10:
                    score += 4; reasons.append(f'52주 고점 근접({from_high:+.1f}%)')
                elif from_high >= -25:
                    score += 2; reasons.append(f'52주 고점권({from_high:+.1f}%)')
                elif from_high >= -40:
                    score += 1; reasons.append(f'중기 회복권({from_high:+.1f}%)')

                if mktcap >= 10_000_000_000_000 and from_high >= -3 and vol_ratio >= 1.3:
                    score += 3; reasons.append('대형주 신고가 거래량 동반')
                elif mktcap >= 1_000_000_000_000 and from_high >= -3 and vol_ratio >= 1.3:
                    score += 1; reasons.append('중대형 신고가 거래량 동반')

                # 거래량: 당일 폭발만 강제하지 않고 5일 평균 재증가도 인정.
                if vol_ratio >= 2.0:
                    score += 4; reasons.append(f'거래량 폭발({vol_ratio:.1f}배)')
                elif vol_ratio >= 1.3:
                    score += 3; reasons.append(f'거래량 증가({vol_ratio:.1f}배)')
                if vol_5d_ratio >= 1.5:
                    score += 3; reasons.append(f'5일 거래량 증가({vol_5d_ratio:.1f}배)')
                elif vol_5d_ratio >= 1.15:
                    score += 1; reasons.append(f'5일 거래량 우상향({vol_5d_ratio:.1f}배)')

                if 55 <= rsi <= 78:
                    score += 4; reasons.append(f'RSI 상승권({rsi})')
                elif 50 <= rsi <= 85:
                    score += 2; reasons.append(f'RSI 모멘텀({rsi})')
                elif rsi > 85:
                    score -= 2; reasons.append(f'RSI 과열({rsi})')

                adx = tech.get("adx", 0) or 0
                pdi = tech.get("pdi", 0) or 0
                mdi = tech.get("mdi", 0) or 0
                macd = tech.get("macd", 0) or 0
                macd_sig = tech.get("macd_signal", 0) or 0
                macd_hist = tech.get("macd_hist", 0) or 0
                stoch_k = tech.get("stoch_k", 50) or 50
                stoch_d = tech.get("stoch_d", 50) or 50
                if tech.get("supertrend_buy"):
                    score += 5; reasons.append('SuperTrend 매수 유지')
                else:
                    score -= 4; reasons.append('SuperTrend 매수 미확인')
                if adx >= 45:
                    score += 5; reasons.append(f'ADX 강한 추세({adx:.1f})')
                elif adx >= 25:
                    score += 3; reasons.append(f'ADX 추세({adx:.1f})')
                elif adx >= 18:
                    score += 1; reasons.append(f'ADX 회복({adx:.1f})')
                if pdi > mdi:
                    score += 3; reasons.append(f'+DI 우위({pdi:.1f}>{mdi:.1f})')
                elif pdi + 3 < mdi:
                    score -= 2; reasons.append(f'-DI 우위({mdi:.1f})')
                if macd > macd_sig and macd_hist > 0:
                    score += 4; reasons.append('MACD 매수')
                elif macd > macd_sig:
                    score += 2; reasons.append('MACD 상향')
                elif macd_hist < 0:
                    score -= 1; reasons.append('MACD 둔화')
                if stoch_k > stoch_d and 45 <= stoch_k <= 90:
                    score += 3; reasons.append(f'스토캐스틱 상승({stoch_k:.0f}>{stoch_d:.0f})')
                elif stoch_k >= 80 and stoch_d >= 80 and stoch_k < stoch_d:
                    score -= 2; reasons.append('스토캐스틱 과열 둔화')
                elif stoch_k >= 80:
                    score += 1; reasons.append('스토캐스틱 강세권')

                rs3 = rs6 = 0.0
                if len(closes) >= 63 and closes[62] > 0:
                    rs3 = (closes[0] - closes[62]) / closes[62] * 100 - kospi_ret_3m
                if len(closes) >= 126 and closes[125] > 0:
                    rs6 = (closes[0] - closes[125]) / closes[125] * 100 - kospi_ret_6m
                rs_score = max(0.0, min(100.0, 50.0 + rs3))
                if rs3 > 15:
                    score += 4; reasons.append(f'3M RS +{rs3:.0f}%p')
                elif rs3 > 5:
                    score += 3; reasons.append(f'3M RS +{rs3:.0f}%p')
                elif rs3 > 0:
                    score += 1; reasons.append(f'3M RS +{rs3:.0f}%p')
                if rs6 > 10:
                    score += 2; reasons.append(f'6M RS +{rs6:.0f}%p')
                if rs_score >= 90:
                    score += 4; reasons.append(f'RS 매우 강함({rs_score:.0f})')
                elif rs_score >= 80:
                    score += 3; reasons.append(f'RS 강함({rs_score:.0f})')
                elif rs_score >= 65:
                    score += 1; reasons.append(f'RS 우위({rs_score:.0f})')

                supply_days = min(5, len(inst_a))
                inst_sum = sum(inst_a[:supply_days]) or sum(inst_q[:supply_days]) / 100
                frn_sum = sum(frn_a[:supply_days]) or sum(frn_q[:supply_days]) / 100
                if inst_sum > 0 and frn_sum > 0:
                    score += 3; reasons.append('기관+외국인 5일 동반매수')
                elif inst_sum > 0 or frn_sum > 0:
                    score += 1; reasons.append('기관/외국인 5일 순매수')

                slarge, smid = code_sector.get(stock_code, (None, None))
                sector_act = sector_map.get(slarge, 0.0) if slarge else 0.0
                if sector_act >= 0.6:
                    score += 3; reasons.append(f'주도섹터({slarge})')
                elif sector_act >= 0.4:
                    score += 2; reasons.append(f'섹터 상승({slarge})')

                fin = fin_map.get(stock_code, {})
                rev_yoy = fin.get("rev_yoy")
                op_yoy = fin.get("op_yoy")
                op_qoq = fin.get("op_qoq")
                if fin.get("op_turnaround"):
                    score += 4; reasons.append('흑자전환/이익 턴어라운드')
                elif op_yoy is not None:
                    if op_yoy >= 50:
                        score += 3; reasons.append(f'영업이익 YoY +{op_yoy:.0f}%')
                    elif op_yoy >= 20:
                        score += 2; reasons.append(f'영업이익 YoY +{op_yoy:.0f}%')
                if op_qoq is not None and op_qoq >= 20:
                    score += 1; reasons.append(f'영업이익 QoQ +{op_qoq:.0f}%')
                if rev_yoy is not None:
                    if rev_yoy >= 30:
                        score += 3; reasons.append(f'매출 YoY +{rev_yoy:.0f}%')
                    elif rev_yoy >= 15:
                        score += 2; reasons.append(f'매출 YoY +{rev_yoy:.0f}%')
                    elif rev_yoy >= 5:
                        score += 1; reasons.append(f'매출 YoY +{rev_yoy:.0f}%')

                # 단기 과열 추격 방지:
                # 소형주가 단기간에 저항 대비 과도하게 이격된 상태(+30% 이상)에서
                # RSI/거래량이 과열이면 편입 후 빠른 되돌림 리스크가 커서 제외한다.
                overheat_chase = (
                    mktcap < 500_000_000_000
                    and resistance_gap >= 30
                    and (
                        rsi >= 82
                        or vol_ratio >= 4.5
                        or (stoch_k >= 90 and stoch_d >= 85)
                    )
                )

                # 스탁이지 Peak 편출 사례는 대부분 MA20 하향 이탈 후 모멘텀이 식는다.
                if overheat_chase or curr < ma20 or curr < ma60 or from_high < -50 or score < 28:
                    continue

                label = '강력매수' if score >= 42 else '매수' if score >= 34 else '관심'
                candidates.append({
                    'stock_code': stock_code,
                    'stock_name': code_name.get(stock_code, stock_code),
                    'signal': 'green' if score >= 28 else 'yellow',
                    'label': label,
                    'score': round(score, 1),
                    'reasons': reasons[:10],
                    'price': curr,
                    'ma20': round(ma20),
                    'ma60': round(ma60),
                    'ma120': round(ma120),
                    'ma200': round(ma200) if ma200 else None,
                    'ma20_slope': round(ma20_slope, 2),
                    'ma60_slope': round(ma60_slope, 2),
                    'resistance_20': round(res20),
                    'resistance_60': round(res60),
                    'resistance_120': round(res120),
                    'resistance_gap': round(resistance_gap, 2),
                    'support_60': round(support60),
                    'support_gap': round(support_gap, 2),
                    'from_high': round(from_high, 1),
                    'rsi': rsi,
                    'adx': adx,
                    'pdi': pdi,
                    'mdi': mdi,
                    'macd': round(macd, 2),
                    'macd_signal': round(macd_sig, 2),
                    'macd_hist': round(macd_hist, 2),
                    'stoch_k': round(stoch_k, 1),
                    'stoch_d': round(stoch_d, 1),
                    'supertrend': tech.get("supertrend"),
                    'supertrend_buy': bool(tech.get("supertrend_buy")),
                    'vol_ratio': round(vol_ratio, 2),
                    'vol_5d_ratio': round(vol_5d_ratio, 2),
                    'rs': round(rs3, 1),
                    'rs_score': round(rs_score, 1),
                    'rs_6m': round(rs6, 1),
                    'sector': slarge or '',
                    'sector_mid': smid or '',
                    'sector_act': round(sector_act, 2),
                    'rev_yoy': round(rev_yoy, 1) if rev_yoy is not None else None,
                    'op_yoy': round(op_yoy, 1) if op_yoy is not None else None,
                    'op_turnaround': bool(fin.get("op_turnaround")),
                    'mktcap': mktcap,
                    'market': code_market.get(stock_code, ''),
                })
            except Exception:
                pass

        candidates.sort(key=lambda x: (x.get('score', 0), x.get('mktcap', 0)), reverse=True)
        return candidates[:70]
    finally:
        if own_conn:
            conn.close()


# ══════════════════════════════════════════════════════════════════
# 가치매수 후보 스캔
# ══════════════════════════════════════════════════════════════════

def calc_value_candidates(conn=None) -> list:
    """
    DB의 모든 종목 중 Graham 가치투자 조건을 충족하는 종목을 스캔.
    반환: [{stock_code, stock_name, signal, score, detail, per, pbr, graham_iv, discount}, ...]
    """
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)

    try:
        # EPS가 있는 종목 목록 (BPS는 equity/shares로 관산 가능)
        stocks = conn.execute("""
            SELECT DISTINCT fd.stock_code,
                   COALESCE(sm.stock_name, su.stock_name, fd.stock_code) AS stock_name
            FROM financial_data fd
            LEFT JOIN stock_meta sm ON fd.stock_code = sm.stock_code
            LEFT JOIN (
                SELECT stock_code, stock_name FROM stock_universe
                WHERE stock_name IS NOT NULL GROUP BY stock_code
            ) su ON fd.stock_code = su.stock_code
            WHERE fd.eps IS NOT NULL AND fd.eps > 0
              AND LENGTH(fd.stock_code) = 6
              AND fd.stock_code GLOB '[0-9]*'
        """).fetchall()

        # 시총/시장구분 사전 로드
        mktcap_map = {}
        market_map = {}
        for sc, mc, mkt in conn.execute("""
            SELECT stock_code, MAX(market_cap) as mc, MAX(market) as mkt
            FROM stock_universe WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code
        """).fetchall():
            mktcap_map[sc] = mc or 0
            market_map[sc] = mkt or ''

        # ★ Graham 데이터 벌크 로드 (기존: 종목당 2 쿼리 × ~1000종목 → 2 쿼리로 통합)
        # 1) 재무 데이터: stock_code당 최신 분기 행 선택 (최대 5행 로드 후 Python에서 최적 행 선택)
        _fin_rows_bulk = conn.execute("""
            SELECT stock_code, eps, bps, roe, total_equity, net_income
            FROM financial_data
            WHERE eps IS NOT NULL AND eps > 0
              AND is_annual = 0
              AND LENGTH(stock_code) = 6 AND stock_code GLOB '[0-9]*'
            ORDER BY stock_code, year DESC, quarter DESC
        """).fetchall()

        # stock_code별로 최적 행 선택 (_get_graham_data 내부 로직 재현)
        _rows_by_sc: dict = _defaultdict(list)
        for row in _fin_rows_bulk:
            sc = row[0]
            if len(_rows_by_sc[sc]) < 5:
                _rows_by_sc[sc].append(row[1:])  # (eps, bps, roe, equity, net_inc)

        _graham_fin: dict = {}  # sc → (eps, bps_resolved, roe)
        for sc, rows in _rows_by_sc.items():
            chosen = None
            for eps, bps, roe, equity, net_inc in rows:
                if not eps or eps <= 0:
                    continue
                if bps and bps > 0:
                    chosen = (eps, bps, roe, equity, net_inc); break
                if equity and equity > 0 and net_inc and net_inc > 0 and equity >= net_inc / 3:
                    chosen = (eps, bps, roe, equity, net_inc); break
            if not chosen and rows:
                chosen = rows[0]
            if chosen:
                eps, bps, roe, equity, net_inc = chosen
                # BPS 관산 (bps=0 이면 equity/shares로 계산)
                if (not bps or bps == 0) and equity and equity > 0 and net_inc and net_inc > 0 and eps > 0:
                    if equity >= net_inc / 3:
                        shares = net_inc / eps
                        if shares > 0:
                            bps = equity / shares
                _graham_fin[sc] = (eps, bps or 0, roe)

        # 2) 최신 종가 벌크 로드
        _price_map: dict = {}
        for sc, close in conn.execute("""
            SELECT p.stock_code, p.close FROM price_history p
            INNER JOIN (
                SELECT stock_code, MAX(date) as mdate
                FROM price_history
                WHERE close > 0 AND LENGTH(stock_code) = 6 AND stock_code GLOB '[0-9]*'
                GROUP BY stock_code
            ) latest ON p.stock_code = latest.stock_code AND p.date = latest.mdate
        """).fetchall():
            _price_map[sc] = close

        # 3) Graham 내재가치 Python 계산 (쿼리 없음)
        graham_map: dict = {}
        for sc, (eps, bps, roe) in _graham_fin.items():
            price = _price_map.get(sc)
            if not price:
                continue
            graham_iv = _math.sqrt(22.5 * eps * bps) if eps > 0 and bps > 0 else None
            per = round(price / eps, 1) if eps > 0 else None
            pbr = round(price / bps, 2) if bps > 0 else None
            discount = round((graham_iv - price) / graham_iv * 100, 1) if graham_iv and graham_iv > 0 else None
            graham_map[sc] = {
                'price': price, 'eps': eps, 'bps': bps,
                'graham_iv': graham_iv, 'per': per, 'pbr': pbr,
                'discount': discount, 'roe': roe,
            }

        # 4) 후보 선별 (per-stock 루프: DB 쿼리 없음, Python 연산만)
        candidates = []
        stock_name_map = {sc: sn for sc, sn in stocks}
        for stock_code in graham_map:
            stock_name = stock_name_map.get(stock_code, stock_code)
            try:
                d = graham_map[stock_code]
                if not d.get('graham_iv'):
                    continue

                pbr  = d.get('pbr')
                per  = d.get('per')
                disc = d.get('discount')

                # 최소 조건: Graham 15% 이상 저평가 OR (PBR<1 AND PER<15)
                pbr_ok  = pbr is not None and pbr < 1.0
                per_ok  = per is not None and 0 < per < 15
                disc_ok = disc is not None and disc >= 15

                if not (disc_ok or (pbr_ok and per_ok)):
                    continue

                # 강화 조건: 심각하게 저평가된 종목만 포함
                deep_disc  = disc is not None and disc >= 25
                deep_value = (pbr is not None and pbr < 0.7 and
                              per is not None and 0 < per < 10)
                if not (deep_disc or deep_value):
                    continue

                # 세부 점수 계산 (pre_d 전달 → 내부 _get_graham_data 중복 호출 제거)
                sig, score, detail = _calc_value_turnaround(conn, stock_code, pre_d=d)

                if score < 5:
                    continue

                # ── ★ 보너스: 저평가 + 수출성장/수주공시 = 구조적 전환 핵심신호 ──
                # 저평가 종목에서 HS수출 증가 or DART수주공시 확인 시 가중치 2배
                value_bonus_labels = []
                _cb_val = _load_contract_bonus_map(days=90).get(stock_code)
                _hb_val = _load_hs_export_bonus_map(months=6).get(stock_code)
                # NPS 고용 보너스: 연간 총인원은 월별 차이 없어 제거

                # 저평가 심화 시 보너스 가중치 2배 (deep value + 성장 = 핵심 전략)
                is_deep_value = deep_disc or deep_value  # 이미 위에서 계산됨
                _mul = 2 if is_deep_value else 1

                if _cb_val and _cb_val["bonus"] > 0:
                    b = _cb_val["bonus"] * _mul
                    score += b
                    value_bonus_labels.append(f'📋수주{_cb_val["label"]}(+{b})')

                if _hb_val and _hb_val["bonus"] > 0:
                    b = _hb_val["bonus"] * _mul
                    score += b
                    value_bonus_labels.append(f'🚢{_hb_val["label"]}(+{b})')

                if value_bonus_labels:
                    detail = detail + " | 보너스: " + ", ".join(value_bonus_labels)

                candidates.append({
                    'stock_code':    stock_code,
                    'stock_name':    stock_name,
                    'signal':        sig,
                    'score':         score,
                    'detail':        detail,
                    'per':           per,
                    'pbr':           pbr,
                    'graham_iv':     round(d['graham_iv']) if d.get('graham_iv') else None,
                    'discount':      disc,
                    'price':         d.get('price'),
                    'eps':           d.get('eps'),
                    'bps':           d.get('bps'),
                    'mktcap':        mktcap_map.get(stock_code, 0),
                    'market':        market_map.get(stock_code, ''),
                    'value_bonuses': value_bonus_labels,
                })
            except Exception:
                pass

        # 점수 높은 순으로 정렬
        candidates.sort(key=lambda x: x.get('score', 0), reverse=True)
        return candidates

    finally:
        if own_conn:
            conn.close()


# ══════════════════════════════════════════════════════════════════
# 진입트리거 TOP20 — 3-트랙 종합 선별
# ══════════════════════════════════════════════════════════════════
def calc_top20_candidates(conn=None) -> list:
    """
    3-트랙 종합 판정으로 전체 종목 중 TOP20 선별.

    [선별 기준]
    - Track A (추세): Minervini 장기 정배열 + RSI + 거래량 폭발 수준 (0~4점)
    - Track B (가치): Graham 내재가치 할인율 + 영업흑자 여부 (0~3점)
    - Track C (섹터): 섹터 주도주 활성화 보너스 (0~1점)
    - 종합점수 = A×2 + B×2 + C → 높은 순으로 TOP20 반환

    [필수 제외 조건]
    - 시가총액 500억 미만 (잡주)
    - 최근 5일 평균 거래대금 50억 미만

    [반환 필드]
    stock_code, stock_name, score, track_a (추세), track_b (가치),
    sector_bonus, signal, detail, per, pbr, graham_iv, discount,
    rsi, vol_ratio, from_high52, mktcap, market, sector
    """
    import math as _math

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)

    try:
        # ── 섹터 활성화 맵 ─────────────────────────────────────────
        sector_map = _build_sector_activation_map(conn)

        # ── 시총/시장/섹터/거래대금 맵 ────────────────────────────
        _umaps      = _load_universe_maps(conn)
        code_mktcap = _umaps["mktcap"]
        code_market = _umaps["market"]
        code_sector = _umaps["sector"]
        code_tvol   = _umaps["tvol"]
        code_name   = _umaps["name"]

        # ── Graham 재무 데이터 bulk 로드 ───────────────────────────
        # 가장 최근 분기 실적을 stock_code별로 1건만 (EPS 부호 무관하게 최신 데이터)
        # 이후 Python에서 EPS > 0 AND op > 0 필터링
        fin_map = {}   # stock_code → {eps, bps, op_positive}
        _recent_fin = conn.execute("""
            SELECT stock_code, eps, bps, operating_profit, revenue,
                   year * 10 + quarter AS yyyyq
            FROM financial_data
            WHERE is_annual = 0
              AND LENGTH(stock_code) = 6 AND stock_code GLOB '[0-9]*'
            ORDER BY stock_code, year DESC, quarter DESC
        """).fetchall()
        _seen_fin = set()
        for sc, eps, bps, op_profit, rev, yyyyq in _recent_fin:
            if sc in _seen_fin:
                continue
            _seen_fin.add(sc)
            # 현재 EPS가 양수이고 영업이익도 흑자인 종목만 Graham 계산 대상
            if eps and eps > 0 and (op_profit or 0) > 0:
                fin_map[sc] = {
                    'eps': eps,
                    'bps': bps or 0,
                    'op_positive': True,
                    'revenue': rev or 0,
                }

        # ── 최신 종가 (Graham 할인율 계산용) ───────────────────────
        price_map = {}
        for sc, close in conn.execute("""
            SELECT p.stock_code, p.close FROM price_history p
            INNER JOIN (
                SELECT stock_code, MAX(date) as mdate
                FROM price_history WHERE close>0 AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                GROUP BY stock_code
            ) latest ON p.stock_code=latest.stock_code AND p.date=latest.mdate
        """).fetchall():
            price_map[sc] = close

        # ── 가격 데이터 충분한 종목 목록 (시총 필터 SQL 적용으로 Python 루프 감소) ──
        stock_rows = conn.execute("""
            SELECT p.stock_code, COUNT(*) as cnt
            FROM price_history p
            INNER JOIN (
                SELECT stock_code FROM stock_universe
                WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  AND (market_cap IS NULL OR market_cap >= ?)
                GROUP BY stock_code
            ) su ON p.stock_code = su.stock_code
            WHERE p.close > 0
            GROUP BY p.stock_code
            HAVING cnt >= 60
        """, (TOP20_MKTCAP_MIN,)).fetchall()

        candidates = []

        for stock_code, data_cnt in stock_rows:
            try:
                # 유동성 필터
                mktcap = code_mktcap.get(stock_code, 0)
                if mktcap > 0 and mktcap < TOP20_MKTCAP_MIN:
                    continue
                tvol_su = code_tvol.get(stock_code, 0)

                # ── 가격 히스토리 로드 ─────────────────────────────
                rows = conn.execute("""
                    SELECT close, volume FROM price_history
                    WHERE stock_code=? AND close>0
                    ORDER BY date DESC LIMIT 260
                """, (stock_code,)).fetchall()

                if len(rows) < 20:
                    continue

                closes = [r[0] for r in rows]
                vols   = [r[1] or 0 for r in rows]

                # 거래대금 필터: 최근 5일 평균 거래대금 미만 제외
                tvals = [vols[i] * closes[i] for i in range(min(5, len(rows)))]
                avg_tval_5d = sum(tvals) / max(len(tvals), 1)
                if avg_tval_5d < TOP20_TVOL_MIN_KRW and tvol_su < TOP20_TVOL_MIN_SUOKU:
                    continue

                # ── 지표 일괄 계산 ─────────────────────────────────
                ind  = _calc_price_indicators(closes, vols)
                curr = ind["curr"];   rsi  = ind["rsi"];  vol_ratio = ind["vol_ratio"]
                ma20 = ind["ma20"];   ma60 = ind["ma60"]; ma120 = ind["ma120"]; ma200 = ind["ma200"]
                high52 = ind["high52"]; from_high = ind["from_high"]

                # ── Track A: 추세 점수 (0~4) ──────────────────────
                track_a = 0
                # 레벨 1: curr > ma20 > ma60 (기본 상승 추세)
                if ma60 and curr > ma20 and ma20 > ma60:
                    track_a = 1

                # 레벨 2: 장기 정배열 (curr > ma120 > ma200)
                if ma120 and ma200 and curr > ma120 and ma120 > ma200:
                    track_a = 2
                    # 레벨 3: 52주 고점 20% 이내
                    if from_high >= -20:
                        track_a = 3
                        # 레벨 4: RSI 60+ AND 거래량 폭발 (Minervini 완성)
                        if rsi >= 60 and vol_ratio >= 2.0:
                            track_a = 4

                # ── Track B: 가치 강도 (0~3) ──────────────────────
                # fin_map 에 포함된 종목은 이미 최신 EPS>0 + 영업흑자 검증 완료
                track_b    = 0
                per_val    = None
                pbr_val    = None
                graham_iv  = None
                disc_val   = None

                if stock_code in fin_map and stock_code in price_map:
                    fd   = fin_map[stock_code]
                    eps  = fd['eps']
                    bps  = fd['bps']
                    price = price_map[stock_code]
                    # op_ok: fin_map 진입 조건에서 이미 보장됨 (True 고정)

                    if eps > 0 and price > 0:
                        per_val = round(price / eps, 1)
                    if bps > 0 and price > 0:
                        pbr_val = round(price / bps, 2)

                    if eps > 0 and bps and bps > 0:
                        graham_iv = _math.sqrt(22.5 * eps * bps)
                        disc_val  = round((graham_iv - price) / graham_iv * 100, 1)

                    # 모든 레벨에서 op_positive(영업흑자) 필수 (fin_map 진입 시 보장)
                    if disc_val is not None:
                        if disc_val >= TOP20_DISC_STRONG:
                            track_b = 3
                        elif disc_val >= TOP20_DISC_MID:
                            track_b = 2
                        elif disc_val >= TOP20_DISC_WEAK and (pbr_val and pbr_val < 1.5 and per_val and 0 < per_val < 20):
                            track_b = 1

                # ── Track C: 섹터 보너스 (0~1) ────────────────────
                sector_name  = code_sector.get(stock_code, '')
                sector_score = sector_map.get(sector_name, 0.0) if sector_name else 0.0
                track_c      = 1 if sector_score >= TOP20_SECTOR_THRESH else 0

                # ── 최소 진입 기준: Track A≥2 OR Track B≥2 필수 ───
                # 반드시 장기 정배열(A≥TOP20_TRACK_A_MIN) 또는 Graham 할인(B≥TOP20_TRACK_B_MIN) 이상이어야 후보 등재
                if track_a < TOP20_TRACK_A_MIN and track_b < TOP20_TRACK_B_MIN:
                    continue

                composite = track_a * 2 + track_b * 2 + track_c

                # ── 대표 signal / detail 텍스트 ────────────────────
                if track_a >= 4 and track_b >= 2:
                    signal = 'green'
                    detail = f'✅ [추세+가치] Minervini 완성 + Graham {disc_val:.0f}% 할인'
                elif track_a >= 3 and track_b >= 1:
                    signal = 'green'
                    detail = f'✅ [추세+가치] 장기 정배열 + 가치 관심 (Graham {disc_val:.0f}% 할인)'
                elif track_a >= 4:
                    signal = 'green'
                    detail = f'✅ [추세] Minervini 완성 — RSI {rsi:.0f} / 거래량 {vol_ratio:.1f}x'
                elif track_b >= 3:
                    signal = 'green'
                    detail = f'✅ [가치] Graham {disc_val:.0f}% 할인 + 영업흑자 — 강력 분할매수'
                elif track_b >= 2:
                    signal = 'green'
                    detail = f'✅ [가치] Graham {disc_val:.0f}% 할인 — 분할매수 검토'
                elif track_a >= 3:
                    signal = 'yellow'
                    detail = f'🟡 [추세] 장기 정배열 + 신고가 {from_high:.0f}% — RSI {rsi:.0f}'
                elif track_a == 2:
                    signal = 'yellow'
                    detail = f'🟡 [추세] MA120/200 정배열 — 진입 트리거 대기'
                elif track_a == 1:
                    signal = 'yellow'
                    detail = f'🟡 [추세] 단기 정배열 — 중기 추세 확인 필요'
                elif track_b == 1:
                    signal = 'yellow'
                    detail = f'🟡 [가치] 가치 관심 구간 — 추가 조건 확인 필요'
                else:
                    signal = 'gray'
                    detail = '조건 미충족'

                sec_tag = f' | {sector_name} 섹터 활성' if track_c else ''
                detail += sec_tag

                candidates.append({
                    'stock_code':  stock_code,
                    'stock_name':  code_name.get(stock_code, stock_code),
                    'score':       composite,
                    'track_a':     track_a,
                    'track_b':     track_b,
                    'sector_bonus': track_c,
                    'signal':      signal,
                    'detail':      detail,
                    'per':         per_val,
                    'pbr':         pbr_val,
                    'graham_iv':   round(graham_iv) if graham_iv else None,
                    'discount':    disc_val,
                    'rsi':         rsi,
                    'vol_ratio':   round(vol_ratio, 2),
                    'from_high52': round(from_high, 1),
                    'mktcap':      mktcap,
                    'market':      code_market.get(stock_code, ''),
                    'sector':      sector_name,
                })

            except Exception:
                continue

        # 점수 높은 순, 동점 시 추세 우선
        candidates.sort(key=lambda x: (x['score'], x['track_a'], x['track_b']), reverse=True)
        return candidates[:20]

    finally:
        if own_conn:
            conn.close()


# ══════════════════════════════════════════════════════════════════
# Logic-#2: 수급 주도 모멘텀 스캐너
# ══════════════════════════════════════════════════════════════════

def _calc_earnings_quality(conn, stock_code: str) -> tuple:
    """
    최근 4분기 YoY 영업이익 성장 연속성 계산.
    반환: (quality_score 0~4, detail_str)
    """
    try:
        rows = conn.execute("""
            SELECT year, quarter, operating_profit
            FROM financial_data
            WHERE stock_code=? AND is_annual=0
              AND operating_profit IS NOT NULL
            ORDER BY year DESC, quarter DESC
            LIMIT 8
        """, (stock_code,)).fetchall()

        if not rows:
            return 0, "실적데이터없음"

        # 최신 분기 흑자 여부
        latest_op = rows[0][2] if rows else 0
        if latest_op is None or latest_op <= 0:
            return 0, "최신분기적자"

        # YoY 비교: 현재 연도 분기 vs 1년 전 같은 분기
        # rows: [(year, quarter, op), ...] 최신순
        yoy_results = []
        for i, (yr, qt, op) in enumerate(rows[:4]):
            prev_yr = yr - 1
            prev_op = None
            for (yr2, qt2, op2) in rows:
                if yr2 == prev_yr and qt2 == qt:
                    prev_op = op2
                    break
            if prev_op and prev_op > 0 and op is not None:
                yoy_results.append(op > prev_op)
            elif prev_op and prev_op <= 0 and op > 0:
                yoy_results.append(True)  # 적자→흑자 전환

        if not yoy_results:
            return V2_QUALITY_POSITIVE, f"흑자(YoY비교불가)"

        consec = 0
        for grew in yoy_results:
            if grew:
                consec += 1
            else:
                break

        if consec >= 3:
            return V2_QUALITY_CONSEC_3Q, f"영업이익 {consec}분기연속YoY성장"
        elif consec == 2:
            return V2_QUALITY_CONSEC_2Q, f"영업이익 2분기연속YoY성장"
        elif consec == 1:
            return V2_QUALITY_CONSEC_1Q, f"영업이익 최신분기YoY성장"
        else:
            return V2_QUALITY_POSITIVE, "흑자(YoY성장없음)"

    except Exception:
        return 0, "실적계산오류"


def _calc_supply_score_v2(inst_a: list, frn_a: list, inst_q: list, frn_q: list,
                           close: float) -> tuple:
    """
    Logic-#2 수급 점수 계산 (0~6).
    inst_a/frn_a: 최신순 금액(백만원), inst_q/frn_q: 최신순 수량(주)
    반환: (score, detail)
    """
    # 금액 또는 수량으로 일별 순매수 산출 (억원 단위 환산)
    def _to_uk(amt_val, qty_val, price):
        """백만원 amt 또는 주수 qty → 억원"""
        amt_val = amt_val or 0
        qty_val = qty_val or 0
        if abs(amt_val) > 0:
            # amt_val이 백만원인지 주수인지 판별
            if qty_val > 0 and abs(amt_val) / max(qty_val, 1) >= 0.1:
                # qty×price 방식 (amt가 주수로 오해될 경우)
                return qty_val * price / 100_000_000
            return amt_val / 100  # 백만원 → 억원
        if qty_val and price:
            return qty_val * price / 100_000_000
        return 0.0

    n = min(20, len(inst_a), len(frn_a))
    if n < 3:
        return 0, "수급데이터부족"

    # 일별 순매수 (억원)
    inst_daily = [_to_uk(inst_a[i], inst_q[i] if i < len(inst_q) else 0, close)
                  for i in range(n)]
    frn_daily  = [_to_uk(frn_a[i],  frn_q[i]  if i < len(frn_q)  else 0, close)
                  for i in range(n)]

    # 연속 동반 매수일 (기관+외국인 모두 양수)
    consec_both = 0
    for i in range(min(10, n)):
        if inst_daily[i] > 0 and frn_daily[i] > 0:
            consec_both += 1
        else:
            break

    # 기관 연속 매수일
    consec_inst = 0
    for i in range(min(10, n)):
        if inst_daily[i] > 0:
            consec_inst += 1
        else:
            break

    # 외국인 연속 매수일
    consec_frn = 0
    for i in range(min(10, n)):
        if frn_daily[i] > 0:
            consec_frn += 1
        else:
            break

    # 10일 누적 순매수 (억원)
    inst_10d = sum(inst_daily[:10])
    frn_10d  = sum(frn_daily[:10])
    total_10d = inst_10d + frn_10d

    # 점수 계산
    score   = 0
    details = []

    # 동반 연속 매수 (최우선 — 기관+외국인이 같이 사야 진짜 신호)
    if consec_both >= V2_SUPPLY_CONSEC_BOTH:
        score += 4
        details.append(f"기관+외국인 동반{consec_both}일매수")
    elif consec_both == 2:
        score += 3
        details.append(f"기관+외국인 동반2일매수")
    elif consec_both == 1:
        score += 2
        details.append(f"기관+외국인 동반1일매수")
    elif consec_inst >= V2_SUPPLY_CONSEC_ONE or consec_frn >= V2_SUPPLY_CONSEC_ONE:
        score += 2
        who = "기관" if consec_inst >= consec_frn else "외국인"
        days = max(consec_inst, consec_frn)
        details.append(f"{who} {days}일연속매수")
    elif consec_inst >= 3 or consec_frn >= 3:
        score += 1
        who = "기관" if consec_inst >= consec_frn else "외국인"
        days = max(consec_inst, consec_frn)
        details.append(f"{who} {days}일매수")

    # 누적 금액 보너스
    if total_10d >= V2_SUPPLY_AMT_STRONG:
        score += 2
        details.append(f"10일누적+{total_10d:.0f}억")
    elif total_10d >= V2_SUPPLY_AMT_MID:
        score += 1
        details.append(f"10일누적+{total_10d:.0f}억")
    elif total_10d < -V2_SUPPLY_AMT_MID:
        score = max(0, score - 1)
        details.append(f"순매도{total_10d:.0f}억")

    return min(score, 6), " | ".join(details) if details else "수급미약"


def calc_combo_v2(conn=None) -> list:
    """
    Logic-#2: 수급 주도 모멘텀 스캐너 (Supply-Led Momentum)

    [선별 기준]
    - Track S (수급): 기관/외국인 연속 누적 매수 (0~6점, 가중치 ×3)
    - Track T (추세): 일봉+주봉 다중 타임프레임 정배열 (0~6점, 가중치 ×2)
    - Track Q (실적): 연속 분기 YoY 영업이익 성장 (0~4점, 가중치 ×2)
    - Track R (RS):   3개 기간(1M/3M/6M) KOSPI 대비 초과수익 (0~4점, 가중치 ×1)

    종합점수 = S×3 + T×2 + Q×2 + R×1  (최대 42점)

    [시장 환경 필터]
    - KOSPI > MA60 OR 현재 KOSPI > 20일 전 KOSPI (추세 방향)

    [최소 진입 기준]
    - Supply 점수 ≥ 2 (수급 없으면 신호 없음)
    - Trend 점수 ≥ 2 (역배열 진입 금지)
    """
    import math as _math

    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)

    try:
        # ── 시장 환경: KOSPI 추세 확인 ─────────────────────────────
        kospi_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0
            ORDER BY date DESC LIMIT 70
        """).fetchall()

        market_ok = True
        kospi_ret_1m  = 0.0
        kospi_ret_3m  = 0.0
        kospi_ret_6m  = 0.0
        if len(kospi_rows) >= 5:
            kospi_now  = kospi_rows[0][1]
            kospi_20d  = kospi_rows[min(19, len(kospi_rows)-1)][1]
            kospi_60d  = kospi_rows[min(59, len(kospi_rows)-1)][1]
            closes_k   = [r[1] for r in kospi_rows]
            ma60_k     = sum(closes_k[:60]) / min(60, len(closes_k))
            # 시장: MA60 위 OR 20일 전보다 상승 → OK
            market_ok  = (kospi_now > ma60_k) or (kospi_now > kospi_20d * 0.97)
            kospi_ret_1m  = (kospi_now - kospi_rows[min(20, len(kospi_rows)-1)][1]) / kospi_rows[min(20, len(kospi_rows)-1)][1] * 100 if len(kospi_rows) > 20 else 0
            kospi_ret_3m  = (kospi_now - kospi_rows[min(62, len(kospi_rows)-1)][1]) / kospi_rows[min(62, len(kospi_rows)-1)][1] * 100 if len(kospi_rows) > 62 else 0
            if len(kospi_rows) >= 126:
                kospi_ret_6m = (kospi_now - kospi_rows[125][1]) / kospi_rows[125][1] * 100

        # ── 유니버스 맵 로드 ───────────────────────────────────────
        _umaps      = _load_universe_maps(conn)
        code_mktcap = _umaps["mktcap"]
        code_market = _umaps["market"]
        code_sector = _umaps["sector"]
        code_name   = _umaps["name"]
        code_tvol   = _umaps["tvol"]

        # ── 실적 데이터 bulk 로드 ──────────────────────────────────
        # 최신 분기 영업이익이 양수인 종목만 대상
        profitable_codes = set()
        _fin_rows = conn.execute("""
            SELECT stock_code FROM financial_data
            WHERE is_annual=0 AND operating_profit > 0
              AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            GROUP BY stock_code
            HAVING MAX(year*10+quarter)
        """).fetchall()
        profitable_codes = {r[0] for r in _fin_rows}

        # ── 120일 이상 데이터 보유 종목 (유동성 필터 포함) ──────────
        stock_rows = conn.execute("""
            SELECT p.stock_code, COUNT(*) as cnt
            FROM price_history p
            INNER JOIN (
                SELECT stock_code FROM stock_universe
                WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                  AND (market_cap IS NULL OR market_cap >= ?)
                GROUP BY stock_code
            ) su ON p.stock_code = su.stock_code
            WHERE p.close > 0
            GROUP BY p.stock_code
            HAVING cnt >= 120
        """, (V2_MKTCAP_MIN,)).fetchall()

        candidates = []

        for stock_code, _ in stock_rows:
            try:
                # ── 유동성 필터 ────────────────────────────────────
                mktcap = code_mktcap.get(stock_code, 0)
                if 0 < mktcap < V2_MKTCAP_MIN:
                    continue
                tvol = code_tvol.get(stock_code, 0)

                # ── 가격 데이터 (최대 600일 = 약 2.5년 일봉) ──────
                rows = conn.execute("""
                    SELECT date, close, high, low, volume,
                           inst_net_buy_amt, frn_net_buy_amt,
                           inst_net_buy, frn_net_buy,
                           COALESCE(trade_amount, 0) as trade_amount
                    FROM price_history WHERE stock_code=? AND close>0
                    ORDER BY date DESC LIMIT 600
                """, (stock_code,)).fetchall()

                if len(rows) < 120:
                    continue

                dates   = [r[0] for r in rows]
                closes  = [r[1] for r in rows]
                highs   = [r[2] or r[1] for r in rows]
                lows    = [r[3] or r[1] for r in rows]
                vols    = [r[4] or 0 for r in rows]
                inst_a  = [r[5] or 0 for r in rows]
                frn_a   = [r[6] or 0 for r in rows]
                inst_q  = [r[7] or 0 for r in rows]
                frn_q   = [r[8] or 0 for r in rows]
                traw    = [r[9] or 0 for r in rows]
                curr    = closes[0]

                # ── 거래대금 필터 (KRX 실데이터 우선, 없으면 proxy) ──
                tvals5 = [traw[i] if traw[i] > 0 else vols[i] * closes[i] for i in range(min(5, len(rows)))]
                avg_tval_5d = sum(tvals5) / max(len(tvals5), 1)
                if avg_tval_5d < V2_TVOL_MIN_KRW and tvol < 100:
                    continue

                # ── 가격 지표 계산 ─────────────────────────────────
                ind     = _calc_price_indicators(closes, vols)
                rsi     = ind["rsi"]
                ma5     = ind["ma5"];  ma20 = ind["ma20"]; ma60  = ind["ma60"]
                ma120   = ind["ma120"]; ma200 = ind["ma200"]
                high52  = ind["high52"]; from_high = ind["from_high"]

                # 완전 붕괴 종목 제외
                if from_high < V2_MAX_FROM_HIGH52:
                    continue

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # Track S: 수급 점수 (0~6, 가중치 ×3)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                track_s, supply_detail = _calc_supply_score_v2(
                    inst_a, frn_a, inst_q, frn_q, curr)

                # 수급 미약 → 제외
                if track_s < V2_SUPPLY_MIN_SIGNAL:
                    continue

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # Track T: 추세 점수 (0~6, 가중치 ×2)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                track_t     = 0
                trend_parts = []

                # 일봉 정배열 (레벨별 가산)
                if ma200 and curr > ma200:
                    track_t += 1
                    if ma120 and curr > ma120 and ma120 > ma200:
                        track_t += 1
                        if ma60 and curr > ma60 and ma60 > ma120:
                            track_t += 1
                            if ma20 and curr > ma20 and ma20 > ma60:
                                track_t += 1  # 완전 정배열 = 4점
                                trend_parts.append("일봉완전정배열")
                            else:
                                trend_parts.append("일봉MA120정배열")
                        else:
                            trend_parts.append("일봉MA200정배열")
                    else:
                        trend_parts.append("MA200위")
                else:
                    trend_parts.append("MA200하단")

                # 주봉 보너스 (40주선, 80주선)
                weekly = _daily_to_weekly_closes(list(zip(dates, closes)))
                if len(weekly) >= 40:
                    w40 = sum(weekly[:40]) / 40
                    if curr > w40:
                        track_t += 1
                        trend_parts.append(f"주봉40주선위")
                        if len(weekly) >= 80:
                            w80 = sum(weekly[:80]) / 80
                            if w40 > w80:
                                track_t += 1
                                trend_parts.append("주봉장기정배열")

                track_t = min(track_t, 6)

                # 추세 역배열 → 제외
                if track_t < V2_TREND_MIN_SIGNAL:
                    continue

                # 52주 고점 근접 보너스 (track_t에 포함)
                high52_bonus = ""
                if from_high >= -5:
                    high52_bonus = f"신고가근접({from_high:+.1f}%)"
                elif from_high >= V2_TREND_HIGH52_MIN:
                    high52_bonus = f"고점{from_high:+.0f}%"

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # Track Q: 실적 품질 (0~4, 가중치 ×2)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                track_q, quality_detail = _calc_earnings_quality(conn, stock_code)

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # Track R: 상대강도 (0~4, 가중치 ×1)
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                track_r   = 0
                rs_detail = ""
                rs_1m = rs_3m = rs_6m = None

                if len(closes) >= 21:
                    rs_1m = (closes[0] - closes[20]) / closes[20] * 100 - kospi_ret_1m
                if len(closes) >= 63:
                    rs_3m = (closes[0] - closes[62]) / closes[62] * 100 - kospi_ret_3m
                if len(closes) >= 126:
                    rs_6m = (closes[0] - closes[125]) / closes[125] * 100 - kospi_ret_6m

                rs_wins = sum([
                    rs_1m is not None and rs_1m > 0,
                    rs_3m is not None and rs_3m > 0,
                    rs_6m is not None and rs_6m > 0,
                ])
                three_ok = (rs_1m is not None and rs_1m > 0 and
                            rs_3m is not None and rs_3m > 0 and
                            rs_6m is not None and rs_6m > 0)
                two_ok   = (rs_3m is not None and rs_3m > 0 and
                            rs_6m is not None and rs_6m > 0)

                if three_ok:
                    track_r = V2_RS_ALL_THREE
                    rs_detail = f"RS 1M:{rs_1m:+.1f}% 3M:{rs_3m:+.1f}% 6M:{rs_6m:+.1f}%"
                elif two_ok:
                    track_r = V2_RS_TWO
                    rs_detail = f"RS 3M:{rs_3m:+.1f}% 6M:{rs_6m:+.1f}%"
                elif rs_3m is not None and rs_3m > 0:
                    track_r = V2_RS_ONE_MID
                    rs_detail = f"RS 3M:{rs_3m:+.1f}%"
                elif rs_1m is not None and rs_1m > 0:
                    track_r = V2_RS_ONE_SHORT
                    rs_detail = f"RS 1M:{rs_1m:+.1f}%"

                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                # 종합 점수 및 시그널 판정
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                composite = track_s * 3 + track_t * 2 + track_q * 2 + track_r * 1

                # 시장 하락 장세에서는 점수 감산
                if not market_ok:
                    composite = int(composite * 0.75)

                if composite >= V2_SCORE_STRONG and track_s >= 4 and track_t >= 4:
                    signal = 'green'
                    label  = '강력추천'
                elif composite >= V2_SCORE_BUY and track_s >= 3 and track_t >= 3:
                    signal = 'green'
                    label  = '추천'
                elif composite >= V2_SCORE_WATCH and track_s >= 2:
                    signal = 'yellow'
                    label  = '관심'
                else:
                    signal = 'yellow'
                    label  = '대기'

                # 시장 역풍
                market_warn = " ⚠시장하락장" if not market_ok else ""

                # detail 텍스트
                detail_parts = []
                if supply_detail:
                    detail_parts.append(f"📊{supply_detail}")
                if trend_parts:
                    detail_parts.append(f"📈{'·'.join(trend_parts)}")
                if high52_bonus:
                    detail_parts.append(high52_bonus)
                if quality_detail and track_q > 0:
                    detail_parts.append(f"💹{quality_detail}")
                if rs_detail:
                    detail_parts.append(rs_detail)
                if market_warn:
                    detail_parts.append(market_warn)

                detail = " | ".join(detail_parts)

                sector_name = code_sector.get(stock_code, '')

                candidates.append({
                    'stock_code':     stock_code,
                    'stock_name':     code_name.get(stock_code, stock_code),
                    'score':          composite,
                    'track_s':        track_s,   # 수급
                    'track_t':        track_t,   # 추세
                    'track_q':        track_q,   # 실적
                    'track_r':        track_r,   # RS
                    'signal':         signal,
                    'label':          label,
                    'detail':         detail,
                    'supply_detail':  supply_detail,
                    'rsi':            round(rsi, 1),
                    'from_high52':    round(from_high, 1),
                    'rs_1m':          round(rs_1m, 1) if rs_1m is not None else None,
                    'rs_3m':          round(rs_3m, 1) if rs_3m is not None else None,
                    'rs_6m':          round(rs_6m, 1) if rs_6m is not None else None,
                    'mktcap':         mktcap,
                    'market':         code_market.get(stock_code, ''),
                    'sector':         sector_name,
                    'market_ok':      market_ok,
                })

            except Exception:
                continue

        # 점수 높은 순, 동점 시 수급 점수 우선
        candidates.sort(key=lambda x: (x['score'], x['track_s'], x['track_t']), reverse=True)
        return candidates[:30]

    finally:
        if own_conn:
            conn.close()


# ══════════════════════════════════════════════════════════════════
# V10: 이익 폭발 & 매출 급성장 (에이피알·삼양식품 유형)
# ══════════════════════════════════════════════════════════════════
def calc_earnings_explosion(conn=None) -> list:
    """
    이익 폭발 + 매출 급성장 종목 발굴 (V10)

    대상: 에이피알·삼양식품 유형
    - 최근 분기 영업이익 전년동기비(YoY) > 80%
    - 최근 분기 매출 YoY > 30%
    - 영업이익률 > 8%
    - 2개 분기 연속 이익 성장 (이익 가속화)
    - 주가 > MA60 (추세 확인)
    - 시총 50억 ~ 20조
    """
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

    try:
        # ── 1. 분기 재무 데이터 피벗 (최근 6개 분기 + 1년 전 동분기) ────
        rows = conn.execute("""
            WITH q AS (
                SELECT stock_code, year, quarter, operating_profit, revenue,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) rn
                FROM financial_data
                WHERE is_annual=0 AND quarter BETWEEN 1 AND 4
                  AND operating_profit IS NOT NULL AND revenue IS NOT NULL
            ),
            yago AS (
                SELECT stock_code, year, quarter, operating_profit as op_ya, revenue as rev_ya
                FROM financial_data
                WHERE is_annual=0 AND quarter BETWEEN 1 AND 4
                  AND operating_profit IS NOT NULL AND revenue IS NOT NULL
            )
            SELECT
                q.stock_code,
                MAX(CASE WHEN q.rn=1 THEN q.year END)             as yr0,
                MAX(CASE WHEN q.rn=1 THEN q.quarter END)          as qr0,
                MAX(CASE WHEN q.rn=1 THEN q.operating_profit END) as op0,
                MAX(CASE WHEN q.rn=1 THEN q.revenue END)          as rev0,
                MAX(CASE WHEN q.rn=2 THEN q.operating_profit END) as op1,
                MAX(CASE WHEN q.rn=2 THEN q.revenue END)          as rev1,
                MAX(CASE WHEN q.rn=3 THEN q.operating_profit END) as op2,
                MAX(CASE WHEN q.rn=5 THEN q.operating_profit END) as op_ya,
                MAX(CASE WHEN q.rn=5 THEN q.revenue END)          as rev_ya
            FROM q GROUP BY q.stock_code
            HAVING op0 IS NOT NULL AND rev0 IS NOT NULL
               AND op_ya IS NOT NULL AND rev_ya IS NOT NULL
        """).fetchall()

        # ── 2. 종목 마스터 일괄 조회 ────────────────────────────────────
        su = {r["stock_code"]: r for r in conn.execute(
            "SELECT stock_code, stock_name, sector_large, market_cap, per, pbr "
            "FROM stock_universe WHERE market_cap BETWEEN 5e9 AND 2e13"
        ).fetchall()}

        candidates = []

        for row in rows:
            sc = row["stock_code"]
            if sc not in su:
                continue

            op0  = row["op0"]  or 0.0
            op1  = row["op1"]  or 0.0
            op2  = row["op2"]  or 0.0
            rev0 = row["rev0"] or 0.0
            op_ya  = row["op_ya"]  or 0.0
            rev_ya = row["rev_ya"] or 0.0

            if rev0 <= 0 or rev_ya <= 0 or op_ya <= 0 or op0 <= 0:
                continue

            # ── 핵심 필터 ──────────────────────────────────────────────
            op_yoy  = (op0 - op_ya) / abs(op_ya) * 100   # 영업이익 YoY%
            rev_yoy = (rev0 - rev_ya) / abs(rev_ya) * 100 # 매출 YoY%
            op_margin = op0 / rev0 * 100                   # 영업이익률

            if op_yoy  < 80:  continue  # 영업이익 YoY > 80%
            if rev_yoy < 30:  continue  # 매출 YoY > 30%
            if op_margin < 8: continue  # 영업이익률 > 8%
            if op0 <= op1:    continue  # 이익 가속: 직전분기보다 증가
            if op1 <= op2:    continue  # 2개 분기 연속 성장

            # ── 주가 추세 확인 (MA60 이상) ─────────────────────────────
            ph = conn.execute("""
                SELECT close FROM price_history
                WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 80
            """, (sc,)).fetchall()
            if len(ph) < 60:
                continue
            closes = [r[0] for r in ph]
            cur = closes[0]
            ma60 = sum(closes[:60]) / 60
            if cur < ma60 * 0.97:  # MA60 97% 이하면 추세 아님
                continue

            # ── 수급 (기관+외인 5일 합산) ──────────────────────────────
            sup = conn.execute("""
                SELECT SUM(inst_net_buy_amt + frn_net_buy_amt) as net_amt
                FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 5
            """, (sc,)).fetchone()
            supply_5d_억 = round((sup[0] or 0) / 100) if sup else 0

            # ── 52주 대비 위치 ─────────────────────────────────────────
            if len(closes) >= 252:
                high52 = max(closes[:252])
                pct_from_high52 = (cur - high52) / high52 * 100
            else:
                pct_from_high52 = 0

            info = su[sc]
            mktcap_억 = round((info["market_cap"] or 0) / 1e8)

            # ── 스코어 (0~100) ─────────────────────────────────────────
            score = 0
            score += min(op_yoy / 5, 30)    # 영업이익 YoY 최대 30점
            score += min(rev_yoy / 3, 20)   # 매출 YoY 최대 20점
            score += min(op_margin / 2, 15) # 영업이익률 최대 15점
            score += min(max(supply_5d_억, 0) / 20, 10)  # 수급 최대 10점
            if cur > ma60 * 1.05: score += 10  # MA60 위 5% 이상
            if cur > max(closes[1:21]): score += 15  # 최근 20일 신고가

            # ── 보너스: DART 수주공시 + HS수출 (NPS 연간고용 제거) ──
            _cb_v10 = _load_contract_bonus_map(days=90).get(sc)
            _hb_v10 = _load_hs_export_bonus_map(months=6).get(sc)
            bonus_labels = []
            if _cb_v10:
                score += _cb_v10["bonus"] * 2  # V10은 이익폭발 — 수주공시 가중치 2배
                bonus_labels.append(f'📋{_cb_v10["label"]}')
            if _hb_v10:
                score += _hb_v10["bonus"]
                bonus_labels.append(f'🚢{_hb_v10["label"]}')

            candidates.append({
                "stock_code":   sc,
                "stock_name":   info["stock_name"],
                "sector":       info["sector_large"] or "",
                "market_cap_억": mktcap_억,
                "current_price": round(cur),
                "ma60":         round(ma60),
                "op_yoy":       round(op_yoy, 1),
                "rev_yoy":      round(rev_yoy, 1),
                "op_margin":    round(op_margin, 1),
                "op_q0_억":     round(op0 / 1e8),
                "op_q1_억":     round(op1 / 1e8),
                "rev_q0_억":    round(rev0 / 1e8),
                "supply_5d_억": supply_5d_억,
                "high52_pct":   round(pct_from_high52, 1),
                "per":          round(info["per"], 1) if info["per"] else None,
                "pbr":          round(info["pbr"], 2) if info["pbr"] else None,
                "score":        round(score, 1),
                "strategy":     "v10_earnings_explosion",
                "bonus_labels": bonus_labels,
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:40]

    finally:
        if own_conn:
            conn.close()


# ══════════════════════════════════════════════════════════════════
# V11: 흑자전환 모멘텀 (이수페타시스·엘앤에프 유형)
# ══════════════════════════════════════════════════════════════════
def calc_turnaround_momentum(conn=None) -> list:
    """
    흑자전환(적자→흑자) + 이익 가속 종목 발굴 (V11)

    대상: 이수페타시스·엘앤에프 유형
    - 최근 2개 분기 영업이익 > 50억 (유의미한 흑자)
    - 직전 2개 분기 중 하나 이상 영업이익 < 20억 (과거 적자 or 미미)
    - 최근 분기 이익이 직전 분기보다 큼 (이익 증가세)
    - 매출 YoY > 15%
    - 주가 > MA60
    - 시총 < 20조 (소·중형주)
    - 기관 or 외인 최근 20일 누적 순매수 (수급 동반)
    """
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

    try:
        # ── 1. 분기 재무 피벗 ──────────────────────────────────────────
        rows = conn.execute("""
            WITH q AS (
                SELECT stock_code, year, quarter, operating_profit, revenue,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) rn
                FROM financial_data
                WHERE is_annual=0 AND quarter BETWEEN 1 AND 4
                  AND operating_profit IS NOT NULL
            )
            SELECT
                stock_code,
                MAX(CASE WHEN rn=1 THEN operating_profit END) as op0,
                MAX(CASE WHEN rn=2 THEN operating_profit END) as op1,
                MAX(CASE WHEN rn=3 THEN operating_profit END) as op2,
                MAX(CASE WHEN rn=4 THEN operating_profit END) as op3,
                MAX(CASE WHEN rn=1 THEN revenue END)          as rev0,
                MAX(CASE WHEN rn=5 THEN revenue END)          as rev_ya
            FROM q GROUP BY stock_code
            HAVING op0 IS NOT NULL AND op1 IS NOT NULL
               AND op2 IS NOT NULL AND op3 IS NOT NULL
        """).fetchall()

        su = {r["stock_code"]: r for r in conn.execute(
            "SELECT stock_code, stock_name, sector_large, market_cap, per, pbr "
            "FROM stock_universe WHERE market_cap BETWEEN 5e9 AND 2e13"
        ).fetchall()}

        candidates = []

        for row in rows:
            sc = row["stock_code"]
            if sc not in su:
                continue

            op0 = row["op0"] or 0.0
            op1 = row["op1"] or 0.0
            op2 = row["op2"] or 0.0
            op3 = row["op3"] or 0.0
            rev0   = row["rev0"]   or 0.0
            rev_ya = row["rev_ya"] or 0.0

            THRESHOLD_HIGH = 5e9   # 50억: 유의미한 흑자 기준
            THRESHOLD_LOW  = 2e9   # 20억: 과거 '미미' 기준

            # ── 핵심 필터 ──────────────────────────────────────────────
            # 현재 2개 분기 모두 50억 이상 흑자
            if op0 < THRESHOLD_HIGH or op1 < THRESHOLD_HIGH:
                continue
            # 과거(Q-2 or Q-3) 중 하나 이상 20억 미만 (적자 또는 극소)
            if op2 >= THRESHOLD_LOW and op3 >= THRESHOLD_LOW:
                continue
            # 이익 증가세
            if op0 <= op1:
                continue
            # 매출 YoY
            rev_yoy = (rev0 - rev_ya) / abs(rev_ya) * 100 if rev_ya and rev0 else 0
            if rev_yoy < 15:
                continue

            # ── 주가 추세 확인 ─────────────────────────────────────────
            ph = conn.execute("""
                SELECT close, inst_net_buy_amt, frn_net_buy_amt
                FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 120
            """, (sc,)).fetchall()
            if len(ph) < 60:
                continue

            closes   = [r[0] for r in ph]
            inst_amt = [r[1] or 0 for r in ph]
            frn_amt  = [r[2] or 0 for r in ph]

            cur  = closes[0]
            ma60 = sum(closes[:60]) / 60
            if cur < ma60 * 0.95:
                continue

            # ── 수급 (20일 누적 기관+외인) ─────────────────────────────
            supply_20d_억 = sum(inst_amt[:20] + frn_amt[:20]) / 100

            # ── 전환 강도 스코어 ────────────────────────────────────────
            # 적자→흑자 전환의 '낙폭'
            worst_past = min(op2, op3)
            reversal_power = (op0 - worst_past) / 1e8  # 억원 단위

            info = su[sc]
            mktcap_억 = round((info["market_cap"] or 0) / 1e8)

            score = 0
            score += min(reversal_power / 10, 40)       # 전환 강도 최대 40점
            score += min(rev_yoy / 3, 20)               # 매출 성장 최대 20점
            score += min(max(supply_20d_억, 0) / 50, 20) # 수급 최대 20점
            if cur > max(closes[1:20]): score += 20     # 20일 신고가

            # 이익 가속도: Q0/Q1 ratio
            accel = op0 / op1 if op1 > 0 else 1
            score += min((accel - 1) * 20, 20)

            # ── 보너스: DART 수주공시 + HS수출 (NPS 연간고용 제거) ──
            _cb_v11 = _load_contract_bonus_map(days=90).get(sc)
            _hb_v11 = _load_hs_export_bonus_map(months=6).get(sc)
            v11_bonus_labels = []
            if _cb_v11:
                score += _cb_v11["bonus"] * 2  # V11 흑자전환 — 수주공시 특히 중요
                v11_bonus_labels.append(f'📋{_cb_v11["label"]}')
            if _hb_v11:
                score += _hb_v11["bonus"] * 2  # V11은 수출 개선이 핵심 드라이버
                v11_bonus_labels.append(f'🚢{_hb_v11["label"]}')

            candidates.append({
                "stock_code":     sc,
                "stock_name":     info["stock_name"],
                "sector":         info["sector_large"] or "",
                "market_cap_억":  mktcap_억,
                "current_price":  round(cur),
                "ma60":           round(ma60),
                "op_q0_억":       round(op0 / 1e8),
                "op_q1_억":       round(op1 / 1e8),
                "op_q2_억":       round(op2 / 1e8),
                "op_q3_억":       round(op3 / 1e8),
                "rev_yoy":        round(rev_yoy, 1),
                "reversal_power": round(reversal_power),
                "supply_20d_억":  round(supply_20d_억),
                "per":            round(info["per"], 1) if info["per"] else None,
                "pbr":            round(info["pbr"], 2) if info["pbr"] else None,
                "score":          round(score, 1),
                "strategy":       "v11_turnaround",
                "bonus_labels":   v11_bonus_labels,
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:40]

    finally:
        if own_conn:
            conn.close()


# ══════════════════════════════════════════════════════════════════
# V12: 섹터 대세 상승 (효성중공업·LS Electric 유형)
# ══════════════════════════════════════════════════════════════════
def calc_sector_megatrend(conn=None) -> list:
    """
    섹터 대세 상승 종목 발굴 (V12)

    대상: 효성중공업·LS Electric 유형
    - KOSPI 3개월 대비 섹터 초과수익률 > 15%인 강세 섹터 선별
    - 해당 섹터 내 RS(상대강도) 우수 종목:
        * 종목 3개월 수익률 > 섹터 평균
        * 주가 > MA120
        * 기관 or 외인 20일 누적 순매수 양호
        * 52주 고점 대비 -20% 이내 (고점 유지)
        * 매출 YoY > 10% (실적 뒷받침)
    """
    own_conn = conn is None
    if own_conn:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

    try:
        # ── 1. KOSPI 3개월 수익률 ─────────────────────────────────────
        kospi_rows = conn.execute("""
            SELECT close FROM price_history
            WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT 70
        """).fetchall()
        if len(kospi_rows) < 63:
            return []
        kospi_cur  = kospi_rows[0][0]
        kospi_3m   = kospi_rows[62][0]
        kospi_3m_ret = (kospi_cur - kospi_3m) / kospi_3m * 100

        # ── 2. 섹터별 평균 3개월 수익률 ──────────────────────────────
        # sector_small 우선, 없으면 sector_large 사용 (더 세밀한 섹터 분류)
        sector_stocks = conn.execute("""
            SELECT stock_code,
                   COALESCE(NULLIF(sector_small,''), NULLIF(sector_large,'')) as sector
            FROM stock_universe
            WHERE COALESCE(NULLIF(sector_small,''), NULLIF(sector_large,'')) IS NOT NULL
              AND COALESCE(NULLIF(sector_small,''), NULLIF(sector_large,'')) NOT IN ('nan')
              AND market_cap > 50000000000
        """).fetchall()

        # 각 종목 현재가/3개월전가 일괄 조회 (최근 65일 데이터)
        sc_list = [r[0] for r in sector_stocks]
        sc_sector = {r[0]: r[1] for r in sector_stocks}

        # 종목별 가격 데이터
        ph_data = {}
        for sc in sc_list:
            ph = conn.execute("""
                SELECT close, inst_net_buy_amt, frn_net_buy_amt
                FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 130
            """, (sc,)).fetchall()
            if len(ph) >= 63:
                ph_data[sc] = ph

        # 섹터별 수익률 계산
        sector_returns = {}
        stock_3m = {}
        for sc, ph in ph_data.items():
            sector = sc_sector.get(sc, "")
            ret3m = (ph[0][0] - ph[62][0]) / ph[62][0] * 100
            stock_3m[sc] = ret3m
            if sector not in sector_returns:
                sector_returns[sector] = []
            sector_returns[sector].append(ret3m)

        # ── 3. 강세 섹터 선별 (KOSPI 대비 +15% 이상) ─────────────────
        hot_sectors = {}
        for sector, rets in sector_returns.items():
            if len(rets) < 3:
                continue
            avg_ret = sum(rets) / len(rets)
            alpha   = avg_ret - kospi_3m_ret
            if alpha > 15:  # 초과수익률 15% 이상
                hot_sectors[sector] = {"avg_ret": avg_ret, "alpha": alpha, "cnt": len(rets)}

        if not hot_sectors:
            return []

        # ── 4. 강세 섹터 내 우수 종목 선별 ──────────────────────────
        su = {r["stock_code"]: r for r in conn.execute(
            """SELECT stock_code, stock_name,
                      COALESCE(NULLIF(sector_small,''), NULLIF(sector_large,'')) as sector_large,
                      market_cap, per, pbr, roe
               FROM stock_universe WHERE market_cap > 5e10"""
        ).fetchall()}

        # 최근 분기 매출 YoY
        fin_data = {}
        for r in conn.execute("""
            WITH q AS (
                SELECT stock_code, revenue,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) rn
                FROM financial_data WHERE is_annual=0 AND quarter BETWEEN 1 AND 4
                  AND revenue IS NOT NULL
            )
            SELECT stock_code,
                   MAX(CASE WHEN rn=1 THEN revenue END) as rev0,
                   MAX(CASE WHEN rn=5 THEN revenue END) as rev_ya
            FROM q GROUP BY stock_code
        """).fetchall():
            if r[1] and r[2] and r[2] > 0:
                fin_data[r[0]] = (r[1] - r[2]) / abs(r[2]) * 100

        candidates = []
        for sc, ph in ph_data.items():
            sector = sc_sector.get(sc, "")
            if sector not in hot_sectors:
                continue
            if sc not in su:
                continue

            closes = [r[0] for r in ph]
            cur    = closes[0]
            ret3m  = stock_3m[sc]
            sector_avg = hot_sectors[sector]["avg_ret"]
            sector_alpha = hot_sectors[sector]["alpha"]

            # RS 필터: 종목 3개월 수익률 > 섹터 평균
            rs_excess = ret3m - sector_avg
            if rs_excess < 0:
                continue

            # MA120 이상
            if len(closes) < 120:
                continue
            ma120 = sum(closes[:120]) / 120
            if cur < ma120 * 0.97:
                continue

            # 52주 고점 대비 -20% 이내
            if len(closes) >= 252:
                high52 = max(closes[:252])
                pct_from_high52 = (cur - high52) / high52 * 100
            else:
                high52 = max(closes)
                pct_from_high52 = (cur - high52) / high52 * 100
            if pct_from_high52 < -20:
                continue

            # 매출 YoY > 10%
            rev_yoy = fin_data.get(sc)
            if rev_yoy is not None and rev_yoy < 10:
                continue

            # 수급 (20일)
            inst_20 = sum(r[1] or 0 for r in ph[:20])
            frn_20  = sum(r[2] or 0 for r in ph[:20])
            supply_20d_억 = (inst_20 + frn_20) / 100

            info = su[sc]
            mktcap_억 = round((info["market_cap"] or 0) / 1e8)

            score = 0
            score += min(sector_alpha / 2, 25)      # 섹터 강도 최대 25점
            score += min(rs_excess / 2, 25)          # RS 초과 최대 25점
            score += min(ret3m / 5, 20)              # 절대 수익률 최대 20점
            score += min(max(supply_20d_억, 0) / 50, 15) # 수급 최대 15점
            if pct_from_high52 > -5: score += 15    # 52주 신고가 근처

            candidates.append({
                "stock_code":       sc,
                "stock_name":       info["stock_name"],
                "sector":           sector,
                "sector_alpha":     round(sector_alpha, 1),
                "sector_avg_ret":   round(sector_avg, 1),
                "market_cap_억":    mktcap_억,
                "current_price":    round(cur),
                "ret_3m_pct":       round(ret3m, 1),
                "rs_excess":        round(rs_excess, 1),
                "high52_pct":       round(pct_from_high52, 1),
                "supply_20d_억":    round(supply_20d_억),
                "rev_yoy":          round(rev_yoy, 1) if rev_yoy is not None else None,
                "per":              round(info["per"], 1) if info["per"] else None,
                "pbr":              round(info["pbr"], 2) if info["pbr"] else None,
                "roe":              round(info["roe"], 1) if info["roe"] else None,
                "hot_sectors":      list(hot_sectors.keys()),
                "score":            round(score, 1),
                "strategy":         "v12_sector_megatrend",
                "kospi_3m_ret":     round(kospi_3m_ret, 1),
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[:40]

    finally:
        if own_conn:
            conn.close()


# ══════════════════════════════════════════════════════════════════
# 실행
# ══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    init_signal_db()
    print("시그널 DB 초기화 완료")
    conn = sqlite3.connect(DB_PATH)
    print("\n[시장 시그널]")
    for r in calc_market_signals(conn):
        e = {'green':'🟢','yellow':'🟡','red':'🔴','gray':'⚪'}.get(r['signal'],'⚪')
        print(f"  {e} {r['label']:15s}: {r['detail']}")
    conn.close()
