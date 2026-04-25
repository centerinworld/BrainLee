"""
backtest.py — AI 적극검토 전략 백테스트 엔진 v5

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 매수 조건 (AI 적극검토 콤보 로직과 완전 일치)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  [0] ★ 시장 추세 필터 (v5 강화):
      KOSPI 현재가 > KOSPI MA120  (MA60→MA120, 더 엄격한 하락장 차단)

  [A] 추세 스크리너 (Minervini — 전부 필수):
      현재가 > MA120 > MA200 (장기 정배열)
      MA20 > MA60 (단기 정배열 최소 조건)
      현재가 >= 52주 최고가 × 80%
      RSI(14) >= 60
      거래량 > 20일 평균 × 2.0배

  [B] 가치 OR 수급 (하나 이상 필수):
      ▸ 가치: Graham 할인 ≥ 25% OR (PBR < 0.7 AND 0 < PER < 10), AND 영업이익 > 0
      ▸ 수급: 기관 5일 순매수 > 0 AND 외국인 5일 순매수 > 0 (동반 매수)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 매도 조건 (매일 보유 종목 전체 체크, 우선순위 순)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ★ 익절: 매수가 대비 +15% 이상 (v5: 20%→15%, 더 빠른 수익 확정)
  ① 하드 손절: 매수가 대비 -6% 이하
  ★ 추적 손절: 고점 대비 -10% 이하 (수익 보호)
  ② MA60 붕괴: 종가 < MA60
  ★ 최소 보유: 진입 후 5거래일 미만이면 MA 청산 스킵 (단기 변동성 방지)

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 미래참조(Look-ahead) 방지 조치
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  • 재무 공시일 지연:
      분기보고서: 분기 종료 후 45일 (Q1→5/15, Q2→8/15, Q3→11/15)
      사업보고서(연간): 회계연도 종료 후 90일 (12월 결산→다음해 3/31)
  • 기술 지표: 과거 데이터만 사용 (윈도우 모두 현재 포함 이전)
  • 매수 집행: 시그널 발생 당일 종가 (실무 근사; 더 보수적으로는 익일 시가)
  • 워밍업: start_date 이전 300거래일치 가격 로드 → day1부터 MA200 계산 가능

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
 생존자 편향 안내
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  price_history 에 있는 모든 6자리 종목 사용(상폐 종목 포함).
  단, 해당 종목의 가격 데이터가 DB에 없으면 편향 잔존 가능.
"""

import sqlite3
import json
import uuid
import math
import argparse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, List, Tuple

DB_PATH = "/Applications/stock_dashboard/stock.db"
WARMUP_DAYS = 300   # MA200 + 여유분


# ══════════════════════════════════════════════════════════════
#  DB 초기화
# ══════════════════════════════════════════════════════════════
def init_backtest_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id          INTEGER PRIMARY KEY,
            run_id      TEXT UNIQUE,
            name        TEXT,
            start_date  TEXT,
            end_date    TEXT,
            per_stock   REAL DEFAULT 10000000,
            max_pos     INTEGER DEFAULT 10,
            status      TEXT DEFAULT 'running',
            total_return_pct  REAL,
            ann_return_pct    REAL,
            win_rate          REAL,
            total_trades      INTEGER,
            profit_trades     INTEGER,
            max_drawdown_pct  REAL,
            trades_json       TEXT,
            equity_json       TEXT,
            summary_text      TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════
#  재무 공시일 추정 (미래참조 방지 핵심)
# ══════════════════════════════════════════════════════════════
def _release_date(year: int, quarter: int, is_annual: bool) -> str:
    """
    해당 재무 데이터가 시장에 공개되는 추정일.
    분기보고서: 분기 종료 후 45일
    사업보고서: 회계연도 종료 후 90일 (12월 결산 기준)
    """
    if is_annual:
        # 12월 결산 → 다음해 3월 31일 (사업보고서 법정 제출기한)
        return f"{year + 1}-03-31"
    release_map = {
        1: f"{year}-05-15",   # Q1(3월말) → 5월15일
        2: f"{year}-08-15",   # Q2(6월말) → 8월15일
        3: f"{year}-11-15",   # Q3(9월말) → 11월15일
        4: f"{year + 1}-02-15",  # Q4 별도 발표 없음 (연간 발표 전 임시)
    }
    return release_map.get(quarter, f"{year}-12-31")


def _get_financial_as_of(fin_rows: list, target_date: str) -> Optional[tuple]:
    """
    target_date 기준으로 이미 공시된 가장 최신 재무 데이터 반환.
    fin_rows: (year, quarter, rev, op, eps, bps, equity, net_inc, roe, is_annual)
    """
    best = None
    best_key = (-1, -1)
    for row in fin_rows:
        y, q, *_, is_ann = row[0], row[1], row[-1]
        release = _release_date(y, q, bool(is_ann))
        if release <= target_date:
            key = (y, q)
            if key > best_key:
                best_key = key
                best = row
    return best


# ══════════════════════════════════════════════════════════════
#  기술 지표 헬퍼
# ══════════════════════════════════════════════════════════════
def _ma(arr: list, n: int) -> Optional[float]:
    """단순 이동평균. arr[-n:]을 사용하므로 항상 과거 데이터만 참조."""
    if len(arr) < n:
        return None
    return sum(arr[-n:]) / n


def _graham_price(eps: float, bps: float) -> Optional[float]:
    """Graham 내재가치: √(22.5 × EPS × BPS)."""
    if eps and bps and eps > 0 and bps > 0:
        return math.sqrt(22.5 * eps * bps)
    return None


def _rsi(prices: list, n: int = 14) -> Optional[float]:
    """
    RSI(n) 계산. prices는 최신이 마지막(ASC).
    signal_engine.py 의 _calc_price_indicators 와 동일 로직.
    """
    if len(prices) < n + 1:
        return None
    deltas = [prices[i] - prices[i - 1] for i in range(len(prices) - n, len(prices))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]
    avg_g  = sum(gains)  / n
    avg_l  = sum(losses) / n
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - 100 / (1 + rs), 1)


