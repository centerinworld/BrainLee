"""virtual_trade_guards 단위 테스트 (sqlite 인메모리 — 쿼리는 표준 SQL)."""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import virtual_trade_guards as g

import pytest


@pytest.fixture(autouse=True)
def _no_shadow_env(monkeypatch):
    """운영 .env의 VT_SHADOW_STRATEGIES(momentum,peak)가 테스트에 새지 않게 한다."""
    monkeypatch.delenv("VT_SHADOW_STRATEGIES", raising=False)


def _conn(kospi_trend="down"):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE price_history (stock_code TEXT, date TEXT, close REAL)")
    c.execute("CREATE TABLE peak_holding (id INTEGER PRIMARY KEY, stock_code TEXT, strategy TEXT, buy_price REAL, quantity INTEGER, is_active INTEGER)")
    c.execute("CREATE TABLE stock_universe (stock_code TEXT, sector_large TEXT)")
    from datetime import date, timedelta
    for i in range(70):  # KOSPI 70일: down=하락 추세(마지막<MA60), up=상승
        v = 3000 - i * 10 if kospi_trend == "down" else 2300 + i * 10
        c.execute("INSERT INTO price_history VALUES ('^KS11', ?, ?)", ((date(2026, 6, 1) + timedelta(days=i)).isoformat(), v))
    return c


def test_regime_blocks_momentum_but_not_exempt():
    c = _conn("down")
    r = g.check_entry(c, "005930", "momentum", 10, 1000)
    assert not r["allowed"] and any("regime_filter" in x for x in r["reasons"])
    assert g.check_entry(c, "005930", "v_recovery", 10, 1000)["allowed"]  # 역발상 계열 제외


def test_regime_allows_in_uptrend_and_failopen_on_short_data():
    assert g.check_entry(_conn("up"), "005930", "momentum", 10, 1000)["allowed"]
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE price_history (stock_code TEXT, date TEXT, close REAL)")
    c.execute("CREATE TABLE peak_holding (id INTEGER PRIMARY KEY, stock_code TEXT, strategy TEXT, buy_price REAL, quantity INTEGER, is_active INTEGER)")
    c.execute("CREATE TABLE stock_universe (stock_code TEXT, sector_large TEXT)")
    assert g.check_entry(c, "005930", "momentum", 10, 1000)["allowed"]  # KOSPI 데이터 부족 → fail-open


def test_exposure_per_stock_limit(monkeypatch):
    c = _conn("up")
    for i, s in enumerate(["a", "b"]):
        c.execute("INSERT INTO peak_holding VALUES (?, '005930', ?, 1000, 10, 1)", (i, s))
    r = g.check_entry(c, "005930", "momentum", 10, 1000)
    assert not r["allowed"] and any("exposure_stock" in x for x in r["reasons"])
    monkeypatch.setenv("VT_EXPOSURE_LIMIT", "0")
    assert g.check_entry(c, "005930", "momentum", 10, 1000)["allowed"]


def test_sector_cap():
    c = _conn("up")
    c.execute("INSERT INTO stock_universe VALUES ('111111', '반도체'), ('222222', '반도체'), ('333333', '화장품')")
    c.execute("INSERT INTO peak_holding VALUES (1, '111111', 'a', 1000000, 20, 1)")   # 반도체 2천만원
    c.execute("INSERT INTO peak_holding VALUES (2, '333333', 'b', 1000000, 5, 1)")    # 화장품 500만원
    r = g.check_entry(c, "222222", "momentum", 10, 1000000)                            # 반도체 +1천만원 → 30/35 = 86%
    assert not r["allowed"] and any("exposure_sector" in x for x in r["reasons"])


