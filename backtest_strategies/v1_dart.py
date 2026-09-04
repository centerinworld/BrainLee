"""
v1_dart.py -- run_backtest_v1_dart()
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
    _run_generic_backtest_with_sc,
    logger,
    sqlite3,
)

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
        mktcap_min=1000,    # 1000억+ (억원 단위)
    )


# ══════════════════════════════════════════════════════════════
#  V10 매수 시그널 — 이익 폭발 (에스티팜·에이피알·삼양식품 유형)
# ══════════════════════════════════════════════════════════════


