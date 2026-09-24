"""
minervini_trend_template.py -- run_backtest_minervini_trend_template()

2026-09-19 신규. 소유자가 GitHub에서 찾아온 xang1234/stock-screener
(https://github.com/xang1234/stock-screener, MIT License)의
MinerviniScanner(backend/app/scanners/minervini_scanner.py)를 이 코드베이스의
백테스트 신호 함수 형태로 옮긴 것. Mark Minervini의 "Trend Template" 8개 기준 중
1~7번을 포팅했다(8번 VCP 패턴은 원본에서도 "optional but ideal"이고, 원본
VCPDetector가 이 저장소엔 없는 pandas 기반 패턴 인식이라 이번엔 제외 - 스코어링에서도
VCP 없이 나머지 80점만으로 재정규화하는 원본 로직과 같은 취지):

  1. RS Rating > 70 (원본은 전체 시장 대비 percentile rank인데, 이 코드베이스의
     signal_fn은 종목별 배열만 받고 시장 전체 순위를 계산할 공유 컨텍스트가 없다 -
     golden_cross.py가 이미 같은 문제를 겪고 KOSPI 6개월 수익률 대비 초과수익으로
     대체한 것과 같은 근사를 쓴다: 종목 6개월(126거래일) 수익률이 KOSPI 6개월
     수익률보다 +15%p 이상 높으면 통과. 진짜 percentile rank가 아니므로 원본만큼
     정밀하지 않다는 점을 명시한다.
  2. 현재가 > 50일선 > 150일선 > 200일선
  3. 200일선이 최근 20거래일간 1% 이상 상승(원본 stage_analysis.calculate_ma_trend와
     동일 임계값)
  4. 50일선이 150일선/200일선 위 (기준2가 이미 함의하지만 원본 8개 항목을 그대로 유지)
  5. 현재가가 52주 최저가보다 30% 이상 위
  6. 현재가가 52주 최고가에서 25% 이내
  7. Stage 2(Weinstein): 기준2/3 + 최근 60거래일 가격의 선형회귀 기울기가
     평균가 대비 하루 +0.05% 이상(원본 stage_analysis.calculate_price_trend와
     동일 임계값 - "uptrend" 판정)

signal_fn 자체는 KOSPI 시리즈가 필요해 golden_cross.py처럼 run_backtest 함수 안에서
한 번 로드해 클로저로 넘긴다 - peg.py의 단순 _is_buy_v1 시그니처와 호환되면서도
_run_generic_backtest()를 그대로 쓴다(golden_cross.py처럼 자체 루프를 새로 짜지
않음 - 감사 스크립트가 지목한 오염률 최악 전략(deep_recovery/extreme_dd_volume)이
_run_generic_backtest()를 안 쓰고 자체 루프를 짜서 assert_research_prices() 가격
무결성 게이트를 건너뛴 것과 같은 함정을 피하기 위함).
"""
from typing import Optional

from backtest_common import (
    DB_PATH,
    _ma,
    _run_generic_backtest,
    logger,
    sqlite3,
)

MA_TREND_THRESHOLD_PCT = 1.0     # stage_analysis.calculate_ma_trend와 동일
PRICE_TREND_SLOPE_THRESHOLD = 0.05  # stage_analysis.calculate_price_trend와 동일 (%/일)
RS_OUTPERFORM_THRESHOLD_PP = 15.0   # RS Rating>70 근사치 (원본은 percentile rank)


def _slope_pct_per_day(values: list) -> float:
    """단순 최소자승 기울기, 평균가 대비 %/일로 정규화 (numpy 없이 순수 파이썬).
    원본 stage_analysis.calculate_price_trend의 np.polyfit(x, prices, 1) 대체."""
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
    slope = num / den
    return (slope / mean_y) * 100


