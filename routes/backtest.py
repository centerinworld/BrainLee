"""
routes/backtest.py — 백테스트 API

  POST   /api/backtest/run              개별 백테스트 실행
  POST   /api/backtest/run-all          전체(4전략 × 5기간) 일괄 실행
  GET    /api/backtest/periods          기간 프리셋 목록 (signal_logic.BACKTEST_PERIODS)
  GET    /api/backtest/strategies       전략 목록 (signal_logic.BACKTEST_STRATEGIES)
  GET    /api/backtest/list             결과 목록 (최근 100건)
  GET    /api/backtest/{run_id}         결과 상세
  DELETE /api/backtest/{run_id}         삭제
"""

import json
import logging
import sqlite3 as _sl
import threading
import uuid

import backtest as _bt
from fastapi import APIRouter
from signal_logic import BACKTEST_PERIODS, BACKTEST_STRATEGIES

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"

# 서버 시작 시 테이블 초기화
_bt.init_backtest_db()

_STRATEGY_LABEL = {s["id"]: f"{s['label']} {s['name']}" for s in BACKTEST_STRATEGIES}


def _db():
    return _sl.connect(DB_PATH, timeout=30)


@router.post("/run")
async def start_backtest(payload: dict):
    """백테스트 비동기 실행. 즉시 run_id 반환."""
    start    = payload.get("start_date", "2023-04-01")
    end      = payload.get("end_date",   "2025-12-31")
    per_s    = float(payload.get("per_stock", 10_000_000))
    strategy = payload.get("strategy", "v5")
    name     = payload.get("name", f"[{_STRATEGY_LABEL.get(strategy, strategy)}] {start[:7]}~{end[:7]}")
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


@router.post("/run-all")
async def run_all_backtests(payload: dict = None):
    """4개 전략 × 5개 기간 = 20회 백테스트 일괄 실행 (백그라운드).
    즉시 run_ids 목록 반환. 각 실행은 /api/backtest/{run_id}로 폴링.
    """
    payload      = payload or {}
    per_s        = float(payload.get("per_stock", 10_000_000))
    strategies   = payload.get("strategies", [s["id"] for s in BACKTEST_STRATEGIES])
    period_ids   = payload.get("period_ids",  [p["id"] for p in BACKTEST_PERIODS])

    selected_periods    = [p for p in BACKTEST_PERIODS    if p["id"] in period_ids]
    selected_strategies = [s for s in BACKTEST_STRATEGIES if s["id"] in strategies]

    run_ids = []
    conn = _db()
    for period in selected_periods:
        for strat in selected_strategies:
            rid  = str(uuid.uuid4())[:8]
            name = f"[{strat['label']} {strat['name']}] {period['label']} ({period['badge']})"
            conn.execute(
                "INSERT OR IGNORE INTO backtest_runs (run_id,name,start_date,end_date,per_stock,status) "
                "VALUES (?,?,?,?,?,'queued')",
                (rid, name, period["start_date"], period["end_date"], per_s)
            )
            run_ids.append({
                "run_id":    rid,
                "strategy":  strat["id"],
                "period_id": period["id"],
                "name":      name,
            })
    conn.commit(); conn.close()

    def _run_sequential(jobs):
        for job in jobs:
            try:
                c = _sl.connect(DB_PATH, timeout=30)
                c.execute("UPDATE backtest_runs SET status='running' WHERE run_id=?", (job["run_id"],))
                c.commit(); c.close()
                _bt.run_backtest(
                    job["start_date"], job["end_date"],
                    per_stock=per_s,
                    run_name=job["name"],
                    run_id=job["run_id"],
                    strategy=job["strategy"],
                )
            except Exception as e:
                c = _sl.connect(DB_PATH, timeout=30)
                c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                          (str(e), job["run_id"]))
                c.commit(); c.close()

    jobs = []
    for item, period in zip(run_ids,
                            [p for p in BACKTEST_PERIODS if p["id"] in period_ids
                             for _ in selected_strategies]):
        pass

    # 각 전략을 개별 스레드로 병렬 실행 (전략 간 독립적)
    for strat in selected_strategies:
        strat_jobs = [
            {**r,
             "start_date": next(p["start_date"] for p in BACKTEST_PERIODS if p["id"] == r["period_id"]),
             "end_date":   next(p["end_date"]   for p in BACKTEST_PERIODS if p["id"] == r["period_id"]),
            }
            for r in run_ids if r["strategy"] == strat["id"]
        ]
        threading.Thread(
            target=_run_sequential,
            args=(strat_jobs,),
            daemon=True,
            name=f"BT-all-{strat['id']}"
        ).start()

    return {
        "status":  "running",
        "total":   len(run_ids),
        "run_ids": [r["run_id"] for r in run_ids],
    }


@router.get("/periods")
def get_periods():
    """백테스트 기간 프리셋 목록 (signal_logic.BACKTEST_PERIODS)."""
    return BACKTEST_PERIODS


@router.get("/strategies")
def get_strategies():
    """백테스트 전략 목록 (signal_logic.BACKTEST_STRATEGIES)."""
    return BACKTEST_STRATEGIES


@router.get("/list")
def list_backtests():
    conn = _db()
    rows = conn.execute(
        "SELECT run_id,name,start_date,end_date,per_stock,status,"
        "total_return_pct,ann_return_pct,cagr,win_rate,total_trades,"
        "profit_trades,max_drawdown_pct,sharpe,pl_ratio,summary_text,created_at "
        "FROM backtest_runs ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    keys = ["run_id","name","start_date","end_date","per_stock","status",
            "total_return_pct","ann_return_pct","cagr","win_rate","total_trades",
            "profit_trades","max_drawdown_pct","sharpe","pl_ratio","summary_text","created_at"]
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
