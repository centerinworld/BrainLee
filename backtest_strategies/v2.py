"""
v2.py -- run_backtest_v2()
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
    _52w_pct,
    _get_financial_as_of,
    _ma,
    _release_date,
    _run_generic_backtest,
    logger,
    sqlite3,
)

def _build_piotroski_map(conn) -> Dict[str, list]:
    """종목별 (avail_date, f_score) 정렬 리스트. 연간 CFS 공시 기준, YoY 비교 필요해
    첫 disclosure 연도는 제외."""
    rows = conn.execute("""
        SELECT stock_code, year, net_income, total_assets, total_equity, total_liabilities,
               revenue, operating_profit
        FROM financial_data
        WHERE is_annual=1 AND report_type='CFS'
          AND net_income IS NOT NULL AND total_assets>0 AND revenue>0
        ORDER BY stock_code, year
    """).fetchall()
    by_code: Dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r[0], []).append(r)

    cf_map = {}
    for r in conn.execute("""
        SELECT stock_code, year, operating_cf FROM cash_flow_data
        WHERE is_annual=1 AND report_type='CFS' AND operating_cf IS NOT NULL
    """).fetchall():
        cf_map[(r[0], r[1])] = r[2]

    result: Dict[str, list] = {}
    for code, yrs in by_code.items():
        yrs.sort(key=lambda x: x[1])
        events = []
        for i in range(1, len(yrs)):
            cur = yrs[i]; prev = yrs[i - 1]
            y = cur[1]
            ni, ta, te, tl, rev, op = cur[2], cur[3], cur[4], cur[5], cur[6], cur[7]
            p_ni, p_ta, p_te, p_tl, p_rev, p_op = prev[2], prev[3], prev[4], prev[5], prev[6], prev[7]
            if not (ta and p_ta and rev and p_rev and te and p_te):
                continue
            roa = ni / ta
            p_roa = p_ni / p_ta if p_ta else None
            ocf = cf_map.get((code, y))
            score = 0
            score += 1 if roa > 0 else 0
            if p_roa is not None:
                score += 1 if roa > p_roa else 0
            if ocf is not None:
                score += 1 if ocf > 0 else 0
                score += 1 if ocf > ni else 0
            if tl and p_tl and te and p_te:
                lev, p_lev = tl / te, p_tl / p_te
                score += 1 if lev < p_lev else 0
            if op and p_op:
                gm, p_gm = op / rev, p_op / p_rev
                score += 1 if gm > p_gm else 0
            turn, p_turn = rev / ta, p_rev / p_ta
            score += 1 if turn > p_turn else 0
            avail = _release_date(y, 4, True, code)
            events.append((avail, score))
        if events:
            events.sort()
            result[code] = events
    return result




def _make_piotroski_bonus_fn(piotroski_map: Dict[str, list], weight: float = 3.0):
    """entry_bonus_fn(code, day)->float 팩토리. F-Score(0~7) × weight를 그대로 진입
    우선순위 보너스로 사용 — score가 없는 종목은 0(기존 동작과 동일, 페널티 아님)."""
    import bisect as _bisect

    def _fn(code: str, day: str) -> float:
        events = piotroski_map.get(code)
        if not events:
            return 0.0
        dates = [e[0] for e in events]
        idx = _bisect.bisect_right(dates, day) - 1
        if idx < 0:
            return 0.0
        return events[idx][1] * weight

    return _fn


# ══════════════════════════════════════════════════════════════
#  PEG Ratio (2026-09-01 신규) — Peter Lynch("One Up on Wall Street")의
#  GARP(Growth At a Reasonable Price) 지표: PER ÷ EPS성장률(%). 낮을수록(성장 대비
#  저평가) 매력적. Walk-forward 라벨검증 통과: 학습기 저PEG그룹 +20.9%(lift2.19x) vs
#  고PEG그룹 +0.43%(lift0.04x), 검증기도 저PEG +20.9%(lift1.25x) vs 고PEG +7.6%
#  (lift0.46x) — 두 기간 모두 저PEG가 고PEG를 뚜렷이 상회, 방향 재현성 확인.
# ══════════════════════════════════════════════════════════════


def _is_buy_v2(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V3 재무우량 — 실증 보강:
    실증 문제: 재무지표(영업이익률/ROE) 단독은 신호 없음 (매출증가 0.94x와 유사)
    수정: 재무 우량 + 52W 범위 위치 확인 (추세 맥락 필수)
      [0] 52주 범위 55%+ (실증 최강 필터 추가)
      [A] 수익성 스코어 ≥ 2점 (이전 3점 → 2점, 52W필터가 더 중요)
      [B] 추세 AND 수급 (기존 유지)
    """
    if i < sim_start_i or i < 60:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    # [0] 52주 범위 55%+ (실증 핵심 필터)
    if i >= 120 and _52w_pct(prices, i) < 55:
        return False

    fin = _get_financial_as_of(fin_rows, dates[i])
    if fin is None:
        return False
    _y, _q, rev, op, eps, bps, eq, ni, roe, _ann, *_ = fin

    if not op or op <= 0:
        return False

    score = 0
    if rev and rev > 0:
        op_margin = op / rev
        if op_margin >= 0.05:
            score += 1
        if op_margin >= 0.08:
            score += 1
    if roe and roe >= 10:
        score += 1
    if ni and eq and eq > 0 and ni / eq >= 0.05:
        score += 1

    if score < 2:  # 3→2 (52W 필터가 더 중요)
        return False

    ma20 = _ma(prices[max(0, i-19):i+1], 20)
    ma60 = _ma(prices[max(0, i-59):i+1], 60)
    trend_ok = bool(ma20 and ma60 and curr > ma20 > ma60)

    supply_ok = False
    if i >= 5:
        inst5 = sum(inst_net[max(0, i-4):i+1])
        frn5  = sum(frn_net[max(0, i-4):i+1])
        supply_ok = (inst5 + frn5 > 0)

    return trend_ok and supply_ok




