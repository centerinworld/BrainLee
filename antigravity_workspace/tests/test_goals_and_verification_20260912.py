"""
소유자가 2026-09-12에 제시한 7개 목표 + 이중 AI 검증 완료 게이트 회귀 테스트.
LLM 호출은 전부 mock - 실제 네트워크/비용 없이 게이트 로직만 검증한다.
"""

import os
import sys
import tempfile
import unittest
import unittest.mock
from unittest.mock import patch

workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

from memory.goals_registry import GoalsRegistry, seed_default_goals, SEED_GOALS, VERDICT_COMPLETE, VERDICT_INCOMPLETE
from memory.llm_usage_ledger import LLMUsageLedger
from goal_verification import GoalVerifier


class TestGoalsRegistry(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp_db.close()
        self.registry = GoalsRegistry(db_path=self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def test_seed_creates_all_7_goals(self):
        seed_default_goals(self.registry)
        goals = self.registry.list_goals()
        self.assertEqual(len(goals), 7)
        self.assertEqual({g["goal_id"] for g in goals}, {g["goal_id"] for g in SEED_GOALS})

    def test_seed_is_idempotent(self):
        """재시작/재실행 시 목표가 중복 생성되지 않는다."""
        seed_default_goals(self.registry)
        seed_default_goals(self.registry)
        self.assertEqual(len(self.registry.list_goals()), 7)

    def test_key_goals_start_active_continuous_goals_start_ongoing(self):
        seed_default_goals(self.registry)
        key_goal = self.registry.get_goal("goal_2_800pct_strategy")
        continuous_goal = self.registry.get_goal("goal_4_market_intelligence")
        self.assertEqual(key_goal["status"], "ACTIVE")
        self.assertEqual(continuous_goal["status"], "ONGOING")

    def test_single_verifier_does_not_complete_key_goal(self):
        seed_default_goals(self.registry)
        result = self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_COMPLETE)
        self.assertFalse(result["completed"])
        self.assertEqual(self.registry.get_goal("goal_1_zero_defect_data")["status"], "ACTIVE")

    def test_two_distinct_verifiers_agreeing_completes_key_goal(self):
        seed_default_goals(self.registry)
        self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_COMPLETE)
        result = self.registry.record_verification("goal_1_zero_defect_data", "deepseek:y", VERDICT_COMPLETE)
        self.assertTrue(result["completed"])
        self.assertEqual(self.registry.get_goal("goal_1_zero_defect_data")["status"], "COMPLETED")

    def test_same_verifier_twice_does_not_count_as_two(self):
        """같은 검증자가 두 번 COMPLETE_100을 말해도 '서로 다른 검증자 2명'은 아니다."""
        seed_default_goals(self.registry)
        self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_COMPLETE)
        result = self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_COMPLETE)
        self.assertFalse(result["completed"])

    def test_disagreement_does_not_complete(self):
        seed_default_goals(self.registry)
        self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_COMPLETE)
        result = self.registry.record_verification("goal_1_zero_defect_data", "deepseek:y", VERDICT_INCOMPLETE)
        self.assertFalse(result["completed"])

    def test_latest_verdict_from_same_verifier_overrides_earlier_one(self):
        """검증자가 나중에 판정을 바꾸면(NOT_COMPLETE -> COMPLETE_100) 최신 판정만 유효해야 한다."""
        seed_default_goals(self.registry)
        self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_INCOMPLETE)
        self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_COMPLETE)
        result = self.registry.record_verification("goal_1_zero_defect_data", "deepseek:y", VERDICT_COMPLETE)
        self.assertTrue(result["completed"])

    def test_continuous_goal_never_auto_completes(self):
        seed_default_goals(self.registry)
        self.registry.record_verification("goal_4_market_intelligence", "gemini:x", VERDICT_COMPLETE)
        result = self.registry.record_verification("goal_4_market_intelligence", "deepseek:y", VERDICT_COMPLETE)
        self.assertFalse(result["completed"])
        self.assertEqual(self.registry.get_goal("goal_4_market_intelligence")["status"], "ONGOING")

    def test_progress_log_records_and_orders_recent_first(self):
        seed_default_goals(self.registry)
        self.registry.log_progress("goal_4_market_intelligence", "첫 진행")
        self.registry.log_progress("goal_4_market_intelligence", "두번째 진행")
        log = self.registry.get_progress_log("goal_4_market_intelligence")
        self.assertEqual(log[0]["note"], "두번째 진행")


