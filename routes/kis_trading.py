from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
import sqlite3
import time
import os
import json
from datetime import datetime, timedelta

from kis_client import kis_client
import config
from db_utils import connect_stock_db
from live_trading_data import (
    ensure_live_data_schema,
    evaluate_live_data_contract,
    record_execution_snapshot,
)

router = APIRouter(prefix="/api/kis-trading", tags=["kis-trading"])
def _conn():
    c = connect_stock_db(timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _insert_and_get_id(c, statement: str, params: tuple, id_column: str) -> int:
    """Insert through either primary backend and return its generated identity."""
    if config.IS_POSTGRES:
        row = c.execute(f"{statement.rstrip()} RETURNING {id_column}", params).fetchone()
        return int(row[0])
    cursor = c.execute(statement, params)
    if cursor.lastrowid is None:
        raise RuntimeError(f"generated {id_column} was not returned")
    return int(cursor.lastrowid)


def _init_tables():
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS kis_paper_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            side TEXT NOT NULL,
            qty INTEGER NOT NULL,
            req_price REAL,
            fill_price REAL,
            status TEXT NOT NULL,
            reason TEXT,
            order_krw REAL,
            mode TEXT NOT NULL DEFAULT 'PAPER'
        );

        CREATE TABLE IF NOT EXISTS kis_paper_positions (
            stock_code TEXT PRIMARY KEY,
            qty INTEGER NOT NULL,
            avg_price REAL NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS kis_paper_realized (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            qty INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            pnl REAL NOT NULL
        );

        -- 2026-07-23 신규(Codex 제안 A1/A2 검토 후 채택): 실전형 주문 생애주기 테이블.
        -- 기존 kis_paper_orders/positions/realized는 하위호환을 위해 그대로 유지하고,
        -- 아래 테이블에는 병행 기록(parallel write)만 추가한다 — 기존 API 응답을 깨지 않음.
        CREATE TABLE IF NOT EXISTS live_orders (
            order_id INTEGER PRIMARY KEY AUTOINCREMENT,
            parent_order_id INTEGER,
            mode TEXT NOT NULL DEFAULT 'PAPER',
            strategy_key TEXT,
            stock_code TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL DEFAULT 'market',
            qty INTEGER NOT NULL,
            limit_price REAL,
            status TEXT NOT NULL,
            filled_qty INTEGER NOT NULL DEFAULT 0,
            avg_fill_price REAL,
            reject_reason TEXT,
            decision_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS live_order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            event_ts TEXT NOT NULL,
            event_type TEXT NOT NULL,
            qty_delta INTEGER,
            price REAL,
            detail TEXT
        );

        CREATE TABLE IF NOT EXISTS live_fills (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            fill_ts TEXT NOT NULL,
            fill_qty INTEGER NOT NULL,
            fill_price REAL NOT NULL,
            cumulative_qty INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS live_cash_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            mode TEXT NOT NULL,
            delta_krw REAL NOT NULL,
            balance_after REAL,
            reason TEXT,
            ref_order_id INTEGER
        );

        CREATE TABLE IF NOT EXISTS risk_gate_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            side TEXT NOT NULL,
            strategy_key TEXT,
            decision TEXT NOT NULL,
            reasons TEXT,
            gate_snapshot TEXT,
            order_id INTEGER
        );
        """
    )
    gate_columns = {row[1] for row in c.execute("PRAGMA table_info(risk_gate_decisions)").fetchall()}
    if "decision_source" not in gate_columns:
        c.execute(
            "ALTER TABLE risk_gate_decisions ADD COLUMN decision_source TEXT NOT NULL DEFAULT 'legacy'"
        )
    c.commit()
    c.close()


_init_tables()
ensure_live_data_schema()


class PaperOrderIn(BaseModel):
    stock_code: str = Field(..., min_length=6, max_length=6)
    side: str = Field(..., pattern="^(buy|sell)$")
    qty: int = Field(..., ge=1)
    limit_price: float | None = Field(default=None, gt=0)
    strategy_key: str | None = Field(default=None)
    override_wait_confirm: bool = Field(default=False)


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _trading_mode() -> str:
    # 기본 PAPER 고정. LIVE는 명시적으로만.
    return (os.getenv("KIS_TRADING_MODE", "PAPER") or "PAPER").upper()


def round_to_tick_size(price: float) -> int:
    """2026-08-13(3차) 신규: KRX 호가단위(2023-01-25 개정 기준) 반올림.
    docs/codex_handoff_live_trading_gaps_20260813.md #2 — 지금까지 PAPER 체결가가
    호가단위를 무시한 임의 가격으로 저장되고 있었음. 실전 KIS 주문에서는 호가단위에
    맞지 않는 limit_price를 보내면 거부될 수 있어, /live/order 차단 해제 전 반드시
    선행돼야 하는 항목 — PAPER 경로에도 지금부터 적용해 시뮬레이션 체결가를 실제
    KIS가 받아들일 가격과 일치시킨다(round 방식은 반올림, 실제 거래소 호가는 항상
    이 단위의 정수배이므로 시장가 체결(px)도 이미 정렬돼 있어 사실상 no-op)."""
    p = float(price)
    if p < 2000:
        tick = 1
    elif p < 5000:
        tick = 5
    elif p < 20000:
        tick = 10
    elif p < 50000:
        tick = 50
    elif p < 200000:
        tick = 100
    elif p < 500000:
        tick = 500
    else:
        tick = 1000
    return int(round(p / tick) * tick)


def _risk_limits() -> dict:
    return {
        "max_order_krw": float(os.getenv("KIS_PAPER_MAX_ORDER_KRW", "5000000")),
        "max_position_count": int(os.getenv("KIS_PAPER_MAX_POSITION_COUNT", "15")),
        "max_daily_loss_krw": float(os.getenv("KIS_PAPER_MAX_DAILY_LOSS_KRW", "3000000")),
    }


def _latest_price(stock_code: str) -> float | None:
    p = kis_client.get_current_price(stock_code)
    if p and p.get("close"):
        return float(p["close"])
    c = _conn()
    r = c.execute(
        "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
        (stock_code,),
    ).fetchone()
    c.close()
    return float(r[0]) if r and r[0] else None


def _today_realized_loss(c: sqlite3.Connection) -> float:
    d = datetime.now().strftime("%Y-%m-%d")
    r = c.execute(
        "SELECT COALESCE(SUM(CASE WHEN pnl<0 THEN -pnl ELSE 0 END),0) FROM kis_paper_realized WHERE ts LIKE ?",
        (f"{d}%",),
    ).fetchone()
    return float(r[0] or 0.0)


# ══════════════════════════════════════════════════════════════
#  A2. 실전 리스크 게이트 (2026-07-23, Codex 제안 검토 후 채택)
#  최초 6개 게이트 구현 — 관리종목/거래정지 여부는 당시 stock_universe에 해당
#  컬럼이 없어 범위 밖으로 명시했으나, 2026-08-13 KRX 종목기본정보 수집기의
#  month-long 정체 버그를 수정하며 stock_universe.sector_type이 복구돼
#  _gate_managed_issue()로 신규 추가(아래).
# ══════════════════════════════════════════════════════════════

_GATE_ORDER = ["BLOCKED_STALE_DATA", "BLOCKED_RISK", "WAIT_CONFIRM", "SIZE_REDUCED", "BUY_ALLOWED"]


def _gate_data_freshness(c: sqlite3.Connection, stock_code: str) -> dict:
    r = c.execute(
        "SELECT MAX(date) FROM price_history WHERE stock_code=? AND close>0", (stock_code,)
    ).fetchone()
    latest = r[0] if r else None
    if not latest:
        return {"ok": False, "reason": "가격이력 없음", "latest_date": None, "age_days": None}
    age_days = (datetime.now() - datetime.strptime(latest[:10], "%Y-%m-%d")).days
    # 주말/공휴일 포함 여유를 둬 5일 이상 정체 시에만 stale 처리(3거래일+공휴일 버퍼)
    return {"ok": age_days <= 5, "reason": f"최근 가격 {age_days}일 전({latest[:10]})", "latest_date": latest[:10], "age_days": age_days}


def _gate_gap_risk(c: sqlite3.Connection, stock_code: str, live_price: float, gap_threshold: float = 0.07) -> dict:
    r = c.execute(
        "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1", (stock_code,)
    ).fetchone()
    prior_close = float(r[0]) if r and r[0] else None
    if not prior_close or prior_close <= 0:
        return {"ok": True, "data_available": False, "gap_pct": None, "reason": "전일종가 없음(판단불가, 통과)"}
    gap_pct = (live_price / prior_close - 1)
    return {
        "ok": gap_pct <= gap_threshold, "data_available": True,
        "gap_pct": round(gap_pct * 100, 2),
        "reason": f"전일종가대비 {gap_pct*100:+.1f}% 갭" + (" — 추격매수 주의" if gap_pct > gap_threshold else ""),
    }


def _gate_liquidity(c: sqlite3.Connection, stock_code: str, order_krw: float, adv_ratio_max: float = 0.03) -> dict:
    # 2026-08-09: trade_amount(KRX ACC_TRDVAL)가 2026-07~08 전종목 0으로 채워지던 인프라버그
    # (scheduler.py _job_krx_daily 참조, 원인규명·수정·백필 완료) 기간 중에는 "WHERE trade_amount>0"
    # 필터가 20일 내내 매칭되는 행을 못 찾아 rows가 비어 "판단불가→통과"로 유동성 게이트가
    # 사실상 무력화됐음. close×volume 폴백을 SQL에서 직접 계산해 재발 방지.
    rows = c.execute(
        "SELECT COALESCE(NULLIF(trade_amount,0), close*COALESCE(volume,0)) FROM price_history "
        "WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 20",
        (stock_code,),
    ).fetchall()
    rows = [r for r in rows if r[0] and r[0] > 0]
    if not rows:
        return {"ok": True, "data_available": False, "adv_20d": None, "ratio": None, "reason": "거래대금 이력 없음(판단불가, 통과)"}
    adv = sum(r[0] for r in rows) / len(rows)
    ratio = (order_krw / adv) if adv > 0 else None
    ok = ratio is None or ratio <= adv_ratio_max
    return {
        "ok": ok, "data_available": True, "adv_20d": round(adv, 0), "ratio": round(ratio, 4) if ratio is not None else None,
        "reason": f"주문금액/20일평균거래대금 비율 {ratio*100:.2f}%" if ratio is not None else "판단불가",
    }


def _gate_dilution_risk(c: sqlite3.Connection, stock_code: str, dilution_max: int = 3) -> dict:
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    r = c.execute(
        """SELECT COUNT(*) FROM dilution_events
           WHERE stock_code=? AND event_type IN ('CB','BW','EB','RIGHTS')
             AND COALESCE(risk_amount_status, 'amount_confirmed') != 'not_amount_applicable'
             AND disclosed_at BETWEEN ? AND ?""",
        (stock_code, cutoff, today),
    ).fetchone()
    n = int(r[0] or 0)
    return {"ok": n <= dilution_max, "data_available": True, "count_365d": n, "reason": f"최근 1년 실질 희석 이벤트 {n}건"}


def _gate_flow_reversal(c: sqlite3.Connection, stock_code: str, lookback_days: int = 5) -> dict:
    rows = c.execute(
        "SELECT inst_net_buy_amt, frn_net_buy_amt FROM price_history WHERE stock_code=? ORDER BY date DESC LIMIT ?",
        (stock_code, lookback_days),
    ).fetchall()
    if not rows:
        return {"ok": True, "data_available": False, "inst_frn_sum_억": None, "reason": "수급 이력 없음(판단불가, 통과)"}
    total = sum((r[0] or 0) + (r[1] or 0) for r in rows) / 100.0  # 백만원 -> 억원
    return {
        "ok": total >= -50, "data_available": True,  # 5일 합산 기관+외인 순매도가 -50억원 이상 심할 때만 경고
        "inst_frn_sum_억": round(total, 1),
        "reason": f"최근{lookback_days}일 기관+외인 순매수 {total:+.1f}억원" + (" — 동반매도 진행중" if total < -50 else ""),
    }


def _gate_market_regime(c: sqlite3.Connection, ma_window: int = 120) -> dict:
    rows = c.execute(
        "SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT ?",
        (ma_window,),
    ).fetchall()
    if len(rows) < ma_window:
        return {"ok": True, "data_available": False, "kospi_vs_ma120_pct": None, "reason": "KOSPI 이력 부족(판단불가, 통과)"}
    closes = [r[0] for r in rows]
    curr = closes[0]
    ma = sum(closes) / len(closes)
    pct = (curr / ma - 1) if ma else 0
    # V-PEAK/V-RECOVERY에서 검증된 패닉감지 기준(KOSPI<MA120*0.85)과 동일 임계 재사용
    return {
        "ok": curr >= ma * 0.85, "data_available": True,
        "kospi_vs_ma120_pct": round(pct * 100, 2),
        "reason": f"KOSPI가 120일선대비 {pct*100:+.1f}%" + (" — 패닉장 국면" if curr < ma * 0.85 else ""),
    }


# ══════════════════════════════════════════════════════════════
#  2026-07-23(2차): Codex S2(매수조건)/S4(포지션사이징) 검토 후 채택 — 기존 6게이트에 없던
#  "신용잔고 급증", "변동성 기반 수량조절", "섹터 집중한도" 3개만 신규 추가.
#  S2/S3의 나머지 항목(RS/거래대금/수급/과열회피/희석 등)은 이미 각 전략(megatrend/
#  V-MOONSHOT/V-SMARTFLOW/V-RECOVERY 등)에 종목선정 로직으로 구현·검증되어 있어 중복
#  구현하지 않음 — 여기 리스크게이트는 "이미 선정된 종목의 주문 크기/실행"만 조정한다.
# ══════════════════════════════════════════════════════════════

def _gate_managed_issue(c: sqlite3.Connection, stock_code: str) -> dict:
    """2026-08-13 신규: 관리종목/투자주의환기종목 배제. KRX sto/stk_isu_base_info의
    sector_type("소속부")을 collectors/krx_isu_base_info.py가 매일 18:35 수집(2026-08-13
    이전에는 basDd=오늘 요청이 KRX 미발행으로 매번 빈 응답을 받아 2026-07-10 이후 한 달간
    조용히 정체돼 있던 버그를 이날 수정 — T-1 폴백 도입, stock_universe.sector_type 즉시
    복구 확인). 관리종목은 상장폐지 심사대상·거래정지 위험이 크고, 투자주의환기종목은
    불공정거래 개연성 경고 — 둘 다 신규 매수 진입을 원천 차단한다(risk가 아니라 확정된
    거래소 지정 사실이므로 사전 확인용 SIZE_REDUCED가 아니라 BLOCKED_RISK로 분류)."""
    r = c.execute(
        "SELECT sector_type, base_info_updated_at FROM stock_universe WHERE stock_code=?", (stock_code,)
    ).fetchone()
    if not r:
        return {"ok": True, "data_available": False, "sector_type": None, "reason": "종목정보 없음(판단불가, 통과)"}
    sector_type, updated_at = r[0] or "", r[1]
    if updated_at:
        try:
            age_days = (datetime.now() - datetime.strptime(str(updated_at)[:10], "%Y-%m-%d")).days
            if age_days > 10:
                return {"ok": True, "data_available": False, "sector_type": sector_type,
                        "reason": f"관리종목 데이터 {age_days}일 전으로 오래됨(판단불가, 통과)"}
        except ValueError:
            pass
    is_flagged = ("관리종목" in sector_type) or ("투자주의환기종목" in sector_type)
    return {
        "ok": not is_flagged, "data_available": True, "sector_type": sector_type,
        "reason": f"소속부: {sector_type or '일반'}" + (" — 관리종목/투자주의환기종목 매수차단" if is_flagged else ""),
    }


def _gate_duplicate_order(c: sqlite3.Connection, stock_code: str, strategy_key: str | None) -> dict:
    """2026-08-13(3차) 신규: 주문 idempotency — 같은 전략+종목으로 오늘 이미 체결된 매수
    주문이 있으면 경고. 스케줄러 재시작 후 같은 신호 재제출, 네트워크 재시도, 병합조합
    간 신호 경합 등으로 중복 매수가 들어갈 위험을 막기 위함(docs/codex_handoff_
    live_trading_gaps_20260813.md #1). 하드 차단(BLOCKED_RISK)이 아니라 WAIT_CONFIRM
    등급으로 분류 — 점수기반 피라미딩처럼 같은 전략이 같은 종목을 하루 중 여러 번
    정당하게 추가매수하는 기존 검증된 패턴(score_based_pyramiding_20260809)과 충돌하지
    않도록, override_wait_confirm=true로 재요청하면 그대로 진행되게 설계."""
    if not strategy_key:
        return {"ok": True, "data_available": False, "is_duplicate": False,
                "reason": "strategy_key 없음(중복확인 불가, 통과)"}
    # db_compat의 DATE() SQL함수 변환이 date=text 타입오류를 유발하는 것을 이 게이트를
    # 실제 배포해 발견함(2026-08-13) — SQLite/Postgres 양쪽에서 안전한 문자열 범위비교로
    # 대체(created_at은 TEXT 컬럼, ISO 8601 포맷이라 사전식 비교로도 날짜 경계가 정확함).
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    r = c.execute(
        "SELECT COUNT(*) FROM live_orders WHERE strategy_key=? AND stock_code=? AND side='buy' "
        "AND status='FILLED' AND created_at>=? AND created_at<?",
        (strategy_key, stock_code, today, tomorrow),
    ).fetchone()
    n = int(r[0] or 0)
    return {
        "ok": n == 0, "data_available": True, "is_duplicate": n > 0, "filled_today": n,
        "reason": f"오늘 동일 전략({strategy_key})으로 이미 {n}건 매수 체결됨 — 의도된 추가매수인지 확인" if n > 0 else "오늘 첫 매수",
    }


def _gate_credit_surge(c: sqlite3.Connection, stock_code: str) -> dict:
    rows = c.execute(
        "SELECT dt, credit_ratio FROM kiwoom_credit_balance WHERE stock_code=? AND credit_ratio IS NOT NULL "
        "ORDER BY dt DESC LIMIT 20",
        (stock_code,),
    ).fetchall()
    if not rows:
        return {"ok": True, "data_available": False, "credit_ratio": None, "reason": "신용잔고 이력 없음(판단불가, 통과)"}
    latest_dt = rows[0][0]
    age_days = (datetime.now() - datetime.strptime(str(latest_dt)[:8], "%Y%m%d")).days
    if age_days > 45:
        return {"ok": True, "data_available": False, "credit_ratio": None, "reason": f"신용잔고 데이터 {age_days}일 전으로 오래됨(판단불가, 통과)"}
    curr = rows[0][1]
    base = rows[-1][1] if len(rows) >= 2 else curr
    surged = curr > 8.0 and base > 0 and (curr / base - 1) > 0.5
    return {
        "ok": not surged, "data_available": True,
        "credit_ratio": round(curr, 2),
        "reason": f"신용잔고비율 {curr:.1f}%(20일전 {base:.1f}%)" + (" — 신용잔고 급증" if surged else ""),
    }


def _gate_volatility_sizing(c: sqlite3.Connection, stock_code: str, order_krw: float, total_capital: float,
                             risk_pct: float = 0.012, assumed_stop_pct: float = 0.20) -> dict:
    """S4: 계좌 위험 기준 수량 조절 — 종목당 손실한도(총자본의 risk_pct)를 가정 손절폭(assumed_stop_pct)
    으로 나눠 허용 주문금액을 산출. 전략별 실제 손절폭은 제각각(-8%~-35%)이라 여기서는 이 세션에서
    가장 흔히 쓰인 기본값(-20%)을 보수적 공통 가정으로 사용 — 전략이 더 타이트한 손절을 쓰면 실제
    허용치는 이보다 커야 하므로, 이 게이트는 "최소한 이 정도는 넘지 말자"는 하한 안전장치다."""
    risk_budget_krw = total_capital * risk_pct
    max_order_krw = risk_budget_krw / assumed_stop_pct if assumed_stop_pct > 0 else order_krw
    return {
        "ok": order_krw <= max_order_krw,
        "max_order_krw": round(max_order_krw, 0),
        "reason": f"종목당 리스크한도(자본×{risk_pct*100:.1f}%÷가정손절{assumed_stop_pct*100:.0f}%) 기준 최대 {max_order_krw:,.0f}원",
    }


def _gate_sector_concentration(c: sqlite3.Connection, stock_code: str, order_krw: float, total_capital: float,
                                sector_limit_pct: float = 0.35) -> dict:
    sec_row = c.execute("SELECT sector_large FROM stock_universe WHERE stock_code=?", (stock_code,)).fetchone()
    sector = sec_row[0] if sec_row else None
    if not sector:
        return {"ok": True, "data_available": False, "sector": None, "current_exposure_pct": None, "reason": "섹터 분류 없음(판단불가, 통과)"}
    positions = c.execute("SELECT stock_code, qty, avg_price FROM kis_paper_positions").fetchall()
    exposure_krw = 0.0
    if positions:
        codes = [p[0] for p in positions]
        sec_map = dict(c.execute(
            "SELECT stock_code, sector_large FROM stock_universe WHERE stock_code IN ({})".format(
                ",".join("?" * len(codes))), codes).fetchall())
        for p_code, p_qty, p_avg in positions:
            if sec_map.get(p_code) == sector:
                exposure_krw += p_qty * p_avg
    new_exposure_pct = (exposure_krw + order_krw) / total_capital if total_capital else 0
    return {
        "ok": new_exposure_pct <= sector_limit_pct, "data_available": True,
        "sector": sector,
        "current_exposure_pct": round(exposure_krw / total_capital * 100, 1) if total_capital else None,
        "projected_exposure_pct": round(new_exposure_pct * 100, 1),
        "reason": f"'{sector}' 섹터 비중(주문후) {new_exposure_pct*100:.1f}%" + (" — 집중한도 초과" if new_exposure_pct > sector_limit_pct else ""),
    }


def evaluate_risk_gates(stock_code: str, side: str, qty: int, live_price: float, strategy_key: str | None = None,
                         strict_for_execution: bool = False,
                         decision_source: str = "preflight") -> dict:
    """A2 리스크 게이트 종합 판정. 매도는 리스크축소 행위이므로 신선도만 확인하고 나머지는 통과시킴
    (진입을 막는 게이트가 목적이지, 청산을 막으면 오히려 위험함).

    strict_for_execution(2026-07-23, 리뷰 발견 P1): 데이터가 없어 판단불가인 게이트는 기본적으로
    "통과"(ok=True)로 처리되는데, 이건 화면에 정보성으로 보여줄 땐 괜찮지만 실제 주문 체결
    경로에서는 반대여야 함 — 데이터가 없으면 모른다는 뜻이지 안전하다는 뜻이 아니기 때문.
    True로 호출하면(place_paper_order가 사용) 매수 시 데이터 판단불가 게이트가 하나라도 있으면
    BUY_ALLOWED를 WAIT_CONFIRM으로 강등한다. /risk-gates/check처럼 단순 조회용 호출은 기본값
    False로 기존 동작(정보성 표시) 유지."""
    c = _conn()
    try:
        order_krw = qty * live_price
        # P1 버그수정(2026-07-23, 리뷰 발견): 현금잔고만 쓰면 포지션을 많이 들수록 분모가
        # 부당하게 작아짐 — 현금+보유포지션 평가액(총자산) 기준으로 계산.
        total_capital = _total_equity(c)
        gates: dict[str, dict] = {"data_freshness": _gate_data_freshness(c, stock_code)}
        if side == "buy":
            gates["gap_risk"] = _gate_gap_risk(c, stock_code, live_price)
            gates["liquidity"] = _gate_liquidity(c, stock_code, order_krw)
            gates["dilution_risk"] = _gate_dilution_risk(c, stock_code)
            gates["flow_reversal"] = _gate_flow_reversal(c, stock_code)
            gates["market_regime"] = _gate_market_regime(c)
            gates["managed_issue"] = _gate_managed_issue(c, stock_code)
            gates["credit_surge"] = _gate_credit_surge(c, stock_code)
            gates["duplicate_order"] = _gate_duplicate_order(c, stock_code, strategy_key)
            gates["volatility_sizing"] = _gate_volatility_sizing(c, stock_code, order_krw, total_capital)
            gates["sector_concentration"] = _gate_sector_concentration(c, stock_code, order_krw, total_capital)

        reasons = [g["reason"] for g in gates.values() if not g["ok"]]

        if not gates["data_freshness"]["ok"]:
            decision = "BLOCKED_STALE_DATA"
        elif side == "buy" and (not gates["dilution_risk"]["ok"] or not gates["flow_reversal"]["ok"]
                                 or not gates["market_regime"]["ok"] or not gates["credit_surge"]["ok"]
                                 or not gates["managed_issue"]["ok"] or not gates["sector_concentration"]["ok"]):
            decision = "BLOCKED_RISK"
        elif side == "buy" and (not gates.get("gap_risk", {"ok": True})["ok"]
                                 or not gates.get("duplicate_order", {"ok": True})["ok"]):
            decision = "WAIT_CONFIRM"
        elif side == "buy" and (not gates.get("liquidity", {"ok": True})["ok"]
                                 or not gates.get("volatility_sizing", {"ok": True})["ok"]):
            decision = "SIZE_REDUCED"
        else:
            decision = "BUY_ALLOWED" if side == "buy" else "SELL_OK"

        if strict_for_execution and side == "buy" and decision == "BUY_ALLOWED":
            missing = [k for k, g in gates.items() if g.get("data_available") is False]
            if missing:
                decision = "WAIT_CONFIRM"
                reasons = reasons + [f"데이터 판단불가 게이트 {len(missing)}건({', '.join(missing)}) — 확인 필요"]

        snapshot = {"order_krw": round(order_krw, 0), "total_capital": round(total_capital, 0), "gates": gates}
        gate_decision_id = _insert_and_get_id(
            c,
            "INSERT INTO risk_gate_decisions(ts, stock_code, side, strategy_key, decision, reasons, gate_snapshot, order_id,decision_source) "
            "VALUES(?,?,?,?,?,?,?,NULL,?)",
            (_now_str(), stock_code, side, strategy_key, decision, json.dumps(reasons, ensure_ascii=False),
             json.dumps(snapshot, ensure_ascii=False), decision_source),
            "id",
        )
        c.commit()
        return {"decision": decision, "reasons": reasons, "gates": gates, "gate_decision_id": gate_decision_id}
    finally:
        c.close()


def authorize_strategy_order(
    stock_code: str,
    side: str,
    qty: int,
    price: float,
    strategy_key: str | None,
    *,
    decision_source: str,
) -> dict:
    """Universal fail-closed gateway for every strategy-originated order."""
    if not strategy_key:
        return {
            "decision": "BLOCKED_RISK",
            "reasons": ["strategy_key is required for strategy order authorization"],
            "gates": {},
            "gate_decision_id": None,
        }
    return evaluate_risk_gates(
        stock_code, side, qty, price, strategy_key,
        strict_for_execution=True, decision_source=decision_source,
    )


@router.get("/status")
def trading_status():
    return {
        "mode": _trading_mode(),
        "limits": _risk_limits(),
        "live_order_enabled": _trading_mode() == "LIVE" and os.getenv("KIS_LIVE_ORDER_ENABLE", "false").lower() == "true",
    }


@router.get("/account/summary")
def account_summary():
    snap = kis_client.get_account_snapshot() or {}
    holdings = snap.get("holdings") or []
    summary = snap.get("summary") or {}
    execs = kis_client.get_today_executions() or []
    acc_no = (getattr(config, "KIS_ACCOUNT_NO", "") or os.getenv("KIS_ACCOUNT_NO", "") or "").strip()
    acc_prod = (getattr(config, "KIS_ACCOUNT_PROD", "") or os.getenv("KIS_ACCOUNT_PROD", "") or "").strip()
    acc_masked = acc_no if len(acc_no) < 4 else ("*" * max(0, len(acc_no) - 4) + acc_no[-4:])
    return {
        "mode": _trading_mode(),
        "account_no": acc_no,
        "account_no_masked": acc_masked,
        "account_prod": acc_prod,
        "summary": summary,
        "holdings_count": len(holdings),
        "holdings": holdings,
        "today_executions": execs,
        "updated_at": _now_str(),
    }


def _cash_balance(c: sqlite3.Connection) -> float:
    r = c.execute("SELECT balance_after FROM live_cash_ledger ORDER BY id DESC LIMIT 1").fetchone()
    if r and r[0] is not None:
        return float(r[0])
    seed = float(os.getenv("KIS_PAPER_INITIAL_CASH", "100000000"))
    c.execute(
        "INSERT INTO live_cash_ledger(ts, mode, delta_krw, balance_after, reason, ref_order_id) VALUES(?,?,?,?,?,NULL)",
        (_now_str(), "PAPER", seed, seed, "seed"),
    )
    return seed


def _total_equity(c: sqlite3.Connection) -> float:
    """P1 버그수정(2026-07-23, 리뷰 발견): 리스크게이트의 자본기준이 현금잔고만 보고 있어서
    포지션을 많이 들고 있을수록(현금은 줄고 자산은 그대로거나 늘어도) 리스크한도·섹터비중
    계산이 왜곡됨(총자산이 실제보다 작게 잡혀 한도가 부당하게 좁아짐). 현금+보유포지션
    평가액 합계로 계산."""
    cash = _cash_balance(c)
    positions = c.execute("SELECT stock_code, qty, avg_price FROM kis_paper_positions").fetchall()
    if not positions:
        return cash
    eval_total = 0.0
    for p_code, p_qty, p_avg in positions:
        px = _latest_price(p_code)
        eval_total += p_qty * (px if px else p_avg)
    return cash + eval_total


def _write_cash_ledger(c: sqlite3.Connection, delta_krw: float, reason: str, ref_order_id: int) -> float:
    balance = _cash_balance(c) + delta_krw
    c.execute(
        "INSERT INTO live_cash_ledger(ts, mode, delta_krw, balance_after, reason, ref_order_id) VALUES(?,?,?,?,?,?)",
        (_now_str(), "PAPER", delta_krw, balance, reason, ref_order_id),
    )
    return balance


def _write_order_lifecycle(c: sqlite3.Connection, o: "PaperOrderIn", fill_price: float, order_krw: float,
                            decision_reason: str, *, write_cash_ledger: bool = True) -> int:
    now = _now_str()
    lo_id = _insert_and_get_id(
        c,
        "INSERT INTO live_orders(parent_order_id, mode, strategy_key, stock_code, side, order_type, qty, "
        "limit_price, status, filled_qty, avg_fill_price, reject_reason, decision_reason, created_at, updated_at) "
        "VALUES(NULL,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?)",
        ("PAPER", o.strategy_key, o.stock_code, o.side, "limit" if o.limit_price else "market", o.qty,
         o.limit_price, "FILLED", o.qty, fill_price, decision_reason, now, now),
        "order_id",
    )
    c.execute(
        "INSERT INTO live_order_events(order_id, event_ts, event_type, qty_delta, price, detail) VALUES(?,?,?,?,?,?)",
        (lo_id, now, "SUBMITTED", o.qty, fill_price, json.dumps({"order_krw": round(order_krw, 0)})),
    )
    c.execute(
        "INSERT INTO live_order_events(order_id, event_ts, event_type, qty_delta, price, detail) VALUES(?,?,?,?,?,?)",
        (lo_id, now, "FILLED", o.qty, fill_price, None),
    )
    c.execute(
        "INSERT INTO live_fills(order_id, fill_ts, fill_qty, fill_price, cumulative_qty) VALUES(?,?,?,?,?)",
        (lo_id, now, o.qty, fill_price, o.qty),
    )
    if write_cash_ledger:
        delta = -order_krw if o.side == "buy" else order_krw
        _write_cash_ledger(c, delta, f"{o.side}:{o.stock_code}", lo_id)
    return lo_id


def record_strategy_paper_fill(
    c,
    *,
    stock_code: str,
    side: str,
    qty: int,
    fill_price: float,
    strategy_key: str,
    source_ref: str,
) -> int:
    """Mirror a strategy-owned paper fill into the standard order lifecycle.

    Strategy Center accounts keep separate cash and positions in the virtual
    ledger.  Therefore this intentionally does not alter `kis_paper_positions`
    or the KIS-paper cash ledger; it supplies the auditable submitted/filled
    event trail needed for forward validation without mixing portfolios.
    """
    order = PaperOrderIn(
        stock_code=str(stock_code), side=str(side).lower(), qty=int(qty),
        strategy_key=str(strategy_key),
    )
    return _write_order_lifecycle(
        c, order, float(fill_price), float(fill_price) * int(qty),
        decision_reason=f"strategy_virtual_ledger:{source_ref}",
        write_cash_ledger=False,
    )


@router.get("/risk-gates/check")
def risk_gates_check(stock_code: str = Query(..., min_length=6, max_length=6),
                      side: str = Query(..., pattern="^(buy|sell)$"),
                      qty: int = Query(..., ge=1),
                      strategy_key: str | None = Query(default=None)):
    """실제 주문 없이 리스크 게이트만 사전 점검(전략이 매수 전에 미리 확인하는 용도)."""
    px = _latest_price(stock_code)
    if not px:
        raise HTTPException(status_code=400, detail="현재가 조회 실패")
    return evaluate_risk_gates(stock_code, side, qty, px, strategy_key, decision_source="api_preflight")


@router.get("/live/data-preflight")
def live_data_preflight(
    stock_code: str = Query(..., min_length=6, max_length=6),
    side: str = Query(..., pattern="^(buy|sell)$"),
    qty: int = Query(..., ge=1),
    strategy_key: str | None = Query(default=None),
):
    """Refresh broker evidence and run the fail-closed live data contract without ordering."""
    quote = kis_client.get_current_price(stock_code) or {}
    price = float(quote.get("close") or 0)
    if price <= 0:
        raise HTTPException(status_code=503, detail="KIS 현재가 원천을 확인할 수 없어 실전 점검을 차단했습니다")
    orderbook = kis_client.get_orderbook(stock_code) or {}
    record_execution_snapshot(stock_code, quote, orderbook)
    return evaluate_live_data_contract(stock_code, side, strategy_key, qty * price)


@router.get("/risk-gates/recent")
def risk_gates_recent(limit: int = Query(100, ge=1, le=1000),
                       decision: str | None = Query(default=None)):
    c = _conn()
    if decision:
        rows = c.execute(
            "SELECT * FROM risk_gate_decisions WHERE decision=? ORDER BY id DESC LIMIT ?", (decision, limit)
        ).fetchall()
    else:
        rows = c.execute("SELECT * FROM risk_gate_decisions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("reasons", "gate_snapshot"):
            if d.get(k):
                try:
                    d[k] = json.loads(d[k])
                except Exception:
                    pass
        out.append(d)
    return out


@router.get("/orders/lifecycle")
def orders_lifecycle(limit: int = Query(100, ge=1, le=1000)):
    c = _conn()
    rows = c.execute(
        "SELECT * FROM live_orders ORDER BY order_id DESC LIMIT ?", (limit,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


@router.get("/orders/{order_id}")
def order_detail(order_id: int):
    c = _conn()
    order = c.execute("SELECT * FROM live_orders WHERE order_id=?", (order_id,)).fetchone()
    if not order:
        c.close()
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다")
    events = c.execute(
        "SELECT * FROM live_order_events WHERE order_id=? ORDER BY id", (order_id,)
    ).fetchall()
    fills = c.execute(
        "SELECT * FROM live_fills WHERE order_id=? ORDER BY id", (order_id,)
    ).fetchall()
    c.close()
    return {"order": dict(order), "events": [dict(e) for e in events], "fills": [dict(f) for f in fills]}


@router.get("/lifecycle/reconciliation")
def orders_reconciliation(limit: int = Query(1000, ge=1, le=5000)):
    """Read-only lifecycle integrity check for paper and future broker orders."""
    c = _conn()
    try:
        orders = c.execute(
            "SELECT order_id,status,qty,filled_qty FROM live_orders ORDER BY order_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        mismatches = []
        for order in orders:
            fill = c.execute(
                "SELECT COALESCE(SUM(fill_qty),0),COALESCE(MAX(cumulative_qty),0) "
                "FROM live_fills WHERE order_id=?",
                (order["order_id"],),
            ).fetchone()
            fill_sum, cumulative = int(fill[0] or 0), int(fill[1] or 0)
            expected = int(order["filled_qty"] or 0)
            status = str(order["status"] or "")
            reason = None
            if fill_sum != expected or cumulative != expected:
                reason = "fill_total_mismatch"
            elif status == "FILLED" and expected != int(order["qty"]):
                reason = "filled_status_qty_mismatch"
            elif expected > int(order["qty"]):
                reason = "overfill"
            if reason:
                mismatches.append({
                    "order_id": int(order["order_id"]), "status": status,
                    "qty": int(order["qty"]), "filled_qty": expected,
                    "fill_sum": fill_sum, "cumulative_qty": cumulative,
                    "reason": reason,
                })
        orphan_fills = int(c.execute(
            "SELECT COUNT(*) FROM live_fills f LEFT JOIN live_orders o ON o.order_id=f.order_id "
            "WHERE o.order_id IS NULL"
        ).fetchone()[0])
        terminal = {"FILLED", "REJECTED", "CANCELLED"}
        open_count = sum(1 for order in orders if str(order["status"] or "") not in terminal)
        return {
            "ok": not mismatches and orphan_fills == 0,
            "orders_checked": len(orders),
            "open_orders": open_count,
            "orphan_fills": orphan_fills,
            "mismatches": mismatches[:100],
        }
    finally:
        c.close()


@router.get("/cash-ledger")
def cash_ledger(limit: int = Query(100, ge=1, le=1000)):
    c = _conn()
    rows = c.execute("SELECT * FROM live_cash_ledger ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    balance = rows[0]["balance_after"] if rows else _cash_balance(c)
    c.close()
    return {"current_balance": balance, "entries": [dict(r) for r in rows]}


@router.post("/paper/order")
def place_paper_order(o: PaperOrderIn):
    if _trading_mode() != "PAPER":
        raise HTTPException(status_code=400, detail="PAPER 모드가 아닙니다. KIS_TRADING_MODE=PAPER 확인 필요")

    px = _latest_price(o.stock_code)
    if not px:
        raise HTTPException(status_code=400, detail="현재가 조회 실패")

    fill_price = round_to_tick_size(float(o.limit_price) if o.limit_price else px)

    # A2: 리스크 게이트 평가(매수만 진입 게이트 적용, 매도는 신선도만 확인)
    gate = authorize_strategy_order(
        o.stock_code, o.side, o.qty, fill_price, o.strategy_key or "manual_paper_order",
        decision_source="paper_order_execution",
    )
    qty = o.qty
    if gate["decision"] == "BLOCKED_STALE_DATA":
        raise HTTPException(status_code=400, detail=f"데이터 신선도 미달로 차단: {'; '.join(gate['reasons'])}")
    if gate["decision"] == "BLOCKED_RISK":
        raise HTTPException(status_code=400, detail=f"리스크 게이트 차단: {'; '.join(gate['reasons'])}")
    if gate["decision"] == "WAIT_CONFIRM" and not o.override_wait_confirm:
        raise HTTPException(
            status_code=409,
            detail=f"확인 필요(갭 리스크 또는 데이터 판단불가) — override_wait_confirm=true로 재요청하면 진행됩니다: {'; '.join(gate['reasons'])}",
        )
    if gate["decision"] == "SIZE_REDUCED":
        candidates_krw = []
        adv = gate["gates"].get("liquidity", {}).get("adv_20d")
        if adv:
            candidates_krw.append(adv * 0.03)
        vol_max = gate["gates"].get("volatility_sizing", {}).get("max_order_krw")
        if vol_max:
            candidates_krw.append(vol_max)
        max_krw = min(candidates_krw) if candidates_krw else fill_price * qty
        reduced = int(max_krw // fill_price)
        if reduced < 1:
            raise HTTPException(status_code=400, detail=f"유동성/리스크한도 부족으로 최소수량도 체결 불가: {'; '.join(gate['reasons'])}")
        qty = min(qty, reduced)

    order_krw = fill_price * qty
    limits = _risk_limits()

    c = _conn()
    try:
        # 리스크: 주문당 금액 한도
        if order_krw > limits["max_order_krw"]:
            raise HTTPException(status_code=400, detail=f"주문한도 초과: {order_krw:,.0f} > {limits['max_order_krw']:,.0f}")

        # 리스크: 일일 손실 한도
        daily_loss = _today_realized_loss(c)
        if daily_loss >= limits["max_daily_loss_krw"]:
            raise HTTPException(status_code=400, detail="일일 손실 한도 초과로 주문 차단")

        pos = c.execute("SELECT qty, avg_price FROM kis_paper_positions WHERE stock_code=?", (o.stock_code,)).fetchone()

        if o.side == "buy":
            # P0 버그수정(2026-07-23, 리뷰 발견): 현금 잔고 확인 없이 매수를 체결하고 있었음 —
            # 특히 같은 종목 추가매수는 max_position_count 체크도 피해서 계속 매수 가능해
            # live_cash_ledger가 음수로 갈 수 있었음. 체결 직전 반드시 현금 확인.
            cash_now = _cash_balance(c)
            if cash_now < order_krw:
                raise HTTPException(
                    status_code=400,
                    detail=f"현금 부족: 필요 {order_krw:,.0f}원 > 보유현금 {cash_now:,.0f}원",
                )
            pos_cnt = c.execute("SELECT COUNT(*) FROM kis_paper_positions WHERE qty>0").fetchone()[0]
            if not pos and pos_cnt >= limits["max_position_count"]:
                raise HTTPException(status_code=400, detail="최대 보유 종목 수 한도 초과")

            if pos:
                old_qty = int(pos[0]); old_avg = float(pos[1])
                new_qty = old_qty + qty
                new_avg = ((old_qty * old_avg) + (qty * fill_price)) / new_qty
                c.execute(
                    "UPDATE kis_paper_positions SET qty=?, avg_price=?, updated_at=? WHERE stock_code=?",
                    (new_qty, new_avg, _now_str(), o.stock_code),
                )
            else:
                c.execute(
                    "INSERT INTO kis_paper_positions(stock_code, qty, avg_price, updated_at) VALUES(?,?,?,?)",
                    (o.stock_code, qty, fill_price, _now_str()),
                )

        else:  # sell
            if not pos or int(pos[0]) < qty:
                raise HTTPException(status_code=400, detail="매도수량이 보유수량보다 큽니다")
            old_qty = int(pos[0]); old_avg = float(pos[1])
            pnl = (fill_price - old_avg) * qty
            remain = old_qty - qty
            if remain == 0:
                c.execute("DELETE FROM kis_paper_positions WHERE stock_code=?", (o.stock_code,))
            else:
                c.execute(
                    "UPDATE kis_paper_positions SET qty=?, updated_at=? WHERE stock_code=?",
                    (remain, _now_str(), o.stock_code),
                )
            c.execute(
                "INSERT INTO kis_paper_realized(ts, stock_code, qty, entry_price, exit_price, pnl) VALUES(?,?,?,?,?,?)",
                (_now_str(), o.stock_code, qty, old_avg, fill_price, pnl),
            )

        oid = _insert_and_get_id(
            c,
            "INSERT INTO kis_paper_orders(ts, stock_code, side, qty, req_price, fill_price, status, reason, order_krw, mode) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (_now_str(), o.stock_code, o.side, qty, o.limit_price, fill_price, "FILLED", "paper", order_krw, "PAPER"),
            "id",
        )

        # A1: 병행 기록 — 실전형 주문 생애주기 테이블에도 동일 체결을 기록(하위호환 유지, 신규 부가)
        o_adj = o.model_copy(update={"qty": qty})
        lifecycle_id = _write_order_lifecycle(
            c, o_adj, fill_price, order_krw,
            decision_reason=f"gate={gate['decision']}" + (f", qty축소({o.qty}->{qty})" if qty != o.qty else ""),
        )
        c.execute("UPDATE risk_gate_decisions SET order_id=? WHERE id=?",
                   (lifecycle_id, gate["gate_decision_id"]))

        c.commit()
        return {
            "ok": True,
            "order_id": oid,
            "lifecycle_order_id": lifecycle_id,
            "stock_code": o.stock_code,
            "side": o.side,
            "qty": qty,
            "qty_requested": o.qty,
            "fill_price": round(fill_price, 2),
            "order_krw": round(order_krw, 0),
            "risk_gate": gate["decision"],
        }
    finally:
        c.close()


@router.get("/paper/orders")
def paper_orders(limit: int = Query(100, ge=1, le=1000)):
    c = _conn()
    rows = c.execute(
        "SELECT * FROM kis_paper_orders ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


@router.get("/paper/positions")
def paper_positions():
    c = _conn()
    pos = c.execute("SELECT * FROM kis_paper_positions ORDER BY stock_code").fetchall()
    out = []
    total_eval = 0.0
    total_cost = 0.0
    for r in pos:
        code = r["stock_code"]
        qty = int(r["qty"])
        avg = float(r["avg_price"])
        px = _latest_price(code) or avg
        eval_ = qty * px
        cost_ = qty * avg
        total_eval += eval_
        total_cost += cost_
        out.append({
            "stock_code": code,
            "qty": qty,
            "avg_price": round(avg, 2),
            "current_price": round(px, 2),
            "unrealized_pnl": round(eval_ - cost_, 0),
            "unrealized_pct": round(((px - avg) / avg * 100.0), 2) if avg else 0.0,
            "updated_at": r["updated_at"],
        })
    c.close()
    return {
        "positions": out,
        "summary": {
            "position_count": len(out),
            "total_eval": round(total_eval, 0),
            "total_cost": round(total_cost, 0),
            "total_unrealized_pnl": round(total_eval - total_cost, 0),
        },
    }


@router.get("/paper/pnl")
def paper_pnl():
    c = _conn()
    d = datetime.now().strftime("%Y-%m-%d")
    daily = c.execute(
        "SELECT COALESCE(SUM(pnl),0) s FROM kis_paper_realized WHERE ts LIKE ?",
        (f"{d}%",),
    ).fetchone()[0]
    total = c.execute("SELECT COALESCE(SUM(pnl),0) s FROM kis_paper_realized").fetchone()[0]
    c.close()
    return {"daily_realized_pnl": round(float(daily or 0), 0), "total_realized_pnl": round(float(total or 0), 0)}


@router.post('/live/order')
def live_order_blocked():
    # 실전주문은 명시적 개발/승인 전까지 강제 차단
    raise HTTPException(status_code=403, detail='LIVE 주문은 현재 차단 상태입니다. 승인된 절차로만 활성화하세요.')