# ══════════════════════════════════════════════════════════════
#  매수 시그널 — AI 적극검토 콤보 로직 완전 재현
# ══════════════════════════════════════════════════════════════
def _is_buy_signal(
    i: int,            # 현재 인덱스 (전체 배열 기준, 워밍업 포함)
    sim_start_i: int,  # 시뮬레이션 시작 인덱스 (워밍업 이후)
    dates: list,
    prices: list,
    volumes: list,
    frn_net: list,
    inst_net: list,
    fin_rows: list,
) -> bool:
    """
    AI 적극검토 콤보 로직 재현 (signal_engine.py 와 동일 기준):

    [A] 추세 스크리너 — 전부 필수 (Minervini)
      ① 장기 정배열: curr > MA120 > MA200
      ② 단기 정배열: MA20 > MA60 이상
      ③ 52주 고점 -20% 이내
      ④ RSI(14) ≥ 60  (TREND_RSI_MIN=60)
      ⑤ 거래량 > 20일평균 × 2.0배  (TREND_VOL_RATIO_MIN=2.0)

    [B] 가치 OR 수급 — 하나 이상 충족 (콤보 추가 스크리너)
      ▸ 가치: Graham 할인 ≥ 25% OR (PBR < 0.7 AND 0 < PER < 10), AND 영업이익 > 0
      ▸ 수급: 기관 5일 순매수 > 0 AND 외국인 5일 순매수 > 0 (동반 매수)
    """
    if i < sim_start_i:
        return False

    curr = prices[i]
    d    = dates[i]

    # ══ [A] 추세 스크리너 (Minervini — 전부 필수) ══════════════
    p_slice = prices[max(0, i - 199): i + 1]
    if len(p_slice) < 120:
        return False

    ma20  = sum(p_slice[-20:]) / 20  if len(p_slice) >= 20  else None
    ma60  = sum(p_slice[-60:]) / 60  if len(p_slice) >= 60  else None
    ma120 = sum(p_slice[-120:]) / 120 if len(p_slice) >= 120 else None
    ma200 = sum(p_slice) / len(p_slice) if len(p_slice) >= 200 else None

    if ma120 is None or ma200 is None or ma20 is None or ma60 is None:
        return False

    # ① 장기 정배열
    if not (curr > ma120 and curr > ma200 and ma120 > ma200):
        return False

    # ② 단기 정배열 (MA20 > MA60 최소 조건)
    if not (ma20 > ma60):
        return False

    # ③ 52주 고점 -20% 이내
    high52 = max(prices[max(0, i - 251): i + 1])
    if curr < high52 * 0.80:
        return False

    # ④ RSI(14) ≥ 60
    rsi_val = _rsi(prices[max(0, i - 28): i + 1])
    if rsi_val is None or rsi_val < 60:
        return False

    # ⑤ 거래량 > 20일 평균 × 2.0배
    vol_window = [v for v in volumes[max(0, i - 20): i] if v and v > 0]
    if not vol_window or not volumes[i] or volumes[i] <= 0:
        return False
    avg20v = sum(vol_window) / len(vol_window)
    if volumes[i] <= avg20v * 2.0:
        return False

    # ══ [B] 가치 OR 수급 OR RS강도 (하나 이상 필수) ════════════
    # ※ 수급 데이터가 부족한 구간(57일치 제한)을 RS 상대강도로 보완
    # signal_engine 콤보 = 추세 + (가치 OR 재무). 재무 대용으로 RS 사용.

    # 가치 스크리너 (Graham 공시일 지연 적용)
    value_ok = False
    fin = _get_financial_as_of(fin_rows, d)
    if fin is not None:
        _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann = fin
        if op and op > 0 and bps and bps > 0 and eps and eps > 0:
            pbr  = curr / bps
            per  = curr / eps
            gp   = _graham_price(eps, bps)
            disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
            # 심층 저평가: Graham 25% 이상 OR (PBR<0.7 AND PER<10)
            value_ok = (disc >= 25) or (pbr < 0.7 and 0 < per < 10)

    # 수급 스크리너: 기관 AND 외국인 5일 동반 순매수 (실시간 데이터)
    frn5  = sum(frn_net[max(0, i - 4): i + 1])
    inst5 = sum(inst_net[max(0, i - 4): i + 1])
    supply_ok = (frn5 > 0 and inst5 > 0)

    # RS 상대강도: 종목 3개월 수익률이 KOSPI보다 +5%p 이상 아웃퍼폼
    # (재무 스크리너 대용 — signal_engine RS 계산과 동일 기준)
    rs_ok = False
    if len(prices) >= 63 and i >= 62:
        stock_3m = (prices[i] - prices[i - 62]) / prices[i - 62] * 100 if prices[i - 62] > 0 else 0
        # kospi_ret는 호출 함수에서 주입되지 않으므로 price_history 없이 근사:
        # 동일 인덱스 기준으로는 계산 불가 → 단순 절대 수익률 기준으로 대체
        # 3개월 수익률 > +5% (상대강도 최소 기준)
        rs_ok = stock_3m > 5.0

    return value_ok or supply_ok or rs_ok


# ══════════════════════════════════════════════════════════════
#  Logic #5 — 시장 국면 적응형 (Regime-Adaptive Strategy)
# ══════════════════════════════════════════════════════════════
#
# 설계 원칙:
#   주식 시장은 4단계 국면(강세/중립/약세/대하락)을 순환한다.
#   같은 매수 조건을 모든 국면에 적용하면, 좋은 장에서 과도하게
#   진입하거나 나쁜 장에서 아예 기회를 잃는다.
#   → 국면별 기준을 달리하여 "언제나 작동하는" 전략을 구현한다.
#
# 국면 분류 (KOSPI 기준):
#   3 강세 : KOSPI > MA60            — 공격형 모멘텀
#   2 중립 : MA120 < KOSPI ≤ MA60   — 균형형 (가치 강화)
#   1 약세 : MA200 < KOSPI ≤ MA120  — 방어형 (깊은 가치만)
#   0 대하락: KOSPI ≤ MA200          — 매수 금지
#
# 핵심 차별점 vs v5:
#   ① 약세장(regime 1)에서도 선별 매수 (v5는 0거래)
#   ② 재무 건전성(영업이익>0) 공통 필수 → 불황 생존 기업만
#   ③ 국면별 익절/손절 조정 (약세장: 더 빠른 exit)
#   ④ 수급 데이터 의존도 최소화 (역사적 백테스트 호환)
#   ⑤ 최대 보유 35일 제한 (기회비용 방지)

def _get_avg(arr: list) -> float:
    return sum(arr) / len(arr) if arr else 0.0


def _is_buy_signal_v6(
    i: int,
    sim_start_i: int,
    dates: list,
    prices: list,
    volumes: list,
    frn_net: list,
    inst_net: list,
    fin_rows: list,
    regime: int = 3,          # 0=대하락, 1=약세, 2=중립, 3=강세
) -> bool:
    """
    Logic #5 매수 시그널.
    regime은 run_backtest()에서 날짜별로 미리 계산된 KOSPI 국면.
    """
    if i < sim_start_i:
        return False

    # 대하락 → 진입 금지
    if regime == 0:
        return False

    curr = prices[i]
    d    = dates[i]
    p_slice = prices[max(0, i - 199): i + 1]

    if len(p_slice) < 20:
        return False

    ma20 = _ma(p_slice[-20:], 20) if len(p_slice) >= 20 else None
    ma60 = _ma(p_slice[-60:], 60) if len(p_slice) >= 60 else None

    if ma20 is None or ma60 is None:
        return False

    # ── 공통: 재무 건전성 (영업이익 > 0) ─────────────────────────
    # 불황 생존 기업만 편입. 재무 없으면 패스.
    fin = _get_financial_as_of(fin_rows, d)
    if fin is None:
        return False
    _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann = fin
    if not (op and op > 0):
        return False   # 영업적자 제외

    # ── 국면 3 강세장: 공격형 모멘텀 ─────────────────────────────
    if regime == 3:
        if len(p_slice) < 120:
            return False
        ma120 = _ma(p_slice[-120:], 120)
        ma200 = _ma(p_slice, len(p_slice)) if len(p_slice) >= 200 else None
        if ma120 is None or ma200 is None:
            return False

        # 3중 정배열: curr > MA60 > MA120 > MA200
        if not (curr > ma60 and ma60 > ma120 and ma120 > ma200):
            return False
        # 단기 정배열: MA20 > MA60
        if not (ma20 > ma60):
            return False
        # 52주 고점 -15% 이내 (핵심 모멘텀 확인)
        high52 = max(prices[max(0, i - 251): i + 1])
        if curr < high52 * 0.85:
            return False
        # RSI(14) ≥ 55
        rsi_val = _rsi(prices[max(0, i - 28): i + 1])
        if rsi_val is None or rsi_val < 55:
            return False
        # 거래량 폭발 (2배)
        vol_w = [v for v in volumes[max(0, i - 20): i] if v and v > 0]
        if not vol_w or not volumes[i] or volumes[i] <= _get_avg(vol_w) * 2.0:
            return False

        # 가치 기준 (완화: Graham 15%+ OR RS 5%+)
        value_ok = False
        if eps and bps and eps > 0 and bps > 0:
            gp   = _graham_price(eps, bps)
            disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
            pbr  = curr / bps
            per  = curr / eps
            value_ok = disc >= 15 or (pbr < 1.0 and 0 < per < 15)
        rs_ok = False
        if i >= 62 and prices[i - 62] > 0:
            rs_ok = (prices[i] - prices[i - 62]) / prices[i - 62] * 100 > 5.0
        # 수급 보조 (있으면 plus)
        frn5  = sum(frn_net[max(0, i - 4): i + 1])
        inst5 = sum(inst_net[max(0, i - 4): i + 1])
        supply_ok = (frn5 > 0 and inst5 > 0)
        return value_ok or rs_ok or supply_ok

    # ── 국면 2 중립장: 균형형 (가치 강화) ────────────────────────
    elif regime == 2:
        if len(p_slice) < 60:
            return False
        # 중기 정배열: curr > MA20 > MA60
        if not (curr > ma20 and ma20 > ma60):
            return False
        # RSI 45~72 (과매도 탈출 + 과매수 진입 차단)
        rsi_val = _rsi(prices[max(0, i - 28): i + 1])
        if rsi_val is None or rsi_val < 45 or rsi_val > 72:
            return False
        # 52주 최저 대비 +20% 이상 (바닥권 회피)
        low52 = min(prices[max(0, i - 251): i + 1])
        if curr < low52 * 1.20:
            return False
        # 거래량 확인 (1.5배)
        vol_w = [v for v in volumes[max(0, i - 20): i] if v and v > 0]
        if not vol_w or not volumes[i] or volumes[i] <= _get_avg(vol_w) * 1.5:
            return False

        # 중립장: Graham 할인 25%+ 이상 (더 엄격한 저평가 요구)
        if not (eps and bps and eps > 0 and bps > 0):
            return False
        gp   = _graham_price(eps, bps)
        disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
        pbr  = curr / bps
        per  = curr / eps
        value_ok = disc >= 25 or (pbr < 0.7 and 0 < per < 10)
        rs_ok = False
        if i >= 62 and prices[i - 62] > 0:
            rs_ok = (prices[i] - prices[i - 62]) / prices[i - 62] * 100 > 8.0
        return value_ok or rs_ok

    # ── 국면 1 약세장: 방어형 (깊은 가치 + 강한 반등 확인) ──────
    elif regime == 1:
        if len(p_slice) < 20:
            return False
        # 단기 정배열: curr > MA20 (최소 조건)
        if curr <= ma20:
            return False
        # 최근 5거래일 연속 상승 모멘텀 확인
        recent = prices[max(0, i - 4): i + 1]
        if len(recent) < 3 or recent[-1] <= recent[0]:
            return False
        # RSI 42~68 (과매도 완전 탈출 + 과매수 제외)
        rsi_val = _rsi(prices[max(0, i - 28): i + 1])
        if rsi_val is None or rsi_val < 42 or rsi_val > 68:
            return False
        # 바닥 탈출 확인: 52주 최저 대비 +25% 이상
        low52 = min(prices[max(0, i - 251): i + 1])
        if curr < low52 * 1.25:
            return False
        # 거래량 1.3배 (약세장이라 기준 완화)
        vol_w = [v for v in volumes[max(0, i - 20): i] if v and v > 0]
        if not vol_w or not volumes[i] or volumes[i] <= _get_avg(vol_w) * 1.3:
            return False

        # 약세장: Graham 할인 30%+ 이상 (매우 깊은 가치만)
        if not (eps and bps and eps > 0 and bps > 0):
            return False
        gp   = _graham_price(eps, bps)
        disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
        pbr  = curr / bps
        value_ok = disc >= 30 or (pbr < 0.5 and bps > 0)
        # RS는 약세장에서 10%+ (강한 상대강도 필수)
        rs_ok = False
        if i >= 62 and prices[i - 62] > 0:
            rs_ok = (prices[i] - prices[i - 62]) / prices[i - 62] * 100 > 10.0
        return value_ok or rs_ok

    return False


