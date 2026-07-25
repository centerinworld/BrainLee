"""
routes/order_contracts.py — 수주공시(단일판매·공급계약) 기반 수주잔고 급증 탐지 API

DART에는 "수주잔고" 표준 필드가 없다. 대신 기업이 수시로 내는
"단일판매ㆍ공급계약체결/해지" 공시(kind='I')를 개별 수집·파싱하여
  · 계약금액, 매출액대비 비율(공시 원문 명시값 우선)
  · 신규계약 누적(추정 수주잔고 proxy) vs 직전 동기간 대비 증감률
  · 매출액(financial_data) 대비 비중
을 계산해 "분기/사업보고서 발표 전 수주잔고가 급증한 종목"을 스크리닝한다.

파싱은 회사별 공시 서식 차이로 100% 정확하지 않을 수 있어 parse_ok/verified
플래그로 사람 검증 워크플로를 전제로 한다.

  GET    /api/order-contracts/stock/{code}     종목별 수주공시 목록 + 매출대비 비교
  GET    /api/order-contracts/backlog/{code}   종목별 추정 수주잔고 추이 (월별 누적)
  GET    /api/order-contracts/screener/surge   수주잔고 급증 종목 스크리너
  POST   /api/order-contracts/collect/today    오늘자 전종목 수주공시 스캔+저장 (수동 트리거)
  POST   /api/order-contracts/collect/{code}   특정 종목 기간 백필 (기본 최근 24개월)
  PATCH  /api/order-contracts/{id}/verify      파싱값 사람 검증/수정
  DELETE /api/order-contracts/{id}             오탐 공시 삭제
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3 as _sl
from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "stock.db"

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS order_contracts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_code         TEXT NOT NULL,
    stock_name         TEXT,
    rcept_no           TEXT NOT NULL UNIQUE,
    rcept_dt           TEXT,               -- 공시 접수일 YYYYMMDD
    report_nm          TEXT,
    is_termination     INTEGER DEFAULT 0,  -- 계약해지 공시 여부
    contract_amount    REAL,               -- 계약금액 (원)
    revenue_ratio_pct  REAL,               -- 공시 원문에 명시된 매출액대비 비율(%)
    recent_revenue     REAL,               -- 공시 원문에 명시된 최근 매출액 (원)
    counterpart        TEXT,               -- 계약상대방
    contract_date      TEXT,
    contract_start     TEXT,
    contract_end       TEXT,
    parse_ok           INTEGER DEFAULT 0,  -- 계약금액 자동추출 성공 여부
    verified           INTEGER DEFAULT 0,  -- 사람 검증 여부
    raw_snippet        TEXT,
    dart_url           TEXT,
    created_at         DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at         DATETIME DEFAULT CURRENT_TIMESTAMP
)
"""
_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_order_contracts_code ON order_contracts(stock_code, rcept_dt)"


def _db():
    conn = _sl.connect(DB_PATH)
    conn.row_factory = _sl.Row
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_INDEX)
    conn.commit()
    return conn


def _latest_annual_revenue(conn, stock_code: str) -> float:
    row = conn.execute(
        """SELECT revenue FROM financial_data
           WHERE stock_code=? AND is_annual=1 AND revenue IS NOT NULL AND revenue > 0
           ORDER BY year DESC LIMIT 1""",
        (stock_code,),
    ).fetchone()
    return float(row[0]) if row else 0.0


def _stock_name(conn, stock_code: str) -> str:
    row = conn.execute(
        "SELECT stock_name FROM stock_universe WHERE stock_code=? ORDER BY base_date DESC LIMIT 1",
        (stock_code,),
    ).fetchone()
    return row[0] if row else stock_code


