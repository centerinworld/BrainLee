"""
routes/telegram.py — 텔레그램 채널 관리 + 종목 언급 통계 API

  GET    /api/telegram/channels
  POST   /api/telegram/channels
  DELETE /api/telegram/channels/{channel_id}
  POST   /api/telegram/collect
  GET    /api/telegram/mentions/daily
  GET    /api/telegram/mentions/weekly
  GET    /api/telegram/mentions/monthly
"""

from db_compat import connect_primary_db
from telegram_store import ensure_schema
import sqlite3 as _sl
import subprocess
import threading
import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH    = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
COLLECTOR  = "/Volumes/Realtek_NVME/stock_dashboard/runtime/telegram_collector.py"
PYTHON     = "/Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python3"


def _db():
    conn = connect_primary_db(timeout=30)
    ensure_schema(conn)
    return conn


def _b(v) -> int:
    return 1 if v in (True, 1, "1", "true", "True") else 0


# ── 채널 관리 ────────────────────────────────────────────────────

@router.get("/channels")
def get_channels():
    """등록 채널 + 수집 옵션 + 수집 누적 건수(PDF/본문/사진)."""
    conn = _db()
    try:
        rows = conn.execute(
            "SELECT id,channel_id,channel_name,is_active,last_sync,collect_pdf,collect_text,collect_photo "
            "FROM telegram_channels ORDER BY id"
        ).fetchall()
        pdf = dict(conn.execute("SELECT channel_id, COUNT(*) FROM report_files GROUP BY channel_id").fetchall())
        post = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT channel_id, COUNT(*), SUM(CASE WHEN photo_path <> '' THEN 1 ELSE 0 END), MAX(msg_date) "
            "FROM telegram_channel_posts GROUP BY channel_id").fetchall()}
    finally:
        conn.close()
    out = []
    for r in rows:
        p = post.get(r[1], (0, 0, None))
        out.append({
            "id": r[0], "channel_id": r[1], "channel_name": r[2],
            "is_active": bool(r[3]), "last_sync": r[4],
            "collect_pdf": bool(1 if r[5] is None else r[5]),
            "collect_text": bool(1 if r[6] is None else r[6]),
            "collect_photo": bool(r[7] or 0),
            "pdf_count": pdf.get(r[1], 0), "post_count": p[0], "photo_count": int(p[1] or 0),
            "last_post_date": p[2],
        })
    return out


@router.post("/channels")
def add_channel(payload: dict):
    """채널 추가(이미 있으면 옵션 갱신 후 재활성화)."""
    ch_id = str(payload.get("channel_id", "")).strip()
    if not ch_id:
        raise HTTPException(status_code=400, detail="channel_id 필수")
    name = str(payload.get("channel_name") or ch_id).strip()
    pdf = _b(payload.get("collect_pdf", True))
    text = _b(payload.get("collect_text", True))
    photo = _b(payload.get("collect_photo", False))
    if not (pdf or text or photo):
        raise HTTPException(status_code=400, detail="수집 항목을 하나 이상 선택하세요")
    hint = str(payload.get("entity_hint") or "").strip() or None
    conn = _db()
    try:
        conn.execute(
            """INSERT INTO telegram_channels
               (channel_id, channel_name, entity_hint, collect_pdf, collect_text, collect_photo, is_active)
               VALUES (?,?,?,?,?,?,1)
               ON CONFLICT(channel_id) DO UPDATE SET
                 channel_name=excluded.channel_name,
                 entity_hint=COALESCE(excluded.entity_hint, telegram_channels.entity_hint),
                 collect_pdf=excluded.collect_pdf, collect_text=excluded.collect_text,
                 collect_photo=excluded.collect_photo, is_active=1""",
            (ch_id, name, hint, pdf, text, photo))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "channel_id": ch_id}


