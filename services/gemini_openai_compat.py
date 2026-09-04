"""Temporary OpenAI-shaped adapter backed entirely by DeepSeek V4 Flash.

It keeps older dashboard call sites working while the provider migration is
completed. The requested OpenAI model name is deliberately ignored.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from services.gemini import generate_text


def _content(messages: list[dict[str, Any]]) -> tuple[str | None, str]:
    system = None
    user_parts: list[str] = []
    for message in messages:
        content = str(message.get("content") or "")
        if message.get("role") == "system":
            system = content
        else:
            user_parts.append(content)
    return system, "\n\n".join(user_parts)


class _Completions:
    def create(self, *, messages: list[dict[str, Any]], **kwargs: Any) -> SimpleNamespace:
        system, prompt = _content(messages)
        response_format = kwargs.get("response_format") or {}
        requested_model = str(kwargs.get("model") or "")
        model_override = requested_model if requested_model.startswith("deepseek-") else None
        text = generate_text(
            prompt,
            system_instruction=system,
            temperature=float(kwargs.get("temperature", 0.2)),
            max_output_tokens=int(kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 1024),
            response_mime_type="application/json" if response_format.get("type") == "json_object" else None,
            model_override=model_override,
            timeout=float(kwargs.get("timeout", 60)),
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


class OpenAI:
    def __init__(self, **_: Any) -> None:
        self.chat = SimpleNamespace(completions=_Completions())


chat = SimpleNamespace(completions=_Completions())
