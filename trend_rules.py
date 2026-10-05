"""추세추종 공통 판정 — 모멘텀Easy·피크Easy 매도 규칙 기준(2026-10-05, 사용자 지시).

국내 종목 차트시그널 종합 판정과 계좌현황 추세추종/매도시그널이 같은 규칙을 쓴다.
규칙 출처(백테스트 채택값, 실제 스탁이지 편출내역으로 교정):
  backtest_strategies/se_momentum.py (모멘텀Easy): 손절 -8% / 이익 +5% 이후 고점 대비 -20% 추적손절 / MA5 < MA20×0.96 이탈
  backtest_strategies/peak_easy.py   (피크Easy)  : 손절 -8% / 이익 +5% 이후 고점 대비 -25% 추적손절 / MA20 < MA60 역전
원칙: 추세가 살아 있는 동안에는 밸류에이션(PER·PBR·ROE)만으로 매도하지 않는다 — 매도는 추세 이탈·추적손절·손절에서만.
"""

STOP = -0.08
TRAIL_MOMENTUM = -0.20
TRAIL_PEAK = -0.25
TRAIL_ARM = 0.05
MA_EXIT_BUFFER = 0.96


def _ma(closes_asc, n, end=None):
    end = len(closes_asc) if end is None else end
    if end < n:
        return None
    return sum(closes_asc[end - n:end]) / n


def assess_trend(closes_asc):
    """closes_asc: 오래된→최근 종가. 반환 dict(regime, label, reason, ma5, ma20, ma60, momentum_exit, peak_exit).

    regime:
      up        상승 추세 — MA20 ≥ MA60, 현재가 ≥ MA20, MA5 ≥ MA20×0.96
      pullback  상승 추세 속 조정 — MA20 ≥ MA60 이지만 현재가 < MA20 (MA5는 아직 MA20×0.96 위)
      weakening 추세 약화 — MA20 ≥ MA60 이지만 MA5 < MA20×0.96 (모멘텀Easy 매도 조건)
      down      비추세/하락 — MA20 < MA60 (피크Easy 매도 조건). 현재가 > MA20 이면 rebound
    """
    c = [float(x) for x in closes_asc if x]
    if len(c) < 60:
        return {"regime": "unknown", "label": "판단 불가", "reason": "가격 60일 미만"}
    cur = c[-1]
    ma5, ma20, ma60 = _ma(c, 5), _ma(c, 20), _ma(c, 60)
    momentum_exit = ma5 < ma20 * MA_EXIT_BUFFER
    peak_exit = ma20 < ma60
    if not peak_exit:
        if momentum_exit:
            regime, label = "weakening", "추세 약화"
            reason = f"MA5 {ma5:,.0f} < MA20×0.96 {ma20 * MA_EXIT_BUFFER:,.0f} — 모멘텀Easy 매도 조건"
        elif cur < ma20:
            regime, label = "pullback", "상승 추세 속 조정"
            reason = f"MA20 ≥ MA60 유지, 현재가가 MA20 {ma20:,.0f} 아래 — 추세 유효(매도 조건 아님)"
        else:
            regime, label = "up", "상승 추세"
            reason = f"현재가 ≥ MA20 {ma20:,.0f} ≥ MA60 {ma60:,.0f} — 매도 조건 없음"
    else:
        if cur > ma20:
            regime, label = "rebound", "하락 추세 속 반등 시도"
            reason = f"MA20 {ma20:,.0f} < MA60 {ma60:,.0f}(피크Easy 매도 조건 상태), 현재가는 MA20 위 — 골든크로스 전까지 추세 전환 미확인"
        else:
            regime, label = "down", "하락 추세"
            reason = f"MA20 {ma20:,.0f} < MA60 {ma60:,.0f} — 피크Easy 매도 조건" + (", MA5<MA20×0.96 — 모멘텀Easy 매도 조건" if momentum_exit else "")
    return {"regime": regime, "label": label, "reason": reason, "ma5": ma5, "ma20": ma20, "ma60": ma60,
            "momentum_exit": momentum_exit, "peak_exit": peak_exit}


def trend_start_index(closes_asc, lookback=252):
    """현재 상승 추세가 시작된 위치(마지막 MA20>MA60 골든크로스). 없으면 lookback 시작."""
    c = [float(x) for x in closes_asc]
    n = len(c)
    lo = max(60, n - lookback)
    for end in range(n, lo, -1):
        a20, a60, p20, p60 = _ma(c, 20, end), _ma(c, 60, end), _ma(c, 20, end - 1), _ma(c, 60, end - 1)
        if None in (a20, a60, p20, p60):
            break
        if p20 <= p60 and a20 > a60:
            return end - 1
    return max(0, n - lookback)


