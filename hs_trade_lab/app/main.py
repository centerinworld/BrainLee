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
        export_latest = monthly[-1]["export_val"] if monthly else None
        export_prev1 = monthly[-2]["export_val"] if len(monthly) >= 2 else None
        export_prev12 = monthly[-13]["export_val"] if len(monthly) >= 13 else None
        import_latest = monthly[-1]["import_val"] if monthly else None
        import_prev1 = monthly[-2]["import_val"] if len(monthly) >= 2 else None
        import_prev12 = monthly[-13]["import_val"] if len(monthly) >= 13 else None
        provisional = _latest_provisional_10day(conn, sector["sector_key"])
        result.append(
            {
                **sector,
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
    rows = conn.execute(
        f"""
        SELECT period_ym, export_value, export_weight, import_value, import_weight, hs_names
        FROM analysis2_company_monthly_cache
        WHERE stock_code = ?
          {sector_where}
          AND period_ym >= date('now', ? || ' months')
        ORDER BY period_ym
        """,
        [*params, f"-{months}"],
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

    def _pct(current: float | None, previous: float | None) -> float | None:
        if not current or not previous:
            return None
        return round(((current - previous) / previous) * 100, 2)

    export_latest = monthly[-1]["export_val"] if monthly else None
    export_prev1 = monthly[-2]["export_val"] if len(monthly) >= 2 else None
    export_prev12 = monthly[-13]["export_val"] if len(monthly) >= 13 else None
    import_latest = monthly[-1]["import_val"] if monthly else None
    import_prev1 = monthly[-2]["import_val"] if len(monthly) >= 2 else None
    import_prev12 = monthly[-13]["import_val"] if len(monthly) >= 13 else None

    provisional = _latest_provisional_10day(conn, sector_key)
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
    latest_period_row = conn.execute(
        """
        SELECT MAX(period_ym) AS latest_period
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND sector_key = ?
        """,
        (stock_code, sector_key),
    ).fetchone()
    chosen_period = period_ym or (latest_period_row["latest_period"] if latest_period_row else None)
    if not chosen_period:
        conn.close()
        return {"stock_code": stock_code, "sector_key": sector_key, "period_ym": None, "items": []}
    rows = conn.execute(
        """
        SELECT stock_name, sector_label, hs_code, hs_name, sector_name, flow_type, mapping_status,
               export_value, export_weight, import_value, import_weight
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND sector_key = ?
          AND period_ym = ?
        ORDER BY CASE flow_type WHEN 'export' THEN 0 ELSE 1 END,
                 CASE flow_type WHEN 'export' THEN export_value ELSE import_value END DESC,
                 hs_code
        """,
        (stock_code, sector_key, chosen_period),
    ).fetchall()
    totals = conn.execute(
        """
        SELECT
          SUM(CASE WHEN flow_type = 'export' THEN export_value ELSE 0 END) AS export_total,
          SUM(CASE WHEN flow_type = 'import' THEN import_value ELSE 0 END) AS import_total
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND sector_key = ?
          AND period_ym = ?
        """,
        (stock_code, sector_key, chosen_period),
    ).fetchone()
    periods = conn.execute(
        """
        SELECT DISTINCT period_ym
        FROM analysis2_company_hs_monthly_cache
        WHERE stock_code = ?
          AND sector_key = ?
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
