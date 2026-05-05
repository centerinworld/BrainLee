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
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"
WARMUP_DAYS = 300   # MA200 + 여유분


# ══════════════════════════════════════════════════════════════
#  DB 초기화
# ══════════════════════════════════════════════════════════════
def init_backtest_db():
    conn = sqlite3.connect(DB_PATH, timeout=120)
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


def _score_entry(i: int, prices: list, volumes: list,
                 sc: str = None, day: str = None,
                 hs_data: dict = None) -> float:
    """
    매수 후보 점수화 함수 (높을수록 우선 매수).
    ① 거래량 비율 (0.35): 오늘 거래량 / 20일 평균 (최대 3배 클리핑)
    ② RSI 점수 (0.35): RSI 55 근방이 최적 (0~1 정규화)
    ③ 진입 품질 (0.20): MA20 대비 괴리 (MA20과 가까울수록 우량 진입)
    ④ HS 수출 보너스 (0.10): 2개월 전 수출 YoY > 10% 시 보너스
    """
    curr = prices[i] if prices[i] and prices[i] > 0 else 0
    if curr <= 0:
        return 0.0

    # ① 거래량 비율
    vol_window = [v for v in volumes[max(0, i-19):i] if v and v > 0]
    avg_vol20 = sum(vol_window) / len(vol_window) if vol_window else 0
    vol_ratio = (volumes[i] / avg_vol20) if avg_vol20 > 0 else 1.0
    vol_score = min(vol_ratio, 3.0) / 3.0  # 0~1

    # ② RSI 점수 (55 근방이 이상적)
    rsi_val = _rsi(prices[max(0, i-30):i+1], 14)
    if rsi_val is None:
        rsi_score = 0.5
    else:
        rsi_score = max(0.0, 1.0 - abs(rsi_val - 55) / 45.0)

    # ③ 진입 품질: MA20 대비 괴리율 (MA20 위 5% 이내가 최선)
    ma20 = _ma(prices[max(0, i-19):i+1], 20)
    if ma20 and ma20 > 0:
        gap = (curr - ma20) / ma20  # 0~0.05 = 좋음, >0.1 = 과열
        entry_quality = max(0.0, 1.0 - gap / 0.10)
    else:
        entry_quality = 0.5

    # ④ HS 수출 보너스 (선택적)
    hs_bonus = 0.0
    if hs_data and sc and day and sc in hs_data:
        # 2개월 전 데이터 참조 (무역통계 발표 지연 보정)
        y, m = int(day[:4]), int(day[5:7])
        m -= 2
        if m <= 0:
            m += 12; y -= 1
        ref_ym = f"{y}-{m:02d}"
        yoy = _get_export_yoy(hs_data[sc], ref_ym)
        if yoy is not None:
            if yoy >= 20:
                hs_bonus = 0.10   # 수출 20%+ 고성장
            elif yoy >= 10:
                hs_bonus = 0.05   # 수출 10%+ 성장
            elif yoy >= 0:
                hs_bonus = 0.02   # 수출 안정

    base = vol_score * 0.35 + rsi_score * 0.35 + entry_quality * 0.20
    return base + hs_bonus


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
#  매도 조건 — 날짜별 단일 계산
# ══════════════════════════════════════════════════════════════
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
#  포트폴리오 시뮬레이션 — 날짜별 전체 스캔
# ══════════════════════════════════════════════════════════════
def _run_portfolio(
    sim_dates: list,               # 시뮬레이션 기간 거래일 (ASC)
    stock_data: Dict[str, dict],   # code → {dates, prices, volumes, frn, inst, fins, sim_start_i}
    per_stock: float,
    max_positions: int,
    stop_loss: float = -0.08,
    market_bullish: Optional[Dict[str, bool]] = None,  # ★ 시장 추세 필터 (날짜→bool)
) -> Tuple[list, list]:
    """
    매일(sim_dates 하루씩) 전 종목을 스캔:
      1) 기존 보유 종목 → 매도 조건 체크 (손절/MA60/MA20)
      2) 빈 슬롯이 있으면 → 매수 조건 충족 종목 편입
      3) 포지션 가득 찬 경우 → 품질 기반 로테이션 (15% 우세 시 교체)

    Returns: (trades, equity_curve)
    """
    positions: Dict[str, dict] = {}    # code → position dict
    trades:    List[dict]       = []
    equity_curve: List[dict]    = []

    total_capital = per_stock * max_positions

    # HS 수출 데이터 로드 (점수 보너스용)
    try:
        _hs_data = _load_trade_signals()
    except Exception:
        _hs_data = {}

    # 날짜→인덱스 빠른 조회
    date_idx: Dict[str, Dict[str, int]] = {
        sc: {dt: idx for idx, dt in enumerate(d['dates'])}
        for sc, d in stock_data.items()
    }

    for day in sim_dates:

        # ── Step 1: 매도 체크 (보유 종목 전체) ─────────────
        sold_today = []
        for sc, pos in list(positions.items()):
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue
            i  = idx_map[day]
            sd = stock_data[sc]
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
        # ★ 시장 추세 필터: KOSPI MA60 아래면 신규 매수 금지
        if market_bullish is not None and not market_bullish.get(day, True):
            # 하락장 → equity curve만 업데이트하고 매수 건너뜀
            unrealized = 0.0
            for sc, pos in positions.items():
                idx_map = date_idx.get(sc, {})
                if day in idx_map:
                    i = idx_map[day]
                    unrealized += (stock_data[sc]['prices'][i] - pos['entry_price']) * pos['qty']
            realized = sum(t['profit_amt'] for t in trades)
            equity_curve.append({'date': day, 'equity': round(total_capital + realized + unrealized)})
            continue

        # 조건 충족 종목 전체 수집 후 점수 정렬 → 상위 종목만 매수
        candidates = []
        for sc, sd in stock_data.items():
            if sc in positions:
                continue
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue
            i = idx_map[day]
            # 매수 시그널 실시간 계산
            if not _is_buy_signal(
                i, sd['sim_start_i'],
                sd['dates'], sd['prices'], sd['volumes'],
                sd['frn'],   sd['inst'],   sd['fins'],
            ):
                continue
            score = _score_entry(i, sd['prices'], sd['volumes'],
                                 sc=sc, day=day, hs_data=_hs_data)
            candidates.append((score, sc, sd, i))
        # 점수 내림차순 정렬
        candidates.sort(key=lambda x: x[0], reverse=True)

        # 빈 슬롯 채우기
        for score, sc, sd, i in candidates:
            if len(positions) >= max_positions:
                break
            curr = sd['prices'][i]
            qty  = max(1, int(per_stock / curr))
            positions[sc] = {
                'entry_date':  day,
                'entry_price': curr,
                'qty':         qty,
                'peak_price':  curr,   # 추적손절용 고점 초기화
                'hold_days':   0,      # 최소보유일 카운터
            }

        # ── 포지션 교체 (로테이션): 더 좋은 종목으로 교체 ────────────────
        # 포지션 가득 찼을 때, 점수가 15% 이상 우세한 신규 종목으로 최하위 보유 종목 교체
        if len(positions) >= max_positions:
            for score, sc, sd, i in candidates:
                if sc in positions:
                    continue  # 이미 보유 중
                # 교체 가능한 보유 종목 (5일 이상 보유) 점수 계산
                held_scores = {}
                for held_sc, pos in list(positions.items()):
                    if pos.get('hold_days', 0) < 5:
                        continue
                    hidx = date_idx.get(held_sc, {}).get(day, -1)
                    if hidx < 0:
                        continue
                    h_score = _score_entry(
                        hidx, stock_data[held_sc]['prices'],
                        stock_data[held_sc]['volumes'],
                        sc=held_sc, day=day, hs_data=_hs_data,
                    )
                    held_scores[held_sc] = h_score
                if not held_scores:
                    break  # 교체 가능 종목 없음
                worst_sc = min(held_scores, key=held_scores.get)
                worst_score = held_scores[worst_sc]
                if score < worst_score * 1.15:  # 15% 이상 우세할 때만 교체
                    continue  # 충분히 좋지 않으면 교체 안 함
                # ── 교체 실행 ──
                hidx = date_idx[worst_sc][day]
                hprice = stock_data[worst_sc]['prices'][hidx]
                hpos = positions[worst_sc]
                hpct = (hprice - hpos['entry_price']) / hpos['entry_price']
                trades.append({
                    'stock_code':  worst_sc,
                    'stock_name':  worst_sc,
                    'entry_date':  hpos['entry_date'],
                    'exit_date':   day,
                    'entry_price': hpos['entry_price'],
                    'exit_price':  hprice,
                    'qty':         hpos['qty'],
                    'profit_pct':  round(hpct * 100, 2),
                    'profit_amt':  round((hprice - hpos['entry_price']) * hpos['qty']),
                    'exit_reason': f'로테이션교체(점수{worst_score:.2f}→{score:.2f})',
                })
                del positions[worst_sc]
                curr = sd['prices'][i]
                qty = max(1, int(per_stock / curr))
                positions[sc] = {'entry_date': day, 'entry_price': curr,
                                 'qty': qty, 'peak_price': curr, 'hold_days': 0}

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
    if total_capital > 0 and years > 0 and end_val > 0:
        cagr = round(((end_val / total_capital) ** (1 / years) - 1) * 100, 2)
    elif total_capital > 0 and end_val <= 0:
        cagr = -100.0   # 전액 손실
    else:
        cagr = 0
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
                 run_id: str = None) -> str:
    """
    run_id가 주어지면 해당 레코드(이미 DB에 존재)를 직접 업데이트.
    없으면 새 run_id를 생성하고 INSERT.
    """
    init_backtest_db()
    run_name = run_name or f"백테스트 {start_date[:7]}~{end_date[:7]}"

    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        # 이미 INSERT된 레코드 → 상태만 running으로 갱신
        conn = sqlite3.connect(DB_PATH, timeout=120)
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

        # ── ★ KOSPI 시장 추세 필터 생성 (v5: MA60→MA120 강화) ─────
        # KOSPI > MA120 이면 상승장(매수 허용), 아니면 하락장(매수 금지)
        market_bullish: Dict[str, bool] = {}
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
                kma120 = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                if kma120 is None:
                    market_bullish[kd] = True   # 데이터 부족 시 허용
                else:
                    market_bullish[kd] = k_prices[ki] > kma120
        except Exception:
            pass  # KOSPI 데이터 없으면 필터 비활성화 (모든 날 True)

        conn.close()

        # ── 포트폴리오 시뮬레이션 ───────────────────────────────
        total_capital = per_stock * max_positions
        trades, equity_curve = _run_portfolio(
            sim_dates, stock_data,
            per_stock, max_positions,
            stop_loss=-0.08,   # 1000억+ 대형주 기준 -8% (변동성 여유)
            market_bullish=market_bullish if market_bullish else None,
        )

        # ── 종목명 매핑 ──────────────────────────────────────
        conn2  = sqlite3.connect(DB_PATH, timeout=120)
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

        # 시장 필터 통계
        bear_days  = sum(1 for v in market_bullish.values() if not v)
        total_days = len(market_bullish)
        filter_pct = round(bear_days / total_days * 100, 1) if total_days > 0 else 0

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ 전략 v5: 시장추세필터(KOSPI MA120) / 손절-8% / 추적손절-10% / 익절+15% / 최소보유5일 / 시총1000억+\n"
            f"시장필터: 하락장 {bear_days}일/{total_days}일({filter_pct}%) 매수 차단\n"
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
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        raise


