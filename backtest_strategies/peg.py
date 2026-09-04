"""
peg.py -- run_backtest_peg()
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
    _ma,
    _run_generic_backtest,
    logger,
    sqlite3,
)

def _is_buy_peg_standalone(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """V-PEG 독립전략 (2026-09-01 신규, Peter Lynch GARP) — 기존 전략에 보너스로
    얹었을 때는 실행레벨에서 악화됐으나(2026-09-01 ledger), 독립 매수조건으로는
    다른 결과가 나올 수 있어 재시도. 라벨검증(전체시장): 저PEG그룹 학습20.9%/검증20.9%
    vs 고PEG그룹 학습0.4%/검증7.6% — 두 기간 모두 저PEG 우위 확인.
      [A] PEG < 1.0 (Lynch 경험칙 — 성장률 대비 저평가)
      [B] EPS YoY 성장률 > 10% (트리비얼한 소폭성장 배제)
      [C] 추세 붕괴 아님: 현재가 > MA60 (낙주매수 방지, 순수 저PEG만으로는 하락추세 종목도 걸릴 수 있음)
    """
    if i < sim_start_i or i < 60:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    annuals = [r for r in fin_rows if r[9] == 1 and r[10] and r[10] <= dates[i]]
    if len(annuals) < 2:
        return False
    annuals.sort(key=lambda r: r[0], reverse=True)
    cur, prev = annuals[0], annuals[1]
    if cur[0] - 1 != prev[0]:
        return False
    eps_cur, eps_prev = cur[4], prev[4]
    if not eps_cur or eps_cur <= 0 or not eps_prev or eps_prev <= 0:
        return False

    growth_pct = (eps_cur - eps_prev) / eps_prev * 100.0
    if growth_pct <= 10:
        return False

    per = curr / eps_cur
    if per <= 0 or per > 100:
        return False
    peg = per / growth_pct
    if peg <= 0 or peg >= 1.0:
        return False

    ma60 = _ma(prices[max(0, i - 59):i + 1], 60)
    if not ma60 or curr <= ma60:
        return False

    return True




def run_backtest_peg(start_date: str, end_date: str,
                     per_stock: float = 10_000_000,
                     max_positions: int = 10,
                     chart_confluence: bool = False,
                     run_name: str = None, run_id: str = None) -> str:
    """V-PEG 독립전략 (2026-09-01 신규, Peter Lynch GARP — PEG<1.0 + EPS성장10%+ + MA60위)
    2026-09-01 entry_bonus 결합 실험(V1가치매수 위에 얹음, -9.3pp 악화)과 별개로,
    독립 매수조건으로 재시도."""
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V-PEG', signal_fn=_is_buy_peg_standalone,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V-PEG독립 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.12, take_profit=0.30,
        mktcap_min=500,     # 500억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=False,   # 가치/성장 종목은 하락장에서도 저평가 매력 존재
        strategy_key='v_peg',
    )




