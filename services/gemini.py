"""Legacy compatibility facade routed exclusively to DeepSeek V4 Flash.

Existing imports keep working while Gemini API traffic is fully removed.
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

DEFAULT_MODEL = "deepseek-v4-flash"
KST = ZoneInfo("Asia/Seoul")

def api_key() -> str:
    return os.getenv("DEEPSEEK_API_KEY", "").strip("\"'")

def is_configured() -> bool:
    return bool(api_key())

def model_name() -> str:
    return os.getenv("DEEPSEEK_FLASH_MODEL", DEFAULT_MODEL).strip("\"'") or DEFAULT_MODEL

def fallback_model_name() -> str:
    return ""

def _is_offpeak() -> bool:
    now = datetime.now(KST)
    return not (10 <= now.hour < 13 or 15 <= now.hour < 19)

def generate_text(
    prompt: str,
    *,
    system_instruction: str | None = None,
    temperature: float = 0.2,
    max_output_tokens: int = 1024,
    response_mime_type: str | None = None,
    model_override: str | None = None,
    timeout: float = 60.0,
) -> str:
    """Call the lowest-cost configured provider: DeepSeek V4 Flash.

    Off-peak-only is enabled by default to preserve the 50% discount. Set
    DEEPSEEK_OFFPEAK_ONLY=0 only when an immediate, paid peak response matters.
    """
    if os.getenv("DEEPSEEK_OFFPEAK_ONLY", "1").lower() in {"1", "true", "yes", "on"} and not _is_offpeak():
        raise RuntimeError("DeepSeek는 할인 시간대(KST 19:00-10:00, 13:00-15:00)에만 실행됩니다.")
    key = api_key()
    if not key:
        raise RuntimeError("DEEPSEEK_API_KEY가 설정되지 않았습니다.")
    requested = str(model_override or "")
    model = requested if requested.startswith("deepseek-") else model_name()
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip("\"'").rstrip("/")
    if base.endswith("/chat/completions"):
        base = base.rsplit("/chat/completions", 1)[0]
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})
    # Flash의 기본 추론 모드는 짧은 응답 한도를 내부 추론에 모두 소진할 수 있다.
    # 대시보드 요약/RAG 분류는 비용과 응답 안정성을 위해 즉시 응답 모드로 호출한다.
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_output_tokens,
        "thinking": {"type": "disabled"},
    }
    if response_mime_type == "application/json":
        payload["response_format"] = {"type": "json_object"}
    try:
        response = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "network")
        raise RuntimeError(f"DeepSeek API 요청 실패 (HTTP {status})") from None
    text = str(body["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise RuntimeError("DeepSeek 응답에 텍스트가 없습니다.")
    return text