async def _save_disclosure(conn, dart, item: dict) -> bool:
    """단일 공시 항목 파싱 후 upsert. 이미 존재하면 스킵. 저장되면 True."""
    rcept_no = item["rcept_no"]
    exists = conn.execute("SELECT 1 FROM order_contracts WHERE rcept_no=?", (rcept_no,)).fetchone()
    if exists:
        return False

    parsed = await dart.parse_contract_document(rcept_no)
    rcept_dt = item.get("rcept_dt", "")
    fmt_dt = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}" if len(rcept_dt) == 8 else rcept_dt

    conn.execute(
        """INSERT OR IGNORE INTO order_contracts
           (stock_code, stock_name, rcept_no, rcept_dt, report_nm, is_termination,
            contract_amount, revenue_ratio_pct, recent_revenue, counterpart,
            contract_date, contract_start, contract_end, parse_ok, raw_snippet, dart_url)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item["stock_code"], item.get("corp_name") or _stock_name(conn, item["stock_code"]),
            rcept_no, fmt_dt, item.get("report_nm", ""), int(item.get("is_termination", False)),
            parsed.get("contract_amount"), parsed.get("revenue_ratio_pct"), parsed.get("recent_revenue"),
            parsed.get("counterpart"), parsed.get("contract_date"), parsed.get("contract_start"),
            parsed.get("contract_end"), int(parsed.get("parse_ok", False)), parsed.get("raw_snippet", ""),
            f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
        ),
    )
    return True


# ── GET /api/order-contracts/stock/{code} ───────────────────────
@router.get("/stock/{stock_code}")
def get_stock_contracts(stock_code: str, months: int = Query(default=24, ge=1, le=120)):
    conn = _db()
    try:
        since = (date.today() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT * FROM order_contracts
               WHERE stock_code=? AND rcept_dt >= ?
               ORDER BY rcept_dt DESC""",
            (stock_code, since),
        ).fetchall()
        revenue = _latest_annual_revenue(conn, stock_code)

        data = []
        for r in rows:
            d = dict(r)
            amt = d.get("contract_amount")
            ratio = d.get("revenue_ratio_pct")
            if ratio is None and amt and revenue:
                ratio = round(amt / revenue * 100, 2)
            d["revenue_ratio_pct"] = ratio
            data.append(d)

        return {
            "stock_code": stock_code,
            "latest_annual_revenue": revenue,
            "count": len(data),
            "disclosures": data,
        }
    finally:
        conn.close()


# ── GET /api/order-contracts/backlog/{code} ─────────────────────
@router.get("/backlog/{stock_code}")
def get_backlog_trend(stock_code: str, months: int = Query(default=24, ge=3, le=120)):
    """월별 신규계약 합계 - 해지계약 합계 누적 (수주잔고 proxy 추이)."""
    conn = _db()
    try:
        since = (date.today() - timedelta(days=months * 30)).strftime("%Y-%m-%d")
        rows = conn.execute(
            """SELECT substr(rcept_dt,1,7) AS ym,
                      SUM(CASE WHEN is_termination=0 THEN COALESCE(contract_amount,0) ELSE 0 END) AS new_amt,
                      SUM(CASE WHEN is_termination=1 THEN COALESCE(contract_amount,0) ELSE 0 END) AS term_amt,
                      COUNT(*) AS cnt
               FROM order_contracts
               WHERE stock_code=? AND rcept_dt >= ? AND contract_amount IS NOT NULL
               GROUP BY ym ORDER BY ym ASC""",
            (stock_code, since),
        ).fetchall()

        revenue = _latest_annual_revenue(conn, stock_code)
        cum = 0.0
        data = []
        for r in rows:
            cum += (r["new_amt"] or 0) - (r["term_amt"] or 0)
            data.append({
                "month": r["ym"],
                "new_amount": r["new_amt"],
                "terminated_amount": r["term_amt"],
                "disclosure_count": r["cnt"],
                "cumulative_backlog_est": round(cum),
                "backlog_to_revenue_pct": round(cum / revenue * 100, 2) if revenue else None,
            })

        return {"stock_code": stock_code, "latest_annual_revenue": revenue, "data": data}
    finally:
        conn.close()


# ── GET /api/order-contracts/screener/surge ─────────────────────
@router.get("/screener/surge")
def get_surge_screener(
    window_months: int = Query(default=3, ge=1, le=12),
    min_growth_pct: float = Query(default=50.0, ge=0),
    limit: int = Query(default=50, ge=5, le=200),
):
    """
    최근 window_months개월 신규계약 합계가 직전 동기간 대비 min_growth_pct% 이상
    급증한 종목 스크리닝 (분기/사업보고서 발표 전 선행 포착 목적).
    """
    conn = _db()
    try:
        today = date.today()
        recent_since = (today - timedelta(days=window_months * 30)).strftime("%Y-%m-%d")
        prev_since = (today - timedelta(days=window_months * 60)).strftime("%Y-%m-%d")

        rows = conn.execute(
            """SELECT stock_code,
                      MAX(stock_name) AS stock_name,
                      SUM(CASE WHEN is_termination=0 AND rcept_dt>=? THEN COALESCE(contract_amount,0) ELSE 0 END) AS recent_sum,
                      SUM(CASE WHEN is_termination=0 AND rcept_dt>=? AND rcept_dt<? THEN COALESCE(contract_amount,0) ELSE 0 END) AS prev_sum,
                      SUM(CASE WHEN is_termination=0 AND rcept_dt>=? THEN 1 ELSE 0 END) AS recent_cnt,
                      MAX(CASE WHEN rcept_dt>=? THEN rcept_dt ELSE NULL END) AS latest_dt
               FROM order_contracts
               WHERE contract_amount IS NOT NULL
               GROUP BY stock_code
               HAVING recent_sum > 0""",
            (recent_since, prev_since, recent_since, recent_since, recent_since),
        ).fetchall()

        result = []
        for r in rows:
            recent_sum, prev_sum = r["recent_sum"] or 0, r["prev_sum"] or 0
            if prev_sum > 0:
                growth_pct = round((recent_sum - prev_sum) / prev_sum * 100, 1)
            elif recent_sum > 0:
                growth_pct = None  # 직전 동기간 계약공시 없음 → "신규 급증"으로 별도 표시
            else:
                continue

            is_new_surge = prev_sum == 0 and recent_sum > 0
            if not is_new_surge and (growth_pct is None or growth_pct < min_growth_pct):
                continue

            revenue = _latest_annual_revenue(conn, r["stock_code"])
            result.append({
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "recent_sum": recent_sum,
                "prev_sum": prev_sum,
                "growth_pct": growth_pct,
                "is_new_surge": is_new_surge,
                "recent_disclosure_count": r["recent_cnt"],
                "latest_disclosure_date": r["latest_dt"],
                "latest_annual_revenue": revenue,
                "recent_to_revenue_pct": round(recent_sum / revenue * 100, 2) if revenue else None,
            })

        result.sort(key=lambda x: (x["growth_pct"] is None, -(x["growth_pct"] or 0), -x["recent_sum"]))
        return {
            "window_months": window_months,
            "min_growth_pct": min_growth_pct,
            "count": len(result),
            "candidates": result[:limit],
        }
    finally:
        conn.close()


