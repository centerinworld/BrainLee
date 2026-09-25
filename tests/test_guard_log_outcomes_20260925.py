"""§11 S3 — 가드 차단 사후 성과 계산과 가드 로그 중복 제거."""
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location("fill_guard_log_outcomes", ROOT / "scripts" / "fill_guard_log_outcomes_20260925.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

import pytest


@pytest.fixture(autouse=True)
def _no_shadow_env(monkeypatch):
    """운영 .env의 VT_SHADOW_STRATEGIES(momentum,peak)가 테스트에 새지 않게 한다."""
    monkeypatch.delenv("VT_SHADOW_STRATEGIES", raising=False)


def test_forward_returns_only_elapsed_horizons():
    closes = [101.0] * 4 + [110.0] + [90.0] * 14 + [120.0]      # 5일째 110, 20일째 120, 60일째 없음
    r = mod.forward_returns(100.0, closes)
    assert r[5] == 10.0 and r[20] == 20.0 and r[60] is None


def test_forward_returns_invalid_base_or_price():
    assert mod.forward_returns(None, [1.0] * 70) == {5: None, 20: None, 60: None}
    assert mod.forward_returns(0, [1.0] * 70) == {5: None, 20: None, 60: None}
    assert mod.forward_returns(100.0, [0.0] * 70)[5] is None


def test_guard_names_come_from_reason_prefix(monkeypatch):
    import sqlite3
    import virtual_trade_guards as g
    logged = []
    monkeypatch.setattr(g, "_log", lambda conn, code, strat, guard, decision, detail, price=None, kospi=None:
                        logged.append((guard, decision, price)))
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE price_history (stock_code TEXT, date TEXT, close REAL)")
    c.execute("CREATE TABLE peak_holding (id INTEGER PRIMARY KEY, stock_code TEXT, strategy TEXT, buy_price REAL, quantity INTEGER, is_active INTEGER)")
    c.execute("CREATE TABLE stock_universe (stock_code TEXT, sector_large TEXT)")
    for i, s in enumerate(["a", "b"]):
        c.execute("INSERT INTO peak_holding VALUES (?, '005930', ?, 1000, 10, 1)", (i, s))
    r = g.check_entry(c, "005930", "momentum", 10, 1000)
    assert not r["allowed"]
    assert ("exposure_stock", "blocked", 1000) in logged
