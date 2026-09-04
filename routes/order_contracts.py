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
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = str(Path(__file__).resolve().parents[1] / "stock.db")

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

# 2026-09 신규: 정정공시("[기재정정]단일판매·공급계약체결" 등) 매칭용 컬럼.
# is_correction=1인 행은 원 공시를 갱신(supersede)만 하고 남겨두는 감사기록이라
# 백로그/급증 스크리너 집계에서는 제외해야 한다(아래 SUM 쿼리 필터 참조).
_CORRECTION_COLUMNS = {
    "is_correction": "INTEGER DEFAULT 0",
    "corrects_rcept_no": "TEXT",
    "corrected_by_rcept_no": "TEXT",
}


def _db():
    conn = _sl.connect(DB_PATH, timeout=30)
    conn.row_factory = _sl.Row
    conn.execute(_CREATE_TABLE)
    conn.execute(_CREATE_INDEX)
    existing = {row[1] for row in conn.execute("PRAGMA table_info(order_contracts)").fetchall()}
    for col, typ in _CORRECTION_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE order_contracts ADD COLUMN {col} {typ}")
    conn.commit()
    return conn


def _latest_annual_revenue(conn, stock_code: str) -> float:
    row = conn.execute(
        """SELECT revenue FROM financial_data
           WHERE stock_code=? AND is_annual=1 AND revenue IS NOT NULL AND revenue > 0
           ORDER BY year DESC,
                    (CASE WHEN report_type='CFS' THEN 0 ELSE 1 END),
                    (CASE WHEN quarter=4 THEN 0 WHEN quarter=0 THEN 1 ELSE 2 END),
                    (CASE WHEN data_source='dart' THEN 0 ELSE 1 END),
                    id DESC
           LIMIT 1""",
        (stock_code,),
    ).fetchone()
    return float(row[0]) if row else 0.0


def _stock_name(conn, stock_code: str) -> str:
    row = conn.execute(
        "SELECT stock_name FROM stock_universe WHERE stock_code=? ORDER BY base_date DESC LIMIT 1",
        (stock_code,),
    ).fetchone()
    return row[0] if row else stock_code


def _latest_order_contract_date(conn) -> date | None:
    row = conn.execute("SELECT MAX(rcept_dt) FROM order_contracts").fetchone()
    raw = row[0] if row else None
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _find_original_order_contract(conn, stock_code: str, corrects_disclosed_at: str) -> str | None:
    """정정공시가 가리키는 원 공시(rcept_no)를 stock_code+공시일로 찾는다."""
    if not stock_code or not corrects_disclosed_at:
        return None
    row = conn.execute(
        """SELECT rcept_no FROM order_contracts
           WHERE stock_code=? AND COALESCE(is_correction,0)=0 AND rcept_dt=?
           ORDER BY rcept_no DESC LIMIT 1""",
        (stock_code, corrects_disclosed_at),
    ).fetchone()
    return row[0] if row else None


