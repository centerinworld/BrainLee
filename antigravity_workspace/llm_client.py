"""
Project Antigravity: 통합 클라우드 LLM 클라이언트 (DeepSeek / Claude / OpenAI)
로컬 맥미니 CPU/RAM 점유율 0% 유지 및 초저비용 고성능(DeepSeek-V3/R1) API 호출
"""

import os
import json
import logging
import subprocess
import tempfile
from typing import Dict, Any, List, Optional
import requests

try:
    from config.env_loader import LOADED_ENV_FILES
except ImportError:
    pass

logger = logging.getLogger("llm_client")

class AntigravityLLMClient:
    def __init__(self):
        # 1차 (1순위): Google Gemini API (무료 Gemini 우선)
        self.gemini_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_AI_STUDIO_KEY")
            or os.getenv("GOOGLE_API_KEY")
            or ""
        ).strip('"').strip("'")
        self.gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

        # 2차 (2순위): Groq / xAI Grok API (키 입력 시 즉시 2차 엔진 가동)
        self.grok_key = (
            os.getenv("GROK_API_KEY")
            or os.getenv("GROQ_API_KEY")
            or os.getenv("XAI_API_KEY")
            or ""
        ).strip('"').strip("'")
        
        # Groq (gsk_...) vs xAI (xai-...) 자동 분기
        if self.grok_key.startswith("gsk_"):
            self.grok_base_url = os.getenv("GROK_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
            self.grok_model = os.getenv("GROK_MODEL", "qwen/qwen3.8-27b")
            self.grok_label = "Groq LPU"
        else:
            self.grok_base_url = os.getenv("GROK_BASE_URL", "https://api.x.ai/v1").rstrip("/")
            self.grok_model = os.getenv("GROK_MODEL", "grok-2-latest")
            self.grok_label = "xAI Grok"

        # 3차 (3순위): DeepSeek API (초저비용 $0.14/1M 백업)
        self.deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip('"').strip("'")
        self.deepseek_base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

        # 4차 (비상 안전망): OpenAI API
        self.openai_key = (
            os.getenv("OpenAI_GPT_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        ).strip('"').strip("'")

        # 로컬 CLI (2026-09-13 추가): 소유자 지시 - 중요한 판단은 저사양 API 모델이
        # 아니라 실제 구독 중인 Codex/Claude가 하도록 한다. 별도 API 키 없이 기존
        # ChatGPT/Claude 구독 인증을 그대로 쓴다. 경로는 이 환경에서 실측 확인했다
        # (`codex login status` -> "Logged in using ChatGPT").
        self.codex_cli_path = os.getenv(
            "CODEX_CLI_PATH", "/Applications/ChatGPT.app/Contents/Resources/codex"
        )
        self.claude_cli_path = os.getenv("CLAUDE_CLI_PATH", "/opt/homebrew/bin/claude")

        self.last_provider_used = "NONE"

    def chat_completion(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500
    ) -> str:
        """하위 호환용 - 응답 텍스트만 반환. 비용/사용량 추적이 필요하면
        chat_completion_with_meta()를 쓴다."""
        return self.chat_completion_with_meta(messages, model, temperature, max_tokens)["content"]

    def _try_gemini(self, messages, model, temperature, max_tokens) -> Optional[Dict[str, Any]]:
        if not (self.gemini_key and not self.gemini_key.startswith("your_")):
            return None
        try:
            g_model = model if model and "gemini" in model else self.gemini_model
            headers = {"Authorization": f"Bearer {self.gemini_key}", "Content-Type": "application/json"}
            payload = {"model": g_model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            res = requests.post(
                "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                headers=headers, json=payload, timeout=15
            )
            if res.status_code == 200:
                data = res.json()
                choices = data.get("choices", [])
                if choices and "message" in choices[0]:
                    self.last_provider_used = f"Google Gemini ({g_model})"
                    logger.info(f"[Gemini 성공] ({g_model}) 응답 완료")
                    return {
                        "content": choices[0]["message"]["content"],
                        "provider": "gemini", "model": g_model,
                        "usage": data.get("usage"), "is_fallback": False
                    }
            elif res.status_code == 429:
                logger.warning("[LLM 라우팅] ⚠️ Google Gemini 무료 할당량(429 Quota) 초과")
            else:
                logger.warning(f"Google Gemini API 반환 코드: {res.status_code}")
        except Exception as e:
            logger.warning(f"Google Gemini 호출 실패: {e}")
        return None

    def _try_grok(self, messages, model, temperature, max_tokens) -> Optional[Dict[str, Any]]:
        if not (self.grok_key and not self.grok_key.startswith("your_")):
            return None
        try:
            target_grok_model = model if model and "grok" in model else self.grok_model
            headers = {"Authorization": f"Bearer {self.grok_key}", "Content-Type": "application/json"}
            payload = {"model": target_grok_model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            res = requests.post(f"{self.grok_base_url}/chat/completions", headers=headers, json=payload, timeout=25)
            if res.status_code == 200:
                data = res.json()
                self.last_provider_used = f"{self.grok_label} ({target_grok_model})"
                logger.info(f"[{self.grok_label} 성공] ({target_grok_model}) 응답 완료")
                return {
                    "content": data["choices"][0]["message"]["content"],
                    "provider": "grok", "model": target_grok_model,
                    "usage": data.get("usage"), "is_fallback": False
                }
            elif res.status_code == 429:
                logger.warning(f"[LLM 라우팅] ⚠️ {self.grok_label} 사용량 초과(429)")
            else:
                logger.warning(f"{self.grok_label} API 반환 코드: {res.status_code}")
        except Exception as e:
            logger.warning(f"{self.grok_label} 호출 실패: {e}")
        return None

    def _try_deepseek(self, messages, model, temperature, max_tokens) -> Optional[Dict[str, Any]]:
        if not (self.deepseek_key and not self.deepseek_key.startswith("your_")):
            return None
        try:
            target_model = model if model and "deepseek" in model else self.deepseek_model
            headers = {"Authorization": f"Bearer {self.deepseek_key}", "Content-Type": "application/json"}
            payload = {"model": target_model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
            res = requests.post(f"{self.deepseek_base_url}/chat/completions", headers=headers, json=payload, timeout=30)
            if res.status_code == 200:
                data = res.json()
                self.last_provider_used = f"DeepSeek ({target_model})"
                logger.info("[DeepSeek 성공] API 응답 완료 (초저비용: $0.14/1M)")
                return {
                    "content": data["choices"][0]["message"]["content"],
                    "provider": "deepseek", "model": target_model,
                    "usage": data.get("usage"), "is_fallback": False
                }
            else:
                logger.warning(f"DeepSeek API 반환 오류: {res.status_code}")
        except Exception as e:
            logger.error(f"DeepSeek API 호출 실패: {e}")
        return None

    def _try_openai(self, messages, model, temperature, max_tokens) -> Optional[Dict[str, Any]]:
        if not (self.openai_key and not self.openai_key.startswith("your_")):
            return None
        try:
            import openai
            client = openai.OpenAI(api_key=self.openai_key)
            target_model = model if model and ("gpt" in model or "o1" in model or "o3" in model) else "gpt-4o-mini"
            res = client.chat.completions.create(
                model=target_model, messages=messages, temperature=temperature, max_tokens=max_tokens
            )
            self.last_provider_used = f"OpenAI ({target_model})"
            usage = getattr(res, "usage", None)
            return {
                "content": res.choices[0].message.content,
                "provider": "openai", "model": target_model,
                "usage": usage.model_dump() if usage else None, "is_fallback": False
            }
        except Exception as e:
            logger.error(f"OpenAI API 호출 실패: {e}")
        return None

    def _try_codex_cli(self, messages, model, temperature, max_tokens) -> Optional[Dict[str, Any]]:
        """실제 ChatGPT 구독 인증(API 키 아님)으로 로컬 Codex CLI를 비대화형·읽기전용
        샌드박스에서 호출한다. 2026-09-13 이 환경에서 실제로 성공 확인
        (`codex login status` -> "Logged in using ChatGPT", 응답/토큰 사용량까지 수신).
        읽기 전용 샌드박스(-s read-only)라 이 호출로는 파일/셸에 어떤 변경도 할 수 없다 -
        중요 판단을 맡기는 이유가 바로 이 실행 능력(파일 검사 등)이지만, 부작용 없는
        읽기 전용 판단으로 한정한다."""
        if not os.path.exists(self.codex_cli_path):
            return None
        prompt = messages[-1]["content"] if messages else ""
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as tmp:
                tmp_path = tmp.name
            res = subprocess.run(
                [self.codex_cli_path, "exec", "-s", "read-only", "--json", "-o", tmp_path, prompt],
                capture_output=True, text=True, timeout=120
            )
            if res.returncode != 0:
                logger.warning(f"codex_cli 실행 실패(exit={res.returncode}): {res.stderr[:300]}")
                return None
            usage = None
            for line in res.stdout.splitlines():
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if event.get("type") == "turn.completed":
                    u = event.get("usage", {}) or {}
                    usage = {
                        "prompt_tokens": u.get("input_tokens"),
                        "completion_tokens": u.get("output_tokens"),
                        "total_tokens": (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0)
                    }
            with open(tmp_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
            if not content:
                logger.warning("codex_cli 응답이 비어있음")
                return None
            self.last_provider_used = "Codex CLI (ChatGPT 구독, read-only)"
            logger.info(f"[Codex CLI 성공] 토큰 사용: {usage}")
            return {
                "content": content, "provider": "codex_cli", "model": "codex-cli-subscription",
                "usage": usage, "is_fallback": False
            }
        except subprocess.TimeoutExpired:
            logger.warning("codex_cli 호출 시간 초과(120초)")
            return None
        except Exception as e:
            logger.warning(f"codex_cli 호출 오류: {e}")
            return None
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def _try_claude_cli(self, messages, model, temperature, max_tokens) -> Optional[Dict[str, Any]]:
        """로컬 Claude Code CLI 구독 인증 호출.

        **중요한 한계(2026-09-13 확인)**: Claude Code는 다른 Claude Code 세션 내부에서
        실행되는 것을 명시적으로 거부한다("Nested sessions share runtime resources and
        will crash all active sessions"). 즉 이 함수를 Claude Code 세션 프로세스 안에서
        호출하면(예: 이 antigravity_workspace 코드를 Claude Code가 직접 실행 중일 때)
        아래 except에서 실패를 잡아 정직하게 is_fallback을 반환한다 - 크래시는 아니지만
        성공도 아니다. 이 provider는 Claude Code가 아닌 독립 프로세스(예: launchd로 뜨는
        검증 스케줄러)에서 호출될 때만 실제로 성공한다. 그 환경에서의 실제 성공 사례는
        이번 세션에서 확인하지 못했다 - 문서화된 CLI 계약(`claude -p --output-format
        json`)대로 작성했으나 독립 실행 검증이 필요하다."""
        if not os.path.exists(self.claude_cli_path):
            return None
        prompt = messages[-1]["content"] if messages else ""
        try:
            res = subprocess.run(
                [
                    self.claude_cli_path, "-p", prompt, "--output-format", "json",
                    "--disallowedTools", "Bash Edit Write NotebookEdit WebFetch WebSearch Task"
                ],
                capture_output=True, text=True, timeout=120
            )
            if res.returncode != 0:
                logger.warning(f"claude_cli 실행 실패(exit={res.returncode}): {res.stderr[:300]}")
                return None
            data = json.loads(res.stdout)
            content = data.get("result") or ""
            if not content:
                logger.warning("claude_cli 응답이 비어있음")
                return None
            self.last_provider_used = "Claude CLI (구독)"
            return {
                "content": content, "provider": "claude_cli",
                "model": data.get("model", "claude-cli-subscription"),
                "usage": data.get("usage"), "is_fallback": False
            }
        except subprocess.TimeoutExpired:
            logger.warning("claude_cli 호출 시간 초과(120초)")
            return None
        except Exception as e:
            logger.warning(f"claude_cli 호출 오류: {e}")
            return None

    # provider 이름 -> 시도 함수. 순서가 기본 캐스케이드 우선순위다.
    # codex_cli/claude_cli는 이 기본 캐스케이드에 넣지 않는다 - 실제 구독 쿼터를 쓰고
    # 호출당 수만 토큰·수십 초가 드는 느린 경로라, 뉴스 요약·패치 초안 같은 흔한 draft
    # 작업마다 자동으로 타면 안 된다. provider="codex_cli"/"claude_cli"로 명시 호출할
    # 때만 쓴다(목표 완료 검증 등 정말 중요한 판단).
    _PROVIDER_TRIERS = ("gemini", "grok", "deepseek", "openai")
    _EXPLICIT_ONLY_PROVIDERS = ("codex_cli", "claude_cli")

    # 2026-09-12 사고: stock_dashboard의 codex_pipeline_orchestrator.py가 "저가 모델
    # (Qwen, 이 코드베이스에서는 grok tier로 Groq를 통해 서빙됨)"에게 최종 백테스트 수치
    # 산출과 사실상의 최종 판단을 맡겼다가, 실제 실행 능력이 없는 그 모델이 결과를
    # 통째로 지어냈다("에코프로 편중도 72.9%→23.4%" 등 조작). 소유자 지시: 저사양
    # 모델이 고사양 모델의 몫(최종 판단/검증)을 이어받지 못하게 구조적으로 막을 것.
    # "draft"는 초안/보조 작업(요약, 패치 초안)에 적합하고, "verified"만 최종 판단
    # (목표 완료 검증, 코드 리뷰 승인, 실전 판단 등)에 쓸 수 있다.
    PROVIDER_TIER = {
        "gemini": "draft",
        "grok": "draft",       # Groq로 서빙되는 Qwen 등 - 초안/실행 보조용, 최종 판단 금지
        "deepseek": "draft",   # 소유자 지시(2026-09-13): DeepSeek도 100% 신뢰하지 않음 - draft 유지
        "openai": "verified",
        "codex_cli": "verified",   # 실제 ChatGPT 구독, 2026-09-13 이 환경에서 성공 확인
        "claude_cli": "verified",  # 실제 Claude 구독 - 단, Claude Code 세션 내부 호출은 항상 실패(의도됨)
    }
    _TIER_RANK = {"draft": 0, "verified": 1}

    def chat_completion_with_meta(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500,
        provider: Optional[str] = None,
        min_tier: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        provider가 None이면 기본 4단계 지능형 캐스케이드(무료 우선):
        Gemini -> Groq/xAI Grok -> DeepSeek -> OpenAI -> 결정론적 폴백.

        provider를 명시하면("gemini"/"grok"/"deepseek"/"openai") 그 provider만 시도하고,
        실패하면 다른 provider로 자동 전환하지 않고 곧바로 폴백을 반환한다 - 목표 완료
        이중검증처럼 "정말 서로 다른 두 모델"이 필요한 호출에서, 캐스케이드가 매번 같은
        1순위 provider로만 응답해 "이중검증"이 사실상 같은 모델을 두 번 부르는 것이 되는
        상황을 막는다.

        min_tier="verified"를 지정하면 PROVIDER_TIER상 "draft" 등급인 provider는 아예
        시도하지 않는다(캐스케이드에서도, provider로 명시해도) - 등급 미달이면 그 provider가
        응답할 수 있어도 조용히 써주지 않고 폴백(is_fallback=True)으로 떨어진다. 저가
        모델이 고신뢰 판단을 떠맡아 결과를 지어내는 사고를 API 레벨에서 막기 위함이다.

        반환값에 provider/model/usage(토큰수)/is_fallback을 함께 담아 실제 사용량 원장을
        만들 수 있게 한다 (기존에는 응답 문자열만 반환해 비용 추적이 불가능했다).
        """
        triers = {
            "gemini": self._try_gemini, "grok": self._try_grok,
            "deepseek": self._try_deepseek, "openai": self._try_openai,
            "codex_cli": self._try_codex_cli, "claude_cli": self._try_claude_cli
        }

        order = [provider] if provider else list(self._PROVIDER_TRIERS)
        if provider and provider not in triers:
            logger.error(f"알 수 없는 provider 지정: {provider}")
            order = []

        if min_tier:
            required_rank = self._TIER_RANK.get(min_tier, 0)
            filtered = [p for p in order if self._TIER_RANK.get(self.PROVIDER_TIER.get(p, "draft"), 0) >= required_rank]
            dropped = set(order) - set(filtered)
            if dropped:
                logger.warning(f"min_tier={min_tier} 미달로 제외된 provider: {dropped} (저사양 모델의 고신뢰 판단 대행 방지)")
            order = filtered

        for name in order:
            result = triers[name](messages, model, temperature, max_tokens)
            if result is not None:
                return result

        self.last_provider_used = "NONE_ALL_PROVIDERS_FAILED"
        logger.error(f"LLM 호출 실패 (provider={provider or '캐스케이드 전체'}) - 폴백 반환 (실제 분석 아님)")
        return {
            "content": "",
            "provider": "none", "model": None,
            "usage": None, "is_fallback": True
        }

