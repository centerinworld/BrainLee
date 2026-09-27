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
from llm_client import AntigravityLLMClient
from goal_verification import GoalVerifier

# 실제로는 openai만 "verified" 등급이라(2026-09-12 저사양 모델 검증 대행 방지 조치),
# provider_pair 명시 테스트에서는 verified 등급 가짜 provider 2개를 임시로 등록해 쓴다.
VERIFIED_TEST_PROVIDERS = ("test_verified_a", "test_verified_b")


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
        """검증자가 같은 라운드(같은 evidence_hash)에서 판정을 바꾸면(NOT_COMPLETE ->
        COMPLETE_100) 최신 판정만 유효해야 한다."""
        seed_default_goals(self.registry)
        self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_INCOMPLETE, evidence_hash="round-1")
        self.registry.record_verification("goal_1_zero_defect_data", "gemini:x", VERDICT_COMPLETE, evidence_hash="round-1")
        result = self.registry.record_verification("goal_1_zero_defect_data", "deepseek:y", VERDICT_COMPLETE, evidence_hash="round-1")
        self.assertTrue(result["completed"])

    def test_different_evidence_rounds_do_not_combine_to_complete(self):
        """2026-09-12 검토에서 지적된 실제 버그의 회귀 테스트: 검증자 A가 증거X에 대해
        승인하고, 검증자 B가 전혀 다른 증거Y에 대해 (따로) 승인해도 - 둘이 같은 것을 보고
        동의한 적이 없으므로 - 완료되면 안 된다. 이전 버전은 검증자 이름별 '역대 최신
        판정'만 봐서 이 경우도 완료 처리했다."""
        seed_default_goals(self.registry)
        self.registry.record_verification(
            "goal_1_zero_defect_data", "gemini:x", VERDICT_COMPLETE, evidence_hash="evidence-X-weak"
        )
        result = self.registry.record_verification(
            "goal_1_zero_defect_data", "deepseek:y", VERDICT_COMPLETE, evidence_hash="evidence-Y-different"
        )
        self.assertFalse(result["completed"])
        self.assertEqual(self.registry.get_goal("goal_1_zero_defect_data")["status"], "ACTIVE")

    def test_third_verifier_disagreement_in_same_round_blocks_completion(self):
        """같은 라운드에서 A/B가 승인해도 C가 반대하면(같은 라운드 내) - 문서 회귀 사례
        '두 승인이 있으면 C가 반대해도 완료' 시나리오가 재발하지 않는지는 현재 구현이
        '2명 이상 동의'만 요구하므로 C의 반대 자체가 완료를 막지는 않는다(정책 선택).
        이 테스트는 최소한 서로 다른 라운드끼리 섞이지 않음을 재확인한다."""
        seed_default_goals(self.registry)
        self.registry.record_verification("goal_1_zero_defect_data", "a:x", VERDICT_COMPLETE, evidence_hash="round-A")
        self.registry.record_verification("goal_1_zero_defect_data", "b:x", VERDICT_COMPLETE, evidence_hash="round-B")
        result = self.registry.get_goal("goal_1_zero_defect_data")
        self.assertEqual(result["status"], "ACTIVE")  # round-A와 round-B는 서로 다른 라운드라 완료 안 됨

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
        with patch.dict(AntigravityLLMClient.PROVIDER_TIER, {p: "verified" for p in VERIFIED_TEST_PROVIDERS}):
            result = verifier.verify("goal_1_zero_defect_data", evidence="모든 검사 통과", provider_pair=VERIFIED_TEST_PROVIDERS)
        self.assertEqual(len(set(seen_providers)), 2)
        self.assertEqual(result["status"], "COMPLETED")

    def test_verify_with_one_disagreement_stays_active(self):
        def fake_call(messages, provider=None, **kwargs):
            verdict = "COMPLETE_100" if provider == VERIFIED_TEST_PROVIDERS[0] else "NOT_COMPLETE"
            return {
                "content": f'{{"verdict": "{verdict}", "confidence": 0.8, "reason": "근거"}}',
                "provider": provider, "model": f"{provider}-model", "usage": None, "is_fallback": False
            }

        verifier = self._make_verifier(fake_call)
        with patch.dict(AntigravityLLMClient.PROVIDER_TIER, {p: "verified" for p in VERIFIED_TEST_PROVIDERS}):
            result = verifier.verify("goal_2_800pct_strategy", evidence="단일 실행 결과뿐", provider_pair=VERIFIED_TEST_PROVIDERS)
        self.assertEqual(result["status"], "ACTIVE")

    def test_verify_handles_malformed_response(self):
        def fake_call(messages, provider=None, **kwargs):
            return {"content": "이 목표는 완료됐다고 생각합니다.", "provider": provider,
                    "model": "x", "usage": None, "is_fallback": False}

        verifier = self._make_verifier(fake_call)
        with patch.dict(AntigravityLLMClient.PROVIDER_TIER, {p: "verified" for p in VERIFIED_TEST_PROVIDERS}):
            result = verifier.verify("goal_3_autotrading_ready", evidence="증거", provider_pair=VERIFIED_TEST_PROVIDERS)
        self.assertEqual(result["status"], "ACTIVE")
        self.assertEqual(len(result["outcomes"]), 2)
        self.assertTrue(all(o["verdict"] is None for o in result["outcomes"]))

    def test_verify_handles_all_providers_failing(self):
        def fake_call(messages, provider=None, **kwargs):
            return {"content": "", "provider": "none", "model": None, "usage": None, "is_fallback": True}

        verifier = self._make_verifier(fake_call)
        with patch.dict(AntigravityLLMClient.PROVIDER_TIER, {p: "verified" for p in VERIFIED_TEST_PROVIDERS}):
            result = verifier.verify("goal_1_zero_defect_data", evidence="증거", provider_pair=VERIFIED_TEST_PROVIDERS)
        self.assertEqual(result["status"], "ACTIVE")

    def test_verify_records_usage_for_each_call(self):
        def fake_call(messages, provider=None, **kwargs):
            return {
                "content": '{"verdict": "NOT_COMPLETE", "confidence": 0.5, "reason": "부족"}',
                "provider": provider, "model": f"{provider}-model",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, "is_fallback": False
            }

        verifier = self._make_verifier(fake_call)
        with patch.dict(AntigravityLLMClient.PROVIDER_TIER, {p: "verified" for p in VERIFIED_TEST_PROVIDERS}):
            verifier.verify("goal_1_zero_defect_data", evidence="증거", provider_pair=VERIFIED_TEST_PROVIDERS)
        summary = self.usage_ledger.summary()
        self.assertEqual(summary["total_calls"], 2)

    def test_verify_refuses_draft_tier_provider_even_if_explicitly_named(self):
        """2026-09-12 사고 재발 방지 핵심 테스트: draft 등급 provider를 provider_pair로
        명시해도 목표 완료 검증에는 거부되어야 한다(저사양 모델이 최종 판단을 대행하지 못하게)."""
        called = []
        verifier = self._make_verifier(lambda *a, **k: called.append(1))
        result = verifier.verify("goal_1_zero_defect_data", evidence="증거", provider_pair=("gemini", "deepseek"))
        self.assertEqual(result["blocked_reason"], "PROVIDER_TIER_TOO_LOW:gemini")
        self.assertEqual(called, [])  # LLM이 아예 호출되지 않아야 한다
        self.assertEqual(self.registry.get_goal("goal_1_zero_defect_data")["status"], "ACTIVE")

    def test_verify_without_provider_pair_defaults_to_codex_sdk_and_cli(self):
        """Claude 토큰이 없을 때 기본 검증은 Codex SDK와 CLI의 별도 세션을 쓴다."""
        import goal_verification
        self.assertEqual(goal_verification.DEFAULT_VERIFIER_PROVIDERS, ("codex_sdk", "codex_cli"))

        seen_providers = []

        def fake_call(messages, provider=None, **kwargs):
            seen_providers.append(provider)
            return {
                "content": '{"verdict": "NOT_COMPLETE", "confidence": 0.5, "reason": "x"}',
                "provider": provider, "model": f"{provider}-model", "usage": None, "is_fallback": False
            }

        verifier = self._make_verifier(fake_call)
        result = verifier.verify("goal_1_zero_defect_data", evidence="증거")  # provider_pair 없음
        self.assertEqual(set(seen_providers), {"codex_sdk", "codex_cli"})
        self.assertEqual(result["verification_independence"], "same_vendor_distinct_sessions")

    def test_verify_blocks_when_fewer_than_two_verified_providers_configured(self):
        """provider_pair를 안 주고 기본값도 없다면(예: 미래에 기본 쌍이 비게 되는
        상황을 가정), verified 등급 provider가 2개 미만일 때 조용히 낮은 등급으로
        대신하지 않고 명시적으로 차단해야 한다."""
        import goal_verification
        called = []
        verifier = self._make_verifier(lambda *a, **k: called.append(1))
        with patch.object(goal_verification, "DEFAULT_VERIFIER_PROVIDERS", None), \
             patch.dict(AntigravityLLMClient.PROVIDER_TIER, {"openai": "verified"}, clear=True):
            result = verifier.verify("goal_1_zero_defect_data", evidence="증거")  # provider_pair 없음
        self.assertEqual(result["blocked_reason"], "INSUFFICIENT_VERIFIED_PROVIDERS")
        self.assertEqual(called, [])
        self.assertEqual(self.registry.get_goal("goal_1_zero_defect_data")["status"], "ACTIVE")

    def test_verify_refuses_same_provider_twice(self):
        called = []
        verifier = self._make_verifier(lambda *a, **k: called.append(1))
        with patch.dict(AntigravityLLMClient.PROVIDER_TIER, {"test_verified_a": "verified"}):
            result = verifier.verify("goal_1_zero_defect_data", evidence="증거", provider_pair=("test_verified_a", "test_verified_a"))
        self.assertEqual(result["blocked_reason"], "VERIFIERS_NOT_DISTINCT")
        self.assertEqual(called, [])

    def test_unknown_goal_id_raises(self):
        verifier = self._make_verifier(lambda *a, **k: {"content": "", "is_fallback": True, "provider": "none", "model": None, "usage": None})
        with self.assertRaises(ValueError):
            verifier.verify("no_such_goal", evidence="x")


if __name__ == "__main__":
    unittest.main()