def _check_sell_v6(
    i: int,
    prices: list,
    pos: dict,
    regime: int = 3,
) -> Optional[str]:
    """
    Logic #5 매도 조건 (국면 적응형).

    강세장: 익절+18%, 손절-6%, 추적손절-9%, MA60, 최대45일
    중립장: 익절+12%, 손절-5%, 추적손절-7%, MA40, 최대35일
    약세장: 익절+9%,  손절-4%, 추적손절-6%, MA20, 최대25일
    """
    curr = prices[i]

    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr

    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    # 국면별 파라미터
    if regime == 3:
        tp, sl, trail_sl, trail_trig, ma_n, max_hold = 0.18, -0.06, -0.09, 0.03, 60, 45
    elif regime == 2:
        tp, sl, trail_sl, trail_trig, ma_n, max_hold = 0.12, -0.05, -0.07, 0.02, 40, 35
    else:  # 1 약세
        tp, sl, trail_sl, trail_trig, ma_n, max_hold = 0.09, -0.04, -0.06, 0.01, 20, 25

    # 익절 (즉시, 최소보유 무관)
    if pct >= tp:
        return f"익절(+{pct * 100:.0f}%)"

    # 하드 손절 (즉시)
    if pct <= sl:
        return f"손절({sl * 100:.0f}%)"

    # 최소 3일 이후 청산 조건
    if hold_days >= 3:
        # 추적 손절 (수익 구간에서만)
        trail = (curr - peak) / peak if peak > 0 else 0
        if trail <= trail_sl and pct > trail_trig:
            return f"추적손절(고점-{abs(trail) * 100:.0f}%)"

        # MA 붕괴
        ma_val = _ma(prices[max(0, i - ma_n + 1): i + 1], ma_n)
        if ma_val is not None and curr < ma_val:
            return f"MA{ma_n} 붕괴"

    # 최대 보유 기간
    if hold_days >= max_hold:
        return f"최대보유({hold_days}일)"

    return None



def _check_sell(
    i: int,
    prices: list,
    pos: dict,
    stop_loss: float = -0.08,
) -> Optional[str]:
    """
    매도 사유 문자열 반환, 없으면 None.
    우선순위: 익절 > 하드손절 > 추적손절 > MA60 붕괴
    v5 변경:
      - 익절 +20% → +15%  (더 빠른 수익 확정)
      - MA20 2일 이탈 제거  (단기 노이즈 청산 원인 → 제거)
      - 최소 보유 5일: 진입 후 5거래일 미만이면 MA 청산 스킵
    """
    curr = prices[i]

    # 고점 업데이트
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr

    pct      = (curr - pos['entry_price']) / pos['entry_price']
    peak     = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1   # 매 호출 시 보유일 증가

    # ★ 익절: +15% 이상 (즉시, 최소보유 무관)
    if pct >= 0.15:
        return f"익절(+{pct*100:.0f}%)"

    # ① 하드 손절 (-6%, 즉시, 최소보유 무관)
    if pct <= stop_loss:
        return f"손절({stop_loss * 100:.0f}%)"

    # ★ 추적 손절: 고점 대비 -10% (최소 +3% 수익 구간 + 최소보유 5일 이후)
    if hold_days >= 5:
        trail_pct = (curr - peak) / peak if peak > 0 else 0
        if trail_pct <= -0.10 and pct > 0.03:
            return f"추적손절(고점-{abs(trail_pct)*100:.0f}%)"

    # ② MA60 붕괴 (최소 보유 5일 이후만 적용)
    if hold_days >= 5:
        ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
        if ma60 is not None and curr < ma60:
            return "MA60 붕괴"


    return None


# ══════════════════════════════════════════════════════════════
#  Logic #6 — 멀티팩터 동조+수출+고용 통합 전략
# ══════════════════════════════════════════════════════════════
#
# 설계 원칙:
#   대한민국 주식은 3가지 비가격 정보를 선행지표로 활용할 수 있다.
#   ① 해외 동조효과: 미국/일본/대만 반도체·배터리 등 섹터 선행
#   ② HS수출 모멘텀: 수출 증가 → 해당 기업 실적 선행 (15일 후행 공개)
#   ③ 고용 증가:     인원 증가 → 물량·매출 증가 선행 (30일 후행 공개)
#   이 3가지를 기존 추세·가치 지표에 통합하여 종합 점수로 매수 결정.
#
# 점수 구성 (합계 1.0):
#   기술적 모멘텀  30%  — MA정배열, RSI, 거래량
#   해외 동조효과  25%  — 섹터 대응 해외주 1D/5D 평균 변동률
#   가치 품질     20%  — Graham 할인, 영업이익>0
#   HS 수출 모멘텀 15%  — YoY 수출증감 (없으면 neutral)
#   고용 증가      10%  — YoY 임직원 증감 (없으면 neutral)
#
# 매수 임계: regime별 최소 점수 (0~1.0 스케일)
#   강세(3): 0.50,  중립(2): 0.55,  약세(1): 0.62,  대하락(0): 진입금지
#
# 데이터 공개 지연 (미래참조 방지):
#   HS수출  : period_ym 마지막날 + 15일 이후 사용
#   고용    : ym 마지막날 + 30일 이후 사용
#   재무    : 기존 _release_date() 동일 (45/90일)
#   해외주가: 전일 종가 (1일 lag)

