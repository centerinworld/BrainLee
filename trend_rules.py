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
    """매도 기준가 판정(2026-10-05 재설계 — 사용자: "'주의'는 이상하다, 반등 중인데 매도라 하지 말고 구체적으로").

    상태(status) 3가지와 '매도 기준가'(이 가격 아래로 내려가면 규칙상 매도)를 함께 낸다.
      hold     보유 — 매도 기준가 = 다음 중 가장 높은(가까운) 가격
                 · 추세 이탈선  = 20일선 × 0.96 (모멘텀Easy: 5일선이 이 아래로 내려가면 매도)
                 · 추적손절가  = 고점 × 0.80 (이익 +5% 이후, 모멘텀Easy -20%)
                 · 손절가      = 평단 × 0.92 (손실 -8% 이내일 때만 — 이미 넘었으면 추세선만 적용)
      rebound  반등 관찰 — 20일선 < 60일선이지만 현재가가 20일선 위. 매도 기준가 = 최근 20거래일 종가 저점
                 (저점을 다시 깨면 반등 실패 → 매도), 추세 전환 확인 = 20일선이 60일선 위로.
      sell     매도 — 현재가가 매도 기준가 아래, 또는 추세 약화(5일선 < 20일선×0.96)·하락 추세(현재가 < 20일선 < 60일선).
    평단 대비 손실이 -8%를 넘은 종목은 '손절가 이미 하회'를 설명에 쓰되, 판정은 지금부터의 추세로 한다(지나간 손절가로 반등 중 매도 표시 안 함).
    고점 대비 하락률은 추적손절이 작동하는 경우(상승 추세 + 이익 +5%)에만 낸다(큐리오시스 1년 고점 대비 -77% 표시 오류 수정)."""
    c = [float(x) for x in closes_asc]
    cur = c[-1]
    t = assess_trend(c)
    reg = t.get("regime")
    pnl = cur / avg_price - 1 if avg_price else 0.0
    out = {"status": "hold", "label": "보유", "reason": "", "sell_price": None, "lines": {}, "pnl_pct": round(pnl * 100, 2),
           "peak_price": None, "peak_date": None, "drawdown_from_peak_pct": None, "peak_basis": None, "regime": reg}
    if reg == "unknown":
        out.update(label="판단 불가", reason=t.get("reason"))
        return out
    ma20, ma60 = t["ma20"], t["ma60"]
    lines = {"추세 이탈선(20일선×0.96)": ma20 * MA_EXIT_BUFFER}
    past_stop = pnl <= STOP
    stop_note = f"평단 대비 {pnl * 100:.1f}% — 손절가(평단×0.92={avg_price * 0.92:,.0f}원) 이미 하회. 지금부터는 추세 기준으로 판단. " if past_stop else ""
    if reg in ("weakening", "down"):
        out.update(status="sell", label="매도(추세 이탈)" if reg == "weakening" else "매도(하락 추세)",
                   reason=stop_note + t["reason"], sell_price=None, lines={k: round(v) for k, v in lines.items()})
        return out
    if reg == "rebound":
        sup = min(c[-21:-1]) if len(c) > 21 else min(c[:-1])
        lines = {"최근 20일 종가 저점": sup, "추세 전환 확인(60일선)": ma60}
        if cur < sup:
            out.update(status="sell", label="매도(반등 실패)", sell_price=round(sup),
                       reason=stop_note + f"하락 추세 속 반등이 최근 20일 저점 {sup:,.0f}원을 깼다")
        else:
            out.update(status="rebound", label="반등 관찰", sell_price=round(sup),
                       reason=stop_note + f"20일선 {ma20:,.0f} < 60일선 {ma60:,.0f}(하락 추세), 현재가는 20일선 위. "
                                          f"{sup:,.0f}원(최근 20일 저점) 아래로 내려가면 반등 실패 → 매도, 20일선이 60일선 위로 올라서면 상승 추세 전환")
        out["lines"] = {k: round(v) for k, v in lines.items()}
        return out
    # up / pullback
    if pnl > TRAIL_ARM:
        if bought_at:
            start = next((k for k, d in enumerate(dates_asc) if str(d)[:10] >= str(bought_at)[:10]), len(c) - 1)
            basis = "매수일 이후 고점"
        else:
            start = trend_start_index(c)
            basis = "현재 상승 추세 시작(20일선>60일선 교차) 이후 고점 — 매수일 미기록"
        seg = c[start:] or [cur]
        k = max(range(len(seg)), key=lambda j: seg[j])
        peak, peak_date = seg[k], str(dates_asc[start + k])[:10]
        lines["추적손절가(고점×0.80)"] = peak * (1 + TRAIL_MOMENTUM)
        out.update(peak_price=peak, peak_date=peak_date, peak_basis=basis, drawdown_from_peak_pct=round((cur / peak - 1) * 100, 2))
    elif not past_stop:
        lines["손절가(평단×0.92)"] = avg_price * (1 + STOP)
    name, line = max(lines.items(), key=lambda kv: kv[1])
    out["lines"] = {k: round(v) for k, v in lines.items()}
    out["sell_price"] = round(line)
    gap = (cur / line - 1) * 100
    if cur < line:
        out.update(status="sell", label="매도(기준가 이탈)", reason=stop_note + f"현재가가 {name} {line:,.0f}원 아래")
    else:
        out.update(status="hold", label="보유", reason=stop_note + f"{t['label']} — 매도 기준가 {line:,.0f}원({name}, 현재가 대비 {-gap:.1f}% 아래)")
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


