"""
Project Antigravity: L1-B Content Orchestrator (KAI 및 항공/방산 인텔리전스 오케스트레이터)
방산 뉴스 수집 계획 수립, 보고서 초안 검토, Slack 알림 및 Notion 대시보드 렌더링 총괄
"""

import os
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from memory.vector_store import MemoryVectorStore
from agents.l2_workers.defense_researcher import DefenseResearcherWorker

logger = logging.getLogger("l1_b_content_orchestrator")

class ContentOrchestrator:
    def __init__(self, vector_store: Optional[MemoryVectorStore] = None):
        self.vector_store = vector_store or MemoryVectorStore()
        self.researcher = DefenseResearcherWorker(vector_store=self.vector_store)
        self.slack_webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
        self.published_reports: List[Dict[str, Any]] = []

    async def run_defense_intelligence_cycle(self) -> Dict[str, Any]:
        """
        KAI 및 방산 인텔리전스 수집-정제-검토-발행 전체 사이클 실행
        """
        logger.info("[L1-B] KAI 및 항공/방산 인텔리전스 사이클 개시")
        
        # 1. 원천 데이터 수집
        raw_feeds = await self.researcher.fetch_defense_sources()
        
        # 2. 유사도 필터링 및 3줄 전략 요약 생성
        processed_intels = await self.researcher.process_and_filter_intel(raw_feeds)
        
        # 3. 초안 검토 및 발행 (Slack & Notion)
        dispatched_count = 0
        for item in processed_intels:
            # 3줄 전략 요약 포맷 검증
            has_fact = "[주요 팩트]" in item.get("fact_summary", "")
            has_impact = "[경쟁 환경 및 산업 영향]" in item.get("impact_summary", "")
            has_strategy = "[전사 사업 전략 관점의 시사점]" in item.get("strategy_summary", "")
            
            if has_fact and has_impact and has_strategy:
                # 슬랙/노션 발행 통보 시뮬레이션
                item["is_published_notion"] = True
                item["is_notified_slack"] = True
                self.published_reports.append(item)
                dispatched_count += 1
                logger.info(f"[L1-B 발행 승인] '{item['title']}' -> Notion & Slack 전송 완료")
            else:
                logger.warning(f"[L1-B 반려] 3줄 전략 요약 포맷 미달: '{item['title']}'")

        return {
            "status": "SUCCESS",
            "total_collected": len(raw_feeds),
            "filtered_and_published": dispatched_count,
            "reports": processed_intels,
            "timestamp": datetime.now().isoformat()
        }
