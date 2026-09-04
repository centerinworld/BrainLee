"""
v11.py -- run_backtest_v11()
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
    _is_buy_v11,
    _ma,
    _run_generic_backtest,
    logger,
    sqlite3,
)

def run_backtest_v11(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V11', signal_fn=_is_buy_v11,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V7 이익가속YoY {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.10, take_profit=0.35,
        mktcap_min=300,                       # 300억+ (억원 단위)
        max_new_per_month=8,
        use_market_filter=True,               # ★ 하락장 진입 차단
        strategy_key='v11',
        # sell_signal_fn=_sell_signal_v7,  # 가속스톨 테스트: +17.0%→+7.4% 하락 — 기본값 사용
    )




def _sell_signal_v7(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V7 이익가속 — 가속 스톨: 45일 후 신고점 없고 MA20 붕괴 시 가속 소진 청산
    기존 방식(손실권 -7% + MA+수급): 이익가속 종목은 손실권 진입 드물어 사실상 미작동.
    새 접근: 45일 보유 → 최근 25일 최고가가 진입가+8% 이내(신고가 갱신 없음) + MA20 하향.
    이익가속 종목은 매수 후 빠르게 상승해야 함 — 45일 후도 8% 이하면 가속 스토리 소진.
    """
    held  = pos.get('hold_days', 0)
    if held < 45:
        return None
    prices = sd['prices']
    entry  = pos['entry_price']
    curr   = prices[i]

    # 최근 25일 최고가가 진입가+8% 이내 → 가속 진행 중 신고가 부재
    recent_high = max(prices[max(0, i-24):i+1])
    if recent_high < entry * 1.08:
        ma20 = _ma(prices[max(0, i-19):i+1], 20)
        if ma20 and curr < ma20:
            return "가속스톨(V7)"
    return None




