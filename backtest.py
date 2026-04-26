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
            cagr              REAL,
            win_rate          REAL,
            total_trades      INTEGER,
            profit_trades     INTEGER,
            max_drawdown_pct  REAL,
            sharpe            REAL,
            pl_ratio          REAL,
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
    i: int,
    sim_start_i: int,
    dates: list,
    prices: list,
    volumes: list,
    frn_net: list,
    inst_net: list,
    fin_rows: list,
    market: str = 'KOSPI',
    kospi_regime: int = 3,
    kosdaq_bullish: bool = True,
    adr_day: float = 50.0,
) -> bool:
    """
    AI 콤보 전략 v5 — 스나이퍼 로직 (V1가치 + V6수급 + V4눌림목 교집합).

    [업데이트] Minervini 돌파 → 눌림목+가치+절대수급 교집합
      ① 시장 필터: KOSPI → 지수 MA120 위(regime≥2) / KOSDAQ → 지수 MA60 위
                  ADR < 100 (시장 과열 방지)
      ② 가치 트랩 방지: 종목 현재가 > MA120
      ③ MA 정배열: MA120 > MA200
      ④ 눌림목 진입: MA20 × 0.98 ≤ 현재가 ≤ MA20 × 1.02
      ⑤ RSI < 50 (단기 과열 해소)
      ⑥ 절대 수급 강도: (inst+frn)5일합 > vol20 평균 × 5%
      ⑦ 가치 조건: Graham 할인 ≥ 25% OR (PBR<1.0 AND PER<15), 영업이익>0
    """
    if i < sim_start_i:
        return False

    # ① 시장 필터
    if market == 'KOSDAQ':
        if not kosdaq_bullish:
            return False
    else:
        if kospi_regime < 2:
            return False
    if adr_day >= 100:
        return False

    p_slice = prices[max(0, i - 249): i + 1]
    if len(p_slice) < 120:
        return False

    curr = prices[i]
    d    = dates[i]

    ma20  = _ma(p_slice[-20:],  20)
    ma60  = _ma(p_slice[-60:],  60)  if len(p_slice) >= 60  else None
    ma120 = _ma(p_slice[-120:], 120) if len(p_slice) >= 120 else None
    ma200 = _ma(p_slice[-200:], 200) if len(p_slice) >= 200 else None

    if ma20 is None or ma60 is None or ma120 is None:
        return False

    # ② 가치 트랩 방지: 현재가 > MA120
    if curr <= ma120:
        return False

    # ③ MA 정배열: MA120 > MA200
    if ma200 is not None and ma120 <= ma200:
        return False

    # ④ 눌림목 구간: MA20 ±2%
    if not (ma20 * 0.98 <= curr <= ma20 * 1.02):
        return False

    # ⑤ RSI < 50 (단기 과열 해소)
    rsi_val = _rsi(prices[max(0, i - 28): i + 1])
    if rsi_val is None or rsi_val >= 50:
        return False

    # ⑥ 절대 수급 강도: (inst+frn)5일합 > vol20 평균 × 5%
    if i < 20:
        return False
    supply_5  = sum(abs(frn_net[j]) + abs(inst_net[j]) for j in range(i - 4, i + 1))
    vol_slice = [v for v in volumes[max(0, i - 19): i + 1] if v > 0]
    vol20_avg = _get_avg(vol_slice)
    if vol20_avg <= 0 or supply_5 <= vol20_avg * 0.05:
        return False

    # ⑦ 가치 조건 + 영업이익 > 0
    fin = _get_financial_as_of(fin_rows, d)
    if fin is None:
        return False
    _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann = fin
    if not (op and op > 0):
        return False
    if not (bps and bps > 0 and eps and eps > 0):
        return False
    pbr  = curr / bps
    per  = curr / eps
    gp   = _graham_price(eps, bps)
    disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
    if not ((disc >= 25) or (pbr < 1.0 and 0 < per < 15)):
        return False

    return True


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
    regime: int = 3,              # 0=대하락, 1=약세, 2=중립, 3=강세
    market: str = 'KOSPI',
    kospi_regime_ext: int = 3,    # 시장 지수 국면 (market filter용)
    kosdaq_bullish: bool = True,
    adr_day: float = 50.0,
) -> bool:
    """
    Logic #5 매수 시그널 (시장 필터 + 절대 수급 강도 추가).
    """
    if i < sim_start_i:
        return False

    # 대하락 → 진입 금지
    if regime == 0:
        return False

    # 시장 필터: KOSPI MA120 위 / KOSDAQ MA60 위 / ADR < 100
    if market == 'KOSDAQ':
        if not kosdaq_bullish:
            return False
    else:
        if kospi_regime_ext < 2:
            return False
    if adr_day >= 100:
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

    # ── 공통: 절대 수급 강도 계산 ─────────────────────────────────
    supply_5  = sum(abs(frn_net[j]) + abs(inst_net[j]) for j in range(i - 4, i + 1)) if i >= 4 else 0
    vol_slice = [v for v in volumes[max(0, i - 19): i + 1] if v > 0]
    vol20_avg = _get_avg(vol_slice)
    supply_strong = (vol20_avg > 0 and supply_5 > vol20_avg * 0.05)

    # ── 국면 3 강세장: 눌림목 + 가치 OR 수급 ─────────────────────
    if regime == 3:
        if len(p_slice) < 120:
            return False
        ma120 = _ma(p_slice[-120:], 120)
        ma200 = _ma(p_slice[-200:], 200) if len(p_slice) >= 200 else None
        if ma120 is None:
            return False

        # 가치 트랩 방지: 현재가 > MA120
        if curr <= ma120:
            return False
        # MA 정배열: MA60 > MA120 (> MA200)
        if not (ma60 > ma120):
            return False
        if ma200 and not (ma120 > ma200):
            return False
        # 눌림목 구간: MA20 ±5%
        if not (ma20 * 0.95 <= curr <= ma20 * 1.05):
            return False
        # RSI < 55 (눌림목 구간에서 과열 해소 확인)
        rsi_val = _rsi(prices[max(0, i - 28): i + 1])
        if rsi_val is None or rsi_val >= 55:
            return False

        # 가치 기준 OR 절대 수급 강도
        value_ok = False
        if eps and bps and eps > 0 and bps > 0:
            gp   = _graham_price(eps, bps)
            disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
            pbr  = curr / bps
            per  = curr / eps
            value_ok = disc >= 15 or (pbr < 1.0 and 0 < per < 15)
        return value_ok or supply_strong

    # ── 국면 2 중립장: 눌림목 + 가치 (가치 트랩 방지 포함) ─────────
    elif regime == 2:
        if len(p_slice) < 120:
            return False
        ma120 = _ma(p_slice[-120:], 120)
        if ma120 is None:
            return False

        # 가치 트랩 방지: 현재가 > MA120
        if curr <= ma120:
            return False
        # 중기 정배열: curr > MA20
        if curr <= ma20:
            return False
        # RSI 35~60 (눌림목 + 반등 초기)
        rsi_val = _rsi(prices[max(0, i - 28): i + 1])
        if rsi_val is None or rsi_val < 35 or rsi_val > 60:
            return False
        # 52주 최저 대비 +20% (바닥권 회피)
        low52 = min(prices[max(0, i - 251): i + 1])
        if curr < low52 * 1.20:
            return False

        # 중립장: Graham 25%+ OR (PBR<0.7 AND PER<10)
        if not (eps and bps and eps > 0 and bps > 0):
            return False
        gp   = _graham_price(eps, bps)
        disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
        pbr  = curr / bps
        per  = curr / eps
        value_ok = disc >= 25 or (pbr < 0.7 and 0 < per < 10)
        return value_ok or supply_strong

    # ── 국면 1 약세장: 방어형 (깊은 가치 + 가치 트랩 방지) ──────
    elif regime == 1:
        if len(p_slice) < 120:
            return False
        ma120 = _ma(p_slice[-120:], 120)
        if ma120 is None:
            return False

        # 가치 트랩 방지: 현재가 > MA120
        if curr <= ma120:
            return False
        # 단기 정배열: curr > MA20
        if curr <= ma20:
            return False
        # RSI 42~65
        rsi_val = _rsi(prices[max(0, i - 28): i + 1])
        if rsi_val is None or rsi_val < 42 or rsi_val > 65:
            return False
        # 바닥 탈출: 52주 최저 대비 +25%
        low52 = min(prices[max(0, i - 251): i + 1])
        if curr < low52 * 1.25:
            return False

        # 약세장: Graham 30%+ OR (PBR<0.5)
        if not (eps and bps and eps > 0 and bps > 0):
            return False
        gp   = _graham_price(eps, bps)
        disc = (gp - curr) / gp * 100 if gp and gp > 0 else 0
        pbr  = curr / bps
        value_ok = disc >= 30 or (pbr < 0.5 and bps > 0)
        return value_ok

    return False