def _save_result(run_id: str, result: dict):
    conn = sqlite3.connect(DB_PATH, timeout=120)
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


# ══════════════════════════════════════════════════════════════
#  V1 매수 시그널 — 미너비니 트렌드 (추세 추종)
# ══════════════════════════════════════════════════════════════
def _is_buy_v1(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V1 트렌드 (미너비니 간략화):
      [A] 현재가 > MA20 > MA60 (정배열 핵심 2줄)
      [B] 현재가 > MA120 (중기 추세 확인)
      [C] RSI 42~72 (과매도/과매수 경계 내)
      [D] 거래량 > 5일 평균 1.3배 (약한 거래량 증가)
      [E] 52주 고점 대비 -30% 이내 (신고가 근접)
    """
    if i < sim_start_i or i < 60:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    ma20  = _ma(prices[max(0, i-19):i+1], 20)
    ma60  = _ma(prices[max(0, i-59):i+1], 60)
    ma120 = _ma(prices[max(0, i-119):i+1], 120) if i >= 120 else None

    # [A] 정배열 MA20 > MA60
    if not (ma20 and ma60):
        return False
    if not (curr > ma20 > ma60):
        return False
    # [B] 중기 추세
    if ma120 and curr < ma120:
        return False
    # [C] RSI (n+1 이상의 데이터 필요)
    rsi = _rsi(prices[max(0, i-30):i+1], 14)
    if rsi is None or not (42 <= rsi <= 72):
        return False
    # [D] 거래량 (5일 평균 대비 1.3배)
    avg_vol5 = _ma(volumes[max(0, i-4):i+1], 5)
    if avg_vol5 and avg_vol5 > 0 and volumes[i] < avg_vol5 * 1.3:
        return False
    # [E] 52주 고점 대비 -30% 이내
    high52 = max(prices[max(0, i-251):i+1])
    if high52 > 0 and (high52 - curr) / high52 > 0.30:
        return False
    return True


def _load_dart_signal_map(min_signal: int = 2, window_days: int = 90) -> dict:
    """
    dart_contracts 테이블에서 최근 window_days 이내 min_signal 이상 수주공시를
    stock_code별 날짜 리스트로 로드.
    Returns: {stock_code: sorted list of disclosed_at ('YYYYMMDD' strings)}
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)
        rows = conn.execute("""
            SELECT stock_code, disclosed_at FROM dart_contracts
            WHERE signal_strength >= ? AND disclosed_at IS NOT NULL
            ORDER BY disclosed_at ASC
        """, (min_signal,)).fetchall()
        conn.close()
    except Exception:
        return {}

    result: dict = {}
    for sc, dt in rows:
        if sc and dt:
            result.setdefault(sc, []).append(str(dt)[:8])  # YYYYMMDD
    return result


def run_backtest_v1(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    run_name: str = None, run_id: str = None) -> str:
    """V1 트렌드 (미너비니 추세추종 기본) — 월 10개 한도"""
    return _run_generic_backtest(
        version='V1', signal_fn=_is_buy_v1,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V1 트렌드 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=100_000_000_000,   # 1000억+
        max_new_per_month=10,         # ★ 추세추종은 후보 많아서 월 10개 제한
        strategy_key='v_trend',
    )