@router.patch("/channels/{channel_id:path}")
def update_channel(channel_id: str, payload: dict):
    """수집 옵션/이름/활성 상태 수정."""
    sets, vals = [], []
    for k in ("collect_pdf", "collect_text", "collect_photo", "is_active"):
        if k in payload:
            sets.append(f"{k}=?"); vals.append(_b(payload[k]))
    if "channel_name" in payload:
        sets.append("channel_name=?"); vals.append(str(payload["channel_name"]).strip())
    if not sets:
        raise HTTPException(status_code=400, detail="변경할 항목 없음")
    conn = _db()
    try:
        cur = conn.execute(f"UPDATE telegram_channels SET {', '.join(sets)} WHERE channel_id=?", (*vals, channel_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="채널 없음")
    finally:
        conn.close()
    return {"status": "ok"}


@router.delete("/channels/{channel_id:path}")
def delete_channel(channel_id: str, hard: bool = True):
    """채널 삭제. 기본은 목록에서 완전 삭제(이미 수집한 PDF·본문·사진 데이터는 보존).
    hard=false 면 수집만 중단(비활성화)."""
    conn = _db()
    try:
        if hard:
            conn.execute("DELETE FROM telegram_channels WHERE channel_id=?", (channel_id,))
        else:
            conn.execute("UPDATE telegram_channels SET is_active=0 WHERE channel_id=?", (channel_id,))
        conn.commit()
    finally:
        conn.close()
    return {"status": "ok", "hard": hard}


@router.post("/collect")
def trigger_collect(payload: dict = {}):
    """수집 즉시 실행. channel_id 생략 시 활성 채널 전체. days/limit 으로 과거분 backfill 가능."""
    channel = str(payload.get("channel_id", "") or "")
    cmd = [PYTHON, COLLECTOR]
    if channel:
        cmd += ["--channel", channel]
    try:
        if payload.get("limit"):
            cmd += ["--limit", str(max(1, min(int(payload["limit"]), 20000)))]
        if payload.get("days"):
            cmd += ["--days", str(max(1, min(int(payload["days"]), 3650)))]
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="limit/days 는 숫자")

    def _run():
        subprocess.run(cmd, capture_output=True, cwd="/Volumes/Realtek_NVME/stock_dashboard/runtime")
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "collecting", "channel": channel or "전체"}


@router.get("/posts")
def get_posts(channel_id: str = "", q: str = "", limit: int = 50, offset: int = 0):
    """수집된 채팅 본문 조회(최신순). q 는 본문 부분일치."""
    limit = max(1, min(limit, 200)); offset = max(0, offset)
    where, params = [], []
    if channel_id:
        where.append("channel_id=?"); params.append(channel_id)
    if q.strip():
        where.append("text LIKE ?"); params.append(f"%{q.strip()}%")
    w = ("WHERE " + " AND ".join(where)) if where else ""
    conn = _db()
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM telegram_channel_posts {w}", params).fetchone()[0]
        rows = conn.execute(
            f"SELECT channel_id,message_id,msg_date,text,has_photo,photo_path,has_document,file_name,views "
            f"FROM telegram_channel_posts {w} ORDER BY msg_date DESC, message_id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset)).fetchall()
    finally:
        conn.close()
@router.post("/rag/index")
def trigger_rag_index(payload: dict = {}):
    """수집된 텔레그램 메시지를 Brian_RAG(LanceDB bge-m3)로 배치 임베딩 적재 트리거."""
    limit = int(payload.get("limit", 1000))
    since = payload.get("since")
    rag_python = "/Volumes/Realtek_NVME/Brian_RAG/venv/bin/python3"
    bridge_script = "/Volumes/Realtek_NVME/stock_dashboard/intel/rag_bridge.py"

    def _run():
        cmd = [rag_python, bridge_script, "--limit", str(limit)]
        if since:
            cmd += ["--since", str(since)]
        subprocess.run(cmd, capture_output=True, cwd="/Volumes/Realtek_NVME/Brian_RAG")
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "indexing_started", "limit": limit, "since": since}