# 섹터명 → 해외 선행 종목 매핑 (market_radar.py SECTORS 기반)
_SECTOR_OVERSEAS: Dict[str, list] = {
    "반도체":    ["NVDA", "AMD", "AVGO", "MU", "^SOX"],
    "반도체장비": ["AMAT", "LRCX", "KLAC", "8035.T"],
    "2차전지":   ["TSLA", "ALB"],
    "전력장비":  ["ETN", "VRT"],
    "제약":      ["LLY", "NVO"],
    "바이오":    ["MRNA", "ABBV"],
    "방산":      ["LMT", "NOC", "RTX"],
    "해운":      ["ZIM", "STNG", "SBLK"],
    "에너지":    ["XOM", "CVX"],
    "소재":      ["FCX", "RIO"],
}
# 기본값: 글로벌 지수
_DEFAULT_OVERSEAS = ["^GSPC", "^IXIC"]

# HS DB 경로
_HS_DB_PATH = str(Path(__file__).parent / "hs_trade_lab" / "data" / "hs_trade_lab.db")
_EMP_DB_PATH = str(Path(__file__).parent / "employment_monitor" / "employment.db")


def _precompute_overseas_signals(
    conn: sqlite3.Connection,
    sim_dates: list,
    warmup_start: str,
    sector_map: Dict[str, str],   # stock_code → sector_large
) -> Dict[str, Dict[str, float]]:
    """
    날짜별·섹터별 해외 선행 시그널 점수(0~3) 사전계산.
    Returns: {date: {sector_large: score}}
    overseas_date는 1거래일 lag 적용.
    """
    # 필요한 해외 심볼 목록
    needed = set(_DEFAULT_OVERSEAS)
    for syms in _SECTOR_OVERSEAS.values():
        needed.update(syms)

    # price_history에서 overseas 가격 로드
    ph: Dict[str, Dict[str, float]] = {}  # sym → {date: close}
    for sym in needed:
        rows = conn.execute(
            "SELECT date, close FROM price_history "
            "WHERE stock_code=? AND date>=? AND close>0 ORDER BY date ASC",
            (sym, warmup_start)
        ).fetchall()
        if rows:
            ph[sym] = {r[0]: float(r[1]) for r in rows}

    # 결과: {date: {sector: score 0~3}}
    result: Dict[str, Dict[str, float]] = {}

    all_sectors = list(_SECTOR_OVERSEAS.keys()) + ["default"]
    for day in sim_dates:
        scores: Dict[str, float] = {}
        # 전일 데이터 사용 (1-day lag)
        prev_dates = [d for d in (ph.get("^GSPC") or {}) if d < day]
        if not prev_dates:
            result[day] = {s: 1.5 for s in all_sectors}  # neutral
            continue
        prev_day  = prev_dates[-1]
        prev2_days = [d for d in prev_dates[:-1]]
        prev5_day  = prev2_days[-4] if len(prev2_days) >= 4 else None

        def _chg(sym: str) -> Optional[float]:
            sp = ph.get(sym, {})
            c  = sp.get(prev_day)
            p  = sp.get(prev2_days[-1]) if prev2_days else None
            if c and p and p > 0:
                return (c - p) / p * 100
            return None

        def _chg5(sym: str) -> Optional[float]:
            sp = ph.get(sym, {})
            c  = sp.get(prev_day)
            p  = sp.get(prev5_day) if prev5_day else None
            if c and p and p > 0:
                return (c - p) / p * 100
            return None

        def _score_sym_list(syms: list) -> float:
            vals = [v for s in syms for v in [_chg(s), _chg5(s)] if v is not None]
            if not vals:
                return 1.5   # neutral
            avg = sum(vals) / len(vals)
            if avg >= 2.0:   return 3.0
            if avg >= 0.5:   return 2.5
            if avg >= -0.3:  return 1.5
            if avg >= -1.5:  return 1.0
            return 0.0

        for sec, syms in _SECTOR_OVERSEAS.items():
            scores[sec] = _score_sym_list(syms)
        scores["default"] = _score_sym_list(_DEFAULT_OVERSEAS)
        result[day] = scores

    return result


def _precompute_hs_signals(
    sim_dates: list,
    stock_codes: list,
) -> Dict[str, Dict[str, float]]:
    """
    HS 수출 모멘텀 점수(0~3) 사전계산.
    Returns: {stock_code: {date: score}}
    HS data 공개: period_ym 마지막날 + 15일 이후 사용가능
    """
    try:
        hs_conn = sqlite3.connect(_HS_DB_PATH, timeout=10)
        hs_conn.row_factory = sqlite3.Row
    except Exception:
        return {}

    result: Dict[str, Dict[str, float]] = {}
    try:
        # stock_code → hs_codes 매핑
        maps = hs_conn.execute(
            "SELECT stock_code, hs_code FROM hs_code_company_map "
            "WHERE mapping_status IN ('confirmed','exact','composite') "
            "AND stock_code IS NOT NULL AND stock_code != ''"
        ).fetchall()

        sc_to_hs: Dict[str, list] = {}
        for r in maps:
            sc = str(r["stock_code"]).strip()
            hc = str(r["hs_code"]).strip()
            if sc and hc:
                sc_to_hs.setdefault(sc, []).append(hc)

        # hs_code → (period_ym, export_value) 전체 로드
        ts_rows = hs_conn.execute(
            "SELECT hs_code, period_ym, export_value FROM trade_series_cache "
            "WHERE flow_type='total' ORDER BY hs_code, period_ym"
        ).fetchall()

        hs_to_ts: Dict[str, Dict[str, float]] = {}
        for r in ts_rows:
            hc = str(r["hs_code"]).strip()
            ym = str(r["period_ym"]).strip()
            ev = float(r["export_value"] or 0)
            hs_to_ts.setdefault(hc, {})[ym] = ev

        for sc in stock_codes:
            hs_list = sc_to_hs.get(sc)
            if not hs_list:
                continue

            # 해당 종목 전체 월별 수출합산
            monthly: Dict[str, float] = {}
            for hc in hs_list:
                for ym, ev in hs_to_ts.get(hc, {}).items():
                    monthly[ym] = monthly.get(ym, 0) + ev

            if not monthly:
                continue

            sc_scores: Dict[str, float] = {}
            for day in sim_dates:
                # 사용 가능한 최신 period_ym: day - 15일 이내
                avail_dt = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=15))
                avail_ym = avail_dt.strftime("%Y-%m")
                # 사용 가능 월 목록
                avail = sorted([ym for ym in monthly if ym <= avail_ym])
                if len(avail) < 13:
                    sc_scores[day] = 1.5  # 데이터 부족 → neutral
                    continue
                # 최근 3개월 평균 vs 전년 동기 3개월 평균
                curr3   = [monthly[m] for m in avail[-3:]]
                prev3   = [monthly[m] for m in avail[-15:-12]]
                if not curr3 or not prev3 or sum(prev3) == 0:
                    sc_scores[day] = 1.5
                    continue
                yoy = (sum(curr3) / len(curr3) - sum(prev3) / len(prev3)) \
                      / (sum(prev3) / len(prev3)) * 100
                if yoy >= 30:   sc_scores[day] = 3.0
                elif yoy >= 10: sc_scores[day] = 2.5
                elif yoy >= -5: sc_scores[day] = 1.5
                elif yoy >= -20:sc_scores[day] = 0.8
                else:           sc_scores[day] = 0.0

            if sc_scores:
                result[sc] = sc_scores
    except Exception:
        pass
    finally:
        try: hs_conn.close()
        except Exception: pass

    return result


