"""
Project Antigravity V2: 멀티 에이전트 Handoff 및 파이프라인 유닛 테스트 (unittest 기반)
"""

import unittest
import asyncio
import os
import sys
from unittest.mock import patch

# Add workspace root to sys.path
workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

from memory.vector_store import MemoryVectorStore
from agents.l1_pm_owner import L1PMOwner
from agents.l1_a_dev_orchestrator import DevOrchestrator
from agents.l1_b_content_orchestrator import ContentOrchestrator

class TestAntigravityAgents(unittest.IsolatedAsyncioTestCase):

    def test_vector_store_embedding_and_cosine(self):
        """L3 벡터 스토어 임베딩 생성 및 코사인 유사도 연산 검증"""
        vs = MemoryVectorStore()
        vec1 = vs.get_embedding("KF-21 초음속 전투기 양산 계약")
        vec2 = vs.get_embedding("KF-21 초음속 전투기 양산 계약")
        vec3 = vs.get_embedding("원유 가격 급등 및 환율 변동")
        
        self.assertEqual(len(vec1), 1536)
        # 동일 텍스트 유사도 == 1.0
        sim_same = vs.cosine_similarity(vec1, vec2)
        self.assertAlmostEqual(sim_same, 1.0, places=2)
        
        # 다른 텍스트 유사도 < 1.0
        sim_diff = vs.cosine_similarity(vec1, vec3)
        self.assertLess(sim_diff, 0.90)

    def test_l1_pm_intent_parsing(self):
        """L1 PM 의도 분석 및 DAG 테스크 분해 검증"""
        pm = L1PMOwner(auto_heal=True)
        
        # 복합 명령 파싱
        tasks = pm.parse_founder_intent("삼성전자 퀀트 리밸런싱 및 KAI 방산 동향 수집해줘")
        self.assertGreaterEqual(len(tasks), 2)
        domains = [t["domain"] for t in tasks]
        self.assertIn("dev_orchestrator", domains)
        self.assertIn("content_orchestrator", domains)
        
        for t in tasks:
            self.assertEqual(t["status"], "PENDING")
            self.assertEqual(t["priority"], "HIGH")

    @patch(
        "agents.l2_workers.defense_researcher.AntigravityLLMClient.chat_completion_with_meta",
        return_value={"content": "", "provider": "none", "model": None, "usage": None, "is_fallback": True}
    )
    async def test_full_autonomous_pipeline(self, mock_llm):
        """전체 자율 오케스트레이션 파이프라인 (L1 -> L1-A/L1-B -> L2 -> QA) 통합 테스트.
        LLM 호출은 mock 처리 - 실제 네트워크/비용 없이 템플릿 폴백 경로로 검증한다."""
        pm = L1PMOwner(auto_heal=True)
        completed_tasks = await pm.run_autonomous_loop("삼성전자 및 KAI 방산 전체 파이프라인 가동")
        
        self.assertGreaterEqual(len(completed_tasks), 2)
        for t in completed_tasks:
            self.assertEqual(t["status"], "COMPLETED")
            self.assertIn("qa_report", t)
            self.assertTrue(t["qa_report"]["qa_passed"])

if __name__ == "__main__":
    unittest.main()