async def _save_disclosure(conn, dart, item: dict) -> bool:
    """단일 공시 항목 파싱 후 upsert. 이미 존재하면 스킵. 저장되면 True."""
    rcept_no = item["rcept_no"]
    exists = conn.execute("SELECT 1 FROM order_contracts WHERE rcept_no=?", (rcept_no,)).fetchone()
    if exists:
        return False

    parsed = await dart.parse_contract_document(rcept_no)
    rcept_dt = item.get("rcept_dt", "")
    fmt_dt = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}" if len(rcept_dt) == 8 else rcept_dt

    # 정정공시("[기재정정]...")면 원 공시를 찾아 정정후 값으로 갱신(supersede)하고,
    # 이 행 자체는 is_correction=1 감사기록으로만 남긴다 — 집계(SUM)에서는 제외된다.
    # 2026-09 신규: 이전에는 "정정" 공시를 아예 수집대상에서 제외해 계약금액 축소/
    # 기간연장 등 실제 변경사항이 화면에 전혀 반영되지 않았음.
    stock_code = item["stock_code"]
    corrects_rcept_no = None
    if parsed.get("is_correction"):
        corrects_rcept_no = _find_original_order_contract(
            conn, stock_code, parsed.get("corrects_disclosed_at") or ""
        )
        if corrects_rcept_no:
            conn.execute(
                """UPDATE order_contracts SET
                       contract_amount=?, revenue_ratio_pct=?, recent_revenue=?,
                       contract_end=?, corrected_by_rcept_no=?, updated_at=CURRENT_TIMESTAMP
                   WHERE rcept_no=?""",
                (
                    parsed.get("contract_amount"), parsed.get("revenue_ratio_pct"),
                    parsed.get("recent_revenue"), parsed.get("contract_end"),
                    rcept_no, corrects_rcept_no,
                ),
            )

    conn.execute(
        """INSERT OR IGNORE INTO order_contracts
           (stock_code, stock_name, rcept_no, rcept_dt, report_nm, is_termination,
            contract_amount, revenue_ratio_pct, recent_revenue, counterpart,
            contract_date, contract_start, contract_end, parse_ok, raw_snippet, dart_url,
            is_correction, corrects_rcept_no)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            stock_code, item.get("corp_name") or _stock_name(conn, stock_code),
            rcept_no, fmt_dt, item.get("report_nm", ""), int(item.get("is_termination", False)),
            parsed.get("contract_amount"), parsed.get("revenue_ratio_pct"), parsed.get("recent_revenue"),
            parsed.get("counterpart"), parsed.get("contract_date"), parsed.get("contract_start"),
            parsed.get("contract_end"), int(parsed.get("parse_ok", False)), parsed.get("raw_snippet", ""),
            f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}",
            int(bool(parsed.get("is_correction"))), corrects_rcept_no,
        ),
    )
    return True


async def collect_recent_disclosures(max_backfill_days: int = 14) -> dict:
    """Catch up recent DART contract disclosures from the last saved date."""
    import config
    from collectors.dart_collector import DARTCollector

    today = date.today()
    fallback_start = today - timedelta(days=max_backfill_days)
    conn = _db()
    try:
        latest_dt = _latest_order_contract_date(conn)
    finally:
        conn.close()

    start_dt = fallback_start
    if latest_dt:
        start_dt = max(fallback_start, latest_dt + timedelta(days=1))
    if start_dt > today:
        start_dt = today

    start_str = start_dt.strftime("%Y%m%d")
    end_str = today.strftime("%Y%m%d")
    dart = DARTCollector(api_key=config.DART_API_KEY)
    items: list[dict] = []
    cursor = start_dt
    while cursor <= today:
        day_str = cursor.strftime("%Y%m%d")
        items.extend(await dart.get_contract_disclosures_range(day_str, day_str))
        cursor += timedelta(days=1)

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

    logger.info(
        "[수주공시] 최근 구간 스캔 완료 — %s~%s, 대상 %s건 중 신규 %s건 저장",
        start_str,
        end_str,
        len(items),
        saved,
    )
    latest_source_dt = max((item.get("rcept_dt") for item in items if item.get("rcept_dt")), default=None)
    return {
        "start_date": start_str,
        "end_date": end_str,
        "scanned": len(items),
        "saved": saved,
        "source_latest_date": latest_source_dt,
    }


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
                 AND COALESCE(is_correction,0)=0
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
                      SUM(CASE WHEN is_termination=0 AND rcept_dt>=? AND verified=1 THEN 1 ELSE 0 END) AS verified_cnt,
                      SUM(CASE WHEN is_termination=0 AND rcept_dt>=? AND parse_ok=1 THEN 1 ELSE 0 END) AS parse_ok_cnt,
                      SUM(CASE WHEN is_termination=0 AND rcept_dt>=? AND (verified=1 OR parse_ok=1) THEN 1 ELSE 0 END) AS reviewed_cnt,
                      MAX(CASE WHEN rcept_dt>=? THEN rcept_dt ELSE NULL END) AS latest_dt
               FROM order_contracts
               WHERE contract_amount IS NOT NULL
                 AND length(stock_code)=6
                 AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                 AND COALESCE(is_correction,0)=0
               GROUP BY stock_code
               HAVING SUM(CASE WHEN is_termination=0 AND rcept_dt>=? THEN COALESCE(contract_amount,0) ELSE 0 END) > 0""",
            (recent_since, prev_since, recent_since, recent_since, recent_since, recent_since, recent_since, recent_since, recent_since),
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
            recent_to_revenue_pct = round(recent_sum / revenue * 100, 2) if revenue else None
            signal_score = 1
            if is_new_surge:
                signal_score += 1
            elif growth_pct is not None and growth_pct >= 500:
                signal_score += 3
            elif growth_pct is not None and growth_pct >= 200:
                signal_score += 2
            elif growth_pct is not None and growth_pct >= 50:
                signal_score += 1
            if recent_to_revenue_pct is not None:
                if recent_to_revenue_pct >= 100:
                    signal_score += 3
                elif recent_to_revenue_pct >= 50:
                    signal_score += 2
                elif recent_to_revenue_pct >= 20:
                    signal_score += 1
            if (r["recent_cnt"] or 0) >= 2:
                signal_score += 1
            signal_score = min(signal_score, 10)
            needs_verification = (r["reviewed_cnt"] or 0) < (r["recent_cnt"] or 0)
            result.append({
                "stock_code": r["stock_code"],
                "stock_name": r["stock_name"],
                "recent_sum": recent_sum,
                "prev_sum": prev_sum,
                "growth_pct": growth_pct,
                "is_new_surge": is_new_surge,
                "recent_disclosure_count": r["recent_cnt"],
                "verified_count": r["verified_cnt"] or 0,
                "parse_ok_count": r["parse_ok_cnt"] or 0,
                "reviewed_count": r["reviewed_cnt"] or 0,
                "needs_verification": needs_verification,
                "signal_score": signal_score,
                "latest_disclosure_date": r["latest_dt"],
                "latest_annual_revenue": revenue,
                "recent_to_revenue_pct": recent_to_revenue_pct,
            })

        result.sort(key=lambda x: (-x["signal_score"], x["needs_verification"], -(x["growth_pct"] or 0), -x["recent_sum"]))
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
    """오늘 포함 최근 누락 구간을 캐치업 스캔한다."""
    return await collect_recent_disclosures()


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
        exists = conn.execute("SELECT 1 FROM order_contracts WHERE id=? LIMIT 1", (contract_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="해당 수주공시를 찾을 수 없습니다")
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
        cur = conn.execute("DELETE FROM order_contracts WHERE id=?", (contract_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="해당 수주공시를 찾을 수 없습니다")
        conn.commit()
        return {"status": "ok"}
    finally:
        conn.close()