@router.get("/rag/search")
def search_telegram_rag(q: str, limit: int = 10):
    """주식 투자 판단을 위해 텔레그램 및 리포트 RAG 지식 베이스 검색."""
    if not q.strip():
        raise HTTPException(status_code=400, detail="검색어를 입력하세요")
    import httpx
    try:
        r = httpx.post("http://127.0.0.1:8888/api/query", json={"query": q.strip()}, timeout=60.0)
        if r.status_code == 200:
            return r.json()
        raise HTTPException(status_code=r.status_code, detail="RAG 검색 실패")
    except Exception as e:
        logger.warning(f"RAG search error: {e}")
        return {"error": str(e), "message": "Brian_RAG 서비스가 127.0.0.1:8888에서 실행 중인지 확인하세요."}


# ── 종목 언급 통계 ────────────────────────────────────────────────

@router.get("/mentions/daily")
def get_daily_mentions():
    import sqlite3 as _sl
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    conn  = _db()
    rows  = conn.execute(
        "SELECT mention_date, stock_name, market, mention_count FROM tg_daily_mentions "
        "WHERE mention_date >= ? ORDER BY mention_date, mention_count DESC",
        (dates[0],)
    ).fetchall()
    # stock_code 조인 (stock_universe)
    su_conn = connect_primary_db()
    su_conn.row_factory = _sl.Row
    code_map = {r["stock_name"]: r["stock_code"] for r in su_conn.execute(
        "SELECT stock_name, stock_code FROM stock_universe"
    ).fetchall()}
    su_conn.close()
    conn.close()

    totals: dict = {}; daily: dict = {}; market_map: dict = {}
    for mention_date, stock_name, mkt, count in rows:
        if stock_name not in totals:
            totals[stock_name] = 0; daily[stock_name] = {}; market_map[stock_name] = mkt
        totals[stock_name] += count
        daily[stock_name][mention_date] = count

    top20 = sorted(totals.items(), key=lambda x: -x[1])[:20]
    return {
        "dates": dates,
        "stocks": [
            {"stock_name": n, "stock_code": code_map.get(n, ""),
             "market": market_map.get(n, "미확인"), "total": t,
             "daily": {d: daily[n].get(d, 0) for d in dates}}
            for n, t in top20
        ],
    }


@router.get("/mentions/weekly")
def get_weekly_mentions():
    import sqlite3 as _sl
    since = (date.today() - timedelta(days=5)).isoformat()
    conn  = _db()
    rows  = conn.execute(
        "SELECT stock_name, MAX(market), SUM(mention_count) FROM tg_daily_mentions "
        "WHERE mention_date >= ? GROUP BY stock_name ORDER BY 3 DESC LIMIT 20",
        (since,)
    ).fetchall()
    su_conn = connect_primary_db()
    su_conn.row_factory = _sl.Row
    code_map = {r["stock_name"]: r["stock_code"] for r in su_conn.execute(
        "SELECT stock_name, stock_code FROM stock_universe"
    ).fetchall()}
    su_conn.close()
    conn.close()
    return [{"stock_name": r[0], "stock_code": code_map.get(r[0], ""), "market": r[1], "count": r[2]} for r in rows]


@router.get("/mentions/monthly")
def get_monthly_mentions():
    import sqlite3 as _sl
    since = date.today().replace(day=1).isoformat()
    conn  = _db()
    rows  = conn.execute(
        "SELECT stock_name, MAX(market), SUM(mention_count) FROM tg_daily_mentions "
        "WHERE mention_date >= ? GROUP BY stock_name ORDER BY 3 DESC LIMIT 20",
        (since,)
    ).fetchall()
    su_conn = connect_primary_db()
    su_conn.row_factory = _sl.Row
    code_map = {r["stock_name"]: r["stock_code"] for r in su_conn.execute(
        "SELECT stock_name, stock_code FROM stock_universe"
    ).fetchall()}
    su_conn.close()
    conn.close()
    return [{"stock_name": r[0], "stock_code": code_map.get(r[0], ""), "market": r[1], "count": r[2]} for r in rows]
