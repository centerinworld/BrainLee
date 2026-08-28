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
import sqlite3 as _sl
import threading
import uuid
from pathlib import Path

import backtest as _bt
from datetime import date as _date
from fastapi import APIRouter, Query

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = "/Applications/stock_dashboard/stock.db"
RESEARCH_SUMMARY_PATH = Path("/Applications/stock_dashboard/research_outputs/strategy_research_summary.json")

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
    "recovery":            "V-RECOVERY 낙폭반등",
    "deep_recovery":       "V-DEEP 깊은낙폭집중",
    "low_base_breakout":   "V-LOWBASE 저점기반돌파",
    "turnaround":          "V-TURNAROUND 흑자전환",
}

STRATEGY_DESC = {
    "v_trend":  "MA20>MA60>MA120 정배열 + RSI 42~72 + 거래량 ×1.3배. 잡음 최소화, 명확한 상승 구조에서만 진입하는 추세추종 기본형 ★AI랠리 최강",
    "v1_value": "Graham 내재가치 25%+ 할인 OR PBR<0.7·PER<10 + 영업흑자. 시장 사이클 무관 저평가 발굴 — 보수적 분할매수에 적합",
    "v2":       "영업이익률·ROE·ROA 수익성 3축 스코어 ≥3점 + 영업흑자 + 보조수급. 재무 우량주 장기 보유형 — 복리효과 극대화",
    "v5":       "기관+외국인 5일 동반 누적 순매수 + MA20>60>120 정배열 + 영업흑자. 스마트머니 방향에 편승하는 수급 주도 모멘텀",
    "v4":       "Minervini RS(상대강도) + Graham PBR·PER 저평가 + 기관·외국인 수급 삼중 필터. 기술적·기본적·수급 3가지 조건 동시 충족 ★최우수 종합전략",
    "v10":      "영업이익 YoY≥80% + 매출 YoY≥30% 2분기 연속 확인 + KOSPI MA60 위. 이익 폭발 구간을 압축 투자로 포착하는 고성장 모멘텀",
    "v11":      "V7 이익가속(Earnings Acceleration): OP YoY>30% 3분기 연속 가속 + 이익성장률>매출성장률(마진 레버리지) + MA60>MA120 추세전환 + 52W 50~88% + 기관OR외인 유입. avg5=+6.5%, 최신+60.7%. 회복장·최근 구간 특히 강세.",
    "v8":       "★V9 수출 변곡점 선행 전략(2026-07-03 재설계): 수출 YoY 음수→양수 전환(진짜 변곡점) 포착 + MA60+20% 상단 차단(선반영 방지). 이전 2개월 부진 또는 급가속 확인으로 후행 진입 제거. 데이터기반 매도: 수출역전청산(YoY<-3%) + 수출전환실패청산. avg5=+8.1%",
    "v12":      "★V10 섹터 초기사이클 전략(2026-07-03 재설계): early-cycle(alpha 4~20%) + 1개월 alpha 양전 동시 확인으로 정점 진입 차단. 데이터기반 매도: 섹터 1M 모멘텀 역전(-2% 이하) + MA20 붕괴 → 섹터사이클 소멸 청산. avg5=+1.6%",
    "regime_adaptive": "레짐 자동 감지 → BULL(KOSPI>MA120)이면 V1 MA추세, BEAR이면 V7 이익가속으로 자동 전환. 전 기간 일관된 성과 목표.",
    "composite":       "★V10 복합스코어링(최종): 흑자전환(35)+추세(25)+거래량(15)+수급AND(20)+가치(5)=100점. 65점 이상+절대모멘텀 필터. 평균 +70.9%, 5기간 전부 양수. 하락장 +17.2%/회복장 +226.6%",
    "golden_cross":         "★V12 골든크로스 모멘텀: MA20이 MA60을 상향돌파(15일내) + 거래량×1.2 + RS6M 랭킹 진입. Trail25%/30% 추적손절. 중대형주 2000억+ 한정(소형주 포함 시 약세/회복장 -39% → 2000억 필터 후 avg6 +39.1%). 5/6기간 양수: 상승장+92%/하락장-2%/회복장+21%/AI랠리-2%/최근+36%/최신+90%",
    "high_profit_compound": "★V13 고수익 집중: 임원매수(180일)+성장섹터+MA20위반등확인+거래량1.3배+계약or수주잔고 + KOSPI MA60 필터. 하락장 신규 진입을 차단해 기존 하락장 손실을 완화. 고정 10슬롯 기준 avg6=+17.3%, 상승장+70.8%/회복장+37.7%/최신+22.5%",
    "sector_focus":         "★V-SECTOR 주도섹터 집중: 섹터 BUY 신호(점수≥55) 발생 시 섹터 내 3개월 RS 리더+기관집중도 우수 종목 TOP3를 매수. KOSPI 절대 레벨로 차단하지 않고 강한 섹터 장세를 우선 반영. 월 1회 리밸런싱, 최소 44일 섹터 보유 후 EXIT.",
    "recovery":             "★V-RECOVERY 낙폭과대 반등: 데이터 실증(2026-06-29) MA60 -25%이상 하방 종목 3배 달성률 69.2%(전체평균 6.7%의 10배!). MA60 -20~-65% 낙폭 + 52주 저점 80%이내 + 거래량반등×2.0 + 3일 2일이상 상승. Trail-20%/25%, 손절-12%",
    "deep_recovery":        "★V-DEEP 깊은낙폭집중: 실증 최강구간(-25~-45% MA60) 집중. 데이터: -35~-45%구간 120d평균+73.9%/2배달성률22.4%, -45%이하 +103.5%/29.5%. 중간반등(저점+30~80%)이 저점직후보다 좋음. MA60 -25~-60% + 거래량1.5배 + 최근5일중3일상승. Trail-22%/30%, 손절-13%, TP100%",
    "low_base_breakout":    "★V-LOWBASE 저점기반돌파: 실증(3배+종목200건분석): 86%가 MA60 ±15%이내 또는 상단, 65%가 52주저점+30%이내, 83%가 52주저점+65%이내. V-GC 골든크로스 직전/초기 진입 — MA60 -18%~+10% + 52주저점+65%이내 + MA20수렴(-8%이내) + 5일중3일상승. Trail-15%/20%/25%, 손절-10%, 만료270일",
    "turnaround":           "★V-TURNAROUND 흑자전환 특화: BQ 실증 흑자전환 종목 평균 6.14x vs 우량성장주 3.48x(1.77배 우위). 52주 고점 -30~-65% 낙폭과대 + 직전 분기 첫 흑자전환(이전 1~3분기 적자 존재) + TTM NI 합산 양수(임시 반등 제외) + PBR≤1.5 저평가. Trail-25%/30%, 손절-13%(stop=-0.13 최적화), 만료300일. avg5=+16.4%",
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
        "진입조건": "100점 스코어 ≥ 65점 + 절대모멘텀(3개월-15%·1개월-5% 필터): [흑자전환 35점: OP>30억+직전분기+1년전적자] + [추세 25점: MA20>MA60+MA120 위] + [거래량 15점: 10일평균 1.5~2배+] + [기관+외국인 동반순매수 20점 / 한쪽만 5점] + [가치 5점: Graham OR PBR<1.2]",
        "매도조건": "65~69점 → 익절+20% / 70~79점 → 익절+30% / 80점+ → 익절+40% | MA60 붕괴 즉시 매도 | 손절-10%",
        "손절선": "-10%",
        "추가필터": "3개월 수익률 -15% 이상 하락주 진입 금지 / 1개월 -5% 이상 하락주 진입 금지 / 재무데이터 없는 종목 자동 제외",
        "적합장세": "★전 사이클 양수: 상승장+99.1%/하락장+17.2%/회복장+226.6%/AI랠리+6.9%/최근+4.6% — 평균 +70.9%",
        "주의사항": "수급 데이터 커버리지: 2020(18~26%)→2025(41~48%). 기관+외국인 동반매수 없으면 수급 5점만으로 65점 달성 어려움 — 자연스러운 신호 필터.",
    },
    "golden_cross": {
        "진입조건": "[A] MA20>MA60 단기 정배열 [B] 최근 15일 내 MA20이 MA60 상향돌파(골든크로스) [C] 5일 거래량>20일 평균×1.2 [D] RS6M(6개월 KOSPI 대비 상대강도) > -20 → RS6M 높은 순으로 진입",
        "매도조건": "★피크이지(Trail25%): 이익 5%+ 달성 후 고점대비 -25% 하락 시 매도 / ★대박홀드(Trail30%): 이익 50%+ 달성 시 고점대비 -30% (추세 길게 가져가기)",
        "손절선": "-12% (갭리스크 고려 기존보다 넓게 설정)",
        "추가필터": "★시총 2000억+ KOSPI/KOSDAQ 한정(소형주 제외: 2000억 미만 포함 시 약세/회복장에서 avg5 -11% → 2000억 필터로 +29% 개선) / 전일대비 50%+ 급등락 종목 자동 제외(분할·합병 미조정 데이터 필터) / 최소 주가 1,000원 이상",
        "적합장세": "★5/6기간 양수(재검증 2026-07-05, 2000억+ 기준): 상승장+92.2% / 하락장-2.0%(KOSPI -22.9% 대비 α+20.9%) / 회복장+21.4% / AI랠리-1.8% / 최근+35.5% / 최신+89.5%. avg6=+39.1%",
        "주의사항": "하락장(-2.0%)과 AI랠리(-1.8%)는 소폭 손실 가능 — 두 기간 합쳐도 -3.8%로 통제 가능. Trail25%가 급등장 조기청산 가능성 있으나 Trail30%(이익50%+) 구간이 보완. 재무 데이터 불필요(순수 기술적 전략). 2000억 미만 소형주는 약세/회복장 골든크로스 신뢰도 낮아 제외.",
    },
    "high_profit_compound": {
        "진입조건": "[A] 최근 180일 임원매수 공시 존재 [B] IT·의료·경기소비재·산업재 성장 섹터 [C] MA20 위(반등 확인) + 52주 저점 대비 20%+ 이탈 [D] 진입일 거래량 ≥ 20일평균×1.3 [E] 일거래대금 20억원+ [F] 계약공시(signal≥2) 또는 수주잔고 존재",
        "매도조건": "Trail-35%(이익10%+발동, 이익100%+시Trail-40%) 추적손절 / 손절-15% / 익절라인없음(Trail로만청산) / 만료400일",
        "손절선": "-15%",
        "추가필터": "KOSPI MA60 아래 신규 진입 차단 / 패닉장 방어 / 3중 교집합: 임원매수×섹터×촉매(계약+수주)",
        "적합장세": "MA60 필터 적용 재검증: 상승장+70.8% / 하락장-40.7% / 회복장+37.7% / AI랠리+10.6% / 최근+2.9% / 최신+22.5%. avg6=+17.3%",
        "주의사항": "임원매수 데이터 2024-06부터. 계약·수주잔고 커버리지 한정. 하락장 손실은 기존보다 완화됐지만 여전히 큼(-40.7%). 승률 낮음이 정상 — 소수 대박 종목이 다수 소액손실 상쇄 구조",
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
ALL_STRATEGIES = ["v_trend", "v1_value", "v2", "v5", "v4", "v10", "v11", "vbr", "v8", "v12", "composite", "golden_cross", "high_profit_compound", "sector_focus", "recovery", "deep_recovery", "low_base_breakout", "turnaround"]
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


@router.get("/strategy-research/summary")
def get_strategy_research_summary():
    payload = {}
    if RESEARCH_SUMMARY_PATH.exists():
        try:
            payload = json.loads(RESEARCH_SUMMARY_PATH.read_text())
        except Exception as e:
            payload = {"error": str(e)}

    # 기존 JSON에 유동성 필터 미적용 시 serve-time에 적용
    cr = payload.get("current_rankings", {})
    if cr and "liquidity_filtered" not in cr:
        ml = _filter_liquid_rankings(cr.get("ml_top20", []))
        hu = _filter_liquid_rankings(cr.get("heuristic_top20", []))
        payload = {
            **payload,
            "current_rankings": {**cr, "ml_top20": ml[:20], "heuristic_top20": hu[:20]},
        }

    return _json_safe({
        **payload,
        "strategy_rankings": _strategy_rankings_for_regime(),
    })


@router.post("/strategy-research/rebuild")
async def rebuild_strategy_research():
    def _run():
        try:
            import sys
            if "/Applications/stock_dashboard" not in sys.path:
                sys.path.insert(0, "/Applications/stock_dashboard")
            from scripts.build_strategy_research_dataset import build_strategy_research_dataset
            build_strategy_research_dataset()
        except Exception:
            logger.exception("strategy research rebuild failed")
    threading.Thread(target=_run, daemon=True, name="StrategyResearchRebuild").start()
    return {"ok": True, "message": "strategy research rebuild started"}


@router.get("/matrix")
def get_backtest_matrix():
    """전략 × 기간 비교 매트릭스 반환 — V1~V9 × 5기간"""
    conn = _db()
    rows = conn.execute(
        "SELECT strategy, start_date, end_date, total_return_pct, ann_return_pct, "
        "max_drawdown_pct, win_rate, total_trades, summary_text, trades_json, status "
        "FROM backtest_runs WHERE status='done' ORDER BY strategy, created_at ASC"
    ).fetchall()
    conn.close()

    matrix = {}
    for r in rows:
        strat, sd, ed, ret, ann, mdd, wr, tc, summary, tj, status = r
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
        matrix[strat]["periods"][label] = {
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
        }

    ordered = [matrix[s] for s in ALL_STRATEGIES if s in matrix]
    for s in matrix:
        if s not in ALL_STRATEGIES:
            ordered.append(matrix[s])

    return {
        "strategies":     ordered,
        "period_order":   CORE_PERIODS,
        "strategy_order": ALL_STRATEGIES,
        "strategy_labels": STRATEGY_LABELS,
    }


@router.post("/run-all-matrix")
async def start_all_matrix_backtests(payload: dict):
    """전체 V1~V9 전략 × 5기간 백테스트 일괄 실행 (최대 45 runs).
    예산: 1억원/전략 → per_stock 기본 10,000,000원 (10종목 기준).
    실제 데이터 확인 가능 시점 기준으로 백테스트 실행.
    """
    per_s = float(payload.get("per_stock", 10_000_000))

    func_map = {
        "v_trend":         _bt.run_backtest_v1,
        "v1_value":        _bt.run_backtest_value,
        "v2":              _bt.run_backtest_v2,
        "v5":              _bt.run_backtest_v5,
        "v4":              _bt.run_backtest,
        "v10":             _bt.run_backtest_v10,
        "v11":             _bt.run_backtest_v11,
        "vbr":             _bt.run_backtest_hidden_rev,
        "v8":              _bt.run_backtest_v8,
        "v12":             _bt.run_backtest_v12,
        "regime_adaptive": _bt.run_backtest_regime_adaptive,
        "composite":       _bt.run_backtest_composite,
        "golden_cross":         _bt.run_backtest_golden_cross,
        "high_profit_compound": _bt.run_backtest_high_profit_compound,
        "sector_focus":         _bt.run_backtest_sector,
        "recovery":             _bt.run_backtest_recovery,
        "deep_recovery":        _bt.run_backtest_deep_recovery,
        "low_base_breakout":    _bt.run_backtest_low_base_breakout,
        "turnaround":           _bt.run_backtest_turnaround,
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
        if "/Applications/stock_dashboard" not in sys.path:
            sys.path.insert(0, "/Applications/stock_dashboard")
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
