"""
Project Antigravity: L1 PM / OS Owner - antigravity_master (제라드 던 페르소나)
사용자 의도 파싱, DAG 테스크 분해, L1-A/L1-B Handoff, 최종 결과물 QA 및 자율 실행 총괄
"""

import os
import sys
import json
import argparse
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from agents.l1_a_dev_orchestrator import DevOrchestrator
from agents.l1_b_content_orchestrator import ContentOrchestrator
from memory.vector_store import MemoryVectorStore
from memory.state_ledger import StateLedger

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [Antigravity L1] %(name)s: %(message)s"
)
logger = logging.getLogger("l1_pm_owner")

class L1PMOwner:
    def __init__(self, auto_heal: bool = True):
        self.persona = "제라드 던 (Jared Dunn)"
        self.auto_heal = auto_heal
        self.vector_store = MemoryVectorStore()
        self.dev_orchestrator = DevOrchestrator(is_mock=True)
        self.content_orchestrator = ContentOrchestrator(vector_store=self.vector_store)
        self.tasks: List[Dict[str, Any]] = []
        # A08: 작업 상태를 재시작 후에도 감사(audit)할 수 있도록 영속 원장에 기록한다.
        # (자동 재개 실행기는 별도 범위 - 여기서는 상태 이력의 영속 기록만 보장한다)
        self.ledger = StateLedger()

    def parse_founder_intent(self, user_prompt: str) -> List[Dict[str, Any]]:
        """
        [Founder Intent Parsing]
        사용자의 자연어 요구사항을 JSON 구조의 DAG 세부 태스크 리스트로 분해
        """
        tasks = []
        timestamp_id = datetime.now().strftime('%Y%m%d%H%M%S')
        
        # 의도 분석 (키워드 및 도메인 매핑)
        # 일반 질문("전체"/방산 무관 문의 등)이 자동으로 주문 실행으로 이어지지 않도록,
        # 도메인 매칭(is_stock/is_defense)과 주문 실행 의도(is_order_intent)를 분리한다.
        # 2026-09-12 실제 텔레그램 테스트에서 "종목" 같은 흔한 표현이 빠져 있어 분류 실패가
        # 발생함을 확인 - 명확히 주식/방산 도메인인 일반 명사를 보강한다(과도하게 일반적인
        # 단어(예: 단순 "분석")는 무관한 요청까지 끌어들일 수 있어 제외).
        is_stock = any(k in user_prompt for k in [
            "주식", "매매", "수급", "포트폴리오", "트레이딩", "삼성전자", "퀀트", "가격", "재무",
            "종목", "차트", "시황", "펀더멘털", "밸류에이션", "실적", "공시", "코스피", "코스닥"
        ])
        is_defense = any(k in user_prompt for k in [
            "방산", "KAI", "항공", "KF-21", "FA-50", "DAPA", "방사청", "국방", "한화에어로", "전투기", "무기체계"
        ])
        is_order_intent = any(k in user_prompt for k in ["매수", "매도", "주문", "리밸런싱 실행", "체결"])

        if is_stock or ("전체" in user_prompt and is_stock):
            real_universe = self.dev_orchestrator.quant_trader.get_real_universe(limit=5)
            tasks.append({
                "task_id": f"TASK_{timestamp_id}_DEV_01",
                "domain": "dev_orchestrator",
                "title": "주식 퀀트 팩터 분석" + (" 및 포트폴리오 리밸런싱 주문 실행" if is_order_intent else " (분석 전용, 주문 미실행)"),
                "priority": "HIGH",
                "deadline": "IMMEDIATE",
                "status": "PENDING",
                "payload": {
                    "universe": real_universe,
                    "execute_orders": is_order_intent
                }
            })

        if is_defense or ("전체" in user_prompt and is_defense):
            tasks.append({
                "task_id": f"TASK_{timestamp_id}_CONTENT_02",
                "domain": "content_orchestrator",
                "title": "KAI 및 항공/방산 인텔리전스 수집, 중복 필터링 및 3줄 전략 요약 발행",
                "priority": "HIGH",
                "deadline": "IMMEDIATE",
                "status": "PENDING",
                "payload": {}
            })

        if not tasks:
            logger.info(f"[L1 PM Intent Parsing] 주식/방산 도메인으로 분류되지 않은 요청 - 실행 태스크 생성 안 함: {user_prompt[:80]!r}")

        for t in tasks:
            self.ledger.upsert("tasks", t["task_id"], t)
        self.tasks.extend(tasks)
        logger.info(f"[L1 PM Intent Parsing] {len(tasks)}개의 실행 DAG 태스크 분해 완료")
        return tasks

    async def execute_handoff(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        [Execution Handoff]
        분해된 태스크를 L1-A(Dev) 또는 L1-B(Content)로 라우팅하여 실행
        """
        domain = task.get("domain")
        task_id = task.get("task_id")
        logger.info(f"[L1 Handoff] 태스크 '{task_id}' -> '{domain}' 오케스트레이터로 전달")
        
        task["status"] = "RUNNING"
        self.ledger.upsert("tasks", task_id, task)
        result = {}

        try:
            if domain == "dev_orchestrator":
                universe = task["payload"].get("universe", [])
                execute_orders = task["payload"].get("execute_orders", False)
                result = await self.dev_orchestrator.run_trading_pipeline(universe, execute_orders=execute_orders)
            elif domain == "content_orchestrator":
                result = await self.content_orchestrator.run_defense_intelligence_cycle()
            else:
                raise ValueError(f"알 수 없는 도메인 오케스트레이터: {domain}")

            task["status"] = "COMPLETED"
            task["result"] = result
        except Exception as e:
            task["status"] = "FAILED"
            task["error"] = str(e)
            logger.error(f"[L1 Handoff 실패] {task_id} 오류 발생: {e}")
            
            # Auto-Heal 루프 발동
            if self.auto_heal:
                logger.info(f"[L1 PM Auto-Heal] {task_id} 자가 고도화 패치 루프 발동")
                heal_res = self.dev_orchestrator.self_healing_loop(e)
                task["healing_record"] = heal_res

        self.ledger.upsert("tasks", task_id, task)
        return task

    def execution_qa(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        [Execution QA]
        하위 오케스트레이터의 실행 결과 무결성 및 품질 검증 후 최종 승인
        """
        task_id = task.get("task_id")
        status = task.get("status")
        result = task.get("result", {})
        
        qa_passed = False
        notes = []

        if status == "COMPLETED" and result.get("status") == "SUCCESS":
            qa_passed = True
            notes.append("모든 데이터 무결성 및 실행 파이프라인 정상 완수.")
        elif task.get("healing_record", {}).get("is_auto_merged"):
            qa_passed = True
            notes.append("실패 발생했으나 Self-Healing 패치 자동 머지 성공으로 안정성 복원.")
        else:
            notes.append(f"QA 실패: {task.get('error', '결과 누락')}")

        qa_report = {
            "task_id": task_id,
            "qa_passed": qa_passed,
            "notes": notes,
            "reviewed_by": self.persona,
            "timestamp": datetime.now().isoformat()
        }
        task["qa_report"] = qa_report
        logger.info(f"[L1 QA 판정] {task_id} -> {'합격(PASSED)' if qa_passed else '불합격(FAILED)'} ({', '.join(notes)})")
        return qa_report

    async def run_autonomous_loop(self, prompt: str = "전체 시스템 파이프라인 자동 가동"):
        """전체 자율 오케스트레이션 루프 실행"""
        print(f"\n==================================================================")
        print(f"  Project Antigravity V2: 총괄 PM '{self.persona}' 가동")
        print(f"  Auto-Heal: {self.auto_heal} | System Mode: FULL-AUTONOMOUS")
        print(f"==================================================================\n")
        
        # 1. 자연어 의도 파싱
        tasks = self.parse_founder_intent(prompt)
        
        # 2. Handoff & 실행
        for task in tasks:
            await self.execute_handoff(task)
            
            # 3. QA 검증
            self.execution_qa(task)

        print("\n==================================================================")
        print("  [L1 PM] 모든 DAG 태스크 실행 및 최종 QA 검증 완료")
        print("==================================================================\n")
        return self.tasks

def main():
    parser = argparse.ArgumentParser(description="Antigravity L1 Master Orchestrator")
    parser.add_argument("--mode", type=str, default="full-autonomous", help="실행 모드 (full-autonomous | test)")
    parser.add_argument("--auto-heal", type=bool, default=True, help="자가 복구 패치 활성화 여부")
    parser.add_argument("--task", type=str, default="전체 주식 퀀트 및 방산 인텔리전스 파이프라인 가동", help="사용자 의도 프롬프트")
    
    args = parser.parse_args()
    
    pm = L1PMOwner(auto_heal=args.auto_heal)
    asyncio.run(pm.run_autonomous_loop(args.task))

if __name__ == "__main__":
    main()
