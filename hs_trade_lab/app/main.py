from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from .analytics import generate_ai_summary, sector_detail, sector_features, stock_features
from .config import DATA_DIR, PROJECT_DIR, STATIC_DIR
from .database import Base, SessionLocal, engine, get_db
from ..semiconductor_value_lab.fastapi_app import app as semiconductor_value_lab_app
from .models import (
    CustomsMonthlyRecord,
    DataSourceConfig,
    HSCodeCompanyMap,
    HSSectorMap,
    SectorPreset,
    SyncRun,
    TradeSeriesCache,
)
from .schemas import (
    AIInsightResponse,
    DataSourceConfigIn,
    DataSourceConfigOut,
    FeatureItem,
    GenericMessage,
    HSCodeMapIn,
    HSCodeMapOut,
    HSSectorMapIn,
    HSSectorMapOut,
    HSCodeSuggestRequest,
    HSCodeSuggestResponse,
    SectorDashboardPoint,
    SectorDashboardResponse,
    SectorPresetOut,
    TrendSummary,
    TradeFetchRequest,
    TradeSeriesPoint,
)
from .sector_presets import DEFAULT_SECTORS
from .stock_reference import list_sectors, search_companies
from .trade_connector import fetch_trade_series, suggest_hs_code


DATA_DIR.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="HS Trade Lab", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/semiconductor-lab", semiconductor_value_lab_app)


def _mask_api_key(value: str) -> str:
    value = value or ""
    if len(value) <= 6:
        return "*" * len(value)
    return value[:3] + "*" * (len(value) - 6) + value[-3:]


def _to_map_out(record: HSCodeCompanyMap) -> HSCodeMapOut:
    return HSCodeMapOut(
        id=record.id,
        hs_code=record.hs_code,
        hs_name=record.hs_name,
        stock_code=record.stock_code,
        stock_name=record.stock_name,
        sector_name=record.sector_name,
        match_type=record.match_type,
        mapping_status=record.mapping_status,
        confidence=record.confidence,
        note=record.note,
    )


def _to_sector_map_out(record: HSSectorMap) -> HSSectorMapOut:
    return HSSectorMapOut(
        id=record.id,
        hs_code=record.hs_code,
        hs_name=record.hs_name,
        sector_key=record.sector_key,
        display_name=record.display_name,
        mapping_status=record.mapping_status,
        note=record.note,
    )


def _get_or_create_config(db: Session) -> DataSourceConfig:
    config = db.get(DataSourceConfig, 1)
    if not config:
        config = DataSourceConfig(id=1)
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def _pct_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 2)


def _ensure_sector_presets() -> None:
    db = SessionLocal()
    try:
        existing = {row.sector_key for row in db.query(SectorPreset).all()}
        for item in DEFAULT_SECTORS:
            if item["sector_key"] in existing:
                continue
            db.add(SectorPreset(**item))
        db.commit()
    finally:
        db.close()


_ensure_sector_presets()


@app.get("/")
def index():
    return FileResponse(Path(STATIC_DIR) / "index.html")


@app.get("/api/health", response_model=GenericMessage)
def health():
    return GenericMessage(message="ok")


@app.get("/api/settings/datasource", response_model=DataSourceConfigOut)
def get_datasource(db: Session = Depends(get_db)):
    config = _get_or_create_config(db)
    return DataSourceConfigOut(
        provider_name=config.provider_name,
        base_url=config.base_url,
        endpoint_path=config.endpoint_path,
        api_key="",
        params_json=config.params_json,
        enabled=config.enabled,
        masked_api_key=_mask_api_key(config.api_key),
    )


@app.post("/api/settings/datasource", response_model=DataSourceConfigOut)
def save_datasource(payload: DataSourceConfigIn, db: Session = Depends(get_db)):
    config = _get_or_create_config(db)
    for field in ("provider_name", "base_url", "endpoint_path", "api_key", "params_json", "enabled"):
        setattr(config, field, getattr(payload, field))
    db.add(config)
    db.commit()
    db.refresh(config)
    return DataSourceConfigOut(
        provider_name=config.provider_name,
        base_url=config.base_url,
        endpoint_path=config.endpoint_path,
        api_key="",
        params_json=config.params_json,
        enabled=config.enabled,
        masked_api_key=_mask_api_key(config.api_key),
    )


@app.get("/api/reference/companies")
def company_reference(query: str = "", limit: int = 30):
    return search_companies(query=query, limit=limit)


@app.get("/api/reference/sectors")
def sector_reference(limit: int = 100):
    return {"items": list_sectors(limit=limit)}


@app.get("/api/sectors", response_model=list[SectorPresetOut])
def get_sector_presets(db: Session = Depends(get_db)):
    rows = db.query(SectorPreset).order_by(SectorPreset.sort_order).all()
    return [
        SectorPresetOut(
            sector_key=row.sector_key,
            label=row.label,
            description=row.description,
            sort_order=row.sort_order,
        )
        for row in rows
    ]


@app.get("/api/mappings", response_model=list[HSCodeMapOut])
def list_mappings(db: Session = Depends(get_db)):
    rows = db.query(HSCodeCompanyMap).order_by(HSCodeCompanyMap.hs_code, HSCodeCompanyMap.stock_name).all()
    return [_to_map_out(row) for row in rows]


