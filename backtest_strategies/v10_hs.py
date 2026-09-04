"""
v10_hs.py -- run_backtest_v10_hs()
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
    _date_to_ym,
    _get_export_yoy,
    _is_buy_v10,
    _load_trade_signals,
    _run_generic_backtest_with_sc,
    logger,
    sqlite3,
)

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
        mktcap_min=500,     # 500억+ (억원 단위)
    )




