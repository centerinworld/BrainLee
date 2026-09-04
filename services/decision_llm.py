"""Isolated providers for evidence extraction and investment review."""
from __future__ import annotations
import json, os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo
import requests
from services.gemini import generate_text

KST = ZoneInfo("Asia/Seoul")

def is_deepseek_offpeak() -> bool:
    """DeepSeek peak windows are daily 10-13 and 15-19 KST."""
    now = datetime.now(KST)
    return not (10 <= now.hour < 13 or 15 <= now.hour < 19)

def _obj(text: str) -> dict[str, Any]:
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start: raise ValueError("JSON 응답이 아닙니다.")
    return json.loads(text[start:end+1])

def deepseek_flash(prompt: str):
    if not is_deepseek_offpeak():
        raise RuntimeError("DeepSeek는 할인 시간대(KST 19:00-10:00, 13:00-15:00)에만 실행됩니다.")
    key = os.getenv("DEEPSEEK_API_KEY", "").strip("\"'")
    if not key: raise RuntimeError("DEEPSEEK_API_KEY 미설정")
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash").strip("\"'")
    r = requests.post(f"{base}/chat/completions", headers={"Authorization":f"Bearer {key}"}, json={"model":model,"messages":[{"role":"system","content":"투자 판단 없이 문서의 사실과 출처 ID만 JSON으로 추출하라."},{"role":"user","content":prompt}],"response_format":{"type":"json_object"},"temperature":0.1,"max_tokens":1200}, timeout=120)
    try:
        r.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", "network")
        raise RuntimeError(f"DeepSeek API 요청 실패 (HTTP {status})") from None
    return model, _obj(r.json()["choices"][0]["message"]["content"])

def gpt_review(prompt: str):
    key = (os.getenv("OPENAI_DECISION_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if not key: raise RuntimeError("OPENAI API 키 미설정")
    model = os.getenv("OPENAI_DECISION_MODEL", "gpt-4.1-mini")
    r = requests.post("https://api.openai.com/v1/chat/completions", headers={"Authorization":f"Bearer {key}"}, json={"model":model,"messages":[{"role":"system","content":"증거 기반 투자위원회 심사자다. 매매 지시 없이 JSON만 반환하라."},{"role":"user","content":prompt}],"response_format":{"type":"json_object"},"temperature":0.15}, timeout=120)
    r.raise_for_status(); return model, _obj(r.json()["choices"][0]["message"]["content"])

def deepseek_review(prompt: str):
    model = os.getenv("DEEPSEEK_FLASH_MODEL", "deepseek-v4-flash")
    return model, _obj(generate_text(prompt, system_instruction="증거 기반 투자위원회 심사자다. 매매 지시 없이 JSON만 반환하라.", response_mime_type="application/json", model_override=model, max_output_tokens=1200, timeout=120))
