"""다중 입력 신호 증거 원장(backtest_common.SignalEvidenceLedger) 규칙 검증.

감사 스크립트와 백테스트 엔진이 같은 판정 함수(evaluate_signal_evidence)를 쓰므로,
여기서 판정 규칙이 흔들리면 selected_strategy_data_availability 결과가 바로 바뀐다.
"""
import inspect
import unittest

import backtest_common as bc
from backtest_common import (
    SIGNAL_EVIDENCE_REQUIREMENTS,
    SignalEvidenceLedger,
    _composite_financial_inputs,
    _normalize_entries,
    _v11_financial_inputs,
    evaluate_signal_evidence,
    evidence_aggregate,
    evidence_item,
)


def _row(code, entry, decision, dataset, row_id="1", avail="2024-01-01", basis="actual_disclosure"):
    return {"stock_code": code, "entry_date": entry, "decision_date": decision, "dataset": dataset,
            "source_row_id": row_id, "available_at": avail, "availability_basis": basis}


class EvaluateSignalEvidenceTest(unittest.TestCase):
    def test_every_entry_needs_every_required_dataset(self):
        entries = [("000001", "2024-03-05")]
        rows = [_row("000001", "2024-03-05", "2024-03-04", "sector_financial_data")]
        verdict = evaluate_signal_evidence("golden_cross", entries, rows)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["missing_evidence"], 1)  # sector_investor_flow 누락

    def test_available_after_signal_fails(self):
        entries = [("000001", "2024-03-05")]
        rows = [_row("000001", "2024-03-05", "2024-03-04", "financial_data", avail="2024-03-05")]
        verdict = evaluate_signal_evidence("turnaround", entries, rows)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["available_after_signal"], 1)

    def test_timestamp_on_signal_day_is_allowed(self):
        entries = [("000001", "2024-03-05")]
        rows = [_row("000001", "2024-03-05", "2024-03-04", "financial_data",
                     avail="2024-03-04 18:10:00")]
        self.assertTrue(evaluate_signal_evidence("turnaround", entries, rows)["passed"])

    def test_signal_must_precede_entry(self):
        entries = [("000001", "2024-03-05")]
        rows = [_row("000001", "2024-03-05", "2024-03-05", "financial_data")]
        verdict = evaluate_signal_evidence("turnaround", entries, rows)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["decision_not_before_entry"], 1)

    def test_explicit_none_counts_as_evidence(self):
        entries = [("000001", "2024-03-05")]
        rows = [_row("000001", "2024-03-05", "2024-03-04", "financial_data", row_id="NONE", avail=None)]
        self.assertTrue(evaluate_signal_evidence("regime_adaptive", entries, rows)["passed"])

    def test_row_without_available_at_fails(self):
        entries = [("000001", "2024-03-05")]
        rows = [_row("000001", "2024-03-05", "2024-03-04", "financial_data", avail=None)]
        verdict = evaluate_signal_evidence("turnaround", entries, rows)
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["row_without_available_at"], 1)

    def test_statutory_estimate_is_only_approx(self):
        entries = [("000001", "2024-03-05")]
        rows = [_row("000001", "2024-03-05", "2024-03-04", "financial_data", basis="statutory_estimate")]
        verdict = evaluate_signal_evidence("turnaround", entries, rows)
        self.assertTrue(verdict["passed"])
        self.assertEqual(verdict["pit_grade"], "point_in_time_approx")

    def test_actual_disclosure_is_verified(self):
        entries = [("000001", "2024-03-05")]
        rows = [_row("000001", "2024-03-05", "2024-03-04", "financial_data")]
        self.assertEqual(evaluate_signal_evidence("turnaround", entries, rows)["pit_grade"],
                         "point_in_time_verified")

    def test_unregistered_strategy_never_passes(self):
        self.assertFalse(evaluate_signal_evidence("no_such_strategy", [], [])["passed"])

    def test_requirements_cover_all_failed_selected_strategies(self):
        expected = {
            "composite", "contract_momentum", "earnings_conviction", "earnings_supply_discovery",
            "golden_cross", "high_profit_compound", "moonshot_turnaround", "recovery",
            "regime_adaptive", "se_momentum", "sector_focus", "turnaround",
        }
        self.assertEqual(set(SIGNAL_EVIDENCE_REQUIREMENTS), expected)


