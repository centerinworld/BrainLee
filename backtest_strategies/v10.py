"""
v10.py -- run_backtest_v10()
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
    _is_buy_v10,
    _run_generic_backtest,
    logger,
    sqlite3,
)

def run_backtest_v10(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V10', signal_fn=_is_buy_v10,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V10 이익폭발 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=500,     # 500억+ (억원 단위)
        max_new_per_month=999,       # 펀더멘탈 전략 — 월 한도 없음 (자연 필터)
        strategy_key='v10',
        # sell_signal_fn=_sell_signal_v6,  # 피크반납 테스트: avg5 동일(7.2%), 기간별 분산 — 기본값 사용
    )




def _sell_signal_v6(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V6 이익폭발 — 피크 반납: 이익 스토리 소진 신호(피크 게인 40%+ 반납)
    기존 방식(손실권 -6% + RSI<30): 이익폭발 종목은 손실권에 잘 안 들어와 사실상 미작동.
    새 접근: 25%+ 피크 게인을 달성한 후 그 게인의 40%를 반납 → 이익 스토리 소진.
    trail stop(-10%)보다 먼저 발동해 이익권에서 조기 청산.
    주: peak_gain=50%일 때 trail=-10% 발동(curr=peak×0.9), giveback=40% 발동(curr=peak×0.6+entry×0.4)
    """
    held  = pos.get('hold_days', 0)
    if held < 25:
        return None
    entry = pos['entry_price']
    curr  = sd['prices'][i]
    peak  = pos.get('peak_price', entry)

    peak_gain = (peak - entry) / entry
    if peak_gain < 0.25:   # 25% 이상 피크 달성 전이면 아직 이익폭발 미확인
        return None

    curr_gain = (curr - entry) / entry
    giveback  = (peak_gain - curr_gain) / peak_gain if peak_gain > 0 else 0
    if giveback > 0.40:
        return f"피크반납{giveback*100:.0f}%(V6)"
    return None




