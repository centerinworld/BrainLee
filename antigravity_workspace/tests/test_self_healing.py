"""
Project Antigravity V2: 자가 고도화(Self-Healing) 및 버그 자동 패치 루프 유닛 테스트 (unittest 기반)
"""

import unittest
import os
import sys
from unittest.mock import Mock

# Add workspace root to sys.path
workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

from agents.l2_workers.codex_builder import CodexBuilder
from agents.l2_workers.claude_reviewer import ClaudeReviewer
from agents.l1_a_dev_orchestrator import DevOrchestrator

class TestSelfHealingLoop(unittest.TestCase):

    def test_codex_builder_stack_diagnosis(self):
        """codex_builder 스택 트레이스 진단 기능 검증"""
        builder = CodexBuilder()
        mock_stack = """Traceback (most recent call last):
  File "agents/l2_workers/quant_trader.py", line 42, in execute_order
    res = 1 / 0
ZeroDivisionError: division by zero"""
        
        diag = builder.analyze_stack_trace(mock_stack)
        self.assertEqual(diag["error_type"], "ZeroDivisionError")
        self.assertEqual(diag["line_number"], 42)
        self.assertIn("quant_trader.py", diag["target_file"])

    def test_claude_reviewer_quality_checks(self):
        """claude_reviewer 무결성/보안 교차 검증 기능 검증"""
        reviewer = ClaudeReviewer()
        
        # 1. 하드코딩된 API Key 거부 검증
        insecure_code = """
def bad_func():
    api_key = "sk-1234567890abcdef1234567890"
    return api_key
"""
        res = reviewer.review_code_quality(insecure_code)
        self.assertEqual(res["status"], "REJECTED")
        self.assertFalse(res["approved"])

        # 2. 클린 코드 통과 검증
        clean_code = """
import os

def good_func():
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise ValueError("API_KEY not found")
    return api_key
"""
        res_clean = reviewer.review_code_quality(clean_code)
        self.assertEqual(res_clean["status"], "APPROVED")
        self.assertTrue(res_clean["approved"])

    def test_orchestrator_self_healing_execution(self):
        """L1-A가 실제 diff를 받아도 검토만으로 자동 머지하지 않는지 검증한다.

        자동 머지는 테스트 실행과 git 반영 절차가 구현된 뒤에만 허용해야 한다.
        외부 LLM 호출 결과에 따라 통과 여부가 달라지지 않도록 응답을 고정한다.
        """
        llm_client = Mock()
        llm_client.chat_completion_with_meta.return_value = {
            "content": "--- a/f.py\n+++ b/f.py\n@@ -1,1 +1,2 @@\n+x = 1\n",
            "provider": "deepseek",
            "model": "deepseek-flash",
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            "is_fallback": False,
        }
        builder = CodexBuilder(llm_client=llm_client)
        orch = DevOrchestrator(is_mock=True, codex_builder=builder)

        # 인위적 예외 발생
        try:
            raise KeyError("missing_column_fnguide_revenue")
        except Exception as e:
            record = orch.self_healing_loop(e)

        self.assertEqual(record["error_type"], "KeyError")
        self.assertIn("fix/", record["patch_branch"])
        self.assertEqual(record["review_status"], "PARTIAL_REVIEW_DIFF")
        self.assertFalse(record["is_auto_merged"])
        self.assertIn("merge_blocked_reason", record)

if __name__ == "__main__":
    unittest.main()
