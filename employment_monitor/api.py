"""
employment_monitor/api.py — 고용/산재보험 FastAPI 라우터

GET /api/employment/monthly          # 업종별 월별 현황
GET /api/employment/trend/{sector}   # 특정 업종 추이
GET /api/employment/summary          # 최신 전체 요약
GET /api/employment/signal/{code}    # 종목별 업종 시그널 (향후 연동)
POST /api/employment/collect         # 수동 수집 트리거
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, BackgroundTasks

from .db import connect

logger = logging.getLogger(__name__)
router = APIRouter()

# 업종 컬럼 → 한글 레이블 매핑
_SECTOR_LABELS = {
    "agri_fore_fis":        "농업·임업·어업",
    "agri_hunt_fore":       "농업·수렵·임업",
    "ahou_gyhwaldong":      "가구내고용활동",
    "art_sport_yeoga":      "예술·스포츠·여가",
    "bogun_shoe_bokji":     "보건·사회복지",
    "budongsanImdae":       "부동산임대",
    "budongsanImdaeSaeop":  "부동산·사업서비스",
    "edu_service":          "교육서비스",
    "fis":                  "어업",
    "fnn_boheom":           "금융·보험",
    "geonseoleop":          "건설",
    "inte_foreign_gigwan":  "국제·외국기관",
    "jejoeop":              "제조",
    "jeongi_gas_step":      "전기·가스·수도",
    "jeongi_gas_tw":        "전기·가스·상수도",
    "jeonmun_sci":          "전문·과학·기술",
    "minind":               "광업",
    "publ_aim_brd":         "출판·방송·정보",
    "pub_hjng_shoe":        "공공행정·국방",
    "saeop_siseol_jiwon":   "사업시설관리",
    "sew_smat_rmatrev":     "하수·폐기물·환경",
    "sukbak_aeh":           "숙박·음식점",
    "unsu":                 "운수",
    "unsu_changgo_tongsin": "운수·창고·통신",
    "whol_ret":             "도소매",
    "whol_ret_cons":        "도소매·소비자용품",
}

_SECTOR_COLS = list(_SECTOR_LABELS.keys())


# ── GET /api/employment/summary ─────────────────────────────────
@router.get("/summary")
def get_summary():
    """최신 월 전체 업종 고용/산재 요약."""
    conn = connect()
    rows = conn.execute(
        "SELECT ym, sj_gy_fg, saeopjang_wk_fg, total "
        "FROM employment_monthly "
        "ORDER BY ym DESC LIMIT 20"
    ).fetchall()
    conn.close()

    if not rows:
        return {"latest_ym": None, "data": []}

    latest_ym = rows[0]["ym"]
    result = [
        {
            "ym": r["ym"],
            "sj_gy_fg": r["sj_gy_fg"],
            "saeopjang_wk_fg": r["saeopjang_wk_fg"],
            "total": r["total"],
        }
        for r in rows
    ]
    return {"latest_ym": latest_ym, "data": result}


# ── GET /api/employment/monthly ─────────────────────────────────
@router.get("/monthly")
def get_monthly(
    sj_gy_fg: str = Query("3", description="1=산재, 3=고용"),
    saeopjang_wk_fg: str = Query("근로자", description="사업장 or 근로자"),
    months: int = Query(24, ge=1, le=120),
):
    """업종별 월별 고용/산재 현황 (최근 N개월)."""
    conn = connect()
    cols_sql = ", ".join(_SECTOR_COLS)
    rows = conn.execute(
        f"SELECT ym, {cols_sql}, total "
        f"FROM employment_monthly "
        f"WHERE sj_gy_fg=? AND saeopjang_wk_fg=? "
        f"ORDER BY ym DESC LIMIT ?",
        (sj_gy_fg, saeopjang_wk_fg, months),
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        item = {"ym": r["ym"], "total": r["total"], "sectors": {}}
        for col in _SECTOR_COLS:
            v = r[col]
            if v is not None:
                item["sectors"][col] = {"value": v, "label": _SECTOR_LABELS[col]}
        result.append(item)

    return {"sj_gy_fg": sj_gy_fg, "saeopjang_wk_fg": saeopjang_wk_fg, "data": result}


# ── GET /api/employment/trend/{sector} ──────────────────────────
@router.get("/trend/{sector}")
def get_sector_trend(
    sector: str,
    sj_gy_fg: str = Query("3"),
    saeopjang_wk_fg: str = Query("근로자"),
    months: int = Query(36, ge=3, le=120),
):
    """특정 업종 컬럼의 월별 추이 + YoY/MoM 변화율."""
    if sector not in _SECTOR_COLS:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector}. Valid: {_SECTOR_COLS}")

    conn = connect()
    rows = conn.execute(
        f"SELECT ym, {sector} AS val FROM employment_monthly "
        f"WHERE sj_gy_fg=? AND saeopjang_wk_fg=? AND {sector} IS NOT NULL "
        f"ORDER BY ym DESC LIMIT ?",
        (sj_gy_fg, saeopjang_wk_fg, months),
    ).fetchall()
    conn.close()

    data = [{"ym": r["ym"], "value": r["val"]} for r in rows]
    # MoM / YoY
    for i, item in enumerate(data):
        mom_idx = i + 1
        yoy_idx = i + 12
        item["mom_pct"] = (
            round((item["value"] - data[mom_idx]["value"]) / data[mom_idx]["value"] * 100, 2)
            if mom_idx < len(data) and data[mom_idx]["value"]
            else None
        )
        item["yoy_pct"] = (
            round((item["value"] - data[yoy_idx]["value"]) / data[yoy_idx]["value"] * 100, 2)
            if yoy_idx < len(data) and data[yoy_idx]["value"]
            else None
        )

    return {
        "sector": sector,
        "label": _SECTOR_LABELS[sector],
        "sj_gy_fg": sj_gy_fg,
        "saeopjang_wk_fg": saeopjang_wk_fg,
        "data": data,
    }


# ── GET /api/employment/signal/{stock_code} ─────────────────────
@router.get("/signal/{stock_code}")
def get_stock_signal(stock_code: str):
    """
    종목 업종에 해당하는 고용 시그널 반환.
    stock_universe의 sector_large로 업종 매핑 후 최근 3개월 YoY 추이 반환.
    """
    from .db import STOCK_DB_PATH
    import sqlite3

    # 1. 종목 업종 조회
    try:
        sconn = sqlite3.connect(STOCK_DB_PATH)
        sconn.row_factory = sqlite3.Row
        meta = sconn.execute(
            "SELECT stock_name, sector_large FROM stock_universe WHERE stock_code=? LIMIT 1",
            (stock_code,),
        ).fetchone()
        sconn.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not meta:
        raise HTTPException(status_code=404, detail="종목 없음")

    sector_large = meta["sector_large"] or ""
    matched_col = _map_sector_to_col(sector_large)

    if not matched_col:
        return {
            "stock_code": stock_code,
            "stock_name": meta["stock_name"],
            "sector_large": sector_large,
            "matched_col": None,
            "signal": "neutral",
            "reason": "업종 매핑 없음",
        }

    conn = connect()
    rows = conn.execute(
        f"SELECT ym, {matched_col} AS val FROM employment_monthly "
        f"WHERE sj_gy_fg='3' AND saeopjang_wk_fg='근로자' AND {matched_col} IS NOT NULL "
        f"ORDER BY ym DESC LIMIT 15",
        (),
    ).fetchall()
    conn.close()

    if len(rows) < 13:
        return {
            "stock_code": stock_code,
            "stock_name": meta["stock_name"],
            "sector_large": sector_large,
            "matched_col": matched_col,
            "signal": "neutral",
            "reason": "데이터 부족",
        }

    latest = rows[0]["val"]
    yoy_val = rows[12]["val"] if rows[12]["val"] else None
    mom_val = rows[1]["val"] if rows[1]["val"] else None
    yoy_pct = round((latest - yoy_val) / yoy_val * 100, 2) if yoy_val else None
    mom_pct = round((latest - mom_val) / mom_val * 100, 2) if mom_val else None

    # 시그널 판정
    if yoy_pct is not None and yoy_pct >= 3.0:
        signal = "positive"
        reason = f"고용 YoY +{yoy_pct:.1f}% (업종 성장)"
    elif yoy_pct is not None and yoy_pct <= -3.0:
        signal = "negative"
        reason = f"고용 YoY {yoy_pct:.1f}% (업종 위축)"
    else:
        signal = "neutral"
        reason = f"고용 YoY {yoy_pct:.1f}%" if yoy_pct is not None else "데이터 없음"

    return {
        "stock_code": stock_code,
        "stock_name": meta["stock_name"],
        "sector_large": sector_large,
        "matched_col": matched_col,
        "sector_label": _SECTOR_LABELS[matched_col],
        "latest_ym": rows[0]["ym"],
        "latest_val": latest,
        "yoy_pct": yoy_pct,
        "mom_pct": mom_pct,
        "signal": signal,
        "reason": reason,
    }


# ── POST /api/employment/collect ────────────────────────────────
@router.post("/collect")
def trigger_collect(background_tasks: BackgroundTasks, months_back: int = Query(3, ge=1, le=36)):
    """수동 수집 트리거 (백그라운드 실행)."""
    def _run():
        from .collect import collect_all
        collect_all(months_back=months_back)

    background_tasks.add_task(_run)
    return {"status": "started", "months_back": months_back}


# ── GET /api/employment/available-months ────────────────────────
@router.get("/available-months")
def get_available_months():
    """수집된 연월 목록."""
    conn = connect()
    rows = conn.execute(
        "SELECT DISTINCT ym FROM employment_monthly ORDER BY ym DESC LIMIT 60"
    ).fetchall()
    conn.close()
    return [r["ym"] for r in rows]


def _map_sector_to_col(sector_large: str) -> str | None:
    """stock_universe.sector_large → employment_monthly 컬럼명 매핑."""
    s = sector_large.lower()
    if "제조" in s or "반도체" in s or "전기전자" in s or "화학" in s or "철강" in s:
        return "jejoeop"
    if "건설" in s:
        return "geonseoleop"
    if "금융" in s or "보험" in s or "증권" in s or "은행" in s:
        return "fnn_boheom"
    if "도매" in s or "소매" in s or "유통" in s:
        return "whol_ret"
    if "운수" in s or "물류" in s or "항공" in s or "해운" in s:
        return "unsu"
    if "숙박" in s or "음식" in s or "외식" in s:
        return "sukbak_aeh"
    if "교육" in s:
        return "edu_service"
    if "보건" in s or "의료" in s or "바이오" in s or "제약" in s:
        return "bogun_shoe_bokji"
    if "출판" in s or "방송" in s or "it" in s or "소프트" in s or "통신" in s:
        return "publ_aim_brd"
    if "부동산" in s:
        return "budongsanImdaeSaeop"
    if "전문" in s or "과학" in s or "기술" in s:
        return "jeonmun_sci"
    if "농업" in s or "임업" in s or "수산" in s:
        return "agri_fore_fis"
    if "예술" in s or "스포츠" in s or "여가" in s:
        return "art_sport_yeoga"
    if "전기" in s or "가스" in s or "에너지" in s:
        return "jeongi_gas_step"
    if "광업" in s or "광산" in s:
        return "minind"
    return None