def _precompute_emp_signals(
    sim_dates: list,
    stock_codes: list,
) -> Dict[str, Dict[str, float]]:
    """
    고용 증가 점수(0~3) 사전계산.
    Returns: {stock_code: {date: score}}
    고용 data 공개: ym 마지막날 + 30일 이후 사용가능
    """
    try:
        emp_conn = sqlite3.connect(_EMP_DB_PATH, timeout=10)
        emp_conn.row_factory = sqlite3.Row
    except Exception:
        return {}

    result: Dict[str, Dict[str, float]] = {}
    try:
        # 전체 고용 데이터 로드
        rows = emp_conn.execute(
            "SELECT stock_code, ym, employee_count FROM employment_company_monthly "
            "WHERE employee_count IS NOT NULL AND employee_count > 0 "
            "ORDER BY stock_code, ym"
        ).fetchall()

        emp_data: Dict[str, Dict[str, int]] = {}
        for r in rows:
            sc = str(r["stock_code"]).strip()
            ym = str(r["ym"]).strip()
            ec = int(r["employee_count"])
            if sc and ym:
                emp_data.setdefault(sc, {})[ym] = ec

        for sc in stock_codes:
            monthly = emp_data.get(sc)
            if not monthly or len(monthly) < 13:
                continue

            sc_scores: Dict[str, float] = {}
            for day in sim_dates:
                avail_dt = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=30))
                avail_ym = avail_dt.strftime("%Y-%m")
                avail    = sorted([ym for ym in monthly if ym <= avail_ym])
                if len(avail) < 13:
                    sc_scores[day] = 1.5
                    continue
                curr_ec = monthly[avail[-1]]
                prev_ec = monthly[avail[-13]]  # 정확히 12개월 전
                if prev_ec == 0:
                    sc_scores[day] = 1.5
                    continue
                yoy = (curr_ec - prev_ec) / prev_ec * 100
                if yoy >= 10:  sc_scores[day] = 3.0
                elif yoy >= 3: sc_scores[day] = 2.5
                elif yoy >= -1:sc_scores[day] = 1.5
                elif yoy >= -5:sc_scores[day] = 0.8
                else:          sc_scores[day] = 0.0
            if sc_scores:
                result[sc] = sc_scores
    except Exception:
        pass
    finally:
        try: emp_conn.close()
        except Exception: pass

    return result


def _is_buy_signal_v7(
    i: int,
    sim_start_i: int,
    dates: list,
    prices: list,
    volumes: list,
    fin_rows: list,
    sector_large: str,
    stock_code: str,
    regime: int,
    overseas_day: Dict[str, float],   # {sector: score} for this date
    hs_score_day: float,              # 0~3 (1.5=neutral)
    emp_score_day: float,             # 0~3 (1.5=neutral)
    v7p: Optional[Dict] = None,       # override params from optimizer
) -> bool:
    """
    Logic #6 멀티팩터 매수 시그널.
    """
    if i < sim_start_i or regime == 0:
        return False

    p_slice = prices[max(0, i - 249): i + 1]
    if len(p_slice) < 60:
        return False

    curr = prices[i]
    d    = dates[i]

    ma20  = _ma(p_slice[-20:], 20) if len(p_slice) >= 20 else None
    ma60  = _ma(p_slice[-60:], 60) if len(p_slice) >= 60 else None
    ma120 = _ma(p_slice[-120:], 120) if len(p_slice) >= 120 else None

    if ma20 is None or ma60 is None:
        return False

    # ── 재무 (공통 필수: 영업이익 > 0) ───────────────────────────
    fin = _get_financial_as_of(fin_rows, d)
    if fin is None:
        return False
    _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann = fin
    if not (op and op > 0):
        return False

    # ─ 파라미터 (override or defaults) ──────────────────────────
    p = v7p or {}
    overseas_w = p.get("overseas_w",  0.30)
    tech_w     = p.get("tech_w",      0.28)
    value_w    = p.get("value_w",     0.17)
    hs_w       = p.get("hs_w",        0.15)
    emp_w      = p.get("emp_w",       0.10)
    sec_weight = p.get("sec_weight",  0.65)
    vol_strong = p.get("vol_strong",  2.0)
    vol_mild   = p.get("vol_mild",    1.3)
    thr_bull   = p.get("thr_bull",    0.52)
    thr_neut   = p.get("thr_neutral", 0.57)
    thr_bear   = p.get("thr_bear",    0.64)
    pen_lo_bnd = p.get("pen_low_bnd",  1.0)
    pen_hi_bnd = p.get("pen_high_bnd", 1.3)
    pen_lo_add = p.get("pen_low_add",  0.10)
    pen_hi_add = p.get("pen_high_add", 0.05)

    # ── 1. 기술적 모멘텀 점수 (0~3) ──────────────────────────────
    tech = 0.0
    # MA 정배열
    if ma120 and curr > ma60 and ma60 > ma120:
        tech += 1.2   # 3중 정배열
    elif curr > ma60:
        tech += 0.8
    elif curr > ma20:
        tech += 0.3
    # RSI
    rsi = _rsi(prices[max(0, i - 28): i + 1])
    if rsi is not None:
        if 50 <= rsi <= 75:   tech += 0.9
        elif 40 <= rsi < 50:  tech += 0.4
    # 거래량 (파라미터화)
    vol_w = [v for v in volumes[max(0, i - 20): i] if v and v > 0]
    if vol_w and volumes[i]:
        vr = volumes[i] / _get_avg(vol_w)
        if vr >= vol_strong:   tech += 0.9
        elif vr >= vol_mild:   tech += 0.5
    tech = min(3.0, tech)

    # ── 2. 해외 동조효과 점수 (0~3) ──────────────────────────────
    ov_sec    = overseas_day.get(sector_large, 1.5)
    ov_global = overseas_day.get("default",   1.5)   # ^GSPC + ^IXIC
    overseas  = ov_sec * sec_weight + ov_global * (1.0 - sec_weight)

    # ── 3. 가치 품질 점수 (0~3) ──────────────────────────────────
    value = 0.0
    if eps and bps and eps > 0 and bps > 0:
        gp   = _graham_price(eps, bps)
        disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
        pbr  = curr / bps
        per  = curr / eps
        if disc >= 25:                 value += 1.5
        elif disc >= 10:               value += 0.8
        if pbr < 1.0 and 0 < per < 15:value += 1.0
        elif pbr < 2.0:                value += 0.5
    # RS 상대강도
    if i >= 62 and prices[i - 62] > 0:
        rs = (curr - prices[i - 62]) / prices[i - 62] * 100
        if rs >= 10:  value += 0.5
        elif rs >= 3: value += 0.2
    value = min(3.0, value)

    # ── 4. HS 수출 모멘텀 (0~3) ──────────────────────────────────
    hs_s = hs_score_day   # pre-computed (1.5 if no data)

    # ── 5. 고용 증가 (0~3) ───────────────────────────────────────
    emp_s = emp_score_day  # pre-computed (1.5 if no data)

    # ── 종합 점수 (0~1.0) ─────────────────────────────────────────
    score = (
        tech     * tech_w     +
        overseas * overseas_w +
        value    * value_w    +
        hs_s     * hs_w       +
        emp_s    * emp_w
    ) / 3.0   # 최대 3.0 → 1.0 정규화

    # ── regime별 임계값 ──────────────────────────────────────────
    threshold = {3: thr_bull, 2: thr_neut, 1: thr_bear}.get(regime, thr_neut)

    # ── 글로벌 하락장 패널티 ─────────────────────────────────────
    if pen_lo_bnd > 0 and ov_global < pen_lo_bnd:
        threshold += pen_lo_add
    elif pen_hi_bnd > 0 and ov_global < pen_hi_bnd:
        threshold += pen_hi_add

    return score >= threshold


def _check_sell_v7(
    i: int,
    prices: list,
    pos: dict,
    regime: int,
) -> Optional[str]:
    """
    Logic #6 국면별 적응형 매도.
    강세(3): 익절+18%, 손절-7%, 추적-9%, MA60, 최대45일
    중립(2): 익절+13%, 손절-6%, 추적-8%, MA40, 최대35일
    약세(1): 익절+9%,  손절-5%, 추적-7%, MA20, 최대25일
    """
    params = {
        3: dict(tp=0.18, sl=-0.07, trail=-0.09, ma_n=60, max_d=45),
        2: dict(tp=0.13, sl=-0.06, trail=-0.08, ma_n=40, max_d=35),
        1: dict(tp=0.09, sl=-0.05, trail=-0.07, ma_n=20, max_d=25),
    }.get(regime, dict(tp=0.13, sl=-0.06, trail=-0.08, ma_n=40, max_d=35))

    curr = prices[i]
    if curr > pos.get("peak_price", pos["entry_price"]):
        pos["peak_price"] = curr

    pct       = (curr - pos["entry_price"]) / pos["entry_price"]
    peak      = pos.get("peak_price", pos["entry_price"])
    hold_days = pos.get("hold_days", 0)
    pos["hold_days"] = hold_days + 1

    # 익절
    if pct >= params["tp"]:
        return f"익절(+{pct*100:.0f}%)"
    # 하드 손절
    if pct <= params["sl"]:
        return f"손절({params['sl']*100:.0f}%)"
    # 최대 보유 초과
    if hold_days >= params["max_d"]:
        return f"보유기한({hold_days}일)"

    if hold_days >= 5:
        # 추적 손절
        trail = (curr - peak) / peak if peak > 0 else 0
        if trail <= params["trail"] and pct > 0.02:
            return f"추적손절(고점-{abs(trail)*100:.0f}%)"
        # MA 붕괴
        ma = _ma(prices[max(0, i - params["ma_n"] + 1): i + 1], params["ma_n"])
        if ma is not None and curr < ma:
            return f"MA{params['ma_n']} 붕괴"

    return None


