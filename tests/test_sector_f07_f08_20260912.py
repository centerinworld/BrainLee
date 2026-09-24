"""
Regression tests for F07 (missing-open fallback / stale exit price) and F08 (RS warmup
buffer) in backtest_strategies/sector.py
(docs/claude_handoff_strategy_code_findings_20260912.md).

These are characterization tests against the actual source text/logic (similar in spirit
to the AST-based F04 verification Codex used) rather than full end-to-end DB-backed runs,
since run_backtest_sector requires the real production DB and _SECTOR_GROUPS universe.
The real end-to-end impact was measured separately by re-running run_backtest_sector
against the live DB (see research_outputs/strategy_code_review_20260912/).
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

SECTOR_SRC = (ROOT / "backtest_strategies" / "sector.py").read_text(encoding="utf-8")


class TestF07MissingOpenNotFilledAsOpen(unittest.TestCase):
    def test_price_loader_stores_none_for_missing_open_not_close_fallback(self):
        self.assertIn("float(op) if op and op > 0 else None", SECTOR_SRC,
                      "missing open must be preserved as None, not silently replaced by close")
        self.assertNotIn("float(op) if op and op > 0 else float(cl)", SECTOR_SRC,
                          "the old close-fallback-as-open pattern must be gone")

    def test_pending_sell_and_buy_gate_on_missing_open(self):
        self.assertIn("pdata is None or pdata[3] is None", SECTOR_SRC)
        occurrences = SECTOR_SRC.count("pdata is None or pdata[3] is None")
        self.assertEqual(occurrences, 2, "both the pending-sell and pending-buy fill sites must gate on missing open")

    def test_replicated_gating_logic_defers_sell_and_expires_buy_on_missing_open(self):
        """Directly replicate the fixed gating branches (same shape as the source) against
        a synthetic pdata table to confirm the intended behavior."""
        price_data = {
            "AAAAAA": {"2024-01-05": (10_500.0, 10_600.0, 10_400.0, None)},   # close known, open missing
            "BBBBBB": {"2024-01-05": (10_500.0, 10_600.0, 10_400.0, 10_450.0)},  # normal day
        }
        trade_date = "2024-01-05"

        # pending sell: A should stay pending, B should fill
        sec_pending_sells = [("AAAAAA", "test"), ("BBBBBB", "test")]
        _still = []
        filled_sells = []
        for code, reason in sec_pending_sells:
            pdata = price_data.get(code, {}).get(trade_date)
            if pdata is None or pdata[3] is None:
                _still.append((code, reason))
                continue
            filled_sells.append((code, pdata[3]))
        self.assertEqual(_still, [("AAAAAA", "test")])
        self.assertEqual(filled_sells, [("BBBBBB", 10_450.0)])

        # pending buy: A should expire, B should fill
        sec_pending_buys = [("AAAAAA", "sector1", {}), ("BBBBBB", "sector1", {})]
        filled_buys = []
        for code, sector_key, meta in sec_pending_buys:
            pdata = price_data.get(code, {}).get(trade_date)
            if pdata is None or pdata[3] is None:
                continue
            filled_buys.append((code, pdata[3]))
        self.assertEqual(filled_buys, [("BBBBBB", 10_450.0)])


class TestF07NoStaleExitPrice(unittest.TestCase):
    def test_final_liquidation_no_longer_falls_back_to_whole_history_dict(self):
        self.assertNotIn('price_data.get(code, {}).get(last_date) or price_data.get(code, {})', SECTOR_SRC,
                          "the stale historical-price fallback for end-of-run liquidation must be removed")
        self.assertIn('pdata = price_data.get(code, {}).get(last_date)\n            if pdata:', SECTOR_SRC)

    def test_replicated_final_liquidation_skips_when_last_date_missing(self):
        """A stock with no price on the final trade_date, but plenty of price history from
        months earlier, must NOT be liquidated at that stale historical price."""
        price_data = {
            "CCCCCC": {"2020-03-02": (5_000.0, 5_100.0, 4_900.0, 5_050.0)},  # last real trade, long ago
        }
        last_date = "2026-09-08"
        pdata = price_data.get("CCCCCC", {}).get(last_date)
        self.assertIsNone(pdata, "no price on the actual final date")
        if pdata:
            sell_p = pdata[0]  # would be wrong: this branch should not execute
            self.fail(f"must not fabricate a same-day fill from stale data, got {sell_p}")
        # correct path: conservative no-price handling, with only a reference-only lookup
        stale_history = price_data.get("CCCCCC", {})
        stale_last_date = sorted(stale_history.keys())[-1] if stale_history else None
        self.assertEqual(stale_last_date, "2020-03-02", "old price kept only as an informational reference")


class TestF07UnresolvedNotConfirmedLoss(unittest.TestCase):
    """Priority 2 (docs/claude_resume_strategy_review_20260912.md, Codex review): missing
    price at period end must not be recorded as a confirmed -100%% loss -- that's an
    assertion of fact we don't have. It must be pnl_pct=None (unresolved) with the -100%%
    kept only as a separately-labeled conservative lower-bound scenario."""

    def test_source_no_longer_asserts_minus_100_as_the_recorded_pnl(self):
        self.assertIn('"pnl_pct": None,', SECTOR_SRC)
        self.assertIn('"disposition": "unresolved_no_price_at_period_end"', SECTOR_SRC)
        self.assertIn('"conservative_lower_bound_pnl_pct": -100.0', SECTOR_SRC)

    def test_sell_trades_filter_excludes_unresolved_records(self):
        self.assertIn('t.get("pnl_pct") is not None', SECTOR_SRC,
                      "average-return/win-rate stats must exclude unresolved (None) records, "
                      "not silently coerce them into a -100%% loss")

    def test_replicated_filter_behavior(self):
        all_trades = [
            {"action": "SELL", "pnl_pct": 12.5},
            {"action": "FINAL", "pnl_pct": None, "disposition": "unresolved_no_price_at_period_end",
             "conservative_lower_bound_pnl_pct": -100.0},
            {"action": "FINAL", "pnl_pct": -3.2, "reason": "종료청산"},
            {"action": "BUY", "pnl_pct": None},
        ]
        sell_trades = [t for t in all_trades if t.get("pnl_pct") is not None and t["action"] != "BUY"]
        self.assertEqual(len(sell_trades), 2, "the unresolved FINAL record must be excluded")
        avg = sum(t["pnl_pct"] for t in sell_trades) / len(sell_trades)
        self.assertAlmostEqual(avg, (12.5 - 3.2) / 2, places=4)


class TestF08WarmupBufferSeparatedFromMeasurementWindow(unittest.TestCase):
    def test_price_load_window_extends_before_start_date(self):
        self.assertIn("warmup_buffer_days = 110", SECTOR_SRC)
        self.assertIn("price_load_start", SECTOR_SRC)

    def test_trade_dates_still_filtered_to_start_date_onward(self):
        self.assertIn("trade_dates = sorted(set(r[1] for r in rows_p if r[1] >= start_date))", SECTOR_SRC,
                      "measured/trading days must stay anchored to start_date even though price_data now has an earlier warmup buffer")


if __name__ == "__main__":
    unittest.main()
