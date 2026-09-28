"""routes/ibd_screener.py — IBD RS + Minervini + 역헤드앤숄더 스크리너 (2026-09-26)

GET  /api/ibd-screener/rs-rankings            최신 RS 순위 (rs_score 상위)
GET  /api/ibd-screener/minervini-scan         Minervini Trend Template 8조건 실시간 스캔
GET  /api/ibd-screener/inverse-hs-scan        역헤드앤숄더 패턴 실시간 스캔
GET  /api/ibd-screener/stock/{code}/rs-history 종목별 RS 이력
POST /api/ibd-screener/compute-ibd-rs         IBD RS 일별 계산 트리거 (강제 재계산 포함)
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from fastapi import APIRouter, Query

import db_compat

logger = logging.getLogger(__name__)
router = APIRouter()


# ─── 공통 헬퍼 ────────────────────────────────────────────────────────────────

def _get_prices(conn, stock_code: str, n_days: int = 260) -> list[dict]:
    """최근 n_days 거래일의 {date, close, volume} 리스트 (오래된→최신 순)."""
    cur = conn.cursor()
    cur.execute("""
        SELECT date, close, volume
        FROM price_history
        WHERE stock_code = %s AND close > 0
        ORDER BY date DESC
        LIMIT %s
    """, (stock_code, n_days))
    rows = cur.fetchall()
    return [{"date": r[0], "close": float(r[1]), "volume": float(r[2] or 0)}
            for r in reversed(rows)]


def _ma(prices: list[float], period: int) -> Optional[float]:
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def _slope_pct_per_day(values: list[float]) -> float:
    """최소자승 기울기를 평균가 대비 %/일로 정규화."""
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2.0
    mean_y = sum(values) / n
    if mean_y == 0:
        return 0.0
    num = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values))
    den = sum((x - mean_x) ** 2 for x in range(n))
    if den == 0:
        return 0.0
    return (num / den / mean_y) * 100


# ─── RS 순위 ─────────────────────────────────────────────────────────────────

@router.get("/rs-rankings")
def rs_rankings(
    date: Optional[str] = Query(None, description="YYYY-MM-DD, 생략 시 최신 거래일"),
    min_score: int = Query(70, ge=1, le=99),
    limit: int = Query(100, ge=1, le=500),
):
    """IBD RS 상위 종목 목록."""
    conn = db_compat.connect_primary_db()
    try:
        cur = conn.cursor()
        if date:
            calc_date = date
        else:
            cur.execute("SELECT MAX(date) FROM ibd_rs_daily")
            row = cur.fetchone()
            if not row or not row[0]:
                return {"items": [], "note": "ibd_rs_daily 데이터 없음 — POST /compute-ibd-rs 먼저 실행"}
            calc_date = row[0]

        cur.execute("""
            SELECT r.stock_code, u.stock_name, u.market, u.sector_large,
                   r.rs_score, r.rs_rank, r.rs_raw, r.universe_size,
                   u.market_cap
            FROM ibd_rs_daily r
            LEFT JOIN stock_universe u USING (stock_code)
            WHERE r.date = %s AND r.rs_score >= %s
            ORDER BY r.rs_rank
            LIMIT %s
        """, (calc_date, min_score, limit))
        rows = cur.fetchall()

        items = [
            {
                "stock_code": r[0],
                "stock_name": r[1],
                "market": r[2],
                "sector": r[3],
                "rs_score": r[4],
                "rs_rank": r[5],
                "rs_raw": round(float(r[6]), 6) if r[6] else None,
                "universe_size": r[7],
                "market_cap_억": round(float(r[8]), 0) if r[8] else None,
            }
            for r in rows
        ]
        return {"date": calc_date, "items": items, "count": len(items)}
    finally:
        conn.close()


# ─── Minervini Trend Template 스캔 ──────────────────────────────────────────

def _check_minervini(prices: list[float], rs_score: int) -> dict:
    """8개 조건 평가 후 {passed: bool, conditions: {1~8: bool}} 반환."""
    n = len(prices)
    if n < 260:
        return {"passed": False, "conditions": {}, "note": "데이터 부족"}

    curr = prices[-1]
    if curr <= 0:
        return {"passed": False, "conditions": {}}

    ma50  = _ma(prices, 50)
    ma150 = _ma(prices, 150)
    ma200 = _ma(prices, 200)
    if not all([ma50, ma150, ma200]):
        return {"passed": False, "conditions": {}}

    # 200일선 20거래일 전 값
    ma200_20ago = _ma(prices[:-20], 200) if n >= 220 else None

    # 52주(252거래일) 위치
    window_252 = prices[max(0, n - 252):]
    high_52w = max(window_252)
    low_52w  = min(window_252)

    conds = {
        1: curr > ma150 and curr > ma200,
        2: ma150 > ma200,
        3: bool(ma200_20ago and ma200_20ago > 0 and
                (ma200 - ma200_20ago) / ma200_20ago * 100 >= 1.0),
        4: ma50 > ma150 and ma50 > ma200,
        5: curr > ma50,
        6: low_52w > 0 and (curr - low_52w) / low_52w * 100 >= 30,
        7: high_52w > 0 and (high_52w - curr) / high_52w * 100 <= 25,
        8: rs_score >= 70,
    }
    return {
        "passed": all(conds.values()),
        "conditions": conds,
        "ma50": round(ma50, 2),
        "ma150": round(ma150, 2),
        "ma200": round(ma200, 2),
        "high_52w": round(high_52w, 2),
        "low_52w": round(low_52w, 2),
    }


@router.get("/minervini-scan")
def minervini_scan(
    rs_min: int = Query(70, ge=1, le=99, description="RS 최소 점수"),
    limit: int = Query(200, ge=1, le=1000),
):
    """Minervini Trend Template 8조건 실시간 스캔.

    1. Price > 150MA AND Price > 200MA
    2. 150MA > 200MA
    3. 200MA 기울기 ≥ +1% (최근 20거래일)
    4. 50MA > 150MA AND 50MA > 200MA
    5. Price > 50MA
    6. Price > 52주 최저가 × 1.30
    7. Price ≤ 52주 최고가 × 0.75 (within −25%)
    8. RS ≥ 70 (ibd_rs_daily 기준)
    """
    conn = db_compat.connect_primary_db()
    try:
        cur = conn.cursor()
        # 최신 RS 날짜
        cur.execute("SELECT MAX(date) FROM ibd_rs_daily")
        row = cur.fetchone()
        if not row or not row[0]:
            return {"items": [], "note": "ibd_rs_daily 없음 — POST /compute-ibd-rs 실행 필요"}
        rs_date = row[0]

        # RS 필터 통과 종목
        cur.execute("""
            SELECT r.stock_code, r.rs_score, r.rs_rank,
                   u.stock_name, u.market, u.sector_large, u.market_cap
            FROM ibd_rs_daily r
            LEFT JOIN stock_universe u USING (stock_code)
            WHERE r.date = %s AND r.rs_score >= %s
            ORDER BY r.rs_rank
            LIMIT %s
        """, (rs_date, rs_min, limit))
        candidates = cur.fetchall()

        results = []
        for row in candidates:
            code, rs_score, rs_rank, name, market, sector, mktcap = row
            price_data = _get_prices(conn, code, 260)
            if len(price_data) < 260:
                continue
            prices = [p["close"] for p in price_data]
            check = _check_minervini(prices, rs_score)
            if check["passed"]:
                results.append({
                    "stock_code": code,
                    "stock_name": name,
                    "market": market,
                    "sector": sector,
                    "market_cap_억": round(float(mktcap), 0) if mktcap else None,
                    "rs_score": rs_score,
                    "rs_rank": rs_rank,
                    "current_price": prices[-1],
                    "ma50": check.get("ma50"),
                    "ma150": check.get("ma150"),
                    "ma200": check.get("ma200"),
                    "high_52w": check.get("high_52w"),
                    "low_52w": check.get("low_52w"),
                    "conditions": check["conditions"],
                })

        return {
            "rs_date": rs_date,
            "scanned": len(candidates),
            "passed": len(results),
            "items": results,
        }
    finally:
        conn.close()


# ─── 역헤드앤숄더 스캔 ────────────────────────────────────────────────────────

def _detect_inverse_hs(prices: list[float], volumes: list[float], window: int = 3) -> Optional[dict]:
    """역헤드앤숄더 패턴 감지.

    알고리즘:
    1. Savitzky-Golay 필터(window=11)로 노이즈 제거
    2. argrelextrema(order=3)로 극소(골) / 극대(봉우리) 탐색
    3. [L1, H1, L2(head), H2, L3] 5점 패턴 검증
       - L2 < L1, L2 < L3 (머리가 가장 낮음)
       - |H1 - H2| / max(H1, H2) ≤ 0.05 (넥라인 수평, 5% 허용)
       - L3 > L2 (오른쪽 어깨 반등)
       - L1, L3 레벨 유사 (|L1-L3|/max ≤ 0.12)
    4. 현재가 ≥ 넥라인: 돌파 확인
    반환: 패턴 없으면 None, 있으면 상세 dict
    """
    try:
        from scipy.signal import savgol_filter, argrelextrema
        import numpy as np
    except ImportError:
        logger.warning("scipy 미설치 — 역헤드앤숄더 스캔 불가")
        return None

    arr = np.array(prices, dtype=float)
    n = len(arr)
    if n < 60:
        return None

    # Savitzky-Golay: 11포인트 고정 (노이즈 제거 + 패턴 유지 균형)
    sg_win = 11 if n >= 11 else (n if n % 2 == 1 else n - 1)
    if sg_win < 5:
        return None
    smoothed = savgol_filter(arr, window_length=sg_win, polyorder=3)

    # 극소·극대 탐색 (order=window, 기본 3)
    lows_idx  = argrelextrema(smoothed, np.less,    order=window)[0]
    highs_idx = argrelextrema(smoothed, np.greater, order=window)[0]

    if len(lows_idx) < 3 or len(highs_idx) < 2:
        return None

    # 최근 150거래일 이내의 극값만 사용
    min_i = max(0, n - 150)
    lows_idx  = lows_idx[lows_idx >= min_i]
    highs_idx = highs_idx[highs_idx >= min_i]

    if len(lows_idx) < 3 or len(highs_idx) < 2:
        return None

    # [L1, H1, L2, H2, L3] 패턴 탐색 (최근 것 우선)
    best = None
    for li2 in range(len(lows_idx) - 2, -1, -1):   # L2 후보
        i_l2 = lows_idx[li2]
        # L1: L2 이전 극소
        prev_lows  = lows_idx[:li2]
        if len(prev_lows) == 0:
            continue
        i_l1 = prev_lows[-1]
        # L3: L2 이후 극소
        next_lows = lows_idx[li2 + 1:]
        if len(next_lows) == 0:
            continue
        i_l3 = next_lows[0]

        # H1: L1 < H1 < L2
        h1_cands = highs_idx[(highs_idx > i_l1) & (highs_idx < i_l2)]
        if len(h1_cands) == 0:
            continue
        i_h1 = h1_cands[-1]

        # H2: L2 < H2 < L3
        h2_cands = highs_idx[(highs_idx > i_l2) & (highs_idx < i_l3)]
        if len(h2_cands) == 0:
            continue
        i_h2 = h2_cands[0]

        # 원본 가격에서 ±2봉 내 실제 극값 사용 (스무딩-원본 불일치 보정)
        half = 2
        l1 = float(arr[max(0, i_l1-half):i_l1+half+1].min())
        l2 = float(arr[max(0, i_l2-half):i_l2+half+1].min())
        l3 = float(arr[max(0, i_l3-half):i_l3+half+1].min())
        h1 = float(arr[max(0, i_h1-half):i_h1+half+1].max())
        h2 = float(arr[max(0, i_h2-half):i_h2+half+1].max())

        # 기본 구조 검증: 극대가 극소 위에 있어야 함
        if h1 <= l1 or h1 <= l2 or h2 <= l2 or h2 <= l3:
            continue
        # 조건 검증
        if l2 >= l1 or l2 >= l3:
            continue
        neckline = max(h1, h2)
        neck_diff = abs(h1 - h2) / neckline if neckline > 0 else 1
        if neck_diff > 0.05:
            continue
        shoulder_diff = abs(l1 - l3) / max(l1, l3) if max(l1, l3) > 0 else 1
        if shoulder_diff > 0.12:
            continue

        # 넥라인 돌파 여부 (현재 종가)
        current = float(arr[-1])
        breakout = current >= neckline

        # 돌파 시 거래량 확인 (최근 5일 평균 vs 이전 20일 평균)
        vol_surge = None
        if volumes and len(volumes) >= 25:
            recent_vol = sum(volumes[-5:]) / 5
            prev_vol   = sum(volumes[-25:-5]) / 20
            vol_surge  = round(recent_vol / prev_vol, 2) if prev_vol > 0 else None

        best = {
            "pattern": "inverse_hs",
            "neckline": round(neckline, 2),
            "breakout": breakout,
            "l1": round(l1, 2), "h1": round(h1, 2),
            "l2_head": round(l2, 2),
            "h2": round(h2, 2), "l3": round(l3, 2),
            "current_price": round(current, 2),
            "neck_diff_pct": round(neck_diff * 100, 2),
            "vol_surge": vol_surge,
            "idx": {"l1": int(i_l1), "h1": int(i_h1), "l2": int(i_l2),
                    "h2": int(i_h2), "l3": int(i_l3)},
        }
        break  # 가장 최근 패턴만

    return best


@router.get("/inverse-hs-scan")
def inverse_hs_scan(
    rs_min: int = Query(50, ge=1, le=99),
    mktcap_min: float = Query(1000, description="시가총액 최소 (억원)"),
    require_breakout: bool = Query(False, description="넥라인 돌파 종목만"),
    limit: int = Query(300, ge=1, le=1000),
):
    """역헤드앤숄더 패턴 스캔 (scipy.signal 기반).

    RS ≥ rs_min이고 시가총액 ≥ mktcap_min인 종목을 대상으로
    최근 150거래일 데이터에서 역H&S 패턴을 탐색한다.
    """
    conn = db_compat.connect_primary_db()
    try:
        cur = conn.cursor()
        cur.execute("SELECT MAX(date) FROM ibd_rs_daily")
        row = cur.fetchone()
        if not row or not row[0]:
            return {"items": [], "note": "ibd_rs_daily 없음 — POST /compute-ibd-rs 실행 필요"}
        rs_date = row[0]

        cur.execute("""
            SELECT r.stock_code, r.rs_score, r.rs_rank,
                   u.stock_name, u.market, u.sector_large, u.market_cap
            FROM ibd_rs_daily r
            LEFT JOIN stock_universe u USING (stock_code)
            WHERE r.date = %s AND r.rs_score >= %s
              AND (u.market_cap IS NULL OR u.market_cap >= %s)
            ORDER BY r.rs_rank
            LIMIT %s
        """, (rs_date, rs_min, mktcap_min, limit))
        candidates = cur.fetchall()

        results = []
        for row in candidates:
            code, rs_score, rs_rank, name, market, sector, mktcap = row
            price_data = _get_prices(conn, code, 160)
            if len(price_data) < 60:
                continue
            prices  = [p["close"] for p in price_data]
            volumes = [p["volume"] for p in price_data]
            pattern = _detect_inverse_hs(prices, volumes)
            if pattern is None:
                continue
            if require_breakout and not pattern["breakout"]:
                continue
            results.append({
                "stock_code": code,
                "stock_name": name,
                "market": market,
                "sector": sector,
                "market_cap_억": round(float(mktcap), 0) if mktcap else None,
                "rs_score": rs_score,
                "rs_rank": rs_rank,
                **pattern,
            })

        return {
            "rs_date": rs_date,
            "scanned": len(candidates),
            "found": len(results),
            "items": sorted(results, key=lambda x: (not x["breakout"], -x["rs_score"])),
        }
    finally:
        conn.close()


# ─── RS 이력 ─────────────────────────────────────────────────────────────────

@router.get("/stock/{code}/rs-history")
def rs_history(code: str, days: int = Query(60, ge=1, le=252)):
    """종목별 최근 RS 점수 이력."""
    conn = db_compat.connect_primary_db()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT date, rs_score, rs_rank, universe_size
            FROM ibd_rs_daily
            WHERE stock_code = %s
            ORDER BY date DESC
            LIMIT %s
        """, (code, days))
        rows = cur.fetchall()
        items = [
            {"date": r[0], "rs_score": r[1], "rs_rank": r[2], "universe_size": r[3]}
            for r in reversed(rows)
        ]
        return {"stock_code": code, "items": items}
    finally:
        conn.close()


# ─── IBD RS 계산 트리거 ───────────────────────────────────────────────────────

@router.post("/compute-ibd-rs")
def trigger_compute(
    target_date: Optional[str] = Query(None, description="YYYY-MM-DD, 생략 시 최신"),
    force: bool = Query(False, description="이미 계산된 날짜도 재계산"),
):
    """IBD RS 일별 계산을 트리거한다.

    force=True이면 해당 날짜의 기존 데이터를 삭제하고 재계산.
    """
    from collectors.ibd_rs_collector import compute_ibd_rs

    if force and target_date:
        conn = db_compat.connect_primary_db()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM ibd_rs_daily WHERE date = %s", (target_date,))
            conn.commit()
            logger.info(f"[IBD RS] force 재계산 — {target_date} 기존 데이터 삭제")
        finally:
            conn.close()

    try:
        result = compute_ibd_rs(target_date)
        return {"status": "ok", **result}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
