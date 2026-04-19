"""
employment_monitor/api.py — 고용/산재보험 FastAPI 라우터

업종별 (data.go.kr):
  GET /api/employment/monthly
  GET /api/employment/trend/{sector}
  GET /api/employment/summary
  GET /api/employment/signal/{stock_code}
  POST /api/employment/collect
  GET /api/employment/available-months

기업별 임직원 (DART):
  GET /api/employment/company/ranking     # 대기업/중견/중소 증감 순위
  GET /api/employment/company/{code}      # 개별 종목 임직원 추이
  POST /api/employment/company/collect    # 수동 수집 트리거
  GET /api/employment/company/status      # 수집 현황 통계
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from .db import connect
from .collect_dart import classify_company_size

logger = logging.getLogger(__name__)
router = APIRouter()

# ── 업종 레이블 ─────────────────────────────────────────────────
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 업종별 API (data.go.kr)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/summary")
def get_summary():
    conn = connect()
    rows = conn.execute(
        "SELECT ym, sj_gy_fg, saeopjang_wk_fg, total FROM employment_monthly "
        "ORDER BY ym DESC LIMIT 20"
    ).fetchall()
    conn.close()
    if not rows:
        return {"latest_ym": None, "data": []}
    latest_ym = rows[0]["ym"]
    return {
        "latest_ym": latest_ym,
        "data": [dict(r) for r in rows],
    }


@router.get("/monthly")
def get_monthly(
    sj_gy_fg: str = Query("3"),
    saeopjang_wk_fg: str = Query("근로자"),
    months: int = Query(24, ge=1, le=120),
):
    conn = connect()
    cols_sql = ", ".join(_SECTOR_COLS)
    rows = conn.execute(
        f"SELECT ym, {cols_sql}, total FROM employment_monthly "
        f"WHERE sj_gy_fg=? AND saeopjang_wk_fg=? ORDER BY ym DESC LIMIT ?",
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


@router.get("/trend/{sector}")
def get_sector_trend(
    sector: str,
    sj_gy_fg: str = Query("3"),
    saeopjang_wk_fg: str = Query("근로자"),
    months: int = Query(36, ge=3, le=120),
):
    if sector not in _SECTOR_COLS:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector}")
    conn = connect()
    rows = conn.execute(
        f"SELECT ym, {sector} AS val FROM employment_monthly "
        f"WHERE sj_gy_fg=? AND saeopjang_wk_fg=? AND {sector} IS NOT NULL "
        f"ORDER BY ym DESC LIMIT ?",
        (sj_gy_fg, saeopjang_wk_fg, months),
    ).fetchall()
    conn.close()

    data = [{"ym": r["ym"], "value": r["val"]} for r in rows]
    for i, item in enumerate(data):
        m, y = i + 1, i + 12
        item["mom_pct"] = (
            round((item["value"] - data[m]["value"]) / data[m]["value"] * 100, 2)
            if m < len(data) and data[m]["value"] else None
        )
        item["yoy_pct"] = (
            round((item["value"] - data[y]["value"]) / data[y]["value"] * 100, 2)
            if y < len(data) and data[y]["value"] else None
        )

    return {
        "sector": sector, "label": _SECTOR_LABELS[sector],
        "sj_gy_fg": sj_gy_fg, "saeopjang_wk_fg": saeopjang_wk_fg, "data": data,
    }


@router.get("/available-months")
def get_available_months():
    conn = connect()
    rows = conn.execute(
        "SELECT DISTINCT ym FROM employment_monthly ORDER BY ym DESC LIMIT 60"
    ).fetchall()
    conn.close()
    return [r["ym"] for r in rows]


@router.post("/collect")
def trigger_collect(bg: BackgroundTasks, months_back: int = Query(3, ge=1, le=36)):
    def _run():
        from .collect import collect_all
        collect_all(months_back=months_back)
    bg.add_task(_run)
    return {"status": "started", "months_back": months_back}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 기업별 임직원 API (DART)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@router.get("/company/status")
def get_company_collect_status():
    """수집 현황 통계."""
    conn = connect()
    total  = conn.execute("SELECT COUNT(DISTINCT stock_code) FROM employment_company_monthly").fetchone()[0]
    actual = conn.execute("SELECT COUNT(*) FROM employment_company_monthly WHERE is_actual=1").fetchone()[0]
    interp = conn.execute("SELECT COUNT(*) FROM employment_company_monthly WHERE is_actual=0").fetchone()[0]
    by_size = conn.execute(
        "SELECT company_size, COUNT(DISTINCT stock_code) as cnt "
        "FROM employment_company_monthly GROUP BY company_size"
    ).fetchall()
    latest_ym = conn.execute(
        "SELECT MAX(ym) FROM employment_company_monthly WHERE is_actual=1"
    ).fetchone()[0]
    conn.close()
    return {
        "total_companies": total,
        "actual_rows": actual,
        "interpolated_rows": interp,
        "latest_ym": latest_ym,
        "by_size": {r["company_size"]: r["cnt"] for r in by_size},
    }


@router.get("/company/ranking")
def get_company_ranking(
    size: str = Query("all", description="all|대기업|중견기업|중소기업"),
    direction: str = Query("increase", description="increase|decrease"),
    limit: int = Query(30, ge=5, le=100),
    ym: Optional[str] = Query(None, description="기준 연월 (없으면 최신)"),
):
    """
    상장기업 임직원 증감 순위.
    - size: 기업 규모 필터
    - direction: increase(채용증가) or decrease(채용감소)
    - limit: 표시 개수
    """
    conn = connect()

    # 기준 연월 결정
    if not ym:
        row = conn.execute(
            "SELECT MAX(ym) FROM employment_company_monthly WHERE is_actual=1"
        ).fetchone()
        ym = row[0] if row and row[0] else None

    if not ym:
        conn.close()
        return {"ym": None, "data": [], "message": "수집된 데이터가 없습니다. 수집 버튼을 눌러주세요."}

    # YoY 기준 연월 (12개월 전)
    year, month = int(ym[:4]), int(ym[5:7])
    prev_year = year - 1
    yoy_ym = f"{prev_year}-{month:02d}"

    size_filter = "" if size == "all" else f"AND a.company_size = '{size}'"

    rows = conn.execute(
        f"""SELECT
            a.stock_code, a.stock_name, a.company_size, a.sector_large,
            a.employee_count  AS cur_count,
            b.employee_count  AS yoy_count,
            a.mktcap,
            CASE WHEN b.employee_count > 0
                 THEN ROUND((a.employee_count - b.employee_count) * 100.0 / b.employee_count, 2)
                 ELSE NULL
            END AS yoy_pct,
            (a.employee_count - COALESCE(b.employee_count, a.employee_count)) AS yoy_abs
        FROM employment_company_monthly a
        LEFT JOIN employment_company_monthly b
               ON a.stock_code = b.stock_code AND b.ym = ?
        WHERE a.ym = ?
          AND a.employee_count IS NOT NULL
          AND a.employee_count > 0
          {size_filter}
        ORDER BY yoy_abs {'DESC' if direction == 'increase' else 'ASC'}
        LIMIT ?""",
        (yoy_ym, ym, limit),
    ).fetchall()
    conn.close()

    result = []
    for r in rows:
        result.append({
            "stock_code":   r["stock_code"],
            "stock_name":   r["stock_name"],
            "company_size": r["company_size"],
            "sector":       r["sector_large"],
            "cur_count":    r["cur_count"],
            "yoy_count":    r["yoy_count"],
            "yoy_abs":      r["yoy_abs"],
            "yoy_pct":      r["yoy_pct"],
            "mktcap":       r["mktcap"],
        })

    return {
        "ym": ym,
        "yoy_ym": yoy_ym,
        "direction": direction,
        "size": size,
        "data": result,
    }


@router.get("/company/{stock_code}")
def get_company_trend(
    stock_code: str,
    months: int = Query(60, ge=12, le=120),
):
    """
    개별 종목 임직원 추이 (최대 5년).
    개별종목 페이지에서 시그널로 사용.
    """
    conn = connect()
    rows = conn.execute(
        """SELECT ym, employee_count, regular_count, contract_count,
                  is_actual, company_size, stock_name, mktcap, sector_large
           FROM employment_company_monthly
           WHERE stock_code=?
           ORDER BY ym DESC LIMIT ?""",
        (stock_code, months),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "stock_code": stock_code,
            "stock_name": None,
            "has_data": False,
            "signal": "neutral",
            "data": [],
        }

    data = [dict(r) for r in rows]
    latest = data[0]
    cur = latest["employee_count"] or 0

    # YoY 시그널
    yoy_row = next((d for d in data if d["ym"][:4] == str(int(latest["ym"][:4]) - 1)
                    and d["ym"][5:7] == latest["ym"][5:7]), None)
    yoy_pct = (
        round((cur - yoy_row["employee_count"]) / yoy_row["employee_count"] * 100, 2)
        if yoy_row and yoy_row["employee_count"]
        else None
    )
    mom_row = data[1] if len(data) > 1 else None
    mom_pct = (
        round((cur - mom_row["employee_count"]) / mom_row["employee_count"] * 100, 2)
        if mom_row and mom_row["employee_count"]
        else None
    )

    if yoy_pct is not None and yoy_pct >= 5.0:
        signal, reason = "positive", f"YoY +{yoy_pct:.1f}% 채용 증가"
    elif yoy_pct is not None and yoy_pct <= -5.0:
        signal, reason = "negative", f"YoY {yoy_pct:.1f}% 인원 감소"
    elif yoy_pct is not None and yoy_pct >= 2.0:
        signal, reason = "slight_positive", f"YoY +{yoy_pct:.1f}% 소폭 증가"
    elif yoy_pct is not None and yoy_pct <= -2.0:
        signal, reason = "slight_negative", f"YoY {yoy_pct:.1f}% 소폭 감소"
    else:
        signal, reason = "neutral", f"YoY {yoy_pct:.1f}%" if yoy_pct is not None else "데이터 없음"

    # MoM 추가
    for i, item in enumerate(data):
        prev = data[i + 1] if i + 1 < len(data) else None
        item["mom_pct"] = (
            round((item["employee_count"] - prev["employee_count"]) / prev["employee_count"] * 100, 2)
            if prev and prev["employee_count"] and item["employee_count"]
            else None
        )
        yoy_ref = next((d for d in data if d["ym"][:4] == str(int(item["ym"][:4]) - 1)
                        and d["ym"][5:7] == item["ym"][5:7]), None)
        item["yoy_pct"] = (
            round((item["employee_count"] - yoy_ref["employee_count"]) / yoy_ref["employee_count"] * 100, 2)
            if yoy_ref and yoy_ref["employee_count"] and item["employee_count"]
            else None
        )

    return {
        "stock_code":   stock_code,
        "stock_name":   latest["stock_name"],
        "company_size": latest["company_size"],
        "sector":       latest["sector_large"],
        "latest_ym":    latest["ym"],
        "cur_count":    cur,
        "yoy_pct":      yoy_pct,
        "mom_pct":      mom_pct,
        "signal":       signal,
        "reason":       reason,
        "has_data":     True,
        "data":         data,
    }


@router.post("/company/collect")
def trigger_company_collect(
    bg: BackgroundTasks,
    mode: str = Query("watchlist", description="watchlist|all"),
):
    """
    기업별 임직원 수집 트리거.
    mode=watchlist: 관심종목+포트폴리오+매수후보만 (빠름)
    mode=all: 전 상장 종목 (수 시간 소요)
    """
    def _run():
        if mode == "all":
            from .collect_dart import collect_all_listed
            collect_all_listed()
        else:
            from .collect_dart import collect_watchlist
            collect_watchlist()

    bg.add_task(_run)
    return {"status": "started", "mode": mode}


# ── 개별종목 페이지 시그널 (기존 호환 유지) ─────────────────────
@router.get("/signal/{stock_code}")
def get_stock_signal(stock_code: str):
    """개별종목 페이지용 고용 시그널 (업종별 + 기업별 통합)."""
    # 1. 기업별 DART 데이터 우선
    company_data = get_company_trend(stock_code, months=24)
    if company_data["has_data"]:
        return {
            **company_data,
            "source": "dart",
        }

    # 2. Fallback: 업종 기반 시그널
    from .db import STOCK_DB_PATH
    import sqlite3
    try:
        sc = sqlite3.connect(str(STOCK_DB_PATH))
        sc.row_factory = sqlite3.Row
        meta = sc.execute(
            "SELECT stock_name, sector_large FROM stock_universe WHERE stock_code=? LIMIT 1",
            (stock_code,),
        ).fetchone()
        sc.close()
    except Exception:
        meta = None

    if not meta:
        raise HTTPException(status_code=404, detail="종목 없음")

    sector_col = _map_sector_to_col(meta["sector_large"] or "")
    if not sector_col:
        return {
            "stock_code": stock_code,
            "stock_name": meta["stock_name"],
            "has_data": False,
            "signal": "neutral",
            "reason": "업종 매핑 없음",
            "source": "industry",
        }

    conn = connect()
    rows = conn.execute(
        f"SELECT ym, {sector_col} AS val FROM employment_monthly "
        f"WHERE sj_gy_fg='3' AND saeopjang_wk_fg='근로자' ORDER BY ym DESC LIMIT 15"
    ).fetchall()
    conn.close()

    if len(rows) < 13:
        return {"stock_code": stock_code, "signal": "neutral", "reason": "데이터 부족", "has_data": False}

    latest, yoy = rows[0]["val"], rows[12]["val"]
    yoy_pct = round((latest - yoy) / yoy * 100, 2) if yoy else None

    if yoy_pct and yoy_pct >= 3.0:
        signal, reason = "positive", f"업종 고용 YoY +{yoy_pct:.1f}%"
    elif yoy_pct and yoy_pct <= -3.0:
        signal, reason = "negative", f"업종 고용 YoY {yoy_pct:.1f}%"
    else:
        signal, reason = "neutral", f"업종 고용 YoY {yoy_pct:.1f}%" if yoy_pct else "-"

    return {
        "stock_code": stock_code, "stock_name": meta["stock_name"],
        "sector_label": _SECTOR_LABELS.get(sector_col, ""), "yoy_pct": yoy_pct,
        "signal": signal, "reason": reason, "has_data": True, "source": "industry",
    }


def _map_sector_to_col(sector_large: str) -> str | None:
    s = sector_large.lower()
    if any(k in s for k in ["제조", "반도체", "전기전자", "화학", "철강", "소재"]):
        return "jejoeop"
    if "건설" in s: return "geonseoleop"
    if any(k in s for k in ["금융", "보험", "증권", "은행"]): return "fnn_boheom"
    if any(k in s for k in ["도매", "소매", "유통"]): return "whol_ret"
    if any(k in s for k in ["운수", "물류", "항공", "해운"]): return "unsu"
    if any(k in s for k in ["숙박", "음식", "외식"]): return "sukbak_aeh"
    if "교육" in s: return "edu_service"
    if any(k in s for k in ["보건", "의료", "바이오", "제약"]): return "bogun_shoe_bokji"
    if any(k in s for k in ["출판", "방송", "it", "소프트", "통신", "정보"]): return "publ_aim_brd"
    if "부동산" in s: return "budongsanImdaeSaeop"
    if any(k in s for k in ["전문", "과학", "기술"]): return "jeonmun_sci"
    if any(k in s for k in ["농업", "임업", "수산"]): return "agri_fore_fis"
    if any(k in s for k in ["전기", "가스", "에너지"]): return "jeongi_gas_step"
    return None