@app.post("/api/mappings", response_model=HSCodeMapOut)
def create_mapping(payload: HSCodeMapIn, db: Session = Depends(get_db)):
    existing = (
        db.query(HSCodeCompanyMap)
        .filter(HSCodeCompanyMap.hs_code == payload.hs_code, HSCodeCompanyMap.stock_code == payload.stock_code)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 HS Code-기업 매핑입니다.")

    row = HSCodeCompanyMap(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_map_out(row)


@app.delete("/api/mappings/{mapping_id}", response_model=GenericMessage)
def delete_mapping(mapping_id: int, db: Session = Depends(get_db)):
    row = db.get(HSCodeCompanyMap, mapping_id)
    if not row:
        raise HTTPException(status_code=404, detail="매핑을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return GenericMessage(message="deleted")


@app.get("/api/sector-mappings", response_model=list[HSSectorMapOut])
def list_sector_mappings(db: Session = Depends(get_db)):
    rows = db.query(HSSectorMap).order_by(HSSectorMap.sector_key, HSSectorMap.hs_code).all()
    return [_to_sector_map_out(row) for row in rows]


@app.post("/api/sector-mappings", response_model=HSSectorMapOut)
def create_sector_mapping(payload: HSSectorMapIn, db: Session = Depends(get_db)):
    existing = (
        db.query(HSSectorMap)
        .filter(HSSectorMap.hs_code == payload.hs_code, HSSectorMap.sector_key == payload.sector_key)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="이미 등록된 HS Code-섹터 매핑입니다.")
    row = HSSectorMap(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return _to_sector_map_out(row)


@app.delete("/api/sector-mappings/{mapping_id}", response_model=GenericMessage)
def delete_sector_mapping(mapping_id: int, db: Session = Depends(get_db)):
    row = db.get(HSSectorMap, mapping_id)
    if not row:
        raise HTTPException(status_code=404, detail="섹터 매핑을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return GenericMessage(message="deleted")


@app.post("/api/trade/fetch", response_model=GenericMessage)
def pull_trade_series(payload: TradeFetchRequest, db: Session = Depends(get_db)):
    config = _get_or_create_config(db)
    try:
        rows = fetch_trade_series(
            config=config,
            hs_code=payload.hs_code,
            start_ym=payload.start_ym,
            end_ym=payload.end_ym,
            flow_type=payload.flow_type,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if payload.overwrite:
        (
            db.query(TradeSeriesCache)
            .filter(
                TradeSeriesCache.hs_code == payload.hs_code,
                TradeSeriesCache.flow_type == payload.flow_type,
            )
            .delete()
        )

    for item in rows:
        db.add(
            TradeSeriesCache(
                hs_code=payload.hs_code,
                period_ym=item["period_ym"],
                flow_type=payload.flow_type,
                export_value=item["export_value"],
                import_value=item["import_value"],
                trade_balance=item["trade_balance"],
                source_name=item["source_name"],
                raw_json=item["raw_json"],
            )
        )
    db.commit()
    return GenericMessage(message="fetched", detail={"count": len(rows)})


@app.get("/api/trade/{hs_code}", response_model=TrendSummary)
def get_trade_summary(hs_code: str, db: Session = Depends(get_db)):
    rows = (
        db.query(TradeSeriesCache)
        .filter(TradeSeriesCache.hs_code == hs_code)
        .order_by(TradeSeriesCache.period_ym)
        .all()
    )
    mapped = (
        db.query(HSCodeCompanyMap)
        .filter(HSCodeCompanyMap.hs_code == hs_code)
        .order_by(HSCodeCompanyMap.stock_name)
        .all()
    )
    points = [
        TradeSeriesPoint(
            period_ym=row.period_ym,
            export_value=row.export_value,
            import_value=row.import_value,
            trade_balance=row.trade_balance,
            source_name=row.source_name,
        )
        for row in rows
    ]
    export_latest = points[-1].export_value if points else 0.0
    import_latest = points[-1].import_value if points else 0.0
    export_prev = points[-13].export_value if len(points) >= 13 else 0.0
    import_prev = points[-13].import_value if len(points) >= 13 else 0.0
    company_names = ", ".join(item.stock_name for item in mapped[:5]) or "매핑된 기업 없음"
    if points:
        inference = (
            f"{hs_code}의 최신 월간 수출은 {export_latest:,.0f}, 수입은 {import_latest:,.0f}입니다. "
            f"연결 기업/섹터는 {company_names}이며, 최근 추세가 기업 실적 민감도와 이어지는지 추가 검토가 필요합니다."
        )
    else:
        inference = "아직 적재된 수출입 시계열이 없습니다. 데이터소스 설정 후 Fetch를 먼저 실행해 주세요."

    return TrendSummary(
        hs_code=hs_code,
        points=points,
        export_latest=export_latest,
        export_yoy=_pct_change(export_latest, export_prev),
        import_latest=import_latest,
        import_yoy=_pct_change(import_latest, import_prev),
        trade_balance_latest=(points[-1].trade_balance if points else 0.0),
        mapped_companies=[_to_map_out(item) for item in mapped],
        inference=inference,
    )


@app.get("/api/customs/status", response_model=GenericMessage)
def customs_status(db: Session = Depends(get_db)):
    counts = {}
    for endpoint in ("itemtrade", "sidoitemtrade", "sidotempertrade", "nationtrade", "idfytempertrade", "sidotrade"):
        counts[endpoint] = db.query(CustomsMonthlyRecord).filter(CustomsMonthlyRecord.endpoint == endpoint).count()
    latest_period = (
        db.query(CustomsMonthlyRecord.period_ym)
        .filter(CustomsMonthlyRecord.period_ym.like("____-__"))
        .order_by(CustomsMonthlyRecord.period_ym.desc())
        .first()
    )
    return GenericMessage(
        message="ok",
        detail={
            "counts": counts,
            "latest_period": latest_period[0] if latest_period else None,
        },
    )


@app.post("/api/customs/ingest", response_model=GenericMessage)
def customs_ingest(db: Session = Depends(get_db)):
    run = SyncRun(run_type="ingest", status="running", detail_json="{}")
    db.add(run)
    db.commit()
    cmd = [str(PROJECT_DIR.parent / "venv" / "bin" / "python"), str(PROJECT_DIR / "scripts" / "ingest_customs_data.py")]
    proc = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)
    run.status = "done" if proc.returncode == 0 else "failed"
    run.detail_json = proc.stdout or proc.stderr
    db.add(run)
    db.commit()
    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail=proc.stderr or "ingest failed")
    return GenericMessage(message="ingested", detail=proc.stdout)


@app.post("/api/customs/daily-refresh", response_model=GenericMessage)
def customs_daily_refresh(db: Session = Depends(get_db)):
    run = SyncRun(run_type="daily_refresh", status="running", detail_json="{}")
    db.add(run)
    db.commit()
    cmd = [str(PROJECT_DIR.parent / "venv" / "bin" / "python"), str(PROJECT_DIR / "scripts" / "daily_refresh.py")]
    proc = subprocess.run(cmd, cwd=PROJECT_DIR, capture_output=True, text=True)
    run.status = "done" if proc.returncode == 0 else "failed"
    run.detail_json = proc.stdout or proc.stderr
    db.add(run)
    db.commit()
    if proc.returncode != 0:
        raise HTTPException(status_code=400, detail=proc.stderr or "daily refresh failed")
    return GenericMessage(message="refreshed", detail=proc.stdout)


@app.get("/api/dashboard/features", response_model=list[FeatureItem])
def dashboard_features(scope: str = "all", limit: int = 20, db: Session = Depends(get_db)):
    items: list[FeatureItem] = []
    if scope in ("all", "sector"):
        for row in sector_features(db)[:limit]:
            items.append(
                FeatureItem(
                    name=row["label"],
                    item_type="sector",
                    key=row["sector_key"],
                    export_latest=row.get("export_latest", 0.0),
                    export_yoy=row.get("export_yoy"),
                    feature_score=row["feature_score"],
                    extra={"description": row.get("description", "")},
                )
            )
    if scope in ("all", "stock"):
        for row in stock_features(db)[:limit]:
            items.append(
                FeatureItem(
                    name=row["stock_name"],
                    item_type="stock",
                    key=row["stock_code"],
                    export_latest=row["export_latest"],
                    export_yoy=row["export_yoy"],
                    feature_score=row["feature_score"],
                    extra={
                        "sector_name": row.get("sector_name", ""),
                        "hs_codes": row.get("hs_codes", []),
                        "momentum_zscore": row.get("momentum_zscore", 0.0),
                    },
                )
            )
    return sorted(items, key=lambda row: row.feature_score, reverse=True)[:limit]


@app.get("/api/dashboard/sectors/{sector_key}", response_model=SectorDashboardResponse)
def dashboard_sector(sector_key: str, db: Session = Depends(get_db)):
    payload = sector_detail(db, sector_key)
    if not payload:
        raise HTTPException(status_code=404, detail="섹터를 찾을 수 없습니다.")
    return SectorDashboardResponse(
        sector_key=payload["sector_key"],
        label=payload["label"],
        description=payload["description"],
        points=[SectorDashboardPoint(**point) for point in payload["points"]],
        hs_mappings=payload["hs_mappings"],
        top_hs_codes=payload["top_hs_codes"],
        mapped_companies=payload["mapped_companies"],
    )


@app.post("/api/dashboard/ai-summary", response_model=AIInsightResponse)
async def dashboard_ai_summary(scope_type: str, scope_key: str, db: Session = Depends(get_db)):
    if scope_type == "sector":
        payload = sector_detail(db, scope_key)
    elif scope_type == "stock":
        payload = next((item for item in stock_features(db) if item["stock_code"] == scope_key), None)
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 scope_type 입니다.")
    if not payload:
        raise HTTPException(status_code=404, detail="요약 대상을 찾지 못했습니다.")
    try:
        summary = await generate_ai_summary(scope_type, scope_key, payload, db)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return AIInsightResponse(scope_type=scope_type, scope_key=scope_key, summary_text=summary)


@app.post("/api/hs/suggest", response_model=HSCodeSuggestResponse)
async def hs_suggest(payload: HSCodeSuggestRequest, db: Session = Depends(get_db)):
    config = _get_or_create_config(db)
    api_key = payload.api_key or config.api_key
    try:
        result = await suggest_hs_code(payload.product_name, api_key, payload.model)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return HSCodeSuggestResponse(**result)


# ── 수출입분석2 전용 API ─────────────────────────────────────
import sqlite3 as _sl2

def _hs_db() -> _sl2.Connection:
    conn = _sl2.connect(str(DATA_DIR / "hs_trade_lab.db"))
    conn.row_factory = _sl2.Row
    return conn


def _mapping_rank_expr(alias: str = "mapping_status") -> str:
    return (
        f"CASE {alias} "
        "WHEN 'exact' THEN 1 "
        "WHEN 'composite' THEN 2 "
        "ELSE 3 END"
    )


PROVISIONAL_10DAY_CATEGORY = {
    "semiconductors": ("반도체", "반도체"),
    "autos": ("승용차", "승용차"),
    "shipbuilding": ("선박", "기계류"),
    "energy_materials": ("철강제품", "기계류"),
    "consumer": ("가전제품", "무선통신기기"),
}


def _latest_provisional_10day(conn: sqlite3.Connection, sector_key: str | None) -> dict | None:
    if not sector_key:
        return None
    categories = PROVISIONAL_10DAY_CATEGORY.get(sector_key)
    if not categories:
        return None
    export_category, import_category = categories
    export_row = conn.execute(
        """
        SELECT period_ym, period_day, category_name, amount_thousand_usd
        FROM customs_best_available_10day_record
        WHERE dataset_key='export_items_10day'
          AND category_name=?
        ORDER BY period_ym DESC, period_day_order DESC
        LIMIT 1
        """,
        (export_category,),
    ).fetchone()
    import_row = conn.execute(
        """
        SELECT period_ym, period_day, category_name, amount_thousand_usd
        FROM customs_best_available_10day_record
        WHERE dataset_key='import_items_10day'
          AND category_name=?
        ORDER BY period_ym DESC, period_day_order DESC
        LIMIT 1
        """,
        (import_category,),
    ).fetchone()
    if not export_row and not import_row:
        return None
    base = export_row or import_row
    return {
        "period_ym": base["period_ym"],
        "period_day": base["period_day"],
        "export_category": export_row["category_name"] if export_row else "",
        "export_value": round(float(export_row["amount_thousand_usd"] or 0) * 1000) if export_row else None,
        "import_category": import_row["category_name"] if import_row else "",
        "import_value": round(float(import_row["amount_thousand_usd"] or 0) * 1000) if import_row else None,
        "note": "10일 단위 잠정 대분류 통계라 HS/기업별 확정 월별 데이터와 별도 표시됩니다.",
    }


@app.get("/api/analysis2/sectors")
def analysis2_sectors(months: int = 24):
    """섹터별 월간 수출/수입 추세 (최근 N개월). 캐시 테이블 기반."""
    conn = _hs_db()
    rows = conn.execute(
        """
        SELECT c.sector_key, c.sector_label, c.period_ym,
               c.export_value, c.export_weight, c.import_value, c.import_weight
        FROM analysis2_sector_monthly_cache c
        JOIN sector_preset sp ON sp.sector_key = c.sector_key
        WHERE c.period_ym >= date('now', ? || ' months')
        ORDER BY sp.sort_order, c.period_ym
        """,
        (f"-{months}",),
    ).fetchall()
    sectors: dict[str, dict] = {}
    for row in rows:
        sector = sectors.setdefault(
            row["sector_key"],
            {"sector_key": row["sector_key"], "label": row["sector_label"], "monthly": []},
        )
        sector["monthly"].append(
            {
                "period_ym": row["period_ym"],
                "export_val": round(row["export_value"] or 0),
                "export_kg": round(row["export_weight"] or 0),
                "import_val": round(row["import_value"] or 0),
                "import_kg": round(row["import_weight"] or 0),
            }
        )

    def _pct(current: float | None, previous: float | None) -> float | None:
        if not current or not previous:
            return None
        return round(((current - previous) / previous) * 100, 2)

    result = []
    for sector in sectors.values():
        monthly = sector["monthly"]
        provisional = _latest_provisional_10day(conn, sector["sector_key"])
        # 가집계 월 데이터를 monthly 시리즈에 추가 (확정 최신월보다 최신이면)
        confirmed_latest_ym = monthly[-1]["period_ym"] if monthly else ""
        if provisional and provisional.get("period_ym", "") > confirmed_latest_ym:
            prov_ym = provisional["period_ym"]
            prov_export = provisional.get("export_value") or 0
            prov_import = provisional.get("import_value") or 0
            monthly = monthly + [{
                "period_ym": prov_ym,
                "export_val": round(prov_export),
                "export_kg": 0,
                "import_val": round(prov_import),
                "import_kg": 0,
                "is_provisional": True,
                "period_day": provisional.get("period_day", ""),
            }]
        # MoM/YoY는 확정치만 비교 (잠정 10일치 vs 1달 확정치 비교 방지)
        cm = [m for m in monthly if not m.get('is_provisional')]
        export_latest = cm[-1]["export_val"] if cm else None
        export_prev1 = cm[-2]["export_val"] if len(cm) >= 2 else None
        export_prev12 = cm[-13]["export_val"] if len(cm) >= 13 else None
        import_latest = cm[-1]["import_val"] if cm else None
        import_prev1 = cm[-2]["import_val"] if len(cm) >= 2 else None
        import_prev12 = cm[-13]["import_val"] if len(cm) >= 13 else None
        result.append(
            {
                **sector,
                "monthly": monthly,
                "latest_period": monthly[-1]["period_ym"] if monthly else None,
                "provisional_10day": provisional,
                "export_latest": export_latest,
                "export_mom": _pct(export_latest, export_prev1),
                "export_yoy": _pct(export_latest, export_prev12),
                "import_latest": import_latest,
                "import_mom": _pct(import_latest, import_prev1),
                "import_yoy": _pct(import_latest, import_prev12),
                "import_dependency": round((import_latest / export_latest) * 100, 2) if export_latest else None,
                "mom": _pct(export_latest, export_prev1),
                "yoy": _pct(export_latest, export_prev12),
            }
        )
    conn.close()
    return result


@app.get("/api/analysis2/companies")
def analysis2_all_companies(months: int = 3):
    """전체 기업 목록 — 섹터 구분 없이 최근 월 기준 합산 수출액 정렬."""
    conn = _hs_db()
    rows = conn.execute(
        """
        SELECT stock_code, stock_name,
               GROUP_CONCAT(DISTINCT sector_key) AS sector_keys,
               GROUP_CONCAT(DISTINCT hs_names) AS hs_names_agg,
               GROUP_CONCAT(DISTINCT sector_label) AS sector_labels,
               GROUP_CONCAT(DISTINCT mapping_status) AS statuses,
               SUM(export_value) AS total_export,
               MAX(period_ym) AS latest_period
        FROM analysis2_company_monthly_cache
        WHERE period_ym >= date('now', ? || ' months')
        GROUP BY stock_code, stock_name
        HAVING MAX(export_value) > 0
        ORDER BY total_export DESC
        """,
        (f"-{months}",),
    ).fetchall()
    conn.close()
    return [
        {
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "sector_keys": (r["sector_keys"] or "").split(","),
            "sector_labels": (r["sector_labels"] or "").split(","),
            "hs_names": r["hs_names_agg"] or "",
            "mapping_status": "exact" if "exact" in (r["statuses"] or "") else ("composite" if "composite" in (r["statuses"] or "") else "provisional"),
            "latest_period": r["latest_period"],
            "total_export": round(r["total_export"] or 0),
        }
        for r in rows
    ]


@app.get("/api/analysis2/company/{stock_code}/by-product")
def analysis2_company_by_product(stock_code: str, months: int = 24):
    """기업별 품목(HS코드)별 월간 수출 상세 — 기업별 탭용."""
    conn = _hs_db()
    # 최근 N개월 HS별 월간 데이터
    # 국가 단위 통계(전국/글로벌) 제외: 회사 고유 HS 데이터만 표시
    hs_rows = conn.execute(
        """
        SELECT period_ym, hs_code, hs_name, sector_key, sector_label, sector_name, flow_type,
               mapping_status, export_value, export_weight, import_value, import_weight
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND period_ym >= date('now', ? || ' months')
          AND sector_name NOT LIKE '전국%'
          AND sector_name NOT IN ('글로벌', '중국', '')
        ORDER BY period_ym DESC, export_value DESC
        """,
        (stock_code, f"-{months}"),
    ).fetchall()
    # 기업 기본정보
    info_row = conn.execute(
        """
        SELECT stock_name, GROUP_CONCAT(DISTINCT sector_key) AS sector_keys,
               GROUP_CONCAT(DISTINCT sector_label) AS sector_labels,
               GROUP_CONCAT(DISTINCT hs_names) AS hs_names,
               MAX(mapping_status) AS mapping_status
        FROM analysis2_company_monthly_cache
        WHERE stock_code = ?
        """,
        (stock_code,),
    ).fetchone()

    # HS 매핑이 없는 기업: 기업의 수출 비중 상위 섹터 HS 구성으로 대체
    is_sector_fallback = False
    if not hs_rows:
        # 기업의 섹터별 실제 수출 비중 (최근 3개월) - 비중 높은 섹터 우선
        comp_sector_rows = conn.execute(
            """
            SELECT sector_key, SUM(export_value) AS total_export
            FROM analysis2_company_monthly_cache
            WHERE stock_code = ? AND period_ym >= date('now', '-3 months')
            GROUP BY sector_key
            ORDER BY total_export DESC
            LIMIT 3
            """,
            (stock_code,),
        ).fetchall()
        top_sectors = [r["sector_key"] for r in comp_sector_rows if r["sector_key"]]
        if top_sectors:
            placeholders = ",".join("?" * len(top_sectors))
            hs_rows = conn.execute(
                f"""
                SELECT period_ym, hs_code, hs_name, sector_key AS sector_name,
                       mapping_status, export_value, export_weight, import_value, import_weight
                FROM analysis2_sector_hs_monthly_cache
                WHERE sector_key IN ({placeholders})
                  AND period_ym >= date('now', '-3 months')
                ORDER BY period_ym DESC, export_value DESC
                """,
                top_sectors,
            ).fetchall()
            is_sector_fallback = True

    # Telegram 게시물 관련 정보
    tg_rows = conn.execute(
        """
        SELECT DISTINCT t.post_message_id, t.posted_at, t.post_url, t.product_title,
               t.flow_type, t.hs_code, t.hs_name, t.flow_scope, t.mapping_status
        FROM telegram_company_hs_flow_map t
        WHERE t.stock_code = ?
        ORDER BY t.posted_at DESC
        LIMIT 30
        """,
        (stock_code,),
    ).fetchall()
    # HS코드별 시장점유율: hs_code_company_map에서 가져옴
    share_rows = conn.execute(
        """
        SELECT m.hs_code, m.market_share_pct,
               (SELECT COUNT(*) FROM hs_code_company_map m2 WHERE m2.hs_code = m.hs_code) AS total_companies
        FROM hs_code_company_map m
        WHERE m.stock_code = ?
        """,
        (stock_code,),
    ).fetchall()
    conn.close()
    # {hs_code: export_share(0~1 또는 None)}
    share_map: dict[str, float | None] = {}
    for sr in share_rows:
        if sr["market_share_pct"] is not None:
            share_map[sr["hs_code"]] = sr["market_share_pct"]
        elif sr["total_companies"] == 1:
            share_map[sr["hs_code"]] = 1.0  # 단독 매핑 → 100%

    # HS 코드별 집계 (최근 3개월 합산)
    from collections import defaultdict
    hs_summary: dict[str, dict] = defaultdict(lambda: {
        "hs_code": "", "hs_name": "", "sector_name": "", "flow_type": "export",
        "mapping_status": "provisional", "export_share": None,
        "export_3m": 0, "export_kg_3m": 0, "import_3m": 0, "monthly": []
    })
    for r in hs_rows:
        flow_type = "export"
        try:
            flow_type = r["flow_type"] or "export"
        except Exception:
            pass
        key = f"{r['hs_code']}_{flow_type}"
        s = hs_summary[key]
        s["hs_code"] = r["hs_code"]
        s["hs_name"] = r["hs_name"]
        try:
            s["sector_name"] = r["sector_name"] or r.get("sector_label", "")
        except Exception:
            s["sector_name"] = r["sector_name"] if r["sector_name"] else ""
        s["flow_type"] = flow_type
        s["mapping_status"] = r["mapping_status"]
        if not is_sector_fallback:
            s["export_share"] = share_map.get(r["hs_code"])
        s["monthly"].append({
            "period_ym": r["period_ym"],
            "export_val": round(r["export_value"] or 0),
            "export_kg": round(r["export_weight"] or 0),
            "import_val": round(r["import_value"] or 0),
            "import_kg": round(r["import_weight"] or 0),
            "unit_price_kg": round((r["export_value"] or 0) / r["export_weight"]) if r["export_weight"] else None,
        })
        s["export_3m"] += r["export_value"] or 0
        s["export_kg_3m"] += r["export_weight"] or 0
        s["import_3m"] += r["import_value"] or 0

    items = sorted(hs_summary.values(), key=lambda x: -x["export_3m"])[:30]
    for item in items:
        item["monthly"].sort(key=lambda x: x["period_ym"])
        item["export_3m"] = round(item["export_3m"])
        item["import_3m"] = round(item["import_3m"])
        item["avg_unit_price_kg"] = round(item["export_3m"] / item["export_kg_3m"]) if item["export_kg_3m"] else None
        # export_share를 % 단위로 변환 (0~1 → 0~100)
        if item["export_share"] is not None:
            item["export_share"] = round(item["export_share"] * 100, 1)

    return {
        "stock_code": stock_code,
        "stock_name": info_row["stock_name"] if info_row else stock_code,
        "sector_keys": (info_row["sector_keys"] or "").split(",") if info_row else [],
        "sector_labels": (info_row["sector_labels"] or "").split(",") if info_row else [],
        "hs_names": info_row["hs_names"] if info_row else "",
        "mapping_status": info_row["mapping_status"] if info_row else "provisional",
        "is_sector_fallback": is_sector_fallback,
        "items": items,
        "telegram_products": [
            {
                "posted_at": r["posted_at"],
                "post_url": r["post_url"],
                "product_title": r["product_title"],
                "flow_type": r["flow_type"],
                "hs_code": r["hs_code"],
                "hs_name": r["hs_name"],
                "flow_scope": r["flow_scope"],
                "mapping_status": r["mapping_status"],
            }
            for r in tg_rows
        ],
    }


@app.get("/api/analysis2/sector/{sector_key}/companies")
def analysis2_sector_companies(sector_key: str):
    """섹터에 매핑된 기업 목록. 캐시 최신월 기준."""
    conn = _hs_db()
    latest_period_row = conn.execute(
        "SELECT MAX(period_ym) AS latest_period FROM analysis2_company_monthly_cache WHERE sector_key = ?",
        (sector_key,),
    ).fetchone()
    latest_period = latest_period_row["latest_period"] if latest_period_row else None
    rows = []
    if latest_period:
        rows = conn.execute(
            """
            SELECT stock_code, stock_name, sector_names, hs_names, mapping_status,
                   export_value, import_value
            FROM analysis2_company_monthly_cache
            WHERE sector_key = ?
              AND period_ym = ?
            ORDER BY export_value DESC,
                     CASE mapping_status
                       WHEN 'exact' THEN 1
                       WHEN 'composite' THEN 2
                       ELSE 3 END,
                     stock_name
            """,
            (sector_key, latest_period),
        ).fetchall()
    conn.close()
    return [
        {
            "stock_code": r["stock_code"],
            "stock_name": r["stock_name"],
            "sector_name": r["sector_names"],
            "hs_names": r["hs_names"],
            "mapping_status": r["mapping_status"],
            "latest_period": latest_period,
            "export_latest": round(r["export_value"] or 0),
            "import_latest": round(r["import_value"] or 0),
        }
        for r in rows
    ]


@app.get("/api/analysis2/company/{stock_code}/trend")
def analysis2_company_trend(stock_code: str, months: int = 24, sector_key: str | None = None):
    """기업 월간 수출/수입 추세. 캐시 테이블 기반."""
    conn = _hs_db()
    params: list = [stock_code]
    sector_where = ""
    if sector_key:
        sector_where = "AND sector_key = ?"
        params.append(sector_key)

    info = conn.execute(
        f"""
        SELECT stock_name, sector_label, sector_names, hs_names, mapping_status
        FROM analysis2_company_monthly_cache
        WHERE stock_code = ?
          {sector_where}
        ORDER BY CASE mapping_status
                   WHEN 'exact' THEN 1
                   WHEN 'composite' THEN 2
                   ELSE 3 END,
                 period_ym DESC
        LIMIT 1
        """,
        params,
    ).fetchone()

    # sector_key 없을 때 모든 섹터를 월별 합산 (GROUP BY) — 중복 행 방지
    if sector_key:
        rows = conn.execute(
            """
            SELECT period_ym, export_value, export_weight, import_value, import_weight, hs_names
            FROM analysis2_company_monthly_cache
            WHERE stock_code = ?
              AND sector_key = ?
              AND period_ym >= date('now', ? || ' months')
            ORDER BY period_ym
            """,
            [stock_code, sector_key, f"-{months}"],
        ).fetchall()
        monthly = [
            {
                "period_ym": row["period_ym"],
                "export_val": round(row["export_value"] or 0),
                "export_kg": round(row["export_weight"] or 0),
                "import_val": round(row["import_value"] or 0),
                "import_kg": round(row["import_weight"] or 0),
                "hs_names": row["hs_names"],
            }
            for row in rows
        ]
    else:
        # 전체 섹터 합산: period_ym 기준 GROUP BY
        rows = conn.execute(
            """
            SELECT period_ym,
                   SUM(export_value)  AS export_value,
                   SUM(export_weight) AS export_weight,
                   SUM(import_value)  AS import_value,
                   SUM(import_weight) AS import_weight
            FROM analysis2_company_monthly_cache
            WHERE stock_code = ?
              AND period_ym >= date('now', ? || ' months')
            GROUP BY period_ym
            ORDER BY period_ym
            """,
            [stock_code, f"-{months}"],
        ).fetchall()
        monthly = [
            {
                "period_ym": row["period_ym"],
                "export_val": round(row["export_value"] or 0),
                "export_kg": round(row["export_weight"] or 0),
                "import_val": round(row["import_value"] or 0),
                "import_kg": round(row["import_weight"] or 0),
                "hs_names": "",
            }
            for row in rows
        ]

    def _pct(current: float | None, previous: float | None) -> float | None:
        if not current or not previous:
            return None
        return round(((current - previous) / previous) * 100, 2)

    # MoM/YoY는 확정치만 비교 (잠정 10일치 vs 1달 확정치 비교 방지)
    cm = [m for m in monthly if not m.get('is_provisional')]
    export_latest = cm[-1]["export_val"] if cm else None
    export_prev1 = cm[-2]["export_val"] if len(cm) >= 2 else None
    export_prev12 = cm[-13]["export_val"] if len(cm) >= 13 else None
    import_latest = cm[-1]["import_val"] if cm else None
    import_prev1 = cm[-2]["import_val"] if len(cm) >= 2 else None
    import_prev12 = cm[-13]["import_val"] if len(cm) >= 13 else None

    # sector_key 없을 때 가집계용으로 수출 비중 가장 큰 섹터 자동 선택
    prov_sector = sector_key
    if not prov_sector:
        top_sector_row = conn.execute(
            """
            SELECT sector_key FROM analysis2_company_monthly_cache
            WHERE stock_code = ? AND period_ym >= date('now', '-3 months')
            GROUP BY sector_key
            ORDER BY SUM(export_value) DESC LIMIT 1
            """,
            (stock_code,),
        ).fetchone()
        if top_sector_row:
            prov_sector = top_sector_row["sector_key"]

    provisional = _latest_provisional_10day(conn, prov_sector)

    # 가집계 데이터를 monthly에 추가 (섹터 비중으로 추정)
    confirmed_latest_ym = monthly[-1]["period_ym"] if monthly else ""
    if provisional and provisional.get("period_ym", "") > confirmed_latest_ym and monthly:
        prov_ym = provisional["period_ym"]
        # 기업의 섹터 내 비중: 최근 3개월 기업 수출 / 섹터 수출
        comp_3m = sum(m["export_val"] for m in monthly[-3:]) if len(monthly) >= 1 else 0
        sector_rows_3m = conn.execute(
            """
            SELECT COALESCE(SUM(export_value), 0) AS total
            FROM analysis2_sector_monthly_cache
            WHERE sector_key = ? AND period_ym >= ?
            """,
            (sector_key, monthly[-3]["period_ym"] if len(monthly) >= 3 else monthly[-1]["period_ym"]),
        ).fetchone() if sector_key else None
        sector_3m = float(sector_rows_3m["total"]) if sector_rows_3m else 0
        share = (comp_3m / sector_3m) if sector_3m > 0 else 0
        prov_export = round((provisional.get("export_value") or 0) * share)
        prov_import = round((provisional.get("import_value") or 0) * share)
        if prov_export > 0:
            monthly = monthly + [{
                "period_ym": prov_ym,
                "export_val": prov_export,
                "export_kg": 0,
                "import_val": prov_import,
                "import_kg": 0,
                "hs_names": "",
                "is_provisional": True,
                "period_day": provisional.get("period_day", ""),
            }]

    conn.close()

    return {
        "stock_code": stock_code,
        "stock_name": info["stock_name"] if info else stock_code,
        "sector_name": info["sector_names"] if info else "",
        "hs_names": info["hs_names"] if info else "",
        "mapping_status": info["mapping_status"] if info else "provisional",
        "sector_key": sector_key,
        "latest_period": monthly[-1]["period_ym"] if monthly else None,
        "provisional_10day": provisional,
        "monthly": monthly,
        "export_latest": export_latest,
        "export_mom": _pct(export_latest, export_prev1),
        "export_yoy": _pct(export_latest, export_prev12),
        "import_latest": import_latest,
        "import_mom": _pct(import_latest, import_prev1),
        "import_yoy": _pct(import_latest, import_prev12),
        "import_dependency": round((import_latest / export_latest) * 100, 2) if export_latest else None,
        "mom": _pct(export_latest, export_prev1),
        "yoy": _pct(export_latest, export_prev12),
    }


@app.get("/api/analysis2/sector/{sector_key}/hs-breakdown")
def analysis2_sector_hs_breakdown(sector_key: str, period_ym: str | None = None):
    """선택 섹터의 HS 코드별 수출입 구성."""
    conn = _hs_db()
    latest_period_row = conn.execute(
        "SELECT MAX(period_ym) AS latest_period FROM analysis2_sector_hs_monthly_cache WHERE sector_key = ?",
        (sector_key,),
    ).fetchone()
    chosen_period = period_ym or (latest_period_row["latest_period"] if latest_period_row else None)
    if not chosen_period:
        conn.close()
        return {"sector_key": sector_key, "period_ym": None, "items": []}
    rows = conn.execute(
        """
        SELECT sector_label, hs_code, hs_name, mapping_status,
               export_value, export_weight, import_value, import_weight
        FROM analysis2_sector_hs_monthly_cache
        WHERE sector_key = ?
          AND period_ym = ?
        ORDER BY export_value DESC, hs_code
        """,
        (sector_key, chosen_period),
    ).fetchall()
    totals = conn.execute(
        """
        SELECT SUM(export_value) AS export_total, SUM(import_value) AS import_total
        FROM analysis2_sector_hs_monthly_cache
        WHERE sector_key = ?
          AND period_ym = ?
        """,
        (sector_key, chosen_period),
    ).fetchone()
    periods = conn.execute(
        """
        SELECT DISTINCT period_ym
        FROM analysis2_sector_hs_monthly_cache
        WHERE sector_key = ?
        ORDER BY period_ym DESC
        """,
        (sector_key,),
    ).fetchall()
    conn.close()
    export_total = totals["export_total"] or 0
    import_total = totals["import_total"] or 0
    return {
        "sector_key": sector_key,
        "sector_label": rows[0]["sector_label"] if rows else sector_key,
        "period_ym": chosen_period,
        "periods": [row["period_ym"] for row in periods],
        "items": [
            {
                "hs_code": row["hs_code"],
                "hs_name": row["hs_name"],
                "mapping_status": row["mapping_status"],
                "export_val": round(row["export_value"] or 0),
                "export_kg": round(row["export_weight"] or 0),
                "import_val": round(row["import_value"] or 0),
                "import_kg": round(row["import_weight"] or 0),
                "export_share": round(((row["export_value"] or 0) / export_total) * 100, 2) if export_total else None,
                "import_share": round(((row["import_value"] or 0) / import_total) * 100, 2) if import_total else None,
            }
            for row in rows
        ],
    }


@app.get("/api/analysis2/company/{stock_code}/hs-breakdown")
def analysis2_company_hs_breakdown(stock_code: str, sector_key: str, period_ym: str | None = None):
    """기업의 HS 코드별 수출/수입 비중."""
    conn = _hs_db()
    _VS = "sector_name NOT LIKE '전국%' AND sector_name NOT IN ('글로벌','중국','')"
    latest_period_row = conn.execute(
        f"""
        SELECT MAX(period_ym) AS latest_period
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND sector_key = ?
          AND {_VS}
        """,
        (stock_code, sector_key),
    ).fetchone()
    chosen_period = period_ym or (latest_period_row["latest_period"] if latest_period_row else None)

    # 기업별 HS 데이터 없으면 섹터 HS 데이터로 대체
    if not chosen_period:
        fallback_period_row = conn.execute(
            "SELECT MAX(period_ym) AS latest_period FROM analysis2_sector_hs_monthly_cache WHERE sector_key = ?",
            (sector_key,),
        ).fetchone()
        chosen_period = period_ym or (fallback_period_row["latest_period"] if fallback_period_row else None)
        if not chosen_period:
            conn.close()
            return {"stock_code": stock_code, "sector_key": sector_key, "period_ym": None, "items": [], "export_items": [], "import_items": []}
        fallback_rows = conn.execute(
            """
            SELECT sector_label, hs_code, hs_name, mapping_status,
                   export_value, export_weight, import_value, import_weight,
                   'export' AS flow_type, '' AS sector_name
            FROM analysis2_sector_hs_monthly_cache
            WHERE sector_key = ? AND period_ym = ?
            ORDER BY export_value DESC
            LIMIT 30
            """,
            (sector_key, chosen_period),
        ).fetchall()
        export_total = sum(r["export_value"] or 0 for r in fallback_rows)
        import_total = sum(r["import_value"] or 0 for r in fallback_rows)
        items = [
            {
                "hs_code": r["hs_code"], "hs_name": r["hs_name"], "sector_name": "섹터 추정",
                "flow_type": "export", "mapping_status": r["mapping_status"],
                "export_val": round(r["export_value"] or 0), "export_kg": round(r["export_weight"] or 0),
                "import_val": round(r["import_value"] or 0), "import_kg": round(r["import_weight"] or 0),
                "export_share": round(((r["export_value"] or 0) / export_total) * 100, 2) if export_total else None,
                "import_share": round(((r["import_value"] or 0) / import_total) * 100, 2) if import_total else None,
            }
            for r in fallback_rows
        ]
        periods_rows = conn.execute(
            "SELECT DISTINCT period_ym FROM analysis2_sector_hs_monthly_cache WHERE sector_key = ? ORDER BY period_ym DESC",
            (sector_key,),
        ).fetchall()
        conn.close()
        return {
            "stock_code": stock_code, "stock_name": stock_code,
            "sector_key": sector_key, "sector_label": sector_key,
            "period_ym": chosen_period,
            "periods": [r["period_ym"] for r in periods_rows],
            "is_sector_fallback": True,
            "items": items, "export_items": items, "import_items": [],
        }

    if not chosen_period:
        conn.close()
        return {"stock_code": stock_code, "sector_key": sector_key, "period_ym": None, "items": [], "export_items": [], "import_items": []}
    rows = conn.execute(
        f"""
        SELECT stock_name, sector_label, hs_code, hs_name, sector_name, flow_type, mapping_status,
               export_value, export_weight, import_value, import_weight
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND sector_key = ?
          AND period_ym = ?
          AND {_VS}
        ORDER BY CASE flow_type WHEN 'export' THEN 0 ELSE 1 END,
                 CASE flow_type WHEN 'export' THEN export_value ELSE import_value END DESC,
                 hs_code
        """,
        (stock_code, sector_key, chosen_period),
    ).fetchall()
    totals = conn.execute(
        f"""
        SELECT
          SUM(CASE WHEN flow_type = 'export' THEN export_value ELSE 0 END) AS export_total,
          SUM(CASE WHEN flow_type = 'import' THEN import_value ELSE 0 END) AS import_total
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND sector_key = ?
          AND period_ym = ?
          AND {_VS}
        """,
        (stock_code, sector_key, chosen_period),
    ).fetchone()
    periods = conn.execute(
        f"""
        SELECT DISTINCT period_ym
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND sector_key = ?
          AND {_VS}
        ORDER BY period_ym DESC
        """,
        (stock_code, sector_key),
    ).fetchall()
    conn.close()
    export_total = totals["export_total"] or 0
    import_total = totals["import_total"] or 0
    items = []
    for row in rows:
        flow_type = row["flow_type"] or "export"
        export_share = round(((row["export_value"] or 0) / export_total) * 100, 2) if flow_type == "export" and export_total else None
        import_share = round(((row["import_value"] or 0) / import_total) * 100, 2) if flow_type == "import" and import_total else None
        items.append(
            {
                "hs_code": row["hs_code"],
                "hs_name": row["hs_name"],
                "sector_name": row["sector_name"],
                "flow_type": flow_type,
                "mapping_status": row["mapping_status"],
                "export_val": round(row["export_value"] or 0),
                "export_kg": round(row["export_weight"] or 0),
                "import_val": round(row["import_value"] or 0),
                "import_kg": round(row["import_weight"] or 0),
                "export_share": export_share,
                "import_share": import_share,
            }
        )
    return {
        "stock_code": stock_code,
        "stock_name": rows[0]["stock_name"] if rows else stock_code,
        "sector_key": sector_key,
        "sector_label": rows[0]["sector_label"] if rows else sector_key,
        "period_ym": chosen_period,
        "periods": [row["period_ym"] for row in periods],
        "items": items,
        "export_items": [item for item in items if item["flow_type"] == "export"],
        "import_items": [item for item in items if item["flow_type"] == "import"],
    }


# ── 개별종목용 수출 요약 ──────────────────────────────────────────────
@app.get("/api/analysis2/stock/{stock_code}/export-summary")
def analysis2_stock_export_summary(stock_code: str, months: int = 12):
    """개별종목 페이지용 수출 요약 — 최근 N개월 추세 + 주요 품목."""
    conn = _hs_db()

    # 월별 수출 추세 (모든 섹터 합산, 중복 제거)
    monthly_rows = conn.execute(
        """
        SELECT period_ym,
               SUM(export_value) AS export_val,
               SUM(export_weight) AS export_kg,
               SUM(import_value) AS import_val
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND period_ym >= date('now', ? || ' months')
          AND flow_type = 'export'
        GROUP BY period_ym
        ORDER BY period_ym
        """,
        (stock_code, f"-{months}"),
    ).fetchall()

    # 최근 3개월 주요 품목
    top_items = conn.execute(
        """
        SELECT hs_code, hs_name, flow_type,
               SUM(export_value) AS export_3m,
               SUM(export_weight) AS export_kg_3m,
               MAX(mapping_status) AS mapping_status
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND period_ym >= date('now', '-3 months')
        GROUP BY hs_code, hs_name, flow_type
        ORDER BY export_3m DESC
        LIMIT 8
        """,
        (stock_code,),
    ).fetchall()

    # 회사 기본 정보 + 시장 비율 표시
    market_shares = conn.execute(
        """
        SELECT ms.hs_code, ms.hs_name, ms.share_pct, ms.basis, ms.as_of_year,
               GROUP_CONCAT(ms2.stock_name || ':' || ROUND(ms2.share_pct*100) || '%') AS peers
        FROM hs_company_market_share ms
        LEFT JOIN hs_company_market_share ms2
          ON ms2.hs_code = ms.hs_code AND ms2.stock_code != ms.stock_code
        WHERE ms.stock_code = ?
        GROUP BY ms.hs_code, ms.hs_name, ms.share_pct, ms.basis, ms.as_of_year
        """,
        (stock_code,),
    ).fetchall()

    # 전월비/전년동월비 계산
    monthly = [
        {"period_ym": r["period_ym"], "export_val": round(r["export_val"] or 0),
         "export_kg": round(r["export_kg"] or 0), "import_val": round(r["import_val"] or 0)}
        for r in monthly_rows
    ]
    export_latest = monthly[-1]["export_val"] if monthly else None
    export_prev1 = monthly[-2]["export_val"] if len(monthly) >= 2 else None
    export_prev12 = monthly[-13]["export_val"] if len(monthly) >= 13 else None

    conn.close()
    return {
        "stock_code": stock_code,
        "monthly": monthly,
        "export_latest": export_latest,
        "export_mom": _pct(export_latest, export_prev1),
        "export_yoy": _pct(export_latest, export_prev12),
        "top_items": [
            {
                "hs_code": r["hs_code"], "hs_name": r["hs_name"],
                "flow_type": r["flow_type"],
                "export_3m": round(r["export_3m"] or 0),
                "export_kg_3m": round(r["export_kg_3m"] or 0),
                "avg_unit_price_kg": round(r["export_3m"] / r["export_kg_3m"]) if r["export_kg_3m"] else None,
                "mapping_status": r["mapping_status"],
            }
            for r in top_items
        ],
        "market_shares": [
            {
                "hs_code": r["hs_code"], "hs_name": r["hs_name"],
                "share_pct": r["share_pct"], "share_pct_label": f"{round(r['share_pct']*100)}%",
                "basis": r["basis"], "as_of_year": r["as_of_year"],
                "peers": r["peers"] or "",
            }
            for r in market_shares
        ],
    }


# ── 수출경쟁력 건강 점수 ─────────────────────────────────────────────
@app.get("/api/analysis2/export-health")
def analysis2_export_health(months: int = 6):
    """섹터 및 기업 수출 건강 점수 — 최근 성장률 기반."""
    import datetime as _dt
    conn = _hs_db()

    def _score(mom, yoy):
        pts = 0
        if mom is not None:
            pts += 1 if mom > 5 else (-1 if mom < -5 else 0)
        if yoy is not None:
            pts += 2 if yoy > 10 else (-2 if yoy < -10 else (1 if yoy > 0 else -1))
        return "good" if pts >= 2 else ("bad" if pts <= -2 else "neutral")

    # ── 섹터 건강 점수 ──────────────────────────────────────────
    sector_rows = conn.execute("""
        WITH numbered AS (
            SELECT sector_key, sector_label, period_ym,
                   export_value, import_value,
                   ROW_NUMBER() OVER (PARTITION BY sector_key ORDER BY period_ym DESC) AS rn
            FROM analysis2_sector_monthly_cache
        )
        SELECT l.sector_key, l.sector_label, l.period_ym,
               l.export_value, l.import_value,
               CASE WHEN p1.export_value > 0
                    THEN ROUND((l.export_value - p1.export_value) / p1.export_value * 100, 1)
                    END AS export_mom,
               CASE WHEN p12.export_value > 0
                    THEN ROUND((l.export_value - p12.export_value) / p12.export_value * 100, 1)
                    END AS export_yoy,
               CASE WHEN p1.import_value > 0
                    THEN ROUND((l.import_value - p1.import_value) / p1.import_value * 100, 1)
                    END AS import_mom,
               CASE WHEN p12.import_value > 0
                    THEN ROUND((l.import_value - p12.import_value) / p12.import_value * 100, 1)
                    END AS import_yoy
        FROM numbered l
        LEFT JOIN numbered p1  ON p1.sector_key  = l.sector_key AND p1.rn  = 2
        LEFT JOIN numbered p12 ON p12.sector_key = l.sector_key AND p12.rn = 13
        WHERE l.rn = 1
        ORDER BY l.export_value DESC
    """).fetchall()

    # 섹터별 최근 12개월 월별 수출 (미니차트용)
    sector_monthly_rows = conn.execute("""
        SELECT sector_key, period_ym, export_value,
               (period_ym = (SELECT MAX(period_ym) FROM analysis2_sector_monthly_cache
                             WHERE period_ym LIKE period_ym || '%')) AS is_latest
        FROM analysis2_sector_monthly_cache
        WHERE period_ym >= strftime('%Y-%m', date('now', '-12 months'))
        ORDER BY sector_key, period_ym
    """).fetchall()

    # 가집계 기간 구하기 (이달 초부터 10일 이내)
    today = _dt.date.today()
    current_ym = today.strftime("%Y-%m")
    prov_boundary = today.replace(day=1) + _dt.timedelta(days=9)
    is_prov_fn = lambda ym: ym == current_ym and today <= prov_boundary

    sector_monthly: dict[str, list] = {}
    for r in sector_monthly_rows:
        sector_monthly.setdefault(r["sector_key"], []).append({
            "period_ym": r["period_ym"],
            "value": r["export_value"],
            "is_provisional": is_prov_fn(r["period_ym"]),
        })

    # ── 기업 건강 점수 ──────────────────────────────────────────
    company_rows = conn.execute("""
        WITH agg AS (
            SELECT stock_code, MAX(stock_name) AS stock_name, MAX(sector_label) AS sector_label,
                   period_ym,
                   SUM(export_value) AS export_val,
                   SUM(import_value) AS import_val,
                   ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY period_ym DESC) AS rn
            FROM analysis2_company_monthly_cache
            GROUP BY stock_code, period_ym
        )
        SELECT l.stock_code, l.stock_name, l.sector_label, l.period_ym,
               l.export_val, l.import_val,
               CASE WHEN p1.export_val > 0
                    THEN ROUND((l.export_val - p1.export_val) / p1.export_val * 100, 1) END AS export_mom,
               CASE WHEN p12.export_val > 0
                    THEN ROUND((l.export_val - p12.export_val) / p12.export_val * 100, 1) END AS export_yoy,
               CASE WHEN p1.import_val > 0
                    THEN ROUND((l.import_val - p1.import_val) / p1.import_val * 100, 1) END AS import_mom,
               CASE WHEN p12.import_val > 0
                    THEN ROUND((l.import_val - p12.import_val) / p12.import_val * 100, 1) END AS import_yoy
        FROM agg l
        LEFT JOIN agg p1  ON p1.stock_code  = l.stock_code AND p1.rn  = 2
        LEFT JOIN agg p12 ON p12.stock_code = l.stock_code AND p12.rn = 13
        WHERE l.rn = 1
          AND l.export_val > 1e6
        ORDER BY export_yoy DESC NULLS LAST
        LIMIT 100
    """).fetchall()

    # ── 공유 HS 코드 목록 (2개 이상 기업) ──────────────────────
    shared_rows = conn.execute("""
        SELECT hcm.hs_code,
               COALESCE(NULLIF(sm.display_name,''), NULLIF(sm.hs_name,''), hcm.hs_code) AS hs_name,
               COUNT(DISTINCT hcm.stock_code) AS company_count
        FROM hs_code_company_map hcm
        LEFT JOIN hs_sector_map sm ON sm.hs_code = hcm.hs_code
        GROUP BY hcm.hs_code
        HAVING company_count >= 2
        ORDER BY company_count DESC, hcm.hs_code
        LIMIT 30
    """).fetchall()

    conn.close()

    return {
        "updated_at": today.strftime("%Y-%m-%d"),
        "sectors": [
            {
                "sector_key": r["sector_key"], "sector_label": r["sector_label"],
                "period_ym": r["period_ym"],
                "export_latest": round(r["export_value"] or 0),
                "import_latest": round(r["import_value"] or 0),
                "export_mom": r["export_mom"], "export_yoy": r["export_yoy"],
                "import_mom": r["import_mom"], "import_yoy": r["import_yoy"],
                "export_health": _score(r["export_mom"], r["export_yoy"]),
                "import_health": _score(r["import_mom"], r["import_yoy"]),
                "monthly_export": sector_monthly.get(r["sector_key"], []),
            }
            for r in sector_rows
        ],
        "companies": [
            {
                "stock_code": r["stock_code"], "stock_name": r["stock_name"],
                "sector_label": r["sector_label"],
                "period_ym": r["period_ym"],
                "export_latest": round(r["export_val"] or 0),
                "import_latest": round(r["import_val"] or 0),
                "export_mom": r["export_mom"], "export_yoy": r["export_yoy"],
                "import_mom": r["import_mom"], "import_yoy": r["import_yoy"],
                "export_health": _score(r["export_mom"], r["export_yoy"]),
                "import_health": _score(r["import_mom"], r["import_yoy"]),
            }
            for r in company_rows
        ],
        "shared_hs": [
            {"hs_code": r["hs_code"], "hs_name": r["hs_name"], "company_count": r["company_count"]}
            for r in shared_rows
        ],
    }


# ── HS 코드 공유 기업 정보 ───────────────────────────────────────────
@app.get("/api/analysis2/hs/{hs_code}/companies")
def analysis2_hs_companies(hs_code: str):
    """특정 HS 코드를 공유하는 기업 목록 + 시장비율."""
    conn = _hs_db()
    rows = conn.execute(
        """
        SELECT hcm.stock_code, hcm.stock_name, hcm.mapping_status, hcm.flow_type,
               hcm.market_share_pct,
               ms.basis AS share_basis, ms.as_of_year AS share_year
        FROM hs_code_company_map hcm
        LEFT JOIN hs_company_market_share ms
          ON ms.hs_code = hcm.hs_code AND ms.stock_code = hcm.stock_code
        WHERE hcm.hs_code = ?
        ORDER BY hcm.market_share_pct DESC NULLS LAST, hcm.stock_name
        """,
        (hs_code,),
    ).fetchall()
    hs_row = conn.execute(
        """
        SELECT COALESCE(NULLIF(sm.display_name,''), NULLIF(sm.hs_name,''), ?) AS hs_name,
               sp.label AS sector_label
        FROM hs_sector_map sm
        LEFT JOIN sector_preset sp ON sp.sector_key = sm.sector_key
        WHERE sm.hs_code = ?
        LIMIT 1
        """,
        (hs_code, hs_code),
    ).fetchone()
    conn.close()
    return {
        "hs_info": {
            "hs_code": hs_code,
            "hs_name": hs_row["hs_name"] if hs_row else hs_code,
            "sector_label": hs_row["sector_label"] if hs_row else None,
        },
        "companies": [
            {
                "stock_code": r["stock_code"], "stock_name": r["stock_name"],
                "mapping_status": r["mapping_status"], "flow_type": r["flow_type"],
                "market_share_pct": r["market_share_pct"],
                "market_share_label": f"{round(r['market_share_pct']*100)}%" if r["market_share_pct"] else None,
                "share_basis": r["share_basis"], "share_year": r["share_year"],
            }
            for r in rows
        ],
    }
