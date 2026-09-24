import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from backtest_common import (
    _final_liquidation_quote,
    _final_liquidation_quote_for_code,
    _load_delisting_outcomes,
    _run_portfolio,
)


class FinalLiquidationQuoteTest(unittest.TestCase):
    def test_exact_period_end_quote_is_used(self):
        self.assertEqual(
            _final_liquidation_quote("2024-12-30", {"2024-12-30": 1}, [100.0, 120.0]),
            (120.0, "기간종료"),
        )

    def test_stale_last_quote_is_not_carried_past_delisting(self):
        self.assertEqual(
            _final_liquidation_quote("2024-12-30", {"2024-10-09": 0}, [13500.0]),
            (0.0, "기간종료(시세부재 전액손실)"),
        )

    def test_exact_period_end_quote_wins_over_delisting_recovery(self):
        # A live quote on the exact final day is real, current data - it must
        # never be overridden by a stale/precomputed delisting recovery value.
        self.assertEqual(
            _final_liquidation_quote(
                "2024-12-30", {"2024-12-30": 1}, [100.0, 120.0],
                delisting_recovery=(999.0, "기간종료(합병ㆍ교환 실제가치 반영-주식)"),
            ),
            (120.0, "기간종료"),
        )

    def test_delisting_recovery_used_when_no_final_quote(self):
        # 2026-09-22: a stock that stops trading isn't always a bankruptcy -
        # a confirmed merger/share-exchange recovery value must replace the
        # blanket zero-recovery assumption when one was pre-loaded.
        self.assertEqual(
            _final_liquidation_quote(
                "2024-12-30", {"2024-10-09": 0}, [13500.0],
                delisting_recovery=(52178.34, "기간종료(합병ㆍ교환 실제가치 반영-주식)"),
            ),
            (52178.34, "기간종료(합병ㆍ교환 실제가치 반영-주식)"),
        )

    def test_delisting_recovery_none_falls_back_to_zero(self):
        # Backward compatibility: omitting the new kwarg entirely, or passing
        # an explicit None (no confirmed outcome found), must not change
        # existing behavior for every strategy that doesn't know about this.
        self.assertEqual(
            _final_liquidation_quote(
                "2024-12-30", {"2024-10-09": 0}, [13500.0], delisting_recovery=None,
            ),
            (0.0, "기간종료(시세부재 전액손실)"),
        )

    def test_bespoke_engine_wrapper_loads_confirmed_recovery(self):
        conn = MagicMock()
        conn.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[
                ("000060", "cash_buyout", None, None, None, 12345.0),
            ])),
        ]
        self.assertEqual(
            _final_liquidation_quote_for_code(
                conn, "000060", "2023-10-31", {"2023-01-20": 0}, [55500.0]
            ),
            (12345.0, "기간종료(합병ㆍ교환 실제가치 반영-현금)"),
        )


class LoadDelistingOutcomesTest(unittest.TestCase):
    def test_empty_stock_codes_short_circuits_without_querying(self):
        conn = MagicMock()
        self.assertEqual(_load_delisting_outcomes(conn, []), {})
        conn.execute.assert_not_called()

    def test_share_exchange_resolves_successor_price_times_ratio(self):
        conn = MagicMock()
        conn.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[
                ("000060", "share_exchange", "138040", 1.2657378, "2023-02-21", None),
            ])),
            MagicMock(fetchone=MagicMock(return_value=(41211.76953125,))),
        ]
        result = _load_delisting_outcomes(conn, ["000060"])
        self.assertIn("000060", result)
        price, reason = result["000060"]
        self.assertAlmostEqual(price, 41211.76953125 * 1.2657378, places=4)
        self.assertEqual(reason, "기간종료(합병ㆍ교환 실제가치 반영-주식)")

    def test_share_exchange_without_successor_price_is_dropped(self):
        # If the successor's own price is missing for the reference date, do
        # not fabricate a number - the stock simply isn't in the result dict,
        # so callers fall back to the existing zero-recovery default.
        conn = MagicMock()
        conn.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[
                ("000060", "share_exchange", "138040", 1.2657378, "2023-02-21", None),
            ])),
            MagicMock(fetchone=MagicMock(return_value=None)),
        ]
        result = _load_delisting_outcomes(conn, ["000060"])
        self.assertEqual(result, {})

    def test_cash_buyout_uses_cash_per_share_directly(self):
        conn = MagicMock()
        conn.execute.side_effect = [
            MagicMock(fetchall=MagicMock(return_value=[
                ("999999", "cash_buyout", None, None, None, 12345.0),
            ])),
        ]
        result = _load_delisting_outcomes(conn, ["999999"])
        self.assertEqual(result["999999"], (12345.0, "기간종료(합병ㆍ교환 실제가치 반영-현금)"))


