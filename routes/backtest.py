"""
routes/backtest.py — 백테스트 API

  POST   /api/backtest/run
  GET    /api/backtest/list
  GET    /api/backtest/{run_id}
  DELETE /api/backtest/{run_id}
  GET    /api/backtest/monthly-picks          ★ 신규 — 월별 종목 추천
  GET    /api/backtest/strategies             ★ 신규 — 전략 카탈로그
  GET    /api/backtest/strategy-research/summary
  POST   /api/backtest/strategy-research/rebuild
"""

import json
import logging
import math
import sqlite3 as _sqlite3
import threading
import uuid
from pathlib import Path

import backtest as _bt
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from fastapi import APIRouter, HTTPException, Query

from merged_simulator import MergeConfig, persist_merged_run, simulate_merged_account
from run_registry import derive_status, register_run_set, registry as selected_registry, select_run
from security_master import resolve_security
from strategy_governance import classify_strategy, summarize_governance
from config import IS_POSTGRES
from db_compat import connect_primary_db

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
RESEARCH_SUMMARY_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/strategy_research_summary.json")
QUALITY_FACTOR_VALIDATION_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/new_quality_factor_validation_20260726.json")
QUALITY_OVERLAY_SWEEP_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/quality_overlay_sweep_20260726.json")
QUALITY_OVERLAY_MONTHLY_BACKTEST_PATH = Path("/Volumes/Realtek_NVME/stock_dashboard/runtime/research_outputs/quality_overlay_monthly_backtest_20260726.json")


class _DatabaseRouter:
    Row = _sqlite3.Row
    Error = Exception

    @staticmethod
    def connect(database, *args, **kwargs):
        if IS_POSTGRES and str(database) == DB_PATH:
            return connect_primary_db(timeout=float(kwargs.get("timeout", 30)))
        return _sqlite3.connect(database, *args, **kwargs)


_sl = _DatabaseRouter()

# 서버 시작 시 테이블 초기화
_bt.init_backtest_db()


def _db():
    return _sl.connect(DB_PATH, timeout=30)


def _json_safe(value):
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _current_market_regime() -> dict:
    conn = _db()
    rows = conn.execute("""
        SELECT date, close
        FROM price_history
        WHERE stock_code='^KS11' AND close>0
        ORDER BY date
    """).fetchall()
    conn.close()
    if len(rows) < 140:
        return {"regime": "NEUTRAL", "diff_pct": 0.0, "as_of": None}
    closes = [float(r[1]) for r in rows]
    as_of = rows[-1][0]
    ma120 = sum(closes[-120:]) / 120.0
    diff_pct = ((closes[-1] / ma120) - 1.0) * 100.0 if ma120 else 0.0
    if diff_pct >= 6:
        regime = "BULL"
    elif diff_pct <= -6:
        regime = "BEAR"
    else:
        regime = "NEUTRAL"
    return {"regime": regime, "diff_pct": round(diff_pct, 2), "as_of": as_of}


def _strategy_rankings_for_regime() -> dict:
    regime = _current_market_regime()
    bucket_map = {
        "BULL": [("2020-03-01", "2021-11-30"), ("2023-11-01", "2024-12-31"), ("2025-06-01", "2026-03-31")],
        "BEAR": [("2021-12-01", "2022-10-31")],
        "NEUTRAL": [("2022-11-01", "2023-10-31"), ("2024-06-01", "2025-05-31")],
    }
    periods = bucket_map.get(regime["regime"], bucket_map["NEUTRAL"])
    conn = _db()
    rows = conn.execute("""
        SELECT strategy, start_date, end_date, total_return_pct
        FROM backtest_runs
        WHERE status='done'
          AND total_return_pct IS NOT NULL
    """).fetchall()
    conn.close()
    keep = {}
    for strategy, start_date, end_date, total_return_pct in rows:
        key = (strategy or "combo", start_date, end_date)
        keep[key] = float(total_return_pct)
    ranking = []
    for strategy in ALL_STRATEGIES_EX:
        vals = [keep[(strategy, sd, ed)] for sd, ed in periods if (strategy, sd, ed) in keep]
        if not vals:
            continue
        ranking.append({
            "strategy": strategy,
            "label": STRATEGY_LABELS.get(strategy, strategy),
            "avg_ret": round(sum(vals) / len(vals), 2),
            "sample_count": len(vals),
        })
    ranking.sort(key=lambda x: x["avg_ret"], reverse=True)
    return {
        "regime": regime,
        "periods": [{"start_date": sd, "end_date": ed} for sd, ed in periods],
        "top": ranking[:8],
    }


@router.post("/run")
async def start_backtest(payload: dict):
    """백테스트 비동기 실행. 즉시 run_id 반환."""
    start   = payload.get("start_date", "2023-04-01")
    end     = payload.get("end_date",   "2025-12-31")
    per_s   = float(payload.get("per_stock", 10_000_000))
    name    = payload.get("name", f"백테스트 {start[:7]}~{end[:7]}")
    run_id  = str(uuid.uuid4())[:8]

    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,start_date,end_date,per_stock,status) "
        "VALUES (?,?,?,?,?,'running')",
        (run_id, name, start, end, per_s)
    )
    conn.commit(); conn.close()

    def _run():
        try:
            # run_id를 직접 전달 → 내부에서 별도 UUID 생성 없이 동일 레코드에 저장
            _bt.run_backtest(start, end, per_stock=per_s, run_name=name, run_id=run_id)
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


@router.post("/run-v1")
async def start_backtest_v1(payload: dict):
    """V트렌드 MA정배열 (MA20>MA60>MA120 + RSI + 거래량) 백테스트."""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-05-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V트렌드 MA {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,?,?,?,?,'running')", (run_id, name, "v_trend", start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v1(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V1-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v1-dart")
async def start_backtest_v1_dart(payload: dict):
    """레거시(삭제 예정) — 호환성 유지용."""
    return {"error": "deprecated endpoint"}


@router.post("/run-vbr")
async def start_backtest_vbr(payload: dict):
    """V8 52W돌파 모멘텀 백테스트 (52주 고점 65%+ + MA정배열 + 거래량 모멘텀)."""
    start  = payload.get("start_date", "2020-01-01")
    end    = payload.get("end_date",   "2026-03-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V8 52W돌파 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'vbr',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()
    def _run():
        try:
            _bt.run_backtest_hidden_rev(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?", (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-VBR-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


def _deprecated_v1dart(payload: dict):
    """V트렌드 + DART 수주공시 ★2 이상 필터 백테스트 (비교용, 삭제됨)."""
    start     = payload.get("start_date", "2021-01-01")
    end       = payload.get("end_date",   "2025-12-31")
    per_s     = float(payload.get("per_stock", 10_000_000))
    dart_min  = int(payload.get("dart_min_signal", 2))
    name      = payload.get("name", f"V트렌드+DART★{dart_min} {start[:7]}~{end[:7]}")
    run_id    = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,?,?,?,?,'running')", (run_id, name, "v_dart", start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v1_dart(start, end, per_stock=per_s, dart_min_signal=dart_min,
                                     run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V1DART-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v8")
async def start_backtest_v8(payload: dict):
    """V8 수출선행 (HS무역통계 YoY+MA60변곡) 백테스트."""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-05-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V8 수출선행 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,?,?,?,?,'running')", (run_id, name, "v8", start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v8(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V8-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-golden-cross")
async def start_backtest_golden_cross(payload: dict):
    """V12 골든크로스 모멘텀 백테스트 (Trail25%/30%, RS6M 랭킹, 분할필터)."""
    start  = payload.get("start_date", "2020-01-01")
    end    = payload.get("end_date",   "2026-03-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V12골든크로스 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'golden_cross',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_golden_cross(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-GC-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-recovery")