def _check_sell_v6(
    i: int,
    prices: list,
    pos: dict,
    regime: int = 3,
) -> Optional[str]:
    """
    Logic #5 공통 3단계 청산 (국면별 손절률·최대보유 유지).
    ① Time Stop: 5일 보유 + 수익 0% 이하
    ② Scale-out: +10% → 절반 익절(scale_out_partial), 잔여분 MA20 이탈 청산
    ③ 추적손절: 고점 +10% 이상 상승 후 고점 대비 -10%
    """
    curr = prices[i]
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr

    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    sl, max_hold = {3: (-0.06, 45), 2: (-0.05, 35), 1: (-0.04, 25)}.get(regime, (-0.05, 35))

    if hold_days >= 5 and pct <= 0:
        return f"time_stop({hold_days}일무수익)"

    if pct >= 0.10 and not pos.get('scaled_out', False):
        pos['scaled_out'] = True
        pos['scale_stop'] = pos['entry_price']
        return "scale_out_partial"

    if pos.get('scaled_out', False):
        ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
        if ma20 is not None and curr < ma20:
            return f"MA20붕괴(잔여분+{pct*100:.0f}%)"
        if curr <= pos.get('scale_stop', 0):
            return "손절(잔여분본전)"
        if hold_days >= max_hold:
            return f"최대보유({hold_days}일)"
        return None

    if pct <= sl:
        return f"손절({sl*100:.0f}%)"

    if hold_days >= max_hold:
        return f"최대보유({hold_days}일)"

    if hold_days >= 5 and peak >= pos['entry_price'] * 1.10:
        trail = (curr - peak) / peak
        if trail <= -0.10:
            return f"추적손절(고점-{abs(trail)*100:.0f}%)"

    return None



