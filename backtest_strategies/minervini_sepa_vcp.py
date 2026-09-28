"""
minervini_sepa_vcp.py — Minervini SEPA + VCP 통합 전략 (2026-09-26)

PDF '마크 미너비니 SEPA 및 VCP 파이썬 시스템 구현 전략'을 기반으로
기존 minervini_trend_template.py를 4단계로 보강한다.

★ 기존 대비 추가·변경 사항
  1. SEPA 펀더멘털 필터 (financial_data 활용, avail_date 룩어헤드 방지)
       - EPS YoY ≥ 20% (A+ 등급은 ≥ 40%)
       - EPS 가속: 최근 분기 YoY > 직전 분기 YoY
       - 매출 YoY ≥ 15%
       - 영업이익률 확장: 최근 분기 OPM > 직전 분기 OPM
       - ROE TTM ≥ 17%
  2. VCP (Volatility Contraction Pattern) 감지 — scipy.signal 기반
       - D1 > D2 > D3: 순차적으로 줄어드는 조정 깊이
       - 거래량 건조 (Volume Dry-up): 최근 5일 평균 거래량 < 직전 45일 평균의 60%
  3. 진입 스톱 강화: -8% (기존 -12%)
  4. 시장 국면 필터: _run_generic_backtest의 use_market_filter(KOSPI MA60/MA120) 유지

★ 파라미터 튜닝 가이드
  use_fundamental = False  → SEPA 필터 끄고 기술적 분석만 (기존과 동일)
  use_vcp         = False  → VCP 필터 끄고 트렌드 템플릿만 진입
  EPS_YOY_MIN     = 0.20   → 20% (논문 기준), 0.40이면 A+ 등급
  ROE_MIN         = 0.17   → 17%
  REV_YOY_MIN     = 0.15   → 15%
"""
from __future__ import annotations

import logging
from typing import Optional

from backtest_common import (
    DB_PATH,
    _ma,
    _run_generic_backtest,
    logger,
    sqlite3,
)

# ─── 전략 파라미터 ────────────────────────────────────────────────────────────
MA_TREND_THRESHOLD_PCT    = 1.0    # 200일선 20거래일 상승률 최소 %
PRICE_TREND_SLOPE_MIN     = 0.05   # 60일 가격 추세 기울기 (평균가 대비 %/일)
STOP_LOSS_PCT             = -0.08  # 하드스탑 (-8%, 원본 7~8%)
TAKE_PROFIT_PCT           = 0.30   # 이익실현 (+30%)
TRAIL_STOP_PCT            = -0.10  # 트레일링 스탑

# SEPA 펀더멘털 기준
EPS_YOY_MIN               = 0.20   # EPS 전년 동기 대비 최소 성장률 (+20%)
REV_YOY_MIN               = 0.15   # 매출 전년 동기 대비 최소 성장률 (+15%)
ROE_MIN                   = 0.17   # ROE TTM 최소 (17%)

# VCP 기준 (catchitearly/vcp 방법론 참조)
VCP_MIN_WAVES             = 2      # 최소 수축 파동 수
VCP_MAX_DEPTH_RATIO       = 0.80   # 다음 파동 깊이 ≤ 이전 × 0.80
VCP_VOLUME_DRY_THRESHOLD  = 0.70   # 최근 10일 거래량 ≤ 직전 50일 평균 × 0.70 (5d/45d×0.60 → 10d/50d×0.70)
VCP_ATR_TIGHT_THRESHOLD   = 0.65   # 최근 10일 ATR ≤ 직전 50일 ATR × 0.65 (가격 수축 확인)


# ─── 기울기 계산 (numpy 없이) ─────────────────────────────────────────────────
def _slope_pct_per_day(values: list) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    if mean_y == 0:
        return 0.0
    num = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values))
    den = sum((x - mean_x) ** 2 for x in range(n))
    if den == 0:
        return 0.0
    return (num / den / mean_y) * 100


