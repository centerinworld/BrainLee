"""
v1.py -- run_backtest_v1()
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
    _is_buy_v1,
    _run_generic_backtest,
    _sell_signal_v1,
    logger,
    sqlite3,
)

def run_backtest_v1(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    chart_confluence: bool = False,
                    run_name: str = None, run_id: str = None) -> str:
    """V1 트렌드 (미너비니 추세추종 기본) — 월 10개 한도"""
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V1', signal_fn=_is_buy_v1,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V1 트렌드 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.08, take_profit=0.20,
        mktcap_min=1000,    # 1000억+ (억원 단위)
        max_new_per_month=10,         # ★ 추세추종은 후보 많아서 월 10개 제한
        strategy_key='v_trend',
        # sell_signal_fn=_sell_signal_v1,  # 데스크로스 테스트: MA60붕괴 조건과 중복, 효과 없음(25.9%→25.9%)
    )