class RunPortfolioFinalLiquidationTest(unittest.TestCase):
    def test_temporal_price_mask_and_financial_provenance_follow_actual_entry(self):
        fin = (2023, 4, 1000.0, 100.0, 10.0, 100.0, 500.0, 80.0, 10.0,
               1, "2024-03-31", 98765, "CFS")
        stock_data = {
            "123456": {
                "dates": ["2024-04-01", "2024-04-02", "2024-04-03"],
                "prices": [100.0, 100.0, 100.0],
                "opens": [100.0, 100.0, 100.0],
                "volumes": [1.0, 1.0, 1.0],
                "frn": [0.0, 0.0, 0.0], "inst": [0.0, 0.0, 0.0],
                "fins": [fin], "sim_start_i": 0, "mkt_cap_억": 500.0,
            }
        }
        provenance = []
        with (
            patch("backtest_common._is_buy_signal", return_value=True) as buy_signal,
            patch("backtest_common._score_entry", return_value=1.0),
            patch("backtest_common._check_sell", return_value=None),
            patch("backtest_common._load_trade_signals", return_value={}),
        ):
            trades, _ = _run_portfolio(
                stock_data["123456"]["dates"], stock_data,
                per_stock=1000.0, max_positions=1,
                entry_blocked_dates={"123456": {"2024-04-01"}},
                financial_provenance=provenance,
            )
        # The masked day never reaches the signal function; Apr-02 queues the
        # order and Apr-03 is the only executed entry.
        self.assertEqual(buy_signal.call_count, 1)
        self.assertEqual(len(trades), 1)
        self.assertEqual(provenance, [{
            "decision_date": "2024-04-02", "source_row_id": 98765,
            "available_at": "2024-03-31", "source_key": "2023Q4:CFS",
            "stock_code": "123456", "entry_date": "2024-04-03",
        }])

    def test_shared_portfolio_ledger_and_terminal_equity_use_zero_recovery(self):
        stock_data = {
            "282690": {
                "dates": ["2024-10-07", "2024-10-08"],
                "prices": [100.0, 100.0],
                "opens": [100.0, 100.0],
                "volumes": [1.0, 1.0],
                "frn": [0.0, 0.0],
                "inst": [0.0, 0.0],
                "fins": [None, None],
                "sim_start_i": 0,
                "mkt_cap_억": 500.0,
            }
        }
        with (
            patch("backtest_common._is_buy_signal", return_value=True),
            patch("backtest_common._score_entry", return_value=1.0),
            patch("backtest_common._check_sell", return_value=None),
            patch("backtest_common._load_trade_signals", return_value={}),
        ):
            trades, equity = _run_portfolio(
                ["2024-10-07", "2024-10-08", "2024-12-30"],
                stock_data,
                per_stock=1000.0,
                max_positions=1,
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_price"], 0.0)
        self.assertEqual(trades[0]["exit_reason"], "기간종료(시세부재 전액손실)")
        self.assertEqual(equity[-1]["date"], "2024-12-30")
        # The engine's existing net-profit convention also charges the entry
        # commission at close, so terminal equity can be slightly below zero.
        self.assertLessEqual(equity[-1]["equity"], 0)

    def test_delisting_recovery_kwarg_replaces_zero_recovery_end_to_end(self):
        stock_data = {
            "000060": {
                "dates": ["2023-01-19", "2023-01-20"],
                "prices": [55500.0, 55500.0],
                "opens": [55500.0, 55500.0],
                "volumes": [1.0, 1.0],
                "frn": [0.0, 0.0],
                "inst": [0.0, 0.0],
                "fins": [None, None],
                "sim_start_i": 0,
                "mkt_cap_억": 500.0,
            }
        }
        with (
            patch("backtest_common._is_buy_signal", return_value=True),
            patch("backtest_common._score_entry", return_value=1.0),
            patch("backtest_common._check_sell", return_value=None),
            patch("backtest_common._load_trade_signals", return_value={}),
        ):
            trades, _ = _run_portfolio(
                ["2023-01-19", "2023-01-20", "2023-10-31"],
                stock_data,
                per_stock=100_000.0,
                max_positions=1,
                delisting_recovery={
                    "000060": (52178.34, "기간종료(합병ㆍ교환 실제가치 반영-주식)"),
                },
            )
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["exit_price"], 52178.34)
        self.assertEqual(trades[0]["exit_reason"], "기간종료(합병ㆍ교환 실제가치 반영-주식)")

    def test_strategy_engines_do_not_restore_missing_final_quotes(self):
        root = Path(__file__).resolve().parents[1]
        strategy_files = [
            "aqr_multifactor.py", "composite.py", "contract_momentum.py",
            "deep_recovery.py", "dual_conviction.py", "dual_momentum.py",
            "earnings_conviction.py", "earnings_supply_discovery.py",
            "extreme_dd_volume.py", "golden_cross.py", "low_base_breakout.py",
            "magic_formula.py", "megatrend.py", "meta_v2.py",
            "moonshot_turnaround.py", "patent_catalyst.py", "peak_easy.py",
            "piotroski_value.py", "recovery.py", "regime_adaptive.py",
            "se_momentum.py", "segment_revenue_divergence.py",
            "tenbagger_accumulator.py", "tenbagger_pyramid.py",
            "turnaround.py", "v8.py", "v12.py",
        ]
        for name in strategy_files:
            source = (root / "backtest_strategies" / name).read_text()
            with self.subTest(strategy=name):
                self.assertIn("_final_liquidation_quote_for_code", source)
                self.assertNotRegex(
                    source,
                    r"if i is not None else (?:p|position)\[['\"]entry['\"]\]",
                )
                self.assertNotIn("for d in reversed(sim_dates)", source)


if __name__ == "__main__":
    unittest.main()