class NormalizeEntriesTest(unittest.TestCase):
    def test_all_engine_ledger_formats(self):
        trades = [
            {"stock_code": "000001", "entry_date": "2024-01-02"},               # base/golden_cross
            {"code": "000002", "buy_date": "2024-01-03", "entry": 1000.0},      # earnings_* (entry=가격)
            {"code": "000002", "buy_date": "2024-01-03", "action": "buy"},      # 부분청산/매수이벤트 중복
            {"date": "2024-01-04", "code": "000003", "action": "BUY"},          # sector_focus
            {"date": "2024-02-04", "code": "000003", "action": "SELL"},
            {"sc": "000004", "entry": "2024-01-05", "exit": "2024-02-01"},      # composite
        ]
        self.assertEqual(_normalize_entries(trades), [
            ("000001", "2024-01-02"), ("000002", "2024-01-03"),
            ("000003", "2024-01-04"), ("000004", "2024-01-05"),
        ])


class LedgerTest(unittest.TestCase):
    def test_entry_links_to_latest_prior_signal(self):
        ledger = SignalEvidenceLedger("turnaround")
        ledger.note("000001", "2024-01-02", [evidence_item("financial_data", 11, "2023-12-01")])
        ledger.note("000001", "2024-03-04", [evidence_item("financial_data", 22, "2024-02-20")])
        entries, rows = ledger.rows_for([{"code": "000001", "buy_date": "2024-03-05"}])
        self.assertEqual(entries, [("000001", "2024-03-05")])
        self.assertEqual([(r["decision_date"], r["source_row_id"]) for r in rows],
                         [("2024-03-04", "22")])

    def test_same_day_note_is_not_a_prior_signal(self):
        ledger = SignalEvidenceLedger("turnaround")
        ledger.note("000001", "2024-03-05", [evidence_item("financial_data", 1, "2024-01-01")])
        _entries, rows = ledger.rows_for([{"code": "000001", "buy_date": "2024-03-05"}])
        self.assertEqual(rows, [])  # 당일 체결은 D 신호 -> D+1 체결 계약 위반

    def test_ledger_does_not_touch_trades(self):
        trades = [{"code": "000001", "buy_date": "2024-03-05", "pnl": 1}]
        before = repr(trades)
        SignalEvidenceLedger("turnaround").rows_for(trades)
        self.assertEqual(repr(trades), before)


class AggregateTest(unittest.TestCase):
    def test_snapshot_hash_is_order_independent(self):
        a = evidence_aggregate("investor_flow", [
            {"row_id": "A:2024-01-02", "available_at": "2024-01-02", "value": [1]},
            {"row_id": "B:2024-01-03", "available_at": "2024-01-03", "value": [2]},
        ])
        b = evidence_aggregate("investor_flow", [
            {"row_id": "B:2024-01-03", "available_at": "2024-01-03", "value": [2]},
            {"row_id": "A:2024-01-02", "available_at": "2024-01-02", "value": [1]},
        ])
        self.assertEqual(a["snapshot_hash"], b["snapshot_hash"])
        self.assertEqual(a["available_at"], "2024-01-03")  # 가장 늦은 구성 행

    def test_value_change_changes_hash(self):
        a = evidence_aggregate("x", [{"row_id": "A", "available_at": "2024-01-02", "value": 1}])
        b = evidence_aggregate("x", [{"row_id": "A", "available_at": "2024-01-02", "value": 2}])
        self.assertNotEqual(a["snapshot_hash"], b["snapshot_hash"])

    def test_empty_aggregate_is_explicit_none(self):
        item = evidence_aggregate("x", [])
        self.assertEqual(item["source_row_id"], "NONE")


def _fin(year, q, op, avail, is_annual=False, row_id=0):
    # (year, quarter, rev, op, eps, bps, equity, ni, roe, is_annual, avail, id, report_type, created, updated)
    return (year, q, 100, op, 1.0, 10.0, 1, 1, 1, is_annual, avail, row_id, "CFS", None, None)


