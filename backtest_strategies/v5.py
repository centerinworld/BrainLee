"""
v5.py -- run_backtest_v5()
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
    _get_financial_as_of,
    _ma,
    _run_generic_backtest,
    logger,
    sqlite3,
)

def _is_buy_v5(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V4 수급모멘텀 (Supply-Led Momentum):
    교훈: 52W 범위 추가가 회복장·최근 성과를 크게 훼손.
    MA정배열(MA20>MA60>MA120)이 이미 상승 맥락을 충분히 확인함.
    52W 필터는 중복 조건으로 오히려 기회를 차단.
      [S] 기관+외국인 5일 동반 순매수 (AND — 둘 다 필요)
      [T] MA20 > MA60 > MA120 정배열 (이미 상승 확인)
      [Q] 영업이익 > 0
    """
    if i < sim_start_i or i < 120:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    # [S] 수급: 기관 AND 외국인 5일 동반 순매수
    if i < 5:
        return False
    inst5 = sum(inst_net[i-4:i+1])
    frn5  = sum(frn_net[i-4:i+1])
    if inst5 <= 0 or frn5 <= 0:
        return False

    # [T] 추세: MA20 > MA60 > MA120 정배열
    ma20  = _ma(prices[max(0, i-19):i+1], 20)
    ma60  = _ma(prices[max(0, i-59):i+1], 60)
    ma120 = _ma(prices[max(0, i-119):i+1], 120)
    if not (ma20 and ma60 and ma120):
        return False
    if not (curr > ma20 > ma60 > ma120):
        return False

    # [Q] 실적: 영업이익 > 0
    fin = _get_financial_as_of(fin_rows, dates[i])
    if fin is None or not fin[3] or fin[3] <= 0:
        return False

    return True




def run_backtest_v5(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    """V5 수급 주도 모멘텀 (기관+외국인 5일 동반 순매수 + MA정배열)"""
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V5', signal_fn=_is_buy_v5,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V5 수급모멘텀 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=1000,    # 1000억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=True,
        strategy_key='v5',
        sell_signal_fn=_sell_signal_v4,  # 수급모멘텀: 동반순매수 해소 + 삼중정배열 붕괴 시 매도
    )




def _sell_signal_v4(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V4 수급모멘텀 데이터 기반 매도 — 손실권에서 진입조건(수급) 역전 시 조기 탈출
    원칙: 이익권은 trail stop + MA60붕괴(기존 공통로직)로 처리.
    이 함수는 손실권(-7%이하)에서 수급 모멘텀이 완전히 꺼지면
    기존 180일 대기 없이 조기 청산.
    """
    prices = sd['prices']
    curr   = prices[i]
    pct    = (curr - pos['entry_price']) / pos['entry_price']
    hold   = pos.get('hold_days', 0)

    # 손실이 작거나 이익권이면 개입 안 함
    if pct > -0.07 or hold < 20:
        return None

    # 20일 롤링 수급 — 월 단위 확인으로 노이즈 최소화
    inst20 = sum(sd['inst'][max(0, i-19):i+1])
    frn20  = sum(sd['frn'][max(0, i-19):i+1])

    # 손실(-7%이하) + 기관 AND 외인 20일 모두 순매도 → 진입조건 완전 소멸
    if inst20 < 0 and frn20 < 0:
        ma20 = _ma(prices[max(0, i-19):i+1], 20)
        ma60 = _ma(prices[max(0, i-59):i+1], 60)
        if ma20 and ma60 and ma20 < ma60 * 0.97:
            return "손실+수급소멸(V4)"

    return None




