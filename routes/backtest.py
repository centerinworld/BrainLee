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
    """V트렌드 + DART 수주공시 ★2 이상 필터 백테스트 (비교용)."""
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
    """V10 이익폭발 + HS 수출 YoY 필터 백테스트 (보너스 효과 검증용)."""
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
    """V11 흑자전환 + HS 수출 YoY 필터 백테스트 (보너스 효과 검증용)."""
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


STRATEGY_LABELS = {
    "v1":      "V1 가치매수",
    "v2":      "V2 재무성장",
    "v3":      "V3 추세단독",
    "v4":      "V4 복합콤보",
    "v5":      "V5 수급모멘텀",
    "v6":      "V6 추세+재무",
    "v7":      "V7 가치+모멘텀",
    "v8":      "V8 수출선행",
    "v10":     "V10 이익폭발",
    "v11":     "V11 흑자전환",
    "v12":     "V12 섹터대세",
    "v_trend": "VT MA정배열",
    "v_dart":  "VT+DART필터",
    "combo":   "AI콤보(구)",
}

STRATEGY_DESC = {
    "v1":      "Graham 내재가치 할인 + 수급 보조 (저평가 발굴)",
    "v2":      "매출·OP YoY 성장/수익성 스코어링 — 단일팩터 장기 최고 CAGR",
    "v3":      "Graham 극단 저평가 단독 — ⚠️ 손절 빈도 높아 단독 비권장",
    "v4":      "Minervini 추세 + Graham 가치 + 수급 삼중 필터 (AI 콤보)",
    "v5":      "기관·외국인 단기 동반 순매수 모멘텀 — ⚠️ 수급 57일치 제한",
    "v6":      "MA정배열 추세 + 재무성장 이중 필터 (V2에 추세 조건 추가)",
    "v7":      "저평가(PBR<1) + 단기 모멘텀(3개월 수익률) 복합",
    "v8":      "HS무역통계 YoY 변곡점 + MA60 — 실제 수출 선행지표",
    "v10":     "OP YoY≥80% + Rev YoY≥30% 2분기 연속 고성장",
    "v11":     "적자→흑자 전환 2분기 연속 (시장필터 없음) ★하락장 강세",
    "v12":     "KOSPI 대비 섹터 알파≥15% 대세 추종 — ⚠️ 후행성",
    "v_trend": "MA20>60>120 정배열 + RSI42-72 + 거래량×1.3배",
    "v_dart":  "VT MA정배열 + DART 수주공시 ★2 이상 하드필터 (비권장)",
    "combo":   "AI 적극검토 콤보 (구버전) — V4와 유사",
}

# 표준 기간 레이블 (start_date, end_date) → 표시명
# ★ 5개 사용자 지정 기간 (한국 시장 사이클 기준)
PERIOD_LABELS = {
    ("2020-03-01", "2021-11-30"): "20.3~21.11",  # 코로나 회복 (상승장)
    ("2021-12-01", "2022-10-31"): "21.12~22.10", # 고점→하락 (인플레·금리)
    ("2022-11-01", "2023-10-31"): "22.11~23.10", # 회복 (저점 반등)
    ("2023-11-01", "2024-12-31"): "23.11~24.12", # AI/반도체 랠리
    ("2024-06-01", "2025-05-31"): "24.6~25.5",   # 최근 (현재까지)
    # 구버전 호환 (기존 DB 데이터 보존용)
    ("2018-01-01", "2025-05-31"): "2018~2025",
    ("2020-01-01", "2021-12-31"): "2020~2021(구)",
    ("2022-01-01", "2023-06-30"): "2022~2023(구)",
    ("2023-07-01", "2025-05-31"): "2023~2025(구)",
}

# 전체 전략 순서 — V1~V12 순번 우선, VT/AI콤보 후반
ALL_STRATEGIES = ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8", "v10", "v11", "v12", "v_trend", "v_dart", "combo"]

# 핵심 5개 기간 (사용자 지정 — 한국 시장 사이클)
CORE_PERIODS = ["20.3~21.11", "21.12~22.10", "22.11~23.10", "23.11~24.12", "24.6~25.5"]


@router.get("/matrix")
def get_backtest_matrix():
    """전략 × 기간 비교 매트릭스 반환 — 전체 전략 × 4기간"""
    conn = _db()
    rows = conn.execute(
        "SELECT strategy, start_date, end_date, total_return_pct, ann_return_pct, "
        "max_drawdown_pct, win_rate, total_trades, summary_text, trades_json, status "
        "FROM backtest_runs WHERE status='done' ORDER BY strategy, created_at ASC"
    ).fetchall()
    conn.close()

    # 전략별 기간별 데이터 구성 (후행 레코드가 이전 레코드를 덮어써서 최신값 유지)
    matrix = {}
    for r in rows:
        strat, sd, ed, ret, ann, mdd, wr, tc, summary, tj, status = r
        strat = strat or "combo"
        label = PERIOD_LABELS.get((sd, ed))
        if label is None:
            continue  # 표준 기간이 아닌 실험적 run은 제외

        cagr = None
        sharpe = None
        pl_ratio = None
        if tj:
            try:
                d = json.loads(tj)
                cagr = d.get("cagr")
                sharpe = d.get("sharpe")
                pl_ratio = d.get("pl_ratio")
            except Exception:
                pass

        if strat not in matrix:
            matrix[strat] = {
                "strategy": strat,
                "label": STRATEGY_LABELS.get(strat, strat),
                "desc": STRATEGY_DESC.get(strat, ""),
                "periods": {}
            }
        matrix[strat]["periods"][label] = {
            "period_key": f"{sd[:7]}~{ed[:7]}",
            "start_date": sd,
            "end_date": ed,
            "total_return_pct": ret,
            "ann_return_pct": ann,
            "cagr": cagr,
            "mdd": mdd,
            "win_rate": wr,
            "trade_count": tc,
            "sharpe": sharpe,
            "pl_ratio": pl_ratio,
        }

    # ALL_STRATEGIES 순서로 정렬, 데이터가 있는 전략만 포함
    ordered = [matrix[s] for s in ALL_STRATEGIES if s in matrix]
    # 순서에 없지만 데이터가 있는 전략 추가
    for s in matrix:
        if s not in ALL_STRATEGIES:
            ordered.append(matrix[s])

    return {
        "strategies": ordered,
        "period_order": CORE_PERIODS,
        "strategy_order": ALL_STRATEGIES,
    }


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
