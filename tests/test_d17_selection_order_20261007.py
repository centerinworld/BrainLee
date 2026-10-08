import inspect

from backtest_common import _sort_selection_candidates
from backtest_strategies import (
    dual_conviction,
    patent_catalyst,
    regime_adaptive,
    segment_revenue_divergence,
    v1_dart,
    v8,
    v10_hs,
    v11_hs,
)


def test_d17_candidate_sorting_modes():
    candidates = [
        ("000003", 0.2, 0.0),
        ("000001", 0.4, 0.0),
        ("000002", 0.4, 0.1),
    ]

    assert [c[0] for c in _sort_selection_candidates(candidates, "code", "2024-01-02")] == [
        "000001",
        "000002",
        "000003",
    ]
    assert [c[0] for c in _sort_selection_candidates(candidates, "score", "2024-01-02")] == [
        "000002",
        "000001",
        "000003",
    ]
    assert _sort_selection_candidates(candidates, "random:7", "2024-01-02") == _sort_selection_candidates(
        list(reversed(candidates)), "random:7", "2024-01-02"
    )


def test_d17_self_loop_strategies_accept_selection_order():
    funcs = [
        v1_dart.run_backtest_v1_dart,
        v10_hs.run_backtest_v10_hs,
        v11_hs.run_backtest_v11_hs,
        regime_adaptive.run_backtest_regime_adaptive,
        v8.run_backtest_v8,
        dual_conviction.run_backtest_dual_conviction,
        patent_catalyst.run_backtest_patent_catalyst,
        segment_revenue_divergence.run_backtest_segment_revenue_divergence,
    ]

    for func in funcs:
        assert "selection_order" in inspect.signature(func).parameters
