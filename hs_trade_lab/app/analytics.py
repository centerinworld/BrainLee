from __future__ import annotations

import json
import asyncio
import math
import os
from collections import defaultdict
from statistics import mean, pstdev
from typing import Any

import httpx
from services.gemini import generate_text, is_configured, model_name
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from .models import CustomsMonthlyRecord, HSCodeCompanyMap, HSSectorMap, InsightCache, SectorPreset


def _pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 2)


def _is_month_ym(value: str | None) -> bool:
    if not value or len(value) != 7 or value[4] != "-":
        return False
    year = value[:4]
    month = value[5:7]
    return year.isdigit() and month.isdigit() and 1 <= int(month) <= 12


def _shift_month(ym: str, months: int) -> str:
    year = int(ym[:4])
    month = int(ym[5:7])
    idx = year * 12 + (month - 1) + months
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def _three_month_avg_yoy(points: list[dict[str, Any]], value_key: str) -> tuple[float | None, float | None]:
    clean = [point for point in points if _is_month_ym(point.get("period_ym"))]
    lookup = {point["period_ym"]: float(point.get(value_key) or 0.0) for point in clean}
    latest_period = None
    for point in sorted(clean, key=lambda x: x["period_ym"], reverse=True):
        if float(point.get(value_key) or 0.0) > 0:
            latest_period = point["period_ym"]
            break
    if not latest_period:
        return None, None

    pairs = []
    for offset in range(3):
        current_period = _shift_month(latest_period, -offset)
        previous_period = _shift_month(current_period, -12)
        current = lookup.get(current_period, 0.0)
        previous = lookup.get(previous_period, 0.0)
        if current > 0 and previous > 0:
            pairs.append((current, previous))
    if not pairs:
        return lookup.get(latest_period), None

    current_avg = sum(current for current, _ in pairs) / len(pairs)
    previous_avg = sum(previous for _, previous in pairs) / len(pairs)
    return lookup.get(latest_period), _pct_change(current_avg, previous_avg)


def _prefix_filters(column, codes: list[str]):
    cleaned = [str(code).strip() for code in codes if str(code).strip()]
    return [column.like(f"{code}%") for code in cleaned]


def sector_series(db: Session, sector_key: str) -> list[dict[str, Any]]:
    hs_codes = [row.hs_code for row in db.query(HSSectorMap).filter(HSSectorMap.sector_key == sector_key).all()]
    if not hs_codes:
        return []
    filters = _prefix_filters(CustomsMonthlyRecord.hs_code, hs_codes)
    if not filters:
        return []
    rows = (
        db.query(
            CustomsMonthlyRecord.period_ym,
            func.sum(CustomsMonthlyRecord.export_value),
            func.sum(CustomsMonthlyRecord.import_value),
            func.sum(CustomsMonthlyRecord.trade_balance),
        )
        .filter(
            CustomsMonthlyRecord.endpoint == "itemtrade",
            or_(*filters),
            CustomsMonthlyRecord.period_ym.like("____-__"),
        )
        .group_by(CustomsMonthlyRecord.period_ym)
        .order_by(CustomsMonthlyRecord.period_ym)
        .all()
    )
    return [
        {
            "period_ym": period_ym,
            "export_value": float(export_value or 0.0),
            "import_value": float(import_value or 0.0),
            "trade_balance": float(trade_balance or 0.0),
        }
        for period_ym, export_value, import_value, trade_balance in rows
    ]


