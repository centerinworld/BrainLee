"""
Regression tests for F01-F04 (docs/claude_handoff_strategy_code_findings_20260912.md).

These convert Codex's synthetic-input BUG-REPRODUCTION assertions (which lived in
research_outputs/strategy_code_review_20260912/verify_findings.py, preserved unmodified as
evidence) into CORRECT-BEHAVIOR regression tests against the fixed code. Where the original
script asserted the buggy value, these tests assert the fix removes it.
"""
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio_engine import CashPortfolio  # noqa: E402
from merged_simulator import (  # noqa: E402
    CandidateOrder,
    MergeConfig,
    simulate_merged_account,
)


class TestF01AnnualYearDisclosureGating(unittest.TestCase):
    """sector.py's OP-YoY component must not select an annual year before its real (or
    legal-deadline-fallback) disclosure date."""

    def test_release_date_gates_future_annual_year(self):
        from backtest_common import _release_date, _load_disc_dates

        # No real DART row loaded -> falls back to legal deadline (annual: year+1 March 31).
        _load_disc_dates.__globals__["_DISC_DATES"] = {}
        # FY2024 results are legally not available until 2025-03-31.
        self.assertGreater(_release_date(2024, 4, True, "A"), "2024-01-05")
        # FY2023 results are legally not available until 2024-03-31 either -- still future
        # relative to a 2024-01-05 decision date.
        self.assertGreater(_release_date(2023, 4, True, "A"), "2024-01-05")
        # FY2022 results (legal deadline 2023-03-31) ARE available by 2024-01-05.
        self.assertLessEqual(_release_date(2022, 4, True, "A"), "2024-01-05")

    def test_sector_op_yoy_uses_disclosure_gate_not_calendar_year(self):
        """Calls the REAL production function (backtest_strategies.sector.
        _pit_gated_sector_op_yoy_median) against an in-memory fixture DB -- not a
        replicated copy of its logic (per Codex's review: a replica can keep passing even
        if the real gate is later removed from the production code path). Fixture mirrors
        Codex's synthetic reproduction shape (2022/2023/2024 annual rows)."""
        from backtest_strategies.sector import _pit_gated_sector_op_yoy_median
        from backtest_common import _load_disc_dates

        _load_disc_dates.__globals__["_DISC_DATES"] = {}  # force legal-deadline fallback path
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE financial_data(stock_code TEXT, year INTEGER, is_annual INTEGER, operating_profit REAL, report_type TEXT)")
        conn.executemany(
            "INSERT INTO financial_data VALUES (?,?,?,?,?)",
            [("A", 2021, 1, 5, "CFS"), ("A", 2022, 1, 8, "CFS"), ("A", 2023, 1, 10, "CFS"), ("A", 2024, 1, 50, "CFS")],
        )
        trade_date = "2024-01-05"

        # sanity: confirm the OLD buggy query WOULD have picked 2024 on this fixture (the
        # exact bug this refactor guards against), even though the real function must not.
        old_year = conn.execute(
            "SELECT MAX(year) FROM financial_data WHERE stock_code IN (?) AND year<=? AND is_annual=1 AND operating_profit IS NOT NULL",
            ("A", int(trade_date[:4])),
        ).fetchone()[0]
        self.assertEqual(old_year, 2024, "sanity check: the bug this test guards against")

        # (2024-8)/8*100 = +525.0 if the bug were present (using undisclosed FY2024=50 vs FY2023=8)
        # (2022-8)/8... real fix should use FY2022=8 vs FY2021=5 -> (8-5)/5*100 = +60.0
        med_yoy = _pit_gated_sector_op_yoy_median(conn, ["A"], int(trade_date[:4]), trade_date)
        self.assertAlmostEqual(med_yoy, 60.0, places=3,
                                msg="must use FY2022 vs FY2021 (both disclosed by 2024-01-05), "
                                    "not FY2024 vs FY2023 (undisclosed)")

    def test_pit_gated_helper_skips_stock_with_no_available_year(self):
        """A stock with only future-dated annual rows (e.g. a very recent IPO with its
        first annual report not yet disclosed) must contribute no YoY value at all, not a
        crash or a spurious 0%%-looking entry that skews the median."""
        from backtest_strategies.sector import _pit_gated_sector_op_yoy_median
        from backtest_common import _load_disc_dates
        _load_disc_dates.__globals__["_DISC_DATES"] = {}
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE financial_data(stock_code TEXT, year INTEGER, is_annual INTEGER, operating_profit REAL, report_type TEXT)")
        conn.executemany(
            "INSERT INTO financial_data VALUES (?,?,?,?,?)",
            [("A", 2021, 1, 5, "CFS"), ("A", 2022, 1, 8, "CFS"),
             ("B", 2024, 1, 100, "CFS")],  # B's only row is undisclosed as of 2024-01-05
        )
        trade_date = "2024-01-05"
        med_yoy = _pit_gated_sector_op_yoy_median(conn, ["A", "B"], int(trade_date[:4]), trade_date)
        self.assertAlmostEqual(med_yoy, 60.0, places=3, msg="B must be excluded, only A contributes")


