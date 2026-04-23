"""
routes/backtest.py — 백테스트 API

  POST   /api/backtest/run
  GET    /api/backtest/list
  GET    /api/backtest/{run_id}
  DELETE /api/backtest/{run_id}
"""

import json
import logging
import sqlite3 as _sl
import threading
import uuid

import backtest as _bt
from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"

# 서버 시작 시 테이블 초기화
_bt.init_backtest_db()


def _db():
    return _sl.connect(DB_PATH, timeout=30)


@router.post("/run")
async def start_backtest(payload: dict):
    """백테스트 비동기 실행. 즉시 run_id 반환."""
    start    = payload.get("start_date", "2023-04-01")
    end      = payload.get("end_date",   "2025-12-31")
    per_s    = float(payload.get("per_stock", 10_000_000))
    strategy = payload.get("strategy", "v5")  # v5=AI콤보, v6=Logic#5 국면적응형
    strategy_label = {'v5': 'AI 콤보 v5', 'v6': 'Logic #5 국면적응형'}.get(strategy, strategy)
    name     = payload.get("name", f"[{strategy_label}] {start[:7]}~{end[:7]}")
    run_id   = str(uuid.uuid4())[:8]

    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,start_date,end_date,per_stock,status) "
        "VALUES (?,?,?,?,?,'running')",
        (run_id, name, start, end, per_s)
    )
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest(start, end, per_stock=per_s, run_name=name,
                             run_id=run_id, strategy=strategy)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()

    threading.Thread(target=_run, daemon=True, name=f"Backtest-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.get("/list")
def list_backtests():
    conn = _db()
    rows = conn.execute(
        "SELECT run_id,name,start_date,end_date,per_stock,status,"
        "total_return_pct,ann_return_pct,win_rate,total_trades,"
        "profit_trades,max_drawdown_pct,summary_text,created_at "
        "FROM backtest_runs ORDER BY created_at DESC LIMIT 20"
    ).fetchall()
    conn.close()
    keys = ["run_id","name","start_date","end_date","per_stock","status",
            "total_return_pct","ann_return_pct","win_rate","total_trades",
            "profit_trades","max_drawdown_pct","summary_text","created_at"]
    return [dict(zip(keys, r)) for r in rows]


@router.get("/{run_id}")
def get_backtest_result(run_id: str):
    conn = _db()
    row  = conn.execute(
        "SELECT status, trades_json, summary_text FROM backtest_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    conn.close()
    if not row:
        return {"error": "not found"}
    status, tj, summary = row
    detail = json.loads(tj) if tj else {}
    detail["status"]       = status
    detail["summary_text"] = summary
    return detail


@router.delete("/{run_id}")
def delete_backtest(run_id: str):
    conn = _db()
    conn.execute("DELETE FROM backtest_runs WHERE run_id=?", (run_id,))
    conn.commit(); conn.close()
    return {"status": "ok"}