def test_breakeven_exit():
    c = _conn("up")
    for i, px in enumerate([100, 105, 112, 108, 99]):  # 매입가 100, 최고 112(+12%) 후 99
        c.execute("INSERT INTO price_history VALUES ('000001', ?, ?)", (f"2026-09-0{i+1}", px))
    assert g.breakeven_exit(c, "000001", "momentum", "2026-09-01", 100.0, 99.0) is True
    assert g.breakeven_exit(c, "000001", "momentum", "2026-09-01", 100.0, 103.0) is False   # 아직 본전 위
    assert g.breakeven_exit(c, "000001", "gpt_v18", "2026-09-01", 100.0, 99.0) is False     # 제외 전략
    c.execute("DELETE FROM price_history WHERE stock_code='000001'")
    for i, px in enumerate([100, 104, 106, 99]):  # 최고 +6% → 미발동
        c.execute("INSERT INTO price_history VALUES ('000001', ?, ?)", (f"2026-09-0{i+1}", px))
    assert g.breakeven_exit(c, "000001", "momentum", "2026-09-01", 100.0, 99.0) is False


def test_flags_off(monkeypatch):
    c = _conn("down")
    monkeypatch.setenv("VT_REGIME_FILTER", "0")
    assert g.check_entry(c, "005930", "momentum", 10, 1000)["allowed"]
    monkeypatch.setenv("VT_BREAKEVEN_STOP", "0")
    c.execute("INSERT INTO price_history VALUES ('000001', '2026-09-01', 120)")
    assert g.breakeven_exit(c, "000001", "momentum", "2026-09-01", 100.0, 99.0) is False


def test_shadow_strategies_block_only_listed_and_log(monkeypatch):
    logged = []
    monkeypatch.setattr(g, "_log", lambda conn, code, strat, guard, decision, detail, price=None, kospi=None:
                        logged.append((strat, guard, decision, price)))
    monkeypatch.setenv("VT_SHADOW_STRATEGIES", "momentum, peak")
    c = _conn("up")
    r = g.check_entry(c, "005930", "momentum", 10, 1000)
    assert r == {"allowed": False, "reasons": ["shadow_strategy: momentum 신규 진입은 기록 전용"], "shadow": True}
    assert logged == [("momentum", "shadow_strategy", "shadow_would_block", 1000)]   # 다른 가드 로그 없음
    assert g.check_entry(c, "005930", "v_recovery", 10, 1000)["allowed"]             # 목록 밖 전략은 그대로
    assert g.shadow_entry(c, "005930", "peak", 900) is True and g.shadow_entry(c, "005930", "value", 900) is False


def test_shadow_default_empty_is_noop(monkeypatch):
    monkeypatch.delenv("VT_SHADOW_STRATEGIES", raising=False)
    assert g.shadow_strategies() == set()
    assert g.check_entry(_conn("up"), "005930", "momentum", 10, 1000)["allowed"]


def test_min_mcap_and_cooldown_flags(monkeypatch):
    from datetime import datetime, timedelta
    c = _conn("up")
    c.execute("DROP TABLE stock_universe")
    c.execute("CREATE TABLE stock_universe (stock_code TEXT, sector_large TEXT, market_cap REAL, base_date TEXT)")
    c.execute("INSERT INTO stock_universe VALUES ('111111', '반도체', 500, '2026-09-01'), ('222222', '반도체', 5000, '2026-09-01')")
    c.execute("ALTER TABLE peak_holding ADD COLUMN sold_at TEXT")
    c.execute("INSERT INTO peak_holding VALUES (9, '222222', 'x', 1000, 10, 0, ?)", ((datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S"),))
    assert g.check_entry(c, "111111", "momentum", 10, 1000)["allowed"]            # 기본 꺼짐 → 그대로
    monkeypatch.setenv("VT_MIN_MCAP_EOK", "1000")
    r = g.check_entry(c, "111111", "momentum", 10, 1000)
    assert not r["allowed"] and any(x.startswith("min_mcap") for x in r["reasons"])
    assert g.check_entry(c, "222222", "momentum", 10, 1000)["allowed"]
    monkeypatch.setenv("VT_REENTRY_COOLDOWN_DAYS", "28")
    r = g.check_entry(c, "222222", "momentum", 10, 1000)
    assert not r["allowed"] and any(x.startswith("reentry_cooldown") for x in r["reasons"])