def _check_sell(
    i: int,
    prices: list,
    pos: dict,
    stop_loss: float = -0.06,
) -> Optional[str]:
    """
    공통 3단계 청산 로직 (v5).
    ① Time Stop: 5일 보유 + 수익 0% 이하 → 기회비용 절약
    ② Scale-out: +10% → 절반 익절(scale_out_partial), 잔여분은 MA20 이탈 청산
    ③ 추적손절: 고점 +10% 이상 상승 후 고점 대비 -10%
    ④ 하드 손절: -6% (즉시)
    """
    curr = prices[i]
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr

    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    if hold_days >= 5 and pct <= 0:
        return f"time_stop({hold_days}일무수익)"

    if pct >= 0.10 and not pos.get('scaled_out', False):
        pos['scaled_out'] = True
        pos['scale_stop'] = pos['entry_price']
        return "scale_out_partial"

    if pos.get('scaled_out', False):
        ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
        if ma20 is not None and curr < ma20:
            return f"MA20붕괴(잔여분+{pct*100:.0f}%)"
        if curr <= pos.get('scale_stop', 0):
            return "손절(잔여분본전)"
        return None

    if pct <= stop_loss:
        return f"손절({stop_loss*100:.0f}%)"

    if hold_days >= 5 and peak >= pos['entry_price'] * 1.10:
        trail = (curr - peak) / peak
        if trail <= -0.10:
            return f"추적손절(고점-{abs(trail)*100:.0f}%)"

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
    frn_net: list = None,
    inst_net: list = None,
    market: str = 'KOSPI',
    kospi_regime_ext: int = 3,
    kosdaq_bullish: bool = True,
    adr_day: float = 50.0,
) -> bool:
    """
    Logic #6 멀티팩터 매수 시그널 (시장 필터 + 절대 수급 강도 추가).
    """
    if i < sim_start_i or regime == 0:
        return False

    # 시장 필터: KOSPI MA120 위 / KOSDAQ MA60 위 / ADR < 100
    if market == 'KOSDAQ':
        if not kosdaq_bullish:
            return False
    else:
        if kospi_regime_ext < 2:
            return False
    if adr_day >= 100:
        return False

    # 절대 수급 강도 필수 (V6/V7 업데이트: 단순 양수 → 절대 강도)
    if frn_net is not None and inst_net is not None and i >= 20:
        supply_5  = sum(abs(frn_net[j]) + abs(inst_net[j]) for j in range(i - 4, i + 1))
        vol_sl    = [v for v in volumes[max(0, i - 19): i + 1] if v > 0]
        vol20_avg = _get_avg(vol_sl)
        if vol20_avg <= 0 or supply_5 <= vol20_avg * 0.05:
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
    Logic #6 공통 3단계 청산 (국면별 손절률·최대보유 유지).
    ① Time Stop: 5일 보유 + 수익 0% 이하
    ② Scale-out: +10% → 절반 익절(scale_out_partial), 잔여분 MA20 이탈 청산
    ③ 추적손절: 고점 +10% 이상 상승 후 고점 대비 -10%
    """
    curr = prices[i]
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr

    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    sl, max_d = {3: (-0.07, 45), 2: (-0.06, 35), 1: (-0.05, 25)}.get(regime, (-0.06, 35))

    if hold_days >= 5 and pct <= 0:
        return f"time_stop({hold_days}일무수익)"

    if pct >= 0.10 and not pos.get('scaled_out', False):
        pos['scaled_out'] = True
        pos['scale_stop'] = pos['entry_price']
        return "scale_out_partial"

    if pos.get('scaled_out', False):
        ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
        if ma20 is not None and curr < ma20:
            return f"MA20붕괴(잔여분+{pct*100:.0f}%)"
        if curr <= pos.get('scale_stop', 0):
            return "손절(잔여분본전)"
        if hold_days >= max_d:
            return f"보유기한({hold_days}일)"
        return None

    if pct <= sl:
        return f"손절({sl*100:.0f}%)"

    if hold_days >= max_d:
        return f"보유기한({hold_days}일)"

    if hold_days >= 5 and peak >= pos['entry_price'] * 1.10:
        trail = (curr - peak) / peak
        if trail <= -0.10:
            return f"추적손절(고점-{abs(trail)*100:.0f}%)"

    return None


# ══════════════════════════════════════════════════════════════
#  Logic #7 — 눌림목(Pullback) 전략 (v8)
# ══════════════════════════════════════════════════════════════

def _compute_kosdaq_regime(
    conn, warmup_start: str, end_date: str, start_date: str
) -> Dict[str, bool]:
    """KOSDAQ ^KQ11 MA60 기준. True = 상승장(MA60 위)."""
    bullish: Dict[str, bool] = {}
    try:
        rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KQ11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (warmup_start, end_date)).fetchall()
        kq_dates  = [r[0] for r in rows]
        kq_prices = [float(r[1]) for r in rows]
        for ki, kd in enumerate(kq_dates):
            if kd < start_date:
                continue
            kq_ma60 = _ma(kq_prices[max(0, ki - 59): ki + 1], 60)
            bullish[kd] = (kq_ma60 is None) or (kq_prices[ki] > kq_ma60)
    except Exception:
        pass
    return bullish


def _compute_adr_signals(sim_dates: list, stock_data: Dict) -> Dict[str, float]:
    """ADR (등락비율) = 상승종목수/하락종목수×100. 이미 로드된 price 데이터로 계산."""
    from collections import defaultdict
    advances: Dict[str, int] = defaultdict(int)
    declines: Dict[str, int] = defaultdict(int)

    for sd in stock_data.values():
        dates  = sd['dates']
        prices = sd['prices']
        for j in range(1, len(dates)):
            d    = dates[j]
            prev = prices[j - 1]
            curr = prices[j]
            if prev > 0:
                if curr > prev:
                    advances[d] += 1
                elif curr < prev:
                    declines[d] += 1

    adr: Dict[str, float] = {}
    for d in sim_dates:
        adv = advances.get(d, 0)
        dec = declines.get(d, 0)
        adr[d] = (adv / max(1, dec)) * 100
    return adr


def _is_buy_signal_v8(
    i: int,
    sim_start_i: int,
    dates: list,
    prices: list,
    volumes: list,
    frn: list,
    inst: list,
    fin_rows: list,
    market: str,
    kospi_regime: int,
    kosdaq_bullish: bool,
    adr_day: float,
) -> bool:
    """
    Logic #7 눌림목(Pullback) 매수 전략.
      1. 시장필터: KOSDAQ→MA60 위, KOSPI→MA200 위(regime≥1)
      2. ADR < 100 (당일 하락종목≥상승종목 → 눌림목 환경)
      3. MA 완전 정배열: MA60>MA120>MA200
      4. 눌림목 구간: MA20×0.98 ≤ curr ≤ MA20×1.05
      5. RSI < 50 (과열 해소)
      6. 수급 강도: (inst+frn)5일합 > vol20평균×5%
      7. 영업이익 > 0
    """
    if i < sim_start_i:
        return False

    # 시장 필터
    if market == 'KOSDAQ':
        if not kosdaq_bullish:
            return False
    else:
        if kospi_regime == 0:
            return False

    # ADR 필터 (눌림목 환경: 당일 하락종목 ≥ 상승종목)
    if adr_day >= 100:
        return False

    p_slice = prices[max(0, i - 249): i + 1]
    if len(p_slice) < 200:
        return False

    curr = prices[i]
    d    = dates[i]

    ma20  = _ma(p_slice[-20:],  20)
    ma60  = _ma(p_slice[-60:],  60)  if len(p_slice) >= 60  else None
    ma120 = _ma(p_slice[-120:], 120) if len(p_slice) >= 120 else None
    ma200 = _ma(p_slice[-200:], 200) if len(p_slice) >= 200 else None

    if ma20 is None or ma60 is None or ma120 is None or ma200 is None:
        return False

    # MA 완전 정배열
    if not (ma60 > ma120 > ma200):
        return False

    # 눌림목 구간 (MA20 -2% ~ +5%)
    if not (ma20 * 0.98 <= curr <= ma20 * 1.05):
        return False

    # RSI < 50 (과열 해소 확인)
    rsi = _rsi(prices[max(0, i - 28): i + 1])
    if rsi is None or rsi >= 50:
        return False

    # 수급 강도: (inst+frn) 5일 합 > vol20 평균 × 5%
    if i < 20:
        return False
    supply_5  = sum(abs(frn[j]) + abs(inst[j]) for j in range(i - 4, i + 1))
    vol_slice = [v for v in volumes[max(0, i - 19): i + 1] if v > 0]
    vol20_avg = _get_avg(vol_slice)
    if vol20_avg <= 0 or supply_5 <= vol20_avg * 0.05:
        return False

    # 재무: 영업이익 > 0
    fin = _get_financial_as_of(fin_rows, d)
    if fin is None:
        return False
    _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann = fin
    if not (op and op > 0):
        return False

    return True


def _check_sell_v8(
    i: int,
    prices: list,
    pos: dict,
) -> Optional[str]:
    """
    Logic #7 눌림목 매도 전략.
    - Time Stop: 5일 보유 + 수익률 ≤ 0
    - Scale-out: +10% 도달 시 절반 익절 → 잔여분은 MA20 이탈/본전 청산
    - 추적손절: 고점 +10% 발동 후 고점 대비 -10%
    - 하드 손절: -8%
    """
    curr = prices[i]
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr

    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    # Time Stop: 5일 보유 + 수익 없음
    if hold_days >= 5 and pct <= 0:
        return f"time_stop({hold_days}일무수익)"

    # Scale-out: 첫 +10% 도달 → 절반 청산 신호
    if pct >= 0.10 and not pos.get('scaled_out', False):
        pos['scaled_out'] = True
        pos['scale_stop'] = pos['entry_price']  # 잔여분 손절가 = 진입가(본전)
        return "scale_out_partial"

    # Scale-out 이후 잔여분 관리
    if pos.get('scaled_out', False):
        ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
        if ma20 is not None and curr < ma20:
            return f"MA20붕괴(잔여분+{pct*100:.0f}%)"
        if curr <= pos.get('scale_stop', 0):
            return "손절(잔여분본전)"
        return None

    # 하드 손절 -8%
    if pct <= -0.08:
        return "손절(-8%)"

    # 추적손절: 고점 +10% 이상 상승 후 고점 대비 -10%
    if hold_days >= 5 and peak >= pos['entry_price'] * 1.10:
        trail = (curr - peak) / peak
        if trail <= -0.10:
            return f"추적손절(고점-{abs(trail)*100:.0f}%)"

    return None


# ══════════════════════════════════════════════════════════════
#  Logic #5 — 텐배거 헌터 (V9)
# ══════════════════════════════════════════════════════════════
def _is_buy_signal_v9(
    i: int,
    sim_start_i: int,
    dates: list,
    prices: list,
    volumes: list,
    fin_rows: list,
    market: str,
    kospi_regime: int,
    kosdaq_bullish: bool,
    adr_day: float,
    mktcap: Optional[float],    # 원 단위 (stock_universe.market_cap)
) -> bool:
    """
    Logic #5 텐배거 헌터 매수 전략.
      1. 시장필터: KOSPI regime≥2(중립이상) / KOSDAQ MA60 위
      2. MA200 상방 (상승 추세 확립)
      3. 52주 고가 88% 이상 (신고가 돌파 직전 또는 직후)
      4. 거래량 폭발: 당일 거래량 > 60일 평균 × 3.0
      5. 실적: 영업이익>0 OR 매출 YoY +30%↑
      6. 시총 ≤ 1.5조 KRW (소형/중형주 한정)
    """
    if i < sim_start_i:
        return False

    # 시장 필터
    if market == 'KOSDAQ':
        if not kosdaq_bullish:
            return False
    else:
        # KOSPI: regime 0(대하락)/1(약세) 에서 매수 금지
        if kospi_regime < 2:
            return False

    p_slice = prices[max(0, i - 249): i + 1]
    if len(p_slice) < 200:
        return False

    curr    = prices[i]
    d       = dates[i]
    ma200   = _ma(p_slice[-200:], 200)

    if ma200 is None:
        return False

    # 1. MA200 상방
    if curr <= ma200:
        return False

    # 2. 52주 고가 88% 이상
    high_250 = max(p_slice)
    if curr < high_250 * 0.88:
        return False

    # 3. 거래량 폭발 × 3.0
    if i < 60:
        return False
    vol_slice = [v for v in volumes[max(0, i - 59): i + 1] if v > 0]
    vol60_avg = _get_avg(vol_slice)
    if vol60_avg <= 0 or volumes[i] < vol60_avg * 3.0:
        return False

    # 4. 재무 필터: 영업이익 흑자 OR 매출 YoY +30%
    fin_now  = _get_financial_as_of(fin_rows, d)
    if fin_now is None:
        return False
    _y, _q, rev_now, op, eps, bps, _eq, _ni, _roe, _ann = fin_now

    has_profit = op is not None and op > 0

    has_growth = False
    if not has_profit and rev_now:
        # 1년 전 데이터
        prev_d = f"{int(d[:4]) - 1}{d[4:]}"
        fin_prev = _get_financial_as_of(fin_rows, prev_d)
        if fin_prev:
            rev_prev = fin_prev[2]
            if rev_prev and rev_prev > 0:
                rev_yoy = (rev_now - rev_prev) / rev_prev
                has_growth = (rev_yoy >= 0.30)

    if not (has_profit or has_growth):
        return False

    # 5. 시총 ≤ 1.5조 (없으면 통과)
    if mktcap is not None and mktcap > 1_500_000_000_000:
        return False

    return True


def _check_sell_v9(
    i: int,
    prices: list,
    pos: dict,
) -> Optional[str]:
    """
    Logic #5 텐배거 헌터 매도 전략.
    "수익은 길게, 손실은 짧게" 원칙.
      - Time Stop: 5일 보유 + 수익률 ≤ 0
      - 하드 손절: -8%
      - Scale-out: +30% 도달 시 절반 익절 (다른 전략보다 늦게)
      - 고점 추적손절: 15% 이상 수익 후 고점 대비 -20%
      - MA20 추적: +15% 수익 후 MA20 이탈 시 청산
    """
    curr = prices[i]
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr

    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    # Time Stop: 5일 + 수익 없음
    if hold_days >= 5 and pct <= 0:
        return f"time_stop({hold_days}일무수익)"

    # 하드 손절 -8%
    if pct <= -0.08:
        return "손절(-8%)"

    # Scale-out: 첫 +30% 도달 시 절반 익절
    if pct >= 0.30 and not pos.get('scaled_out', False):
        pos['scaled_out'] = True
        pos['scale_stop'] = pos['entry_price'] * 1.10  # 잔여분 손절가 = +10% 본전
        return "scale_out_partial"

    # Scale-out 이후 잔여분 관리
    if pos.get('scaled_out', False):
        ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
        if ma20 is not None and curr < ma20 and pct >= 0.15:
            return f"MA20붕괴(잔여분+{pct*100:.0f}%)"
        if curr <= pos.get('scale_stop', 0):
            return "손절(잔여분+10%선)"
        return None

    # 고점 추적손절: +15% 이상 수익 후 고점 대비 -20%
    if pct >= 0.15 and peak >= pos['entry_price'] * 1.15:
        trail = (curr - peak) / peak
        if trail <= -0.20:
            return f"추적손절(고점-{abs(trail)*100:.0f}%)"

    # MA20 추적: +15% 수익 후 MA20 이탈
    if pct >= 0.15:
        ma20 = _ma(prices[max(0, i - 19): i + 1], 20)
        if ma20 is not None and curr < ma20:
            return f"MA20추적손절(+{pct*100:.0f}%)"

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
    kosdaq_regime: Optional[Dict[str, bool]] = None,                 # v8/v9: {date:bool}
    adr_signals: Optional[Dict[str, float]] = None,                  # v8/v9: {date:float}
    market_map: Optional[Dict[str, str]] = None,                     # v8/v9: {sc:'KOSPI'/'KOSDAQ'}
    mktcap_map: Optional[Dict[str, float]] = None,                   # v9: {sc: 원단위 시총}
) -> Tuple[list, list]:
    """
    매일(sim_dates 하루씩) 전 종목을 스캔:
      1) 기존 보유 종목 → 매도 조건 체크
      2) 빈 슬롯이 있으면 → 매수 조건 충족 종목 편입

    strategy='v5': 기존 AI 콤보 (KOSPI MA120 필터)
    strategy='v6': Logic #5 국면 적응형
    strategy='v7': Logic #6 멀티팩터 (동조+수출+고용)
    strategy='v8': Logic #7 눌림목(Pullback) 전략
    strategy='v9': Logic #5 텐배거 헌터

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
            if strategy == 'v9':
                reason = _check_sell_v9(i, sd['prices'], pos)
            elif strategy == 'v8':
                reason = _check_sell_v8(i, sd['prices'], pos)
            elif strategy == 'v7':
                reason = _check_sell_v7(i, sd['prices'], pos, regime)
            elif strategy == 'v6':
                reason = _check_sell_v6(i, sd['prices'], pos, regime)
            else:
                reason = _check_sell(i, sd['prices'], pos, stop_loss)
            if reason is None:
                continue
            curr = sd['prices'][i]
            pct  = (curr - pos['entry_price']) / pos['entry_price']
            # Scale-out: 절반만 청산하고 포지션 유지
            if reason == "scale_out_partial":
                half_qty      = max(1, pos['qty'] // 2)
                remaining_qty = pos['qty'] - half_qty
                if remaining_qty > 0:
                    trades.append({
                        'stock_code':  sc,
                        'entry_date':  pos['entry_date'],
                        'exit_date':   day,
                        'entry_price': pos['entry_price'],
                        'exit_price':  curr,
                        'qty':         half_qty,
                        'profit_pct':  round(pct * 100, 2),
                        'profit_amt':  round((curr - pos['entry_price']) * half_qty),
                        'exit_reason': "scale_out(절반+10%)",
                    })
                    pos['qty'] = remaining_qty
                    continue  # 잔여분 유지, sold_today에 추가 안 함
                reason = "scale_out(전량+10%)"
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
        if strategy in ('v6', 'v7', 'v9'):
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
                if strategy == 'v9':
                    sig = _is_buy_signal_v9(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['fins'],
                        (market_map or {}).get(sc, 'KOSPI'),
                        regime,
                        (kosdaq_regime or {}).get(day, True),
                        (adr_signals or {}).get(day, 50.0),
                        (mktcap_map or {}).get(sc),
                    )
                elif strategy == 'v8':
                    sig = _is_buy_signal_v8(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['frn'], sd['inst'], sd['fins'],
                        (market_map or {}).get(sc, 'KOSPI'),
                        regime,
                        (kosdaq_regime or {}).get(day, True),
                        (adr_signals or {}).get(day, 50.0),
                    )
                elif strategy == 'v7':
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
                        frn_net=sd['frn'],
                        inst_net=sd['inst'],
                        market=(market_map or {}).get(sc, 'KOSPI'),
                        kospi_regime_ext=regime,
                        kosdaq_bullish=(kosdaq_regime or {}).get(day, True),
                        adr_day=(adr_signals or {}).get(day, 50.0),
                    )
                elif strategy == 'v6':
                    sig = _is_buy_signal_v6(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['frn'],   sd['inst'],   sd['fins'],
                        regime,
                        market=(market_map or {}).get(sc, 'KOSPI'),
                        kospi_regime_ext=regime,
                        kosdaq_bullish=(kosdaq_regime or {}).get(day, True),
                        adr_day=(adr_signals or {}).get(day, 50.0),
                    )
                else:
                    sig = _is_buy_signal(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['frn'],   sd['inst'],   sd['fins'],
                        market=(market_map or {}).get(sc, 'KOSPI'),
                        kospi_regime=regime,
                        kosdaq_bullish=(kosdaq_regime or {}).get(day, True),
                        adr_day=(adr_signals or {}).get(day, 50.0),
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
#  데이터 사전 로드 (optimizer 캐시용)
# ══════════════════════════════════════════════════════════════
def prepare_backtest_data(start_date: str, end_date: str, strategy: str = 'v7') -> Dict:
    """
    주어진 기간의 모든 백테스트 데이터를 미리 로드한다.
    optimizer에서 동일 기간을 여러 파라미터로 반복 실행할 때
    이 결과를 캐시로 넘겨 DB 재조회를 생략한다.

    반환값을 run_backtest()의 _preloaded 파라미터에 전달하면 됨.
    """
    warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                    - timedelta(days=450)).strftime('%Y-%m-%d')

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")

    # ── 거래일 목록 ───────────────────────────────────────────
    sim_dates = [r[0] for r in conn.execute("""
        SELECT DISTINCT date FROM price_history
        WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
        ORDER BY date ASC
    """, (start_date, end_date)).fetchall()]
    if not sim_dates:
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

    # ── 재무 데이터 ───────────────────────────────────────────
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
        fin_all.setdefault(r[0], []).append(r[1:])

    # ── 종목 목록 (시총 1000억+, 충분한 데이터 보유) ─────────
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

    # ── 종목별 OHLCV 로드 ─────────────────────────────────────
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
            sim_start_i = next(
                (i for i, d in enumerate(dates) if d >= start_date),
                len(dates)
            )
            stock_data[sc] = {
                'dates': dates, 'prices': prices, 'volumes': volumes,
                'frn': frn, 'inst': inst,
                'fins': fin_all.get(sc, []),
                'sim_start_i': sim_start_i,
            }
        except Exception:
            continue

    # ── KOSPI 국면 계산 ───────────────────────────────────────
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
            market_bullish[kd] = (kma120 is None) or (curr > kma120)
            if kma60 and curr > kma60:        kospi_regime[kd] = 3
            elif kma120 and curr > kma120:    kospi_regime[kd] = 2
            elif kma200 and curr > kma200:    kospi_regime[kd] = 1
            else:                             kospi_regime[kd] = 0
    except Exception:
        pass

    conn.close()

    # ── v7 전용: 섹터맵 + 멀티팩터 사전계산 ─────────────────
    v7_sector_map:  Dict[str, str]              = {}
    v7_overseas:    Dict[str, Dict[str, float]] = {}
    v7_hs:          Dict[str, Dict[str, float]] = {}
    v7_emp:         Dict[str, Dict[str, float]] = {}

    if strategy == 'v7':
        sc_list = list(stock_data.keys())
        conn3 = sqlite3.connect(DB_PATH, timeout=30)
        try:
            for i in range(0, len(sc_list), 200):
                chunk = sc_list[i: i + 200]
                ph = ','.join('?' * len(chunk))
                for sc, sl in conn3.execute(
                    f"SELECT stock_code, sector_large FROM stock_universe "
                    f"WHERE stock_code IN ({ph})", chunk
                ).fetchall():
                    v7_sector_map[sc] = sl or "기타"
        except Exception:
            pass
        finally:
            conn3.close()

        conn4 = sqlite3.connect(DB_PATH, timeout=30)
        try:
            v7_overseas = _precompute_overseas_signals(
                conn4, sim_dates, warmup_start, v7_sector_map)
        except Exception:
            pass
        finally:
            conn4.close()

        v7_hs  = _precompute_hs_signals(sim_dates, sc_list)
        v7_emp = _precompute_emp_signals(sim_dates, sc_list)

    # ── 전략 공통: KOSDAQ 국면 + ADR + 시장구분 ─────────────────
    v8_kosdaq_bullish: Dict[str, bool]  = {}
    v8_adr_signals:    Dict[str, float] = {}
    v8_market_map:     Dict[str, str]   = {}

    conn_v8 = sqlite3.connect(DB_PATH, timeout=30)
    try:
        v8_kosdaq_bullish = _compute_kosdaq_regime(
            conn_v8, warmup_start, end_date, start_date)
    except Exception:
        pass
    finally:
        conn_v8.close()

    v8_adr_signals = _compute_adr_signals(sim_dates, stock_data)

    sc_list_v8 = list(stock_data.keys())
    conn_v8b = sqlite3.connect(DB_PATH, timeout=30)
    try:
        for i8 in range(0, len(sc_list_v8), 200):
            chunk8 = sc_list_v8[i8: i8 + 200]
            ph8    = ','.join('?' * len(chunk8))
            for sc, mk in conn_v8b.execute(
                f"SELECT stock_code, market FROM stock_universe "
                f"WHERE stock_code IN ({ph8})", chunk8
            ).fetchall():
                v8_market_map[sc] = mk or 'KOSPI'
    except Exception:
        pass
    finally:
        conn_v8b.close()

    # ── 시총 맵 (v9 텐배거 헌터용) ──────────────────────────────
    mktcap_map: Dict[str, float] = {}
    sc_list_mktcap = list(stock_data.keys())
    conn_mc = sqlite3.connect(DB_PATH, timeout=30)
    try:
        for i_mc in range(0, len(sc_list_mktcap), 200):
            chunk_mc = sc_list_mktcap[i_mc: i_mc + 200]
            ph_mc    = ','.join('?' * len(chunk_mc))
            for sc, mc in conn_mc.execute(
                f"SELECT stock_code, market_cap FROM stock_universe "
                f"WHERE stock_code IN ({ph_mc})", chunk_mc
            ).fetchall():
                if mc is not None:
                    mktcap_map[sc] = float(mc)
    except Exception:
        pass
    finally:
        conn_mc.close()

    return {
        'start_date':      start_date,
        'end_date':        end_date,
        'warmup_start':    warmup_start,
        'sim_dates':       sim_dates,
        'stock_data':      stock_data,
        'fin_all':         fin_all,
        'market_bullish':  market_bullish,
        'kospi_regime':    kospi_regime,
        'sector_map':      v7_sector_map,
        'overseas':        v7_overseas,
        'hs':              v7_hs,
        'emp':             v7_emp,
        'kosdaq_bullish':  v8_kosdaq_bullish,
        'adr_signals':     v8_adr_signals,
        'market_map':      v8_market_map,
        'mktcap_map':      mktcap_map,
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
                 _override_params: Optional[Dict] = None,
                 _preloaded: Optional[Dict] = None) -> str:
    """
    run_id가 주어지면 해당 레코드(이미 DB에 존재)를 직접 업데이트.
    없으면 새 run_id를 생성하고 INSERT.
    strategy: 'v5' = AI 콤보 v5 (KOSPI MA120 필터)
              'v6' = Logic #5 국면 적응형 (4단계 regime)
    """
    init_backtest_db()
    strategy_label = {
        'v5': 'AI 콤보 v5',
        'v6': 'Logic #2 국면적응형',
        'v7': 'Logic #3 멀티팩터(동조+수출+고용)',
        'v8': 'Logic #4 눌림목(Pullback) 전략',
        'v9': 'Logic #5 텐배거 헌터',
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
        # ── 데이터 로드: 캐시(_preloaded)가 있으면 재사용, 없으면 DB에서 직접 로드 ─
        if _preloaded is not None:
            # optimizer 캐시 경로 (DB 재조회 생략 → 수십 배 빠름)
            warmup_start      = _preloaded['warmup_start']
            sim_dates         = _preloaded['sim_dates']
            stock_data        = _preloaded['stock_data']
            market_bullish    = _preloaded['market_bullish']
            kospi_regime      = _preloaded['kospi_regime']
            v7_sector_map     = _preloaded.get('sector_map', {})
            v7_overseas       = _preloaded.get('overseas', {})
            v7_hs             = _preloaded.get('hs', {})
            v7_emp            = _preloaded.get('emp', {})
            v8_kosdaq_bullish = _preloaded.get('kosdaq_bullish', {})
            v8_adr_signals    = _preloaded.get('adr_signals', {})
            v8_market_map     = _preloaded.get('market_map', {})
            v9_mktcap_map     = _preloaded.get('mktcap_map', {})
            conn.close()
        else:
            # ── 워밍업 시작일 ─────────────────────────────────────
            warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                            - timedelta(days=450)).strftime('%Y-%m-%d')

            # ── 시뮬레이션 거래일 목록 (start_date ~ end_date) ─────
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]
            if not sim_dates:
                sim_dates = [r[0] for r in conn.execute("""
                    SELECT DISTINCT date FROM price_history
                    WHERE date>=? AND date<=? AND close>0
                    ORDER BY date ASC
                """, (start_date, end_date)).fetchall()]

            # ── 재무 데이터 로드 ──────────────────────────────────
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
                fin_all.setdefault(r[0], []).append(r[1:])

            # ── 종목 목록 ─────────────────────────────────────────
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

            # ── 종목별 OHLCV 로드 ─────────────────────────────────
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
                    sim_start_i = next(
                        (i for i, d in enumerate(dates) if d >= start_date),
                        len(dates)
                    )
                    stock_data[sc] = {
                        'dates': dates, 'prices': prices, 'volumes': volumes,
                        'frn': frn, 'inst': inst,
                        'fins': fin_all.get(sc, []),
                        'sim_start_i': sim_start_i,
                    }
                except Exception:
                    continue

            # ── KOSPI 국면 계산 ───────────────────────────────────
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
                    market_bullish[kd] = (kma120 is None) or (curr > kma120)
                    if kma60 and curr > kma60:        kospi_regime[kd] = 3
                    elif kma120 and curr > kma120:    kospi_regime[kd] = 2
                    elif kma200 and curr > kma200:    kospi_regime[kd] = 1
                    else:                             kospi_regime[kd] = 0
            except Exception:
                pass

            conn.close()

            # ── v7 전용: 섹터맵 + 멀티팩터 사전계산 ─────────────
            v7_sector_map:  Dict[str, str]              = {}
            v7_overseas:    Dict[str, Dict[str, float]] = {}
            v7_hs:          Dict[str, Dict[str, float]] = {}
            v7_emp:         Dict[str, Dict[str, float]] = {}

            if strategy == 'v7':
                sc_list = list(stock_data.keys())
                conn3 = sqlite3.connect(DB_PATH, timeout=30)
                try:
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

                conn4 = sqlite3.connect(DB_PATH, timeout=30)
                try:
                    v7_overseas = _precompute_overseas_signals(
                        conn4, sim_dates, warmup_start, v7_sector_map)
                except Exception:
                    pass
                finally:
                    conn4.close()

                v7_hs  = _precompute_hs_signals(sim_dates, sc_list)
                v7_emp = _precompute_emp_signals(sim_dates, sc_list)

            # ── 전략 공통: KOSDAQ 국면 + ADR + 시장구분 ─────────────
            v8_kosdaq_bullish: Dict[str, bool]  = {}
            v8_adr_signals:    Dict[str, float] = {}
            v8_market_map:     Dict[str, str]   = {}

            conn_kq = sqlite3.connect(DB_PATH, timeout=30)
            try:
                v8_kosdaq_bullish = _compute_kosdaq_regime(
                    conn_kq, warmup_start, end_date, start_date)
            except Exception:
                pass
            finally:
                conn_kq.close()

            v8_adr_signals = _compute_adr_signals(sim_dates, stock_data)

            sc_list_v8 = list(stock_data.keys())
            conn_mk = sqlite3.connect(DB_PATH, timeout=30)
            try:
                for i_mk in range(0, len(sc_list_v8), 200):
                    chunk_mk = sc_list_v8[i_mk: i_mk + 200]
                    ph_mk    = ','.join('?' * len(chunk_mk))
                    for sc, mk in conn_mk.execute(
                        f"SELECT stock_code, market FROM stock_universe "
                        f"WHERE stock_code IN ({ph_mk})", chunk_mk
                    ).fetchall():
                        v8_market_map[sc] = mk or 'KOSPI'
            except Exception:
                pass
            finally:
                conn_mk.close()

            # ── 시총 맵 (v9 텐배거 헌터용) ──────────────────────
            v9_mktcap_map: Dict[str, float] = {}
            conn_mc2 = sqlite3.connect(DB_PATH, timeout=30)
            try:
                for i_mc in range(0, len(sc_list_v8), 200):
                    chunk_mc = sc_list_v8[i_mc: i_mc + 200]
                    ph_mc    = ','.join('?' * len(chunk_mc))
                    for sc, mc in conn_mc2.execute(
                        f"SELECT stock_code, market_cap FROM stock_universe "
                        f"WHERE stock_code IN ({ph_mc})", chunk_mc
                    ).fetchall():
                        if mc is not None:
                            v9_mktcap_map[sc] = float(mc)
            except Exception:
                pass
            finally:
                conn_mc2.close()

        # ── 포트폴리오 시뮬레이션 ───────────────────────────────
        total_capital = per_stock * max_positions
        trades, equity_curve = _run_portfolio(
            sim_dates, stock_data,
            per_stock, max_positions,
            stop_loss=-0.08,
            market_bullish=market_bullish if (strategy == 'v5' and market_bullish) else None,
            strategy=strategy,
            kospi_regime=kospi_regime if strategy in ('v6', 'v7', 'v8', 'v9') else None,
            overseas_signals=v7_overseas if strategy == 'v7' else None,
            hs_signals=v7_hs         if strategy == 'v7' else None,
            emp_signals=v7_emp       if strategy == 'v7' else None,
            sector_map=v7_sector_map if strategy == 'v7' else None,
            v7_params=_override_params if strategy == 'v7' else None,
            kosdaq_regime=v8_kosdaq_bullish,
            adr_signals=v8_adr_signals,
            market_map=v8_market_map,
            mktcap_map=v9_mktcap_map if strategy == 'v9' else None,
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
        elif strategy == 'v8':
            r0 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 0)
            r3 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 3)
            kq_bull = sum(1 for d in sim_dates if v8_kosdaq_bullish.get(d, True))
            adr_lt100 = sum(1 for d in sim_dates if v8_adr_signals.get(d, 50) < 100)
            filter_line = (
                f"KOSPI 대하락 {r0}일 / KOSPI 강세 {r3}일 / "
                f"KOSDAQ MA60 위 {kq_bull}일 / ADR<100 {adr_lt100}일/{len(sim_dates)}일"
            )
            strategy_line = (
                "★ Logic#4: 눌림목(Pullback) / MA60>MA120>MA200 정배열 / "
                "MA20±2~5% / RSI<50 / 수급강도>vol20×5% / Scale-out+10% / TrailStop+10%발동"
            )
        elif strategy == 'v9':
            r0 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) == 0)
            r2 = sum(1 for d in sim_dates if (kospi_regime or {}).get(d, 3) >= 2)
            kq_bull = sum(1 for d in sim_dates if v8_kosdaq_bullish.get(d, True))
            filter_line = (
                f"KOSPI 대하락·약세 차단 {r0}일 / KOSPI 중립이상 {r2}일 / "
                f"KOSDAQ MA60 위 {kq_bull}일 / 시총≤1.5조 필터"
            )
            strategy_line = (
                "★ Logic#5 텐배거헌터: MA200 상방 / 52주고가88% / 거래량×3 / "
                "영업이익흑자 OR 매출YoY+30% / 시총1.5조이하 / Scale-out+30% / 추적손절-20%"
            )
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
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    # cagr/sharpe/pl_ratio 컬럼이 없는 구 DB에도 호환되도록 ALTER TABLE 시도
    for col_def in [("cagr", "REAL"), ("sharpe", "REAL"), ("pl_ratio", "REAL")]:
        try:
            conn.execute(f"ALTER TABLE backtest_runs ADD COLUMN {col_def[0]} {col_def[1]}")
        except Exception:
            pass
    conn.execute("""
        UPDATE backtest_runs SET
            status='done',
            total_return_pct=?, ann_return_pct=?, cagr=?,
            win_rate=?, total_trades=?, profit_trades=?,
            max_drawdown_pct=?, sharpe=?, pl_ratio=?,
            trades_json=?, summary_text=?
        WHERE run_id=?
    """, (
        result.get('total_return_pct', 0),  result.get('ann_return_pct', 0),
        result.get('cagr', 0),              result.get('win_rate', 0),
        result.get('total_trades', 0),      result.get('profit_trades', 0),
        result.get('max_drawdown_pct', 0),  result.get('sharpe', 0),
        result.get('pl_ratio', 0),
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
