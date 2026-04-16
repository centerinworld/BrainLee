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

import sqlite3 as _sl
import subprocess
import threading
import logging
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH    = "/Applications/stock_dashboard/stock.db"
COLLECTOR  = "/Applications/stock_dashboard/telegram_collector.py"
PYTHON     = "/Applications/stock_dashboard/venv/bin/python3"


def _db():
    return _sl.connect(DB_PATH)


# ── 채널 관리 ────────────────────────────────────────────────────

@router.get("/channels")
def get_channels():
    conn = _db()
    rows = conn.execute(
        "SELECT id,channel_id,channel_name,is_active,last_sync FROM telegram_channels ORDER BY id"
    ).fetchall()
    conn.close()
    return [{"id": r[0], "channel_id": r[1], "channel_name": r[2],
             "is_active": bool(r[3]), "last_sync": r[4]} for r in rows]


@router.post("/channels")
def add_channel(payload: dict):
    ch_id = payload.get("channel_id", "").strip()
    if not ch_id:
        raise HTTPException(status_code=400, detail="channel_id 필수")
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO telegram_channels (channel_id,channel_name) VALUES (?,?)",
        (ch_id, payload.get("channel_name", ch_id))
    )
    conn.commit(); conn.close()
    return {"status": "ok", "channel_id": ch_id}


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: str):
    conn = _db()
    conn.execute("UPDATE telegram_channels SET is_active=0 WHERE channel_id=?", (channel_id,))
    conn.commit(); conn.close()
    return {"status": "ok"}


@router.post("/collect")
def trigger_collect(payload: dict = {}):
    channel = payload.get("channel_id", "")
    def _run():
        cmd = [PYTHON, COLLECTOR]
        if channel:
            cmd += ["--channel", channel]
        subprocess.run(cmd, capture_output=True)
    threading.Thread(target=_run, daemon=True).start()
    return {"status": "collecting", "channel": channel or "전체"}


# ── 종목 언급 통계 ────────────────────────────────────────────────

@router.get("/mentions/daily")
def get_daily_mentions():
    today = date.today()
    dates = [(today - timedelta(days=i)).isoformat() for i in range(6, -1, -1)]
    conn  = _db()
    rows  = conn.execute(
        "SELECT mention_date, stock_name, market, mention_count FROM tg_daily_mentions "
        "WHERE mention_date >= ? ORDER BY mention_date, mention_count DESC",
        (dates[0],)
    ).fetchall()
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
            {"stock_name": n, "market": market_map.get(n, "미확인"), "total": t,
             "daily": {d: daily[n].get(d, 0) for d in dates}}
            for n, t in top20
        ],
    }


@router.get("/mentions/weekly")
def get_weekly_mentions():
    since = (date.today() - timedelta(days=5)).isoformat()
    conn  = _db()
    rows  = conn.execute(
        "SELECT stock_name, market, SUM(mention_count) FROM tg_daily_mentions "
        "WHERE mention_date >= ? GROUP BY stock_name ORDER BY 3 DESC LIMIT 20",
        (since,)
    ).fetchall()
    conn.close()
    return [{"stock_name": r[0], "market": r[1], "count": r[2]} for r in rows]


@router.get("/mentions/monthly")
def get_monthly_mentions():
    since = date.today().replace(day=1).isoformat()
    conn  = _db()
    rows  = conn.execute(
        "SELECT stock_name, market, SUM(mention_count) FROM tg_daily_mentions "
        "WHERE mention_date >= ? GROUP BY stock_name ORDER BY 3 DESC LIMIT 20",
        (since,)
    ).fetchall()
    conn.close()
    return [{"stock_name": r[0], "market": r[1], "count": r[2]} for r in rows]
