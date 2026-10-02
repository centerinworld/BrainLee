"""
routes/stock_bundle.py — 국내종목 상세 페이지 API 번들 엔드포인트

단일 요청으로 14개 개별 API를 병렬 호출해 응답을 묶어 반환.
기존 개별 API는 그대로 유지(제거하지 않음).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter

router = APIRouter()


def _get_port() -> int:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("PORT=") or stripped.startswith("APP_PORT="):
                try:
                    return int(stripped.split("=", 1)[1].strip().strip('"').strip("'"))
                except ValueError:
                    pass
    return 8000


_BASE_URL = f"http://127.0.0.1:{_get_port()}"


async def _fetch(client: httpx.AsyncClient, url: str) -> Any:
    try:
        r = await client.get(url, timeout=15.0)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


@router.get("/api/dashboard/stock-bundle/{code}")
async def get_stock_bundle(code: str):
    """국내종목 상세 analysis 탭 — 14개 보조 API를 병렬로 묶어 1회 응답."""
    base = _BASE_URL
    async with httpx.AsyncClient() as client:
        (
            corporate_actions,
            chart_signals,
            stock_quality_signals,
            stock_extra,
            ch_data,
            stock_insight,
            estimated_performance,
            kiwoom_summary,
            disclosures,
            extra_signals,
            data_quality,
            notices,
            major_holders,
            company_intel,
        ) = await asyncio.gather(
            _fetch(client, f"{base}/api/dashboard/corporate-actions/{code}?days=730"),
            _fetch(client, f"{base}/api/extra-signals/chart/{code}"),
            _fetch(client, f"{base}/api/tenbagger/stock-quality-signals/{code}"),
            _fetch(client, f"{base}/api/tenbagger/stock-extra/{code}"),
            _fetch(client, f"{base}/api/dart-excel/ch-data/{code}"),
            _fetch(client, f"{base}/api/tenbagger/stock-insight/{code}"),
            _fetch(client, f"{base}/api/dashboard/estimated-performance/{code}"),
            _fetch(client, f"{base}/api/kiwoom/summary/{code}"),
            _fetch(client, f"{base}/api/dashboard/disclosures/{code}"),
            _fetch(client, f"{base}/api/extra-signals/extra-signals/{code}"),
            _fetch(client, f"{base}/api/dashboard/data-quality/{code}"),
            _fetch(client, f"{base}/api/notices/stock/{code}"),
            _fetch(client, f"{base}/api/insider/major/{code}?limit=50"),
            _fetch(client, f"{base}/api/company-intelligence/company/{code}"),
        )

    return {
        "corporate_actions":      corporate_actions,
        "chart_signals":          chart_signals,
        "stock_quality_signals":  stock_quality_signals,
        "stock_extra":            stock_extra,
        "ch_data":                ch_data,
        "stock_insight":          stock_insight,
        "estimated_performance":  estimated_performance,
        "kiwoom_summary":         kiwoom_summary,
        "disclosures":            disclosures,
        "extra_signals":          extra_signals,
        "data_quality":           data_quality,
        "notices":                notices,
        "major_holders":          major_holders,
        "company_intel":          company_intel,
    }