# ─── SEPA 펀더멘털 체크 ───────────────────────────────────────────────────────
def _sepa_pass(fins: list, signal_date: str) -> bool:
    """avail_date ≤ signal_date인 최신 분기 실적으로 SEPA 펀더멘털 조건 판단.

    fins 행 구조 (backtest_common.py의 _run_generic_backtest에서 로드):
      (year, quarter, revenue, operating_profit, eps, bps, total_equity,
       net_income, roe, is_annual, avail_date)
         0      1       2             3           4    5       6
         7       8         9            10
    """
    # 분기 데이터만, avail_date 공개 이후, CFS 우선 (중복 행 처리)
    quarterly: dict[tuple, dict] = {}
    for r in fins:
        if r[9]:  # is_annual
            continue
        q_key = (int(r[0]), int(r[1]))  # (year, quarter)
        avail = r[10]
        if avail > signal_date:
            continue
        # 같은 분기 중복 행 → avail_date 가장 빠른 것 (최초 공시 기준)
        existing = quarterly.get(q_key)
        if existing is None or avail < existing['avail']:
            quarterly[q_key] = {
                'avail': avail,
                'rev': r[2], 'op': r[3], 'eps': r[4], 'roe': r[8],
            }

    if not quarterly:
        return False

    # 최신 분기
    latest_key = max(quarterly.keys())
    latest = quarterly[latest_key]
    yr, qtr = latest_key
    rev, op, eps, roe = latest['rev'], latest['op'], latest['eps'], latest['roe']

    # ① ROE ≥ 17%
    if roe is not None and roe < ROE_MIN * 100:  # DB는 % 단위 (예: 17.0)
        return False

    # ② EPS YoY ≥ 20%
    prev_yr_key = (yr - 1, qtr)
    if prev_yr_key not in quarterly:
        return False  # 작년 동기 데이터 없으면 판단 불가 → 보수적으로 제외
    prev = quarterly[prev_yr_key]
    prev_eps = prev['eps']
    prev_rev = prev['rev']
    prev_op  = prev['op']

    if not (eps and prev_eps and prev_eps > 0):
        return False
    eps_yoy = eps / prev_eps - 1
    if eps_yoy < EPS_YOY_MIN:
        return False

    # ③ 매출 YoY ≥ 15%
    if rev and prev_rev and prev_rev > 0:
        if rev / prev_rev - 1 < REV_YOY_MIN:
            return False

    # ④ 영업이익률 확장 (최근 > 직전 분기)
    # 직전 분기 키 계산
    if qtr == 1:
        prev_q_key = (yr - 1, 4)
    else:
        prev_q_key = (yr, qtr - 1)
    if prev_q_key in quarterly:
        pq = quarterly[prev_q_key]
        if (op and rev and rev > 0 and
                pq['op'] is not None and pq['rev'] and pq['rev'] > 0):
            opm_curr = op / rev
            opm_prev = pq['op'] / pq['rev']
            if opm_curr <= opm_prev:
                return False

    # ⑤ EPS 가속 (최근 분기 YoY > 직전 분기 YoY)
    if prev_q_key in quarterly:
        pq = quarterly[prev_q_key]
        pq_prev_yr_key = (prev_q_key[0] - 1, prev_q_key[1])
        if pq_prev_yr_key in quarterly:
            pq_prev_yr = quarterly[pq_prev_yr_key]
            if (pq['eps'] and pq_prev_yr['eps'] and pq_prev_yr['eps'] > 0
                    and pq['eps'] / pq_prev_yr['eps'] - 1 >= eps_yoy):
                return False  # 전분기 YoY가 이번 YoY보다 크다 → 가속 없음

    return True