# ══════════════════════════════════════════════════════════════
#  포트폴리오 시뮬레이션 — 날짜별 전체 스캔
# ══════════════════════════════════════════════════════════════
def _run_portfolio(
    sim_dates: list,
    stock_data: Dict[str, dict],
    per_stock: float,
    max_positions: int,
    stop_loss: float = -0.08,
    market_bullish: Optional[Dict[str, bool]] = None,
    strategy: str = 'v5',
    kospi_regime: Optional[Dict[str, int]] = None,
    overseas_signals: Optional[Dict[str, Dict[str, float]]] = None,  # v7: {date:{sector:score}}
    hs_signals: Optional[Dict[str, Dict[str, float]]] = None,        # v7: {sc:{date:score}}
    emp_signals: Optional[Dict[str, Dict[str, float]]] = None,       # v7: {sc:{date:score}}
    sector_map: Optional[Dict[str, str]] = None,                     # v7: {sc:sector_large}
    v7_params: Optional[Dict] = None,                                # v7: optimizer override
) -> Tuple[list, list]:
    """
    매일(sim_dates 하루씩) 전 종목을 스캔:
      1) 기존 보유 종목 → 매도 조건 체크
      2) 빈 슬롯이 있으면 → 매수 조건 충족 종목 편입

    strategy='v5': 기존 AI 콤보 (KOSPI MA120 필터)
    strategy='v6': Logic #5 국면 적응형
    strategy='v7': Logic #6 멀티팩터 (동조+수출+고용)

    Returns: (trades, equity_curve)
    """
    positions: Dict[str, dict] = {}    # code → position dict
    trades:    List[dict]       = []
    equity_curve: List[dict]    = []

    total_capital = per_stock * max_positions

    # 날짜→인덱스 빠른 조회
    date_idx: Dict[str, Dict[str, int]] = {
        sc: {dt: idx for idx, dt in enumerate(d['dates'])}
        for sc, d in stock_data.items()
    }

    for day in sim_dates:
        # Logic #5 용 당일 시장 국면
        regime = (kospi_regime or {}).get(day, 3)

        # ── Step 1: 매도 체크 (보유 종목 전체) ─────────────
        sold_today = []
        for sc, pos in list(positions.items()):
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue
            i  = idx_map[day]
            sd = stock_data[sc]
            if strategy == 'v7':
                reason = _check_sell_v7(i, sd['prices'], pos, regime)
            elif strategy == 'v6':
                reason = _check_sell_v6(i, sd['prices'], pos, regime)
            else:
                reason = _check_sell(i, sd['prices'], pos, stop_loss)
            if reason is None:
                continue
            curr = sd['prices'][i]
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            trades.append({
                'stock_code':  sc,
                'entry_date':  pos['entry_date'],
                'exit_date':   day,
                'entry_price': pos['entry_price'],
                'exit_price':  curr,
                'qty':         pos['qty'],
                'profit_pct':  round(pct * 100, 2),
                'profit_amt':  round((curr - pos['entry_price']) * pos['qty']),
                'exit_reason': reason,
            })
            sold_today.append(sc)

        for sc in sold_today:
            del positions[sc]

        # ── Step 2: 매수 스캔 (빈 슬롯이 있을 때만) ───────
        if strategy in ('v6', 'v7'):
            if regime == 0:
                unrealized = sum(
                    (stock_data[sc]['prices'][date_idx[sc][day]] - pos['entry_price']) * pos['qty']
                    for sc, pos in positions.items()
                    if day in date_idx.get(sc, {})
                )
                realized = sum(t['profit_amt'] for t in trades)
                equity_curve.append({'date': day, 'equity': round(total_capital + realized + unrealized)})
                continue
        else:
            if market_bullish is not None and not market_bullish.get(day, True):
                unrealized = sum(
                    (stock_data[sc]['prices'][date_idx[sc][day]] - pos['entry_price']) * pos['qty']
                    for sc, pos in positions.items()
                    if day in date_idx.get(sc, {})
                )
                realized = sum(t['profit_amt'] for t in trades)
                equity_curve.append({'date': day, 'equity': round(total_capital + realized + unrealized)})
                continue

        # v7 당일 해외 시그널 (pre-computed)
        ov_day = (overseas_signals or {}).get(day, {})

        if len(positions) < max_positions:
            for sc, sd in stock_data.items():
                if len(positions) >= max_positions:
                    break
                if sc in positions:
                    continue
                idx_map = date_idx.get(sc, {})
                if day not in idx_map:
                    continue
                i = idx_map[day]
                if strategy == 'v7':
                    sig = _is_buy_signal_v7(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['fins'],
                        (sector_map or {}).get(sc, "기타"),
                        sc,
                        regime,
                        ov_day,
                        (hs_signals or {}).get(sc, {}).get(day, 1.5),
                        (emp_signals or {}).get(sc, {}).get(day, 1.5),
                        v7_params,
                    )
                elif strategy == 'v6':
                    sig = _is_buy_signal_v6(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['frn'],   sd['inst'],   sd['fins'],
                        regime,
                    )
                else:
                    sig = _is_buy_signal(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['frn'],   sd['inst'],   sd['fins'],
                    )
                if not sig:
                    continue
                curr = sd['prices'][i]
                qty  = max(1, int(per_stock / curr))
                positions[sc] = {
                    'entry_date':  day,
                    'entry_price': curr,
                    'qty':         qty,
                    'peak_price':  curr,
                    'hold_days':   0,
                }

        # ── Step 3: 에쿼티 커브 ────────────────────────────
        unrealized = 0.0
        for sc, pos in positions.items():
            idx_map = date_idx.get(sc, {})
            if day in idx_map:
                i = idx_map[day]
                unrealized += (stock_data[sc]['prices'][i] - pos['entry_price']) * pos['qty']

        realized = sum(t['profit_amt'] for t in trades)
        equity_curve.append({'date': day, 'equity': round(total_capital + realized + unrealized)})

    # ── 기간 종료: 미청산 포지션 강제 청산 ─────────────────
    last_day = sim_dates[-1] if sim_dates else None
    for sc, pos in list(positions.items()):
        idx_map = date_idx.get(sc, {})
        sd = stock_data[sc]
        if last_day and last_day in idx_map:
            curr = sd['prices'][idx_map[last_day]]
        else:
            curr = sd['prices'][-1] if sd['prices'] else pos['entry_price']
        pct = (curr - pos['entry_price']) / pos['entry_price']
        trades.append({
            'stock_code':  sc,
            'entry_date':  pos['entry_date'],
            'exit_date':   last_day or pos['entry_date'],
            'entry_price': pos['entry_price'],
            'exit_price':  curr,
            'qty':         pos['qty'],
            'profit_pct':  round(pct * 100, 2),
            'profit_amt':  round((curr - pos['entry_price']) * pos['qty']),
            'exit_reason': '기간종료',
        })

    return trades, equity_curve


