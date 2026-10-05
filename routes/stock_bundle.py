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
            revenue_mix,
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
            _fetch(client, f"{base}/api/dashboard/revenue-mix/{code}"),
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
        "revenue_mix":            revenue_mix,
    }


@router.get("/api/dashboard/revenue-mix/{code}")
def get_revenue_mix(code: str):
    """국내/해외 매출(XBRL 지역 주석, 합계 항등식 확인분만 — revenue_geography) + 제품·부문별 매출 구성(사업보고서, company_product_mix).
    2026-10-05 신규. 항등식 불일치(mismatch) 행은 내보내지 않는다(fail-closed)."""
    import json as _json
    import re as _re
    from db_compat import connect_primary_db
    conn = connect_primary_db(timeout=30, readonly=True)
    try:
        geo = []
        for r in conn.execute(
            """SELECT fiscal_year, report_type, total_krw, domestic_krw, overseas_krw, overseas_pct, regions_json, identity_status, rcept_no
               FROM revenue_geography WHERE stock_code=? AND identity_status IN ('identity_ok','domestic_only')
               ORDER BY fiscal_year DESC, CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END""", (code,)).fetchall():
            r = tuple(r)
            if any(g["year"] == r[0] for g in geo):
                continue  # 연도당 연결 우선 1건
            regions = {_re.sub(r"\s*\[구성요소\]$", "", k): v for k, v in _json.loads(r[6] or "{}").items()}
            geo.append({"year": r[0], "report_type": r[1], "total": r[2], "domestic": r[3], "overseas": r[4],
                        "overseas_pct": r[5], "regions": regions, "status": r[7], "rcept_no": r[8]})
            if len(geo) >= 3:
                break
        y = conn.execute("SELECT MAX(year) FROM company_product_mix WHERE stock_code=?", (code,)).fetchone()[0]
        products = []
        if y:
            products = [{"category": r[0], "product": r[1], "revenue": r[2], "pct": r[3]} for r in map(tuple, conn.execute(
                "SELECT category, product_name, revenue_krw, revenue_pct FROM company_product_mix WHERE stock_code=? AND year=? ORDER BY revenue_pct DESC NULLS LAST",
                (code, y)).fetchall())]
        return {"ok": True, "geography": geo, "product_mix_year": y, "product_mix": products,
                "note": "국내/해외 = DART XBRL 주석 '지역에 대한 정보'(지역 합계 = 전체 매출 확인분만). 제품별 = 사업보고서 '매출 및 수주상황'."}
    finally:
        conn.close()