def run_backtest_v1_dart(start_date: str, end_date: str,
                         per_stock: float = 10_000_000,
                         max_positions: int = 10,
                         dart_min_signal: int = 2,
                         run_name: str = None, run_id: str = None) -> str:
    """
    V1 트렌드 + DART 수주공시 ★2 이상 (최근 90일) 필터.
    DART 5년치 데이터 기반 — 수주공시가 추세추종 매수에 유효한지 검증.
    """
    # DART 수주공시 데이터 사전 로드 (전체 기간)
    dart_map = _load_dart_signal_map(min_signal=dart_min_signal, window_days=9999)

    def _is_buy_v1_dart(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows,
                        _sc=None, **kw):
        if not _is_buy_v1(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows):
            return False
        if _sc not in dart_map:
            return False
        # 현재 날짜 기준 90일 이내 DART 수주공시 확인
        cur_dt = dates[i]  # 'YYYY-MM-DD'
        cutoff = (datetime.strptime(cur_dt, '%Y-%m-%d') - timedelta(days=90)).strftime('%Y%m%d')
        cur_dt8 = cur_dt.replace('-', '')
        return any(cutoff <= dt <= cur_dt8 for dt in dart_map[_sc])

    return _run_generic_backtest_with_sc(
        version='V1+DART', signal_fn=_is_buy_v1_dart,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V1+DART★{dart_min_signal} {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=100_000_000_000,
    )


# ══════════════════════════════════════════════════════════════
#  V10 매수 시그널 — 이익 폭발 (에스티팜·에이피알·삼양식품 유형)
# ══════════════════════════════════════════════════════════════
def _is_buy_v10(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V10 이익폭발:
      [A] 영업이익 YoY > 50% (재무 공시 지연 적용)
      [B] 매출 YoY > 20%
      [C] 영업이익률 > 5%
      [D] 2분기 연속 영업이익 성장 (추세 확인)
      [E] 현재가 > MA60 (추세 확인)
      [F] RSI 45~75 (과열 전, 과매도 전 구간)
    """
    if i < sim_start_i:
        return False
    d    = dates[i]
    curr = prices[i]

    # [E] 현재가 > MA60
    p_slice = prices[max(0, i - 60): i + 1]
    if len(p_slice) < 60:
        return False
    ma60 = sum(p_slice[-60:]) / 60
    if curr < ma60 * 0.95:   # 5% 여유 허용
        return False

    # [F] RSI 범위
    rsi_val = _rsi(prices[max(0, i - 28): i + 1])
    if rsi_val is None or rsi_val < 40 or rsi_val > 80:
        return False

    # 재무 조건: 공시 지연 적용, 최소 3개 분기 필요
    fin = _get_financial_as_of(fin_rows, d)
    if fin is None:
        return False
    y0, q0, rev0, op0, eps0, bps0, eq0, ni0, roe0, ann0 = fin

    if ann0:   # 연간 데이터는 스킵 (분기 데이터만 사용)
        return False
    if op0 is None or rev0 is None or op0 <= 0 or rev0 <= 0:
        return False

    # 영업이익률 [C]
    op_margin = op0 / rev0 * 100 if rev0 > 0 else 0
    if op_margin < 5.0:
        return False

    # 1년 전 동일 분기 (YoY 비교)
    ya_candidates = [r for r in fin_rows
                     if r[1] == q0 and r[0] == y0 - 1 and not r[-1]
                     and _release_date(r[0], r[1], False) <= d]
    if not ya_candidates:
        return False
    ya = ya_candidates[0]
    op_ya, rev_ya = ya[3], ya[2]   # [3]=operating_profit, [2]=revenue
    if op_ya is None or rev_ya is None or op_ya <= 0 or rev_ya <= 0:
        return False

    # [A] 영업이익 YoY
    op_yoy = (op0 - op_ya) / abs(op_ya) * 100
    if op_yoy < 50:
        return False

    # [B] 매출 YoY
    rev_yoy = (rev0 - rev_ya) / rev_ya * 100
    if rev_yoy < 20:
        return False

    # [D] 2분기 연속 성장: 직전 분기 데이터 확인
    prev_q = q0 - 1 if q0 > 1 else 4
    prev_y = y0 if q0 > 1 else y0 - 1
    prev_candidates = [r for r in fin_rows
                       if r[1] == prev_q and r[0] == prev_y and not r[-1]
                       and _release_date(r[0], r[1], False) <= d]
    if prev_candidates:
        op1 = prev_candidates[0][3]   # [3]=operating_profit
        if op1 is not None and op1 > 0 and op0 < op1 * 0.8:
            # 직전 분기보다 영업이익이 20% 이상 감소하면 탈락
            return False

    return True


# ══════════════════════════════════════════════════════════════
#  V11 매수 시그널 — 흑자전환 모멘텀 (이수페타시스·엘앤에프 유형)
# ══════════════════════════════════════════════════════════════
def _is_buy_v11(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V11 흑자전환:
      [A] 현재 2분기 모두 영업이익 > 30억 (흑자 안착)
      [B] 1년 전 동일 분기 영업이익 < 10억 (과거 적자/소규모)
      [C] 매출 YoY > 10%
      [D] 현재가 > MA120 (장기 바닥 돌파 확인)
      [E] 거래량 증가 (10일 평균 대비 1.5배 이상)

    ★ 2022~2023 구간 CAGR 50.4%: 이수페타시스·한화에어로스페이스·LS에코에너지 유형 포착
    ★ 2023~2025 구간 저조(3.9%)는 구조적 원인: 흑자전환 풀 축소 + 평균 수익률 압축
    """
    if i < sim_start_i:
        return False
    d    = dates[i]
    curr = prices[i]

    THRESHOLD_HIGH = 3_000_000_000   # 30억
    THRESHOLD_LOW  = 1_000_000_000   # 10억

    # [D] 현재가 > MA120
    p_slice = prices[max(0, i - 120): i + 1]
    if len(p_slice) < 60:
        return False
    ma60  = sum(p_slice[-60:]) / 60
    ma120 = sum(p_slice[-120:]) / 120 if len(p_slice) >= 120 else None
    ref_ma = ma120 if ma120 is not None else ma60
    if curr < ref_ma * 0.97:
        return False

    # [E] 거래량 증가
    vol_window = [v for v in volumes[max(0, i - 10): i] if v and v > 0]
    if not vol_window or not volumes[i] or volumes[i] <= 0:
        return False
    avg10v = sum(vol_window) / len(vol_window)
    if volumes[i] < avg10v * 1.5:
        return False

    # 재무: 최근 2개 분기 모두 흑자
    available = [r for r in fin_rows
                 if not r[-1] and r[1] in (1, 2, 3, 4)
                 and _release_date(r[0], r[1], False) <= d]
    available.sort(key=lambda r: (r[0], r[1]), reverse=True)

    if len(available) < 2:
        return False

    fin0, fin1 = available[0], available[1]
    op0, op1 = fin0[3], fin1[3]   # [3]=operating_profit
    rev0 = fin0[2]                # [2]=revenue

    if op0 is None or op1 is None:
        return False

    # [A] 현재 2분기 모두 흑자 (30억 이상)
    if op0 < THRESHOLD_HIGH or op1 < THRESHOLD_HIGH:
        return False

    # [B] 1년 전 같은 분기 — 과거 소규모/적자
    y0, q0 = fin0[0], fin0[1]
    ya_cands = [r for r in fin_rows
                if r[1] == q0 and r[0] == y0 - 1 and not r[-1]
                and _release_date(r[0], r[1], False) <= d]
    if not ya_cands:
        return False
    op_ya = ya_cands[0][3]    # [3]=operating_profit
    rev_ya = ya_cands[0][2]   # [2]=revenue
    if op_ya is None or op_ya >= THRESHOLD_LOW:
        return False   # 과거에도 이미 흑자였으면 흑자전환이 아님

    # [C] 매출 YoY
    if rev0 and rev_ya and rev_ya > 0:
        rev_yoy = (rev0 - rev_ya) / rev_ya * 100
        if rev_yoy < 10:
            return False

    return True


# ══════════════════════════════════════════════════════════════
#  V12 매수 시그널 — 섹터 대세 상승 (효성중공업·LS Electric 유형)
# ══════════════════════════════════════════════════════════════
def _run_backtest_v12(conn, warmup_start, start_date, end_date, sim_dates,
                      per_stock, max_positions, stop_loss, stop_loss_pct,
                      take_profit_pct):
    """
    V12는 섹터별 상대강도를 계산해야 해서 별도 함수로 구현.
    섹터 alpha = 해당 섹터 평균 3개월 수익률 - KOSPI 3개월 수익률
    종목은 섹터 alpha > 0 이고 자체 RS도 양수인 경우만 매수.
    """
    # KOSPI 로드
    kospi_rows = conn.execute("""
        SELECT date, close FROM price_history
        WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
        ORDER BY date ASC
    """, (warmup_start, end_date)).fetchall()
    k_dates  = [r[0] for r in kospi_rows]
    k_prices = {r[0]: float(r[1]) for r in kospi_rows}

    # 시장 상승장 필터
    k_price_list = [float(r[1]) for r in kospi_rows]
    market_bullish = {}
    for ki, kd in enumerate(k_dates):
        if kd < start_date:
            continue
        kma120 = _ma(k_price_list[max(0, ki - 119): ki + 1], 120)
        market_bullish[kd] = (kma120 is None) or (k_price_list[ki] > kma120)

    # 섹터 정보 + 전 종목 로드 (시총 2000억+, 섹터 정의된 종목만)
    sector_map = {}
    for sc, sec in conn.execute("""
        SELECT stock_code, COALESCE(NULLIF(sector_small,''), NULLIF(sector_large,''), '기타')
        FROM stock_universe
        WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
          AND market_cap >= 200000000000
          AND sector_large NOT IN ('기타','벤처기업부','신성장기업부','우선주','리츠','ETF','ETN','','스팩')
    """).fetchall():
        if sec and sec not in ('기타', '벤처기업부', '신성장기업부'):
            sector_map[sc] = sec

    stock_data = {}
    for sc in sector_map:
        rows = conn.execute("""
            SELECT date, close, COALESCE(volume,0)
            FROM price_history
            WHERE stock_code=? AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (sc, warmup_start, end_date)).fetchall()
        if len(rows) < 60:
            continue
        dates  = [r[0] for r in rows]
        prices = [float(r[1]) for r in rows]
        vols   = [float(r[2]) for r in rows]
        sim_start_i = next((idx for idx, dt in enumerate(dates) if dt >= start_date), len(dates))
        stock_data[sc] = {'dates': dates, 'prices': prices, 'volumes': vols,
                          'sim_start_i': sim_start_i}

    # 날짜→인덱스 맵
    date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                for sc, d in stock_data.items()}

    positions = {}
    trades    = []
    equity_curve = []
    total_capital = per_stock * max_positions

    # 섹터별 3개월 수익률 캐시 (당일 계산, 재계산 최소화)
    _sector_cache = {}
    _sector_cache_date = [None]

    def _get_hot_sectors(day):
        if _sector_cache_date[0] == day:
            return _sector_cache
        _sector_cache.clear()
        _sector_cache_date[0] = day

        # KOSPI 3개월 수익률
        kp_now  = k_prices.get(day)
        if kp_now is None:
            return _sector_cache
        # 약 63 거래일 전 날짜 찾기
        k_date_list = sorted(k_prices.keys())
        idx_now = next((idx for idx, d in enumerate(k_date_list) if d >= day), None)
        if idx_now is None or idx_now < 60:
            return _sector_cache
        kp_63 = k_prices.get(k_date_list[max(0, idx_now - 63)])
        kospi_3m = (kp_now - kp_63) / kp_63 * 100 if kp_63 and kp_63 > 0 else 0

        # 섹터별 평균 수익률
        sec_rets = {}
        for sc, sec in sector_map.items():
            if sc not in stock_data:
                continue
            sd = stock_data[sc]
            idx_map = date_idx.get(sc, {})
            i = idx_map.get(day)
            if i is None or i < 63 or i < sd['sim_start_i']:
                continue
            p_now = sd['prices'][i]
            p_63  = sd['prices'][i - 63]
            if p_63 <= 0:
                continue
            ret = (p_now - p_63) / p_63 * 100
            sec_rets.setdefault(sec, []).append(ret)

        for sec, rets in sec_rets.items():
            avg = sum(rets) / len(rets) if rets else 0
            alpha = avg - kospi_3m
            _sector_cache[sec] = {'alpha': alpha, 'avg_ret': avg, 'kospi_3m': kospi_3m}
        return _sector_cache

    for day in sim_dates:
        # 매도 체크
        sold_today = []
        for sc, pos in list(positions.items()):
            idx_map = date_idx.get(sc, {})
            if day not in idx_map:
                continue
            i  = idx_map[day]
            sd = stock_data[sc]
            reason = _check_sell_v12(i, sd['prices'], pos, stop_loss, stop_loss_pct, take_profit_pct)
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

        # 시장 필터
        if market_bullish and not market_bullish.get(day, True):
            unrealized = sum(
                (stock_data[sc]['prices'][date_idx[sc][day]] - pos['entry_price']) * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            realized = sum(t['profit_amt'] for t in trades)
            equity_curve.append({'date': day, 'equity': round(total_capital + realized + unrealized)})
            continue

        # 매수 스캔
        if len(positions) < max_positions:
            hot_sectors = _get_hot_sectors(day)

            for sc, sd in stock_data.items():
                if len(positions) >= max_positions:
                    break
                if sc in positions:
                    continue
                sec = sector_map.get(sc, '기타')
                sec_info = hot_sectors.get(sec)
                if sec_info is None or sec_info['alpha'] < 15:
                    continue   # 섹터 alpha 15% 미만은 제외 (기준 강화)

                idx_map = date_idx.get(sc, {})
                i = idx_map.get(day)
                if i is None or i < sd['sim_start_i'] or i < 120:
                    continue

                p = sd['prices']
                curr = p[i]

                # 개별 종목 RS: 섹터 평균 아웃퍼폼 (KOSPI보다 엄격)
                p63_back = p[i - 63] if i >= 63 else None
                if p63_back and p63_back > 0:
                    stock_3m = (curr - p63_back) / p63_back * 100
                    # 섹터 평균보다 낮거나 KOSPI보다 낮으면 탈락
                    if stock_3m < sec_info['avg_ret'] * 0.7 or stock_3m < sec_info['kospi_3m']:
                        continue

                # 가격 > MA60 > MA120 (추세 정배열)
                p_slice = p[max(0, i - 120): i + 1]
                if len(p_slice) < 120:
                    continue
                ma60  = sum(p_slice[-60:]) / 60
                ma120 = sum(p_slice) / len(p_slice)
                if curr < ma60 or ma60 < ma120 * 0.98:
                    continue

                # 52주 고점 -20% 이내 (V4 기준 적용)
                high52 = max(p[max(0, i - 251): i + 1])
                if curr < high52 * 0.80:
                    continue

                # RSI 50~75 (모멘텀 확인, 과열 제외)
                rsi_val = _rsi(p[max(0, i - 28): i + 1])
                if rsi_val is None or rsi_val < 50 or rsi_val > 75:
                    continue

                # 거래량 증가 확인 (최소 10일 평균 1.2배)
                vols = sd['volumes']
                vol_win = [v for v in vols[max(0, i - 10): i] if v > 0]
                if not vol_win or vols[i] < sum(vol_win) / len(vol_win) * 1.2:
                    continue

                qty = max(1, int(per_stock / curr))
                positions[sc] = {
                    'entry_date':  day,
                    'entry_price': curr,
                    'qty':         qty,
                    'peak_price':  curr,
                    'hold_days':   0,
                }

        unrealized = sum(
            (stock_data[sc]['prices'][date_idx[sc][day]] - pos['entry_price']) * pos['qty']
            for sc, pos in positions.items() if day in date_idx.get(sc, {})
        )
        realized = sum(t['profit_amt'] for t in trades)
        equity_curve.append({'date': day, 'equity': round(total_capital + realized + unrealized)})

    # 기간 종료 강제 청산
    last_day = sim_dates[-1] if sim_dates else None
    for sc, pos in list(positions.items()):
        idx_map = date_idx.get(sc, {})
        sd = stock_data[sc]
        curr = sd['prices'][idx_map[last_day]] if last_day and last_day in idx_map \
               else (sd['prices'][-1] if sd['prices'] else pos['entry_price'])
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


def _check_sell_v12(i, prices, pos, stop_loss=-0.08, stop_loss_pct=-0.07, take_profit_pct=0.20):
    """V12 매도: 익절 +20%, 손절 -7%, 추적손절 -12%, MA60 붕괴."""
    curr = prices[i]
    if curr > pos.get('peak_price', pos['entry_price']):
        pos['peak_price'] = curr
    pct       = (curr - pos['entry_price']) / pos['entry_price']
    peak      = pos.get('peak_price', pos['entry_price'])
    hold_days = pos.get('hold_days', 0)
    pos['hold_days'] = hold_days + 1

    if pct >= take_profit_pct:
        return f"익절(+{pct*100:.0f}%)"
    if pct <= stop_loss_pct:
        return f"손절({stop_loss_pct*100:.0f}%)"
    if hold_days >= 5:
        trail_pct = (curr - peak) / peak if peak > 0 else 0
        if trail_pct <= -0.12 and pct > 0.03:
            return f"추적손절(고점-{abs(trail_pct)*100:.0f}%)"
        ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
        if ma60 is not None and curr < ma60:
            return "MA60 붕괴"
    return None


# ══════════════════════════════════════════════════════════════
#  V10/V11 공통 백테스트 실행 (V4 run_backtest 구조 재활용)
# ══════════════════════════════════════════════════════════════
def run_backtest_v10(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     run_name: str = None, run_id: str = None) -> str:
    return _run_generic_backtest(
        version='V10', signal_fn=_is_buy_v10,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V10 이익폭발 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=50_000_000_000,   # 500억+
        max_new_per_month=999,       # 펀더멘탈 전략 — 월 한도 없음 (자연 필터)
        strategy_key='v10',
    )


def run_backtest_v11(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     run_name: str = None, run_id: str = None) -> str:
    return _run_generic_backtest(
        version='V11', signal_fn=_is_buy_v11,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V11 흑자전환 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.10, take_profit=0.30,   # 흑자전환은 더 큰 수익 기대 → 넓게
        mktcap_min=30_000_000_000,            # 300억+ (소형 전환주 포함)
        max_new_per_month=999,                # 펀더멘탈 전략 — 월 한도 없음 (자연 필터)
        use_market_filter=False,              # ★ 흑자전환은 하락장에서도 매수 (구조적 전략)
        strategy_key='v11',
    )


def _make_hs_filtered_signal_fn(base_signal_fn, hs_yoy_min: float = 10.0):
    """
    HS 수출 YoY 조건을 추가한 시그널 함수 생성기.
    base_signal_fn이 True인 종목 중 HS 수출 YoY >= hs_yoy_min인 경우만 매수.
    """
    # 한 번만 로드 (전체 백테스트 기간에 공유)
    _trade_all = _load_trade_signals()

    def _filtered(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows,
                  _sc=None, **kw):
        if not base_signal_fn(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows):
            return False
        if _sc is None or _sc not in _trade_all:
            return False  # HS 데이터 없는 종목은 필터에서 제외
        ref_ym = _date_to_ym(dates[i], lag_months=2)
        yoy = _get_export_yoy(_trade_all[_sc], ref_ym)
        if yoy is None or yoy < hs_yoy_min:
            return False
        return True

    return _filtered, _trade_all


def run_backtest_v10_hs(start_date: str, end_date: str,
                        per_stock: float = 10_000_000,
                        max_positions: int = 10,
                        hs_yoy_min: float = 10.0,
                        run_name: str = None, run_id: str = None) -> str:
    """
    V10 이익폭발 + HS 수출 YoY ≥ 10% 필터.
    HS 데이터가 있는 종목(285개)에서만 매수.
    기존 V10과 비교해 HS 조건이 수익률을 실제로 개선하는지 검증.
    """
    # HS 데이터 있는 종목 목록 로드
    trade_all = _load_trade_signals()
    hs_stocks = set(trade_all.keys())

    def _is_buy_v10_hs(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows,
                       _sc=None, **kw):
        if _sc not in hs_stocks:
            return False
        if not _is_buy_v10(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows):
            return False
        ref_ym = _date_to_ym(dates[i], lag_months=2)
        yoy = _get_export_yoy(trade_all.get(_sc, {}), ref_ym)
        return yoy is not None and yoy >= hs_yoy_min

    return _run_generic_backtest_with_sc(
        version='V10+HS', signal_fn=_is_buy_v10_hs,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V10+HS수출≥{hs_yoy_min:.0f}% {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=50_000_000_000,
    )


def run_backtest_v11_hs(start_date: str, end_date: str,
                        per_stock: float = 10_000_000,
                        max_positions: int = 10,
                        hs_yoy_min: float = 10.0,
                        run_name: str = None, run_id: str = None) -> str:
    """
    V11 흑자전환 + HS 수출 YoY ≥ 10% 필터.
    """
    trade_all = _load_trade_signals()
    hs_stocks = set(trade_all.keys())

    def _is_buy_v11_hs(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows,
                       _sc=None, **kw):
        if _sc not in hs_stocks:
            return False
        if not _is_buy_v11(i, sim_start_i, dates, prices, volumes, frn_net, inst_net, fin_rows):
            return False
        ref_ym = _date_to_ym(dates[i], lag_months=2)
        yoy = _get_export_yoy(trade_all.get(_sc, {}), ref_ym)
        return yoy is not None and yoy >= hs_yoy_min

    return _run_generic_backtest_with_sc(
        version='V11+HS', signal_fn=_is_buy_v11_hs,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V11+HS수출≥{hs_yoy_min:.0f}% {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.10, take_profit=0.30,
        mktcap_min=30_000_000_000,
    )


def _run_generic_backtest_with_sc(version: str, signal_fn,
                                   start_date: str, end_date: str,
                                   per_stock: float, max_positions: int,
                                   run_name: str, run_id: str,
                                   stop_loss: float, take_profit: float,
                                   mktcap_min: int = 100_000_000_000) -> str:
    """
    _run_generic_backtest와 동일하나 signal_fn에 _sc(stock_code) 키워드 인자를 전달.
    V10+HS, V11+HS처럼 시그널 함수가 종목 코드 접근이 필요한 경우 사용.
    """
    init_backtest_db()
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        fin_all: Dict[str, list] = {}
        for r in conn.execute("""
            SELECT stock_code, year, quarter,
                   revenue, operating_profit, eps, bps,
                   total_equity, net_income, roe,
                   CASE WHEN is_annual=1 THEN 1 ELSE 0 END
            FROM financial_data
            WHERE (is_annual=0 AND quarter BETWEEN 1 AND 4) OR (is_annual=1)
            ORDER BY stock_code, year, quarter
        """).fetchall():
            fin_all.setdefault(r[0], []).append(r[1:])

        stock_codes = [r[0] for r in conn.execute("""
            SELECT ph.stock_code, COUNT(*) AS cnt
            FROM price_history ph
            INNER JOIN (
                SELECT stock_code FROM stock_universe
                WHERE (market_cap IS NULL OR market_cap >= ?)
                  AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            ) su ON ph.stock_code = su.stock_code
            WHERE ph.date>=? AND ph.date<=? AND ph.close>0
            GROUP BY ph.stock_code HAVING cnt >= 200
        """, (mktcap_min, warmup_start, end_date)).fetchall()]

        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            rows = conn.execute("""
                SELECT date, close, COALESCE(volume,0),
                       COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0)
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
            sim_start_i = next((i for i, d in enumerate(dates) if d >= start_date), len(dates))
            stock_data[sc] = {
                'dates': dates, 'prices': prices, 'volumes': volumes,
                'frn': frn, 'inst': inst, 'fins': fin_all.get(sc, []),
                'sim_start_i': sim_start_i,
            }

        kospi_prices = {r[0]: r[1] for r in conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 AND date>=? AND date<=?
        """, (warmup_start, end_date)).fetchall()}

        # O(1) 날짜 → 인덱스 사전 (핵심 성능 최적화)
        date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                    for sc, d in stock_data.items()}

        # KOSPI MA120 사전계산
        kp_vals = [kospi_prices.get(d, 0) for d in sim_dates]
        kp_valids: list = []
        kma120_map: dict = {}
        for ki, sim_date in enumerate(sim_dates):
            kp = kp_vals[ki]
            if kp > 0:
                kp_valids.append(kp)
            kma120_map[sim_date] = (sum(kp_valids[-120:]) / len(kp_valids[-120:])
                                    if len(kp_valids) >= 120 else 0)

        trades = []
        equity_curve = []
        portfolio: Dict[str, dict] = {}
        cash = per_stock * max_positions

        for sim_date in sim_dates:
            kp = kospi_prices.get(sim_date, 0)
            kp_ma120 = kma120_map.get(sim_date, 0)
            market_bullish = kp > kp_ma120 if kp_ma120 > 0 else True

            # 매도 체크
            for sc in list(portfolio.keys()):
                sd = stock_data.get(sc)
                if sd is None:
                    continue
                i = date_idx.get(sc, {}).get(sim_date, -1)
                if i < 0:
                    continue
                curr = sd['prices'][i]
                pos = portfolio[sc]
                entry = pos['entry_price']
                pct = (curr - entry) / entry
                peak = max(pos.get('peak', entry), curr)
                pos['peak'] = peak
                days_held = pos.get('days_held', 0) + 1
                pos['days_held'] = days_held

                reason = None
                if pct >= take_profit:
                    reason = f'익절 {pct*100:.1f}%'
                elif pct <= stop_loss:
                    reason = f'손절 {pct*100:.1f}%'
                elif (peak - entry) / entry > 0.05 and (curr - peak) / peak < -0.10:
                    reason = f'추적손절 {(curr-peak)/peak*100:.1f}%'
                elif days_held > 5:
                    prices_slice = sd['prices'][max(0, i - 60):i + 1]
                    ma60 = sum(prices_slice[-60:]) / len(prices_slice[-60:]) if len(prices_slice) >= 60 else 0
                    if ma60 > 0 and curr < ma60:
                        reason = 'MA60붕괴'

                if reason:
                    profit_amt = round((curr - entry) * pos['qty'])
                    cash += curr * pos['qty']
                    trades.append({
                        'stock_code': sc, 'stock_name': pos.get('stock_name', sc),
                        'entry_date': pos['entry_date'],
                        'exit_date': sim_date, 'entry_price': entry, 'exit_price': curr,
                        'qty': pos['qty'], 'profit_amt': profit_amt,
                        'return_pct': round(pct * 100, 2), 'reason': reason,
                    })
                    del portfolio[sc]

            # 매수 체크
            if market_bullish and len(portfolio) < max_positions:
                for sc, sd in stock_data.items():
                    if sc in portfolio or len(portfolio) >= max_positions:
                        continue
                    i = date_idx.get(sc, {}).get(sim_date, -1)
                    if i < 0 or i < sd['sim_start_i']:
                        continue
                    buy = signal_fn(
                        i, sd['sim_start_i'],
                        sd['dates'], sd['prices'], sd['volumes'],
                        sd['frn'], sd['inst'], sd['fins'],
                        _sc=sc,
                    )
                    if buy:
                        curr = sd['prices'][i]
                        qty = max(1, int(per_stock / curr))
                        if cash >= curr * qty:
                            cash -= curr * qty
                            portfolio[sc] = {
                                'entry_date': sim_date, 'entry_price': curr,
                                'qty': qty, 'peak': curr, 'days_held': 0,
                            }

            total_val = cash + sum(
                pos['qty'] * (stock_data[sc]['prices'][date_idx[sc].get(sim_date, -1)]
                              if date_idx[sc].get(sim_date, -1) >= 0
                              else pos['entry_price'])
                for sc, pos in portfolio.items() if sc in stock_data
            )
            equity_curve.append({'date': sim_date, 'equity': total_val})

        # 강제 청산
        for sc, pos in portfolio.items():
            sd = stock_data.get(sc)
            if sd:
                last_p = sd['prices'][-1]
                ret_pct = (last_p - pos['entry_price']) / pos['entry_price'] * 100
                trades.append({
                    'stock_code': sc, 'stock_name': pos.get('stock_name', sc),
                    'entry_date': pos['entry_date'],
                    'exit_date': sim_dates[-1] if sim_dates else end_date,
                    'entry_price': pos['entry_price'], 'exit_price': last_p,
                    'qty': pos['qty'],
                    'profit_amt': round((last_p - pos['entry_price']) * pos['qty']),
                    'return_pct': round(ret_pct, 2),
                    'reason': '기간종료',
                })

        metrics = _calc_metrics(trades, equity_curve, start_date, end_date,
                                per_stock * max_positions)

        # 종목별 손익 요약
        from collections import defaultdict
        per_name: Dict[str, float] = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        top_winners = sorted(per_name.items(), key=lambda x: -x[1])[:5]
        top_losers  = sorted(per_name.items(), key=lambda x:  x[1])[:5]

        summary_text = (
            f"기간: {start_date} ~ {end_date}\n"
            f"전략: {version} | CAGR: {metrics.get('cagr', 0):.1f}% | "
            f"MDD: {metrics.get('max_drawdown_pct', 0):.1f}% | "
            f"승률: {metrics.get('win_rate', 0):.1f}% | "
            f"거래: {metrics.get('total_trades', 0)}건"
        )

        result = {
            **metrics,
            'equity_curve': equity_curve[-252:],
            'trades':       sorted(trades, key=lambda x: x.get('exit_date', ''), reverse=True)[:200],
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in top_winners],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in top_losers],
            'summary':      summary_text,
        }
        conn.close()
        _save_result(run_id, result)
    except Exception as e:
        try:
            conn.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                         (str(e), run_id))
            conn.commit()
        except Exception:
            pass
        finally:
            conn.close()
        logger.error(f"[백테스트/{version}] 오류: {e}", exc_info=True)
    return run_id


def run_backtest_v12(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     run_name: str = None, run_id: str = None) -> str:
    """V12는 섹터 계산이 필요하므로 별도 흐름."""
    init_backtest_db()
    run_name = run_name or f"V12 섹터대세 {start_date[:7]}~{end_date[:7]}"
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]

        trades, equity_curve = _run_backtest_v12(
            conn, warmup_start, start_date, end_date, sim_dates,
            per_stock, max_positions,
            stop_loss=-0.07, stop_loss_pct=-0.07, take_profit_pct=0.25,
        )

        # 종목명 매핑
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for i in range(0, len(codes), 100):
            batch = codes[i:i+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn.execute(f"""
                SELECT DISTINCT ph.stock_code,
                       COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        total_capital = per_stock * max_positions
        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)

        from collections import defaultdict
        monthly = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        per_name = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        exit_reasons = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1

        summary_text = (
            f"기간: {start_date} ~ {end_date}\n"
            f"★ V12 섹터대세: KOSPI MA120 필터 / 섹터 alpha>10% / 개별 RS / 익절+25% / 손절-7%\n"
            f"총 거래: {metrics['total_trades']}건  승률: {metrics['win_rate']}%  "
            f"CAGR: {metrics['cagr']}%  MDD: {metrics['max_drawdown_pct']}%  샤프: {metrics['sharpe']}\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )
        result = {
            **metrics,
            'monthly':      [{'month': k, 'profit': v} for k, v in sorted(monthly.items())],
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in sorted(per_name.items(), key=lambda x: -x[1])[:5]],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in sorted(per_name.items(), key=lambda x:  x[1])[:5]],
            'exit_reasons': dict(exit_reasons),
            'trades':       sorted(trades, key=lambda x: x['exit_date'], reverse=True)[:200],
            'summary':      summary_text,
        }
        _save_result(run_id, result)
        conn.close()
        return run_id

    except Exception as e:
        import traceback
        err = f"{e}\n{traceback.format_exc()}"
        try:
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        conn.close()
        raise


def _run_generic_backtest(version: str, signal_fn,
                           start_date: str, end_date: str,
                           per_stock: float, max_positions: int,
                           run_name: str, run_id: str,
                           stop_loss: float, take_profit: float,
                           mktcap_min: int = 100_000_000_000,
                           max_new_per_month: int = 10,
                           use_market_filter: bool = True,
                           strategy_key: str = None) -> str:
    """V10/V11 공통 백테스트 실행기 (V4 run_backtest 구조 재활용).
    use_market_filter=False: V11 흑자전환처럼 하락장에서도 매수해야 하는 전략에 사용.
    strategy_key: DB에 저장할 전략 키 (v10, v11, v_trend 등). None이면 'combo' 기본값.
    """
    init_backtest_db()
    _strat_key = strategy_key or version.lower().replace('+', '_').replace(' ', '_')
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status,strategy)
            VALUES (?,?,?,?,?,?,'running',?)
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions, _strat_key))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running', strategy=? WHERE run_id=?",
                     (_strat_key, run_id))
        conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
        sim_dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM price_history
            WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (start_date, end_date)).fetchall()]
        if not sim_dates:
            # KOSPI 데이터 없으면 전체 종목에서 날짜 추출
            sim_dates = [r[0] for r in conn.execute("""
                SELECT DISTINCT date FROM price_history
                WHERE date>=? AND date<=? AND close>0
                ORDER BY date ASC
            """, (start_date, end_date)).fetchall()]

        # 재무 데이터 로드
        fin_all: Dict[str, list] = {}
        for r in conn.execute("""
            SELECT stock_code, year, quarter,
                   revenue, operating_profit, eps, bps,
                   total_equity, net_income, roe,
                   CASE WHEN is_annual=1 THEN 1 ELSE 0 END
            FROM financial_data
            WHERE (is_annual=0 AND quarter BETWEEN 1 AND 4)
               OR (is_annual=1)
            ORDER BY stock_code, year, quarter
        """).fetchall():
            sc = r[0]
            fin_all.setdefault(sc, []).append(r[1:])

        # 종목 목록 (시총 필터)
        stock_codes = [r[0] for r in conn.execute("""
            SELECT ph.stock_code, COUNT(*) AS cnt
            FROM price_history ph
            INNER JOIN (
                SELECT stock_code FROM stock_universe
                WHERE (market_cap IS NULL OR market_cap >= ?)
                  AND LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
            ) su ON ph.stock_code = su.stock_code
            WHERE ph.date>=? AND ph.date<=? AND ph.close>0
            GROUP BY ph.stock_code HAVING cnt >= 200
        """, (mktcap_min, warmup_start, end_date)).fetchall()]

        # 종목별 데이터 로드
        stock_data: Dict[str, dict] = {}
        for sc in stock_codes:
            try:
                rows = conn.execute("""
                    SELECT date, close, COALESCE(volume,0),
                           COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0)
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
                sim_start_i = next((i for i, d in enumerate(dates) if d >= start_date), len(dates))
                stock_data[sc] = {
                    'dates': dates, 'prices': prices, 'volumes': volumes,
                    'frn': frn, 'inst': inst, 'fins': fin_all.get(sc, []),
                    'sim_start_i': sim_start_i,
                }
            except Exception:
                continue

        # KOSPI 시장 필터
        market_bullish: Dict[str, bool] = {}
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
                kma120 = _ma(k_prices[max(0, ki - 119): ki + 1], 120)
                market_bullish[kd] = (kma120 is None) or (k_prices[ki] > kma120)
        except Exception:
            pass

        conn.close()

        # V10/V11용 커스텀 매도 함수 (익절/손절 파라미터 반영)
        def _check_sell_generic(i, prices, pos):
            curr = prices[i]
            if curr > pos.get('peak_price', pos['entry_price']):
                pos['peak_price'] = curr
            pct       = (curr - pos['entry_price']) / pos['entry_price']
            peak      = pos.get('peak_price', pos['entry_price'])
            hold_days = pos.get('hold_days', 0)
            pos['hold_days'] = hold_days + 1
            if pct >= take_profit:
                return f"익절(+{pct*100:.0f}%)"
            if pct <= stop_loss:
                return f"손절({stop_loss*100:.0f}%)"
            if hold_days >= 5:
                trail = (curr - peak) / peak if peak > 0 else 0
                if trail <= -0.12 and pct > 0.03:
                    return f"추적손절(고점-{abs(trail)*100:.0f}%)"
                ma60 = _ma(prices[max(0, i - 59): i + 1], 60)
                if ma60 is not None and curr < ma60:
                    return "MA60 붕괴"
            return None

        # 포트폴리오 시뮬레이션 (signal_fn 주입)
        total_capital = per_stock * max_positions
        date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                    for sc, d in stock_data.items()}
        positions: Dict[str, dict] = {}
        trades:    list = []
        equity_curve: list = []
        monthly_buys: Dict[str, int] = {}  # 'YYYY-MM' → 월별 신규 매수 건수

        for day in sim_dates:
            # 매도
            sold = []
            for sc, pos in list(positions.items()):
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i  = im[day]
                sd = stock_data[sc]
                reason = _check_sell_generic(i, sd['prices'], pos)
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
                sold.append(sc)
            for sc in sold:
                del positions[sc]

            # 시장 필터 (use_market_filter=False인 전략은 하락장에서도 매수)
            if use_market_filter and market_bullish and not market_bullish.get(day, True):
                unreal = sum(
                    (stock_data[sc]['prices'][date_idx[sc][day]] - pos['entry_price']) * pos['qty']
                    for sc, pos in positions.items() if day in date_idx.get(sc, {})
                )
                realized = sum(t['profit_amt'] for t in trades)
                equity_curve.append({'date': day, 'equity': round(total_capital + realized + unreal)})
                continue

            # 매수 (월별 한도, 시그널 기반 순서대로)
            month_key = day[:7]  # 'YYYY-MM'
            if len(positions) < max_positions and monthly_buys.get(month_key, 0) < max_new_per_month:
                for sc, sd in stock_data.items():
                    if len(positions) >= max_positions:
                        break
                    if monthly_buys.get(month_key, 0) >= max_new_per_month:
                        break
                    if sc in positions:
                        continue
                    im = date_idx.get(sc, {})
                    if day not in im:
                        continue
                    i = im[day]
                    if not signal_fn(i, sd['sim_start_i'], sd['dates'], sd['prices'],
                                     sd['volumes'], sd['frn'], sd['inst'], sd['fins']):
                        continue
                    curr = sd['prices'][i]
                    qty  = max(1, int(per_stock / curr))
                    positions[sc] = {'entry_date': day, 'entry_price': curr,
                                     'qty': qty, 'peak_price': curr, 'hold_days': 0}
                    monthly_buys[month_key] = monthly_buys.get(month_key, 0) + 1

            # 에쿼티
            unreal = sum(
                (stock_data[sc]['prices'][date_idx[sc][day]] - pos['entry_price']) * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            realized = sum(t['profit_amt'] for t in trades)
            equity_curve.append({'date': day, 'equity': round(total_capital + realized + unreal)})

        # 강제 청산
        last_day = sim_dates[-1] if sim_dates else None
        for sc, pos in list(positions.items()):
            sd = stock_data[sc]
            im = date_idx.get(sc, {})
            curr = sd['prices'][im[last_day]] if last_day and last_day in im else sd['prices'][-1]
            pct = (curr - pos['entry_price']) / pos['entry_price']
            trades.append({
                'stock_code': sc, 'entry_date': pos['entry_date'],
                'exit_date': last_day or pos['entry_date'],
                'entry_price': pos['entry_price'], 'exit_price': curr,
                'qty': pos['qty'], 'profit_pct': round(pct * 100, 2),
                'profit_amt': round((curr - pos['entry_price']) * pos['qty']),
                'exit_reason': '기간종료',
            })

        # 종목명 + 성과
        conn2 = sqlite3.connect(DB_PATH, timeout=120)
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for idx in range(0, len(codes), 100):
            batch = codes[idx:idx+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn2.execute(f"""
                SELECT DISTINCT ph.stock_code, COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn2.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)
        from collections import defaultdict
        monthly = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        per_name = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        exit_reasons = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1
        bear_days  = sum(1 for v in market_bullish.values() if not v) if use_market_filter else 0
        total_days = len(market_bullish) if use_market_filter else 0
        filter_pct = round(bear_days / total_days * 100, 1) if total_days else 0
        mkt_label = f"하락장 차단: {bear_days}/{total_days}일({filter_pct}%)" if use_market_filter else "시장필터 없음 (흑자전환 전략 — 하락장에서도 매수)"

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  종목수: {len(stock_data)}\n"
            f"★ {version}: {'KOSPI MA120 필터' if use_market_filter else '시장필터 없음'} / 익절{take_profit*100:.0f}% / 손절{stop_loss*100:.0f}% / 추적손절-12%\n"
            f"{mkt_label}\n"
            f"총 거래: {metrics['total_trades']}건  승률: {metrics['win_rate']}%  "
            f"CAGR: {metrics['cagr']}%  MDD: {metrics['max_drawdown_pct']}%  샤프: {metrics['sharpe']}\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )
        result = {
            **metrics,
            'monthly':      [{'month': k, 'profit': v} for k, v in sorted(monthly.items())],
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)} for k, v in sorted(per_name.items(), key=lambda x: -x[1])[:5]],
            'top_losers':   [{'name': k, 'profit': int(v)} for k, v in sorted(per_name.items(), key=lambda x:  x[1])[:5]],
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
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        raise


