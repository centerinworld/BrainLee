"""
Project Antigravity: 통합 클라우드 LLM 클라이언트 (DeepSeek / Claude / OpenAI)
로컬 맥미니 CPU/RAM 점유율 0% 유지 및 초저비용 고성능(DeepSeek-V3/R1) API 호출
"""

import os
import json
import logging
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

    def chat_completion_with_meta(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1500
    ) -> Dict[str, Any]:
        """
        사용자 지정 3단계 지능형 LLM 연쇄 호출 (Cascading Multi-Tier):
        1차 (1순위): Google Gemini API (무료 Gemini API 우선 사용)
        2차 (2순위 - 한도 초과 시 자동 전환): Groq / xAI Grok API
        3차 (3순위 - 추가 초과 시 자동 전환): DeepSeek API (deepseek-chat / deepseek-reasoner)
        4차 (최종 안전망): OpenAI GPT-4o-mini & 룰베이스 엔진

        반환값에 provider/model/usage(토큰수)/is_fallback을 함께 담아 실제 사용량 원장을
        만들 수 있게 한다 (기존에는 응답 문자열만 반환해 비용 추적이 불가능했다).
        """
        # =========================================================================
        # 1차 (1순위): Google Gemini API
        # =========================================================================
        if self.gemini_key and not self.gemini_key.startswith("your_"):
            try:
                g_model = model if model and "gemini" in model else self.gemini_model
                headers = {
                    "Authorization": f"Bearer {self.gemini_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": g_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                res = requests.post(
                    "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=15
                )
                if res.status_code == 200:
                    data = res.json()
                    choices = data.get("choices", [])
                    if choices and "message" in choices[0]:
                        self.last_provider_used = f"Google Gemini ({g_model})"
                        logger.info(f"[1차 성공] Google Gemini ({g_model}) 응답 완료")
                        return {
                            "content": choices[0]["message"]["content"],
                            "provider": "gemini", "model": g_model,
                            "usage": data.get("usage"), "is_fallback": False
                        }
                elif res.status_code == 429:
                    logger.warning("[LLM 라우팅] ⚠️ 1차 Google Gemini 무료 할당량(429 Quota) 초과 -> 2차 Grok으로 자동 전환합니다.")
                else:
                    logger.warning(f"Google Gemini API 반환 코드: {res.status_code} -> 2차 Grok으로 폴백")
            except Exception as e:
                logger.warning(f"1차 Google Gemini 호출 실패 ({e}) -> 2차 Grok으로 폴백")

        # =========================================================================
        # 2차 (2순위): xAI Grok API
        # =========================================================================
        if self.grok_key and not self.grok_key.startswith("your_"):
            try:
                target_grok_model = model if model and "grok" in model else self.grok_model
                headers = {
                    "Authorization": f"Bearer {self.grok_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": target_grok_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                res = requests.post(f"{self.grok_base_url}/chat/completions", headers=headers, json=payload, timeout=25)
                if res.status_code == 200:
                    data = res.json()
                    self.last_provider_used = f"xAI Grok ({target_grok_model})"
                    logger.info(f"[2차 성공] xAI Grok ({target_grok_model}) 응답 완료")
                    return {
                        "content": data["choices"][0]["message"]["content"],
                        "provider": "grok", "model": target_grok_model,
                        "usage": data.get("usage"), "is_fallback": False
                    }
                elif res.status_code == 429:
                    logger.warning("[LLM 라우팅] ⚠️ 2차 xAI Grok 사용량 초과(429) -> 3차 DeepSeek으로 자동 전환합니다.")
                else:
                    logger.warning(f"xAI Grok API 반환 코드: {res.status_code} -> 3차 DeepSeek으로 폴백")
            except Exception as e:
                logger.warning(f"2차 xAI Grok 호출 실패 ({e}) -> 3차 DeepSeek으로 폴백")

        # =========================================================================
        # 3차 (3순위): DeepSeek API
        # =========================================================================
        if self.deepseek_key and not self.deepseek_key.startswith("your_"):
            try:
                target_model = self.deepseek_model
                headers = {
                    "Authorization": f"Bearer {self.deepseek_key}",
                    "Content-Type": "application/json"
                }
                payload = {
                    "model": target_model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                res = requests.post(f"{self.deepseek_base_url}/chat/completions", headers=headers, json=payload, timeout=30)
                if res.status_code == 200:
                    data = res.json()
                    self.last_provider_used = f"DeepSeek ({target_model})"
                    logger.info(f"[3차 성공] DeepSeek API 응답 완료 (초저비용: $0.14/1M)")
                    return {
                        "content": data["choices"][0]["message"]["content"],
                        "provider": "deepseek", "model": target_model,
                        "usage": data.get("usage"), "is_fallback": False
                    }
                else:
                    logger.warning(f"DeepSeek API 반환 오류: {res.status_code} -> 4차 안전망으로 폴백")
            except Exception as e:
                logger.error(f"3차 DeepSeek API 호출 실패: {e}")

        # =========================================================================
        # 4차 (비상 안전망): OpenAI API
        # =========================================================================
        if self.openai_key and not self.openai_key.startswith("your_"):
            try:
                import openai
                client = openai.OpenAI(api_key=self.openai_key)
                res = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                self.last_provider_used = "OpenAI (gpt-4o-mini)"
                usage = getattr(res, "usage", None)
                return {
                    "content": res.choices[0].message.content,
                    "provider": "openai", "model": "gpt-4o-mini",
                    "usage": usage.model_dump() if usage else None, "is_fallback": False
                }
            except Exception as e:
                logger.error(f"OpenAI API 호출 실패: {e}")

        # =========================================================================
        # 5차: 모든 provider 실패 - 결정론적 폴백 (실제 LLM 응답이 아님을 명시)
        # =========================================================================
        self.last_provider_used = "NONE_ALL_PROVIDERS_FAILED"
        logger.error("모든 LLM provider 호출 실패 - 폴백 텍스트 반환 (실제 분석 아님)")
        return {
            "content": "",
            "provider": "none", "model": None,
            "usage": None, "is_fallback": True
        }

