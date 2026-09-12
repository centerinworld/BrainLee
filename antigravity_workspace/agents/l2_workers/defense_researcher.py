"""
Project Antigravity: L2 Worker - defense_researcher (codex ceo-briefing-platform 실데이터 연동 버전)
ceo-briefing-platform (ceo_briefing.db & Port 8011 API) 실시간 DAPA/국방부/KAI 피드 및 3줄 전략 요약 연동
"""

import os
import re
import sqlite3
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime

from llm_client import AntigravityLLMClient
from memory.llm_usage_ledger import LLMUsageLedger

logger = logging.getLogger("defense_researcher")

class DefenseResearcherWorker:
    def __init__(
        self,
        vector_store=None,
        codex_db_path: Optional[str] = None,
        llm_client: Optional[AntigravityLLMClient] = None,
        usage_ledger: Optional[LLMUsageLedger] = None
    ):
        self.vector_store = vector_store
        self.similarity_threshold = 0.85
        self.codex_db_path = codex_db_path or os.getenv(
            "CODEX_DB_PATH",
            "/Volumes/Realtek_NVME/AI System/codex/ceo-briefing-platform/data/ceo_briefing.db"
        )
        # 요약 생성이 실제 LLM을 부르도록 - 이전에는 아래 3줄 요약이 전부 키워드
        # if-elif 템플릿이었다(에이전트라는 이름이었지만 LLM을 호출한 적이 없었음).
        self.llm_client = llm_client or AntigravityLLMClient()
        self.usage_ledger = usage_ledger or LLMUsageLedger()

    async def fetch_defense_sources(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        codex ceo-briefing-platform (ceo_briefing.db)에서 실제 DAPA/국방부/KAI 피드 수집
        """
        await asyncio.sleep(0.02)
        
        if not os.path.exists(self.codex_db_path):
            logger.warning(f"ceo_briefing.db 파일 미발견: {self.codex_db_path}. 기본 폴백 피드 사용.")
            return [
                {
                    "source": "DAPA",
                    "title": "한국형 초음속 전투기 KF-21 최초 양산 계약 체결 및 납품 일정 공고",
                    "content": "방위사업청은 KAI(한국항공우주산업)와 1조 9,600억원 규모의 KF-21 블록1 최초 양산 계약을 체결했다.",
                    "published_at": datetime.now().isoformat()
                }
            ]

        try:
            conn = sqlite3.connect(self.codex_db_path)
            conn.row_factory = sqlite3.Row
            query = """
                SELECT id, source, title, summary, link, article_published_at, article_publisher, article_category
                FROM feed_items
                WHERE title IS NOT NULL AND title != ''
                ORDER BY id DESC
                LIMIT ?
            """
            rows = conn.execute(query, (limit,)).fetchall()
            conn.close()

            feeds = []
            for r in rows:
                feeds.append({
                    "id": r["id"],
                    "source": r["source"] or r["article_publisher"] or "DAPA/방산",
                    "title": r["title"].strip(),
                    "content": r["summary"] or r["title"],
                    "link": r["link"],
                    "published_at": r["article_published_at"] or datetime.now().isoformat()
                })
            return feeds
        except Exception as e:
            logger.error(f"ceo_briefing.db 수집 오류: {e}")
            return []

    def _template_summary(self, clean_title: str, title: str, source: str) -> Dict[str, str]:
        """LLM 호출이 전부 실패했을 때만 쓰는 키워드 기반 결정론적 폴백 (이전에는 이게 기본 동작이었다)."""
        if any(k in title for k in ["KF-21", "전투기", "항공", "KAI", "사천"]):
            fact = f"{clean_title} 관련 핵심 사업 및 양산/수출 진행 상황 확인 ({source})."
            impact = "국내 항공 완제기 독점 제조 경쟁력 강화 및 엔진/항전 협력업체 밸류체인 수혜 확대."
            strategy = "블록1 양산 일정 엄수와 유무인 복합체계(MUM-T) 및 파생형 기체 글로벌 마케팅 가속화."
        elif any(k in title for k in ["드론", "AI", "무인", "자폭"]):
            fact = f"{clean_title} 관련 첨단 무인기 및 국방 AI 전력화 동향 ({source})."
            impact = "전장 환경의 저비용·고효율 무인화 전환에 따른 신규 국방 R&D 소요 급증."
            strategy = "AI 기반 자율비행 및 유무인 연계 제어 소프트웨어 원천 기술 내재화 필요."
        elif any(k in title for k in ["수출", "폴란드", "루마니아", "사우디", "K9", "천궁"]):
            fact = f"{clean_title} 관련 글로벌 방산 수출 계약 및 협력 논의 ({source})."
            impact = "K-방산 브랜드 신뢰도 제고 및 현지 MRO/라이선스 생산 요구 대응력 부각."
            strategy = "현지 생산 거점 확보 및 나토(NATO) 무장 호환성을 무기로 추가 옵션 계약 유치."
        else:
            fact = f"{clean_title} 정책/기술/입찰 주요 공시 내용 ({source})."
            impact = "방산 산업 생태계 재편 및 정부 국방 획득 사업 참여 기회 창출."
            strategy = "신규 사업 제안서 준비 및 핵심 부품 국산화 로드맵 사전 정비."

        return {
            "fact_summary": f"[주요 팩트] {fact}",
            "impact_summary": f"[경쟁 환경 및 산업 영향] {impact}",
            "strategy_summary": f"[전사 사업 전략 관점의 시사점] {strategy}"
        }

    @staticmethod
    def _parse_three_line_summary(text: str) -> Optional[Dict[str, str]]:
        """LLM 응답에서 3개 필수 대괄호 헤더 라인을 추출한다. 형식이 안 맞으면 None
        (형식이 틀린 응답을 그대로 진짜 요약처럼 쓰지 않는다)."""
        if not text:
            return None
        patterns = {
            "fact_summary": r"\[주요 팩트\][^\n]*",
            "impact_summary": r"\[경쟁 환경 및 산업 영향\][^\n]*",
            "strategy_summary": r"\[전사 사업 전략 관점의 시사점\][^\n]*",
        }
        result = {}
        for key, pattern in patterns.items():
            m = re.search(pattern, text)
            if not m:
                return None
            result[key] = m.group(0).strip()
        return result

    def generate_three_line_strategy_summary(self, title: str, content: str, source: str) -> Dict[str, str]:
        """
        엄격한 3단계 전략 요약문 생성 (실제 LLM 호출):
        1. [주요 팩트]
        2. [경쟁 환경 및 산업 영향]
        3. [전사 사업 전략 관점의 시사점]
        전부 실패하거나 형식이 안 맞으면 키워드 템플릿으로 저하하고 data_source로 표시한다.
        """
        clean_title = title.replace("[", "").replace("]", "")
        prompt = (
            "다음 방산/항공 뉴스에 대해 정확히 3줄로 전략 요약을 작성해줘. "
            "각 줄은 반드시 아래 대괄호 헤더로 시작하고, 헤더당 정확히 한 문장만 써줘:\n"
            "[주요 팩트] <핵심 사실>\n"
            "[경쟁 환경 및 산업 영향] <산업/경쟁 영향>\n"
            "[전사 사업 전략 관점의 시사점] <전략적 시사점>\n\n"
            f"출처: {source}\n제목: {clean_title}\n본문: {content[:1000]}"
        )
        result = self.llm_client.chat_completion_with_meta(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=400
        )
        self.usage_ledger.record("defense_researcher.generate_three_line_strategy_summary", result)

        if not result.get("is_fallback"):
            parsed = self._parse_three_line_summary(result.get("content", ""))
            if parsed:
                parsed["data_source"] = f"llm_generated:{result.get('provider')}"
                return parsed
            logger.warning(f"[{result.get('provider')}] 응답이 3줄 형식과 안 맞음 - 템플릿 폴백: '{title[:40]}'")
        else:
            logger.warning(f"모든 LLM provider 실패 - 템플릿 폴백: '{title[:40]}'")

        summary = self._template_summary(clean_title, title, source)
        summary["data_source"] = "template_fallback"
        return summary

    async def process_and_filter_intel(self, raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        코사인 유사도 0.85 이상 중복 제거 및 3줄 전략 요약 정제 파이프라인
        """
        processed_results = []
        for item in raw_items:
            title = item["title"]
            content = item["content"]
            source = item["source"]
            
            # 1. 임베딩 및 중복 검사
            if self.vector_store:
                emb = self.vector_store.get_embedding(f"{title} {content}")
                is_dup, matched, sim = self.vector_store.check_duplicate(
                    emb, self.similarity_threshold, text=f"{title} {content}"
                )
                if is_dup:
                    logger.info(f"중복/노이즈 기사 필터링 차단 (유사도 {sim:.2f} >= {self.similarity_threshold}): '{title}'")
                    continue
            else:
                emb = None

            # 2. 3줄 전략 요약 생성
            summary = self.generate_three_line_strategy_summary(title, content, source)

            # 3. 데이터베이스 적재
            saved_doc = None
            if self.vector_store:
                saved_doc = self.vector_store.insert_defense_intelligence(
                    source=source,
                    title=title,
                    raw_content=content,
                    fact_summary=summary["fact_summary"],
                    impact_summary=summary["impact_summary"],
                    strategy_summary=summary["strategy_summary"],
                    sentiment_score=0.85,
                    embedding=emb
                )

            processed_results.append({
                "id": saved_doc["id"] if saved_doc else item.get("id", len(processed_results) + 1),
                "source": source,
                "title": title,
                "raw_content": content,
                "link": item.get("link", ""),
                **summary,
                "sentiment_score": 0.85,
                "created_at": item.get("published_at", datetime.now().isoformat())
            })

        return processed_results