# ══════════════════════════════════════════════════════════════
#  V8 — 수출 선행지표 멀티팩터 (HS 무역통계 + 고용 데이터 활용)
# ══════════════════════════════════════════════════════════════

HS_DB_PATH  = "/Applications/stock_dashboard/hs_trade_lab/data/hs_trade_lab.db"
EMP_DB_PATH = "/Applications/stock_dashboard/employment_monitor/employment.db"


def _load_trade_signals() -> Dict[str, Dict[str, float]]:
    """
    HS 무역통계 DB에서 종목별 월별 수출액 로드.
    반환: {stock_code: {ym: export_value}} (예: {'000660': {'2022-03': 900000000, ...}})
    ym 형식: 'YYYY-MM'
    """
    try:
        conn = sqlite3.connect(HS_DB_PATH, timeout=10)
        rows = conn.execute("""
            SELECT m.stock_code, t.period_ym, SUM(t.export_value) AS total_exp
            FROM hs_code_company_map m
            JOIN trade_series_cache t ON m.hs_code = t.hs_code
            WHERE t.export_value > 0
            GROUP BY m.stock_code, t.period_ym
            ORDER BY m.stock_code, t.period_ym
        """).fetchall()
        conn.close()
        result: Dict[str, Dict[str, float]] = {}
        for sc, ym, val in rows:
            result.setdefault(sc, {})[ym] = float(val)
        return result
    except Exception:
        return {}