class TestF02SameDayCloseLookahead(unittest.TestCase):
    """Buy-sizing (position_limit/equity) must not depend on a same-day close the D+1-open
    order execution could not have known.

    Strengthened per Codex's review (docs/claude_resume_strategy_review_20260912.md priority
    4): the original version of this test held only ONE existing position with abundant
    slots (limit 10 vs 11 while only 1 of 10 slots was used), so the two runs it compared
    gave the SAME buy decision (filled) either way regardless of whether the bug was
    present -- it could not actually have caught the bug it claimed to guard against. This
    version fills ALL slots exactly to the position_limit boundary (10 held positions,
    limit=10 at the unchanged mark) so that whether stock B's buy is allowed or rejected
    is entirely decided by whether the same-day close crosses that boundary (limit 10->11).
    """

    def test_merged_account_buy_slot_unaffected_by_same_day_close(self):
        """10 existing positions exactly fill dynamic_tickets' position_limit (10) at their
        buy price. A same-day (2024-01-05) close 20%% higher for those 10 positions would,
        under the pre-fix bug, raise equity enough to push position_limit to 11 BEFORE
        stock K's buy is evaluated -- letting K fill. Under the fix (marks_preopen), K's
        buy decision must be identical (rejected) regardless of that same-day close,
        because the D+1-open fill for K cannot have known it."""

        def run(same_day_close_multiplier: float) -> dict:
            held_codes = [f"H{i:05d}" for i in range(10)]
            orders = [CandidateOrder("2024-01-04", code, "buy", 900, "s1", 1.0, budget=9_000_000)
                      for code in held_codes]
            orders.append(CandidateOrder("2024-01-05", "KKKKKK", "buy", 1_000, "s1", 1.0))
            cfg = MergeConfig(initial_cash=100_000_000, dynamic_tickets=True, ticket_budget=10_000_000,
                               fee_bps=0, slippage_bps=0, sell_tax_bps=0)
            import merged_simulator as ms
            orig = ms._load_daily_price_map
            price_map = {code: {"2024-01-04": 900, "2024-01-05": 900 * same_day_close_multiplier}
                         for code in held_codes}
            ms._load_daily_price_map = lambda *a, **k: price_map
            try:
                return simulate_merged_account(orders, cfg)
            finally:
                ms._load_daily_price_map = orig

        result_low = run(1.0)   # same-day close unchanged from buy price
        result_high = run(1.2)  # same-day close +20%% for the 10 already-held positions
        k_status_low = next(e for e in result_low["events"] if e["stock_code"] == "KKKKKK" and e["side"] == "buy")["status"]
        k_status_high = next(e for e in result_high["events"] if e["stock_code"] == "KKKKKK" and e["side"] == "buy")["status"]
        self.assertEqual(k_status_low, k_status_high,
                          "K's buy decision on 2024-01-05 must not depend on the 10 held positions' same-day close")
        # With marks_preopen (fixed), equity for the position_limit check stays at the
        # unchanged-mark level (100,000,000 -> limit=10), and len(positions)==10 already,
        # so K must be rejected in BOTH cases -- a pre-fix version feeding today's close
        # into this calculation would have let K fill in the high-close run (limit->11).
        self.assertEqual(k_status_low, "rejected")
        self.assertEqual(k_status_high, "rejected")