def stock_features(db: Session) -> list[dict[str, Any]]:
    mapping_rows = db.query(HSCodeCompanyMap).order_by(HSCodeCompanyMap.stock_code).all()
    stock_to_hs: dict[str, list[str]] = defaultdict(list)
    stock_meta: dict[str, dict[str, str]] = {}
    for row in mapping_rows:
        stock_to_hs[row.stock_code].append(row.hs_code)
        stock_meta[row.stock_code] = {
            "stock_code": row.stock_code,
            "stock_name": row.stock_name,
            "sector_name": row.sector_name,
        }

    items = []
    for stock_code, hs_codes in stock_to_hs.items():
        filters = _prefix_filters(CustomsMonthlyRecord.hs_code, hs_codes)
        if not filters:
            continue
        rows = (
            db.query(
                CustomsMonthlyRecord.period_ym,
                func.sum(CustomsMonthlyRecord.export_value),
                func.sum(CustomsMonthlyRecord.import_value),
                func.sum(CustomsMonthlyRecord.trade_balance),
            )
            .filter(
                CustomsMonthlyRecord.endpoint == "itemtrade",
                or_(*filters),
                CustomsMonthlyRecord.period_ym.like("____-__"),
            )
            .group_by(CustomsMonthlyRecord.period_ym)
            .order_by(CustomsMonthlyRecord.period_ym)
            .all()
        )
        if len(rows) < 13:
            continue
        points = [
            {"period_ym": row[0], "export_value": float(row[1] or 0.0), "trade_balance": float(row[3] or 0.0)}
            for row in rows
        ]
        exports = [point["export_value"] for point in points]
        latest_export, yoy = _three_month_avg_yoy(points, "export_value")
        latest_export = latest_export or 0.0
        deltas = []
        for idx in range(1, len(exports)):
            prev = exports[idx - 1]
            deltas.append(0.0 if prev == 0 else ((exports[idx] - prev) / prev) * 100)
        zscore = 0.0
        if len(deltas) >= 6 and pstdev(deltas[:-1]) > 0:
            zscore = (deltas[-1] - mean(deltas[:-1])) / pstdev(deltas[:-1])
        latest_balance = float(rows[-1][3] or 0.0)
        items.append(
            {
                **stock_meta[stock_code],
                "hs_codes": sorted(set(hs_codes)),
                "export_latest": latest_export,
                "export_yoy": yoy,
                "momentum_zscore": round(zscore, 2),
                "trade_balance_latest": latest_balance,
                "feature_score": round(abs(yoy or 0.0) + abs(zscore) * 12, 2),
            }
        )
    return sorted(items, key=lambda row: row["feature_score"], reverse=True)


def sector_features(db: Session) -> list[dict[str, Any]]:
    sectors = db.query(SectorPreset).order_by(SectorPreset.sort_order).all()
    features = []
    for sector in sectors:
        points = sector_series(db, sector.sector_key)
        if len(points) < 13:
            features.append(
                {
                    "sector_key": sector.sector_key,
                    "label": sector.label,
                    "description": sector.description,
                    "points": points,
                    "export_latest": 0.0,
                    "export_yoy": None,
                    "feature_score": 0.0,
                }
            )
            continue
        exports = [point["export_value"] for point in points]
        latest_export, yoy = _three_month_avg_yoy(points, "export_value")
        latest_export = latest_export or 0.0
        growths = []
        for idx in range(1, len(exports)):
            prev = exports[idx - 1]
            growths.append(0.0 if prev == 0 else ((exports[idx] - prev) / prev) * 100)
        zscore = 0.0
        if len(growths) >= 6 and pstdev(growths[:-1]) > 0:
            zscore = (growths[-1] - mean(growths[:-1])) / pstdev(growths[:-1])
        features.append(
            {
                "sector_key": sector.sector_key,
                "label": sector.label,
                "description": sector.description,
                "points": points,
                "export_latest": latest_export,
                "export_yoy": yoy,
                "import_latest": points[-1]["import_value"],
                "trade_balance_latest": points[-1]["trade_balance"],
                "feature_score": round(abs(yoy or 0.0) + abs(zscore) * 10, 2),
            }
        )
    return sorted(features, key=lambda row: row["feature_score"], reverse=True)