async def start_backtest_recovery(payload: dict):
    """V-RECOVERY 낙폭과대 반등 전략 — 데이터 실증: MA60 -25%이상 하방 종목 3배 달성률 69%"""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V-RECOVERY낙폭반등 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'recovery',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_recovery(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-RECOVERY-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-turnaround")
async def start_backtest_turnaround(payload: dict):
    """V-TURNAROUND 흑자전환 특화 — BQ 실증: 흑자전환 종목 평균 6.14x (우량성장주 3.48x의 1.77배)"""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V-TURNAROUND흑자전환 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'turnaround',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_turnaround(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-TURNAROUND-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-deep-recovery")
async def start_backtest_deep_recovery(payload: dict):
    """V-DEEP 깊은낙폭집중 전략 — MA60 -25~-60% 실증 최강구간 집중"""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V-DEEP깊은낙폭 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'deep_recovery',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_deep_recovery(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-DEEP-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-low-base-breakout")
async def start_backtest_low_base_breakout(payload: dict):
    """V-LOWBASE 저점기반돌파 전략 — 실증: 3배+종목 86%가 MA60 ±15%이내, V-GC 직전 진입"""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V-LOWBASE저점기반 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'low_base_breakout',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_low_base_breakout(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-LOWBASE-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-high-profit-compound")
async def start_backtest_high_profit(payload: dict):
    """V13 고수익 집중 전략 — 임원매수+성장섹터+계약/수주 복합 필터"""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V13고수익집중 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'high_profit_compound',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_high_profit_compound(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-HPC-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-sector")
async def start_backtest_sector(payload: dict):
    """V-SECTOR 섹터 집중 투자 백테스트.
    섹터 BUY 신호 발생 시 해당 섹터 급등 후보 TOP3 집중 매수.
    """
    start  = payload.get("start_date", "2022-01-01")
    end    = payload.get("end_date",   "2026-03-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    buy_th = float(payload.get("buy_threshold", 55.0))
    name   = payload.get("name", f"V-SECTOR섹터집중 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'sector_focus',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_sector(start, end, per_stock=per_s, buy_threshold=buy_th,
                                    run_name=name, run_id=run_id)
        except Exception as e:
            import traceback
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (f"{e}\n{traceback.format_exc()}", run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-SECTOR-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v10")
async def start_backtest_v10(payload: dict):
    """V10 이익폭발 백테스트 비동기 실행."""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V10 이익폭발 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'v10',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v10(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V10-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v11")
async def start_backtest_v11(payload: dict):
    """V11 흑자전환 백테스트 비동기 실행."""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V11 흑자전환 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'v11',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v11(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V11-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v10-hs")
async def start_backtest_v10_hs(payload: dict):
    """레거시(삭제됨) — V6+수출 조합 검증용이었으나 V6 이익폭발로 통합."""
    return {"error": "deprecated, use /run-v10"}


def _deprecated_v10_hs(payload: dict):
    """V10 이익폭발 + HS 수출 YoY 필터 백테스트 (보너스 효과 검증용, 삭제됨)."""
    start  = payload.get("start_date", "2020-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    hs_min = float(payload.get("hs_yoy_min", 10.0))
    name   = payload.get("name", f"V10+HS수출≥{hs_min:.0f}% {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'v10_hs',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v10_hs(start, end, per_stock=per_s, hs_yoy_min=hs_min,
                                     run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error' WHERE run_id=?", (run_id,))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V10HS-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v11-hs")
async def start_backtest_v11_hs(payload: dict):
    """레거시(삭제됨) — V7+수출 조합 검증용이었으나 V7 이익가속으로 통합."""
    return {"error": "deprecated, use /run-v11"}


def _deprecated_v11_hs(payload: dict):
    """V11 흑자전환 + HS 수출 YoY 필터 백테스트 (보너스 효과 검증용, 삭제됨)."""
    start  = payload.get("start_date", "2020-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    hs_min = float(payload.get("hs_yoy_min", 10.0))
    name   = payload.get("name", f"V11+HS수출≥{hs_min:.0f}% {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'v11_hs',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v11_hs(start, end, per_stock=per_s, hs_yoy_min=hs_min,
                                     run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error' WHERE run_id=?", (run_id,))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V11HS-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v12")
async def start_backtest_v12(payload: dict):
    """V12 섹터대세 백테스트 비동기 실행."""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V12 섹터대세 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'v12',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v12(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V12-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-regime-adaptive")
async def start_backtest_regime_adaptive(payload: dict):
    """레짐 적응형 전략 (Meta-V): BULL→V1 MA추세, BEAR→V7 흑자전환 자동 전환."""
    start  = payload.get("start_date", "2020-03-01")
    end    = payload.get("end_date",   "2025-05-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"레짐 적응형 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'regime_adaptive',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_regime_adaptive(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-REGIME-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-composite")
async def start_backtest_composite(payload: dict):
    """V10 복합 스코어링 전략: 100점 중 60점 이상 고품질 신호만 매수 — 승률 45%+ 목표."""
    start  = payload.get("start_date", "2020-03-01")
    end    = payload.get("end_date",   "2025-05-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    thresh = int(payload.get("score_threshold", 60))
    name   = payload.get("name", f"V10 복합스코어링 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'composite',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_composite(start, end, per_stock=per_s,
                                       score_threshold=thresh, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-COMP-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v1-value")
async def start_backtest_v1_value(payload: dict):
    """V1 가치매수 (Graham 내재가치 25%+ 할인) 백테스트 비동기 실행."""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V1 가치매수 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'v1_value',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_value(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V1VALUE-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v2")
async def start_backtest_v2(payload: dict):
    """V2 재무스크리너 (수익성 스코어 ≥ 3점) 백테스트 비동기 실행."""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V2 재무스크리너 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'v2',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v2(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V2-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


@router.post("/run-v5")
async def start_backtest_v5(payload: dict):
    """V5 수급 주도 모멘텀 (기관+외국인 동반 순매수 + MA정배열) 백테스트 비동기 실행."""
    start  = payload.get("start_date", "2018-01-01")
    end    = payload.get("end_date",   "2025-12-31")
    per_s  = float(payload.get("per_stock", 10_000_000))
    name   = payload.get("name", f"V5 수급모멘텀 {start[:7]}~{end[:7]}")
    run_id = str(uuid.uuid4())[:8]
    conn = _db()
    conn.execute(
        "INSERT OR IGNORE INTO backtest_runs (run_id,name,strategy,start_date,end_date,per_stock,status) "
        "VALUES (?,?,'v5',?,?,?,'running')", (run_id, name, start, end, per_s))
    conn.commit(); conn.close()

    def _run():
        try:
            _bt.run_backtest_v5(start, end, per_stock=per_s, run_name=name, run_id=run_id)
        except Exception as e:
            c = _sl.connect(DB_PATH, timeout=30)
            c.execute("UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                      (str(e), run_id))
            c.commit(); c.close()
    threading.Thread(target=_run, daemon=True, name=f"BT-V5-{run_id}").start()
    return {"run_id": run_id, "status": "running"}


# ── 전략 재명명 (2026-06-21): 중복 제거 + V1~V9 체계 정립 ──────────────────
# 제거된 중복: combo(=V5), v10_hs(=V6 필터 변형), v11_hs(=V7 필터 변형), v_dart(=V1 필터 변형)
STRATEGY_LABELS = {
    "v_trend":         "V1 MA추세",
    "v1_value":        "V2 가치매수",
    "v2":              "V3 재무우량",
    "v5":              "V4 수급모멘텀",
    "v4":              "V5 복합콤보",
    "v10":             "V6 이익폭발",
    "v11":             "V7 이익가속",        # 흑자전환 → 이익가속(Earnings Acceleration) 재설계
    "vbr":             "V8 52W돌파",         # 신규: 52주 고점 돌파 모멘텀
    "v8":              "V9 수출선행",
    "v12":             "V10 섹터대세",       # V9→V10 재번호
    "regime_adaptive": "Meta-V 레짐 적응형",
    "composite":       "V11 복합스코어링",   # V10→V11 재번호
    "golden_cross":        "V12 골든크로스",
    "high_profit_compound":"V13 고수익집중",
    "sector_focus":        "V-SECTOR 주도섹터",
    "recovery":            "V-RECOVERY 낙폭과대반등",
    "deep_recovery":       "V-DEEP 깊은낙폭집중",
    "low_base_breakout":   "V-LOWBASE 저점기반돌파",
    "turnaround":          "V-TURNAROUND 흑자전환",
    "extreme_dd_volume":   "V-EXTREME 초낙폭거래량",
    "se_momentum":         "V-SE 주도섹터(스탁이지)",
    "megatrend":           "V-MEGATREND 구조테마추종",
    "earnings_conviction": "V-EARNINGS 실적가속 집중배분",
    "moonshot_turnaround": "V-MOONSHOT 턴어라운드 대박발굴",
    "contract_momentum":   "V-CONTRACT 해외수주 모멘텀",
    "earnings_supply_discovery": "V-DISCOVERY 공급+실적 발굴",
}

STRATEGY_DESC = {
    "v_trend":  "MA20>MA60>MA120 정배열 + RSI 42~72 + 거래량 ×1.3배. 잡음 최소화, 명확한 상승 구조에서만 진입하는 추세추종 기본형 ★AI랠리 최강",
    "v1_value": "Graham 내재가치 25%+ 할인 OR PBR<0.7·PER<10 + 영업흑자. 시장 사이클 무관 저평가 발굴 — 보수적 분할매수에 적합",
    "v2":       "영업이익률·ROE·ROA 수익성 3축 스코어 ≥3점 + 영업흑자 + 보조수급. 재무 우량주 장기 보유형 — 복리효과 극대화",
    "v5":       "기관+외국인 5일 동반 누적 순매수 + MA20>60>120 정배열 + 영업흑자. 스마트머니 방향에 편승하는 수급 주도 모멘텀",
    "v4":       "Minervini RS(상대강도) + Graham PBR·PER 저평가 + 기관·외국인 수급 삼중 필터. 기술적·기본적·수급 3가지 조건 동시 충족. 2026-07-27 as-of 시총(security_master_history 기반) 리트로핏 재등록(point_in_time_approx): avg6=+15.09%, 3/6기간 양수 [상승+63.19/하락0/회복+1.14/AI-1.69/최근-4.17/최신+32.08] — 구 +21.7%(4/6)는 현재시총 기준 유니버스 필터의 룩어헤드 포함 수치.",
    "v10":      "영업이익 YoY≥80% + 매출 YoY≥30% 2분기 연속 확인 + KOSPI MA60 위. 이익 폭발 구간을 압축 투자로 포착하는 고성장 모멘텀",
    "v11":      "V7 이익가속(Earnings Acceleration): OP YoY>30% 3분기 연속 가속 + 이익성장률>매출성장률(마진 레버리지) + MA60>MA120 추세전환 + 52W 50~88% + 기관OR외인 유입. avg5=+6.5%, 최신+60.7%. 회복장·최근 구간 특히 강세.",
    "v8":       "★V9 수출 변곡점 선행 전략: 수출 YoY 음수→양수 전환(진짜 변곡점) 포착 + MA60+20% 상단 차단(선반영 방지). 데이터기반 매도: 수출역전청산(YoY<-3%) + 수출전환실패청산. 2026-07-16 고정슬롯→현금원장 전환(execution_strict) 재등록: avg6=+16.1%, 2/6기간 양수 [상승+76.3/하락0/회복-3.6/AI-9.0/최근-0.3/최신+33.1]. 2026-07-27 코드 재확인 결과 애초에 시총 기반 유니버스 필터 자체가 없음(종목선정은 수출데이터 보유여부만 기준) — market_cap_mode를 \"current\"(부정확한 라벨)에서 \"not_applicable\"로 정정, 수치·로직 변경 없음.",
    "v12":      "V10 섹터 초기사이클 전략: early-cycle(alpha 4~20%) + 1개월 alpha 양전 동시 확인으로 정점 진입 차단. 데이터기반 매도: 섹터 1M 모멘텀 역전(-2% 이하) + MA20 붕괴 → 섹터사이클 소멸 청산. 2026-07-17 as-of 시총(point_in_time_approx) 재등록: avg6=+3.1%, 3/6기간 양수 [상승+20.6/하락0/회복-17.1/AI-3.8/최근+4.8/최신+14.4] — 구 +17.9%는 현재시총 룩어헤드 포함 수치, as-of 전환으로 정직화. 단독운용 비권장.",
    "regime_adaptive": "레짐 자동 감지 → BULL(KOSPI>MA120)이면 V1 MA추세, BEAR이면 V7 이익가속으로 자동 전환. 2026-07-16 고정슬롯→현금원장+엄격체결(D+1시가) 전환(execution_strict) 재등록: avg6=+1.8%, 3/6기간 양수 [상승+25.9/하락-5.2/회복+1.6/AI-12.4/최근-12.2/최신+12.9] — 레짐 전환 신호 자체의 유효성이 약함이 실증됨. 2026-07-27 코드 재확인 결과 유니버스·신호함수(_is_buy_v1/_is_buy_v11) 어디에도 시총 파라미터가 없음(과거 \"V1이 1000억+ 확인\" 주석은 잘못된 것) — market_cap_mode를 \"current\"에서 \"not_applicable\"로 정정, 수치·로직 변경 없음.",
    "composite":       "★V11 복합스코어링: 흑자전환(35)+추세(25)+거래량(15)+수급AND(20)+가치(5)=100점. 65점 이상+절대모멘텀 필터. 2026-07-16 퍼센트모델(수량무관)→실제 qty 기반 현금원장+엄격체결 전환(execution_strict). 2026-07-27 코드 재확인 결과 시총 기반 유니버스 필터 자체가 없음 — market_cap_mode를 \"current\"에서 \"not_applicable\"로 정정. 2026-08-30 신규: 특허·기술이전·R&D(dart_rd_patent_signals)/자사주매입·소각(treasury_buyback)/희석위험(dilution_events, risk_event_bucket 필터링)을 _score_stock()에 opt-in 이벤트 보정으로 결합(use_event_bonus, 6기간 walk-forward 검증 후 기본값 True 채택) — avg6 +9.47%→+10.95%(4/6기간 개선: 상승+14.5pp/하락+7.0pp/회복+2.4pp/23.11~24.12+4.7pp, 단 24.6~25.5 -13.5pp·25.6~26.3 -6.2pp 소폭 악화, 정직하게 트레이드오프 존재). 재등록 결과 avg6=+10.95%, 4/6기간 양수 [상승+98.3/하락-32.1/회복+4.7/AI-24.7/최근+2.1/최신+17.3]. point_in_time_verified.",
    "high_profit_compound": "⚠️[legacy — 실전용 아님, 재검증 필요] V13 고수익 집중: 임원매수(180일)+성장섹터+MA20위반등확인+거래량1.3배+계약or수주잔고 + KOSPI MA60 필터. 하락장 신규 진입을 차단해 기존 하락장 손실을 완화. 고정 10슬롯(same_close, current 시총, fixed_slot) 기준 avg6=+17.3%, 상승장+70.8%/회복장+37.7%/최신+22.5% — **D+1 체결·현금원장·as-of 시총 미적용**이라 다른 전략들과 직접 비교 불가. 2026-07-23 Codex 점검으로 전략센터 기본 API(`include_legacy=false`)에서 제외됨, `include_legacy=true`로만 조회 가능. 실전 비교용으로 쓰려면 6기간 suite를 execution_strict 이상으로 재구축 필요.",
    "sector_focus":         "★V-SECTOR 주도섹터 집중: 섹터 BUY 신호(점수≥55) 발생 시 섹터 내 3개월 RS 리더+기관집중도 우수 종목 TOP3를 매수. KOSPI 절대 레벨로 차단하지 않고 강한 섹터 장세를 우선 반영. 월 1회 리밸런싱, 최소 44일 섹터 보유 후 EXIT. 2026-07-21 추적손절 -20%→-30% 기본값 변경(연속운용 227.89%→245.02%, 승률46.3→46.7%, 조기청산 감소) 재등록 avg6=+30.0% 5/6기간 양수: 상승+61.1/하락-7.1/회복+21.9/AI+40.5/최근+36.9/최신+26.6. execution_strict.",
    "recovery":             "★V-RECOVERY 낙폭과대 반등: MA60 -20~-65% 낙폭 + 52주 저점 40%이내 + 거래량반등×2.0 + 3일중 2일 상승. 랭킹 보너스: 직전분기 첫 흑자전환 +20pt + 기관·외인 5일 순매수 양수 +20pt. Trail-20%/25%, 손절-12%. 2026-07-15 point_in_time_approx 재등록 avg6=+23.0% 5/6기간 양수: 상승+13.7/하락+42.9/회복+25.4/AI-3.8/최근+56.3/최신+3.6 (체결은 여전히 당일종가)",
    "golden_cross":         "V12 골든크로스: MA20이 MA60을 15일 내 상향돌파(골든크로스) + 거래량 1.2배 + RS6M 랭킹 + 40일 수익률 100%초과 과열종목 제외(avoid_overheat). 손절-12%, as-of 시총 필터(min_mktcap 4000억+). 2026-08-10 min_mktcap 2000→4000 변경 — 384건 교차전략 텐버거 캡처분석에서 시총이 유일하게 단조판별력 보유(학습/검증 홀드아웃 양쪽 방향일치+검증기 강화) 확인, 실전략 스윕(2000~10000) 후 6기간 공정비교 avg6=25.28%→35.48%(4/6기간 양수, 거의 전구간 개선·하락장 방어도 개선): 상승+96.3/하락-31.0/회복-2.8/AI+8.1/최근+28.8/최신+113.6 — 유니버스 룩어헤드 제거 후에도 상승장 편중은 여전, 단독운용 비권장.",
    "deep_recovery":        "V-DEEP 깊은낙폭집중: 실증 최강구간(-25~-45% MA60) 집중. MA60 -25~-60% + 거래량1.5배 + 최근5일중3일상승. Trail-22%/30%, 손절-13%, TP100%. 2026-07-17 as-of 시총(point_in_time_approx) 재등록: avg6=+0.3%, 2/6기간 양수 [상승+81.3/하락-17.8/회복+7.2/AI-36.5/최근-30.4/최신-2.0] — 구 +6.9%는 현재시총 룩어헤드 포함 수치. 상승장 편중 심함, 단독운용 비권장.",
    "low_base_breakout":    "V-LOWBASE 저점기반돌파: V-GC 골든크로스 직전/초기 진입 — MA60 -18%~+10% + 52주저점+65%이내 + MA20수렴(-8%이내) + 5일중3일상승. Trail-15%/20%/25%, 손절-10%, 만료270일. 2026-07-17 as-of 시총(point_in_time_approx) 재등록: avg6=+0.3%, 2/6기간 양수 [상승+58.0/하락-35.2/회복-1.5/AI-13.3/최근-6.5/최신+0.6] — 구 +6.7%는 현재시총 룩어헤드 포함 수치. 단독운용 비권장.",
    "turnaround":           "★V-TURNAROUND 흑자전환 특화: BQ 실증 흑자전환 종목 평균 6.14x vs 우량성장주 3.48x(1.77배 우위). 52주 고점 -30~-65% 낙폭과대 + 직전 분기 첫 흑자전환(이전 1~3분기 적자 존재) + TTM NI 합산 양수(임시 반등 제외) + PBR≤1.5 저평가. Trail-25%/30%, 손절-13%, 만료300일. 2026-07-17 as-of 시총(point_in_time_approx) 재등록: avg6=+6.9%, 3/6기간 양수 [상승+49.2/하락-22.5/회복+31.6/AI-16.4/최근-15.6/최신+15.1] — as-of 전환에도 avg 유지(current +5.7% 대비 소폭 개선).",
    "extreme_dd_volume": "★V-EXTREME 초낙폭+거래량 (2026-07-18 신규): 텐버거 엔진 S등급 시그널(52주고점 -70%↓ + 당일거래량 20일평균 1.5배+ + 거래대금 5억+, walk-forward 검증 12개월 3배율 22.6% vs 기준율 7%)의 최초 실전 백테스트化. 1차(거래량급증 즉시매수)는 패닉투매 매수로 -82.8% 참패 → 차트 컨플루언스(일봉MA+주봉구조+캔들 2/3 합의) 바닥확인 게이트 추가로 +20.6%/승률 43% 전환. Trail-35%/40%(V13 철학: 익절상한 없음), 손절-15%, as-of 시총. 연속운용(2020-03~2026-03) +20.6% — 아직 중하위권, 파라미터 개선 여지 있음.",
    "se_momentum": "★V-SE 주도섹터 바스켓 (2026-07-18 신규, 2026-07-22 실적가속게이트+실측손절 채택): 스탁이지 모멘텀 전략(실보유 BUY 94~100% 재현 검증된 로직)의 최초 백테스트化. SE섹터(middle) 전체멤버 평균 ret20 랭킹 상위 2개 주도섹터 → 섹터 내 MA5>MA20 + 주가≥MA20×0.97 + 기관or외인 5일 순매수 + 매출/영업이익YoY 가속 또는 흑자전환(require_earnings_accel) → 시총상위 5종목 바스켓. 매도: 섹터 히스테리시스 편출(상위8위 밖 or 모멘텀 음전) + MA5<MA20×0.96 깊은 이탈 + Trail-20% + **손절-8%**(2026-07-22: 사용자 제공 로그인세션으로 실제 스탁이지 편출내역 129건 직접 확인 결과 손실거래의 60%가 -8.0~-8.14%에 정확히 클러스터링 — 하드손절선 확정, 승자는 15~45일 보유하며 최대+244%까지 무제한 보유하는 '손절은 짧게 승자는 길게' 패턴 실증). 6기간 avg6 +22.4%→+27.0%(4/6기간, stop -0.10/-0.12보다 우수). ⚠️섹터분류는 현재시점 적용(V-SECTOR 동일 한계). 매도조건 9종 스윕 이력은 signal_experiment_ledger 참조.",
    "megatrend": "★V-MEGATREND 구조테마추종 (2026-07-20 신규→2026-07-21 다중섹터 확장→2026-07-22 저가주 필터): 사용자 지시로 반도체(151종목) 한정에서 전력기기·조선·화장품ODM도 포착하도록 확장. 유니버스=IT+산업재+필수소비재 섹터(1,040종목, 시총300억+), 6개월수익률≥100% & 52주고점대비-15%이내 & **같은 섹터 내 동시충족 3종목+**(sector_confirm_min) & **진입가 5만원+**(min_price — 실측진단: 24.6~25.5구간 저가 투기성 급등주가 sector_large 필터를 통해 대량 혼입돼 진짜 대세종목(한화에어로스페이스+136%·HD현대중공업+48%·현대로템+29% 등 실제 매수·대부분 수익)을 압도, 진입가 10만원+ 승률45%/+15.2% vs 10만원미만 승률12~27%/-8~-16% 확인). 최대 30종목 분산매수, 손절-20%(하드)+트레일링스탑-30%. 6기간 avg6=+6.3%(2/6, 저가주필터 적용 전과 거의 동일)이나 **하락장 리스크 대폭 축소(-35.2%→-7.1%)** — 완전한 해결책이 아니라 다운사이드 방어 트레이드오프(상승장 캡처도 소폭 감소). 24.6~25.5 구간도 -24.4%→-13.0%로 개선되었으나 잔존 마이너스(저가 투기주 제거 후에도 일부 소형 종목 휩쏘 남음). 다중섹터+섹터확인 적용 결과 연속운용(2020-03~2026-07-20) +125.1%(반도체단독+123.2%와 대등), 실제 HD현대일렉트릭+226.7%·코스메카코리아+263.8%·SK하이닉스+206.5%·한화오션+129.5%·효성중공업+150.8% 등 타업종 메가랠리 포착 확인. walk-forward 검증(n=232건): 개별승률 19~32%로 낮으나 -20%손절+트레일보유 가정 시 건당기대값 플러스(fat-tail) — 반드시 30종목 분산 전제. 손절 후 재매수 흔함(56/102종목 2회+거래, 재도전 성공사례 다수). 2026-07-27 as-of 시총 리트로핏(min_mktcap_억 컷오프가 현재시총 정적필터였음 — as-of로 전환) 재등록(point_in_time_approx): avg6=+3.84%, 2/6기간 양수 [상승+14.31/하락-8.01/회복-4.49/AI-6.84/최근-13.03/최신+41.11] — 이전 current-mode 수치보다 하락, 정직화.",
    "earnings_conviction": "★V-EARNINGS 실적가속 집중배분 (2026-07-22 신규→2026-07-22 3차 절대증가액 전환): 사용자 지적 2건 — ①\"삼성전자/SK하이닉스가 역대급 이익을 내는데 왜 편입이 늦나, 비중을 늘려야 하지 않나\" ②\"산술평균이 아니라 수익 극대화가 목표. 점수높고 확실한 종목에 더 집중해야. 매출 급증도 매수신호로 인정해야\". **가격조건 완전 제거**, ①분기 영업이익 YoY 가속(매출YoY 동반양수, 절대영업이익 500억+) 또는 ②매출 YoY 단독 급증(+40%+, 절대매출 500억+, 이익요건 없음 — 적자성장기업 포착) 중 하나로 진입. **핵심 버그 2회 수정**: 1차(%기준 티어+배치정규화)는 초소형 %폭발(경동도시가스 매출 +379,440%)이 절대영업이익 하한을 2000억으로 올려도 SK하이닉스(+157%, 4.5조원 증가)를 랭킹에서 계속 밀어냄 → **랭킹·가중치를 %가 아니라 절대 증가액(억원)으로 전환**(진입자격만 %로 확인, 이후 5,000억원 증가당 가중치 1배씩·최대 3배 상한) — SK하이닉스가 54개 후보 중 정확히 1위(4조5,505억원 증가)로 확인. 포지션수 20→10 축소(균등화 아닌 소수 집중), 배치 내 정규화(평균=1.0) 폐지 — 점수 자체의 절대 크기를 그대로 반영. 매도: 손절-20%/추적손절-30%(이익권)/실적악화청산(YoY 역성장 전환 시)/만료252일. **6기간 KOSPI/KOSDAQ 대비 실측(avg6=+22.61%, 4/6기간 양수)**: 상승장+17.78%(KOSPI+42.88%, 미달) / 하락장-5.39%(KOSPI-20.90%, 방어) / **회복장-21.27%(KOSPI-2.45%, 집중의 하방리스크 — 대폭 미달)** / AI랠리+5.90%(KOSPI+4.25%, 상회) / **최근+50.07%(KOSPI+2.32%, 압도)** / **최신+88.60%(KOSPI+87.29%, 거의 정확히 일치 — 사용자가 지목한 SK하이닉스/삼성전자 사례의 목표기간에서 지수와 사실상 동률 달성)**. SK하이닉스 실제 캡처+270.5%(원본+296.2%의 91%). **정직한 트레이드오프**: 소수 종목 집중배분이라 최고 강세장·회복 구간에서 극적 개선을 냈지만(최근/최신), 회복장처럼 소수 베팅이 어긋나는 구간에서는 이전 버전(균등정규화, avg6 +18.44%, 회복장 -8.27%)보다 변동성이 커지고 손실도 커짐 — '수익 극대화'와 '안정성'은 명백한 트레이드오프이며 이번 재설계는 전자를 우선한 것. 2026-07-27 as-of 시총 리트로핏(min_mktcap_억 컷오프가 현재시총 정적필터였음) 재등록(point_in_time_approx): avg6=+23.29%, 4/6기간 양수 [상승+21.85/하락-5.39/회복-21.27/AI+5.9/최근+50.07/최신+88.6] — 메가캡(SK하이닉스 등)은 어느 시점에도 시총 컷오프를 여유있게 통과해 as-of 전환에도 수치가 거의 유지·소폭 개선됨.",
    "moonshot_turnaround": "★V-MOONSHOT 턴어라운드 종합스코어 대박발굴 (2026-07-23 신규→2026-07-23 2차 발굴개수 우선 재조정): 사용자 지시 \"기존 전략을 그대로 두더라도 핵심종목 1000%씩 오르는 종목의 발굴에 집중하는것도 괜찮다\" + \"1000% 상승하는 종목을 찾는건 정말 중요, 꼭 1000% 안먹어도 중간에 타서 어깨에 나와도 상관없다\" — V-EARNINGS(메가캡 집중배분)와 반대 극단으로, walk-forward 검증된 turnaround-watch(2026-07-19) comprehensive_score(재도전턴어라운드+매출YoY성장+감가상각주도 이익의질, 3신호 0~3점, 검증 lift 2점=1.13~1.23x/3점=1.38x·검증1.63x)를 최초로 실전 백테스트化. TTM 적자 모집단(V-EARNINGS와 겹치지 않는 별개 population) 중 comprehensive_score≥2 + 희석위험(CB/BW/EB 트레일링365일)≤3건인 종목을 최대 **30종목 균등분산**(집중 아님 — 어느 종목이 1000%될지 사전에 알 수 없어 광범위 분산으로 fat-tail 노림) 매수. 손절-35%/추적손절-35%(변동성 큰 모집단 감안 확대, '어깨'에서 청산되는 게 정상 설계) + 만료 500거래일(~2년, 턴어라운드가 무르익는 데 필요한 긴 호흡). **2026-07-23 2차: max_positions 20→30 상향** — 사용자 지시대로 '총수익 극대화'보다 '발굴 개수 극대화' 우선. 실측(연속운용 2020-03~2026-03): 100%+ 대박종목 21건→**28건**으로 증가(예스24+336.7%·인화정공+254.6%·코스맥스+214.8% 등 추가 포착), 1000%+ 2건은 그대로(RF머트리얼즈+1099.3%·데브시스터즈+1023.4%), 6기간 avg6도 18.94%→**22.39%(3/6→4/6기간 양수)**로 개선 — 단 연속운용 총수익은 190.31%→160.97%로 하락(자본이 더 얇게 분산돼 개별 대박 기여도 희석, 정직하게 밝힘). 6기간[상승+101.44/하락-16.67/회복+14.61/AI+1.91/최근-19.95/최신+53.01]. 정직한 한계: 승률 27~53%로 낮고, 특정 구간(최근)은 여전히 큰 손실 — 반드시 30종목 분산 전제, 집중 매수 금지. 2026-07-27 as-of 시총 리트로핏(min_mktcap_억 컷오프가 현재시총 정적필터였음 — 부실기업이 턴어라운드로 몸집이 커진 뒤에야 소급 편입되는 룩어헤드) 재등록(point_in_time_approx): avg6=+7.60%, 3/6기간 양수 [상승+71.47/하락-19.65/회복+4.56/AI-9.79/최근-17.15/최신+16.16] — 4/6→3/6로 하락, 소형주 비중이 큰 모집단 특성상 as-of 전환의 영향을 크게 받음(정직화).",
    "contract_momentum": "★V-CONTRACT 해외수주 모멘텀 (2026-08-09 신규): 2026-08-09 사용자 지시로 3년내 10배 종목 251개를 상승계기별 카테고리화한 결과 \"대형수주\"(36개, 14.3%) 공시일의 42%(15/36)가 저점 이전 발생 — 선행지표로 활용 가능함을 확인. 원신호는 2026-07-23~24 Codex가 독립 스크립트로 발굴·홀드아웃 검증(학습기<2024-01-01 그리드서치 최적파라미터를 얼려서 검증기 2024-01-01+에 적용 → +154.3%/145건/승률24.8%/PF2.51/MDD-27.9%, 붕괴 없음 확인)했으나 정식 backtest.py 함수로 이식되지 않아 실전(가상매매/콤보)에 연결된 적이 없었음. 매수: dart_contracts 중 \"단일판매/공급계약\"류 공시(해지·거래정지·유동성공급·[첨부추가] 제외) + 계약금액/매출비율≥10% + 해외수주 한정 + 52주 내 상대위치≤1.0 + 종가≥MA20 + 20일평균거래대금≥20억. 공시 다음거래일 시가매수, 동일일 복수신호는 비율·AI점수 내림차순 우선(최대 10포지션, 종목당 1,000만원). 매도: 손절-8%/추적손절-25%(이익10%+발동)/만기240거래일. 이식버전 자체 재검증(원본과 동일분할 학습<2024-01-01/검증≥2024-01-01): 학습+45.8%(47건,승률36%)/검증+163.5%(99건,승률29%) — 절대치는 원본(고정슬롯 vs 원본 dynamic_tickets 차이 추정)과 다르나 방향·안정성 일치. 6기간 avg6=+25.27%(5/6기간 양수) [상승0.0/하락-6.23/회복+31.73/AI+33.12/최근+69.91/최신+23.11] — 상승장(2020~2021)은 dart_contracts 데이터 자체가 희소해 0건. 시총필터 없음(원본 설계 그대로, market_cap_mode=not_applicable). execution_strict.",
}

# 전략별 진입/매도 조건 (상세 설명 테이블용)
STRATEGY_CONDITIONS = {
    "v_trend":  {
        "진입조건": "MA20>MA60>MA120 정배열 + RSI 42~72 + 5일 거래량≥ 20일 거래량×1.3",
        "매도조건": "MA20 하향이탈 또는 RSI>80 또는 최대보유 120일",
        "손절선": "-8%",
        "추가필터": "거래량 최소 1억원/일 이상",
        "적합장세": "상승장·추세장 ★AI랠리",
        "주의사항": "횡보·하락장 손실 — 시장 상승 추세 확인 필수",
    },
    "v1_value": {
        "진입조건": "Graham DCF 25%+ 할인 OR (PBR<0.7 AND PER<10) + 최근 2분기 영업이익 흑자",
        "매도조건": "내재가치 도달 또는 최대보유 200일",
        "손절선": "-10%",
        "추가필터": "시총 300억+ / 부채비율 200% 미만",
        "적합장세": "횡보·하락장 모두 — 가격 저평가 기반",
        "주의사항": "성장주 제외 편향 — IT·바이오 대형주 미포함",
    },
    "v2":       {
        "진입조건": "영업이익률≥8% AND ROE≥10% AND ROA≥5% 중 2개 이상 충족 + 영업흑자 + MA20>MA60 + 기관+외인5일>0",
        "매도조건": "★데이터기반: 손실-7%이하 + MA20<MA60×0.96 + 기관&외인 15일 동반 순매도 → 진입조건 역전 조기청산 / 기존: 손절-10%, 추익절+20%, 추적손절-10%, MA60붕괴, 240일횡보안전망",
        "손절선": "-10%",
        "추가필터": "최근 4분기 매출 성장 or 안정 / KOSPI MA60 위 시장필터",
        "적합장세": "상승장 ★ (avg5=+11.8%, 상승장+49.5%)",
        "주의사항": "이익권 포지션은 trail stop·MA60붕괴가 처리 — 데이터기반매도는 손실-7%이하 확인된 손실고착 포지션만 조기청산",
    },
    "v5":       {
        "진입조건": "기관 5일 누적 순매수 > 0 AND 외국인 5일 누적 순매수 > 0 + MA20>MA60>MA120 삼중정배열 + 영업흑자",
        "매도조건": "★데이터기반: 손실-7%이하 + 기관&외인 20일 동반 순매도(AND) + MA20<MA60×0.97 → 수급모멘텀 완전소멸 / 기존: 손절-8%, 추익절+20%, 추적손절-10%, MA60붕괴, 240일횡보안전망",
        "손절선": "-8%",
        "추가필터": "영업이익 흑자 / KOSPI MA60 위 시장필터",
        "적합장세": "상승장 ★ (avg5=+20.4%, 상승장+90.4%, 하락장+2.1%)",
        "주의사항": "이익권 포지션은 trail stop·MA60붕괴가 처리 — 데이터기반매도는 수급·MA 조건이 동시에 역전된 20일 확인 후만 발동 (5일 수급 노이즈에 반응하지 않음)",
    },
    "v4":       {
        "진입조건": "Minervini RS≥70 + (PBR<2 AND PER<30) + 기관·외국인 10일 순매수 양수",
        "매도조건": "MA60 이탈 또는 추적손절(고점대비 -10%) 또는 최대보유 200일",
        "손절선": "-8%",
        "추가필터": "KOSPI MA60 위 (하락장 차단) / 시총 300억~5조",
        "적합장세": "상승장·회복장 ★전 사이클 최우수",
        "주의사항": "진입 종목 수 제한 필요 — 과집중 방지",
    },
    "v10":      {
        "진입조건": "OP YoY≥80% + Rev YoY≥30% 직전 2분기 연속 확인 + KOSPI MA60 위",
        "매도조건": "성장세 둔화(YoY 50% 이하) 또는 최대보유 120일",
        "손절선": "-8%",
        "추가필터": "시총 1조 미만 / 영업이익률 5% 이상",
        "적합장세": "회복장·상승장 초입 — 이익 반등 구간",
        "주의사항": "2분기 연속 확인 후 진입 → 일부 상승분 이미 반영",
    },
    "v11":      {
        "진입조건": "영업이익 YoY>30%(전년동기비) + 직전 분기도 흑자+YoY양성 + 52W범위 50~88% + MA20>MA60 + 기관OR외인 5일 순매수",
        "매도조건": "추적손절 -10% / 익절 +35% / 최대보유 180일",
        "손절선": "-10%",
        "추가필터": "시총 300억+ / 시장 필터(KOSPI<MA60 시 신규 진입 차단)",
        "적합장세": "하락장 α+11.5% / AI랠리 α+15.7% — 이익질 높은 성장주 국면",
        "주의사항": "분기 공시 지연(45일)으로 광역 상승장·회복장에서 선반영 후 진입 → 부진. avg5=-0.5%(재검증 2026-06-26). V-GC·V8·V11복합 병행 권장",
    },
    "v8":       {
        "진입조건": "수출 YoY +2% 이상(최소 양성) + 이전 2개월 중 부진(≤2%)/음수 구간 존재(진짜 변곡점) 또는 YoY 15%pt 급가속 + MA60 위 + MA60+20% 미만(선반영 구간 차단)",
        "매도조건": "★데이터기반: 수출YoY<-3% → 수출역전청산 / 최근YoY<+2%+전월<0% → 수출전환실패청산 / 240일 장기횡보안전망 / MA200붕괴",
        "손절선": "-10%",
        "추가필터": "수출 관련 종목(HS코드 매핑) + 시총 200억+ + KOSPI 한정",
        "적합장세": "수출 사이클 변곡점 초입 — 부진→반등 전환 구간. 상승장+52.2%, 회복장+16.5%",
        "주의사항": "수출 데이터 월 1회 업데이트(2개월 후행). 이전 전략 대비: 지속성장 케이스(C) 제거로 후행 진입 방지. avg5=+8.1%(개선전 -2.9%)",
    },
    "v12":      {
        "진입조건": "섹터 3개월 alpha 4~20%(early-cycle, 과열/저평가 동시 차단) + 섹터 1개월 alpha 양수(최근 모멘텀 확인) + 종목 3개월 수익률>섹터 평균 + MA120 위",
        "매도조건": "★데이터기반: 손실-7%이하 + 섹터1M alpha<-2%(사이클역전) + MA20붕괴 → 섹터모멘텀소멸청산 / Trail-20% / 익절+20% / 손절-10%",
        "손절선": "-10%",
        "추가필터": "기관·외인 20일 누적 순매수 양수 / 시총 500억+",
        "적합장세": "섹터 초기사이클(alpha 4~20%) — 정점 진입 차단으로 최근 -47.7%→-17.1%로 개선",
        "주의사항": "alpha≥20% 섹터 진입 차단(과열). 섹터 1M alpha 모니터링이 핵심 이탈 신호. avg5=+1.6%(개선전 -6.5%)",
    },
    "regime_adaptive": {
        "진입조건": "BULL(KOSPI>MA120): V1 MA정배열+거래량 / BEAR(KOSPI<MA120): V7 흑자전환 2분기 연속",
        "매도조건": "BULL매수: 익절+20%·손절-8%·MA60 붕괴 / BEAR매수: 익절+30%·손절-10%·추적손절",
        "손절선": "BULL -8% / BEAR -10%",
        "추가필터": "레짐 전환은 매일 KOSPI MA120 기준으로 자동 판단 / BULL 시총 1000억+, BEAR 300억+",
        "적합장세": "★전 사이클 — 상승장·하락장·회복장 모두 대응",
        "주의사항": "레짐 전환 시점 지연 가능 (MA120 후행성). 복합 전략이므로 단일 전략보다 거래 복잡도 높음.",
    },
    "composite": {
        "진입조건": "100점+이벤트보정 스코어 ≥ 65점 + 절대모멘텀(3개월-15%·1개월-5% 필터): [흑자전환 35점: OP>30억+직전분기+1년전적자] + [추세 25점: MA20>MA60+MA120 위] + [거래량 15점: 10일평균 1.5~2배+] + [기관+외국인 동반순매수 20점 / 한쪽만 5점] + [가치 5점: Graham OR PBR<1.2] + [2026-08-30 이벤트보정: 특허·기술이전·R&D +최대3점 / 자사주매입·소각 +최대3점 / 희석위험(CB/BW/EB/RIGHTS) -최대6점]",
        "매도조건": "65~69점 → 익절+20% / 70~79점 → 익절+30% / 80점+ → 익절+40% | MA60 붕괴 즉시 매도 | 손절-10%",
        "손절선": "-10%",
        "추가필터": "3개월 수익률 -15% 이상 하락주 진입 금지 / 1개월 -5% 이상 하락주 진입 금지 / 재무데이터 없는 종목 자동 제외",
        "적합장세": "실현qty기반 현금원장+엄격체결 6기간 avg6=+10.95%(4/6기간 양수): 상승+98.3/하락-32.1/회복+4.7/AI-24.7/최근+2.1/최신+17.3 — 구 +70.9%는 실현손익 누산 방식의 낙관치였음(2026-07-16 정직화).",
        "주의사항": "수급 데이터 커버리지: 2020(18~26%)→2025(41~48%). 기관+외국인 동반매수 없으면 수급 5점만으로 65점 달성 어려움 — 자연스러운 신호 필터. 이벤트보정은 4/6기간 개선하나 24.6~25.5·25.6~26.3 2개 구간은 소폭 악화(트레이드오프 존재, signal_experiment_ledger 참조).",
    },
    "golden_cross": {
        "진입조건": "[A] MA20>MA60 단기 정배열 [B] 최근 15일 내 MA20이 MA60 상향돌파(골든크로스) [C] 5일 거래량>20일 평균×1.2 [D] RS6M(6개월 KOSPI 대비 상대강도) > -20 → RS6M 높은 순으로 진입",
        "매도조건": "★피크이지(Trail25%): 이익 5%+ 달성 후 고점대비 -25% 하락 시 매도 / ★대박홀드(Trail30%): 이익 50%+ 달성 시 고점대비 -30% (추세 길게 가져가기)",
        "손절선": "-12% (갭리스크 고려 기존보다 넓게 설정)",
        "추가필터": "★시총 4000억+ KOSPI/KOSDAQ 한정(2026-08-10: 2000→4000, 텐버거 캡처분석에서 시총 단조판별력 홀드아웃 통과 확인) / 전일대비 50%+ 급등락 종목 자동 제외(분할·합병 미조정 데이터 필터) / 최소 주가 1,000원 이상",
        "적합장세": "★4/6기간 양수(재검증 2026-08-10, 4000억+ 기준, 동일조건 공정비교): 상승장+96.3% / 하락장-31.0% / 회복장-2.8% / AI랠리+8.1% / 최근+28.8% / 최신+113.6%. avg6=+35.48%(기존 2000억+ 기준 avg6=25.28% 대비 개선)",
        "주의사항": "하락장(-2.0%)과 AI랠리(-1.8%)는 소폭 손실 가능 — 두 기간 합쳐도 -3.8%로 통제 가능. Trail25%가 급등장 조기청산 가능성 있으나 Trail30%(이익50%+) 구간이 보완. 재무 데이터 불필요(순수 기술적 전략). 2000억 미만 소형주는 약세/회복장 골든크로스 신뢰도 낮아 제외.",
    },
    "high_profit_compound": {
        "진입조건": "[A] 최근 180일 임원매수 공시 존재 [B] IT·의료·경기소비재·산업재 성장 섹터 [C] MA20 위(반등 확인) + 52주 저점 대비 20%+ 이탈 [D] 진입일 거래량 ≥ 20일평균×1.3 [E] 일거래대금 20억원+ [F] 계약공시(signal≥2) 또는 수주잔고 존재",
        "매도조건": "Trail-35%(이익10%+발동, 이익100%+시Trail-40%) 추적손절 / 손절-15% / 익절라인없음(Trail로만청산) / 만료400일",
        "손절선": "-15%",
        "추가필터": "KOSPI MA60 아래 신규 진입 차단 / 패닉장 방어 / 3중 교집합: 임원매수×섹터×촉매(계약+수주)",
        "적합장세": "MA60 필터 적용 재검증: 상승장+70.8% / 하락장-40.7% / 회복장+37.7% / AI랠리+10.6% / 최근+2.9% / 최신+22.5%. avg6=+17.3%",
        "주의사항": "⚠️legacy 등급 — 실전용 아님, 재검증 필요(same_close/current시총/fixed_slot). 임원매수 데이터 2024-06부터. 계약·수주잔고 커버리지 한정. 하락장 손실은 기존보다 완화됐지만 여전히 큼(-40.7%). 승률 낮음이 정상 — 소수 대박 종목이 다수 소액손실 상쇄 구조",
    },
    "recovery": {
        "진입조건": "[A] MA60 대비 -15~-65% 낙폭 [B] 52주 저점 대비 +80% 이내 [C] 거래량 20일평균×2.0배 [D] 최근 3일 중 2일 이상 상승 [E] 시총 200억+",
        "매도조건": "Trail-20%(이익5%+발동) / Trail-25%(이익50%+, 대박홀드) / 손절-12% / 익절+80% / 만료240일",
        "손절선": "-12%",
        "추가필터": "52주 저점 대비 위치 80% 이내 필터(중간반등 포착 — 저점 직후보다 반등 확인 후가 실증상 우수)",
        "적합장세": "낙폭과대 종목이 많이 생기는 하락장 말기, 회복장 초기에 특히 강세. 하락장+49.2%(전략최강)/상승장+47.6%/회복장+25.1%/AI+31.7%. avg5=+28.8%",
        "주의사항": "단기 급락 후 추가하락 리스크 존재. Trail과 손절 준수 필수. 시총 200억 이상만 필터링",
    },
    "deep_recovery": {
        "진입조건": "[A] MA60 대비 -25~-60% 깊은낙폭(실증 최강구간 집중) [B] 52주 저점 대비 +10~100% 반등(중간반등 포함) [C] 거래량 20일평균×1.5배 [D] 최근 5일 중 3일 이상 상승(더 강한 반등 확인) [E] 시총 200억+",
        "매도조건": "Trail-22%(이익5%+발동) / Trail-30%(이익50%+발동, 깊은낙폭일수록 대박 가능성 높아 더 넓게) / 손절-13% / 익절+100% / 만료300일",
        "손절선": "-13%",
        "추가필터": "MA60 -25% 미만(얕은 낙폭 제외) — 실증 최강구간만 집중. 저점+30~80% 중간반등에 +12점 보너스로 우선 진입",
        "적합장세": "심한 조정을 받은 종목이 많은 하락장 말기~회복장 초기. MA60 -35~-45% 구간에서 2배+ 달성률 22.4%(전체평균 3.4배). 매트릭스 실행 후 결과 업데이트 예정",
        "주의사항": "깊은 낙폭 = 추가 하락 위험도 높음. 손절 -13%는 넓은 편이므로 포지션 크기 조절 필수. 임계 낙폭(-60% 이상)은 제외(회생 불가능 기업 필터)",
    },
    "low_base_breakout": {
        "진입조건": "[A] MA60 대비 -18%~+10% 범위(저점 근방 + 막 돌파) [B] 52주 저점 대비 +0~65% 이내 [C] MA20이 MA60 대비 -8% 이내(골든크로스 수렴 중) [D] 최근 5일 중 3일 이상 상승 [E] 시총 300억+",
        "매도조건": "Trail-15%(이익5%+발동) / Trail-20%(이익30%+) / Trail-25%(이익80%+, 대박 홀드) / 손절-10% / 만료270일",
        "손절선": "-10%",
        "추가필터": "실증: 3배+종목 200건 분석(2022-2025) — 86%가 MA60 ±15%이내/상단, 65%가 52주저점+30%이내, 83%가+65%이내. V-GC 골든크로스 직전/초기 진입이 핵심. 점수: 저점근접도+수렴도 합산",
        "적합장세": "코스피 횡보~초기 상승장. 특히 개별주가 골든크로스 직전 축적 단계. V-GC가 크로스 후 진입이면 V-LOWBASE는 크로스 전 초기 진입",
        "주의사항": "MA60 위(+10% 초과) 이미 상승한 종목은 제외. V-GC와 상보적: 함께 사용 시 골든크로스 전후 모두 포착 가능. 백테스트 실행 후 결과 업데이트 예정",
    },
    "turnaround": {
        "진입조건": "[A] 52주 고점 대비 -30~-65% 낙폭(BQ: 70.5% 흑자전환 성공 종목이 이 구간에서 출발) [B] 최근 공시가능 분기 첫 흑자전환(직전 NI>0, 이전 1~3분기 중 NI<0 존재) [C] PBR≤1.5 (저평가 확인) [D] 거래량 20일평균×1.3배 [E] 시총 200억+",
        "매도조건": "Trail-25%(기본) / Trail-30%(이익50%+, 흑자전환 대박 종목 길게 홀드) / 손절-13%(최적화: -12% 너무 빡빡, -15% 하락장 지나치게 노출, -13% 회복장+6.5% 달성) / 만료300일(흑자전환→실적개선 사이클 충분히 기다림)",
        "손절선": "-13%",
        "추가필터": "KOSPI MA120×0.85 이하 패닉장 진입 차단 / 낙폭 점수: 고점대비 -55% → 55점(최대) / 적자기간 점수: 3분기 적자 → 30점(최대) / PBR 점수: PBR 0.5 → 15점(최대)",
        "적합장세": "하락장 말기~회복장 초기. BQ 실증: 흑자전환 종목 평균 6.14x vs 우량성장주 3.48x(1.77배). 낙폭과대+흑자전환 복합 신호가 텐버거 가장 강력한 선행 패턴",
        "주의사항": "재무 공시 지연(45일) 반영 — fin_disclosure_dates 사용. PBR 데이터 없으면 대체 필터 사용. 적자→흑자 전환은 일시적일 수 있어 손절 준수 필수",
    },
    "sector_focus": {
        "진입조건": "섹터 점수≥55: 외국인·기관 3개월 순매수(금액 없으면 수량×종가 환산) + 영업이익 YoY + 섹터 3개월 가격 모멘텀. BUY 섹터 중 상위 3개 섹터만 사용",
        "매도조건": "손절 -12% / 이익 후 고점대비 -20% 추적손절 / 익절 +50% / 섹터 점수 30 미만 시 최소 44일 보유 후 SECTOR_EXIT",
        "손절선": "-12%",
        "추가필터": "섹터 확정 후 종목은 3개월 RS 리더 60% + 기관집중도 40% 점수로 TOP3 선택. KOSPI 8000~9000 같은 절대 지수 레벨은 차단 조건으로 쓰지 않음",
        "적합장세": "반도체·방산·조선처럼 특정 섹터가 시장을 주도하는 구간. 지수 전체보다 섹터 내부 강도가 우선",
        "주의사항": "섹터별 편입 종목 매핑 오류가 성과를 크게 왜곡할 수 있어 조선 000720→329180 교정, 수급 금액 누락 구간은 수량 환산으로 보완",
    },
    "megatrend": {
        "진입조건": "IT+산업재+필수소비재 섹터(시총300억+) + 6개월 수익률≥100% + 52주 고점 대비 -15% 이내 + 같은 섹터 내 동시충족 3종목+ — 최대 30종목 분산 매수",
        "매도조건": "손절 -20%(하드) 또는 고점대비 추적손절(트레일링스탑) -30%(이익권일 때만)",
        "손절선": "-20%",
        "추가필터": "sector_confirm_min=3(섹터 내 동시충족 확인) — 순수 전체시장 확장은 노이즈 급등 유입으로 오히려 악화(-6.1%), 섹터 확인 필터로 해결(2026-07-21)",
        "적합장세": "반도체 AI설비투자·전력망 증설·조선 슈퍼사이클·화장품 ODM 수출붐처럼 여러 종목이 동시에 겪는 구조적 재평가 국면. 6기간 매트릭스보다 연속운용(+125.1%)이 실제 설계의도 반영",
        "주의사항": "개별종목 승률 19~32%로 낮음 — 반드시 30종목 분산 전제(집중 매수 금지). 손절 후 재매수 흔함 — '가짜 하락 후 재상승'을 여러 번 재시도하는 구조. 차트 컨플루언스는 승률은 높지만 조기매도로 총수익은 최하위 — 추세추종엔 역효과. 6기간 매트릭스는 구간경계에서 장기보유 트레이드를 강제청산해 과소평가됨.",
    },
    "earnings_conviction": {
        "진입조건": "가격조건 없음. ①분기 영업이익 YoY 가속≥20%(report_type=CFS) + 매출YoY 동시 양수 + 절대 영업이익 500억원+ 또는 ②매출 YoY 단독 급증≥40% + 절대매출 500억원+ (이익요건 없음, 시총300억+)",
        "매도조건": "손절 -20%(하드) 또는 추적손절 -30%(이익권) 또는 실적악화청산(최신분기 YoY 역성장 전환) 또는 만료 252일",
        "손절선": "-20%",
        "추가필터": "확신비중(conviction weight, 2026-07-22 3차 개정) — 랭킹·가중치는 %가 아니라 **절대 증가액(억원)** 기준(5,000억원 증가당 가중치 1배, 최대 3배 상한). %기준이면 초소형 매출 기저효과가 SK하이닉스급 진짜 대형가속을 항상 랭킹에서 밀어내는 근본결함이 있어(실측: 절대이익 하한 2000억 상향에도 재발) 전환. 최대 10종목 집중배분(20종목 균등화에서 축소), 배치 내 정규화 폐지 — 점수 자체의 절대크기 그대로 반영",
        "적합장세": "삼성전자/SK하이닉스처럼 이미 크고 안정적인 기업이 실적을 역대급으로 갱신하는 국면 — 가격이 아직 반응하기 전(2024Q1부터 갱신, 주가는 2025년 하반기 반응)부터 선제 포착. 최근/최신처럼 소수 메가캡이 시장을 주도하는 구간에서 특히 강함",
        "주의사항": "집중배분이라 소수 베팅이 어긋나는 구간(회복장 -21.27% vs KOSPI-2.45%)에서 변동성·손실이 커짐 — 안정성보다 절대수익 극대화를 우선한 설계. 최고 강세장(최신, KOSPI+87.29%)에서는 +88.60%로 지수와 거의 정확히 일치.",
    },
    "moonshot_turnaround": {
        "진입조건": "TTM 순이익≤0(적자 모집단) + 종합스코어(재도전턴어라운드/매출YoY성장/감가상각주도 이익의질 3신호 합산 0~3점)≥2 + 희석위험(CB/BW/EB 트레일링365일)≤3건 — 최대 30종목 균등분산(2026-07-23 2차: 20→30, 발굴개수 우선)",
        "매도조건": "손절 -35%(하드, 변동성 큰 모집단 고려 확대) 또는 추적손절 -35%(이익권, '어깨'에서 청산되는 게 정상 설계) 또는 만료 500거래일(~2년)",
        "손절선": "-35%",
        "추가필터": "turnaround-watch(2026-07-19) walk-forward 검증된 comprehensive_score 그대로 재사용 — 3신호 모두 충족 시 lift 1.38x(학습)/1.63x(검증). 희석위험 4건+는 1년내 -30%↓비율 42.6~49.3%로 급격 악화해 배제.",
        "적합장세": "구조 무관 — 개별 종목의 턴어라운드 사이클에 좌우되는 바텀업 전략. 상승장·최신처럼 소형주 회전이 활발한 구간에서 대박 포착 빈도가 높음",
        "주의사항": "승률 27~53%로 낮고 fat-tail 구조(소수 대박이 다수 소손실 상쇄) — 반드시 30종목 분산 전제, 집중 매수 절대 금지. 연속운용(2020-03~2026-03) +160.97%에서 RF머트리얼즈+1099%·데브시스터즈+1023% 등 실제 1000%+ 종목 2건 + 100%+ 대박종목 28건 포착 확인. max_positions 20→30 상향으로 발굴개수는 늘었으나(21→28건) 연속운용 총수익은 190.31%→160.97%로 하락(자본 분산 효과) — 사용자 지시대로 발굴개수를 우선한 트레이드오프.",
    },
    "earnings_supply_discovery": {
        "진입조건": "분기 영업이익 YoY 성장≥100%(공시일 as-of) + 20일 기관+외국인 순매수 합계≥10억원 — 최대 25종목 분산, as-of 시총 300억+",
        "매도조건": "손절 -35%(하드) 또는 추적손절 -35%(이익권) 또는 만료 500거래일(~2년) — V-MOONSHOT과 동일 설계",
        "손절선": "-35%",
        "추가필터": "2026-08-11 Codex PIT(생존편향 제거, 상장폐지 214종목 포함) walk-forward 재검증에서 발견된 신호(`supply_20d_억>=10 & op_growth>=100%`) — 10배 단독 정밀도(2.13%)는 목표(15%) 미달로 Codex는 '연구후보(research_candidate_only)'로 실전 승격 보류했고, ★2026-08-12 재검증 결과 **이 판단이 옳았음이 확인됨**(아래 주의사항 참조).",
        "적합장세": "해당없음 — 벤치마크 미달로 실전 부적합 판정",
        "주의사항": "⚠️★2026-08-12 벤치마크 대비 기각(실전 후보 제외): 연속운용(2020-03~2026-03) +123.01%(승률50.3%,145거래)가 **동기간 KOSPI 단순 buy&hold(+152.3%)에도 못 미침**. 코로나폭락기(20.3~4.15) 진입분이 원인인지 검증했으나 오히려 그 구간 건당평균손익(110.9만원)이 나머지(75.9만원)보다 높아 급락장 진입 문제가 아니었음 — 근본원인은 전략 구조(25종목분산+승률50%+넓은손절-35%) 자체가 6년 내내 지수를 못 이긴 것. 3배기준 lift(4.2x)가 통계적으로 유의해도 포트폴리오 알파로 전환되지 않은 사례로 기록(signal_experiment_ledger verdict=rejected_underperforms_benchmark). 매트릭스에는 투명성 목적으로 유지하되 실전 승격 대상 아님.",
    },
}

# 표준 기간 레이블 (start_date, end_date) → 표시명
# ★ 6개 사용자 지정 기간 (한국 시장 사이클 기준)
PERIOD_LABELS = {
    ("2020-03-01", "2021-11-30"): "20.3~21.11",  # 코로나 회복 (상승장)
    ("2021-12-01", "2022-10-31"): "21.12~22.10", # 고점→하락 (인플레·금리)
    ("2022-11-01", "2023-10-31"): "22.11~23.10", # 회복 (저점 반등)
    ("2023-11-01", "2024-12-31"): "23.11~24.12", # AI/반도체 랠리
    ("2024-06-01", "2025-05-31"): "24.6~25.5",   # 최근
    ("2025-06-01", "2026-03-31"): "25.6~26.3",   # 최신 ★ 신규
    # 구버전 호환 (기존 DB 데이터 보존용)
    ("2018-01-01", "2025-05-31"): "2018~2025",
    ("2020-01-01", "2021-12-31"): "2020~2021(구)",
    ("2022-01-01", "2023-06-30"): "2022~2023(구)",
    ("2023-07-01", "2025-05-31"): "2023~2025(구)",
}

# 핵심 전략 — V1~V9 + V10복합스코어 + VBR(52W돌파) (레짐적응형은 EX에만)
ALL_STRATEGIES = ["v_trend", "v1_value", "v2", "v5", "v4", "v10", "v11", "vbr", "v8", "v12", "composite", "golden_cross", "high_profit_compound", "sector_focus", "recovery", "deep_recovery", "low_base_breakout", "turnaround", "extreme_dd_volume", "se_momentum", "megatrend", "earnings_conviction", "moonshot_turnaround", "contract_momentum", "earnings_supply_discovery"]
ALL_STRATEGIES_EX = ALL_STRATEGIES + ["regime_adaptive"]

# 핵심 6개 기간 (사용자 지정 — 한국 시장 사이클)
CORE_PERIODS = ["20.3~21.11", "21.12~22.10", "22.11~23.10", "23.11~24.12", "24.6~25.5", "25.6~26.3"]

# 전략별 백테스트 실행 함수 매핑
STRATEGY_RUN_FUNCS = {
    "v_trend":        "run_backtest_v1",
    "v1_value":       "run_backtest_value",
    "v2":             "run_backtest_v2",
    "v5":             "run_backtest_v5",
    "v4":             "run_backtest",
    "v10":            "run_backtest_v10",
    "v11":            "run_backtest_v11",
    "vbr":            "run_backtest_hidden_rev",
    "v8":             "run_backtest_v8",
    "v12":            "run_backtest_v12",
    "regime_adaptive": "run_backtest_regime_adaptive",
    "composite":       "run_backtest_composite",
    "golden_cross": "run_backtest_golden_cross",
    "high_profit_compound": "run_backtest_high_profit_compound",
    "sector_focus": "run_backtest_sector",
    "recovery": "run_backtest_recovery",
    "deep_recovery": "run_backtest_deep_recovery",
    "low_base_breakout": "run_backtest_low_base_breakout",
    "turnaround": "run_backtest_turnaround",
    "extreme_dd_volume": "run_backtest_extreme_dd_volume",
    "se_momentum":       "run_backtest_se_momentum",
    "megatrend":         "run_backtest_megatrend",
    "earnings_conviction": "run_backtest_earnings_conviction",
    "moonshot_turnaround": "run_backtest_moonshot_turnaround",
    "contract_momentum":   "run_backtest_contract_momentum",
    "earnings_supply_discovery": "run_backtest_earnings_supply_discovery",
}

# 표준 6기간 정의 (run-all-matrix용)
STANDARD_PERIODS = [
    ("2020-03-01", "2021-11-30"),
    ("2021-12-01", "2022-10-31"),
    ("2022-11-01", "2023-10-31"),
    ("2023-11-01", "2024-12-31"),
    ("2024-06-01", "2025-05-31"),
    ("2025-06-01", "2026-03-31"),   # ★ 최신 기간 추가
]


@router.get("/strategies")
def get_strategy_catalog():
    """전략 카탈로그 — V1~V9 상세 설명 테이블용"""
    result = []
    for key in ALL_STRATEGIES:
        cond = STRATEGY_CONDITIONS.get(key, {})
        result.append({
            "key":       key,
            "label":     STRATEGY_LABELS.get(key, key),
            "desc":      STRATEGY_DESC.get(key, ""),
            "entry":     cond.get("진입조건", ""),
            "exit":      cond.get("매도조건", ""),
            "stop_loss": cond.get("손절선", ""),
            "filter":    cond.get("추가필터", ""),
            "market_fit":cond.get("적합장세", ""),
            "warning":   cond.get("주의사항", ""),
        })
    return {"strategies": result}


def _filter_liquid_rankings(rankings: list) -> list:
    """거래정지·극소형·메가캡·거래량없음·PBR누락·극단PBR·이미급등 종목을 현재 순위에서 제거"""
    result = []
    for r in rankings:
        close = r.get("close_price") or 0
        vol = r.get("vol_ratio_20d") or 0
        mcap = r.get("market_cap_억") or 0
        pbr = r.get("pbr")
        pbr_val = pbr if pbr is not None else 0
        ret60 = r.get("ret_60d")
        ret60_val = ret60 if ret60 is not None else 0
        if (
            close > 0 and
            vol > 0.01 and
            100 <= mcap <= 100_000 and   # 100억~10조 (메가캡 3배 사실상 불가)
            0 < pbr_val < 30 and         # PBR NULL/음수/극단값 제거
            ret60_val <= 2.0             # 60일 200% 이상 이미 급등 제거
        ):
            result.append(r)
    return result


def _quality_overlay_current_rankings(limit: int = 10) -> list:
    """Current auxiliary ranking from the validated quality overlay.

    Keep it separate from the core ML ranking. The 2026-07-26 sweep found this
    useful for Strategy Center Top10 auxiliary screening, not as a replacement
    for the main score.
    """
    conn = _db()
    conn.row_factory = _sl.Row
    try:
        latest = conn.execute("SELECT MAX(snapshot_date) FROM strategy_feature_snapshot").fetchone()[0]
        if not latest:
            return []
        rows = [dict(r) for r in conn.execute("""
            SELECT stock_code, stock_name, sector_large, close_price, market_cap_억,
                   model_score_12m, heuristic_score, pbr, per, dist_high_252,
                   ret_60d, vol_ratio_20d, supply_20d_억
            FROM strategy_feature_snapshot
            WHERE snapshot_date=?
              AND stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
        """, (latest,)).fetchall()]

        def latest_map(sql: str) -> dict:
            return {r["stock_code"]: dict(r) for r in conn.execute(sql).fetchall()}

        advance = latest_map("""
            WITH ranked AS (
                SELECT stock_code, signal_score, quality_flag,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY fiscal_year DESC, fiscal_quarter DESC) rn
                FROM contract_advance_signals
                WHERE length(stock_code)=6
            )
            SELECT stock_code,
                   CASE WHEN signal_score >= 4 AND quality_flag='ok' THEN 1 ELSE 0 END AS good
            FROM ranked WHERE rn=1
        """)
        inventory = latest_map("""
            WITH ranked AS (
                SELECT stock_code, signal_type, signal_score, risk_score,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY fiscal_year DESC, fiscal_quarter DESC) rn
                FROM inventory_sales_signals
                WHERE length(stock_code)=6
            )
            SELECT stock_code,
                   CASE WHEN signal_type IN ('build_up','digestion') AND signal_score >= 4 THEN 1 ELSE 0 END AS good,
                   CASE WHEN risk_score >= 4 THEN 1 ELSE 0 END AS risk
            FROM ranked WHERE rn=1
        """)
        cash = latest_map("""
            WITH ranked AS (
                SELECT stock_code, signal_type, signal_score, risk_score,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY fiscal_year DESC, fiscal_quarter DESC) rn
                FROM cash_conversion_signals
                WHERE length(stock_code)=6
            )
            SELECT stock_code,
                   CASE WHEN signal_type='cash_quality' AND signal_score >= 4 THEN 1 ELSE 0 END AS good,
                   CASE WHEN risk_score >= 4 THEN 1 ELSE 0 END AS risk
            FROM ranked WHERE rn=1
        """)
        # 2026-08-10: SQLite date(x,'-120 day') 2-인자 문법은 PostgreSQL에 없어 500 에러 유발
        # (전략 설명 탭 크래시 원인) — 파이썬에서 컷오프 날짜를 계산해 표준 문자열 비교로 교체.
        _cutoff_120d = (_datetime.strptime(str(latest)[:10], "%Y-%m-%d") - _timedelta(days=120)).strftime("%Y-%m-%d")
        orders = latest_map(f"""
            SELECT stock_code, 1 AS recent
            FROM order_contracts
            WHERE is_termination=0
              AND rcept_dt >= '{_cutoff_120d}'
              AND COALESCE(revenue_ratio_pct,0) >= 10
              AND length(stock_code)=6
            GROUP BY stock_code
        """)
    finally:
        conn.close()

    out = []
    for r in _filter_liquid_rankings(rows):
        sc = r.get("stock_code")
        advance_good = int((advance.get(sc) or {}).get("good") or 0)
        order_recent = int((orders.get(sc) or {}).get("recent") or 0)
        cash_good = int((cash.get(sc) or {}).get("good") or 0)
        inventory_good = int((inventory.get(sc) or {}).get("good") or 0)
        risk_count = int((inventory.get(sc) or {}).get("risk") or 0) + int((cash.get(sc) or {}).get("risk") or 0)
        if not (risk_count == 0 or advance_good or order_recent):
            continue
        model_score = float(r.get("model_score_12m") or 0)
        overlay_score = (
            model_score
            + 0.10 * advance_good
            + 0.06 * order_recent
            + 0.01 * cash_good
            - 0.02 * inventory_good
        )
        out.append({
            **r,
            "quality_overlay_score": round(overlay_score, 6),
            "advance_good": advance_good,
            "order_recent": order_recent,
            "cash_good": cash_good,
            "inventory_good": inventory_good,
            "quality_risk_count": risk_count,
        })
    out.sort(key=lambda r: r["quality_overlay_score"], reverse=True)
    return out[:limit]


@router.get("/hardening-plan")
def get_hardening_plan():
    """2026-08-25 신규: 데이터 정확도 강화검증 + 버그개선 + 사후검증 착수 계획.
    실험로드맵 페이지의 '정확도 강화계획' 탭에서 사용."""
    conn = connect_primary_db(timeout=20, row_factory=_sqlite3.Row)
    try:
        rows = conn.execute(
            """SELECT id, category, phase, title, description, priority, status,
                      owner, evidence, target_note, created_at, updated_at
               FROM system_hardening_plan
               ORDER BY category, priority, id"""
        ).fetchall()
    finally:
        conn.close()
    items = [dict(r) for r in rows]
    by_category: dict = {}
    for it in items:
        by_category.setdefault(it["category"], []).append(it)
    counts = {}
    for it in items:
        counts.setdefault(it["status"], 0)
        counts[it["status"]] += 1
    return {
        "items_by_category": by_category,
        "total": len(items),
        "status_counts": counts,
        "categories": list(by_category.keys()),
    }


@router.get("/strategy-research/summary")
def get_strategy_research_summary():
    payload = {}
    if RESEARCH_SUMMARY_PATH.exists():
        try:
            payload = json.loads(RESEARCH_SUMMARY_PATH.read_text())
        except Exception as e:
            payload = {"error": str(e)}

    quality_payload = None
    if QUALITY_FACTOR_VALIDATION_PATH.exists():
        try:
            quality_payload = json.loads(QUALITY_FACTOR_VALIDATION_PATH.read_text())
        except Exception as e:
            quality_payload = {"error": str(e)}
    overlay_sweep_payload = None
    if QUALITY_OVERLAY_SWEEP_PATH.exists():
        try:
            overlay_sweep_payload = json.loads(QUALITY_OVERLAY_SWEEP_PATH.read_text())
        except Exception as e:
            overlay_sweep_payload = {"error": str(e)}
    overlay_monthly_backtest_payload = None
    if QUALITY_OVERLAY_MONTHLY_BACKTEST_PATH.exists():
        try:
            overlay_monthly_backtest_payload = json.loads(QUALITY_OVERLAY_MONTHLY_BACKTEST_PATH.read_text())
        except Exception as e:
            overlay_monthly_backtest_payload = {"error": str(e)}

    # 기존 JSON에 유동성 필터 미적용 시 serve-time에 적용
    cr = payload.get("current_rankings", {})
    if cr and "liquidity_filtered" not in cr:
        ml = _filter_liquid_rankings(cr.get("ml_top20", []))
        hu = _filter_liquid_rankings(cr.get("heuristic_top20", []))
        payload = {
            **payload,
            "current_rankings": {
                **cr,
                "ml_top20": ml[:20],
                "heuristic_top20": hu[:20],
                "quality_overlay_top10": _quality_overlay_current_rankings(10),
            },
        }
    elif cr:
        payload = {
            **payload,
            "current_rankings": {
                **cr,
                "quality_overlay_top10": _quality_overlay_current_rankings(10),
            },
        }

    return _json_safe({
        **payload,
        "quality_factor_validation": quality_payload,
        "quality_overlay_sweep": overlay_sweep_payload,
        "quality_overlay_monthly_backtest": overlay_monthly_backtest_payload,
        "strategy_rankings": _strategy_rankings_for_regime(),
    })


@router.post("/strategy-research/rebuild")
async def rebuild_strategy_research():
    def _run():
        try:
            import sys
            if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in sys.path:
                sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
            from scripts.build_strategy_research_dataset import build_strategy_research_dataset
            build_strategy_research_dataset()
        except Exception:
            logger.exception("strategy research rebuild failed")
    threading.Thread(target=_run, daemon=True, name="StrategyResearchRebuild").start()
    return {"ok": True, "message": "strategy research rebuild started"}


@router.get("/matrix")
def get_backtest_matrix(include_legacy: bool = Query(False)):
    """선택 registry의 run_hash만 반환한다. 레거시는 감사 요청에서만 포함한다."""
    conn = _db()
    conn.row_factory = _sl.Row
    # Matrix builds suite verification from immutable manifests below. Re-running
    # recursive artifact verification here made the read endpoint scale poorly.
    registry_rows = selected_registry(
        DB_PATH, report_type="strategy_center", include_verification=False
    )
    selected_hashes = {row["run_hash"] for row in registry_rows}
    component_to_suite = {
        row["run_hash"]: row["suite_hash"] for row in conn.execute(
            """SELECT m.run_hash,m.suite_hash
               FROM backtest_run_set_members m
               JOIN selected_run_registry s ON s.run_hash=m.suite_hash
               WHERE s.report_type='strategy_center'"""
        )
    }
    status_rank = {
        "legacy": 0, "execution_strict": 1, "point_in_time_approx": 2,
        "point_in_time_verified": 3, "forward_validated": 4,
    }
    suite_verification = {}
    component_price_integrity = {
        row["run_hash"]: bool(row["passed"])
        for row in conn.execute(
            """SELECT run_hash,passed FROM run_verification_artifacts
               WHERE artifact_type='price_integrity'"""
        )
    }
    suite_rows = conn.execute(
        """SELECT rs.suite_hash,rs.strategy,rs.manifest_json
           FROM backtest_run_sets rs
           JOIN selected_run_registry sr ON sr.run_hash=rs.suite_hash
           WHERE sr.report_type='strategy_center'"""
    ).fetchall()
    for suite_hash, suite_strategy, manifest_json in suite_rows:
        manifest = json.loads(manifest_json or "{}")
        members = manifest.get("members") or {}
        component_rows = [
            {
                "period_label": label,
                "run_hash": item.get("run_hash"),
                "status": item.get("status") or "legacy",
            }
            for label, item in members.items()
        ]
        suite_status = min(
            (row["status"] for row in component_rows),
            key=lambda value: status_rank.get(value, 0),
            default="legacy",
        )
        failed_price_runs = [
            row["run_hash"] for row in component_rows
            if component_price_integrity.get(row["run_hash"]) is False
        ]
        if failed_price_runs:
            suite_status = "legacy"
        suite_verification[suite_hash] = {
            "run_hash": suite_hash,
            "suite_hash": suite_hash,
            "strategy": suite_strategy,
            "status": suite_status,
            "status_rank": status_rank.get(suite_status, 0),
            "is_suite": True,
            "gates": {
                "run_spec": bool(component_rows),
                "completed": bool(component_rows),
                "single_suite_identity": True,
                "all_components_same_or_higher_status": bool(component_rows),
            },
            "reasons": ["price_integrity"] if failed_price_runs else [],
            "failed_price_integrity_runs": failed_price_runs,
            "components": component_rows,
        }
    allowed_hashes = selected_hashes | set(component_to_suite)
    selection_params = ()
    if include_legacy:
        selection_clause = ""
    elif allowed_hashes:
        selection_clause = "AND s.run_hash IN ({})".format(
            ",".join("?" for _ in allowed_hashes)
        )
        selection_params = tuple(sorted(allowed_hashes))
    else:
        selection_clause = "AND 1=0"
    rows = conn.execute(
        "SELECT r.run_id, r.strategy, r.start_date, r.end_date, r.total_return_pct, "
        "r.ann_return_pct, r.max_drawdown_pct, r.win_rate, r.total_trades, "
        "r.summary_text, r.trades_json, r.status, s.engine_version, s.git_commit, "
        "s.signal_timing, s.execution_timing, s.market_cap_mode, s.universe_version, "
        "s.allocation_rule, s.fee_model, s.run_hash "
        "FROM backtest_runs r LEFT JOIN backtest_run_specs s ON s.run_id=r.run_id "
        f"WHERE r.status='done' {selection_clause} ORDER BY r.strategy, r.created_at ASC",
        selection_params,
    ).fetchall()

    matrix = {}
    # A suite hash is shared by all six period cells. Deriving it per cell repeats
    # the same recursive artifact queries six times and made this endpoint exceed
    # 90 seconds on the full strategy registry.
    verification_cache = {}
    for r in rows:
        (run_id, strat, sd, ed, ret, ann, mdd, wr, tc, summary, tj, status,
         engine_version, git_commit, signal_timing, execution_timing,
         market_cap_mode, universe_version, allocation_rule, fee_model, run_hash) = r
        strat = strat or "combo"
        label = PERIOD_LABELS.get((sd, ed))
        if label is None:
            continue

        cagr = sharpe = pl_ratio = None
        if tj:
            try:
                d = json.loads(tj)
                cagr = d.get("cagr")
                sharpe = d.get("sharpe")
                pl_ratio = d.get("pl_ratio")
            except Exception:
                pass

        created_strategy_entry = False
        if strat not in matrix:
            cond = STRATEGY_CONDITIONS.get(strat, {})
            matrix[strat] = {
                "strategy":   strat,
                "label":      STRATEGY_LABELS.get(strat, strat),
                "desc":       STRATEGY_DESC.get(strat, ""),
                "market_fit": cond.get("적합장세", ""),
                "stop_loss":  cond.get("손절선", ""),
                "periods":    {}
            }
            created_strategy_entry = True
        required_spec = all([
            run_hash, engine_version, git_commit, signal_timing,
            execution_timing, market_cap_mode, universe_version,
            allocation_rule, fee_model,
        ])
        point_in_time_universe = bool(
            universe_version
            and "current" not in universe_version.lower()
            and market_cap_mode in {"pit", "asof_approx"}
        )
        suite_hash = component_to_suite.get(run_hash)
        display_hash = suite_hash or run_hash
        if suite_hash and suite_hash in suite_verification:
            verification = suite_verification[suite_hash]
        elif display_hash:
            if display_hash not in verification_cache:
                verification_cache[display_hash] = derive_status(conn, display_hash)
            verification = verification_cache[display_hash]
        else:
            verification = {
                "status": "legacy", "gates": {"run_spec": False},
                "reasons": ["missing_run_spec"],
            }
        verification_status = verification["status"]
        if not include_legacy and verification_status == "legacy":
            if created_strategy_entry and not matrix[strat]["periods"]:
                del matrix[strat]
            continue
        methodology_verified = verification_status in {"point_in_time_verified", "forward_validated"}
        # 2026-07-23 리뷰 발견 P2 수정: execution_strict(D+1체결+현금원장은 실제 검증됨, PIT유니버스만
        # 미검증)와 point_in_time_approx(PIT는 근사 반영, 체결은 미검증)를 그동안 "specified_unverified"
        # 한 등급으로 뭉뚱그려서, 실제로는 체결검증까지 끝난 execution_strict가 실제보다 낮게 전달됐음
        # — 중간 등급 2개를 추가해 실제 검증 수준을 정확히 반영.
        if methodology_verified:
            methodology_status = "verified"
        elif verification_status == "execution_strict":
            methodology_status = "execution_verified_pit_pending"
        elif verification_status == "point_in_time_approx":
            methodology_status = "pit_approx_execution_pending"
        elif run_hash:
            methodology_status = "specified_unverified"
        else:
            methodology_status = "legacy_unversioned"
        methodology_warning = None
        if methodology_status == "legacy_unversioned":
            methodology_warning = "실행 명세가 없는 레거시 결과입니다. 전략 비교·채택 근거로 사용하지 마세요."
        elif methodology_status == "execution_verified_pit_pending":
            methodology_warning = "체결(D+1 시가)·현금원장 검증은 완료되었으나 point-in-time 유니버스 검증은 아직입니다."
        elif methodology_status == "pit_approx_execution_pending":
            methodology_warning = "point-in-time 유니버스는 근사 반영되었으나 엄격 체결 검증은 아직입니다."
        elif methodology_status == "specified_unverified":
            methodology_warning = "실행 명세는 있으나 point-in-time 유니버스 또는 엄격 체결 검증이 완료되지 않았습니다."

        matrix[strat]["periods"][label] = {
            "run_id":      run_id,
            "period_key": f"{sd[:7]}~{ed[:7]}",
            "start_date": sd,
            "end_date":   ed,
            "total_return_pct": ret,
            "ann_return_pct":   ann,
            "cagr":       cagr,
            "mdd":        mdd,
            "win_rate":   wr,
            "trade_count": tc,
            "sharpe":     sharpe,
            "pl_ratio":   pl_ratio,
            "methodology_status": methodology_status,
            "methodology_warning": methodology_warning,
            "verification_status": verification_status,
            "verification": verification,
            "selected": bool(display_hash and display_hash in selected_hashes),
            "methodology": {
                "engine_version": engine_version,
                "git_commit": git_commit,
                "signal_timing": signal_timing,
                "execution_timing": execution_timing,
                "market_cap_mode": market_cap_mode,
                "universe_version": universe_version,
                "allocation_rule": allocation_rule,
                "fee_model": fee_model,
                "run_hash": display_hash,
                "component_run_hash": run_hash if suite_hash else None,
            },
        }

    ordered = [matrix[s] for s in ALL_STRATEGIES if s in matrix]
    for s in matrix:
        if s not in ALL_STRATEGIES:
            ordered.append(matrix[s])

    for strategy in ordered:
        strategy["governance"] = classify_strategy(strategy.get("periods") or {})
    tier_order = {
        "live_eligible": 0, "paper_core": 1, "offensive_satellite": 2,
        "validation_queue": 3, "retired": 4,
    }
    ordered.sort(key=lambda item: (
        tier_order.get(item["governance"]["tier"], 9),
        -(item["governance"]["metrics"].get("average_return_pct") or -999),
    ))
    governance = summarize_governance(ordered)

    conn.close()

    return {
        "strategies":     ordered,
        "period_order":   CORE_PERIODS,
        "strategy_order": ALL_STRATEGIES,
        "strategy_labels": STRATEGY_LABELS,
        "selected_registry": registry_rows,
        "selection_required": not bool(registry_rows),
        "include_legacy": include_legacy,
        "governance": governance,
        "auto_trading_allowed": False,
    }


@router.get("/registry")
def get_selected_run_registry(report_type: str | None = None):
    return {"items": selected_registry(DB_PATH, report_type=report_type)}


@router.get("/registry/candidates")
def get_run_registry_candidates(strategy: str | None = None):
    conn = _db(); conn.row_factory = _sl.Row
    try:
        params: list = []
        where = "WHERE r.status='done' AND s.run_hash IS NOT NULL"
        if strategy:
            where += " AND s.strategy=?"
            params.append(strategy)
        rows = conn.execute(
            f"""
            SELECT s.run_hash,s.run_id,s.strategy,r.start_date,r.end_date,r.total_return_pct,s.created_at
            FROM backtest_run_specs s JOIN backtest_runs r ON r.run_id=s.run_id
            {where} ORDER BY s.created_at DESC LIMIT 500
            """,
            params,
        ).fetchall()
        return {"items": [{**dict(row), "verification": derive_status(conn, row["run_hash"])} for row in rows]}
    finally:
        conn.close()


@router.post("/registry/select")
def select_registry_run(payload: dict):
    try:
        return select_run(
            strategy=str(payload.get("strategy") or ""),
            report_type=str(payload.get("report_type") or "strategy_center"),
            run_hash=str(payload.get("run_hash") or ""),
            selected_by=str(payload.get("selected_by") or "local_user"),
            note=str(payload.get("note") or ""),
            db_path=DB_PATH,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/registry/suites")
def create_registry_suite(payload: dict):
    try:
        return register_run_set(
            strategy=str(payload.get("strategy") or ""),
            report_type=str(payload.get("report_type") or "strategy_center"),
            members=dict(payload.get("members") or {}),
            db_path=DB_PATH,
        )
    except (ValueError, _sl.Error) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/verification/{run_hash}")
def get_run_verification(run_hash: str):
    conn = _db(); conn.row_factory = _sl.Row
    try:
        return derive_status(conn, run_hash)
    finally:
        conn.close()


@router.post("/combinations/simulate")
def simulate_strategy_combination(payload: dict):
    orders = payload.get("orders") or []
    if not orders:
        raise HTTPException(status_code=422, detail="timestamped component orders are required; weighted returns are not accepted")
    config = MergeConfig(**(payload.get("config") or {}))
    component_hashes = [str(value) for value in payload.get("component_run_hashes") or []]
    if payload.get("persist"):
        if not component_hashes:
            raise HTTPException(status_code=422, detail="component_run_hashes are required for persistence")
        try:
            return persist_merged_run(orders, component_hashes, config, DB_PATH)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return simulate_merged_account(orders, config)


@router.get("/combinations/list")
def list_strategy_combinations():
    """등록된 병합계좌(combined) run 목록 — 전략센터 조합 탭 소스 (2026-07-18).

    2026-07-28 정리: 과거엔 최근 20개 run을 전부 반환해 프론트가 "이전 실험(참고용)" 회색
    행들을 계속 쌓아 보여주고 있었음 — 사용자 지적: "프론트엔드에는 이전 실험과 같은 것이
    남지 않도록 해줘". 게다가 2026-07-25 merged_simulator.py의 daily mark-to-market 버그
    수정 이전(2026-07-25 이전) run들은 더 이상 유효하지 않은 수치(예: 605.05%는 552.22%가
    맞는 수치)인데도 total_return_pct 내림차순 1위로 뽑혀 "현재 최고"로 잘못 표시되고 있었음.
    → 수정된 시뮬레이터 이후(2026-07-25~) run만 대상으로 하고, **현재 유효한 최선 1건만** 반환.
    """
    conn = _db(); conn.row_factory = _sl.Row
    try:
        # 2026-07-28 재정정: total_return_pct DESC 정렬은 "측정기간이 짧아서 최근 조정을
        # 덜 반영한 낡은 등록"이 수치가 더 높다는 이유만으로 "현재 최고"로 뽑히는 함정이 있음
        # (실제로 656%대 조합이 2026-03-31 시점 컷오프였고, 동일 조합을 2026-07-28까지
        # 최신화하면 613%대로 내려감 — 둘 다 look-ahead는 없지만 후자가 "현재 기준" 정답).
        # end_date(측정이 실제로 커버한 최신 시점) 최댓값을 우선 기준으로 삼고, 동률이면
        # created_at 최신순으로 골라 "가장 최신 데이터로 측정된 결과"가 항상 이기도록 함.
        #
        # 2026-08-08 재조정: LIMIT 1은 "가장 최신 end_date를 공유하는 유효 등록이 여러 개일 때"
        # 나머지를 통째로 숨겨버린다. 실제로 자본비례 티켓(ticket_pct) 검증 결과 동일 구성·동일
        # 측정기간인데 리스크 프로파일만 다른 등록이 둘 생겼고(15% 1151.8%/MDD-43.4,
        # 25% 2681.9%/MDD-49.5), 등록 순서 때문에 더 높은 쪽이 가려졌다. "낡은 등록을 숨긴다"는
        # 원래 의도는 end_date 필터가 이미 수행하므로, 최신 end_date를 공유하는 등록만 수익률
        # 내림차순으로 함께 보여준다(과거 실험 회색행이 쌓이던 문제는 재발하지 않음).
        latest = conn.execute("""
            SELECT MAX(br.end_date) FROM backtest_runs br
            JOIN backtest_run_specs s ON s.run_id=br.run_id
            WHERE br.strategy='combined' AND br.status='done'
              AND COALESCE(br.total_trades,0) >= 10
              AND br.created_at >= '2026-07-25'
        """).fetchone()
        latest_end = latest[0] if latest else None
        rows = conn.execute("""
            SELECT br.run_id, br.start_date, br.end_date, br.total_return_pct,
                   br.win_rate, br.total_trades, br.created_at, br.max_drawdown_pct,
                   s.run_hash, s.parameter_json
            FROM backtest_runs br JOIN backtest_run_specs s ON s.run_id=br.run_id
            WHERE br.strategy='combined' AND br.status='done'
              AND COALESCE(br.total_trades,0) >= 10   -- 1~2건짜리 인프라 테스트 잔재 제외
              AND br.created_at >= '2026-07-25'       -- daily mark-to-market 버그 수정 이후만
              AND br.end_date = ?
            ORDER BY br.total_return_pct DESC LIMIT 5
        """, (latest_end,)).fetchall()
        out = []
        for r in rows:
            components = []
            initial_cash = None
            tiebreak = None
            try:
                pj = json.loads(r["parameter_json"])
                initial_cash = (pj.get("config") or {}).get("initial_cash")
                # 2026-08-08: 동점 타이브레이크 의존도(경로운). 등록 시점에 측정돼 저장된다.
                # None이면 이 게이트 도입(2026-08-08) 이전 등록이라 미측정이라는 뜻 —
                # 그 수치는 코드순 행운이 섞여 있을 수 있으므로 그대로 신뢰하면 안 된다.
                tiebreak = pj.get("tiebreak_stability")
                for h in pj.get("component_run_hashes", []):
                    srow = conn.execute(
                        "SELECT strategy FROM backtest_run_specs WHERE run_hash=? LIMIT 1", (h,)
                    ).fetchone()
                    skey = srow["strategy"] if srow else "?"
                    components.append({"run_hash": h[:12], "strategy": skey,
                                       "label": STRATEGY_LABELS.get(skey, skey)})
            except Exception:
                pass
            out.append({
                "run_id": r["run_id"], "run_hash": r["run_hash"],
                "start_date": r["start_date"], "end_date": r["end_date"],
                "total_return_pct": r["total_return_pct"], "win_rate": r["win_rate"],
                "total_trades": r["total_trades"], "created_at": r["created_at"],
                "max_drawdown_pct": r["max_drawdown_pct"],
                "initial_cash": initial_cash, "components": components,
                "tiebreak_stability": tiebreak,
            })
        return {"combinations": out}
    finally:
        conn.close()


@router.get("/continuous-returns")
def get_continuous_returns():
    """전략별 최신 연속운용(2020-03~현재) 백테스트 실측 결과 — 프론트의
    STRATEGY_HUB_CONTINUOUS_RETURNS 하드코딩 객체를 대체하는 라이브 소스 (2026-08-30 신규).

    사용자 지시: "백테스트가 돌고나면 자동으로 프론트엔드가 수정되도록해" — 지금까지는
    scratch/register_*.py 스크립트로 연속운용을 재실행할 때마다 App.jsx의 숫자를 손으로
    고쳐야 했음(재실행→수동 반영 누락 위험). 이 엔드포인트는 backtest_runs에서 종목전량
    2020-03-15 이전 시작 + 최근 60일 이내까지 커버하는(=연속운용 스타일) 최신 'done' run을
    전략별로 하나씩 뽑아 그대로 반환 — 매주 일요일 01:30 전략센터주간재검증이나 수동 실험이
    새 연속운용 run을 저장하면, 프론트는 다음 새로고침에 자동으로 그 값을 보여준다.
    아직 이런 연속운용 run이 없는 전략(키가 응답에 없음)은 프론트가 기존 하드코딩 폴백값을
    계속 쓴다 — 이 API는 "있으면 덮어쓰기"용이지 전체 대체가 아니다.
    """
    conn = _db(); conn.row_factory = _sl.Row
    try:
        cutoff_end = (_date.today() - _timedelta(days=60)).isoformat()
        rows = conn.execute("""
            SELECT br.strategy, br.run_id, br.start_date, br.end_date,
                   br.total_return_pct, br.win_rate, br.total_trades, br.max_drawdown_pct,
                   br.created_at
            FROM backtest_runs br
            WHERE br.status='done' AND br.strategy != 'combined'
              AND br.start_date <= '2020-03-15' AND br.end_date >= ?
              AND br.total_return_pct IS NOT NULL
            ORDER BY br.strategy, br.created_at DESC
        """, (cutoff_end,)).fetchall()
        out = {}
        for r in rows:
            key = r["strategy"]
            if key in out:
                continue  # 이미 더 최신 run(created_at DESC 정렬상 먼저 나온 것)을 채택함
            out[key] = {
                "ret": r["total_return_pct"], "win": r["win_rate"],
                "trades": r["total_trades"], "mdd": r["max_drawdown_pct"],
                "run_id": r["run_id"], "start_date": r["start_date"],
                "end_date": r["end_date"], "updated_at": r["created_at"],
            }
        return {"strategies": out, "cutoff_end": cutoff_end}
    finally:
        conn.close()


@router.get("/security-master/{stock_code}")
def get_asof_security(stock_code: str, as_of: str = Query(...)):
    conn = _db(); conn.row_factory = _sl.Row
    try:
        return resolve_security(conn, stock_code, as_of).__dict__
    finally:
        conn.close()


def _verdict_label(verdict: str) -> dict:
    """signal_experiment_ledger.verdict(영문 스네이크케이스 코드)를 한글 배지로 매핑.
    2026-07-28: 사용자 지시로 '검증 이력'을 화면에 노출하며 신설 — verdict 문자열은
    자유서술이라 완전열거는 불가능해 키워드 우선순위로 분류(모호하면 '참고/미분류').
    ⚠️ 우선순위 중요: "adopt"와 "reject"가 둘 다 들어간 verdict(예: as-of 리트로핏처럼
    '수익률 개선 목적이 아니라 정직화 목적으로 채택'하는 경우, 또는 'A는 채택 B는 기각'
    형태의 혼합 결론)가 실제로 존재하므로, 일반 채택/기각 판정보다 먼저 걸러야 한다."""
    v = (verdict or "").lower()
    if "honesty_fix" in v:
        if "declined" in v:
            return {"badge": "정직화(수익↓)", "color": "#fb923c"}
        if "unchanged" in v or "no_material_change" in v:
            return {"badge": "정직화(변화없음)", "color": "#60a5fa"}
        return {"badge": "정직화", "color": "#60a5fa"}
    if "label_only_no_logic_change" in v or "no_material_change" in v:
        return {"badge": "라벨정정(로직불변)", "color": "#94a3b8"}
    if ("adopt" in v and "reject" in v) or "mixed" in v or "partial" in v:
        return {"badge": "부분채택", "color": "#fbbf24"}
    if "reject" in v or "no_discriminative" in v or "not_adopted" in v:
        return {"badge": "기각", "color": "#f87171"}
    if "adopt" in v or v.startswith("promote") or "confirmed_adopted" in v:
        return {"badge": "채택", "color": "#34d399"}
    if "implemented" in v:
        return {"badge": "반영(통계적주장아님)", "color": "#60a5fa"}
    if "deferred" in v or "inconclusive" in v:
        return {"badge": "보류", "color": "#a78bfa"}
    return {"badge": "참고", "color": "#94a3b8"}


@router.get("/experiment-ledger")
def get_experiment_ledger(strategy_key: str | None = Query(None), limit: int = Query(300)):
    """전략센터 수익률 개선 실험/검증 이력 (signal_experiment_ledger).
    2026-07-28 신설 — 사용자 지시: '수익률 개선을 위해 검증했던 내용을 기록해달라'.
    walk-forward(학습/검증 분리) 또는 as-of 리트로핏 등 이 프로젝트에서 시도한 모든 가설실험을
    채택/기각 여부와 함께 그대로 노출 — 오늘 실험이든 과거 실험이든 동일하게 조회 가능."""
    conn = _db(); conn.row_factory = _sl.Row
    try:
        where = "WHERE strategy_key=?" if strategy_key else ""
        params = [strategy_key] if strategy_key else []
        rows = conn.execute(
            f"""SELECT id, strategy_key, experiment_name, hypothesis, baseline_avg6, baseline_pos,
                       treatment_avg6, treatment_pos, verdict, detail, tested_at
                FROM signal_experiment_ledger {where}
                ORDER BY tested_at DESC, id DESC LIMIT ?""",
            params + [limit],
        ).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            d.update(_verdict_label(r["verdict"]))
            items.append(d)
        strategy_keys = [r[0] for r in conn.execute(
            "SELECT DISTINCT strategy_key FROM signal_experiment_ledger ORDER BY strategy_key"
        ).fetchall()]
        return {"items": items, "total": len(items), "strategy_keys": strategy_keys}
    finally:
        conn.close()


@router.post("/run-all-matrix")
async def start_all_matrix_backtests(payload: dict):
    """전체 V1~V9 전략 × 5기간 백테스트 일괄 실행 (최대 45 runs).
    예산: 1억원/전략 → per_stock 기본 10,000,000원 (10종목 기준).
    실제 데이터 확인 가능 시점 기준으로 백테스트 실행.
    """
    per_s = float(payload.get("per_stock", 10_000_000))

    func_map = {
        strategy: getattr(_bt, function_name)
        for strategy, function_name in STRATEGY_RUN_FUNCS.items()
    }

    include_regime = payload.get("include_regime_adaptive", True)
    run_strategies = ALL_STRATEGIES_EX if include_regime else ALL_STRATEGIES
    started = []
    for strat_key in run_strategies:
        run_func = func_map.get(strat_key)
        if not run_func:
            continue
        for (start, end) in STANDARD_PERIODS:
            period_label = PERIOD_LABELS.get((start, end), f"{start[:7]}~{end[:7]}")
            strat_label  = STRATEGY_LABELS.get(strat_key, strat_key)
            run_id = str(uuid.uuid4())[:8]
            name   = f"{strat_label} {period_label}"

            conn = _db()
            conn.execute(
                "INSERT OR IGNORE INTO backtest_runs "
                "(run_id,name,strategy,start_date,end_date,per_stock,status) "
                "VALUES (?,?,?,?,?,?,'running')",
                (run_id, name, strat_key, start, end, per_s)
            )
            conn.commit(); conn.close()

            def _run(func, s, e, ps, nm, rid):
                try:
                    func(s, e, per_stock=ps, run_name=nm, run_id=rid)
                except Exception as ex:
                    c = _sl.connect(DB_PATH, timeout=30)
                    c.execute(
                        "UPDATE backtest_runs SET status='error',summary_text=? WHERE run_id=?",
                        (str(ex), rid)
                    )
                    c.commit(); c.close()

            threading.Thread(
                target=_run,
                args=(run_func, start, end, per_s, name, run_id),
                daemon=True, name=f"BT-ALL-{strat_key[:4]}-{run_id}"
            ).start()
            started.append({"run_id": run_id, "strategy": strat_label, "period": period_label})

    return {"started": len(started), "runs": started}


# ── 월별 종목 후보 (Codex 검증 실패 전략) — /{run_id} 앞에 위치해야 함 ──

@router.get("/monthly-picks")
def get_monthly_picks(
    signal_date: str = Query(None, description="기준일 YYYY-MM-DD (없으면 오늘)"),
    top_n: int = Query(5, ge=1, le=20),
    sizing: str = Query("vol_inv", description="equal|score|vol_inv"),
):
    """
    Codex 후보 전략 (strong_trend_material_stop20) 기준 월별 종목 후보.
    2026-06-22 재검토 결과: 5년 OOS -10.6%, MDD -51.2%로 실매매 보류.
    """
    try:
        import sys
        if "/Volumes/Realtek_NVME/stock_dashboard/runtime" not in sys.path:
            sys.path.insert(0, "/Volumes/Realtek_NVME/stock_dashboard/runtime")
        from scripts.strategy_monthly_picks import (
            generate_monthly_picks,
            strategy_description,
        )
        sig = _date.fromisoformat(signal_date) if signal_date else _date.today()
        picks = generate_monthly_picks(signal_date=sig, top_n=top_n, position_sizing=sizing)
        desc  = strategy_description()
        return {
            "signal_date": sig.isoformat(),
            "sizing":      sizing,
            "top_n":       top_n,
            "picks":       picks,
            "strategy":    desc,
        }
    except Exception as e:
        logger.exception("monthly-picks error")
        return {"error": str(e), "picks": []}


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