# ── POST /api/order-contracts/collect/today ─────────────────────
@router.post("/collect/today")
async def collect_today():
    """오늘자 전종목 수주(단일판매·공급계약) 공시 스캔 + 파싱 저장. 스케줄러/수동 겸용."""
    import config
    from collectors.dart_collector import DARTCollector

    dart = DARTCollector(api_key=config.DART_API_KEY)
    items = await dart.get_todays_contract_disclosures()

    conn = _db()
    saved = 0
    try:
        for item in items:
            try:
                if await _save_disclosure(conn, dart, item):
                    saved += 1
                    conn.commit()
            except Exception as e:
                logger.warning(f"[수주공시] {item.get('rcept_no')} 저장 오류: {e}")
    finally:
        conn.close()

    logger.info(f"[수주공시] 오늘자 스캔 완료 — 대상 {len(items)}건 중 신규 {saved}건 저장")
    return {"scanned": len(items), "saved": saved}


# ── POST /api/order-contracts/collect/{code} ────────────────────
@router.post("/collect/{stock_code}")
async def collect_stock_history(stock_code: str, months: int = Query(default=24, ge=1, le=120)):
    """특정 종목의 최근 N개월 수주공시 백필."""
    if not (stock_code.isdigit() and len(stock_code) == 6):
        raise HTTPException(status_code=400, detail="6자리 종목코드가 필요합니다")

    import config
    from collectors.dart_collector import DARTCollector

    dart = DARTCollector(api_key=config.DART_API_KEY)
    end = date.today().strftime("%Y%m%d")
    start = (date.today() - timedelta(days=months * 30)).strftime("%Y%m%d")
    items = await dart.get_contract_disclosures(stock_code, start, end)

    conn = _db()
    saved = 0
    try:
        for item in items:
            item["stock_code"] = stock_code
            try:
                if await _save_disclosure(conn, dart, item):
                    saved += 1
                    conn.commit()
            except Exception as e:
                logger.warning(f"[수주공시] {stock_code} {item.get('rcept_no')} 저장 오류: {e}")
    finally:
        conn.close()

    return {"stock_code": stock_code, "scanned": len(items), "saved": saved}


# ── PATCH /api/order-contracts/{id}/verify ──────────────────────
@router.patch("/{contract_id}/verify")
def verify_contract(contract_id: int, payload: dict):
    """사람 검증: contract_amount/revenue_ratio_pct/counterpart 등 정정 후 verified=1."""
    fields = [k for k in [
        "contract_amount", "revenue_ratio_pct", "recent_revenue",
        "counterpart", "contract_date", "contract_start", "contract_end", "is_termination",
    ] if k in payload]

    conn = _db()
    try:
        sets = ", ".join(f"{k}=?" for k in fields)
        vals = [payload[k] for k in fields]
        if sets:
            conn.execute(
                f"UPDATE order_contracts SET {sets}, verified=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                vals + [contract_id],
            )
        else:
            conn.execute(
                "UPDATE order_contracts SET verified=1, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (contract_id,),
            )
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()


# ── DELETE /api/order-contracts/{id} ────────────────────────────
@router.delete("/{contract_id}")
def delete_contract(contract_id: int):
    conn = _db()
    try:
        conn.execute("DELETE FROM order_contracts WHERE id=?", (contract_id,))
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()
