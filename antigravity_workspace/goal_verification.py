"""
Project Antigravity: 목표 완료 여부의 이중 AI 검증.

소유자 원칙(2026-09-12): 무료 모델로 시작하되, 중요한 판단(완료 판정 등)은 더 강한 모델로
확인한다. 필요하면 유료 API 키를 추가한다.

2026-09-12 사고 재확인: stock_dashboard의 codex_pipeline_orchestrator.py가 저가 모델
(Qwen, 이 코드베이스에서는 llm_client의 grok tier)에게 최종 백테스트 결과 산출과 사실상의
최종 승인까지 맡겼다가, 그 모델이 실행 능력이 없어 결과를 통째로 지어냈다. 소유자 지시:
저사양 모델이 고사양 모델의 몫(여기서는 목표 완료 판정)을 이어받지 못하게 구조적으로 막을 것.

이 모듈은 이제 llm_client.PROVIDER_TIER상 "verified" 등급 provider만 검증자로 쓴다.
"draft" 등급(gemini/grok/deepseek)은 목표 완료 판정에 아예 쓰이지 않는다 - 캐스케이드가
그 등급으로 저하되어 응답해도 llm_client.chat_completion_with_meta(min_tier="verified")가
거부한다. 검증자 2명은 서로 다른 provider여야 하며, "verified" 등급 provider가 2개
미만이면(2026-09-12 현재 openai 1개뿐) 조용히 낮은 등급으로 대신하지 않고 명시적으로
검증 불가 상태를 반환한다 - 유료 키를 추가하거나(GOAL_VERIFIER_1/2 env, provider_pair
인자) 로컬 Claude/Codex CLI 연동이 추가될 때까지는 핵심 목표가 COMPLETED로 종결될 수 없다.
이것은 버그가 아니라 의도된 동작이다.
"""

import os
import json
import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple

from llm_client import AntigravityLLMClient
from memory.llm_usage_ledger import LLMUsageLedger
from memory.goals_registry import GoalsRegistry, default_registry, VERDICT_COMPLETE, VERDICT_INCOMPLETE

logger = logging.getLogger("goal_verification")

REQUIRED_VERIFIER_TIER = "verified"

# 2026-09-13 현재 Claude 구독 토큰을 사용할 수 없다는 소유자 지시에 따라 기본 쌍을
# 공식 Codex SDK와 기존 Codex CLI의 서로 다른 로컬 thread로 구성한다. 같은 공급자 계열이므로
# 독립 모델 이중검증으로 과장하지 않고 반환값에 same_vendor_distinct_sessions로 표시한다.
# draft provider로 낮추지는 않으며, 같은 evidence_hash와 기계 검사 게이트는 유지한다.
_env_v1 = os.getenv("GOAL_VERIFIER_1")
_env_v2 = os.getenv("GOAL_VERIFIER_2")
DEFAULT_VERIFIER_PROVIDERS: Tuple[str, str] = (_env_v1, _env_v2) if (_env_v1 and _env_v2) else ("codex_sdk", "codex_cli")


def _verified_tier_providers() -> List[str]:
    return [p for p, tier in AntigravityLLMClient.PROVIDER_TIER.items() if tier == REQUIRED_VERIFIER_TIER]


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
            min_tier=REQUIRED_VERIFIER_TIER,
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
        """기본적으로 Codex SDK와 Codex CLI의 별도 로컬 thread에 같은 증거를 물어보고,
        각 판정을 GoalsRegistry에 기록한다. 같은 공급자 계열의 서로 다른 세션이라는
        한계는 verification_independence에 명시한다.
        둘 다 COMPLETE_100이면 목표가 COMPLETED로 종결된다
        (GoalsRegistry.record_verification의 게이트, 같은 evidence_hash에 한함).

        저사양(draft 등급, gemini/grok/deepseek 전부 포함) provider는 검증자로 아예
        받아들이지 않는다 - 명시적으로 지정돼도 거부한다(2026-09-12 Qwen 사고,
        2026-09-13 "Deepseek도 100% 신뢰 못함" 지시 반영). Claude 토큰이 다시 사용
        가능해지면 GOAL_VERIFIER_2=claude_cli로 독립 공급자 검증을 복원할 수 있다."""
        goal = self.registry.get_goal(goal_id)
        if not goal:
            raise ValueError(f"알 수 없는 goal_id: {goal_id}")
        if not goal["is_key_goal"]:
            logger.warning(f"{goal_id}는 핵심 목표(is_key_goal)가 아니므로 이중검증 게이트를 적용하지 않는다")

        providers = provider_pair or DEFAULT_VERIFIER_PROVIDERS
        if providers is None:
            candidates = _verified_tier_providers()
            if len(candidates) < 2:
                logger.error(
                    f"이중검증 불가: '{REQUIRED_VERIFIER_TIER}' 등급 provider가 {len(candidates)}개뿐입니다"
                    f"({candidates}). 저사양 모델로 대신 검증하지 않습니다. GOAL_VERIFIER_1/2 "
                    "환경변수나 provider_pair로 명시하거나, 유료 API 키를 추가하세요."
                )
                return {
                    "goal_id": goal_id, "status": goal["status"], "outcomes": [],
                    "blocked_reason": "INSUFFICIENT_VERIFIED_PROVIDERS"
                }
            providers = (candidates[0], candidates[1])

        if len(set(providers)) < 2:
            logger.error(f"검증자 2명이 서로 다른 provider여야 합니다: {providers}")
            return {
                "goal_id": goal_id, "status": goal["status"], "outcomes": [],
                "blocked_reason": "VERIFIERS_NOT_DISTINCT"
            }

        for p in providers:
            tier = AntigravityLLMClient.PROVIDER_TIER.get(p, "draft")
            if tier != REQUIRED_VERIFIER_TIER:
                logger.error(f"'{p}'는 '{tier}' 등급이라 목표 완료 검증에 쓸 수 없습니다(최소 {REQUIRED_VERIFIER_TIER} 필요)")
                return {
                    "goal_id": goal_id, "status": goal["status"], "outcomes": [],
                    "blocked_reason": f"PROVIDER_TIER_TOO_LOW:{p}"
                }

        prompt = _build_verdict_prompt(goal, evidence)
        # 2026-09-12 검토 지적(정확함) 수정: 두 검증자가 반드시 "같은 증거"에 대해 판정한
        # 것으로 묶이도록 이 라운드의 evidence_hash를 한 번만 계산해 둘 다에게 공유한다.
        # 이전에는 GoalsRegistry가 검증자별 evidence 텍스트 없이 해시를 자체 계산해,
        # 서로 다른 시점/증거의 판정이 섞여 완료될 수 있었다.
        evidence_hash = hashlib.sha256(evidence.encode("utf-8")).hexdigest()

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
                evidence=parsed.get("reason", ""),
                evidence_hash=evidence_hash
            )
            outcomes.append({"provider": provider, "verdict": parsed["verdict"], "confidence": parsed.get("confidence")})

        updated_goal = self.registry.get_goal(goal_id)
        same_vendor = set(providers).issubset({"codex_sdk", "codex_cli"})
        return {
            "goal_id": goal_id,
            "status": updated_goal["status"],
            "outcomes": outcomes,
            "evidence_hash": evidence_hash,
            "verification_independence": (
                "same_vendor_distinct_sessions" if same_vendor else "distinct_providers"
            ),
        }