def _make_is_buy_minervini(k_idx: dict, k_prices: list):
    """KOSPI 날짜→인덱스/종가 클로저를 받아 _is_buy_v1 호환 신호함수를 반환.
    golden_cross.py의 _get_k6m(date) 클로저와 동일한 접근."""

    def _kospi_6m_return(day: str) -> Optional[float]:
        idx = k_idx.get(day)
        if idx is None:
            for d in reversed(k_idx.keys()):
                if d <= day:
                    idx = k_idx[d]
                    break
        if idx is None or idx < 126:
            return None
        p0, p126 = k_prices[idx], k_prices[idx - 126]
        return (p0 / p126 - 1) * 100 if p126 > 0 else None

    def _is_buy_minervini(
        i: int, sim_start_i: int,
        dates: list, prices: list, volumes: list,
        frn_net: list, inst_net: list, _unused_delayed_data: list,
    ) -> bool:
        # 마지막 인자는 _run_generic_backtest의 공용 콜백 시그니처를 맞추기 위한 자리만
        # 차지하는 미사용 값이다(원래 이름이 DART/재무 인자와 같아서
        # audit_selected_strategy_data_availability.py의 텍스트스캔에 매칭되어, 실제로는
        # 재무 데이터를 전혀 쓰지 않는데도 "지연데이터 사용"으로 오탐 처리됐다
        # - 2026-09-21 확인 후 개명).
        # 200일선 + "20거래일 전" 200일선 + 52주(약 252거래일) 데이터가 다 있어야 함
        if i < sim_start_i or i < 260:
            return False
        curr = prices[i]
        if curr <= 0:
            return False

        # [5][6] 52주 위치 - 최저가+30% 이상, 최고가-25% 이내
        window_52w = prices[max(0, i - 251):i + 1]
        high_52w, low_52w = max(window_52w), min(window_52w)
        if low_52w <= 0 or high_52w <= 0:
            return False
        above_low_pct = (curr - low_52w) / low_52w * 100
        from_high_pct = (high_52w - curr) / high_52w * 100
        if above_low_pct < 30 or from_high_pct > 25:
            return False

        # [2][4] 이동평균 정렬: 현재가 > 50일 > 150일 > 200일
        ma50 = _ma(prices[:i + 1], 50)
        ma150 = _ma(prices[:i + 1], 150)
        ma200 = _ma(prices[:i + 1], 200)
        if not ma50 or not ma150 or not ma200:
            return False
        if not (curr > ma50 > ma150 > ma200):
            return False

        # [3] 200일선이 20거래일 전보다 1% 이상 상승
        ma200_20ago = _ma(prices[:i - 19], 200) if i >= 219 else None
        if not ma200_20ago or ma200_20ago <= 0:
            return False
        ma200_change_pct = (ma200 - ma200_20ago) / ma200_20ago * 100
        if ma200_change_pct < MA_TREND_THRESHOLD_PCT:
            return False

        # [7] Stage 2: 최근 60거래일 가격 추세가 우상향(선형회귀 기울기 기준)
        slope = _slope_pct_per_day(prices[i - 59:i + 1])
        if slope <= PRICE_TREND_SLOPE_THRESHOLD:
            return False

        # [1] RS: 종목 6개월 수익률이 KOSPI 6개월 수익률보다 +15%p 이상
        if i < 126:
            return False
        stock_6m = (curr - prices[i - 126]) / prices[i - 126] * 100 if prices[i - 126] > 0 else None
        kospi_6m = _kospi_6m_return(dates[i])
        if stock_6m is None or kospi_6m is None:
            return False
        if stock_6m - kospi_6m < RS_OUTPERFORM_THRESHOLD_PP:
            return False

        return True

    return _is_buy_minervini


def run_backtest_minervini_trend_template(
    start_date: str, end_date: str,
    per_stock: float = 10_000_000,
    max_positions: int = 10,
    chart_confluence: bool = False,
    run_name: str = None, run_id: str = None,
) -> str:
    """Mark Minervini Trend Template 독립전략 (2026-09-19 신규, xang1234/stock-screener
    포팅 - 이 파일 최상단 docstring 참고)."""
    conn = sqlite3.connect(DB_PATH, timeout=60)
    try:
        kospi_rows = conn.execute("""
            SELECT date, close FROM price_history
            WHERE stock_code='^KS11' AND close>0
            ORDER BY date
        """).fetchall()
    finally:
        conn.close()
    k_dates = [r[0] for r in kospi_rows]
    k_prices = [float(r[1]) for r in kospi_rows]
    k_idx = {d: idx for idx, d in enumerate(k_dates)}

    if len(k_prices) < 127:
        logger.warning("run_backtest_minervini_trend_template: KOSPI(^KS11) 데이터가 "
                        f"{len(k_prices)}건뿐이라 RS 계산 불가 - 전량 매수 신호 없음으로 진행")

    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V-MINERVINI', signal_fn=_make_is_buy_minervini(k_idx, k_prices),
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"Minervini Trend Template {start_date[:7]}~{end_date[:7]}",
        # 2026-09-20 발견(실사용 중 재현): run_id를 항상 새로 만들어 넘기면
        # _run_generic_backtest이 "이미 존재하는 run_id로 재실행"으로 오인해
        # UPDATE만 시도하고(대상 행이 없으니 조용히 0행 영향) INSERT를 안 해서,
        # 백테스트 자체는 성공해도 backtest_runs에 결과가 전혀 기록 안 됐다
        # (peg.py 등 다른 전략은 run_id=None을 그대로 넘겨 매번 새 INSERT가
        # 되게 하는데, 이 파일만 실수로 항상 새 UUID를 채워 넣고 있었음).
        run_id=run_id,
        stop_loss=-0.12, take_profit=0.30,
        mktcap_min=500,
        max_new_per_month=10,
        use_market_filter=True,   # 원본 자체가 이미 RS/Stage2로 강세장 종목만 거르므로 시장필터 병행
        strategy_key='minervini',
    )