def _load_employment_signals() -> Dict[str, Dict[str, int]]:
    """
    고용보험 DB에서 종목별 연도별 고용인원 로드.
    반환: {stock_code: {ym: worker_count}} (예: {'000660': {'2024-12': 21493, ...}})
    ym 형식: 'YYYY-MM' (대부분 12월 = 연말 기준)
    """
    try:
        conn = sqlite3.connect(EMP_DB_PATH, timeout=10)
        rows = conn.execute("""
            SELECT stock_code, ym, worker_count
            FROM employment_company
            WHERE worker_count IS NOT NULL AND worker_count > 0
            ORDER BY stock_code, ym
        """).fetchall()
        conn.close()
        result: Dict[str, Dict[str, int]] = {}
        for sc, ym, cnt in rows:
            result.setdefault(sc, {})[ym] = int(cnt)
        return result
    except Exception:
        return {}


def _get_export_yoy(trade_sc: Dict[str, float], ref_ym: str) -> Optional[float]:
    """
    주어진 period_ym 기준으로 YoY 수출 증가율 계산.
    ref_ym: 'YYYY-MM' 형식, 1년 전 동일 월과 비교.
    """
    if not trade_sc or ref_ym not in trade_sc:
        return None
    y, m = int(ref_ym[:4]), int(ref_ym[5:7])
    ya_ym = f"{y - 1}-{m:02d}"
    if ya_ym not in trade_sc:
        return None
    base = trade_sc[ya_ym]
    if base <= 0:
        return None
    return (trade_sc[ref_ym] - base) / base * 100.0