class TestGoalVerifier(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp_db.close()
        self.tmp_usage_db = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        self.tmp_usage_db.close()
        self.registry = GoalsRegistry(db_path=self.tmp_db.name)
        seed_default_goals(self.registry)
        self.usage_ledger = LLMUsageLedger(db_path=self.tmp_usage_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)
        os.unlink(self.tmp_usage_db.name)

    def _make_verifier(self, side_effect):
        stub = unittest.mock.Mock()
        stub.chat_completion_with_meta.side_effect = side_effect
        return GoalVerifier(llm_client=stub, usage_ledger=self.usage_ledger, registry=self.registry)

    def test_verify_calls_two_distinct_providers(self):
        """두 모델이 실제로 서로 다른 provider로 지정 호출되는지 확인 (같은 provider가
        두 번 불려서 '이중검증'이 무의미해지는 걸 방지하는 것이 이 기능의 핵심)."""
        seen_providers = []

        def fake_call(messages, provider=None, **kwargs):
            seen_providers.append(provider)
            return {
                "content": '{"verdict": "COMPLETE_100", "confidence": 0.95, "reason": "충분한 증거"}',
                "provider": provider, "model": f"{provider}-model", "usage": None, "is_fallback": False
            }

        verifier = self._make_verifier(fake_call)
        result = verifier.verify("goal_1_zero_defect_data", evidence="모든 검사 통과")
        self.assertEqual(len(set(seen_providers)), 2)
        self.assertEqual(result["status"], "COMPLETED")

    def test_verify_with_one_disagreement_stays_active(self):
        def fake_call(messages, provider=None, **kwargs):
            verdict = "COMPLETE_100" if provider == "gemini" else "NOT_COMPLETE"
            return {
                "content": f'{{"verdict": "{verdict}", "confidence": 0.8, "reason": "근거"}}',
                "provider": provider, "model": f"{provider}-model", "usage": None, "is_fallback": False
            }

        verifier = self._make_verifier(fake_call)
        result = verifier.verify("goal_2_800pct_strategy", evidence="단일 실행 결과뿐")
        self.assertEqual(result["status"], "ACTIVE")

    def test_verify_handles_malformed_response(self):
        def fake_call(messages, provider=None, **kwargs):
            return {"content": "이 목표는 완료됐다고 생각합니다.", "provider": provider,
                    "model": "x", "usage": None, "is_fallback": False}

        verifier = self._make_verifier(fake_call)
        result = verifier.verify("goal_3_autotrading_ready", evidence="증거")
        self.assertEqual(result["status"], "ACTIVE")
        self.assertTrue(all(o["verdict"] is None for o in result["outcomes"]))

    def test_verify_handles_all_providers_failing(self):
        def fake_call(messages, provider=None, **kwargs):
            return {"content": "", "provider": "none", "model": None, "usage": None, "is_fallback": True}

        verifier = self._make_verifier(fake_call)
        result = verifier.verify("goal_1_zero_defect_data", evidence="증거")
        self.assertEqual(result["status"], "ACTIVE")

    def test_verify_records_usage_for_each_call(self):
        def fake_call(messages, provider=None, **kwargs):
            return {
                "content": '{"verdict": "NOT_COMPLETE", "confidence": 0.5, "reason": "부족"}',
                "provider": provider, "model": f"{provider}-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "is_fallback": False
            }

        verifier = self._make_verifier(fake_call)
        verifier.verify("goal_1_zero_defect_data", evidence="증거")
        summary = self.usage_ledger.summary()
        self.assertEqual(summary["total_calls"], 2)

    def test_unknown_goal_id_raises(self):
        verifier = self._make_verifier(lambda *a, **k: {"content": "", "is_fallback": True, "provider": "none", "model": None, "usage": None})
        with self.assertRaises(ValueError):
            verifier.verify("no_such_goal", evidence="x")


if __name__ == "__main__":
    unittest.main()