def sector_detail(db: Session, sector_key: str) -> dict[str, Any]:
    sector = db.query(SectorPreset).filter(SectorPreset.sector_key == sector_key).first()
    if not sector:
        return {}
    points = sector_series(db, sector_key)
    hs_maps = db.query(HSSectorMap).filter(HSSectorMap.sector_key == sector_key).order_by(HSSectorMap.hs_code).all()
    hs_codes = [row.hs_code for row in hs_maps]
    filters = _prefix_filters(CustomsMonthlyRecord.hs_code, hs_codes)
    hs_rows = (
        db.query(
            CustomsMonthlyRecord.hs_code,
            CustomsMonthlyRecord.hs_name,
            func.sum(CustomsMonthlyRecord.export_value),
            func.sum(CustomsMonthlyRecord.import_value),
        )
        .filter(
            CustomsMonthlyRecord.endpoint == "itemtrade",
            or_(*filters) if filters else CustomsMonthlyRecord.hs_code == "__never__",
            CustomsMonthlyRecord.period_ym.like("____-__"),
        )
        .group_by(CustomsMonthlyRecord.hs_code, CustomsMonthlyRecord.hs_name)
        .all()
    )
    top_hs = sorted(
        [
            {
                "hs_code": row[0],
                "display_name": next((item.display_name for item in hs_maps if item.hs_code == row[0]), row[1] or row[0]),
                "mapping_status": next((item.mapping_status for item in hs_maps if item.hs_code == row[0]), "provisional"),
                "export_value": float(row[2] or 0.0),
                "import_value": float(row[3] or 0.0),
            }
            for row in hs_rows
        ],
        key=lambda item: item["export_value"],
        reverse=True,
    )[:12]
    mapped_companies = (
        db.query(HSCodeCompanyMap)
        .filter(HSCodeCompanyMap.hs_code.in_(hs_codes or [""]))
        .order_by(HSCodeCompanyMap.stock_name)
        .all()
    )
    return {
        "sector_key": sector.sector_key,
        "label": sector.label,
        "description": sector.description,
        "points": points,
        "hs_mappings": [
            {
                "id": row.id,
                "hs_code": row.hs_code,
                "hs_name": row.hs_name,
                "display_name": row.display_name,
                "mapping_status": row.mapping_status,
                "note": row.note,
            }
            for row in hs_maps
        ],
        "top_hs_codes": top_hs,
        "mapped_companies": [
            {
                "id": row.id,
                "hs_code": row.hs_code,
                "stock_code": row.stock_code,
                "stock_name": row.stock_name,
                "sector_name": row.sector_name,
                "mapping_status": row.mapping_status,
                "confidence": row.confidence,
                "note": row.note,
            }
            for row in mapped_companies
        ],
    }


async def generate_ai_summary(scope_type: str, scope_key: str, payload: dict[str, Any], db: Session) -> str:
    if not is_configured():
        raise ValueError("GEMINI_API_KEY 또는 GOOGLE_API_KEY가 없습니다.")

    prompt = (
        "당신은 한국 수출입 데이터 애널리스트입니다. "
        "주어진 JSON을 바탕으로 4문장 이내 한국어 요약을 작성하세요. "
        "핵심 변화, 주목 포인트, 잠재적 수혜/리스크 기업군을 포함하되 과장하지 마세요.\n"
        f"JSON:\n{json.dumps(payload, ensure_ascii=False)}"
    )
    text = await asyncio.to_thread(
        generate_text,
        prompt,
        system_instruction="당신은 한국 수출입 데이터 애널리스트입니다.",
        temperature=0.2,
        max_output_tokens=500,
        timeout=45,
    )
    cache = db.query(InsightCache).filter(InsightCache.scope_type == scope_type, InsightCache.scope_key == scope_key).first()
    if not cache:
        cache = InsightCache(scope_type=scope_type, scope_key=scope_key)
    cache.summary_text = text
    cache.model_name = model_name()
    cache.detail_json = json.dumps(payload, ensure_ascii=False)
    db.add(cache)
    db.commit()
    return text
