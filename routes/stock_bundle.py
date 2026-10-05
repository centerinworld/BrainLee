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
        # 2021~2022(또는 XBRL 지역 주석이 없는 연도): 사업보고서 표의 내수/수출(합계·매출 일치 확인분)
        have_y = {g["year"] for g in geo}
        for r in map(tuple, conn.execute(
                """SELECT fiscal_year, source, domestic_krw, export_krw, total_krw, export_pct, matched_basis FROM biz_sales_domestic_export
                   WHERE stock_code=? AND check_status='ok' ORDER BY fiscal_year DESC""", (code,)).fetchall()):
            if r[0] not in have_y and len(geo) < 5:
                geo.append({"year": r[0], "report_type": r[6], "total": r[4], "domestic": r[2], "overseas": r[3], "overseas_pct": r[5],
                            "regions": {"내수": r[2], "수출": r[3]}, "status": "doc_" + r[1]})
                have_y.add(r[0])
        geo.sort(key=lambda g: -g["year"])
        def q(sql, *a):
            try:
                return [tuple(r) for r in conn.execute(sql, a).fetchall()]
            except Exception:
                return []
        rd = [{"year": r[0], "rd_krw": r[1], "ratio_pct": r[2], "status": r[3]} for r in q(
            "SELECT fiscal_year, rd_total_krw, rd_ratio_pct, check_status FROM biz_rd_expense WHERE stock_code=? ORDER BY fiscal_year DESC LIMIT 5", code)]
        cap_y = q("SELECT MAX(fiscal_year) FROM biz_capacity WHERE stock_code=? AND utilization_pct IS NOT NULL", code)
        capacity = [{"item": r[0], "capacity": r[1], "production": r[2], "util_pct": r[3], "unit": r[4], "status": r[5]} for r in q(
            "SELECT item, capacity, production, utilization_pct, unit, check_status FROM biz_capacity WHERE stock_code=? AND fiscal_year=? AND utilization_pct IS NOT NULL ORDER BY item",
            code, cap_y[0][0])] if cap_y and cap_y[0][0] else []
        raw_y = q("SELECT MAX(fiscal_year) FROM biz_raw_material_price WHERE stock_code=?", code)
        raw = []
        if raw_y and raw_y[0][0]:
            prev = {r[0]: r[1] for r in q("SELECT item, price FROM biz_raw_material_price WHERE stock_code=? AND fiscal_year=?", code, raw_y[0][0] - 1)}
            raw = [{"item": r[0], "price": r[1], "prior": prev.get(r[0]), "unit": r[2]} for r in q(
                "SELECT item, price, unit FROM biz_raw_material_price WHERE stock_code=? AND fiscal_year=? ORDER BY item", code, raw_y[0][0])]
        cn_y = q("SELECT MAX(fiscal_year) FROM biz_cost_nature WHERE stock_code=? AND check_status IN ('ok','no_is')", code)
        cost = [{"category": r[0], "amount": r[1], "total": r[2], "status": r[3]} for r in q(
            "SELECT category, amount_krw, total_krw, check_status FROM biz_cost_nature WHERE stock_code=? AND fiscal_year=? AND check_status IN ('ok','no_is') ORDER BY amount_krw DESC",
            code, cn_y[0][0])] if cn_y and cn_y[0][0] else []
        extra = {}
        for r in q("""SELECT fiscal_year, report_type, field, value_krw, status FROM financial_extra_accounts WHERE stock_code=? AND status IN ('confirmed','dart_only','definition_fit')
                      ORDER BY fiscal_year DESC, CASE report_type WHEN 'CFS' THEN 0 ELSE 1 END""", code):
            extra.setdefault(r[0], {}).setdefault(r[2], {"value": r[3], "status": r[4], "report_type": r[1]})
        extra_list = [{"year": y_, **{k: v for k, v in d.items()}} for y_, d in sorted(extra.items(), reverse=True)[:3]]
        div = [{"year": r[0], "dps": r[1], "total_bn": r[2], "yield_pct": r[3], "payout_pct": r[4]} for r in q(
            "SELECT fiscal_year, dps_krw, total_cash_div_bn, div_yield_pct, div_payout_pct FROM dart_dividends WHERE stock_code=? AND reprt_code='11011' AND fiscal_year>=2021 ORDER BY fiscal_year DESC", code)]
        return {"ok": True, "geography": geo, "product_mix_year": y, "product_mix": products,
                "rd": rd, "capacity_year": cap_y[0][0] if cap_y else None, "capacity": capacity,
                "raw_material_year": raw_y[0][0] if raw_y else None, "raw_material": raw,
                "cost_nature_year": cn_y[0][0] if cn_y else None, "cost_nature": cost, "extra_accounts": extra_list, "dividends": div,
                "note": "국내/해외 = XBRL '지역에 대한 정보'(2023~) 또는 사업보고서 매출실적·지역별 매출 표(2021~22), 합계 = 전체 매출 확인분만. 제품별 = 사업보고서 '매출 및 수주상황'. 연구개발비·가동률·원재료·원가 = 사업보고서 본문, 차입금 등 = DART 재무제표(확정 = FnGuide 일치)."}
    finally:
        conn.close()
