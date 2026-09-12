"""
Project Antigravity: 목표 완료 여부의 이중 AI 검증.

소유자 원칙(2026-09-12): 무료 모델로 시작하되, 중요한 판단(완료 판정 등)은 더 강한 모델로
확인한다. 필요하면 유료 API 키를 추가한다.

핵심 목표(GoalsRegistry.is_key_goal=True)는 서로 다른 검증자 2명이 모두 "100% 완료"라고
판정해야만 COMPLETED로 종결된다. 이 모듈은 llm_client의 provider 강제 지정 기능
(chat_completion_with_meta(provider=...))을 써서 실제로 서로 다른 두 모델이 응답하도록
보장한다 - 캐스케이드를 그냥 두 번 부르면 매번 같은 1순위 provider가 응답해 "이중검증"이
사실상 같은 모델을 두 번 묻는 것이 되기 때문이다.

기본 검증자 쌍은 gemini/deepseek(둘 다 이 환경에 실제 키가 있는 provider, 2026-09-12
확인). 유료 키를 추가하면 DEFAULT_VERIFIER_PROVIDERS 환경변수나 verify() 호출의
provider_pair 인자로 더 강한 모델(예: openai)을 검증자로 바꿔 쓸 수 있다 - 코드 변경 없이.
"""

import os
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from llm_client import AntigravityLLMClient
from memory.llm_usage_ledger import LLMUsageLedger
from memory.goals_registry import GoalsRegistry, default_registry, VERDICT_COMPLETE, VERDICT_INCOMPLETE

logger = logging.getLogger("goal_verification")

DEFAULT_VERIFIER_PROVIDERS: Tuple[str, str] = (
    os.getenv("GOAL_VERIFIER_1", "gemini"),
    os.getenv("GOAL_VERIFIER_2", "deepseek"),
)


def _build_verdict_prompt(goal: Dict[str, Any], evidence: str) -> str:
    return (
        "다음 목표가 제시된 증거만으로 100% 완료됐다고 볼 수 있는지 엄격하게 판정해줘. "
        "조금이라도 미확인·부분완료·추정치·단일 실행 결과뿐인 부분이 있으면 완료로 보지 마.\n\n"
        f"목표: {goal['title']}\n설명: {goal['description']}\n\n"
        f"증거:\n{evidence}\n\n"
        "다른 텍스트 없이 정확히 이 JSON 형식으로만 답해줘:\n"
        '{"verdict": "COMPLETE_100 또는 NOT_COMPLETE", "confidence": 0.0에서 1.0 사이 숫자, "reason": "한 문장 근거"}'
    )


def _parse_verdict(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    try:
        start = text.index("{")
        end = text.rindex("}") + 1
        data = json.loads(text[start:end])
    except Exception:
        return None
    if data.get("verdict") not in (VERDICT_COMPLETE, VERDICT_INCOMPLETE):
        return None
    return data


class GoalVerifier:
    def __init__(
        self,
        llm_client: Optional[AntigravityLLMClient] = None,
        usage_ledger: Optional[LLMUsageLedger] = None,
        registry: Optional[GoalsRegistry] = None,
    ):
        self.llm_client = llm_client or AntigravityLLMClient()
        self.usage_ledger = usage_ledger or LLMUsageLedger()
        self.registry = registry or default_registry

    def _ask(self, prompt: str, provider: str) -> Tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
        result = self.llm_client.chat_completion_with_meta(
            messages=[{"role": "user", "content": prompt}],
            provider=provider,
            temperature=0.0,
            max_tokens=300
        )
        self.usage_ledger.record("goal_verification", result)
        if result.get("is_fallback"):
            logger.warning(f"목표 검증용 {provider} 호출 실패 - 이 검증자의 판정 보류")
            return None, result
        return _parse_verdict(result.get("content", "")), result

    def verify(
        self,
        goal_id: str,
        evidence: str,
        provider_pair: Optional[Tuple[str, str]] = None
    ) -> Dict[str, Any]:
        """서로 다른 두 provider에게 같은 증거로 완료 여부를 독립적으로 물어보고,
        각 판정을 GoalsRegistry에 기록한다. 둘 다 COMPLETE_100이면 목표가 COMPLETED로
        종결된다(GoalsRegistry.record_verification의 게이트)."""
        goal = self.registry.get_goal(goal_id)
        if not goal:
            raise ValueError(f"알 수 없는 goal_id: {goal_id}")
        if not goal["is_key_goal"]:
            logger.warning(f"{goal_id}는 핵심 목표(is_key_goal)가 아니므로 이중검증 게이트를 적용하지 않는다")

        providers = provider_pair or DEFAULT_VERIFIER_PROVIDERS
        prompt = _build_verdict_prompt(goal, evidence)

        outcomes: List[Dict[str, Any]] = []
        for provider in providers:
            parsed, raw = self._ask(prompt, provider)
            if parsed is None:
                outcomes.append({"provider": provider, "verdict": None, "note": "판정 실패/형식불일치"})
                continue
            self.registry.record_verification(
                goal_id,
                verifier=f"{provider}:{raw.get('model')}",
                verdict=parsed["verdict"],
                confidence=parsed.get("confidence"),
                evidence=parsed.get("reason", "")
            )
            outcomes.append({"provider": provider, "verdict": parsed["verdict"], "confidence": parsed.get("confidence")})

        updated_goal = self.registry.get_goal(goal_id)
        return {"goal_id": goal_id, "status": updated_goal["status"], "outcomes": outcomes}