# ─── VCP 체크 ────────────────────────────────────────────────────────────────
def _vcp_pass(prices: list, volumes: list, i: int) -> bool:
    """최근 100거래일 가격/거래량으로 VCP 패턴을 검증.

    1. SG 필터로 스무딩
    2. 피크/트러프 탐색
    3. 순차 수축 파동 D1 > D2 (≥ VCP_MIN_WAVES개)
    4. 거래량 건조 확인
    """
    try:
        from scipy.signal import savgol_filter, argrelextrema
        import numpy as np
    except ImportError:
        return True  # scipy 없으면 VCP 필터 비활성화 (통과)

    start = max(0, i - 99)
    p_arr = np.array(prices[start:i + 1], dtype=float)
    v_arr = np.array(volumes[start:i + 1], dtype=float)
    n = len(p_arr)
    if n < 30:
        return False

    # Savitzky-Golay 스무딩
    sg_win = 11 if n >= 11 else (n if n % 2 == 1 else n - 1)
    if sg_win < 5:
        return False
    smoothed = savgol_filter(p_arr, window_length=sg_win, polyorder=3)

    peaks_idx   = argrelextrema(smoothed, np.greater, order=3)[0]
    troughs_idx = argrelextrema(smoothed, np.less,    order=3)[0]
    if len(peaks_idx) < VCP_MIN_WAVES or len(troughs_idx) < VCP_MIN_WAVES:
        return False

    # 피크-트러프 교번 시퀀스로 조정 깊이 추출
    all_exts = sorted(
        [(idx, 'P', float(p_arr[idx])) for idx in peaks_idx] +
        [(idx, 'T', float(p_arr[idx])) for idx in troughs_idx],
        key=lambda x: x[0],
    )
    depths: list[float] = []
    pending_peak = None
    for _, typ, val in all_exts:
        if typ == 'P':
            pending_peak = val
        elif typ == 'T' and pending_peak is not None:
            if pending_peak > 0:
                depths.append((pending_peak - val) / pending_peak)
            pending_peak = None

    if len(depths) < VCP_MIN_WAVES:
        return False

    # 순차 수축 검증: 각 파동이 이전 파동의 VCP_MAX_DEPTH_RATIO 이하
    for j in range(1, len(depths)):
        if depths[j] >= depths[j - 1] * VCP_MAX_DEPTH_RATIO:
            return False

    # 거래량 건조 (Volume Dry-up) — catchitearly/vcp: 10d avg < 70% of 50d avg
    if len(v_arr) >= 60:
        recent_v = float(np.mean(v_arr[-10:]))
        prior_v  = float(np.mean(v_arr[-60:-10]))
        if prior_v > 0 and recent_v > prior_v * VCP_VOLUME_DRY_THRESHOLD:
            return False

    # ATR 수축 (Price Tightness) — catchitearly/vcp: 10d ATR < 65% of 50d ATR
    if len(p_arr) >= 60:
        def _atr_simple(arr: np.ndarray) -> float:
            highs = arr[1:]
            lows  = arr[:-1]
            return float(np.mean(np.abs(highs - lows)))

        atr_recent = _atr_simple(p_arr[-10:])
        atr_prior  = _atr_simple(p_arr[-60:-10])
        if atr_prior > 0 and atr_recent > atr_prior * VCP_ATR_TIGHT_THRESHOLD:
            return False  # 변동성 아직 충분히 수축하지 않음

    return True


