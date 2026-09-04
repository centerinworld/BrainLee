"""
hidden_rev.py -- run_backtest_hidden_rev()
Split out of backtest.py on 2026-09-03. Pure relocation, no logic changed.
"""
import json
import uuid
import math
import re
import logging
import bisect
from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple

from backtest_common import (
    DB_PATH,
    _run_generic_backtest,
    logger,
    sqlite3,
)

def _is_buy_hidden_rev(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    52주 강세 돌파 (Breakout Momentum) — 실증 기반:
    [A] 52주 가격 범위 상위 65%+ (강한 추세에 있는 종목)
    [B] MA60 > MA120 × 1.02 (중기 추세 > 장기 추세, 정배열)
    [C] 현재가 MA60 대비 +3%~+25% (추세 위에 올라선 상태)
    [D] 거래량 최근 5일 > 20일 평균 × 1.3 (상승에 거래량 동반)
    [E] 단기 모멘텀: 현재가 > 10일 전 가격 (방향 확인)

    이론 아닌 실증: 2020~2025 982,889 시점 분석에서
    이 조합이 winRate 18.5%, 리프트 1.64x로 최고 성과 확인
    """
    if i < sim_start_i or i < 120:
        return False
    curr = prices[i]

    # [B] MA 정배열 (먼저 체크, 빠른 탈락)
    ma60  = sum(prices[i-59:i+1]) / 60
    ma120 = sum(prices[i-119:i+1]) / 120
    if not (ma60 > ma120 * 1.02):
        return False

    # [C] 현재가 MA60 위 3%~25%
    pos_ma60 = (curr - ma60) / ma60 * 100
    if not (3 <= pos_ma60 <= 25):
        return False

    # [A] 52주 범위 상위 65%+
    low_52w  = min(prices[max(0, i-252):i+1])
    high_52w = max(prices[max(0, i-252):i+1])
    if high_52w <= low_52w:
        return False
    pos_52w = (curr - low_52w) / (high_52w - low_52w) * 100
    if pos_52w < 65:
        return False

    # [D] 거래량 확대
    if i < 20:
        return False
    v20 = sum(volumes[i-20:i]) / 20
    v5  = sum(volumes[i-5:i])  / 5
    if not (v20 > 0 and v5 > v20 * 1.3):
        return False

    # [E] 단기 모멘텀 양수 (10일 전 대비 상승)
    if i >= 10:
        if curr <= prices[i-10]:
            return False

    return True


# ══════════════════════════════════════════════════════════════
#  V11 매수 시그널 — 흑자전환 모멘텀 (이수페타시스·엘앤에프 유형)
# ══════════════════════════════════════════════════════════════


def run_backtest_hidden_rev(start_date: str, end_date: str,
                           per_stock: float = 10_000_000,
                           max_positions: int = 10,
                           run_name: str = None, run_id: str = None,
                           exit_mode: str = 'trail20',
                           chart_confluence: bool = False,
                           sell_signal_fn=None) -> str:
    """
    52주 강세 돌파 모멘텀 (Breakout Momentum)
    실증 근거: 2020~2025 982,889 샘플 분석 — 52W 고점 근처 + MA위 + 거래량 조합 1.64x 리프트
    exit_mode: 'trail20' = Trail-20%(고점-20% 추적손절, 실증 최적)
               'tp30'    = TP+30% + Trail-10%(기존 방식)
    """
    if exit_mode == 'trail20':
        # Trail-20%: 고점대비 -20% 추적손절, 고정 TP 없음(99999)
        _tp = 99999.0
        _trail = -0.20
        _name = f"52W돌파Trail20 {start_date[:7]}~{end_date[:7]}"
    else:
        _tp = 0.30
        _trail = -0.10
        _name = f"52W돌파TP30 {start_date[:7]}~{end_date[:7]}"
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='VBR', signal_fn=_is_buy_hidden_rev,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or _name,
        run_id=run_id,
        stop_loss=-0.08,    # 추세 추종 — 빠른 손절
        take_profit=_tp,
        trail_stop=_trail,
        mktcap_min=1000,    # 1000억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=True,   # 추세 전략 — 시장 필터 적용
        strategy_key='vbr',
        sell_signal_fn=sell_signal_fn,
    )