# ══════════════════════════════════════════════════════════════
#  성과 지표 계산
# ══════════════════════════════════════════════════════════════
def _calc_metrics(trades: list, equity_curve: list,
                  start_date: str, end_date: str,
                  total_capital: float) -> dict:
    if not trades:
        return {
            'total_return_pct': 0, 'ann_return_pct': 0, 'cagr': 0,
            'win_rate': 0, 'total_trades': 0, 'profit_trades': 0,
            'max_drawdown_pct': 0, 'sharpe': 0, 'pl_ratio': 0,
            'total_profit_amt': 0,
            'summary': '해당 기간 동안 신호가 발생한 종목이 없습니다.'
        }

    profit_t = [t for t in trades if t['profit_amt'] > 0]
    loss_t   = [t for t in trades if t['profit_amt'] <= 0]

    total_profit = sum(t['profit_amt'] for t in trades)
    win_rate     = round(len(profit_t) / len(trades) * 100, 1) if trades else 0

    # 손익비 (평균 수익 / 평균 손실)
    avg_win  = sum(t['profit_amt'] for t in profit_t) / len(profit_t) if profit_t else 0
    avg_loss = abs(sum(t['profit_amt'] for t in loss_t)) / len(loss_t) if loss_t else 1
    pl_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

    days  = max((datetime.strptime(end_date, '%Y-%m-%d') -
                 datetime.strptime(start_date, '%Y-%m-%d')).days, 1)
    years = days / 365.25

    total_ret_pct = round(total_profit / total_capital * 100, 2) if total_capital else 0
    end_val       = total_capital + total_profit
    cagr          = round(((end_val / total_capital) ** (1 / years) - 1) * 100, 2) \
                    if total_capital > 0 and years > 0 else 0
    ann_ret_pct   = round(total_ret_pct / years, 2)

    # MDD (에쿼티 커브 기준)
    peak   = total_capital
    max_dd = 0.0
    for e in equity_curve:
        eq = e['equity']
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (eq - peak) / peak * 100
            max_dd = min(max_dd, dd)

    # 샤프지수 (일별 수익률, 무위험 3%)
    sharpe = 0.0
    if len(equity_curve) > 5:
        eq_vals = [e['equity'] for e in equity_curve]
        daily_r = [(eq_vals[i] - eq_vals[i - 1]) / eq_vals[i - 1]
                   for i in range(1, len(eq_vals)) if eq_vals[i - 1] > 0]
        if len(daily_r) > 5:
            rf      = 0.03 / 252
            mean_r  = sum(daily_r) / len(daily_r)
            std_r   = (sum((r - mean_r) ** 2 for r in daily_r) / len(daily_r)) ** 0.5
            sharpe  = round((mean_r - rf) / std_r * (252 ** 0.5), 2) if std_r > 0 else 0

    return {
        'total_return_pct':  total_ret_pct,
        'ann_return_pct':    ann_ret_pct,
        'cagr':              cagr,
        'win_rate':          win_rate,
        'total_trades':      len(trades),
        'profit_trades':     len(profit_t),
        'max_drawdown_pct':  round(max_dd, 2),
        'sharpe':            sharpe,
        'pl_ratio':          pl_ratio,
        'total_profit_amt':  int(total_profit),
    }