# ─── 신호 함수 팩토리 ─────────────────────────────────────────────────────────
def _make_is_buy_sepa_vcp(
    k_idx: dict, k_prices: list,
    use_fundamental: bool = True,
    use_vcp: bool = True,
):
    """SEPA + VCP 신호 함수를 클로저로 반환 (fins 인자 사용)."""

    def _kospi_6m_return(day: str) -> Optional[float]:
        idx = k_idx.get(day)
        if idx is None:
            for d in reversed(sorted(k_idx.keys())):
                if d <= day:
                    idx = k_idx[d]
                    break
        if idx is None or idx < 126:
            return None
        p0, p126 = k_prices[idx], k_prices[idx - 126]
        return (p0 / p126 - 1) * 100 if p126 > 0 else None

    def _is_buy(
        i: int, sim_start_i: int,
        dates: list, prices: list, volumes: list,
        frn_net: list, inst_net: list, fins: list,
    ) -> bool:
        if i < sim_start_i or i < 260:
            return False
        curr = prices[i]
        if curr <= 0:
            return False

        # ── 트렌드 템플릿 8조건 ──────────────────────────────────────────────
        # [5][6] 52주 위치
        window_252 = prices[max(0, i - 251):i + 1]
        high_52w, low_52w = max(window_252), min(window_252)
        if low_52w <= 0 or high_52w <= 0:
            return False
        if (curr - low_52w) / low_52w * 100 < 30:
            return False  # 조건 5
        if (high_52w - curr) / high_52w * 100 > 25:
            return False  # 조건 6 (52주 고가의 25% 이내)

        # [1][2][4] 이동평균 정렬
        ma50  = _ma(prices[:i + 1], 50)
        ma150 = _ma(prices[:i + 1], 150)
        ma200 = _ma(prices[:i + 1], 200)
        if not all([ma50, ma150, ma200]):
            return False
        if not (curr > ma50 > ma150 > ma200):
            return False

        # [3] 200일선 기울기 ≥ +1% (20거래일 전 대비)
        ma200_20ago = _ma(prices[:i - 19], 200) if i >= 219 else None
        if not ma200_20ago or ma200_20ago <= 0:
            return False
        if (ma200 - ma200_20ago) / ma200_20ago * 100 < MA_TREND_THRESHOLD_PCT:
            return False

        # [7] Stage 2: 60일 가격 추세 기울기 양수
        if _slope_pct_per_day(prices[i - 59:i + 1]) <= PRICE_TREND_SLOPE_MIN:
            return False

        # [8] RS 조건: KOSPI 대비 6개월 초과수익 +15%p (IBD RS ibd_rs_daily 없을 때 근사)
        if i >= 126:
            stock_6m = (curr / prices[i - 126] - 1) * 100 if prices[i - 126] > 0 else None
            kospi_6m = _kospi_6m_return(dates[i])
            if stock_6m is None or kospi_6m is None:
                return False
            if stock_6m - kospi_6m < 15.0:
                return False

        # ── SEPA 펀더멘털 필터 ───────────────────────────────────────────────
        if use_fundamental and fins:
            if not _sepa_pass(fins, dates[i]):
                return False

        # ── VCP 패턴 필터 ────────────────────────────────────────────────────
        if use_vcp:
            if not _vcp_pass(prices, volumes, i):
                return False

        return True

    return _is_buy


# ─── 백테스트 진입점 ──────────────────────────────────────────────────────────
def run_backtest_minervini_sepa_vcp(
    start_date: str, end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    use_fundamental: bool = True,
    use_vcp: bool = True,
    chart_confluence: bool = False,
    run_name: str = None,
    run_id: str = None,
) -> str:
    """Minervini SEPA + VCP 통합 전략 백테스트.

    use_fundamental=False, use_vcp=False → 기존 Minervini 트렌드 템플릿과 동일.
    """
    import db_compat as _dc
    conn = _dc.connect_primary_db()
    try:
        rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0 ORDER BY date
        """).fetchall()
    finally:
        conn.close()

    k_dates  = [r[0] for r in rows]
    k_prices = [float(r[1]) for r in rows]
    k_idx    = {d: idx for idx, d in enumerate(k_dates)}

    if len(k_prices) < 127:
        logger.warning("[SEPA+VCP] KOSPI 데이터 부족 — RS 조건 건너뜀")

    variant = []
    if use_fundamental:
        variant.append("SEPA")
    if use_vcp:
        variant.append("VCP")
    suffix = "+".join(variant) if variant else "TrendOnly"

    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version=f'V-MINERVINI-{suffix}',
        signal_fn=_make_is_buy_sepa_vcp(k_idx, k_prices, use_fundamental, use_vcp),
        start_date=start_date,
        end_date=end_date,
        per_stock=per_stock,
        max_positions=max_positions,
        run_name=run_name or f"Minervini {suffix} {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=STOP_LOSS_PCT,
        take_profit=TAKE_PROFIT_PCT,
        trail_stop=TRAIL_STOP_PCT,
        mktcap_min=1000,
        max_new_per_month=10,
        use_market_filter=True,
        strategy_key='minervini_sepa_vcp',
        avoid_overheat=None,
    )