class SignalInputReproductionTest(unittest.TestCase):
    ROWS = [
        _fin(2023, 1, 50e8, "2023-05-15", row_id=1),
        _fin(2023, 2, 60e8, "2023-08-14", row_id=2),
        _fin(2024, 1, 90e8, "2024-05-14", row_id=3),
        _fin(2024, 2, 95e8, "2024-08-14", row_id=4),
        _fin(2024, 3, 99e8, "2024-11-14", row_id=5),  # 판단일 이후 공시
    ]

    def test_v11_inputs_respect_availability(self):
        used = dict((role, r[11]) for role, r in _v11_financial_inputs(self.ROWS, "2024-09-01"))
        self.assertEqual(used, {"f0": 4, "f0_yoy": 2, "f1": 3, "f1_yoy": 1})

    def test_v11_inputs_match_signal_function_inputs(self):
        # _is_buy_v11의 재무 판단부와 같은 행을 재현하는지: f0/f0_yoy YoY>30%면 해당 재무 조건 통과
        used = dict(_v11_financial_inputs(self.ROWS, "2024-09-01"))
        self.assertGreater((used["f0"][3] - used["f0_yoy"][3]) / used["f0_yoy"][3], 0.30)

    def test_composite_inputs(self):
        roles = [role for role, _ in _composite_financial_inputs(self.ROWS, "2024-09-01")]
        self.assertEqual(roles, ["ta_f0", "ta_f1", "ta_year_ago", "value_latest"])


class GoldenCrossSectorMemoTest(unittest.TestCase):
    def test_memo_never_reuses_a_later_as_of_date(self):
        from backtest_strategies import golden_cross as gc
        calls = []
        original_score = gc._sector_score_as_of
        original_key = gc._get_stock_sector_key
        try:
            gc._sector_score_as_of = lambda conn, sk, as_of: calls.append(as_of) or 60.0
            gc._get_stock_sector_key = lambda code: "semis"
            memo = {("semis", "2024-06"): ("2024-06-20", 99.0)}  # 다른 run이 늦은 날짜로 채운 값
            self.assertTrue(gc._is_sector_buy(None, "000001", "2024-06-03", 55.0, memo=memo))
            self.assertEqual(calls, ["2024-06-03"])               # 늦은 캐시는 재계산
            gc._is_sector_buy(None, "000001", "2024-06-10", 55.0, memo=memo)
            self.assertEqual(calls, ["2024-06-03"])               # 이른 캐시는 재사용
        finally:
            gc._sector_score_as_of = original_score
            gc._get_stock_sector_key = original_key

    def test_engine_uses_run_scoped_memo(self):
        from backtest_strategies import golden_cross as gc
        src = inspect.getsource(gc.run_backtest_golden_cross)
        self.assertIn("memo=gc_sector_memo", src)


class EngineWiringTest(unittest.TestCase):
    """각 엔진이 실제로 증거를 남기고 저장하는지(코드 경로) 확인."""

    ENGINES = {
        "earnings_conviction": "run_backtest_earnings_conviction",
        "earnings_supply_discovery": "run_backtest_earnings_supply_discovery",
        "contract_momentum": "run_backtest_contract_momentum",
        "moonshot_turnaround": "run_backtest_moonshot_turnaround",
        "turnaround": "run_backtest_turnaround",
        "recovery": "run_backtest_recovery",
        "regime_adaptive": "run_backtest_regime_adaptive",
        "se_momentum": "run_backtest_se_momentum",
        "golden_cross": "run_backtest_golden_cross",
        "sector": "run_backtest_sector",
        "high_profit_compound": "run_backtest_high_profit_compound",
        "composite": "run_backtest_composite",
    }

    def test_every_engine_notes_and_persists(self):
        import importlib
        for module_name, fn in self.ENGINES.items():
            mod = importlib.import_module(f"backtest_strategies.{module_name}")
            src = inspect.getsource(getattr(mod, fn))
            with self.subTest(engine=module_name):
                self.assertIn("SignalEvidenceLedger(", src)
                self.assertIn("evidence.persist(", src)
                self.assertTrue("evidence.note(" in src or "_note_evidence(" in src
                                or "_note_gc_evidence(" in src)

    def test_contract_correction_date_from_rcept_no(self):
        from backtest_strategies import contract_momentum as cm
        src = inspect.getsource(cm.run_backtest_contract_momentum)
        self.assertIn("corrected_by_rcept_no", src)
        self.assertIn("uses_corrected = min_quarterly_impact is not None", src)


class RegistryApproxGradeTest(unittest.TestCase):
    def test_estimated_availability_blocks_verified(self):
        import run_registry
        src = inspect.getsource(run_registry)
        self.assertIn('get("pit_grade") == "point_in_time_approx"', src)
        self.assertIn("not availability_estimated", src)


if __name__ == "__main__":
    unittest.main()
