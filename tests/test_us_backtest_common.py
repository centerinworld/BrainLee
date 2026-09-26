from __future__ import annotations

import pytest

from us_backtest_common import (
    USBacktestConfig,
    USBar,
    USTarget,
    _valid_bar,
    run_us_backtest,
)


def bar(ticker, day, opn, close=None):
    close = opn if close is None else close
    return USBar(ticker, day, opn, max(opn, close), min(opn, close), close, 1_000_000)


def test_close_signal_executes_only_at_next_session_open():
    bars = {"AAA": [bar("AAA", "2026-01-02", 10, 11), bar("AAA", "2026-01-05", 20, 21)]}
    cfg = USBacktestConfig("2026-01-02", "2026-01-05", initial_cash=1000,
                           max_positions=1, rebalance="daily", slippage_bps=0,
                           universe_mode="explicit")
    result = run_us_backtest(
        bars, cfg,
        lambda day, history: [USTarget("AAA", 1.0)] if day == "2026-01-02" else [],
    )
    assert len(result.trades) == 1
    assert result.trades[0]["signal_date"] == "2026-01-02"
    assert result.trades[0]["execution_date"] == "2026-01-05"
    assert result.trades[0]["price"] == 20
    assert result.trades[0]["price"] != 11


def test_missing_next_open_rejects_order_without_close_fallback():
    bars = {
        "AAA": [bar("AAA", "2026-01-02", 10), bar("AAA", "2026-01-06", 12)],
        "BBB": [bar("BBB", "2026-01-02", 5), bar("BBB", "2026-01-05", 6), bar("BBB", "2026-01-06", 7)],
    }
    cfg = USBacktestConfig("2026-01-02", "2026-01-06", initial_cash=1000,
                           max_positions=1, rebalance="daily", slippage_bps=0,
                           universe_mode="explicit")
    result = run_us_backtest(
        bars, cfg,
        lambda day, history: [USTarget("AAA", 1.0)] if day == "2026-01-02" else [],
    )
    assert result.trades == []
    assert result.quality["missing_open_rejections"] == 1


def test_current_membership_is_explicitly_survivorship_biased():
    bars = {"AAA": [bar("AAA", "2026-01-02", 10), bar("AAA", "2026-01-05", 11)]}
    cfg = USBacktestConfig("2026-01-02", "2026-01-05", max_positions=1,
                           universe_mode="current_membership")
    result = run_us_backtest(bars, cfg, lambda *_: [])
    assert result.quality["survivorship_bias"] is True
    assert result.quality["research_grade"] is False


def test_open_position_at_end_is_not_reported_as_complete():
    bars = {"AAA": [bar("AAA", "2026-01-02", 10), bar("AAA", "2026-01-05", 11)]}
    cfg = USBacktestConfig("2026-01-02", "2026-01-05", initial_cash=1000,
                           max_positions=1, slippage_bps=0, universe_mode="point_in_time")
    result = run_us_backtest(bars, cfg, lambda *_: [USTarget("AAA", 1)])
    assert result.quality["open_positions_at_end"] == ["AAA"]
    assert result.quality["execution_complete"] is False
    assert result.quality["research_grade"] is False


def test_large_discontinuity_is_reported_not_silently_adjusted():
    bars = {"AAA": [bar("AAA", "2026-01-02", 10), bar("AAA", "2026-01-05", 100)]}
    cfg = USBacktestConfig("2026-01-02", "2026-01-05", max_positions=1,
                           universe_mode="point_in_time")
    result = run_us_backtest(bars, cfg, lambda *_: [])
    assert result.quality["large_jump_count"] == 1
    assert result.quality["research_grade"] is False


def test_malformed_ohlc_fails_closed():
    assert _valid_bar(("AAA", "2026-01-02", None, 11, 9, 10, 1)) is None
    assert _valid_bar(("AAA", "2026-01-02", 10, 9, 8, 10, 1)) is None


def test_target_constraints_are_enforced():
    bars = {
        "AAA": [bar("AAA", "2026-01-02", 10), bar("AAA", "2026-01-05", 10)],
        "BBB": [bar("BBB", "2026-01-02", 10), bar("BBB", "2026-01-05", 10)],
    }
    cfg = USBacktestConfig("2026-01-02", "2026-01-05", max_positions=1)
    with pytest.raises(ValueError, match="targets exceed"):
        run_us_backtest(bars, cfg, lambda *_: [USTarget("AAA", .6), USTarget("BBB", .6)])


def test_rebalance_resizes_existing_position_at_next_open():
    bars = {"AAA": [
        bar("AAA", "2026-01-02", 10),
        bar("AAA", "2026-01-05", 10),
        bar("AAA", "2026-01-06", 10),
    ]}
    cfg = USBacktestConfig("2026-01-02", "2026-01-06", initial_cash=1000,
                           max_positions=1, rebalance="daily", slippage_bps=0,
                           universe_mode="explicit")

    def signal(day, _history):
        return [USTarget("AAA", 1.0 if day == "2026-01-02" else 0.5)]

    result = run_us_backtest(bars, cfg, signal)
    assert [(x["side"], x["shares"], x["execution_date"]) for x in result.trades] == [
        ("buy", 100, "2026-01-05"),
        ("sell", 50, "2026-01-06"),
    ]


def test_us_engine_source_does_not_import_korean_backtest(monkeypatch):
    import inspect
    import us_backtest_common

    source = inspect.getsource(us_backtest_common)
    assert "from backtest_common" not in source
    assert "FROM price_history" not in source
    assert "stock_universe" not in source


def test_benchmark_is_measured_but_hidden_from_signal_universe():
    bars = {
        "AAA": [bar("AAA", "2026-01-02", 10), bar("AAA", "2026-01-05", 11)],
        "SPY": [bar("SPY", "2026-01-02", 100), bar("SPY", "2026-01-05", 110)],
    }
    seen = []
    cfg = USBacktestConfig("2026-01-02", "2026-01-05", benchmark="SPY")
    result = run_us_backtest(bars, cfg, lambda _day, history: seen.extend(history) or [])
    assert seen == ["AAA"]
    assert result.metrics["benchmark_return_pct"] == 10.0