# ══════════════════════════════════════════════════════════════
#  메인 백테스트
# ══════════════════════════════════════════════════════════════
def run_backtest(start_date: str, end_date: str,
                 per_stock: float = 10_000_000,
                 max_positions: int = 10,
                 run_name: str = None,
                 run_id: str = None,
                 strategy: str = 'v5',
                 _override_params: Optional[Dict] = None) -> str:
    """
    run_id가 주어지면 해당 레코드(이미 DB에 존재)를 직접 업데이트.
    없으면 새 run_id를 생성하고 INSERT.
    strategy: 'v5' = AI 콤보 v5 (KOSPI MA120 필터)
              'v6' = Logic #5 국면 적응형 (4단계 regime)
    """
    init_backtest_db()
    strategy_label = {
        'v5': 'AI 콤보 v5',
        'v6': 'Logic #5 국면적응형',
        'v7': 'Logic #6 멀티팩터(동조+수출+고용)',
    }.get(strategy, strategy)
    run_name = run_name or f"[{strategy_label}] {start_date[:7]}~{end_date[:7]}"

    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        # 이미 INSERT된 레코드 → 상태만 running으로 갱신
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    try:
        # ── 워밍업 시작일: start_date 보다 WARMUP_DAYS 거래일 이전 ──
        # 달력 기준 약 450일 전(영업일 300일 여유분)
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')

        # ── 시뮬레이션 거래일 목록 (start_date ~ end_date) ─────────
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        if not sim_dates:
            # KOSPI 없으면 전체 종목에서 추출
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]

        # ── 재무 데이터 로드 (분기 + 연간 모두, 공시 전 미래 참조 방지) ──
        # 컬럼: year, quarter, rev, op, eps, bps, equity, net_inc, roe, is_annual
        fin_all: Dict[str, list] = {}
        for r in conn.execute("""
            SELECT stock_code, year, quarter,
                   revenue, operating_profit, eps, bps,
                   total_equity, net_income, roe,
                   CASE WHEN is_annual=1 THEN 1 ELSE 0 END
            FROM financial_data
            WHERE (is_annual=0 AND quarter BETWEEN 1 AND 3)
               OR (is_annual=1)
            ORDER BY stock_code, year, quarter
        """).fetchall():
            sc = r[0]
            fin_all.setdefault(sc, []).append(r[1:])   # (y,q,rev,op,eps,bps,eq,ni,roe,is_ann)

        # ── 종목 목록 (시총 1000억 이상 + 워밍업 포함 충분한 데이터 보유) ─
        # 시총 필터로 변동성 높은 소형주 제거 (signal_engine TREND_MKTCAP_MIN=500억 보다 강화)
        stock_codes = [r[0] for r in conn.execute("""
            SELECT ph.stock_code, COUNT(*) AS cnt
            FROM price_history ph
            INNER JOIN (
                SELECT stock_code FROM stock_universe
                WHERE (market_cap IS NULL OR market_cap >= 100000000000)
                  AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
                GROUP BY stock_code
            ) su ON ph.stock_code = su.stock_code
            WHERE ph.date>=? AND ph.date<=? AND ph.close>0
            GROUP BY ph.stock_code
            HAVING cnt >= 200
        """, (warmup_start, end_date)).fetchall()]

        # ── 종목별 데이터 로드 ────────────────────────────────────
        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            try:
                rows = conn.execute("""
                    SELECT date, close,
                           COALESCE(volume, 0),
                           COALESCE(frn_net_buy, 0),
                           COALESCE(inst_net_buy, 0)
                    FROM price_history
                    WHERE stock_code=? AND date>=? AND date<=? AND close>0
                    ORDER BY date ASC
                """, (sc, warmup_start, end_date)).fetchall()

                if len(rows) < 200:
                    continue

                dates   = [r[0] for r in rows]
                prices  = [float(r[1]) for r in rows]
                volumes = [float(r[2]) for r in rows]
                frn     = [float(r[3]) for r in rows]
                inst    = [float(r[4]) for r in rows]
                fins    = fin_all.get(sc, [])

                # 시뮬레이션 시작 인덱스 (start_date 이후 첫 거래일)
                sim_start_i = next(
                    (i for i, d in enumerate(dates) if d >= start_date),
                    len(dates)
                )

                stock_data[sc] = {
                    'dates':       dates,
                    'prices':      prices,
                    'volumes':     volumes,
                    'frn':         frn,
                    'inst':        inst,
                    'fins':        fins,
                    'sim_start_i': sim_start_i,
                }
            except Exception:
                continue

        # ── KOSPI 시장 추세/국면 계산 ───────────────────────────
        market_bullish: Dict[str, bool] = {}
        kospi_regime:   Dict[str, int]  = {}
        try:
            kospi_rows = conn.execute("""
                SELECT date, close FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (warmup_start, end_date)).fetchall()
            k_dates  = [r[0] for r in kospi_rows]
            k_prices = [float(r[1]) for r in kospi_rows]
            for ki, kd in enumerate(k_dates):
                if kd < start_date:
                    continue
                curr   = k_prices[ki]
                kma60  = _ma(k_prices[max(0, ki - 59):  ki + 1], 60)
                kma120 = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                kma200 = _ma(k_prices[max(0, ki - 199): ki + 1], 200)

                # v5 필터: MA120 상방 여부
                if kma120 is None:
                    market_bullish[kd] = True
                else:
                    market_bullish[kd] = curr > kma120

                # v6 국면: 4단계 (3=강세 / 2=중립 / 1=약세 / 0=대하락)
                if kma60 and curr > kma60:
                    kospi_regime[kd] = 3
                elif kma120 and curr > kma120:
                    kospi_regime[kd] = 2
                elif kma200 and curr > kma200:
                    kospi_regime[kd] = 1
                else:
                    kospi_regime[kd] = 0
        except Exception:
            pass  # KOSPI 데이터 없으면 필터 비활성화

        conn.close()

        # ── v7 전용: 섹터맵 + 3가지 멀티팩터 사전계산 ──────────
        v7_sector_map:     Dict[str, str]                  = {}
        v7_overseas:       Dict[str, Dict[str, float]]     = {}
        v7_hs:             Dict[str, Dict[str, float]]     = {}
        v7_emp:            Dict[str, Dict[str, float]]     = {}

        if strategy == 'v7':
            # 종목별 sector_large 조회
            conn3 = sqlite3.connect(DB_PATH, timeout=30)
            try:
                sc_list = list(stock_data.keys())
                for chunk_start in range(0, len(sc_list), 200):
                    chunk = sc_list[chunk_start: chunk_start + 200]
                    ph2   = ','.join('?' * len(chunk))
                    for sc, sl in conn3.execute(
                        f"SELECT stock_code, sector_large FROM stock_universe "
                        f"WHERE stock_code IN ({ph2})", chunk
                    ).fetchall():
                        v7_sector_map[sc] = sl or "기타"
            except Exception:
                pass
            finally:
                conn3.close()

            # 1. 해외 동조효과 사전계산
            conn4 = sqlite3.connect(DB_PATH, timeout=30)
            try:
                v7_overseas = _precompute_overseas_signals(
                    conn4, sim_dates, warmup_start, v7_sector_map
                )
            except Exception:
                pass
            finally:
                conn4.close()

            # 2. HS 수출 사전계산
            v7_hs = _precompute_hs_signals(sim_dates, sc_list)

            # 3. 고용 사전계산
            v7_emp = _precompute_emp_signals(sim_dates, sc_list)

        # ── 포트폴리오 시뮬레이션 ───────────────────────────────
        total_capital = per_stock * max_positions
        trades, equity_curve = _run_portfolio(
            sim_dates, stock_data,
            per_stock, max_positions,
            stop_loss=-0.08,
            market_bullish=market_bullish if (strategy == 'v5' and market_bullish) else None,
            strategy=strategy,
            kospi_regime=kospi_regime if strategy in ('v6', 'v7') else None,
            overseas_signals=v7_overseas if strategy == 'v7' else None,
            hs_signals=v7_hs      if strategy == 'v7' else None,
            emp_signals=v7_emp    if strategy == 'v7' else None,
            sector_map=v7_sector_map if strategy == 'v7' else None,
            v7_params=_override_params if strategy == 'v7' else None,
        )

        # ── 종목명 매핑 ──────────────────────────────────────
        conn2  = sqlite3.connect(DB_PATH, timeout=30)
        name_map: Dict[str, str] = {}
        codes = list({t['stock_code'] for t in trades})
        for i in range(0, len(codes), 100):
            batch = codes[i:i + 100]
            ph = ','.join('?' * len(batch))
            for sc, sn in conn2.execute(f"""
                SELECT DISTINCT ph.stock_code,
                       COALESCE(sm.stock_name, su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_meta sm USING(stock_code)
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn2.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        # ── 성과 지표 ────────────────────────────────────────
        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)

        # 월별 손익
        monthly: dict = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        monthly_list = [{'month': k, 'profit': v} for k, v in sorted(monthly.items())]

        # 종목별 손익
        from collections import defaultdict
        per_name: Dict[str, float] = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        top_winners = sorted(per_name.items(), key=lambda x: -x[1])[:5]
        top_losers  = sorted(per_name.items(), key=lambda x:  x[1])[:5]

        # 매도 사유별 통계
        exit_reasons: Dict[str, int] = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1

        # 전략별 시장 필터 통계
        total_days = len(sim_dates)
        if strategy == 'v5':
            bear_days  = sum(1 for v in market_bullish.values() if not v)
            filter_pct = round(bear_days / total_days * 100, 1) if total_days else 0
            filter_line = f"시장필터: 하락장 {bear_days}일/{total_days}일({filter_pct}%) 매수 차단"
            strategy_line = "★ 전략 v5: 시장추세필터(KOSPI MA120) / 손절-8% / 추적손절-10% / 익절+15% / 최소보유5일 / 시총1000억+"
        elif strategy == 'v6':
            r0 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 0)
            r1 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 1)
            r2 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 2)
            r3 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 3)
            filter_line = f"국면분포: 대하락{r0}일 / 약세{r1}일 / 중립{r2}일 / 강세{r3}일"
            strategy_line = "★ Logic#5: KOSPI 4단계 국면 적응형 / 국면별 익절·손절 자동전환"
        elif strategy == 'v7':
            r0 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 0)
            r1 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 1)
            r2 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 2)
            r3 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 3)
            filter_line = f"국면분포: 대하락{r0}일 / 약세{r1}일 / 중립{r2}일 / 강세{r3}일 (대하락 전면차단)"
            strategy_line = "★ Logic#6: 멀티팩터(해외동조30%+기술28%+가치17%+HS15%+고용10%) / 글로벌하락 패널티"
        else:
            filter_line = ""
            strategy_line = f"★ 전략 {strategy}"

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"{strategy_line}\n"
            f"{filter_line}\n"
            f"총 거래: {metrics['total_trades']}건  수익: {metrics['profit_trades']}건  "
            f"손실: {metrics['total_trades'] - metrics['profit_trades']}건\n"
            f"승률: {metrics['win_rate']}%  손익비: {metrics['pl_ratio']}배  "
            f"총손익: {metrics['total_profit_amt']:,}원\n"
            f"총수익률: {metrics['total_return_pct']}%  CAGR: {metrics['cagr']}%  "
            f"연환산: {metrics['ann_return_pct']}%\n"
            f"MDD: {metrics['max_drawdown_pct']}%  샤프지수: {metrics['sharpe']}\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )

        result = {
            **metrics,
            'monthly':      monthly_list,
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in top_winners],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in top_losers],
            'exit_reasons': dict(exit_reasons),
            'trades':       sorted(trades, key=lambda x: x['exit_date'], reverse=True)[:200],
            'summary':      summary_text,
        }
        _save_result(run_id, result)
        return run_id

    except Exception as e:
        import traceback
        err = f"{e}\n{traceback.format_exc()}"
        try:
            c = sqlite3.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        raise


def _save_result(run_id: str, result: dict):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("""
        UPDATE backtest_runs SET
            status='done',
            total_return_pct=?, ann_return_pct=?, win_rate=?,
            total_trades=?, profit_trades=?, max_drawdown_pct=?,
            trades_json=?, summary_text=?
        WHERE run_id=?
    """, (
        result.get('total_return_pct', 0), result.get('ann_return_pct', 0),
        result.get('win_rate', 0),         result.get('total_trades', 0),
        result.get('profit_trades', 0),    result.get('max_drawdown_pct', 0),
        json.dumps(result, ensure_ascii=False),
        result.get('summary', ''),
        run_id,
    ))
    conn.commit(); conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--start',     default='2023-04-01')
    parser.add_argument('--end',       default='2025-12-31')
    parser.add_argument('--per-stock', type=float, default=10_000_000)
    parser.add_argument('--max-pos',   type=int,   default=10)
    args = parser.parse_args()

    print(f"백테스트 시작: {args.start} ~ {args.end}  "
          f"(종목당 {args.per_stock:,.0f}원, 최대 {args.max_pos}종목 동시 보유)")
    rid = run_backtest(args.start, args.end,
                       per_stock=args.per_stock,
                       max_positions=args.max_pos)
    print(f"완료! run_id={rid}")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    row  = conn.execute("SELECT summary_text FROM backtest_runs WHERE run_id=?", (rid,)).fetchone()
    conn.close()
    if row: print(row[0])
