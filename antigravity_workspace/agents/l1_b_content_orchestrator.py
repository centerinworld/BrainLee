"""
Project Antigravity: L1-B Content Orchestrator (KAI 및 항공/방산 인텔리전스 오케스트레이터)
방산 뉴스 수집 계획 수립, 보고서 초안 검토, Slack 알림 및 Notion 대시보드 렌더링 총괄
"""

import os
import hashlib
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from memory.vector_store import MemoryVectorStore
from memory.state_ledger import StateLedger
from agents.l2_workers.defense_researcher import DefenseResearcherWorker

logger = logging.getLogger("l1_b_content_orchestrator")

class ContentOrchestrator:
    def __init__(
        self,
        vector_store: Optional[MemoryVectorStore] = None,
        ledger: Optional[StateLedger] = None,
        llm_client=None,
        usage_ledger=None
    ):
        self.vector_store = vector_store or MemoryVectorStore()
        # llm_client/usage_ledger는 테스트에서 실제 네트워크 호출 없이 주입할 수 있게 전달만 한다.
        self.researcher = DefenseResearcherWorker(
            vector_store=self.vector_store, llm_client=llm_client, usage_ledger=usage_ledger
        )
        self.slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self.published_reports: List[Dict[str, Any]] = []
        # A08: 발행 준비 상태를 재시작에도 남도록 영속 원장에 저장해, 같은 항목을 다시
        # "발행 준비"로 중복 집계/재처리하지 않는다.
        self.ledger = ledger or StateLedger()

    @staticmethod
    def _delivery_key(item: Dict[str, Any]) -> str:
        return hashlib.sha256(f"{item.get('source', '')}::{item.get('title', '')}".encode("utf-8")).hexdigest()

    async def run_defense_intelligence_cycle(self) -> Dict[str, Any]:
        """
        KAI 및 방산 인텔리전스 수집-정제-검토-발행 전체 사이클 실행
        """
        logger.info("[L1-B] KAI 및 항공/방산 인텔리전스 사이클 개시")
        
        # 1. 원천 데이터 수집
        raw_feeds = await self.researcher.fetch_defense_sources()
        
        # 2. 유사도 필터링 및 3줄 전략 요약 생성
        processed_intels = await self.researcher.process_and_filter_intel(raw_feeds)
        
        # 3. 초안 검토 및 발행 준비 (Slack/Notion 실제 전송 연동은 미구현 - 아래에서 상태를 사실대로 표시)
        prepared_count = 0
        for item in processed_intels:
            # 3줄 전략 요약 포맷 검증
            has_fact = "[주요 팩트]" in item.get("fact_summary", "")
            has_impact = "[경쟁 환경 및 산업 영향]" in item.get("impact_summary", "")
            has_strategy = "[전사 사업 전략 관점의 시사점]" in item.get("strategy_summary", "")

            if has_fact and has_impact and has_strategy:
                delivery_key = self._delivery_key(item)
                if self.ledger.exists("published_reports", delivery_key):
                    # 재시작 여부와 무관하게 같은 (source, title) 조합은 다시 "새로 준비됨"으로
                    # 집계하지 않는다 - 향후 실제 Slack/Notion 전송이 구현되면 중복 전송 방지의 기반이 된다.
                    item["delivery_status"] = "ALREADY_PREPARED_SKIPPED"
                    logger.info(f"[L1-B 중복 방지] '{item['title']}' -> 이미 발행 준비된 항목, 재처리하지 않음")
                    continue

                # 포맷 검증만 통과한 상태. 실제 Slack/Notion API 호출이 구현되어 있지 않으므로
                # 제공자 메시지 ID/응답 없이는 "전송 완료"로 표시하지 않는다.
                item["delivery_status"] = "PREPARED_NOT_SENT"
                item["is_published_notion"] = False
                item["is_notified_slack"] = False
                self.published_reports.append(item)
                self.ledger.upsert("published_reports", delivery_key, item)
                prepared_count += 1
                logger.info(f"[L1-B 포맷 검증 통과] '{item['title']}' -> 발행 대기(PREPARED_NOT_SENT), 실제 Notion/Slack 전송 미구현")
            else:
                item["delivery_status"] = "REJECTED_FORMAT"
                logger.warning(f"[L1-B 반려] 3줄 전략 요약 포맷 미달: '{item['title']}'")

        return {
            "status": "SUCCESS",
            "total_collected": len(raw_feeds),
            "filtered_and_prepared": prepared_count,
            "reports": processed_intels,
            "timestamp": datetime.now().isoformat()
        }