def run_backtest_v2(start_date: str, end_date: str,
                    per_stock: float = 10_000_000,
                    max_positions: int = 10,
                    chart_confluence: bool = True,  # 2026-07-18 채택: 3요소 컨플루언스 게이트 — 연속운용 158.5→170.3(+11.8pp)·승률 39.3→41.9 실측 개선. 13전략 중 유일하게 개선된 전략(나머지는 악화로 기본 off)
                    use_piotroski_bonus: bool = False,  # 2026-09-01 실험: Piotroski F-Score 진입우선순위 보너스 opt-in (walk-forward 검증 전까지 기본 비활성)
                    run_name: str = None, run_id: str = None,
                    data_asof_ts: str = None) -> str:
    """V2 재무스크리너 (수익성 스코어 ≥ 3점: 영업이익률/ROE/ROA 복합 점수)

    data_asof_ts: 2026-09-04 신규. 재무데이터 실시간 보정 잡과의 경쟁으로 인한
    회귀검증 비재현성 수정 — _run_generic_backtest 참조."""
    entry_bonus_fn = None
    if use_piotroski_bonus:
        _pconn = sqlite3.connect(DB_PATH, timeout=60)
        entry_bonus_fn = _make_piotroski_bonus_fn(_build_piotroski_map(_pconn))
        _pconn.close()
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V2', signal_fn=_is_buy_v2,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V2 재무스크리너 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.10, take_profit=0.20,
        mktcap_min=1000,    # 1000억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=True,
        strategy_key='v2',
        sell_signal_fn=_sell_signal_v3,  # 재무우량: MA정배열 붕괴 + 수급 이탈 시 매도
        entry_bonus_fn=entry_bonus_fn,
        data_asof_ts=data_asof_ts,
    )




def _sell_signal_v3(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V3 재무우량 데이터 기반 매도 — 손실권 진입조건 역전 시 조기 탈출
    원칙: 이익권은 trail stop + MA60붕괴(기존 공통로직)로 처리.
    이 함수는 명확한 손실권(-7%이하)에서 진입조건(MA정배열+수급)이 역전되면
    기존 180일 대기 없이 조기 청산하는 역할만 수행.
    """
    prices = sd['prices']
    curr   = prices[i]
    pct    = (curr - pos['entry_price']) / pos['entry_price']
    hold   = pos.get('hold_days', 0)

    # 손실이 작거나 이익권이면 개입 안 함 — trail/MA60붕괴에 맡김
    if pct > -0.07 or hold < 25:
        return None

    ma20 = _ma(prices[max(0, i-19):i+1], 20)
    ma60 = _ma(prices[max(0, i-59):i+1], 60)
    if not ma20 or not ma60:
        return None

    # 손실(-7%이하) + MA정배열 붕괴 + 수급 이탈 삼중 확인
    # → 진입조건(MA20>MA60 + 기관+외인>0)이 완전히 소멸: 회복 가능성 없는 상태
    if ma20 < ma60 * 0.96:
        inst15 = sum(sd['inst'][max(0, i-14):i+1])
        frn15  = sum(sd['frn'][max(0, i-14):i+1])
        if inst15 < 0 and frn15 < 0:
            return "손실+MA붕괴+수급이탈(V3)"

    return None




