"""trend_rules.py 단위 테스트 — docs/SIGNAL_RULES.md 규칙을 고정 가격 시계열로 확인(2026-10-05).

규칙이 하루에 두 번 바뀐 일이 있어(현재가→5일선 비교, 252일 이력 조건) 회귀를 막는다.
"""
import trend_rules as tr


def _dates(n):
    import datetime as dt
    d0 = dt.date(2025, 1, 1)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def rising(n=120, start=100.0, step=1.0):
    return [start + step * i for i in range(n)]


def falling(n=120, start=300.0, step=1.0):
    return [start - step * i for i in range(n)]


# ── 1. 추세 국면 5개 + 판단 불가 ─────────────────────────────
def test_regime_up():
    assert tr.assess_trend(rising())["regime"] == "up"


def test_regime_down():
    assert tr.assess_trend(falling())["regime"] == "down"


def test_regime_pullback():
    # 꾸준히 오르다 마지막 이틀 20일선 바로 아래로 — 5일선은 아직 20일선×0.96 위, 20일선 ≥ 60일선
    c = rising(120)
    ma20 = sum(c[-20:]) / 20
    c[-1] = ma20 * 0.99
    t = tr.assess_trend(c)
    assert t["regime"] == "pullback", t


def test_regime_weakening():
    # 오르다가 최근 5일 급락 → 5일선 < 20일선×0.96, 20일선은 아직 60일선 위
    c = rising(120) + [150.0] * 5
    t = tr.assess_trend(c)
    assert t["ma20"] >= t["ma60"] and t["ma5"] < t["ma20"] * 0.96
    assert t["regime"] == "weakening"


def test_regime_rebound():
    # 오래 내리다 최근 반등 → 20일선 < 60일선, 현재가 > 20일선
    c = falling(120) + [250.0]
    t = tr.assess_trend(c)
    assert t["ma20"] < t["ma60"] and c[-1] > t["ma20"]
    assert t["regime"] == "rebound"


def test_regime_unknown_short_history():
    assert tr.assess_trend(rising(59))["regime"] == "unknown"


# ── 2. 매도시그널(exit_signal) ──────────────────────────────
def test_exit_hold_with_trailing_stop_and_peak_reference():
    c = rising(120)
    e = tr.exit_signal(c, _dates(len(c)), avg_price=100.0)
    assert e["status"] == "hold"
    assert e["sell_price"] == round(max(c) * 0.80)  # 추적손절가(모멘텀Easy −20%)
    assert any("피크Easy" in k for k in e["lines"])  # −25%는 참고 기준선
    assert "5일선" in e["reason"]  # 추세 이탈은 5일선 조건으로 안내


def test_exit_trend_line_is_not_price_trigger():
    # 하루 급락으로 현재가가 20일선×0.96 아래여도 5일선이 위면 매도 아님(원 전략 = 5일선 비교)
    c = rising(120)
    ma20 = sum(c[-20:]) / 20
    c[-1] = ma20 * 0.95
    t = tr.assess_trend(c)
    assert t["ma5"] >= t["ma20"] * 0.96
    e = tr.exit_signal(c, _dates(len(c)), avg_price=150.0)
    assert e["status"] != "sell", e


def test_exit_rebound_watch_uses_20day_low():
    c = falling(120) + [250.0]
    e = tr.exit_signal(c, _dates(len(c)), avg_price=260.0)
    assert e["status"] == "rebound"
    assert e["sell_price"] == round(min(c[-21:-1]))


def test_exit_sell_on_down_trend():
    c = falling(120)
    e = tr.exit_signal(c, _dates(len(c)), avg_price=200.0)
    assert e["status"] == "sell"


def test_exit_past_stop_in_uptrend_has_no_price_line():
    # 평단 대비 −8%를 이미 넘김 + 상승 추세 → 손절 대신 추세 조건만(SIGNAL_RULES 5절 2번)
    c = rising(120)
    e = tr.exit_signal(c, _dates(len(c)), avg_price=c[-1] / 0.80)
    assert e["status"] == "hold" and e["sell_price"] is None
    assert "이미 하회" in e["reason"]


def test_exit_stop_price_within_loss_band():
    c = rising(120)
    avg = c[-1] / 0.97  # 손실 −3% → 손절가 평단×0.92 적용
    e = tr.exit_signal(c, _dates(len(c)), avg_price=avg)
    assert e["status"] == "hold" and e["sell_price"] == round(avg * 0.92)


# ── 3. 매수후보 진입(entry_signal) ─────────────────────────
def test_entry_peak_requires_252_days():
    c = rising(200)
    v = [100.0] * 195 + [500.0] * 5  # 거래량 재증가
    sig, reason = tr.entry_signal(c, v, inst5=0, frn5=0)
    assert sig != "strong_buy" and "252" in reason


def test_entry_peak_with_full_year():
    c = rising(300)
    v = [100.0] * 295 + [500.0] * 5
    assert tr.entry_signal(c, v)[0] == "strong_buy"


def test_entry_blocked_in_down_trend():
    assert tr.entry_signal(falling(120), [100.0] * 120)[0] == "strong_sell"


# ── 4. 무상증자 보정 ────────────────────────────────────────
def test_adjust_series_scales_before_event():
    d = _dates(4)
    s = tr.adjust_series(d, [100.0, 100.0, 50.0, 50.0], [(d[2], 0.5)])
    assert s == [50.0, 50.0, 50.0, 50.0]
