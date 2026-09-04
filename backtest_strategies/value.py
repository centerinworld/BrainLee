"""
value.py -- run_backtest_value()
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
    _release_date,
    _run_generic_backtest,
    logger,
    sqlite3,
)

def _build_peg_map(conn) -> Dict[str, list]:
    """종목별 (avail_date, -PEG) 정렬 리스트. 부호를 반전해 '클수록 좋음'으로 통일
    (entry_bonus_fn과 동일 관례). EPS 역성장(적자전환/역성장) 연도는 PEG 정의상 제외."""
    rows = conn.execute("""
        SELECT stock_code, year, eps FROM financial_data
        WHERE is_annual=1 AND report_type='CFS' AND eps IS NOT NULL AND eps>0
        ORDER BY stock_code, year
    """).fetchall()
    by_code: Dict[str, list] = {}
    for sc, y, eps in rows:
        by_code.setdefault(sc, []).append((y, eps))

    result: Dict[str, list] = {}
    for code, yrs in by_code.items():
        yrs.sort()
        events = []
        for i in range(1, len(yrs)):
            y, eps = yrs[i]
            py, p_eps = yrs[i - 1]
            if py != y - 1 or p_eps <= 0:
                continue
            growth_pct = (eps - p_eps) / p_eps * 100.0
            if growth_pct <= 0:
                continue
            avail = _release_date(y, 4, True, code)
            price_row = conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND date>=? AND close>0 ORDER BY date ASC LIMIT 1",
                (code, avail)
            ).fetchone()
            if not price_row:
                continue
            per = price_row[0] / eps
            if per <= 0 or per > 200:
                continue
            peg = per / growth_pct
            events.append((avail, -peg))
        if events:
            events.sort()
            result[code] = events
    return result




def _make_peg_bonus_fn(peg_map: Dict[str, list], weight: float = 8.0):
    """entry_bonus_fn(code, day)->float 팩토리. -PEG × weight — PEG가 없는(성장주가
    아니거나 데이터 없는) 종목은 0(기존 동작과 동일, 페널티 아님)."""
    import bisect as _bisect

    def _fn(code: str, day: str) -> float:
        events = peg_map.get(code)
        if not events:
            return 0.0
        dates = [e[0] for e in events]
        idx = _bisect.bisect_right(dates, day) - 1
        if idx < 0:
            return 0.0
        return events[idx][1] * weight

    return _fn


# ══════════════════════════════════════════════════════════════
#  기술 지표 헬퍼
# ══════════════════════════════════════════════════════════════


def _is_buy_value(
    i: int, sim_start_i: int,
    dates: list, prices: list, volumes: list,
    frn_net: list, inst_net: list, fin_rows: list,
) -> bool:
    """
    V2 가치매수 — Graham Value + Smart Money AND:
    교훈: 52W 범위 필터 + 느슨한 가치 조건이 역효과 (-5.5%).
    핵심 알파: 깊은 저평가(Graham) + 기관&외국인 동반 진입(AND).
    수정: Graham 조건 복원 + 수급 합산→AND 강화 + MA60 ±5% 유지.
      [A] Graham: PBR<0.7 AND PER<10 OR IV 25%+ 할인 + 영업이익>0
      [B] MA60 대비 5% 이상 하락 중이면 제외 (추세 붕괴 방지)
      [C] 수급 AND: 기관 5일 > 0 AND 외국인 5일 > 0 (합산→AND 강화)
    """
    if i < sim_start_i or i < 60:
        return False
    curr = prices[i]
    if curr <= 0:
        return False

    # [B] MA60 -5% 이상 하락 제외
    ma60 = _ma(prices[max(0, i-59):i+1], 60)
    if ma60 and curr < ma60 * 0.95:
        return False

    fin = _get_financial_as_of(fin_rows, dates[i])
    if fin is None:
        return False
    _y, _q, _rev, op, eps, bps, _eq, _ni, _roe, _ann, *_ = fin
    if not op or op <= 0:
        return False

    # [A] Graham 가치 조건 복원
    graham_ok = False
    if eps and eps > 0 and bps and bps > 0:
        import math as _m2
        graham_iv = _m2.sqrt(22.5 * eps * bps)
        if graham_iv > 0 and curr <= graham_iv * 0.75:
            graham_ok = True
        per = curr / eps
        pbr = curr / bps
        if 0 < per < 10 and pbr < 0.7:
            graham_ok = True
    if not graham_ok:
        return False

    # [C] 수급 AND (합산→AND, 더 엄격한 스마트머니 확인)
    if i >= 5:
        inst5 = sum(inst_net[max(0, i-4):i+1])
        frn5  = sum(frn_net[max(0, i-4):i+1])
        if inst5 <= 0 or frn5 <= 0:  # 둘 다 양수여야 함
            return False

    return True




def run_backtest_value(start_date: str, end_date: str,
                       per_stock: float = 10_000_000,
                       max_positions: int = 10,
                       chart_confluence: bool = False,
                       use_peg_bonus: bool = False,  # 2026-09-01 실험: PEG(Lynch) 진입우선순위 보너스 opt-in (walk-forward 검증 전까지 기본 비활성)
                    run_name: str = None, run_id: str = None,
                    data_asof_ts: str = None) -> str:
    """V1 가치매수 (Graham 내재가치 25%+ 할인 OR PBR<0.7 AND PER<10) — 하락장 무관 매수

    data_asof_ts: 2026-09-04 신규. 재무데이터 실시간 보정 잡과의 경쟁으로 인한
    회귀검증 비재현성 수정 — _run_generic_backtest 참조."""
    entry_bonus_fn = None
    if use_peg_bonus:
        _pconn = sqlite3.connect(DB_PATH, timeout=60)
        entry_bonus_fn = _make_peg_bonus_fn(_build_peg_map(_pconn))
        _pconn.close()
    return _run_generic_backtest(
        chart_confluence=chart_confluence,
        version='V1_VALUE', signal_fn=_is_buy_value,
        start_date=start_date, end_date=end_date,
        per_stock=per_stock, max_positions=max_positions,
        run_name=run_name or f"V1 가치매수 {start_date[:7]}~{end_date[:7]}",
        run_id=run_id,
        stop_loss=-0.12, take_profit=0.25,
        mktcap_min=500,     # 500억+ (억원 단위)
        max_new_per_month=10,
        use_market_filter=False,      # 하락장에서도 저평가 종목 매수
        strategy_key='v1_value',
        # sell_signal_fn=_sell_signal_v2,  # 스마트머니이탈 테스트: 효과 없음(5.1%→5.0%), 기본값 사용
        entry_bonus_fn=entry_bonus_fn,
        data_asof_ts=data_asof_ts,
    )




def _sell_signal_v2(i: int, sd: dict, pos: dict) -> Optional[str]:
    """V2 가치매수 — 스마트머니 이탈: 매수 조건(기관&외인 AND 양수) 역전 시 청산
    기존 방식(손실권 -8% + MA120 + MA60): 세 조건 동시 충족이 거의 없어 사실상 미작동.
    새 접근: 기관10일+외인10일 모두 음수 → 매수 진입 근거(스마트머니 동반 매수)가 소멸.
    이익/손실 무관 발동. 20일 이상 보유 후 (수급 노이즈 구간 제외).
    """
    held = pos.get('hold_days', 0)
    if held < 20:
        return None
    inst10 = sum(sd['inst'][max(0, i-9):i+1])
    frn10  = sum(sd['frn'][max(0, i-9):i+1])
    # 매수 조건(기관>0 AND 외인>0)의 역전 → 기관&외인 모두 이탈
    if inst10 < 0 and frn10 < 0:
        return "스마트머니이탈(V2)"
    return None




