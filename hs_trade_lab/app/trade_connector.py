from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

import httpx

from .models import DataSourceConfig


def _parse_json_value(text: str) -> dict[str, Any]:
    text = (text or "{}").strip() or "{}"
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _pick_sequence(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "data", "results", "response", "body"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = _pick_sequence(value)
            if nested:
                return nested
    return []


def _clean_number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _infer_period(item: dict[str, Any]) -> str | None:
    keys = ("period_ym", "period", "ym", "yearMonth", "statYm", "baseYm")
    for key in keys:
        if item.get(key):
            text = str(item[key]).strip()
            match = re.search(r"(\d{4})[-/]?(\d{2})", text)
            if match:
                return f"{match.group(1)}-{match.group(2)}"
    if item.get("year") and item.get("month"):
        return f"{int(item['year']):04d}-{int(item['month']):02d}"
    return None


def _infer_metric(item: dict[str, Any], candidates: Iterable[str]) -> float:
    lowered = {str(k).lower(): v for k, v in item.items()}
    for candidate in candidates:
        if candidate in lowered:
            return _clean_number(lowered[candidate])
    return 0.0


def fetch_trade_series(
    config: DataSourceConfig,
    hs_code: str,
    start_ym: str,
    end_ym: str,
    flow_type: str = "total",
) -> list[dict[str, Any]]:
    base_url = (config.base_url or "").rstrip("/")
    if not base_url:
        raise ValueError("데이터 소스 URL이 비어 있습니다.")

    endpoint_path = (config.endpoint_path or "").lstrip("/")
    url = f"{base_url}/{endpoint_path}" if endpoint_path else base_url

    params = _parse_json_value(config.params_json)
    params.update(
        {
            "hsCode": hs_code,
            "hs_code": hs_code,
            "startYm": start_ym,
            "endYm": end_ym,
            "flowType": flow_type,
        }
    )
    if config.api_key:
        params.setdefault("serviceKey", config.api_key)
        params.setdefault("apiKey", config.api_key)

    with httpx.Client(timeout=20.0) as client:
        response = client.get(url, params=params, headers={"Accept": "application/json"})
        response.raise_for_status()
        payload = response.json()

    rows = _pick_sequence(payload)
    normalized: list[dict[str, Any]] = []
    for row in rows:
        period_ym = _infer_period(row)
        if not period_ym:
            continue
        export_value = _infer_metric(
            row,
            ("export_value", "expvalue", "export", "exp_amt", "outAmt", "exportamount"),
        )
        import_value = _infer_metric(
            row,
            ("import_value", "impvalue", "import", "imp_amt", "inAmt", "importamount"),
        )
        if not export_value and not import_value:
            amount = _infer_metric(row, ("trade_value", "amount", "value", "tot_amt"))
            if flow_type == "export":
                export_value = amount
            elif flow_type == "import":
                import_value = amount
            else:
                export_value = amount
        normalized.append(
            {
                "period_ym": period_ym,
                "export_value": export_value,
                "import_value": import_value,
                "trade_balance": export_value - import_value,
                "raw_json": json.dumps(row, ensure_ascii=False),
                "source_name": config.provider_name,
            }
        )
    return sorted(normalized, key=lambda item: item["period_ym"])


async def suggest_hs_code(product_name: str, api_key: str, model: str) -> dict[str, Any]:
    if not api_key:
        raise ValueError("Gemini API 키가 필요합니다.")

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    prompt = (
        "다음 상품명에 대해 한국 수출입 분석용 참고 HS Code를 추정해 주세요. "
        "응답 형식은 반드시 JSON 하나만 사용하고 키는 hs_code, description, reason 으로 제한하세요. "
        f"상품명: {product_name}"
    )
    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt},
                ]
            }
        ]
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        data = response.json()

    text = ""
    for candidate in data.get("candidates", []):
        content = candidate.get("content", {})
        for part in content.get("parts", []):
            if "text" in part:
                text += part["text"]

    cleaned = text.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        parsed = {}
    return {
        "raw_text": text.strip(),
        "parsed_hs_code": parsed.get("hs_code"),
        "parsed_description": parsed.get("description"),
    }