class TestF03TicketPctCannotExpandHardCap(unittest.TestCase):
    def test_hard_cap_wins_over_ticket_pct_expansion(self):
        p = CashPortfolio(initial_cash=100_000_000, ticket_pct=0.25, fee_bps=0, slippage_bps=0)
        p.buy("A", "2024-01-05", 10_000, budget=5_000_000, hard_cap=5_000_000)
        spent = 100_000_000 - p.cash
        self.assertLessEqual(spent, 5_000_000 + 1, "hard_cap must bound spend even when ticket_pct wants to expand it")

    def test_ticket_pct_still_expands_default_ticket_when_no_hard_cap(self):
        """Regression guard: the fix must not break ticket_pct's actual intended purpose
        (id=139, equity_proportional_ticket_sizing_20260808, adopted) when no caller hard
        cap is in play (i.e. hard_cap=None, the default)."""
        p = CashPortfolio(initial_cash=100_000_000, ticket_pct=0.25, fee_bps=0, slippage_bps=0)
        p.buy("A", "2024-01-05", 10_000, budget=5_000_000)  # no hard_cap passed
        spent = 100_000_000 - p.cash
        self.assertEqual(spent, 25_000_000, "without hard_cap, ticket_pct should still scale the default ticket up")

    def test_merged_account_strategy_budget_weights_respected_with_ticket_pct(self):
        orders = [CandidateOrder("2024-01-05", "AAAAAA", "buy", 10_000, "s1", 1.0, budget=5_000_000)]
        cfg = MergeConfig(initial_cash=100_000_000, ticket_pct=0.25, fee_bps=0, slippage_bps=0, sell_tax_bps=0,
                           strategy_budget_weights={"s1": 0.05})  # 5% of 100M = 5,000,000 hard cap
        import merged_simulator as ms
        orig = ms._load_daily_price_map
        ms._load_daily_price_map = lambda *a, **k: {}
        try:
            result = simulate_merged_account(orders, cfg)
        finally:
            ms._load_daily_price_map = orig
        spent = cfg.initial_cash - result["summary"]["cash"]
        self.assertLessEqual(spent, 5_000_000 + 1,
                              "strategy_budget_weights cap (5,000,000) must not be expanded by ticket_pct=0.25")


class TestF04PyramidCashReconciliationSign(unittest.TestCase):
    def test_pyramid_add_treated_as_cash_outflow_in_reconciliation(self):
        p = CashPortfolio(initial_cash=100_000, fee_bps=0, slippage_bps=0, sell_tax_bps=0)
        p.buy("A", "2024-01-04", 100, budget=10_000)
        p.add_to_position("A", "2024-01-05", 100, budget=5_000)
        self.assertEqual(p.cash, 85_000)

        expected_cash = p.initial_cash
        for row in p.ledger:
            gross = float(row["quantity"]) * float(row["price"])
            side = row["side"]
            if side in ("buy", "pyramid_add"):
                expected_cash -= gross + float(row.get("fee") or 0)
            elif side == "sell":
                expected_cash += gross - float(row.get("fee") or 0) - float(row.get("tax") or 0)
            else:
                raise ValueError(f"unknown side {side!r}")
        self.assertEqual(expected_cash, p.cash, "fixed reconciliation must match actual cash exactly")

    def test_unknown_ledger_side_raises_instead_of_silently_crediting(self):
        fake_ledger = [{"quantity": 1, "price": 100, "side": "mystery_side", "fee": 0, "tax": 0}]
        expected_cash = 100_000
        with self.assertRaises(ValueError):
            for row in fake_ledger:
                gross = float(row["quantity"]) * float(row["price"])
                side = row["side"]
                if side in ("buy", "pyramid_add"):
                    expected_cash -= gross + float(row.get("fee") or 0)
                elif side == "sell":
                    expected_cash += gross - float(row.get("fee") or 0) - float(row.get("tax") or 0)
                else:
                    raise ValueError(f"unknown side {side!r}")


if __name__ == "__main__":
    unittest.main()