def entry_signal(closes_asc, vols_asc, inst5=0.0, frn5=0.0):
    """매수후보 진입 판단(2026-10-05) — 모멘텀Easy(se_momentum)·피크Easy(peak_easy) 진입 조건, 매도와 같은 추세 국면 규칙.
    주도 섹터 순위·KOSPI 대비 상대강도 조건은 이 화면에 데이터가 없어 제외(설명에 명시).
    반환 (신호, 사유): strong_buy 피크Easy 충족 / buy 모멘텀Easy 충족 / hold 상승 추세·조건 미충족 / caution 하락 추세 속 반등 / sell 추세 약화 / strong_sell 하락 추세."""
    c = [float(x) for x in closes_asc]
    t = assess_trend(c)
    reg = t.get("regime")
    if reg == "unknown":
        return "hold", "가격 60일 미만 — 판단 불가"
    if reg == "down":
        return "strong_sell", "하락 추세(20일선 < 60일선, 현재가 < 20일선) — 진입 불가"
    if reg == "weakening":
        return "sell", "추세 약화(5일선 < 20일선×0.96) — 진입 불가"
    if reg == "rebound":
        return "caution", f"하락 추세 속 반등 — 20일선 {t['ma20']:,.0f}이 60일선 {t['ma60']:,.0f} 위로 올라설 때까지 대기"
    cur, ma5, ma20 = c[-1], t["ma5"], t["ma20"]
    hi52 = max(c[-252:])
    v = [float(x or 0) for x in vols_asc]
    vol_ok = len(v) >= 20 and sum(v[-5:]) / 5 > (sum(v[-20:]) / 20) * 1.3
    if cur >= hi52 * 0.995 and cur > ma20 and vol_ok:
        return "strong_buy", "피크Easy 진입 조건 충족: 52주 신고가권 + 20일선>60일선 + 거래량 재증가(5일 평균 > 20일 평균×1.3)"
    if ma5 > ma20 and cur >= ma20 * 0.97 and (inst5 > 0 or frn5 > 0):
        return "buy", "모멘텀Easy 진입 조건 충족: 5일선 > 20일선, 현재가 ≥ 20일선×0.97, 기관 또는 외국인 5일 순매수"
    miss = []
    if not ma5 > ma20:
        miss.append("5일선 ≤ 20일선")
    if not (inst5 > 0 or frn5 > 0):
        miss.append("기관·외국인 5일 순매도")
    if cur < hi52 * 0.995:
        miss.append(f"52주 고점 대비 {(cur / hi52 - 1) * 100:.0f}%")
    return "hold", f"{t['label']} — 진입 조건 미충족({', '.join(miss)})"