def exit_signal(closes_asc, dates_asc, avg_price, bought_at=None, regime=None):
    """가격 기반 매도 규칙(손절·추적손절). regime(assess_trend 결과)이 up/pullback 이면 손절 기준을 넘었어도
    '추세 회복 중 — 추세 이탈 시 매도'(watch)로 낮춘다: 이미 -8%를 넘긴 채 보유 중인 종목을 상승 추세 한가운데서
    손절하라고 하지 않는다(사용자 원칙 '진짜 추세가 전환해야 매도'). 반환 dict(status sell|watch|hold, reason, peak_price, peak_date, drawdown_from_peak_pct, peak_basis)."""
    c = [float(x) for x in closes_asc]
    cur = c[-1]
    pnl = cur / avg_price - 1 if avg_price else 0.0
    if bought_at:
        start = next((k for k, d in enumerate(dates_asc) if str(d)[:10] >= str(bought_at)[:10]), len(c) - 1)
        basis = "매수일 이후 고점"
    else:
        start = trend_start_index(c)
        basis = "매수일 미기록 — 현재 상승 추세 시작(MA20>MA60 교차) 이후 고점"
    seg = c[start:] or [cur]
    k = max(range(len(seg)), key=lambda j: seg[j])
    peak, peak_date = seg[k], str(dates_asc[start + k])[:10]
    dd = cur / peak - 1 if peak else 0.0
    out = {"status": "hold", "reason": "", "peak_price": peak, "peak_date": peak_date,
           "drawdown_from_peak_pct": round(dd * 100, 2), "peak_basis": basis, "pnl_pct": round(pnl * 100, 2)}
    if pnl <= STOP and regime in ("up", "pullback"):
        out.update(status="watch", reason=f"손절 기준(-8%) 초과 상태({pnl * 100:.1f}%)지만 상승 추세 회복 중 — 추세 이탈(MA5<MA20×0.96 또는 MA20<MA60) 시 매도")
    elif pnl <= STOP:
        out.update(status="sell", reason=f"손절 기준 초과(매수가 대비 {pnl * 100:.1f}% ≤ -8%, 모멘텀·피크Easy 공통)")
    elif regime in ("weakening", "down", "rebound"):
        # 상승 추세가 아니면 '추세 시작 이후 고점'이 없어 추적손절이 의미 없다(예전엔 5개월 전 고점 대비 -36%로 매도 표시).
        out.update(status="watch", reason="상승 추세 아님 — 추적손절 대상 구간 없음, 매도 여부는 추세추종 신호(추세 이탈 조건)로 판단")
    elif pnl > TRAIL_ARM and dd <= TRAIL_MOMENTUM:
        out.update(status="sell", reason=f"추적손절 발동(고점 {peak_date} {peak:,.0f}원 대비 {dd * 100:.1f}% ≤ -20%, 모멘텀Easy"
                                         + (" · 피크Easy -25%도 도달)" if dd <= TRAIL_PEAK else ")"))
    elif pnl > TRAIL_ARM and dd <= -0.10:
        out.update(status="watch", reason=f"고점 대비 {dd * 100:.1f}% — 추적손절(-20%) 접근")
    else:
        out["reason"] = (f"추적손절 대기(이익 {pnl * 100:.1f}%, 고점 대비 {dd * 100:.1f}%)" if pnl > TRAIL_ARM
                         else f"손절 기준(-8%) 안쪽(매수가 대비 {pnl * 100:.1f}%)")
    return out


def action_factors(code, dates_asc, closes_asc, conn):
    """원주가 시계열의 기업행위(무상증자·분할·병합) 보정 계수 목록 [(event_date, factor)].

    price_history는 원주가(2026-10-04 정본)라 무상증자일에 가짜 급락이 생겨 이평선·고점이 왜곡된다(494120 큐리오시스 2026-07-15 2:1).
    corporate_action_events 중 factor_confirmed 만 쓰고, **그날 실제 가격 비율이 계수와 맞을 때만** 적용한다
    (같은 이벤트가 신주 상장일로 한 번 더 등록된 경우 — 494120 2026-08-21 — 를 걸러냄). 허용: 실제 비율이 계수의 0.6~1.4배."""
    try:
        evs = conn.execute("SELECT event_date, backward_price_factor FROM corporate_action_events WHERE stock_code=? "
                           "AND adjustment_status='factor_confirmed' AND backward_price_factor IS NOT NULL AND backward_price_factor>0 "
                           "AND backward_price_factor<>1 ORDER BY event_date", (code,)).fetchall()
    except Exception:
        return []
    idx = {str(d)[:10]: k for k, d in enumerate(dates_asc)}
    out = []
    for ev_date, f in evs:
        ev = str(ev_date)[:10]
        k = idx.get(ev)
        if k is None or k == 0:
            continue
        actual = closes_asc[k] / closes_asc[k - 1] if closes_asc[k - 1] else None
        if actual and 0.6 * float(f) <= actual <= 1.4 * float(f):
            out.append((ev, float(f)))
    return out


def adjust_series(dates_asc, series_asc, factors):
    """event_date 이전 값에 계수를 곱한 수정 시계열."""
    s = [float(x) if x is not None else None for x in series_asc]
    for ev, f in factors:
        for k, d in enumerate(dates_asc):
            if str(d)[:10] >= ev:
                break
            if s[k] is not None:
                s[k] *= f
    return s