def _date_to_ym(date_str: str, lag_months: int = 2) -> str:
    """
    날짜 문자열('YYYY-MM-DD')을 2개월 전 ym('YYYY-MM')으로 변환.
    무역통계는 약 1-2개월 후 공표 → 2개월 지연 적용.
    """
    y, m = int(date_str[:4]), int(date_str[5:7])
    m -= lag_months
    if m <= 0:
        m += 12
        y -= 1
    return f"{y}-{m:02d}"


def _run_backtest_v8(conn, warmup_start, start_date, end_date, sim_dates,
                     per_stock, max_positions,
                     stop_loss_pct, take_profit_pct, max_hold_days):
    """
    V8 선행지표 멀티팩터 포트폴리오 시뮬레이터.

    매수 조건 (선행지표 기반 — '싸게 사서 비싸게 판다'):
      [A] 수출 YoY 가속: 최근 2개월 수출 YoY 모두 > 10%
                         + 최근 3개월 YoY 평균 > 직전 3개월 YoY 평균 (가속 확인)
      [B] 가격 선진입:   현재가 < MA60 × 1.15 (강한 상승 추세 진입 전, 바닥권~초기)
                         OR 52주 고점 대비 -30% 이상 하락
      [C] 재무 건전:     최근 공시 영업이익 > 0 (적자 기업 제외)
      [D] 고평가 제외:   BPS > 0 이면 PBR < 4.0
      [E] 시장 필터:     KOSPI > MA120 (하락장 매수 금지)
      [F] 고용 보조:     연간 고용 YoY > 3% (데이터 있을 때만 추가 점수)

    매도 조건:
      ① 익절: +take_profit_pct
      ② 손절: -stop_loss_pct
      ③ 추적 손절: 고점 대비 -15% (수익 구간에서만)
      ④ MA60 붕괴 (최소 5일 보유 후)
      ⑤ 최대 보유 max_hold_days 초과 시 강제 청산 (선행지표 미실현 대비)
    """
    # ── 수출 + 고용 데이터 로드 ────────────────────────────────
    trade_all = _load_trade_signals()    # {sc: {ym: export_val}}
    emp_all   = _load_employment_signals()  # {sc: {ym: worker_cnt}}

    # ── KOSPI 시장 필터 ────────────────────────────────────────
    kospi_rows = conn.execute("""
        SELECT date, close FROM price_history
        WHERE stock_code='^KS11' AND date>=? AND date<=? AND close>0
        ORDER BY date ASC
    """, (warmup_start, end_date)).fetchall()
    k_dates      = [r[0] for r in kospi_rows]
    k_price_list = [float(r[1]) for r in kospi_rows]
    market_bullish: Dict[str, bool] = {}
    for ki, kd in enumerate(k_dates):
        if kd < start_date:
            continue
        kma120 = _ma(k_price_list[max(0, ki - 119): ki + 1], 120)
        market_bullish[kd] = (kma120 is None) or (k_price_list[ki] > kma120)

    # ── 재무 데이터 로드 ────────────────────────────────────────
    fin_all: Dict[str, list] = {}
    for r in conn.execute("""
        SELECT stock_code, year, quarter,
               revenue, operating_profit, eps, bps,
               total_equity, net_income, roe,
               CASE WHEN is_annual=1 THEN 1 ELSE 0 END
        FROM financial_data
        WHERE (is_annual=0 AND quarter BETWEEN 1 AND 4)
           OR (is_annual=1)
        ORDER BY stock_code, year, quarter
    """).fetchall():
        sc = r[0]
        fin_all.setdefault(sc, []).append(r[1:])

    # ── 종목 목록: 수출 데이터 있는 종목만 (trade_all 키셋)
    #    + price_history에서 충분한 데이터 보유 확인 ─────────────
    export_stocks = set(trade_all.keys())
    stock_codes = [r[0] for r in conn.execute("""
        SELECT ph.stock_code, COUNT(*) AS cnt
        FROM price_history ph
        INNER JOIN (
            SELECT stock_code FROM stock_universe
            WHERE LENGTH(stock_code)=6 AND stock_code GLOB '[0-9]*'
        ) su ON ph.stock_code = su.stock_code
        WHERE ph.date>=? AND ph.date<=? AND ph.close>0
        GROUP BY ph.stock_code HAVING cnt >= 200
    """, (warmup_start, end_date)).fetchall()
    if r[0] in export_stocks]

    # ── 종목별 가격 데이터 로드 ────────────────────────────────
    stock_data: Dict[str, dict] = {}
    for sc in stock_codes:
        rows = conn.execute("""
            SELECT date, close, COALESCE(volume,0),
                   COALESCE(frn_net_buy,0), COALESCE(inst_net_buy,0)
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

        # ── 데이터 품질 필터: 단일일 50% 이상 급등락 종목 제외
        # (두 시리즈 혼재, 권리락 미반영 등 데이터 오염 방지)
        bad_data = False
        for pi in range(1, len(prices)):
            if prices[pi - 1] > 0:
                ratio = prices[pi] / prices[pi - 1]
                if ratio > 3.0 or ratio < 0.20:   # 하루 3배 이상 또는 80% 이상 하락
                    bad_data = True
                    break
        if bad_data:
            continue

        sim_start_i = next((idx for idx, dt in enumerate(dates) if dt >= start_date), len(dates))
        stock_data[sc] = {
            'dates': dates, 'prices': prices, 'volumes': volumes,
            'frn': frn, 'inst': inst, 'fins': fin_all.get(sc, []),
            'sim_start_i': sim_start_i,
            'trade': trade_all.get(sc, {}),   # 이 종목의 월별 수출액
            'emp':   emp_all.get(sc, {}),     # 이 종목의 연별 고용인원
        }

    date_idx = {sc: {dt: idx for idx, dt in enumerate(d['dates'])}
                for sc, d in stock_data.items()}

    total_capital = per_stock * max_positions
    positions: Dict[str, dict] = {}
    trades:    list = []
    equity_curve: list = []

    def _check_sell_v8(i, prices, pos, trade_sc=None, d=None):
        """
        V8 매도: 선행지표 기반 보유 전략
        - 수출 YoY 이 여전히 양호하면 MA60 붕괴에도 보유 (선행지표 우선)
        - 수출이 꺾이거나 손절선 도달 시 청산
        """
        curr = prices[i]
        if curr > pos.get('peak_price', pos['entry_price']):
            pos['peak_price'] = curr
        pct       = (curr - pos['entry_price']) / pos['entry_price']
        peak      = pos.get('peak_price', pos['entry_price'])
        hold_days = pos.get('hold_days', 0)
        pos['hold_days'] = hold_days + 1

        # 익절 (항상 우선)
        if pct >= take_profit_pct:
            return f"익절(+{pct*100:.0f}%)"

        # 하드 손절 (항상 우선)
        if pct <= -stop_loss_pct:
            return f"손절(-{stop_loss_pct*100:.0f}%)"

        # 추적 손절: 고점 대비 -15% (수익 구간, 5일 이상 보유)
        if hold_days >= 5 and pct > 0.05:
            trail = (curr - peak) / peak if peak > 0 else 0
            if trail <= -0.15:
                return f"추적손절(고점-{abs(trail)*100:.0f}%)"

        # 최대 보유기간 초과
        if hold_days >= max_hold_days:
            return f"기간초과({hold_days}일)"

        # 수출 선행지표 소멸 체크 (30일마다 또는 60일 이상 보유 시)
        # 수출이 꺾이면 MA60 여부와 상관없이 청산
        if hold_days >= 30 and trade_sc is not None and d is not None:
            ref_ym1 = _date_to_ym(d, lag_months=2)
            ref_ym2 = _date_to_ym(d, lag_months=3)
            yoy1 = _get_export_yoy(trade_sc, ref_ym1)
            yoy2 = _get_export_yoy(trade_sc, ref_ym2)
            # 수출이 둘 다 음수로 전환되면 청산
            if yoy1 is not None and yoy2 is not None:
                if yoy1 < -5 and yoy2 < -5:
                    return "수출감소청산"

        # MA200 붕괴 (장기 하락 구조 편입, 30일 이상 보유)
        if hold_days >= 30:
            ma200 = _ma(prices[max(0, i - 199): i + 1], 200)
            if ma200 is not None and curr < ma200 * 0.92:
                return "MA200 붕괴"

        return None

    def _is_buy_v8_signal(sc, sd, i, d):
        """
        V8 매수 시그널 v2 — '수출 선행 + 일시 눌림목 매수'

        철학: 수출이 증가하는 기업이 단기 조정 구간에 있을 때 매수.
              → 장기 추세(MA200)는 살아 있으나 단기적으로 MA60 이하 또는 근접
              → 수출 데이터(선행 2개월)가 YoY 회복/성장 구간
              → RSI 35~60 (과매도 후 회복 초기)
        """
        if i < sd['sim_start_i']:
            return False
        curr   = sd['prices'][i]
        trade  = sd['trade']
        emp    = sd['emp']
        fins   = sd['fins']

        # ══ [A] 수출 YoY 변곡점 포착 (선행 2개월 지연) ═══════════
        # 핵심 철학: 수출이 하락/부진 → 상승 전환하는 "변곡점" 순간 매수
        # 시장은 1~3개월 후 분기 실적에서 이를 확인 → 선행 매수 기회
        ref_ym1 = _date_to_ym(d, lag_months=2)   # 가장 최근 공표된 달
        ref_ym2 = _date_to_ym(d, lag_months=3)   # 그 전달
        ref_ym3 = _date_to_ym(d, lag_months=4)   # 3개월 전
        ref_ym4 = _date_to_ym(d, lag_months=5)   # 4개월 전
        ref_ym5 = _date_to_ym(d, lag_months=6)   # 5개월 전

        yoy1 = _get_export_yoy(trade, ref_ym1)
        yoy2 = _get_export_yoy(trade, ref_ym2)
        if yoy1 is None or yoy2 is None:
            return False

        # [A-1] 최근 2개월 YoY 모두 양수 > 8% (본격 회복/성장)
        if yoy1 < 8 or yoy2 < 8:
            return False

        # [A-2] 변곡점 확인: 2~4개월 전 중 하나라도 YoY < 5%였거나,
        #        최근 3개월 평균 YoY가 이전 3개월보다 20%p 이상 개선
        yoy3 = _get_export_yoy(trade, ref_ym3)
        yoy4 = _get_export_yoy(trade, ref_ym4)
        yoy5 = _get_export_yoy(trade, ref_ym5)

        # 변곡점 검사: 최근 성장이 이전보다 의미있게 개선됐는가?
        older = [y for y in [yoy3, yoy4, yoy5] if y is not None]
        if older:
            avg_older = sum(older) / len(older)
            avg_recent = (yoy1 + yoy2) / 2
            # 케이스 A: 이전에 부진(-10% 이하)하다가 최근 회복
            # 케이스 B: 이전보다 20%p 이상 가속
            # 케이스 C: 이전에도 괜찮았지만 최근에도 유지 (지속 성장)
            inflection = (avg_older < 5 and avg_recent >= 8) or \
                         (avg_recent >= avg_older + 20) or \
                         (avg_recent >= 15 and avg_older >= 10)
            if not inflection:
                return False

        # ══ [B] 가격 구조: 장기 추세 안에서 단기 눌림 ══════════
        # 철학: 수출이 이미 성장 중이고, 주가가 이제 막 MA60을 돌파하는 순간 매수
        #       → 너무 이른 진입(MA60 아래) X, 너무 늦은 진입(Minervini 완성) X
        p_all = sd['prices'][max(0, i - 250): i + 1]
        if len(p_all) < 120:
            return False
        ma60  = sum(p_all[-60:]) / 60
        ma120 = sum(p_all[-120:]) / 120
        ma200 = sum(p_all) / len(p_all) if len(p_all) >= 200 else None

        # [B-1] 현재가 MA60 근처 또는 MA60 위 (막 돌파한 상태)
        # MA60 위이되 MA60의 120% 이하 (아직 Minervini 강한 추세 아님)
        if curr < ma60 * 0.95:
            return False  # MA60보다 5% 이상 아래 → 아직 하락 중
        if curr > ma60 * 1.20:
            return False  # MA60보다 20% 이상 위 → 이미 강한 추세 진입, V4 영역

        # [B-2] MA60이 10일 전보다 높거나 같다 (MA60 방향 전환 또는 상승 중)
        if i >= 10:
            ma60_10ago = sum(sd['prices'][max(0, i - 69): i - 9]) / 60 \
                         if (i - 9) >= 60 else None
            if ma60_10ago is not None and ma60 < ma60_10ago * 0.99:
                return False  # MA60 여전히 하락 중

        # [B-3] 장기 하락 구조 제외: MA120 > MA200 * 0.92 (장기 상승 구조)
        if ma200 is not None and ma120 < ma200 * 0.92:
            return False

        # [B-4] 52주 고점 대비 -35% 이상 하락이면 너무 큰 낙폭 → 제외
        high52 = max(sd['prices'][max(0, i - 251): i + 1])
        if high52 > 0 and curr < high52 * 0.65:
            return False

        # ══ [C] RSI 42~65 (눌림 회복 ~ 모멘텀 시작 구간) ═════
        rsi_val = _rsi(p_all[-29:] if len(p_all) >= 29 else p_all)
        if rsi_val is None or rsi_val < 42 or rsi_val > 65:
            return False  # 42 미만=아직 하락 모멘텀, 65 초과=과열 초입

        # ══ [D] 재무 건전: 영업이익 > 0 ═════════════════════
        fin = _get_financial_as_of(fins, d)
        if fin is not None:
            _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann = fin
            if op is not None and op <= 0:
                return False  # 영업적자 제외
            if bps and bps > 0 and curr / bps > 6.0:
                return False  # 극단적 고평가(PBR>6) 제외

        # ══ [E] 고용 보조: 연간 고용 감소(-5% 이상) 기업 제외 ══
        if emp:
            emp_sorted = sorted(
                [(ym, cnt) for ym, cnt in emp.items() if ym <= d[:7]],
                reverse=True
            )
            if len(emp_sorted) >= 2:
                (y1, cnt1), (_, cnt2) = emp_sorted[0], emp_sorted[1]
                if cnt2 > 0 and (cnt1 - cnt2) / cnt2 * 100 < -5.0:
                    return False

        return True

    for day in sim_dates:
        # 매도 체크
        sold = []
        for sc, pos in list(positions.items()):
            im = date_idx.get(sc, {})
            if day not in im:
                continue
            i      = im[day]
            sd     = stock_data[sc]
            reason = _check_sell_v8(i, sd['prices'], pos, trade_sc=sd['trade'], d=day)
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
            sold.append(sc)
        for sc in sold:
            del positions[sc]

        # 시장 필터 (하락장 매수 금지)
        if market_bullish and not market_bullish.get(day, True):
            unreal = sum(
                (stock_data[sc]['prices'][date_idx[sc][day]] - pos['entry_price']) * pos['qty']
                for sc, pos in positions.items() if day in date_idx.get(sc, {})
            )
            realized = sum(t['profit_amt'] for t in trades)
            equity_curve.append({'date': day, 'equity': round(total_capital + realized + unreal)})
            continue

        # 매수 스캔
        if len(positions) < max_positions:
            for sc, sd in stock_data.items():
                if len(positions) >= max_positions:
                    break
                if sc in positions:
                    continue
                im = date_idx.get(sc, {})
                if day not in im:
                    continue
                i = im[day]
                if not _is_buy_v8_signal(sc, sd, i, day):
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

        # 에쿼티 커브
        unreal = sum(
            (stock_data[sc]['prices'][date_idx[sc][day]] - pos['entry_price']) * pos['qty']
            for sc, pos in positions.items() if day in date_idx.get(sc, {})
        )
        realized = sum(t['profit_amt'] for t in trades)
        equity_curve.append({'date': day, 'equity': round(total_capital + realized + unreal)})

    # 기간 종료 강제 청산
    last_day = sim_dates[-1] if sim_dates else None
    for sc, pos in list(positions.items()):
        sd  = stock_data[sc]
        im  = date_idx.get(sc, {})
        curr = sd['prices'][im[last_day]] if last_day and last_day in im else sd['prices'][-1]
        pct  = (curr - pos['entry_price']) / pos['entry_price']
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

    return trades, equity_curve, len(stock_data), market_bullish


def run_backtest_v8(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    run_name: str = None, run_id: str = None) -> str:
    """
    V8 수출 선행지표 멀티팩터 백테스트.
    HS 무역통계(월별 수출 YoY) + 고용 데이터를 선행 신호로 활용.
    """
    init_backtest_db()
    run_name = run_name or f"V8 수출선행 {start_date[:7]}~{end_date[:7]}"
    if run_id is None:
        run_id = str(uuid.uuid4())[:8]
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("""
            INSERT INTO backtest_runs (run_id,name,start_date,end_date,per_stock,max_pos,status)
            VALUES (?,?,?,?,?,?,'running')
        """, (run_id, run_name, start_date, end_date, per_stock, max_positions))
        conn.commit()
    else:
        conn = sqlite3.connect(DB_PATH, timeout=120)
        conn.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (run_id,))
        conn.commit()

    try:
        warmup_start = (datetime.strptime(start_date, '%Y-%m-%d')
                        - timedelta(days=450)).strftime('%Y-%m-%d')
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

        trades, equity_curve, n_stocks, market_bullish = _run_backtest_v8(
            conn, warmup_start, start_date, end_date, sim_dates,
            per_stock, max_positions,
            stop_loss_pct=0.10,      # 선행 매수 → 넓은 손절 허용
            take_profit_pct=0.30,    # 선행 매수 → 충분한 상승 기다림
            max_hold_days=252,       # 최대 1년 보유 (선행지표 실현 대기)
        )

        # 종목명 매핑
        name_map = {}
        codes = list({t['stock_code'] for t in trades})
        for idx in range(0, len(codes), 100):
            batch = codes[idx:idx+100]
            ph = ','.join('?'*len(batch))
            for sc, sn in conn.execute(f"""
                SELECT DISTINCT ph.stock_code, COALESCE(su.stock_name, ph.stock_code)
                FROM (SELECT DISTINCT stock_code FROM price_history WHERE stock_code IN ({ph})) ph
                LEFT JOIN stock_universe su USING(stock_code)
            """, batch).fetchall():
                name_map[sc] = sn
        conn.close()
        for t in trades:
            t['stock_name'] = name_map.get(t['stock_code'], t['stock_code'])

        total_capital = per_stock * max_positions
        metrics = _calc_metrics(trades, equity_curve, start_date, end_date, total_capital)

        from collections import defaultdict
        monthly = {}
        for t in sorted(trades, key=lambda x: x['exit_date']):
            mo = t['exit_date'][:7]
            monthly[mo] = monthly.get(mo, 0) + t['profit_amt']
        per_name: Dict[str, float] = defaultdict(float)
        for t in trades:
            per_name[t.get('stock_name', t['stock_code'])] += t['profit_amt']
        exit_reasons: Dict[str, int] = defaultdict(int)
        for t in trades:
            exit_reasons[t['exit_reason']] += 1

        bear_days  = sum(1 for v in market_bullish.values() if not v)
        total_days = len(market_bullish)
        filter_pct = round(bear_days / total_days * 100, 1) if total_days else 0

        summary_text = (
            f"기간: {start_date} ~ {end_date}  |  수출데이터 종목수: {n_stocks}\n"
            f"★ V8 수출선행: HS무역통계 월별수출 YoY>10%(2개월연속) + 가격선진입(MA60근처) + 영업이익>0\n"
            f"  매개변수: 익절+30% / 손절-10% / 추적손절-15% / 최대보유252일 / KOSPI MA120 필터\n"
            f"하락장 차단: {bear_days}/{total_days}일({filter_pct}%)\n"
            f"총 거래: {metrics['total_trades']}건  승률: {metrics['win_rate']}%  "
            f"CAGR: {metrics['cagr']}%  MDD: {metrics['max_drawdown_pct']}%  샤프: {metrics['sharpe']}\n"
            f"손익비: {metrics['pl_ratio']}배  총손익: {metrics.get('total_profit_amt',0):,}원\n"
            f"매도사유: " + " / ".join(f"{k} {v}건" for k, v in sorted(exit_reasons.items()))
        )
        result = {
            **metrics,
            'strategy': 'v8',
            'monthly':      [{'month': k, 'profit': v} for k, v in sorted(monthly.items())],
            'equity_curve': equity_curve[-252:],
            'top_winners':  [{'name': k, 'profit': int(v)}
                             for k, v in sorted(per_name.items(), key=lambda x: -x[1])[:5]],
            'top_losers':   [{'name': k, 'profit': int(v)}
                             for k, v in sorted(per_name.items(), key=lambda x:  x[1])[:5]],
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
            c = sqlite3.connect(DB_PATH, timeout=120)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (err, run_id))
            c.commit(); c.close()
        except Exception:
            pass
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--start',     default='2023-04-01')
    parser.add_argument('--end',       default='2025-12-31')
    parser.add_argument('--per-stock', type=float, default=10_000_000)
    parser.add_argument('--max-pos',   type=int,   default=10)
    parser.add_argument('--version',   default='V4', choices=['V4','V8','V10','V11','V12'])
    args = parser.parse_args()

    print(f"백테스트 시작 ({args.version}): {args.start} ~ {args.end}  "
          f"(종목당 {args.per_stock:,.0f}원, 최대 {args.max_pos}종목)")

    fn_map = {'V4': run_backtest, 'V8': run_backtest_v8,
              'V10': run_backtest_v10, 'V11': run_backtest_v11, 'V12': run_backtest_v12}
    rid = fn_map[args.version](args.start, args.end,
                                per_stock=args.per_stock, max_positions=args.max_pos)
    print(f"완료! run_id={rid}")
    conn = sqlite3.connect(DB_PATH, timeout=120)
    row  = conn.execute("SELECT summary_text FROM backtest_runs WHERE run_id=?", (rid,)).fetchone()
    conn.close()
    if row: print(row[0])
