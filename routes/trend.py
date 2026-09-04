"""
routes/trend.py — 스탁이지 가상매매 + AI 자동매매 API

  GET  /api/trend/holdings
  POST /api/trend/buy
  POST /api/trend/update
  POST /api/trend/sell
  GET  /api/trend/trades
  GET  /api/trend/summary
  GET  /api/trend/ai-holdings
  POST /api/trend/ai-combo/execute
  DELETE /api/trend/trades/all
"""

import sqlite3 as _sl
import logging
import json
import time
import math
import threading
from datetime import date as _date, datetime as _dt, timedelta as _timedelta
from statistics import mean
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from db_utils import STOCK_DB_PATH, connect_stock_db
from virtual_trading_ledger import account_summary, available_cash, record_trade

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = str(STOCK_DB_PATH)
V18_STRATEGY = "gpt_v18"
TURNOVER_STRATEGY = "turnover_100m"
TURNOVER_AUTO_STRATEGY = "turnover_auto_100m"
STRATEGY_ALIASES = {
    "golden_cross": "v_gc",
    "recovery": "v_recovery",
    "contract_momentum": "v_contract_momentum",
}
V18_TICKET_KRW   = 12_000_000     # 종목당 1회 진입금액 (1,200만원)
VIRTUAL_CAPITAL  = 100_000_000    # 총 가상 예산 (1억원)
CASH_RESERVE_PCT = 0.20           # 최소 현금 보유 비율 (20% = 2,000만원 상시 예수금)
# 최대 투자 가능 금액 = 1억 × 80% = 8,000만원
# 1,200만원 티켓 기준 최대 동시 보유 티켓 수 ≈ 6개
# → 종목당 max=2이더라도 예산 소진 시 추가 매수 불가

PYRAMID_MIN_DAYS = 2  # 동일 종목 2번째 티켓은 1번째 매수 후 최소 N일 이후에만 허용
# (백테스트: 2nd 티켓은 다음 신호 발생일에 진입 → 즉시 전량 매수 방지)

# ── V12 골든크로스 전략 설정 (백테스트 avg6=+47.6%, 6/6기간 양수) ──
GC_STRATEGY      = "v_gc"
GC_TICKET_KRW    = 10_000_000    # 종목당 1,000만원
GC_CAPITAL_KRW   = 100_000_000   # 총 가상 예산 1억원
GC_CASH_RESERVE  = 0.20          # 최소 현금 20%
GC_MAX_POSITIONS = 8             # 최대 동시보유 8종목 (8×1,000만=8,000만원)
GC_CROSS_DAYS    = 15            # MA20이 MA60을 상향돌파한 기간 이내
GC_VOL_RATIO     = 1.2           # 거래량 필터: 5일평균 vs 20일평균
GC_RS6M_MIN      = -20.0         # KOSPI 대비 6개월 RS 하한
GC_MIN_MKTCAP    = 4000          # 최소 시총 (억원) — 중대형주 필터. 2026-08-10: 2000→4000
                                  # (backtest.py run_backtest_golden_cross와 동일, 6기간 avg6
                                  # 25.28%→35.48% 개선 채택, CLAUDE.md 2026-08-10 참조)
GC_TRAIL_PCT     = -0.25         # 트레일 손절 (이익 5%+ 발동)
GC_TRAIL_BIG     = -0.30         # 트레일 손절 (이익 50%+ 발동)
GC_STOP_PCT      = -0.12         # 하드스탑 -12%
GC_MAX_HOLD_DAYS = 300           # 최대 보유일
GC_COOLDOWN_DAYS = 10            # 매도 후 재매수 금지 기간
_gc_cache: dict = {"updated_at": None, "data": {}}
_GC_CACHE_TTL_SEC = 600

# ── V-RECOVERY 낙폭과대 반등 가상매매 (2026-07-12, backtest run_backtest_recovery 채택 로직과 일치) ──
REC_STRATEGY      = "v_recovery"
REC_TICKET_KRW    = 10_000_000   # 종목당 1,000만원
REC_CAPITAL_KRW   = 100_000_000  # 총 가상 예산 1억원
REC_CASH_RESERVE  = 0.20         # 최소 현금 20%
REC_MAX_POSITIONS = 10
REC_STOP_PCT      = -0.12        # 하드스탑
REC_TRAIL_PCT     = -0.20        # 추적손절 (수익 무관 상시)
REC_TRAIL_BIG     = -0.25        # +50% 이상 수익 구간 추적손절 완화
REC_TP_PCT        = 0.80         # 익절 +80%
REC_MAX_HOLD_DAYS = 240
REC_MIN_MKTCAP    = 200          # 억원
REC_DEPTH_MIN     = -0.20        # MA60 대비 낙폭 상한 (이보다 얕으면 제외)
REC_DEPTH_MAX     = -0.65        # 낙폭 하한 (너무 깊으면 제외)
REC_LOW_MAX_PCT   = 40.0         # 52주 저점 대비 +40% 이내
REC_VOL_RATIO     = 2.0          # 당일 거래량 ≥ 20일 평균 × 2.0
REC_TA_BONUS      = 20.0         # 직전 공시분기 첫 흑자전환 랭킹 보너스 (2026-07-12 백테스트 채택)
REC_FLOW_BONUS    = 20.0         # 기관+외인 5일 순매수 양수 랭킹 보너스 (2026-07-12 채택: avg6 +29.5%)
REC_COOLDOWN_DAYS = 10
REC_MAX_BUYS_PER_RUN = 3         # 백테스트 top3/일과 일치
_rec_cache: dict = {"updated_at": None, "data": {}}
_REC_CACHE_TTL_SEC = 600

_v18_cache: dict = {"updated_at": None, "data": {"buy_candidates": [], "sell_candidates": [], "summary": {}}}
_V18_CACHE_TTL_SEC = 600
_turnover_auto_state: dict = {
    "running": False,
    "interval_sec": 300,
    "thread_name": None,
    "last_run_at": None,
    "last_result": None,
    "last_error": None,
}
_turnover_auto_lock = threading.Lock()


def _db():
    return connect_stock_db(timeout=30)


def _paper_buy_gate(stock_code: str, strategy: str, qty: int, price: float) -> dict:
    """Use the same fail-closed gate as the order API before every paper buy."""
    if len(str(stock_code or "")) != 6 or qty <= 0 or price <= 0:
        return {"decision": "BLOCKED_RISK", "reasons": ["invalid paper order inputs"]}
    from routes.kis_trading import authorize_strategy_order

    return authorize_strategy_order(
        str(stock_code), "buy", int(qty), float(price), strategy,
        decision_source="strategy_virtual_execution",
    )


def _investable_cash(conn, strategy: str, seed: float, reserve_pct: float) -> float:
    balance = available_cash(conn, strategy, seed)
    return max(0.0, balance - seed * reserve_pct)


def _record_paper_trade(
    conn, *, strategy: str, side: str, code: str, name: str, holding_id: int | None,
    qty: int, price: float, trade_id: int, occurred_at: str, gross_profit: float = 0.0,
) -> dict:
    result = record_trade(
        conn, strategy=strategy, initial_cash=100_000_000.0, side=side,
        stock_code=code, stock_name=name, holding_id=holding_id,
        quantity=qty, price=price, ref_key=f"peak_trade:{trade_id}",
        occurred_at=occurred_at, gross_profit=gross_profit,
    )
    # Keep the strategy-specific cash ledger authoritative, while mirroring the
    # fill into the shared lifecycle trail for reconciliation and forward tests.
    from routes.kis_trading import record_strategy_paper_fill

    record_strategy_paper_fill(
        conn, stock_code=code, side=side, qty=qty, fill_price=price,
        strategy_key=strategy, source_ref=f"peak_trade:{trade_id}",
    )
    return result


def _ensure_virtual_strategy_runs(conn) -> None:
    """Keep an explicit run heartbeat even when a strategy has no trades."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS virtual_strategy_runs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               strategy TEXT NOT NULL,
               run_at TEXT NOT NULL,
               status TEXT NOT NULL,
               sold_count INTEGER NOT NULL DEFAULT 0,
               bought_count INTEGER NOT NULL DEFAULT 0,
               message TEXT
           )"""
    )
    conn.execute(
        """CREATE INDEX IF NOT EXISTS ix_virtual_strategy_runs_strategy_time
           ON virtual_strategy_runs(strategy, run_at DESC)"""
    )
    conn.commit()


def _record_virtual_strategy_run(strategy: str, status: str, *, sold: int = 0, bought: int = 0, message: str = "") -> None:
    conn = _db()
    try:
        _ensure_virtual_strategy_runs(conn)
        conn.execute(
            """INSERT INTO virtual_strategy_runs
               (strategy,run_at,status,sold_count,bought_count,message)
               VALUES (?,CURRENT_TIMESTAMP,?,?,?,?)""",
            (strategy, status, int(sold), int(bought), message[:1000] or None),
        )
        conn.commit()
    finally:
        conn.close()


@router.get("/performance")
def get_virtual_performance():
    """Actual paper-account performance and scheduler health by strategy."""
    conn = _db()
    try:
        _ensure_virtual_strategy_runs(conn)
        accounts = conn.execute(
            """SELECT strategy,initial_cash,balance_krw,updated_at
               FROM virtual_cash_accounts"""
        ).fetchall()
        active_rows = conn.execute(
            """SELECT strategy,stock_code,quantity
               FROM peak_holding WHERE is_active=1"""
        ).fetchall()
        active_value: dict[str, float] = {}
        active_count: dict[str, int] = {}
        for strategy, code, quantity in active_rows:
            price, _prev, _as_of, currency = _latest_price_pair(conn, str(code or ""))
            if currency != "KRW" or not price:
                continue
            active_value[strategy] = active_value.get(strategy, 0.0) + float(price) * float(quantity or 0)
            active_count[strategy] = active_count.get(strategy, 0) + 1
        runs = conn.execute(
            """SELECT strategy,run_at,status,sold_count,bought_count,message
               FROM (
                   SELECT strategy,run_at,status,sold_count,bought_count,message,
                          ROW_NUMBER() OVER (PARTITION BY strategy ORDER BY run_at DESC,id DESC) AS rn
                   FROM virtual_strategy_runs
               ) latest WHERE rn=1"""
        ).fetchall()
        run_map = {str(r[0]): r for r in runs}
        result = {}
        for strategy, initial_cash, balance, updated_at in accounts:
            key = str(strategy)
            initial = float(initial_cash or 0)
            equity = float(balance or 0) + active_value.get(key, 0.0)
            run = run_map.get(key)
            result[key] = {
                "initial_cash": round(initial),
                "cash": round(float(balance or 0)),
                "equity": round(equity),
                "return_pct": round((equity / initial - 1) * 100, 2) if initial else None,
                "active_positions": active_count.get(key, 0),
                "account_updated_at": updated_at,
                "last_run_at": run[1] if run else None,
                "last_run_status": run[2] if run else "unknown",
                "last_sold": int(run[3] or 0) if run else 0,
                "last_bought": int(run[4] or 0) if run else 0,
                "last_message": run[5] if run else None,
            }
        return {"ok": True, "strategies": result}
    finally:
        conn.close()


@router.get("/ledger/{strategy}")
def get_virtual_ledger(strategy: str, limit: int = Query(100, ge=1, le=1000)):
    conn = _db()
    try:
        summary = account_summary(conn, strategy)
        rows = conn.execute(
            """SELECT event_type,stock_code,stock_name,quantity,price,gross_amount,
                      fee,tax,slippage_cost,cash_delta,balance_after,
                      realized_pnl_gross,realized_pnl_net,occurred_at,ref_key
               FROM virtual_cash_ledger WHERE strategy=?
               ORDER BY occurred_at DESC,id DESC LIMIT ?""",
            (strategy, limit),
        ).fetchall()
        keys = (
            "event_type", "stock_code", "stock_name", "quantity", "price", "gross_amount",
            "fee", "tax", "slippage_cost", "cash_delta", "balance_after",
            "realized_pnl_gross", "realized_pnl_net", "occurred_at", "ref_key",
        )
        return {"strategy": strategy, "summary": summary, "entries": [dict(zip(keys, row)) for row in rows]}
    finally:
        conn.close()


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _normalize_strategy_key(strategy: str | None) -> str | None:
    if not strategy:
        return None
    key = str(strategy).strip()
    return STRATEGY_ALIASES.get(key, key)


def _is_kr_market_open(now: _dt | None = None) -> bool:
    t = now or _dt.now()
    if t.weekday() >= 5:
        return False
    cur = t.hour * 60 + t.minute
    return (9 * 60) <= cur <= (15 * 60 + 30)


def _latest_price_and_ma(conn, stock_code: str, ma_days: int = 60) -> tuple[float, float, float]:
    rows = conn.execute(
        "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT ?",
        (stock_code, ma_days),
    ).fetchall()
    closes = [_safe_float(r[0]) for r in rows if r and r[0] is not None]
    if not closes:
        return 0.0, 0.0, 0.0
    cur = closes[0]
    # 키움 실시간 스냅샷이 2분 내 있으면 현재가를 우선 사용 (장중 매도 신호 지연 완화)
    try:
        rt = conn.execute(
            """
            SELECT last_price
            FROM kiwoom_realtime_quote
            WHERE stock_code=?
              AND last_price>0
              AND datetime(updated_at) >= datetime('now', '-120 seconds', 'localtime')
            ORDER BY datetime(updated_at) DESC
            LIMIT 1
            """,
            (stock_code,),
        ).fetchone()
        if rt and rt[0]:
            cur = _safe_float(rt[0])
    except Exception:
        # 테이블 미생성/초기상태에서는 무시하고 일별 종가 fallback
        pass
    ma20 = mean(closes[:20]) if len(closes) >= 20 else mean(closes)
    ma60 = mean(closes[:60]) if len(closes) >= 60 else mean(closes)
    return cur, ma20, ma60


def _is_us_ticker(code: str | None) -> bool:
    c = str(code or "").strip().upper()
    return bool(c) and not (len(c) == 6 and c.isdigit())


def _latest_price_pair(conn, code: str) -> tuple[float | None, float | None, str | None, str]:
    """Return latest/previous close for KR or US symbols.

    The StockEasy/virtual-trading tables historically stored only Korean 6-digit
    stock codes. US virtual holdings introduced ticker symbols, so current-price
    refresh must branch by market instead of querying `price_history` blindly.
    """
    c = str(code or "").strip().upper()
    if not c:
        return None, None, None, "KRW"
    if _is_us_ticker(c):
        rows = conn.execute(
            "SELECT close, date FROM us_price_history WHERE ticker=? AND close>0 ORDER BY date DESC LIMIT 2",
            (c,),
        ).fetchall()
        if not rows:
            return None, None, None, "USD"
        cur = float(rows[0][0])
        prev = float(rows[1][0]) if len(rows) > 1 and rows[1][0] else cur
        return cur, prev, str(rows[0][1])[:10], "USD"
    rows = conn.execute(
        "SELECT close, date FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 2",
        (c,),
    ).fetchall()
    if not rows:
        return None, None, None, "KRW"
    cur = float(rows[0][0])
    prev = float(rows[1][0]) if len(rows) > 1 and rows[1][0] else cur
    return cur, prev, str(rows[0][1])[:10], "KRW"


# ── v_anchor 현재 유니버스 (2025 H2 시총 상위 대형주) ──────────────────────
V_ANCHOR_UNIVERSE = [
    "005930",  # 삼성전자
    "000660",  # SK하이닉스
    "005380",  # 현대차
    "012450",  # 한화에어로스페이스
    "105560",  # KB금융
    "267260",  # HD현대일렉트릭
    "035420",  # NAVER
]
V_ANCHOR_MA_WINDOW = 60      # KOSPI MA60 기준 (일별 종가)
V_ANCHOR_BREAK_DAYS = 3      # MA 하향 N일 연속 시 청산 신호
V_ANCHOR_COOLDOWN_DAYS = 5   # 매도 후 재매수 금지 기간 (영업일 기준 달력 7일)
COMBO_COOLDOWN_DAYS = 3      # combo 종목 매도 후 재매수 금지 기간

# ── 피라미딩 설정 (백테스트 검증: max=2가 max=1 대비 +43%p 개선, MDD 동일) ──
# 비중이 작은 종목이라도 스코어 최고면 추가 매수 허용
MAX_TICKETS_V_ANCHOR = 2     # v_anchor 대형주: 최대 2티켓 (1,200만원×2=2,400만원)
MAX_TICKETS_COMBO    = 2     # combo 종목: 최대 2티켓

# ── 회전율 단타(가상매매) 전략 설정 ─────────────────────────────
TURNOVER_CAPITAL_KRW = 100_000_000   # 1억원
TURNOVER_TICKET_KRW  = 10_000_000    # 종목당 1,000만원
TURNOVER_MAX_POS     = 10            # 최대 동시보유 10종목
TURNOVER_STOP_PCT    = -10.0         # 손절 -10%
TURNOVER_TP1_PCT     = 10.0          # 익절1 +10%
TURNOVER_TP2_PCT     = 40.0          # 익절2 +40% (상승장 수익 확장)
TURNOVER_TRAIL_AFTER_TP1_PCT = 12.0  # TP1 이후 피크대비 -12% 이탈 시 잔여 청산 (추세추종 강화)
TURNOVER_BREAKEVEN_AFTER_TP1_PCT = 0.0  # TP1 이후 본전 미만 복귀 시 잔여 청산
TURNOVER_TIME_STOP_DAYS = 20         # 최대 보유일 (상승장 추세 보유 확장)
TURNOVER_TIME_STOP_MIN_PROFIT_PCT = 0.0  # 최대 보유일 경과 시 최소수익 미달이면 청산


def _check_kospi_vs_ma60(conn) -> tuple[bool, float, float]:
    """KOSPI 일별 종가 vs MA60 비교. returns (above_ma, close, ma60)
    ※ 실시간 가격이 아닌 DB의 마지막 일별 종가 사용 → 장중 변동성 무시
    """
    rows = conn.execute(
        "SELECT date, close FROM price_history "
        "WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT 90"
    ).fetchall()
    # 장중 같은날 row가 여러개면 최신 1개만 (date 기준 distinct)
    seen, closes = set(), []
    for r in rows:
        d = r[0][:10]
        if d not in seen:
            seen.add(d)
            closes.append(_safe_float(r[1]))
    if len(closes) < 20:
        return True, 0.0, 0.0
    cur = closes[0]
    ma60 = sum(closes[:60]) / min(len(closes), 60)
    return cur > ma60, cur, ma60


def _count_kospi_below_ma60_days(conn) -> int:
    """일별 종가 기준으로 KOSPI가 MA60 아래였던 연속 일수 계산."""
    rows = conn.execute(
        "SELECT date, close FROM price_history "
        "WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT 120"
    ).fetchall()
    seen, closes = set(), []
    for r in rows:
        d = r[0][:10]
        if d not in seen:
            seen.add(d)
            closes.append(_safe_float(r[1]))
    if len(closes) < 60:
        return 0
    break_days = 0
    for i in range(len(closes)):
        if i + 60 > len(closes):
            break
        ma = sum(closes[i:i+60]) / 60
        if closes[i] < ma:
            break_days += 1
        else:
            break
    return break_days


GLOBAL_HARD_STOP_PCT = -10.0   # 전 전략 공통 하드스탑 (V18 전략은 자체 로직 우선)

def _auto_hardstop_all_strategies(conn) -> int:
    """
    gpt_v18 이외 전략(ai_combo, peak, value 등) 포지션에 대해
    price_history 최신가 기준 하드스탑(-10%) 자동 실행.
    → peak_monitor/StockEasy 편출 신호를 기다리지 않고 즉시 손절.
    반환: 손절 실행 건수
    """
    from datetime import datetime as _now_dt
    now_ts = _now_dt.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = conn.execute("""
        SELECT ph.id, ph.stock_code, ph.stock_name, ph.buy_price, ph.quantity, ph.strategy
        FROM peak_holding ph
        WHERE ph.is_active = 1
          AND ph.strategy != 'gpt_v18'
    """).fetchall()

    sold = 0
    for h_id, code, name, buy_price, qty, strategy in rows:
        price_row = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
            (code,)
        ).fetchone()
        if not price_row:
            continue
        cur = float(price_row[0])
        bp  = float(buy_price or 0)
        if bp <= 0:
            continue
        pct = (cur - bp) / bp * 100.0
        if pct > GLOBAL_HARD_STOP_PCT:
            # 손절 아직 불필요 → current_price/profit_pct만 업데이트
            conn.execute(
                "UPDATE peak_holding SET current_price=?, profit_pct=?, updated_at=? WHERE id=?",
                (cur, round(pct, 2), now_ts, h_id)
            )
            continue
        # 하드스탑 발동
        qty_int = int(qty or 0)
        profit  = round((cur - bp) * qty_int)
        conn.execute("""
            UPDATE peak_holding
            SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=?
            WHERE id=?
        """, (cur, now_ts, cur, round(pct, 2), now_ts, h_id))
        conn.execute("""
            INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (name, "sell", cur, qty_int, round(cur * qty_int), profit, round(pct, 2), now_ts, strategy))
        logger.info(f"[하드스탑] {name} ({strategy}) {pct:.1f}% → 자동손절 {cur:,.0f}원")
        sold += 1

    if sold > 0:
        conn.commit()
    return sold


def _get_recently_sold_codes(conn, strategy: str, cooldown_days: int) -> set[str]:
    """최근 cooldown_days 일 이내에 매도한 종목 코드 집합 반환 → 재매수 방지."""
    cutoff = (_dt.now() - _timedelta(days=cooldown_days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """
        SELECT DISTINCT ph.stock_code
        FROM peak_holding ph
        WHERE ph.strategy = ?
          AND ph.is_active = 0
          AND ph.sold_at >= ?
        """,
        (strategy, cutoff),
    ).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def _get_current_month_bought_codes(conn, strategy: str) -> set[str]:
    """이번 달 이미 매수(보유 중 포함)한 종목 코드 → 월 1회 진입 원칙."""
    rows = conn.execute(
        """
        SELECT DISTINCT stock_code FROM peak_holding
        WHERE strategy = ?
          AND entry_date >= date('now', 'start of month', 'localtime')
        """,
        (strategy,),
    ).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def _load_combo_candidates() -> list[dict]:
    import main as _main
    combo = _main._signal_cache.get("combo_candidates", {}).get("data", [])
    if combo:
        return combo
    # V18 조회 API는 응답 속도 우선:
    # 콜드 캐시에서 무거운 사전계산(60~120초)을 강제하지 않고 빈 리스트 반환.
    # 사전계산은 스케줄러/수동 재계산 엔드포인트가 담당한다.
    return []


def _build_v18_recommendations(conn) -> dict:
    """
    V18.1 매수/매도 추천 생성

    ═══════════════════════════════════════════════════════════
    설계 원칙 (사람이 실제로 매매하는 방식)
    ═══════════════════════════════════════════════════════════
    [매수]
    - 미보유 종목만 (보유 중이면 절대 추가 매수 없음)
    - 매도 후 쿨다운: v_anchor 5일, combo 3일 재매수 금지
    - v_anchor: KOSPI > MA60(일별 종가) 이면 대형주 편입
    - combo: 추세+가치+재무 2개↑ 일치

    [매도 — 10분마다 실시간 체크, 장중 악재 대응]
    - v_anchor: 하드스탑 -10% OR KOSPI < MA60 3일 연속 (일별 종가)
      ※ 개별종목 MA 조건 적용 안 함 — KOSPI 상승추세인 한 대형주 보유
    - combo: 하드스탑 -10% OR 개별종목 추세이탈(MA20↓+MA60↓, 실시간 체크)

    [핵심 주의사항]
    - v_anchor 종목에 개별 MA 조건 적용하면 매수 직후 매도 발생
      (예: KB금융 MA60≈157,000 > 현재가 151,300 → 매수하자마자 trend_break)
    - 매도 조건은 10분마다 체크, 매수는 미보유 종목에만
    ═══════════════════════════════════════════════════════════
    """
    combo = _load_combo_candidates()
    holdings = conn.execute(
        """
        SELECT id, stock_code, stock_name, buy_price, quantity, profit_pct, entry_date
        FROM peak_holding
        WHERE strategy=? AND is_active=1
        ORDER BY entry_date ASC, id ASC
        """,
        (V18_STRATEGY,),
    ).fetchall()

    # ── [전략 무관] 하드스탑 -10% 자동 체크 — ai_combo/peak/value 등도 포함 ──
    # price_history 최신가 기준으로 모든 활성 포지션 검사 → 기회매도 누락 방지
    _auto_hardstop_all_strategies(conn)

    # ── 종목코드별 보유 티켓 수 + 가장 최근 매수일 ───────────────────────
    tickets_by_code: dict[str, int] = {}
    latest_entry_by_code: dict[str, str] = {}   # 종목별 가장 최근 매수일 (YYYY-MM-DD)
    for r in holdings:
        code = str(r[1] or "")
        if not code:
            continue
        tickets_by_code[code] = tickets_by_code.get(code, 0) + 1
        entry_d = str(r[6] or "")[:10]
        if entry_d > latest_entry_by_code.get(code, ""):
            latest_entry_by_code[code] = entry_d

    # ── 예산 추적 ──────────────────────────────────────────────────────────
    total_invested = conn.execute(
        "SELECT COALESCE(SUM(buy_price * quantity), 0) FROM peak_holding WHERE strategy=? AND is_active=1",
        (V18_STRATEGY,),
    ).fetchone()[0] or 0.0
    investable_cap  = VIRTUAL_CAPITAL * (1.0 - CASH_RESERVE_PCT)   # 최대 투자 가능 금액
    remaining_cash  = max(0.0, investable_cap - float(total_invested))
    can_buy_more    = remaining_cash >= V18_TICKET_KRW

    today_str = _dt.now().strftime("%Y-%m-%d")

    def _pyramid_ok(code: str) -> bool:
        """피라미딩 최소 간격 체크: 가장 최근 매수일로부터 PYRAMID_MIN_DAYS 이상 지났는지."""
        last = latest_entry_by_code.get(code, "")
        if not last:
            return True
        from datetime import date as _d2
        try:
            delta = (_d2.fromisoformat(today_str) - _d2.fromisoformat(last)).days
            return delta >= PYRAMID_MIN_DAYS
        except Exception:
            return True

    # ── 쿨다운: 매도 후 재매수 금지 기간 ──────────────────────────────────
    anchor_cooldown = _get_recently_sold_codes(conn, V18_STRATEGY, V_ANCHOR_COOLDOWN_DAYS)
    combo_cooldown  = _get_recently_sold_codes(conn, V18_STRATEGY, COMBO_COOLDOWN_DAYS)

    # ── KOSPI 추세 (일별 종가 기준) ───────────────────────────────────────
    kospi_above_ma, kospi_close, kospi_ma60 = _check_kospi_vs_ma60(conn)
    kospi_break_days = _count_kospi_below_ma60_days(conn) if not kospi_above_ma else 0

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 매도 체크 (10분마다 실시간 — 장중 악재 즉시 대응)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    sell_candidates = []
    for r in holdings:
        h_id, code, name, buy_price, qty, profit_pct, entry_date = r
        code = str(code or "")
        if not code:
            continue

        # 실시간 현재가 (10분마다 장중 가격 반영)
        cur, ma20, ma60_stock = _latest_price_and_ma(conn, code, 60)
        if not cur:
            continue

        bp  = _safe_float(buy_price)
        pct = ((cur - bp) / bp * 100.0) if bp > 0 else _safe_float(profit_pct)

        is_anchor = code in V_ANCHOR_UNIVERSE

        # ── v_anchor 매도 조건 ─────────────────────────────────────────
        # KOSPI 상승추세인 한 개별종목 MA는 무시, 대형주 보유 유지
        # 조건1: 하드스탑 -10% (장중 악재 즉시 대응)
        # 조건2: KOSPI < MA60 3일 연속 (추세 전환 확인 후 청산)
        if is_anchor:
            hard_stop   = pct <= -10.0
            anchor_exit = kospi_break_days >= V_ANCHOR_BREAK_DAYS
            if hard_stop:
                sell_candidates.append({
                    "id": h_id, "stock_code": code, "stock_name": name,
                    "current_price": round(cur, 2), "buy_price": round(bp, 2),
                    "profit_pct": round(pct, 2), "reason": "hard_stop(-10%)",
                })
            elif anchor_exit:
                sell_candidates.append({
                    "id": h_id, "stock_code": code, "stock_name": name,
                    "current_price": round(cur, 2), "buy_price": round(bp, 2),
                    "profit_pct": round(pct, 2),
                    "reason": f"anchor_exit(KOSPI<MA60 {kospi_break_days}일 연속)",
                })
        # ── combo 매도 조건 ────────────────────────────────────────────
        # 하드스탑 -10% OR 추세 이탈 (실시간 체크, 즉각 대응)
        else:
            hard_stop   = pct <= -10.0
            trend_break = (cur < ma20 and ma20 < ma60_stock) or (cur < ma60_stock * 0.985)
            if hard_stop or trend_break:
                sell_candidates.append({
                    "id": h_id, "stock_code": code, "stock_name": name,
                    "current_price": round(cur, 2), "buy_price": round(bp, 2),
                    "profit_pct": round(pct, 2),
                    "reason": "hard_stop(-10%)" if hard_stop else "trend_break(MA20/MA60)",
                })

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # 매수 후보
    # [예산 규칙] 총 투자금 = VIRTUAL_CAPITAL × (1 - CASH_RESERVE_PCT) 이내
    #   예: 1억 × 80% = 8,000만원 → 1,200만원 티켓 기준 최대 6개
    # [피라미딩] 동일 종목 2번째 티켓은 1번째 매수 후 PYRAMID_MIN_DAYS일 이후
    #   (즉시 전량 매수 방지 — 백테스트와 동일하게 신호 재발생 시 진입)
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    buy_candidates = []

    # ── [1] v_anchor 매수 — KOSPI > MA60 AND 티켓 < MAX AND 쿨다운 없음 ──
    # 피라미딩 허용: 이미 보유 중이더라도 티켓 수가 MAX_TICKETS_V_ANCHOR 미만이면 추가 매수
    if kospi_above_ma:
        for code in V_ANCHOR_UNIVERSE:
            held = tickets_by_code.get(code, 0)
            if held >= MAX_TICKETS_V_ANCHOR:
                continue  # 최대 티켓 도달 → 스킵
            if held > 0 and not _pyramid_ok(code):
                continue  # 2번째 티켓: 최소 간격 미충족 → 스킵
            if code in anchor_cooldown and held == 0:
                continue  # 미보유 상태에서만 쿨다운 적용 (보유 중이면 추가 매수 허용)
            cur_row = conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (code,)
            ).fetchone()
            if not cur_row:
                continue
            cur = _safe_float(cur_row[0])
            name_row = conn.execute(
                "SELECT stock_name FROM stock_universe WHERE stock_code=? LIMIT 1", (code,)
            ).fetchone()
            name = name_row[0] if name_row else code
            buy_candidates.append(
                {
                    "stock_code": code,
                    "stock_name": name,
                    "market": "KOSPI",
                    "score": 9.0,
                    "match_count": 3,
                    "trend_score": 0.0,
                    "value_score": 0.0,
                    "fin_score": 0.0,
                    "reason": f"v_anchor(KOSPI{kospi_close:,.0f}>MA60{kospi_ma60:,.0f})",
                    "executable": bool(can_buy_more),
                    "blocked_reason": None if can_buy_more else "budget_insufficient",
                }
            )

    # ── [2] combo_candidates — 티켓 < MAX AND 쿨다운 없음 ──────────────────
    sorted_combo = sorted(
        combo,
        key=lambda x: (_safe_float(x.get("match_count")), _safe_float(x.get("combined_score")), _safe_float(x.get("trend_score"))),
        reverse=True,
    )
    for s in sorted_combo:
        code = str(s.get("stock_code") or "")
        if not code:
            continue
        held = tickets_by_code.get(code, 0)
        if held >= MAX_TICKETS_COMBO:
            continue  # 최대 티켓 도달 → 스킵
        if held > 0 and not _pyramid_ok(code):
            continue  # 피라미딩 최소 간격 미충족
        if _safe_float(s.get("match_count")) < 2:
            continue
        if code in combo_cooldown and held == 0:
            continue  # 미보유 상태에서만 쿨다운 적용
        if code in V_ANCHOR_UNIVERSE and kospi_above_ma and held < MAX_TICKETS_V_ANCHOR:
            continue  # v_anchor에서 이미 처리됨
        buy_candidates.append(
            {
                "stock_code": code,
                "stock_name": s.get("stock_name") or code,
                "market": s.get("market"),
                "score": round(_safe_float(s.get("combined_score")), 2),
                "match_count": int(_safe_float(s.get("match_count"))),
                "trend_score": round(_safe_float(s.get("trend_score")), 2),
                "value_score": round(_safe_float(s.get("value_score")), 2),
                "fin_score": round(_safe_float(s.get("fin_score")), 2),
                "reason": "combo_3way" if int(_safe_float(s.get("match_count"))) >= 3 else "combo_2way",
                "executable": bool(can_buy_more),
                "blocked_reason": None if can_buy_more else "budget_insufficient",
            }
        )
        if len(buy_candidates) >= 15:
            break

    # ── AI 스크리너 표시용 관찰 목록: "왜 가상매매엔 있는데 여기엔 없지?" 해소 ──
    # V18 매수추천은 "신규/추가진입 가능 종목"만 노출되므로,
    # 이미 보유 중인 종목은 별도 watch_candidates 로 함께 내려준다.
    watch_candidates = []
    for r in holdings:
        h_id, code, name, buy_price, qty, profit_pct, entry_date = r
        code = str(code or "")
        if not code:
            continue
        cur, _, _ = _latest_price_and_ma(conn, code, 60)
        bp = _safe_float(buy_price)
        pct = ((cur - bp) / bp * 100.0) if (bp > 0 and cur > 0) else _safe_float(profit_pct)
        watch_candidates.append({
            "id": h_id,
            "stock_code": code,
            "stock_name": name,
            "entry_date": str(entry_date or "")[:10],
            "tickets": int(tickets_by_code.get(code, 1)),
            "buy_price": round(bp, 2),
            "current_price": round(cur, 2) if cur else None,
            "profit_pct": round(pct, 2),
            "reason": "holding_v18",
        })

    return {
        "buy_candidates": buy_candidates,
        "sell_candidates": sell_candidates,
        "watch_candidates": watch_candidates,
        "kospi_status": {
            "above_ma60": kospi_above_ma,
            "close": round(kospi_close, 2),
            "ma60": round(kospi_ma60, 2),
            "break_days": kospi_break_days,
        },
        "summary": {
            "holdings_count": len(holdings),
            "buy_count": len(buy_candidates),
            "buy_executable_count": len([c for c in buy_candidates if c.get("executable")]),
            "sell_count": len(sell_candidates),
            "watch_count": len(watch_candidates),
            "ticket_krw": V18_TICKET_KRW,
            # ── 예산 현황 ──────────────────────────────────────────────────
            "virtual_capital": VIRTUAL_CAPITAL,
            "total_invested": round(float(total_invested)),
            "remaining_cash": round(remaining_cash),
            "investable_cap": round(investable_cap),
            "budget_pct_used": round(float(total_invested) / VIRTUAL_CAPITAL * 100, 1),
            "cash_reserve_pct": round(CASH_RESERVE_PCT * 100),
        },
    }


def _get_v18_cached_or_build(force: bool = False) -> tuple[str, dict]:
    now = time.time()
    cache_ts = _safe_float(_v18_cache.get("updated_epoch"), 0.0)
    cached_data = _v18_cache.get("data") or {}
    if (
        not force
        and cache_ts > 0
        and (now - cache_ts) < _V18_CACHE_TTL_SEC
        and (cached_data.get("buy_candidates") is not None)
    ):
        return (_v18_cache.get("updated_at") or _dt.now().strftime("%Y-%m-%d %H:%M:%S"), cached_data)

    conn = _db()
    try:
        data = _build_v18_recommendations(conn)
    finally:
        conn.close()
    updated_at = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    _v18_cache["updated_at"] = updated_at
    _v18_cache["updated_epoch"] = now
    _v18_cache["data"] = data
    return updated_at, data


def _send_v18_telegram_alert(data: dict, sold_rows: list[dict], bought_rows: list[dict], now_ts: str) -> None:
    if not sold_rows and not bought_rows:
        return
    try:
        from notifier import send as _tg_send
    except Exception:
        logger.exception("[v18] notifier import failed")
        return

    kospi_st = data.get("kospi_status", {})
    kospi_flag = "📈 상승추세" if kospi_st.get("above_ma60") else f"📉 하락추세({kospi_st.get('break_days',0)}일)"
    lines = [
        "🤖 <b>GPT추천(V18.1) 매매 알림</b>",
        f"⏰ {now_ts}",
        f"KOSPI {kospi_st.get('close',0):,.0f} / MA60 {kospi_st.get('ma60',0):,.0f} — {kospi_flag}",
        f"매수 {len(bought_rows)}건 · 매도 {len(sold_rows)}건",
    ]
    if bought_rows:
        lines.append("")
        lines.append("🟢 <b>매수</b>")
        for r in bought_rows[:5]:
            lines.append(f"- {r['stock_name']}({r['stock_code']}) {r['qty']:,}주 @ {int(r['price']):,}원")
    if sold_rows:
        lines.append("")
        lines.append("🔴 <b>매도</b>")
        for r in sold_rows[:5]:
            lines.append(
                f"- {r['stock_name']}({r['stock_code']}) {r['qty']:,}주 @ {int(r['price']):,}원 ({r['profit_pct']:+.2f}%)"
            )
    lines.append("")
    lines.append(
        f"요약: 보유 {data.get('summary', {}).get('holdings_count', 0)} · "
        f"추천매수 {data.get('summary', {}).get('buy_count', 0)} · 추천매도 {data.get('summary', {}).get('sell_count', 0)}"
    )
    msg = "\n".join(lines)
    key = f"v18_trade_{now_ts}_{len(bought_rows)}_{len(sold_rows)}"
    _tg_send(msg, key=key)


def _turnover_state_from_reason(raw: str) -> dict:
    try:
        j = json.loads(raw) if raw else {}
        if isinstance(j, dict):
            return j
    except Exception:
        pass
    return {}


def _turnover_fetch_candidates(conn) -> list[dict]:
    """
    회전율 전략 후보 생성:
    market_indicators의 종가기반 브레이크아웃 API를 재사용.
    """
    try:
        from routes.market_indicators import get_turnover_breakout_signals
        data = get_turnover_breakout_signals(
            date_str="",
            market="ALL",
            scan_limit=250,
            top_n=30,
            min_turnover_pct=8.0,
            min_body_pct=5.0,
            min_vol_ratio=1.8,
            require_dual_flow=True,
        )
        return data.get("candidates") or []
    except Exception as e:
        logger.warning("[turnover] candidate build failed: %s", e)
        return []


def _ensure_peak_holding_reason_columns(conn) -> None:
    """Store StockEasy click-detail research with the virtual holding row."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(peak_holding)").fetchall()}
    for name, ddl in (
        ("entry_reason_text", "ALTER TABLE peak_holding ADD COLUMN entry_reason_text TEXT"),
        ("entry_reason_json", "ALTER TABLE peak_holding ADD COLUMN entry_reason_json TEXT"),
        ("entry_reason_updated_at", "ALTER TABLE peak_holding ADD COLUMN entry_reason_updated_at TEXT"),
    ):
        if name not in cols:
            conn.execute(ddl)


# ── GET /api/trend/holdings ─────────────────────────────────────
@router.get("/holdings")
def get_trend_holdings(
    strategy: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    conn   = _db()
    _ensure_peak_holding_reason_columns(conn)
    params = []
    where = ""
    if strategy:
        strategy = _normalize_strategy_key(strategy)
        where = "WHERE strategy=?"
        params.append(strategy)
    rows   = conn.execute(f"""
        SELECT id, stock_name, buy_price, current_price,
               quantity, profit_pct, sell_price, sold_at, is_active,
               hold_days, sector, updated_at, entry_date, sold_price, strategy, stock_code,
               entry_reason_text, entry_reason_json, entry_reason_updated_at
        FROM peak_holding {where}
        ORDER BY entry_date DESC
    """, params).fetchall()
    result = []

    for r in rows:
        stock_name = r[1]
        buy_price  = r[2] or 0
        quantity   = r[4] or 0
        is_active  = bool(r[8])
        stock_code = r[15] or ""

        # stock_code 없으면 universe에서 조회 후 캐시
        if not stock_code:
            for tbl in ("stock_universe", "stock_meta", "listed_company_info"):
                row = conn.execute(
                    f"SELECT stock_code FROM {tbl} WHERE stock_name=? LIMIT 1", (stock_name,)
                ).fetchone()
                if row:
                    stock_code = row[0]
                    conn.execute("UPDATE peak_holding SET stock_code=? WHERE id=?", (stock_code, r[0]))
                    conn.commit()
                    break

        # 현재가: 최신 거래일 close
        # 등락률: 최신 close vs 직전 거래일 close (휴장일 포함 고정 계산)
        daily_change = 0.0
        daily_change_pct = 0.0
        price_as_of = None
        currency = "KRW"
        if is_active and stock_code:
            cur_px, prev_price, price_as_of, currency = _latest_price_pair(conn, stock_code)
            if cur_px:
                current_price = cur_px
                daily_change = (current_price - prev_price) if prev_price else 0.0
                daily_change_pct = ((current_price - prev_price) / prev_price * 100.0) if prev_price else 0.0
            else:
                current_price = (r[3] or buy_price)
        else:
            current_price = r[3] or buy_price
            currency = "USD" if _is_us_ticker(stock_code) else "KRW"

        profit_raw  = (current_price - buy_price) * quantity if buy_price and quantity else 0
        profit      = round(profit_raw, 2) if currency == "USD" else round(profit_raw)
        profit_pct  = round((current_price - buy_price) / buy_price * 100, 2) if buy_price else 0
        total_raw   = current_price * quantity if current_price and quantity else 0
        total_value = round(total_raw, 2) if currency == "USD" else round(total_raw)

        result.append({
            "id": r[0], "stock_code": stock_code, "stock_name": stock_name,
            "buy_price": buy_price, "current_price": current_price, "quantity": quantity,
            "profit_pct": profit_pct, "profit": profit, "total_value": total_value,
            "daily_change": round(daily_change, 2),
            "daily_change_pct": round(daily_change_pct, 2),
            "currency": currency,
            "market_type": "US" if currency == "USD" else "KR",
            "price_as_of": price_as_of,
            "sell_price": r[6], "sold_at": r[7],
            "is_active": is_active, "hold_days": r[9],
            "sector": r[10], "updated_at": r[11],
            "entry_date": r[12], "strategy": r[14],
            "entry_reason_text": r[16] or "",
            "entry_reason_json": r[17] or "",
            "entry_reason_updated_at": r[18] or "",
        })

    conn.close()
    return result


# ── POST /api/trend/buy ─────────────────────────────────────────
@router.post("/buy")
def trend_buy(payload: dict):
    stock_name = payload.get("stock_name", "")
    stock_code = payload.get("stock_code", "")
    buy_price  = float(payload.get("current_price") or payload.get("buy_price") or 0)
    quantity   = int(payload.get("quantity") or 0)
    entry_date = payload.get("entry_date") or _dt.now().strftime("%Y-%m-%d")
    strategy   = payload.get("strategy", "peak")
    sector     = payload.get("sector", "")
    entry_reason_text = payload.get("entry_reason_text", "")
    entry_reason_json = payload.get("entry_reason_json", "")

    if not stock_name or not buy_price:
        raise HTTPException(status_code=400, detail="stock_name, buy_price 필수")

    conn = _db()
    _ensure_peak_holding_reason_columns(conn)
    if not stock_code:
        for tbl in ("stock_universe", "stock_meta", "listed_company_info"):
            row = conn.execute(
                f"SELECT stock_code FROM {tbl} WHERE stock_name=? LIMIT 1", (stock_name,)
            ).fetchone()
            if row:
                stock_code = row[0]
                break

    # 중복 방지
    dup = conn.execute(
        "SELECT id FROM peak_holding WHERE stock_name=? AND entry_date=? AND strategy=?",
        (stock_name, entry_date, strategy)
    ).fetchone()
    if dup:
        conn.execute(
            "UPDATE peak_holding SET is_active=1, current_price=?, "
            "stock_code=COALESCE(stock_code,?), "
            "entry_reason_text=COALESCE(NULLIF(?,''), entry_reason_text), "
            "entry_reason_json=COALESCE(NULLIF(?,''), entry_reason_json), "
            "entry_reason_updated_at=CASE WHEN NULLIF(?, '') IS NOT NULL THEN CURRENT_TIMESTAMP ELSE entry_reason_updated_at END, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (buy_price, stock_code or None, entry_reason_text, entry_reason_json, entry_reason_text, dup[0])
        )
        conn.commit(); conn.close()
        return {"status": "ok", "stock_name": stock_name}

    active = conn.execute(
        "SELECT id FROM peak_holding WHERE stock_name=? AND is_active=1 AND strategy=?",
        (stock_name, strategy)
    ).fetchone()
    if not active:
        gate = _paper_buy_gate(stock_code, strategy, quantity, buy_price)
        if gate["decision"] != "BUY_ALLOWED":
            conn.close()
            raise HTTPException(status_code=409, detail={
                "message": "가상매수 위험게이트 차단", "decision": gate["decision"],
                "reasons": gate.get("reasons") or [],
            })
        holding_cursor = conn.execute(
            "INSERT INTO peak_holding (stock_code,stock_name,sector,buy_price,current_price,quantity,"
            "entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at,"
            "entry_reason_text,entry_reason_json,entry_reason_updated_at) "
            "VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,"
            "CASE WHEN NULLIF(?, '') IS NOT NULL THEN CURRENT_TIMESTAMP ELSE NULL END)",
            (
                stock_code or None, stock_name, sector, buy_price, buy_price, quantity,
                entry_date, strategy, entry_reason_text, entry_reason_json, entry_reason_text,
            )
        )
        holding_id = holding_cursor.lastrowid
        if holding_id is None:
            holding_id = conn.execute(
                "SELECT id FROM peak_holding WHERE stock_name=? AND entry_date=? AND strategy=? ORDER BY id DESC LIMIT 1",
                (stock_name, entry_date, strategy),
            ).fetchone()[0]
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) "
            "VALUES (?,?,?,?,?,0,0.0,CURRENT_TIMESTAMP,?)",
            (stock_name, "buy", buy_price, quantity, round(buy_price * quantity), strategy)
        )
        _record_paper_trade(
            conn, strategy=strategy, side="buy", code=stock_code, name=stock_name,
            holding_id=int(holding_id), qty=quantity, price=buy_price,
            trade_id=int(trade_cursor.lastrowid), occurred_at=_dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        )
    conn.commit(); conn.close()
    return {"status": "ok", "stock_name": stock_name}


# ── POST /api/trend/update ──────────────────────────────────────
@router.post("/update")
def trend_update(payload: dict):
    stock_name    = payload.get("stock_name", "")
    stock_code    = payload.get("stock_code", "")
    strategy      = payload.get("strategy", "peak")
    current_price = float(payload.get("current_price") or 0)
    hold_days     = int(payload.get("hold_days") or 0)
    profit_pct    = float(payload.get("profit_pct") or 0)
    entry_reason_text = payload.get("entry_reason_text", "")
    entry_reason_json = payload.get("entry_reason_json", "")

    if not stock_name:
        return {"status": "skip"}

    try:
        conn = _db()
        _ensure_peak_holding_reason_columns(conn)
    except _sl.OperationalError as e:
        if "database is locked" in str(e).lower():
            logger.warning("[trend/update] DB locked on open; skipped non-critical update for %s", stock_name)
            return {"status": "skip", "reason": "db_locked"}
        raise
    try:
        conn.execute(
            "UPDATE peak_holding SET current_price=?, hold_days=?, profit_pct=?, "
            "stock_code=COALESCE(NULLIF(stock_code,''), NULLIF(?,''), stock_code), "
            "entry_reason_text=COALESCE(NULLIF(?,''), entry_reason_text), "
            "entry_reason_json=COALESCE(NULLIF(?,''), entry_reason_json), "
            "entry_reason_updated_at=CASE WHEN NULLIF(?, '') IS NOT NULL THEN CURRENT_TIMESTAMP ELSE entry_reason_updated_at END, "
            "updated_at=CURRENT_TIMESTAMP "
            "WHERE stock_name=? AND strategy=? AND is_active=1",
            (
                current_price, hold_days, profit_pct, stock_code,
                entry_reason_text, entry_reason_json, entry_reason_text,
                stock_name, strategy,
            )
        )
        conn.commit()
    except _sl.OperationalError as e:
        if "database is locked" in str(e).lower():
            logger.warning("[trend/update] DB locked; skipped non-critical update for %s", stock_name)
            return {"status": "skip", "reason": "db_locked"}
        raise
    finally:
        conn.close()
    return {"status": "ok"}


# ── POST /api/trend/sell ────────────────────────────────────────
@router.post("/sell")
def trend_sell(payload: dict):
    stock_name = payload.get("stock_name", "")
    strategy   = payload.get("strategy", "peak")
    sell_price = float(payload.get("sell_price") or 0)
    profit     = float(payload.get("profit") or 0)
    profit_pct = float(payload.get("profit_pct") or 0)
    sold_at    = payload.get("sold_at") or _dt.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = _db()
    row = conn.execute(
        "SELECT id,quantity,strategy,buy_price,COALESCE(stock_code,'') FROM peak_holding "
        "WHERE stock_name=? AND strategy=? AND is_active=1 ORDER BY id DESC LIMIT 1",
        (stock_name, strategy),
    ).fetchone()
    if not row:
        conn.close()
        return {"status": "skip", "reason": "active_holding_not_found"}
    holding_id, qty, row_strategy, buy_price, stock_code = row
    qty = int(qty or 0)
    gross_profit = (sell_price - float(buy_price or 0)) * qty
    profit = gross_profit
    profit_pct = ((sell_price / float(buy_price)) - 1) * 100 if buy_price else 0.0
    conn.execute(
        "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE stock_name=? AND strategy=? AND is_active=1",
        (sell_price, sold_at, sell_price, profit_pct, stock_name, strategy)
    )
    trade_cursor = conn.execute(
        "INSERT INTO peak_trade (holding_id,stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (holding_id, stock_name, "sell", sell_price, qty, round(sell_price * qty), profit, profit_pct, sold_at, row_strategy or "peak")
    )
    _record_paper_trade(
        conn, strategy=row_strategy or "peak", side="sell", code=stock_code, name=stock_name,
        holding_id=int(holding_id), qty=qty, price=sell_price,
        trade_id=int(trade_cursor.lastrowid), occurred_at=sold_at, gross_profit=gross_profit,
    )
    conn.commit(); conn.close()
    return {"status": "ok", "stock_name": stock_name}


# ── GET /api/trend/trades ───────────────────────────────────────
@router.get("/trades")
def get_trend_trades(db: Session = Depends(get_db)):
    conn = _db()
    rows = conn.execute(
        "SELECT id,'' as stock_code,stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy "
        "FROM peak_trade ORDER BY tx_at DESC LIMIT 100"
    ).fetchall()
    conn.close()
    keys = ["id","stock_code","stock_name","tx_type","price","quantity","total_amount","profit","profit_pct","tx_at","strategy"]
    return [dict(zip(keys, r)) for r in rows]


# ── GET /api/trend/summary ──────────────────────────────────────
@router.get("/summary")
def get_trend_summary(strategy: str | None = Query(default=None), db: Session = Depends(get_db)):
    conn = _db()
    strategy = _normalize_strategy_key(strategy)
    where = "WHERE is_active=0"
    params: list = []
    if strategy:
        where += " AND strategy=?"
        params.append(strategy)
    row  = conn.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN profit_pct>0 THEN 1 ELSE 0 END), "
        f"SUM((sell_price-buy_price)*quantity) FROM peak_holding {where}",
        params,
    ).fetchone()
    # 손익비(Profit/Loss ratio) = 평균 수익금액 ÷ 평균 손실금액(절댓값).
    # 승률만으로는 "적게 자주 벌고 크게 가끔 잃는" 전략을 구분 못하므로 함께 기록한다.
    pl_row = conn.execute(
        f"SELECT "
        f"AVG(CASE WHEN (sell_price-buy_price)*quantity>0 THEN (sell_price-buy_price)*quantity END), "
        f"AVG(CASE WHEN (sell_price-buy_price)*quantity<0 THEN (sell_price-buy_price)*quantity END) "
        f"FROM peak_holding {where}",
        params,
    ).fetchone()
    conn.close()
    total = row[0] or 0
    wins  = row[1] or 0
    avg_win  = pl_row[0]
    avg_loss = pl_row[1]
    pl_ratio = None
    if avg_win is not None and avg_loss is not None and avg_loss != 0:
        pl_ratio = round(avg_win / abs(avg_loss), 2)
    return {
        "strategy": strategy,
        "total_trades": total,
        "win_count":    wins,
        "win_rate":     round(wins / total * 100, 1) if total else None,
        "total_profit": round(row[2] or 0),
        "avg_win":      round(avg_win) if avg_win is not None else None,
        "avg_loss":     round(abs(avg_loss)) if avg_loss is not None else None,
        "pl_ratio":     pl_ratio,
        "profit_loss_ratio": pl_ratio,
    }


# ── GET /api/trend/ai-holdings ──────────────────────────────────
@router.get("/ai-holdings")
def get_ai_holdings():
    conn = _db()
    rows = conn.execute(
        "SELECT stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,"
        "       detected_at,is_active,id,sell_price,sold_at,stock_code "
        "FROM peak_holding WHERE strategy='ai_combo' "
        "ORDER BY is_active DESC, entry_date DESC, id DESC LIMIT 50"
    ).fetchall()
    result = []
    for r in rows:
        stock_code  = r[13]
        buy_price   = r[2] or 0
        quantity    = r[4] or 0
        is_active   = bool(r[9])
        sell_price  = r[11]

        if is_active and stock_code:
            pr = conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1",
                (stock_code,)
            ).fetchone()
            current_price = pr[0] if pr else (r[3] or buy_price)
        else:
            current_price = sell_price or r[3] or buy_price

        profit_pct  = round((current_price - buy_price) / buy_price * 100, 2) if buy_price else 0
        result.append({
            "stock_code": stock_code, "stock_name": r[0], "sector": r[1],
            "buy_price": buy_price, "current_price": current_price, "quantity": quantity,
            "entry_date": r[5], "hold_days": r[6],
            "profit_pct": profit_pct,
            "profit": round((current_price - buy_price) * quantity),
            "total_value": round(current_price * quantity),
            "detected_at": r[8], "is_active": is_active, "id": r[10],
            "sell_price": sell_price, "sold_at": r[12],
        })
    conn.close()
    return result


# ═══════════════════════════════════════════════════════════════════
# V12 골든크로스 가상매매
# ═══════════════════════════════════════════════════════════════════

def _gc_get_price_series(conn, code: str, n: int = 300):
    rows = conn.execute(
        "SELECT close, volume FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT ?",
        (code, n)
    ).fetchall()
    return [(float(r[0]), float(r[1] or 0)) for r in rows]


def _gc_calc_rs6m(conn, code: str) -> float:
    """KOSPI 대비 6개월 상대강도 계산."""
    series = _gc_get_price_series(conn, code, 130)
    if len(series) < 125:
        return 0.0
    cur_price = series[0][0]
    price_6m  = series[min(125, len(series)-1)][0]
    stock_ret = (cur_price - price_6m) / price_6m * 100 if price_6m > 0 else 0.0

    kospi = conn.execute(
        "SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT 130"
    ).fetchall()
    if len(kospi) < 125:
        return stock_ret
    kc = float(kospi[0][0]); k6 = float(kospi[min(125, len(kospi)-1)][0])
    kospi_ret = (kc - k6) / k6 * 100 if k6 > 0 else 0.0
    return stock_ret - kospi_ret


def _gc_check_cross(conn, code: str) -> tuple[bool, float, float, float]:
    """MA20이 MA60을 상향돌파했는지 최근 GC_CROSS_DAYS 이내 체크.
    Returns (is_golden_cross, cur_price, ma20, ma60)
    """
    series = _gc_get_price_series(conn, code, 100)
    if len(series) < 65:
        return False, 0.0, 0.0, 0.0
    prices = [s[0] for s in series]
    cur_price = prices[0]
    ma20_now = sum(prices[:20]) / 20
    ma60_now = sum(prices[:60]) / 60
    if ma20_now <= ma60_now:
        return False, cur_price, ma20_now, ma60_now
    # 최근 GC_CROSS_DAYS 일 중 MA20이 MA60 아래였던 적이 있는지 (교차 감지)
    for i in range(1, GC_CROSS_DAYS + 1):
        if i + 60 > len(prices):
            break
        ma20_prev = sum(prices[i:i+20]) / 20
        ma60_prev = sum(prices[i:i+60]) / 60
        if ma20_prev < ma60_prev:
            return True, cur_price, ma20_now, ma60_now
    return False, cur_price, ma20_now, ma60_now


def _gc_check_volume(conn, code: str) -> bool:
    """5일 평균 거래량 >= 20일 평균 거래량 × GC_VOL_RATIO."""
    series = _gc_get_price_series(conn, code, 25)
    if len(series) < 21:
        return True  # 데이터 부족 시 통과
    vols = [s[1] for s in series]
    v5 = sum(vols[:5]) / 5
    v20 = sum(vols[:20]) / 20
    return v20 > 0 and (v5 / v20) >= GC_VOL_RATIO


def _build_gc_recommendations(conn) -> dict:
    """V12 골든크로스 매수/매도 추천 생성."""
    from datetime import datetime as _now_dt
    now_ts = _now_dt.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 1) 현재 보유 포지션 → 매도 조건 체크 ──────────────────────
    holdings = conn.execute(
        """SELECT id, stock_code, stock_name, buy_price, quantity, entry_date, current_price
           FROM peak_holding
           WHERE strategy=? AND is_active=1
           ORDER BY entry_date""",
        (GC_STRATEGY,)
    ).fetchall()

    sell_candidates = []
    for h_id, code, name, buy_price, qty, entry_date, cur_cached in holdings:
        bp = float(buy_price or 0)
        if bp <= 0:
            continue
        series = _gc_get_price_series(conn, code, 10)
        cur = series[0][0] if series else 0.0
        if cur <= 0:
            cur = float(cur_cached or bp)
        pct = (cur - bp) / bp

        # 보유일 계산
        try:
            from datetime import date as _d
            ed = _d.fromisoformat(str(entry_date)[:10])
            hold_days = (_d.today() - ed).days
        except Exception:
            hold_days = 0

        # 최대 손실 추적 (피크 기반 트레일)
        peak_row = conn.execute(
            "SELECT MAX(close) FROM price_history WHERE stock_code=? AND close>0 AND date >= ?",
            (code, str(entry_date)[:10])
        ).fetchone()
        peak = float(peak_row[0]) if peak_row and peak_row[0] else cur
        trail_from_peak = (cur - peak) / peak if peak > 0 else 0.0

        sell_reason = None

        # 하드스탑 -12%
        if pct <= GC_STOP_PCT:
            sell_reason = f"손절(-12%): {pct*100:.1f}%"

        # 트레일 손절: 이익 50%+ 이면 -30%, 그 외 이익 5%+ 이면 -25%
        elif pct >= 0.50 and trail_from_peak <= GC_TRAIL_BIG:
            sell_reason = f"트레일(-30%): 고점대비 {trail_from_peak*100:.1f}%"
        elif pct >= 0.05 and trail_from_peak <= GC_TRAIL_PCT:
            sell_reason = f"트레일(-25%): 고점대비 {trail_from_peak*100:.1f}%"

        # 최대 보유일 초과
        elif hold_days >= GC_MAX_HOLD_DAYS:
            sell_reason = f"만기({hold_days}일)"

        if sell_reason:
            sell_candidates.append({
                "stock_code": code,
                "stock_name": name,
                "current_price": cur,
                "buy_price": bp,
                "profit_pct": round(pct * 100, 2),
                "hold_days": hold_days,
                "entry_date": str(entry_date)[:10] if entry_date else None,
                "reason": sell_reason,
            })
            # 현재가 업데이트
            conn.execute(
                "UPDATE peak_holding SET current_price=?, profit_pct=?, updated_at=? WHERE id=?",
                (cur, round(pct * 100, 2), now_ts, h_id)
            )
        else:
            conn.execute(
                "UPDATE peak_holding SET current_price=?, profit_pct=?, updated_at=? WHERE id=?",
                (cur, round(pct * 100, 2), now_ts, h_id)
            )

    # ── 2) 매수 후보: 골든크로스 조건 충족 종목 스캔 ───────────────
    # 현재 보유 코드
    held_codes = {str(h[1]) for h in holdings if h[1]}
    # 매도된 코드 (쿨다운)
    recently_sold = _get_recently_sold_codes(conn, GC_STRATEGY, GC_COOLDOWN_DAYS)
    excluded_codes = held_codes | recently_sold

    # 유니버스: KOSPI/KOSDAQ 시총 2000억+ 6자리 코드
    candidates_raw = conn.execute(
        """SELECT su.stock_code, su.stock_name, su.market_cap
           FROM stock_universe su
           WHERE su.market IN ('KOSPI','KOSDAQ')
             AND su.market_cap >= ?
             AND LENGTH(su.stock_code) = 6
             AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
           ORDER BY su.market_cap DESC
           LIMIT 800""",
        (GC_MIN_MKTCAP,)
    ).fetchall()

    buy_candidates = []
    for code, name, mktcap in candidates_raw:
        if code in excluded_codes:
            continue
        is_gc, cur, ma20, ma60 = _gc_check_cross(conn, code)
        if not is_gc or cur <= 0:
            continue
        if not _gc_check_volume(conn, code):
            continue
        rs6m = _gc_calc_rs6m(conn, code)
        if rs6m < GC_RS6M_MIN:
            continue
        # 과열 회피 (2026-07-13 백테스트 채택과 동기화): 40거래일 수익률 +100% 초과 급등주 제외
        # 실증: 급등직후 종목의 6개월 -30% 하락률 37~41% (기준율 3.6배)
        _oh = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 41",
            (code,),
        ).fetchall()
        if len(_oh) >= 41 and _oh[40][0] > 0 and (cur / _oh[40][0] - 1) > 1.0:
            continue
        buy_candidates.append({
            "stock_code": code,
            "stock_name": name,
            "current_price": cur,
            "ma20": round(ma20),
            "ma60": round(ma60),
            "rs6m": round(rs6m, 1),
            "mktcap_억": int(mktcap or 0),
        })
        if len(buy_candidates) >= 20:
            break

    # 주도섹터 보너스 철회 (2026-07-13): 채택 근거가 유니버스 룩어헤드로 판명 —
    # as-of 시총 백테스트에서 부스트 무효(+15.7% vs 무부스트 +17.4%). RS6M 단독 랭킹으로 복귀.
    # RS6M 내림차순 정렬
    buy_candidates.sort(key=lambda x: x["rs6m"], reverse=True)

    # ── 3) 예산 요약 ──────────────────────────────────────────────
    active_cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (GC_STRATEGY,)
    ).fetchone()[0] or 0
    total_invested = conn.execute(
        "SELECT COALESCE(SUM(buy_price*quantity),0) FROM peak_holding WHERE strategy=? AND is_active=1",
        (GC_STRATEGY,)
    ).fetchone()[0] or 0.0
    investable_cap = GC_CAPITAL_KRW * (1 - GC_CASH_RESERVE)
    avail_cash = max(0.0, investable_cap - float(total_invested))

    summary = {
        "strategy": GC_STRATEGY,
        "capital_krw": GC_CAPITAL_KRW,
        "ticket_krw": GC_TICKET_KRW,
        "active_positions": active_cnt,
        "total_invested": round(float(total_invested)),
        "avail_cash": round(avail_cash),
        "sell_count": len(sell_candidates),
        "buy_count": len(buy_candidates),
        "updated_at": now_ts,
    }
    # ── 4) 전체 보유 리스트 ──────────────────────────────────────────
    holdings_list = []
    for h_id, code, name, buy_price, qty, entry_date, cur_cached in holdings:
        bp = float(buy_price or 0)
        cur = float(cur_cached or bp)
        pct = (cur - bp) / bp * 100 if bp > 0 else 0.0
        try:
            from datetime import date as _d2
            ed = _d2.fromisoformat(str(entry_date)[:10])
            hold_days_h = (_d2.today() - ed).days
        except Exception:
            hold_days_h = 0
        holdings_list.append({
            "stock_code": code,
            "stock_name": name,
            "buy_price": bp,
            "current_price": cur,
            "profit_pct": round(pct, 2),
            "entry_date": str(entry_date)[:10] if entry_date else None,
            "hold_days": hold_days_h,
        })

    return {
        "sell_candidates": sell_candidates,
        "buy_candidates": buy_candidates,
        "holdings": holdings_list,
        "summary": summary,
    }


def _get_gc_cached_or_build(force: bool = False):
    now = time.time()
    cached = _gc_cache.get("data")
    updated_at = _gc_cache.get("updated_at")
    updated_epoch = _gc_cache.get("updated_epoch", 0)
    if not force and cached and updated_epoch and (now - updated_epoch) < _GC_CACHE_TTL_SEC:
        return updated_at, cached
    conn = _db()
    try:
        data = _build_gc_recommendations(conn)
        conn.commit()
    finally:
        conn.close()
    _gc_cache["data"] = data
    _gc_cache["updated_at"] = data["summary"]["updated_at"]
    _gc_cache["updated_epoch"] = now
    return _gc_cache["updated_at"], data


# ── GET /api/trend/gc/recommendations ───────────────────────────
@router.get("/gc/recommendations")
def get_gc_recommendations():
    updated_at, data = _get_gc_cached_or_build(force=False)
    return {"ok": True, "updated_at": updated_at, **data}


# ── POST /api/trend/gc/execute ──────────────────────────────────
@router.post("/gc/execute")
def execute_gc_now():
    """V12 골든크로스 가상매매 즉시 실행."""
    _, data = _get_gc_cached_or_build(force=True)
    conn = _db()
    _ensure_peak_holding_reason_columns(conn)
    sold = 0
    bought = 0
    today = _dt.now().strftime("%Y-%m-%d")
    now_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    sold_rows: list[dict] = []
    bought_rows: list[dict] = []

    # 1) 매도
    for s in data["sell_candidates"]:
        code = s["stock_code"]
        price = float(s["current_price"])
        row = conn.execute(
            "SELECT id, quantity, buy_price FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (code, GC_STRATEGY),
        ).fetchone()
        if not row:
            continue
        h_id, qty, buy_price = row
        qty = int(qty or 0)
        if qty <= 0:
            continue
        bp = float(buy_price or 0)
        profit = round((price - bp) * qty)
        profit_pct = round((price - bp) / bp * 100, 2) if bp > 0 else 0.0
        conn.execute(
            "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=? WHERE id=?",
            (price, now_ts, price, profit_pct, now_ts, h_id),
        )
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) VALUES (?,?,?,?,?,?,?,?,?)",
            (s["stock_name"], "sell", price, qty, round(price * qty), profit, profit_pct, now_ts, GC_STRATEGY),
        )
        _record_paper_trade(
            conn, strategy=GC_STRATEGY, side="sell", code=code, name=s["stock_name"],
            holding_id=int(h_id), qty=qty, price=price, trade_id=int(trade_cursor.lastrowid),
            occurred_at=now_ts, gross_profit=profit,
        )
        sold += 1
        sold_rows.append({"stock_name": s["stock_name"], "stock_code": code, "qty": qty, "price": price, "profit_pct": profit_pct})
        logger.info(f"[V12-GC 매도] {s['stock_name']} {profit_pct:.1f}% — {s['reason']}")

    # 2) 매수
    avail_cash = _investable_cash(conn, GC_STRATEGY, GC_CAPITAL_KRW, GC_CASH_RESERVE)
    active_cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (GC_STRATEGY,)
    ).fetchone()[0] or 0

    for b in data["buy_candidates"][:GC_MAX_POSITIONS]:
        if avail_cash < GC_TICKET_KRW:
            break
        if active_cnt >= GC_MAX_POSITIONS:
            break
        code = b["stock_code"]
        name = b["stock_name"]
        cur = float(b["current_price"])
        if cur <= 0:
            continue
        # 이미 보유 중이면 스킵
        existing = conn.execute(
            "SELECT COUNT(*) FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1",
            (code, GC_STRATEGY)
        ).fetchone()[0]
        if existing > 0:
            continue
        qty = int(GC_TICKET_KRW // cur)
        if qty <= 0:
            continue
        gate = _paper_buy_gate(code, GC_STRATEGY, qty, cur)
        if gate["decision"] != "BUY_ALLOWED":
            logger.warning("[V12-GC 매수차단] %s %s", code, gate["decision"])
            continue
        reason_text = f"V12-GC ma20={b['ma20']:,} ma60={b['ma60']:,} rs6m={b['rs6m']}%"
        holding_cursor = conn.execute(
            """INSERT INTO peak_holding
               (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at,entry_reason_text,entry_reason_json,entry_reason_updated_at)
               VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)""",
            (code, name, "", cur, cur, qty, today, GC_STRATEGY, reason_text, json.dumps(b, ensure_ascii=False)),
        )
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) VALUES (?,?,?,?,?,0,0.0,?,?)",
            (name, "buy", cur, qty, round(cur * qty), now_ts, GC_STRATEGY),
        )
        _record_paper_trade(
            conn, strategy=GC_STRATEGY, side="buy", code=code, name=name,
            holding_id=int(holding_cursor.lastrowid), qty=qty, price=cur,
            trade_id=int(trade_cursor.lastrowid), occurred_at=now_ts,
        )
        bought += 1
        active_cnt += 1
        avail_cash -= cur * qty
        bought_rows.append({"stock_name": name, "stock_code": code, "qty": qty, "price": cur})
        logger.info(f"[V12-GC 매수] {name} {cur:,}원 × {qty}주 rs6m={b['rs6m']}%")

    conn.commit()
    conn.close()
    _gc_cache["updated_at"] = now_ts
    _gc_cache["updated_epoch"] = time.time()
    _gc_cache["data"] = data
    return {
        "ok": True,
        "updated_at": now_ts,
        "sold": sold,
        "bought": bought,
        "sold_rows": sold_rows,
        "bought_rows": bought_rows,
        "summary": data["summary"],
    }


# ═══════════════════════════════════════════════════════════════
#  V-RECOVERY 낙폭과대 반등 가상매매 (2026-07-12)
#  backtest.run_backtest_recovery(turnaround_bonus=20) 채택 로직과 일치
# ═══════════════════════════════════════════════════════════════
def _rec_get_series(conn, code: str, n: int = 260):
    """(close, volume, low) 최신순."""
    rows = conn.execute(
        "SELECT close, COALESCE(volume,0), COALESCE(low, close) FROM price_history "
        "WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT ?",
        (code, n)
    ).fetchall()
    return [(float(r[0]), float(r[1]), float(r[2])) for r in rows]


def _rec_is_turnaround(conn, code: str) -> bool:
    """직전 분기 첫 흑자전환: 최신 분기 NI>0 AND 이전 1~3분기 중 NI<0 존재."""
    rows = conn.execute(
        "SELECT net_income FROM financial_data "
        "WHERE stock_code=? AND is_annual=0 AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL "
        "ORDER BY year DESC, quarter DESC LIMIT 4",
        (code,)
    ).fetchall()
    if len(rows) < 2:
        return False
    if float(rows[0][0] or 0) <= 0:
        return False
    return any(float(r[0] or 0) < 0 for r in rows[1:])


def _rec_kospi_panic(conn) -> bool:
    """KOSPI < MA120 × 0.85 이면 완전 패닉장 → 신규 매수 스킵."""
    rows = conn.execute(
        "SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT 120"
    ).fetchall()
    if len(rows) < 120:
        return False
    cur = float(rows[0][0])
    ma120 = sum(float(r[0]) for r in rows) / len(rows)
    return cur < ma120 * 0.85


CM_STRATEGY      = "v_contract_momentum"
CM_TICKET_KRW    = 10_000_000
CM_CAPITAL_KRW   = 100_000_000
CM_CASH_RESERVE  = 0.20
CM_MAX_POSITIONS = 10
CM_STOP_PCT      = -0.08
CM_TRAIL_PCT     = -0.25
CM_TRAIL_ACTIVATE_PCT = 0.10
CM_MAX_HOLD_DAYS = 400  # 2026-08-10: 240→400 — backtest.py run_backtest_contract_momentum과 동일하게
                         # 변경(홀드아웃 검증 채택, CLAUDE.md 2026-08-10 참조)
CM_MIN_RATIO     = 10.0     # 계약금액/매출 비율(%) 하한
CM_MIN_AVG20_AMT = 2_000_000_000  # 20일평균거래대금 20억+
CM_LOOKBACK_DAYS = 20        # 최근 N일 내 공시만 매수후보로 스캔(신선한 신호만)
CM_COOLDOWN_DAYS = 10
CM_MAX_BUYS_PER_RUN = 3
_cm_cache: dict = {"updated_at": None, "data": {}}
_CM_CACHE_TTL_SEC = 600


def _cm_is_clean_contract_report(report_name: str) -> bool:
    name = (report_name or "").replace(" ", "")
    if not name:
        return False
    if "단일판매" not in name and "공급계약" not in name:
        return False
    blocked = ("계약해지", "주권매매거래정지", "유동성공급", "[첨부추가]")
    return not any(token in name for token in blocked)


def _build_cm_recommendations(conn) -> dict:
    """V-CONTRACT-MOMENTUM 해외수주 모멘텀 매수/매도 추천 생성.

    backtest.py run_backtest_contract_momentum과 동일 필터·매도규칙(2026-08-09 신규,
    검증: 원본과 동일분할 학습+45.8%/검증+163.5%, 6기간 avg6=+25.27% 5/6양수). 라이브는
    "오늘 시점 신호"만 필요하므로 as-of 시총 스캔 없이 현재 dart_contracts 최신 공시를
    직접 조회 — v_gc/v_recovery와 동일한 가벼운 패턴.
    """
    from datetime import datetime as _now_dt, timedelta as _td
    now_ts = _now_dt.now().strftime("%Y-%m-%d %H:%M:%S")
    lookback_date = (_now_dt.now() - _td(days=CM_LOOKBACK_DAYS)).strftime("%Y-%m-%d")

    # ── 1) 보유 포지션 매도 체크 ──────────────────────────────────
    holdings = conn.execute(
        """SELECT id, stock_code, stock_name, buy_price, quantity, entry_date, current_price
           FROM peak_holding WHERE strategy=? AND is_active=1 ORDER BY entry_date""",
        (CM_STRATEGY,)
    ).fetchall()

    sell_candidates = []
    for h_id, code, name, buy_price, qty, entry_date, cur_cached in holdings:
        bp = float(buy_price or 0)
        if bp <= 0:
            continue
        row = conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1", (code,)
        ).fetchone()
        cur = float(row[0]) if row else float(cur_cached or bp)
        pct = (cur - bp) / bp
        try:
            from datetime import date as _d
            ed = _d.fromisoformat(str(entry_date)[:10])
            hold_days = (_d.today() - ed).days
        except Exception:
            hold_days = 0
        peak_row = conn.execute(
            "SELECT MAX(close) FROM price_history WHERE stock_code=? AND close>0 AND date >= ?",
            (code, str(entry_date)[:10])
        ).fetchone()
        peak = float(peak_row[0]) if peak_row and peak_row[0] else cur
        trail_from_peak = (cur - peak) / peak if peak > 0 else 0.0

        sell_reason = None
        if pct <= CM_STOP_PCT:
            sell_reason = f"손절({CM_STOP_PCT*100:.0f}%): {pct*100:.1f}%"
        elif pct > CM_TRAIL_ACTIVATE_PCT and trail_from_peak <= CM_TRAIL_PCT:
            sell_reason = f"트레일: 고점대비 {trail_from_peak*100:.1f}%"
        elif hold_days >= CM_MAX_HOLD_DAYS:
            sell_reason = f"만기({hold_days}일)"

        conn.execute(
            "UPDATE peak_holding SET current_price=?, profit_pct=?, updated_at=? WHERE id=?",
            (cur, round(pct * 100, 2), now_ts, h_id)
        )
        if sell_reason:
            sell_candidates.append({
                "stock_code": code, "stock_name": name,
                "current_price": cur, "buy_price": bp,
                "profit_pct": round(pct * 100, 2), "hold_days": hold_days,
                "entry_date": str(entry_date)[:10] if entry_date else None,
                "reason": sell_reason,
            })

    # ── 2) 매수 후보 스캔 (최근 CM_LOOKBACK_DAYS일 내 해외수주 공시) ──
    buy_candidates = []
    held_codes = {str(h[1]) for h in holdings if h[1]}
    recently_sold = _get_recently_sold_codes(conn, CM_STRATEGY, CM_COOLDOWN_DAYS)
    excluded = held_codes | recently_sold

    events = conn.execute("""
        SELECT stock_code, stock_name, disclosed_at, report_nm, contract_ratio_pct, is_overseas, ai_score
        FROM dart_contracts
        WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
          AND contract_ratio_pct IS NOT NULL AND contract_ratio_pct >= ?
          AND COALESCE(is_overseas,0)=1
    """, (CM_MIN_RATIO,)).fetchall()

    for code, ev_name, dt, report_nm, ratio, overseas, ai_score in events:
        digits = "".join(ch for ch in str(dt or "") if ch.isdigit())
        if len(digits) < 8:
            continue
        iso = f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
        if iso < lookback_date:
            continue
        if code in excluded:
            continue
        if not _cm_is_clean_contract_report(report_nm):
            continue
        rows = conn.execute(
            "SELECT close, COALESCE(volume,0), COALESCE(high,close), COALESCE(low,close), COALESCE(trade_amount,0) "
            "FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 260",
            (code,)
        ).fetchall()
        if len(rows) < 260:
            continue
        closes = [float(r[0]) for r in rows]
        vols_raw = [float(r[1]) for r in rows]
        # 2026-08-09: trade_amount(KRX ACC_TRDVAL) 결측 시 close×volume 폴백 —
        # signal_engine.py/screener.py와 동일 방어패턴(2026-07~08 회귀 재발 방지).
        amts = [float(r[4]) if r[4] and r[4] > 0 else closes[i] * vols_raw[i] for i, r in enumerate(rows)]
        cur = closes[0]
        ma20 = sum(closes[:20]) / 20
        avg20_amt = sum(amts[:20]) / 20
        if ma20 <= 0 or avg20_amt < CM_MIN_AVG20_AMT:
            continue
        highs = [float(r[2]) for r in rows]; lows = [float(r[3]) for r in rows]
        hi252 = max(highs[:252]); lo252 = min(lows[:252])
        if hi252 <= lo252:
            continue
        pos52 = (cur - lo252) / (hi252 - lo252)
        if pos52 > 1.0:
            continue
        if cur / ma20 - 1 < 0.0:
            continue
        existing = next((b for b in buy_candidates if b["stock_code"] == code), None)
        if existing:
            if ratio > existing["_ratio"]:
                existing.update(dict(ratio_pct=round(ratio, 1), _ratio=ratio, disclosed_at=iso, report_nm=report_nm))
            continue
        buy_candidates.append({
            "stock_code": code, "stock_name": ev_name or code,
            "current_price": cur, "ratio_pct": round(ratio, 1), "_ratio": ratio,
            "ai_score": round(float(ai_score or 0), 1),
            "disclosed_at": iso, "report_nm": report_nm,
            "pos52": round(pos52, 2),
        })
    buy_candidates.sort(key=lambda x: (-x["_ratio"], -x["ai_score"]))
    for b in buy_candidates:
        b.pop("_ratio", None)
    buy_candidates = buy_candidates[:20]

    # ── 3) 예산 요약 ──────────────────────────────────────────────
    active_cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (CM_STRATEGY,)
    ).fetchone()[0] or 0
    total_invested = conn.execute(
        "SELECT COALESCE(SUM(buy_price*quantity),0) FROM peak_holding WHERE strategy=? AND is_active=1",
        (CM_STRATEGY,)
    ).fetchone()[0] or 0.0
    avail_cash = max(0.0, CM_CAPITAL_KRW * (1 - CM_CASH_RESERVE) - float(total_invested))

    summary = {
        "strategy": CM_STRATEGY,
        "capital_krw": CM_CAPITAL_KRW,
        "ticket_krw": CM_TICKET_KRW,
        "active_positions": active_cnt,
        "total_invested": round(float(total_invested)),
        "avail_cash": round(avail_cash),
        "sell_count": len(sell_candidates),
        "buy_count": len(buy_candidates),
        "updated_at": now_ts,
    }

    holdings_list = []
    for h_id, code, name, buy_price, qty, entry_date, cur_cached in holdings:
        bp = float(buy_price or 0)
        cur = float(cur_cached or bp)
        pct = (cur - bp) / bp * 100 if bp > 0 else 0.0
        try:
            from datetime import date as _d2
            hold_days_h = (_d2.today() - _d2.fromisoformat(str(entry_date)[:10])).days
        except Exception:
            hold_days_h = 0
        holdings_list.append({
            "stock_code": code, "stock_name": name,
            "buy_price": bp, "current_price": cur,
            "profit_pct": round(pct, 2),
            "entry_date": str(entry_date)[:10] if entry_date else None,
            "hold_days": hold_days_h,
        })

    return {
        "sell_candidates": sell_candidates,
        "buy_candidates": buy_candidates,
        "holdings": holdings_list,
        "summary": summary,
    }


def _get_cm_cached_or_build(force: bool = False):
    now = time.time()
    if (not force and _cm_cache.get("data") and _cm_cache.get("updated_epoch", 0)
            and now - _cm_cache["updated_epoch"] < _CM_CACHE_TTL_SEC):
        return _cm_cache["updated_at"], _cm_cache["data"]
    conn = _db()
    data = _build_cm_recommendations(conn)
    conn.commit()
    conn.close()
    now_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    _cm_cache["updated_at"] = now_ts
    _cm_cache["updated_epoch"] = now
    _cm_cache["data"] = data
    return now_ts, data


@router.get("/cm/recommendations")
def get_cm_recommendations():
    updated_at, data = _get_cm_cached_or_build(force=False)
    return {"ok": True, "updated_at": updated_at, **data, "cache_ttl_sec": _CM_CACHE_TTL_SEC}


@router.post("/cm/execute")
def execute_cm_now():
    """V-CONTRACT-MOMENTUM 해외수주 모멘텀 가상매매 즉시 실행."""
    _, data = _get_cm_cached_or_build(force=True)
    conn = _db()
    _ensure_peak_holding_reason_columns(conn)
    sold = 0
    bought = 0
    today = _dt.now().strftime("%Y-%m-%d")
    now_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    sold_rows: list[dict] = []
    bought_rows: list[dict] = []

    for s in data["sell_candidates"]:
        code = s["stock_code"]
        price = float(s["current_price"])
        row = conn.execute(
            "SELECT id, quantity, buy_price FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (code, CM_STRATEGY),
        ).fetchone()
        if not row:
            continue
        h_id, qty, buy_price = row
        qty = int(qty or 0)
        if qty <= 0:
            continue
        bp = float(buy_price or 0)
        profit = round((price - bp) * qty)
        profit_pct = round((price - bp) / bp * 100, 2) if bp > 0 else 0.0
        conn.execute(
            "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=? WHERE id=?",
            (price, now_ts, price, profit_pct, now_ts, h_id),
        )
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) VALUES (?,?,?,?,?,?,?,?,?)",
            (s["stock_name"], "sell", price, qty, round(price * qty), profit, profit_pct, now_ts, CM_STRATEGY),
        )
        _record_paper_trade(
            conn, strategy=CM_STRATEGY, side="sell", code=code, name=s["stock_name"],
            holding_id=int(h_id), qty=qty, price=price, trade_id=int(trade_cursor.lastrowid),
            occurred_at=now_ts, gross_profit=profit,
        )
        sold += 1
        sold_rows.append({"stock_name": s["stock_name"], "stock_code": code, "qty": qty, "price": price, "profit_pct": profit_pct})
        logger.info(f"[V-CM 매도] {s['stock_name']} {profit_pct:.1f}% — {s['reason']}")

    avail_cash = _investable_cash(conn, CM_STRATEGY, CM_CAPITAL_KRW, CM_CASH_RESERVE)
    active_cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (CM_STRATEGY,)
    ).fetchone()[0] or 0

    for b in data["buy_candidates"][:CM_MAX_BUYS_PER_RUN]:
        if avail_cash < CM_TICKET_KRW or active_cnt >= CM_MAX_POSITIONS:
            break
        code = b["stock_code"]
        name = b["stock_name"]
        cur = float(b["current_price"])
        if cur <= 0:
            continue
        existing = conn.execute(
            "SELECT COUNT(*) FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1",
            (code, CM_STRATEGY)
        ).fetchone()[0]
        if existing > 0:
            continue
        qty = int(CM_TICKET_KRW // cur)
        if qty <= 0:
            continue
        gate = _paper_buy_gate(code, CM_STRATEGY, qty, cur)
        if gate["decision"] != "BUY_ALLOWED":
            logger.warning("[V-CM 매수차단] %s %s", code, gate["decision"])
            continue
        reason_text = f"V-CM ratio={b['ratio_pct']}% ai={b['ai_score']} pos52={b['pos52']} 공시{b['disclosed_at']}"
        holding_cursor = conn.execute(
            """INSERT INTO peak_holding
               (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at,entry_reason_text,entry_reason_json,entry_reason_updated_at)
               VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)""",
            (code, name, "", cur, cur, qty, today, CM_STRATEGY, reason_text, json.dumps(b, ensure_ascii=False)),
        )
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) VALUES (?,?,?,?,?,0,0.0,?,?)",
            (name, "buy", cur, qty, round(cur * qty), now_ts, CM_STRATEGY),
        )
        _record_paper_trade(
            conn, strategy=CM_STRATEGY, side="buy", code=code, name=name,
            holding_id=int(holding_cursor.lastrowid), qty=qty, price=cur,
            trade_id=int(trade_cursor.lastrowid), occurred_at=now_ts,
        )
        bought += 1
        active_cnt += 1
        avail_cash -= cur * qty
        bought_rows.append({"stock_name": name, "stock_code": code, "qty": qty, "price": cur})
        logger.info(f"[V-CM 매수] {name} {cur:,}원 × {qty}주 ratio={b['ratio_pct']}%")

    conn.commit()
    conn.close()
    _cm_cache["updated_at"] = now_ts
    _cm_cache["updated_epoch"] = time.time()
    _cm_cache["data"] = data
    return {
        "ok": True, "updated_at": now_ts,
        "sold": sold, "bought": bought,
        "sold_rows": sold_rows, "bought_rows": bought_rows,
        "summary": data["summary"],
    }


def _build_rec_recommendations(conn) -> dict:
    """V-RECOVERY 매수/매도 추천 생성."""
    from datetime import datetime as _now_dt
    now_ts = _now_dt.now().strftime("%Y-%m-%d %H:%M:%S")

    # ── 1) 보유 포지션 매도 체크 ──────────────────────────────────
    holdings = conn.execute(
        """SELECT id, stock_code, stock_name, buy_price, quantity, entry_date, current_price
           FROM peak_holding WHERE strategy=? AND is_active=1 ORDER BY entry_date""",
        (REC_STRATEGY,)
    ).fetchall()

    sell_candidates = []
    for h_id, code, name, buy_price, qty, entry_date, cur_cached in holdings:
        bp = float(buy_price or 0)
        if bp <= 0:
            continue
        series = _rec_get_series(conn, code, 5)
        cur = series[0][0] if series else 0.0
        if cur <= 0:
            cur = float(cur_cached or bp)
        pct = (cur - bp) / bp
        try:
            from datetime import date as _d
            ed = _d.fromisoformat(str(entry_date)[:10])
            hold_days = (_d.today() - ed).days
        except Exception:
            hold_days = 0
        peak_row = conn.execute(
            "SELECT MAX(close) FROM price_history WHERE stock_code=? AND close>0 AND date >= ?",
            (code, str(entry_date)[:10])
        ).fetchone()
        peak = float(peak_row[0]) if peak_row and peak_row[0] else cur
        trail_from_peak = (cur - peak) / peak if peak > 0 else 0.0

        sell_reason = None
        if pct <= REC_STOP_PCT:
            sell_reason = f"손절({REC_STOP_PCT*100:.0f}%): {pct*100:.1f}%"
        elif pct >= REC_TP_PCT:
            sell_reason = f"익절(+{REC_TP_PCT*100:.0f}%): {pct*100:.1f}%"
        elif trail_from_peak <= (REC_TRAIL_BIG if pct >= 0.50 else REC_TRAIL_PCT):
            sell_reason = f"트레일: 고점대비 {trail_from_peak*100:.1f}%"
        elif hold_days >= REC_MAX_HOLD_DAYS:
            sell_reason = f"만기({hold_days}일)"

        conn.execute(
            "UPDATE peak_holding SET current_price=?, profit_pct=?, updated_at=? WHERE id=?",
            (cur, round(pct * 100, 2), now_ts, h_id)
        )
        if sell_reason:
            sell_candidates.append({
                "stock_code": code, "stock_name": name,
                "current_price": cur, "buy_price": bp,
                "profit_pct": round(pct * 100, 2), "hold_days": hold_days,
                "entry_date": str(entry_date)[:10] if entry_date else None,
                "reason": sell_reason,
            })

    # ── 2) 매수 후보 스캔 ─────────────────────────────────────────
    buy_candidates = []
    if not _rec_kospi_panic(conn):
        held_codes = {str(h[1]) for h in holdings if h[1]}
        recently_sold = _get_recently_sold_codes(conn, REC_STRATEGY, REC_COOLDOWN_DAYS)
        excluded = held_codes | recently_sold

        universe = conn.execute(
            """SELECT su.stock_code, su.stock_name, su.market_cap
               FROM stock_universe su
               WHERE su.market IN ('KOSPI','KOSDAQ') AND su.market_cap >= ?
                 AND LENGTH(su.stock_code)=6
                 AND su.stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
               ORDER BY su.market_cap DESC LIMIT 3000""",
            (REC_MIN_MKTCAP,)
        ).fetchall()

        for code, name, mktcap in universe:
            if code in excluded:
                continue
            s = _rec_get_series(conn, code, 260)
            if len(s) < 80:
                continue
            closes = [x[0] for x in s]
            vols   = [x[1] for x in s]
            lows   = [x[2] for x in s]
            cur = closes[0]
            if cur < 500:
                continue
            # [A][B] MA60 대비 낙폭 -20~-65%
            ma60 = sum(closes[:60]) / 60
            if ma60 <= 0:
                continue
            depth = (cur - ma60) / ma60
            if depth > REC_DEPTH_MIN or depth < REC_DEPTH_MAX:
                continue
            # [C] 52주 저점 대비 +40% 이내
            low52 = min(lows[:252]) if lows else cur
            if low52 <= 0:
                continue
            pct_from_low = (cur - low52) / low52 * 100
            if pct_from_low > REC_LOW_MAX_PCT:
                continue
            # [D] 당일 거래량 ≥ 20일 평균 × 2.0
            v_now = vols[0]
            v_avg20 = sum(vols[1:21]) / max(1, len(vols[1:21]))
            if v_now <= 0 or v_avg20 <= 0 or v_now < v_avg20 * REC_VOL_RATIO:
                continue
            # [E] 최근 3일 중 2일 이상 상승
            if len(closes) >= 4:
                up_days = sum(1 for j in range(3) if closes[j] > closes[j + 1])
                if up_days < 2:
                    continue
            # 복합 점수 (백테스트와 동일): 낙폭 70% + 저점위치 보너스 + 흑자전환 +20pt + 수급 +20pt
            depth_score = min(-depth * 100, 50)
            low_bonus = 10.0 if 30 <= pct_from_low <= 80 else (5.0 if pct_from_low < 30 else 0.0)
            is_ta = _rec_is_turnaround(conn, code)
            flow_row = conn.execute(
                "SELECT COALESCE(SUM(COALESCE(inst_net_buy,0)+COALESCE(frn_net_buy,0)),0) FROM ("
                "SELECT inst_net_buy, frn_net_buy FROM price_history "
                "WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 5)",
                (code,)
            ).fetchone()
            is_flow = bool(flow_row and float(flow_row[0] or 0) > 0)
            score = (depth_score * 0.7 + low_bonus
                     + (REC_TA_BONUS if is_ta else 0.0)
                     + (REC_FLOW_BONUS if is_flow else 0.0))
            buy_candidates.append({
                "stock_code": code, "stock_name": name,
                "current_price": cur,
                "depth_pct": round(depth * 100, 1),
                "pct_from_low": round(pct_from_low, 1),
                "vol_x": round(v_now / v_avg20, 1),
                "turnaround": bool(is_ta),
                "flow": is_flow,
                "score": round(score, 1),
                "mktcap_억": int(mktcap or 0),
            })
        buy_candidates.sort(key=lambda x: -x["score"])
        buy_candidates = buy_candidates[:20]

    # ── 3) 예산 요약 ──────────────────────────────────────────────
    active_cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (REC_STRATEGY,)
    ).fetchone()[0] or 0
    total_invested = conn.execute(
        "SELECT COALESCE(SUM(buy_price*quantity),0) FROM peak_holding WHERE strategy=? AND is_active=1",
        (REC_STRATEGY,)
    ).fetchone()[0] or 0.0
    avail_cash = max(0.0, REC_CAPITAL_KRW * (1 - REC_CASH_RESERVE) - float(total_invested))

    summary = {
        "strategy": REC_STRATEGY,
        "capital_krw": REC_CAPITAL_KRW,
        "ticket_krw": REC_TICKET_KRW,
        "active_positions": active_cnt,
        "total_invested": round(float(total_invested)),
        "avail_cash": round(avail_cash),
        "sell_count": len(sell_candidates),
        "buy_count": len(buy_candidates),
        "updated_at": now_ts,
    }

    holdings_list = []
    for h_id, code, name, buy_price, qty, entry_date, cur_cached in holdings:
        bp = float(buy_price or 0)
        cur = float(cur_cached or bp)
        pct = (cur - bp) / bp * 100 if bp > 0 else 0.0
        try:
            from datetime import date as _d2
            hold_days_h = (_d2.today() - _d2.fromisoformat(str(entry_date)[:10])).days
        except Exception:
            hold_days_h = 0
        holdings_list.append({
            "stock_code": code, "stock_name": name,
            "buy_price": bp, "current_price": cur,
            "profit_pct": round(pct, 2),
            "entry_date": str(entry_date)[:10] if entry_date else None,
            "hold_days": hold_days_h,
        })

    return {
        "sell_candidates": sell_candidates,
        "buy_candidates": buy_candidates,
        "holdings": holdings_list,
        "summary": summary,
    }


def _get_rec_cached_or_build(force: bool = False):
    now = time.time()
    if (not force and _rec_cache.get("data") and _rec_cache.get("updated_epoch", 0)
            and (now - _rec_cache["updated_epoch"]) < _REC_CACHE_TTL_SEC):
        return _rec_cache["updated_at"], _rec_cache["data"]
    conn = _db()
    try:
        data = _build_rec_recommendations(conn)
        conn.commit()
    finally:
        conn.close()
    _rec_cache["data"] = data
    _rec_cache["updated_at"] = data["summary"]["updated_at"]
    _rec_cache["updated_epoch"] = now
    return _rec_cache["updated_at"], data


@router.get("/rec/recommendations")
def get_rec_recommendations():
    updated_at, data = _get_rec_cached_or_build(force=False)
    return {"ok": True, "updated_at": updated_at, **data}


@router.post("/rec/execute")
def execute_rec_now():
    """V-RECOVERY 낙폭반등 가상매매 즉시 실행."""
    _, data = _get_rec_cached_or_build(force=True)
    conn = _db()
    _ensure_peak_holding_reason_columns(conn)
    sold = 0
    bought = 0
    today = _dt.now().strftime("%Y-%m-%d")
    now_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    sold_rows: list[dict] = []
    bought_rows: list[dict] = []

    for s in data["sell_candidates"]:
        code = s["stock_code"]
        price = float(s["current_price"])
        row = conn.execute(
            "SELECT id, quantity, buy_price FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (code, REC_STRATEGY),
        ).fetchone()
        if not row:
            continue
        h_id, qty, buy_price = row
        qty = int(qty or 0)
        if qty <= 0:
            continue
        bp = float(buy_price or 0)
        profit = round((price - bp) * qty)
        profit_pct = round((price - bp) / bp * 100, 2) if bp > 0 else 0.0
        conn.execute(
            "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=? WHERE id=?",
            (price, now_ts, price, profit_pct, now_ts, h_id),
        )
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) VALUES (?,?,?,?,?,?,?,?,?)",
            (s["stock_name"], "sell", price, qty, round(price * qty), profit, profit_pct, now_ts, REC_STRATEGY),
        )
        _record_paper_trade(
            conn, strategy=REC_STRATEGY, side="sell", code=code, name=s["stock_name"],
            holding_id=int(h_id), qty=qty, price=price, trade_id=int(trade_cursor.lastrowid),
            occurred_at=now_ts, gross_profit=profit,
        )
        sold += 1
        sold_rows.append({"stock_name": s["stock_name"], "stock_code": code, "qty": qty, "price": price, "profit_pct": profit_pct})
        logger.info(f"[V-REC 매도] {s['stock_name']} {profit_pct:.1f}% — {s['reason']}")

    avail_cash = _investable_cash(conn, REC_STRATEGY, REC_CAPITAL_KRW, REC_CASH_RESERVE)
    active_cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (REC_STRATEGY,)
    ).fetchone()[0] or 0

    for b in data["buy_candidates"][:REC_MAX_BUYS_PER_RUN]:
        if avail_cash < REC_TICKET_KRW or active_cnt >= REC_MAX_POSITIONS:
            break
        code = b["stock_code"]
        name = b["stock_name"]
        cur = float(b["current_price"])
        if cur <= 0:
            continue
        existing = conn.execute(
            "SELECT COUNT(*) FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1",
            (code, REC_STRATEGY)
        ).fetchone()[0]
        if existing > 0:
            continue
        qty = int(REC_TICKET_KRW // cur)
        if qty <= 0:
            continue
        gate = _paper_buy_gate(code, REC_STRATEGY, qty, cur)
        if gate["decision"] != "BUY_ALLOWED":
            logger.warning("[V-REC 매수차단] %s %s", code, gate["decision"])
            continue
        reason_text = (f"V-REC depth={b['depth_pct']}% low+{b['pct_from_low']}% "
                       f"vol×{b['vol_x']}{' 흑자전환' if b.get('turnaround') else ''} score={b['score']}")
        holding_cursor = conn.execute(
            """INSERT INTO peak_holding
               (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at,entry_reason_text,entry_reason_json,entry_reason_updated_at)
               VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)""",
            (code, name, "", cur, cur, qty, today, REC_STRATEGY, reason_text, json.dumps(b, ensure_ascii=False)),
        )
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) VALUES (?,?,?,?,?,0,0.0,?,?)",
            (name, "buy", cur, qty, round(cur * qty), now_ts, REC_STRATEGY),
        )
        _record_paper_trade(
            conn, strategy=REC_STRATEGY, side="buy", code=code, name=name,
            holding_id=int(holding_cursor.lastrowid), qty=qty, price=cur,
            trade_id=int(trade_cursor.lastrowid), occurred_at=now_ts,
        )
        bought += 1
        active_cnt += 1
        avail_cash -= cur * qty
        bought_rows.append({"stock_name": name, "stock_code": code, "qty": qty, "price": cur})
        logger.info(f"[V-REC 매수] {name} {cur:,}원 × {qty}주 score={b['score']}")

    conn.commit()
    conn.close()
    _rec_cache["updated_at"] = now_ts
    _rec_cache["updated_epoch"] = time.time()
    _rec_cache["data"] = data
    return {
        "ok": True, "updated_at": now_ts,
        "sold": sold, "bought": bought,
        "sold_rows": sold_rows, "bought_rows": bought_rows,
        "summary": data["summary"],
    }


# ── GET /api/trend/v18/recommendations ──────────────────────────
@router.get("/v18/recommendations")
def get_v18_recommendations():
    updated_at, data = _get_v18_cached_or_build(force=False)
    return {"ok": True, "updated_at": updated_at, **data, "cache_ttl_sec": _V18_CACHE_TTL_SEC}


# ── POST /api/trend/v18/execute ─────────────────────────────────
@router.post("/v18/execute")
def execute_v18_now():
    """
    V18 가상매매 즉시 실행:
    1) sell_candidates 우선 청산
    2) buy_candidates 상위부터 종목당 1,200만원 가상 매수
    """
    _, data = _get_v18_cached_or_build(force=True)
    conn = _db()
    sold = 0
    bought = 0
    today = _dt.now().strftime("%Y-%m-%d")
    now_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    sold_rows: list[dict] = []
    bought_rows: list[dict] = []

    # 1) sells
    for s in data["sell_candidates"]:
        name = s["stock_name"]
        price = _safe_float(s["current_price"])
        row = conn.execute(
            "SELECT id, quantity, buy_price FROM peak_holding WHERE stock_name=? AND strategy=? AND is_active=1 ORDER BY id DESC LIMIT 1",
            (name, V18_STRATEGY),
        ).fetchone()
        if not row:
            continue
        h_id, qty, buy_price = row
        qty = int(qty or 0)
        if qty <= 0:
            continue
        profit = round((price - _safe_float(buy_price)) * qty)
        profit_pct = round((price - _safe_float(buy_price)) / _safe_float(buy_price) * 100, 2) if _safe_float(buy_price) > 0 else 0.0
        conn.execute(
            """
            UPDATE peak_holding
            SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (price, now_ts, price, profit_pct, h_id),
        )
        conn.execute(
            """
            INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (name, "sell", price, qty, round(price * qty), profit, profit_pct, now_ts, V18_STRATEGY),
        )
        sold += 1
        sold_rows.append(
            {
                "stock_name": name,
                "stock_code": s.get("stock_code") or "",
                "qty": qty,
                "price": price,
                "profit_pct": profit_pct,
            }
        )

    # 2) buys — 예산 체크 후 상위 5개까지
    # 현재 투자금액 재계산 (sells 이후 현금이 늘었을 수 있음)
    total_invested_now = conn.execute(
        "SELECT COALESCE(SUM(buy_price * quantity), 0) FROM peak_holding WHERE strategy=? AND is_active=1",
        (V18_STRATEGY,),
    ).fetchone()[0] or 0.0
    investable_cap_now = VIRTUAL_CAPITAL * (1.0 - CASH_RESERVE_PCT)
    avail_cash = max(0.0, investable_cap_now - float(total_invested_now))

    for b in data["buy_candidates"][:5]:
        code = b["stock_code"]
        name = b["stock_name"]

        # 예산 소진 체크 (현금 보유 원칙 준수)
        if avail_cash < V18_TICKET_KRW:
            logger.info(
                f"[V18매수중단] 예산 소진: 투자가능 {avail_cash:,.0f}원 < 티켓 {V18_TICKET_KRW:,}원 "
                f"(총투자 {total_invested_now:,.0f}원 / 한도 {investable_cap_now:,.0f}원)"
            )
            break

        # 티켓 수 체크 (피라미딩: MAX_TICKETS 미만이면 추가 매수 허용)
        ticket_count = conn.execute(
            "SELECT COUNT(*) FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1",
            (code, V18_STRATEGY),
        ).fetchone()[0]
        max_t = MAX_TICKETS_V_ANCHOR if code in V_ANCHOR_UNIVERSE else MAX_TICKETS_COMBO
        if ticket_count >= max_t:
            continue  # 최대 티켓 도달 → 추가 매수 불가
        cur, _, _ = _latest_price_and_ma(conn, code, 20)
        if cur <= 0:
            continue
        qty = int(V18_TICKET_KRW // cur)
        if qty <= 0:
            continue
        reason_text = f"V18 score={b.get('score')} match={b.get('match_count')} reason={b.get('reason')}"
        reason_json = json.dumps(b, ensure_ascii=False)
        conn.execute(
            """
            INSERT INTO peak_holding
            (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at,entry_reason_text,entry_reason_json,entry_reason_updated_at)
            VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)
            """,
            (code, name, "", cur, cur, qty, today, V18_STRATEGY, reason_text, reason_json),
        )
        conn.execute(
            """
            INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
            VALUES (?,?,?,?,?,0,0.0,?,?)
            """,
            (name, "buy", cur, qty, round(cur * qty), now_ts, V18_STRATEGY),
        )
        bought += 1
        bought_rows.append(
            {
                "stock_name": name,
                "stock_code": code,
                "qty": qty,
                "price": cur,
            }
        )
        # 이번 매수 반영해 가용 현금 차감 (다음 매수 전 재체크)
        avail_cash -= cur * qty

    conn.commit()
    conn.close()
    _v18_cache["updated_at"] = now_ts
    _v18_cache["updated_epoch"] = time.time()
    _v18_cache["data"] = data
    _send_v18_telegram_alert(data, sold_rows, bought_rows, now_ts)
    return {
        "ok": True,
        "updated_at": now_ts,
        "sold": sold,
        "bought": bought,
        "summary": data["summary"],
    }


# ── GET /api/trend/turnover/recommendations ─────────────────────
@router.get("/turnover/recommendations")
def get_turnover_recommendations():
    conn = _db()
    try:
        cands = _turnover_fetch_candidates(conn)
        return {
            "ok": True,
            "strategy": TURNOVER_STRATEGY,
            "capital_krw": TURNOVER_CAPITAL_KRW,
            "ticket_krw": TURNOVER_TICKET_KRW,
            "max_positions": TURNOVER_MAX_POS,
            "rules": {
                "stop_loss_pct": TURNOVER_STOP_PCT,
                "tp1_pct": TURNOVER_TP1_PCT,
                "tp1_sell_ratio": 0.5,
                "tp2_pct": TURNOVER_TP2_PCT,
                "tp2_sell_ratio": 1.0,
            },
            "count": len(cands),
            "candidates": cands,
        }
    finally:
        conn.close()


# ── POST /api/trend/turnover/execute ────────────────────────────
@router.post("/turnover/execute")
def execute_turnover_now(payload: dict | None = None):
    """
    회전율 단타 가상매매 실행:
    - 손절 -10%: 전량 매도
    - +10%: 50% 1회 매도
    - +20%: 잔여 전량 매도
    - TP1 이후: 본전이탈(+0.5% 미만) 또는 피크대비 -4% 트레일링 시 잔여 청산
    - 시간청산: 7일 경과 + 수익률 2% 미만이면 잔여 청산
    - 신규매수: 회전율 후보 상위에서 예산/최대보유 제한 내 진입
    """
    strat = str((payload or {}).get("strategy") or TURNOVER_STRATEGY)
    now_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    today = _dt.now().strftime("%Y-%m-%d")
    conn = _db()
    _ensure_peak_holding_reason_columns(conn)

    sold = 0
    bought = 0
    sold_rows: list[dict] = []
    bought_rows: list[dict] = []

    # 1) 보유 포지션 관리(매도 먼저)
    holdings = conn.execute(
        """
        SELECT id, stock_code, stock_name, buy_price, quantity, entry_reason_json, entry_date
        FROM peak_holding
        WHERE strategy=? AND is_active=1
        ORDER BY entry_date ASC, id ASC
        """,
        (strat,),
    ).fetchall()

    for h in holdings:
        h_id, code, name, buy_price, qty, reason_json, entry_date = h
        qty = int(qty or 0)
        bp = _safe_float(buy_price)
        if qty <= 0 or bp <= 0:
            continue
        cur, _, _ = _latest_price_and_ma(conn, str(code or ""), 20)
        if cur <= 0:
            continue
        pct = (cur - bp) / bp * 100.0
        st = _turnover_state_from_reason(reason_json)
        tp1_done = bool(st.get("tp1_done", False))
        max_profit_pct = _safe_float(st.get("max_profit_pct"))
        if pct > max_profit_pct:
            max_profit_pct = pct
            st["max_profit_pct"] = round(max_profit_pct, 3)
            conn.execute(
                """
                UPDATE peak_holding
                SET entry_reason_json=?, entry_reason_updated_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (json.dumps(st, ensure_ascii=False), h_id),
            )

        hold_days = 0
        try:
            hold_days = (_dt.now().date() - _dt.strptime(str(entry_date), "%Y-%m-%d").date()).days
        except Exception:
            hold_days = 0

        # 하드 손절
        if pct <= TURNOVER_STOP_PCT:
            sell_qty = qty
            profit = round((cur - bp) * sell_qty)
            profit_pct = round((cur - bp) / bp * 100.0, 2)
            conn.execute(
                """
                UPDATE peak_holding
                SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, quantity=0, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (cur, now_ts, cur, profit_pct, h_id),
            )
            conn.execute(
                """
                INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (name, "sell", cur, sell_qty, round(cur * sell_qty), profit, profit_pct, now_ts, strat),
            )
            sold += 1
            sold_rows.append({"stock_code": code, "stock_name": name, "qty": sell_qty, "price": cur, "profit_pct": profit_pct, "reason": "stop_-10"})
            continue

        # TP1 이후 잔여 포지션 보호
        if tp1_done:
            drawdown_from_peak = pct - max_profit_pct
            should_exit = False
            exit_reason = ""
            if pct < TURNOVER_BREAKEVEN_AFTER_TP1_PCT:
                should_exit = True
                exit_reason = "tp1_breakeven"
            elif drawdown_from_peak <= -TURNOVER_TRAIL_AFTER_TP1_PCT:
                should_exit = True
                exit_reason = "tp1_trailing"
            elif hold_days >= TURNOVER_TIME_STOP_DAYS and pct < TURNOVER_TIME_STOP_MIN_PROFIT_PCT:
                should_exit = True
                exit_reason = "time_stop"

            if should_exit:
                sell_qty = qty
                profit = round((cur - bp) * sell_qty)
                profit_pct = round((cur - bp) / bp * 100.0, 2)
                conn.execute(
                    """
                    UPDATE peak_holding
                    SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, quantity=0, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?
                    """,
                    (cur, now_ts, cur, profit_pct, h_id),
                )
                conn.execute(
                    """
                    INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (name, "sell", cur, sell_qty, round(cur * sell_qty), profit, profit_pct, now_ts, strat),
                )
                sold += 1
                sold_rows.append({"stock_code": code, "stock_name": name, "qty": sell_qty, "price": cur, "profit_pct": profit_pct, "reason": exit_reason})
                continue

        # 2차 익절(+20%) 전량
        if pct >= TURNOVER_TP2_PCT:
            sell_qty = qty
            profit = round((cur - bp) * sell_qty)
            profit_pct = round((cur - bp) / bp * 100.0, 2)
            conn.execute(
                """
                UPDATE peak_holding
                SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, quantity=0, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (cur, now_ts, cur, profit_pct, h_id),
            )
            conn.execute(
                """
                INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (name, "sell", cur, sell_qty, round(cur * sell_qty), profit, profit_pct, now_ts, strat),
            )
            sold += 1
            sold_rows.append({"stock_code": code, "stock_name": name, "qty": sell_qty, "price": cur, "profit_pct": profit_pct, "reason": "tp2_20_full"})
            continue

        # 1차 익절(+10%) 50% 1회
        if (pct >= TURNOVER_TP1_PCT) and (not tp1_done):
            sell_qty = max(1, math.ceil(qty * 0.5))
            remain_qty = max(0, qty - sell_qty)
            profit = round((cur - bp) * sell_qty)
            profit_pct = round((cur - bp) / bp * 100.0, 2)

            st["tp1_done"] = True
            st["tp1_at"] = now_ts
            st["tp1_price"] = round(cur, 2)
            new_reason = json.dumps(st, ensure_ascii=False)

            conn.execute(
                """
                UPDATE peak_holding
                SET quantity=?, current_price=?, profit_pct=?, entry_reason_json=?, entry_reason_updated_at=CURRENT_TIMESTAMP, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (remain_qty, cur, profit_pct, new_reason, h_id),
            )
            conn.execute(
                """
                INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
                VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (name, "sell", cur, sell_qty, round(cur * sell_qty), profit, profit_pct, now_ts, strat),
            )
            sold += 1
            sold_rows.append({"stock_code": code, "stock_name": name, "qty": sell_qty, "price": cur, "profit_pct": profit_pct, "reason": "tp1_10_half"})

    # 2) 신규 매수
    active_after_sell = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1",
        (strat,),
    ).fetchone()[0] or 0
    invested_now = conn.execute(
        "SELECT COALESCE(SUM(buy_price * quantity), 0) FROM peak_holding WHERE strategy=? AND is_active=1",
        (strat,),
    ).fetchone()[0] or 0.0
    remaining_cash = max(0.0, TURNOVER_CAPITAL_KRW - float(invested_now))

    held_codes = {
        str(r[0] or "")
        for r in conn.execute(
            "SELECT DISTINCT stock_code FROM peak_holding WHERE strategy=? AND is_active=1",
            (strat,),
        ).fetchall()
    }
    cands = _turnover_fetch_candidates(conn)
    for c in cands:
        if active_after_sell >= TURNOVER_MAX_POS:
            break
        if remaining_cash < TURNOVER_TICKET_KRW:
            break

        code = str(c.get("stock_code") or "")
        name = c.get("stock_name") or code
        if not code or code in held_codes:
            continue
        cur = _safe_float(c.get("close"))
        if cur <= 0:
            continue
        qty = int(TURNOVER_TICKET_KRW // cur)
        if qty <= 0:
            continue

        reason_state = {
            "model": strat,
            "tp1_done": False,
            "max_profit_pct": 0.0,
            "rule": {
                "stop": TURNOVER_STOP_PCT,
                "tp1": TURNOVER_TP1_PCT,
                "tp2": TURNOVER_TP2_PCT,
                "trail_after_tp1": TURNOVER_TRAIL_AFTER_TP1_PCT,
                "breakeven_after_tp1": TURNOVER_BREAKEVEN_AFTER_TP1_PCT,
                "time_stop_days": TURNOVER_TIME_STOP_DAYS,
                "time_stop_min_profit": TURNOVER_TIME_STOP_MIN_PROFIT_PCT,
            },
            "candidate": c,
        }
        conn.execute(
            """
            INSERT INTO peak_holding
            (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at,entry_reason_text,entry_reason_json,entry_reason_updated_at)
            VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)
            """,
            (
                code, name, c.get("sector") or "", cur, cur, qty, today, strat,
                f"turnover_breakout score={c.get('score')} turnover={c.get('turnover_pct')}%",
                json.dumps(reason_state, ensure_ascii=False),
            ),
        )
        conn.execute(
            """
            INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
            VALUES (?,?,?,?,?,0,0.0,?,?)
            """,
            (name, "buy", cur, qty, round(cur * qty), now_ts, strat),
        )
        bought += 1
        bought_rows.append({"stock_code": code, "stock_name": name, "qty": qty, "price": cur})
        held_codes.add(code)
        active_after_sell += 1
        remaining_cash -= cur * qty

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "strategy": strat,
        "updated_at": now_ts,
        "sold": sold,
        "bought": bought,
        "capital_krw": TURNOVER_CAPITAL_KRW,
        "ticket_krw": TURNOVER_TICKET_KRW,
        "max_positions": TURNOVER_MAX_POS,
        "rules": {
            "stop_loss_pct": TURNOVER_STOP_PCT,
            "tp1_pct": TURNOVER_TP1_PCT,
            "tp1_sell_ratio": 0.5,
            "tp2_pct": TURNOVER_TP2_PCT,
            "tp2_sell_ratio": 1.0,
            "tp1_breakeven_pct": TURNOVER_BREAKEVEN_AFTER_TP1_PCT,
            "trail_after_tp1_pct": TURNOVER_TRAIL_AFTER_TP1_PCT,
            "time_stop_days": TURNOVER_TIME_STOP_DAYS,
            "time_stop_min_profit_pct": TURNOVER_TIME_STOP_MIN_PROFIT_PCT,
        },
        "sold_rows": sold_rows,
        "bought_rows": bought_rows,
    }


def _turnover_auto_worker():
    while True:
        with _turnover_auto_lock:
            running = bool(_turnover_auto_state.get("running"))
            interval = int(_turnover_auto_state.get("interval_sec") or 300)
        if not running:
            break
        try:
            # 장중에만 자동 실행 (장외는 상태만 갱신)
            if _is_kr_market_open():
                res = execute_turnover_now({"strategy": TURNOVER_AUTO_STRATEGY})
                with _turnover_auto_lock:
                    _turnover_auto_state["last_run_at"] = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                    _turnover_auto_state["last_result"] = res
                    _turnover_auto_state["last_error"] = None
            else:
                with _turnover_auto_lock:
                    _turnover_auto_state["last_run_at"] = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
                    _turnover_auto_state["last_result"] = {
                        "ok": True,
                        "strategy": TURNOVER_AUTO_STRATEGY,
                        "sold": 0,
                        "bought": 0,
                        "note": "market_closed_skip",
                    }
                    _turnover_auto_state["last_error"] = None
        except Exception as e:
            with _turnover_auto_lock:
                _turnover_auto_state["last_error"] = str(e)
        time.sleep(max(30, interval))


@router.get("/turnover/auto/status")
def turnover_auto_status():
    with _turnover_auto_lock:
        return {
            "ok": True,
            "strategy": TURNOVER_AUTO_STRATEGY,
            "running": bool(_turnover_auto_state.get("running")),
            "interval_sec": int(_turnover_auto_state.get("interval_sec") or 300),
            "last_run_at": _turnover_auto_state.get("last_run_at"),
            "last_result": _turnover_auto_state.get("last_result"),
            "last_error": _turnover_auto_state.get("last_error"),
        }


@router.post("/turnover/auto/start")
def turnover_auto_start(payload: dict | None = None):
    interval = int((payload or {}).get("interval_sec") or 300)
    interval = max(30, interval)
    with _turnover_auto_lock:
        _turnover_auto_state["interval_sec"] = interval
        if _turnover_auto_state.get("running"):
            return {"ok": True, "running": True, "interval_sec": interval, "note": "already_running"}
        _turnover_auto_state["running"] = True
        _turnover_auto_state["thread_name"] = f"turnover-auto-{int(time.time())}"
    th = threading.Thread(target=_turnover_auto_worker, daemon=True, name=_turnover_auto_state["thread_name"])
    th.start()
    return {"ok": True, "running": True, "interval_sec": interval}


@router.post("/turnover/auto/stop")
def turnover_auto_stop():
    with _turnover_auto_lock:
        _turnover_auto_state["running"] = False
    return {"ok": True, "running": False}


# ── POST /api/trend/ai-combo/execute ────────────────────────────
@router.post("/ai-combo/execute")
def execute_ai_combo_now():
    """현재 combo-candidates 기준으로 AI 자동매매 즉시 실행."""
    # _signal_cache와 _process_ai_combo_autotrade는 main에서 임포트 (지연)
    import main as _main
    combo = _main._signal_cache.get("combo_candidates", {}).get("data", [])
    if not combo:
        import threading
        threading.Thread(target=_main._run_screener_precompute, daemon=True).start()
        return {"status": "computing", "message": "스크리너 계산 중... 30초 후 다시 시도하세요."}
    _main._process_ai_combo_autotrade(combo)
    conn = _db()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy='ai_combo' AND is_active=1"
    ).fetchone()[0]
    conn.close()
    return {"status": "ok", "message": f"AI 자동매매 실행 완료. 현재 보유: {cnt}종목", "active": cnt}


# ── DELETE /api/trend/trades/all ────────────────────────────────
@router.delete("/trades/all")
def clear_all_trades():
    conn = _db()
    conn.execute("DELETE FROM peak_trade")
    conn.commit(); conn.close()
    return {"status": "ok", "message": "매매 내역이 모두 삭제되었습니다."}


# ── PATCH /api/trend/holdings/{id}/buy-price ────────────────────
@router.patch("/holdings/{holding_id}/buy-price")
def patch_buy_price(holding_id: int, payload: dict):
    """보유종목 매수가 수정 (관리용)"""
    new_price = float(payload.get("buy_price", 0))
    if new_price <= 0:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="buy_price > 0 필요")
    conn = _db()
    conn.execute(
        "UPDATE peak_holding SET buy_price=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (new_price, holding_id)
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "id": holding_id, "buy_price": new_price}


# ══════════════════════════════════════════════════════════════════
#  전략센터 병합조합 가상매매 (2026-07-23 신규)
#
#  전략센터 성과매트릭스 "전략 조합" 탭에서 검증된 4개 병합조합
#  (605.05%/539.18%/510.12%/473.87%, persist_merged_run 등록)을
#  각각 독립된 1억원 가상계좌로 실행한다.
#
#  방식: 구성 컴포넌트 전략을(등록 당시와 동일 파라미터로) 2020-03-01~
#  최신거래일까지 매번 재실행(backtest.py 동일 함수 재사용)해, 그 중
#  "최신거래일 당일" 발생한 매수/매도 신호만 오늘의 라이브 시그널로
#  추출한다. 콤보 우선순위(등록된 priority)로 랭킹해 이 콤보 자신의
#  독립 1억원 계좌(peak_holding/peak_trade, strategy=combo_key)에
#  체결한다 — v_gc/v_recovery와 동일한 고정티켓(1,000만원)/20%현금
#  보유 패턴을 그대로 사용(merged_simulator의 동적슬롯 대신 단순
#  고정로직 채택 — 신규 1억 계좌 기준으로는 사실상 동치이고 검증된
#  기존 v_gc/v_recovery 파이프라인과 동일한 코드경로라 신뢰도가 높음).
#
#  매도 판단: (a) 원천 컴포넌트가 오늘 자신의 매도신호를 냈으면 그대로
#  반영 (b) 컴포넌트별 stop_loss를 안전망으로 상시 병행 평가 — 콤보
#  계좌의 실제 진입가/일자가 컴포넌트의 연속운용 시뮬레이션과 다를 수
#  있어(콤보는 오늘 처음 매수했는데 컴포넌트는 수년 전 매수했을 수
#  있음) 컴포넌트 자체 매도신호만으로는 누락 위험이 있기 때문.
# ══════════════════════════════════════════════════════════════════
import backtest as _bt
from merged_simulator import _neutral_tiebreak as _combo_neutral_tiebreak

COMBO_CAPITAL_KRW  = 100_000_000   # 콤보별 독립 시드 1억원
COMBO_CASH_RESERVE = 0.20
COMBO_TICKET_KRW   = 10_000_000

# 2026-08-08: 자본비례 티켓(ticket_pct) — 콤보별로 개별 검증해 통과한 것만 적용.
# 배경: 명목 고정 티켓은 계좌가 커질수록 건당 비중이 줄어 자본이 유휴로 남는다
# (실측 cmb_8d727d5b7a8f: 에쿼티 1억→7.13억인데 신규 편입은 건당 1,000만원=1.4%,
#  현금 61% 유휴). portfolio_engine.CashPortfolio.ticket_pct 참조.
# ⚠️ 보편적 개선이 아니라 "강한 엣지를 증폭하는 레버리지"라, 검증된 판별자는
#    수익률이나 승률이 아니라 **손익비(PF)가 오르는가**다(승률은 6/6 조합 전부에서
#    올랐지만 그중 2개는 총수익이 악화 — signal_experiment_ledger
#    'ticket_pct_success_discriminator_20260808' 참조).
# 라이브 4개 구성 실측(scratch/tccbridge/livesweep.py):
#   combo_539  PF -0.26~-0.83 (25%에서 수익 -12.7%/MDD -76.2%)  → 적용 불가
#   combo_605  10%만 PF +0.15이나 MDD -42.0→-61.6              → 적용 불가
#   combo_474  PF -0.05~+0.06 제자리(수익 상승은 순수 레버리지)  → 적용 불가
#   v2+sector  PF +0.23~+0.37 전 구간 상승                      → 적용
# 값 선택도 PF 기준으로 함: 학습기(2020-04~2023-06) PF 최대 25%(2.53) → 얼려서
# 검증기(2023-07~2026-07) 적용 시 401.4%(base 165.5%), 검증기 PF 최대도 25%(2.23)로 일치.
# ⚠️ 2026-08-08 보류: 25%를 플래그십(v2+sector_focus)에 적용하려다 **동점 타이브레이크 의존성**을
#    발견해 라이브 반영을 되돌림. merged_simulator의 매수 랭킹은 (-priority, stock_code, ...) 순인데
#    이 구성은 v2·sector 우선순위가 둘 다 1.0이라 실질 정렬 기준이 종목코드다. 티켓을 키우면 슬롯이
#    4개로 줄어 "그날 코드가 낮은 4종목"만 담기게 되므로, 포트폴리오가 신호 품질이 아니라 임의 기준으로
#    결정될 수 있다. 콤보별 결과가 비단조로 튄 것(combo_605: 10% 744% → 15% 388% → 20% 199% → 25% 274%)이
#    그 징후. 동점 순서를 무작위로 흔들어 수익률 분산을 재는 검증 통과 전까지는 어떤 콤보에도 적용하지 않는다.
COMBO_TICKET_PCT: dict[str, float] = {}

# component_key -> (백테스트 함수, kwargs, 안전망 stop_loss)
# kwargs는 backtest_run_specs에 기록된 등록 당시 실측 파라미터와 동일하게 고정.
# sector_focus만 trail=-0.2로 명시 오버라이드 — 등록 당시(2026-07-18) 기본값이
# -0.2였고 이후(2026-07-21) 전략 기본값이 -0.30으로 바뀌었으나, 이미 검증된
# 조합 경제성(605%/539%/510%/473%)을 그대로 재현하려면 등록시점 파라미터를
# 써야 함(현재 기본값을 그대로 쓰면 다른 전략을 라이브하는 셈이 됨).
COMBO_COMPONENTS = {
    # 2026-07-28: run_backtest/run_backtest_earnings_conviction/run_backtest_moonshot_turnaround의
    # asof_mktcap 기본값이 True로 바뀌면서(전략센터 as-of 시총 리트로핏), 이 라이브 콤보 패널이
    # "오늘 하루치 신호"만 필요한데도 전체 유니버스의 security_share_history를 매번 새로 스캔하는
    # 무거운 as-of 경로를 타게 되어 combo_605 상태조회가 200초+ 응답불가로 걸리는 회귀가 발생함
    # (사용자 신고: "매수해야 하는 종목이 안 나온다"). asof_mktcap=False를 명시해 원래의 가벼운
    # current-mode 유니버스 필터로 되돌림 — "오늘" 시점 신호 추출에는 as-of/현재 시총 차이가
    # 사실상 없으므로(과거 백테스트 구간에서만 룩어헤드가 문제) 정확도 손실 없이 속도만 복구.
    "v4":                  (_bt.run_backtest,               dict(per_stock=10_000_000, max_positions=10, asof_mktcap=False), -0.08),
    "v2":                  (_bt.run_backtest_v2,             dict(per_stock=10_000_000, max_positions=10), -0.10),
    # sector_focus는 trail=-0.2(2026-07-18 등록 시점 값) 유지 — combo_605 전용.
    # run_backtest_sector 단독 기본값은 2026-07-21에 -0.30으로 바뀌었지만(CLAUDE.md 참조),
    # combo_605(7전략, recovery/v10 포함)에 trail=-0.30 sector를 넣으면 자본타이밍 상호작용으로
    # 605.05%→554.73%(-50.3%p) 악화됨을 확인(scratch/recompute_combos_with_fresh_sector_20260724.py)
    # — combo_605만은 반드시 이 stale(trail=-0.2) 버전을 그대로 써야 함.
    "sector_focus":        (_bt.run_backtest_sector,         dict(per_stock=10_000_000, max_positions=9, trail=-0.2), -0.12),
    # 2026-07-24: sector_focus_v30 신규 — trail=-0.30(2026-07-21 표준 채택값, 함수 자체 기본값
    # 상속). combo_539/510/474/546은 recovery/v10이 없어 trail=-0.30로 갱신해도 전부 개선
    # (+11~36%p, scratch/test_percombo_sector_split_20260724.py 검증) — 이 4개 콤보 전용.
    "sector_focus_v30":    (_bt.run_backtest_sector,         dict(per_stock=10_000_000, max_positions=9), -0.12),
    "v10":                 (_bt.run_backtest_v10,            dict(per_stock=10_000_000, max_positions=10), -0.08),
    "recovery":            (_bt.run_backtest_recovery,       dict(per_stock=10_000_000, max_positions=10), -0.12),
    "earnings_conviction": (_bt.run_backtest_earnings_conviction, dict(total_capital=100_000_000, max_positions=10, asof_mktcap=False), -0.20),
    "moonshot_turnaround": (_bt.run_backtest_moonshot_turnaround, dict(total_capital=100_000_000, max_positions=30, asof_mktcap=False), -0.35),
}
COMBO_LABELS = {
    "v4": "V5 복합콤보", "v2": "V3 재무우량", "sector_focus": "V-SECTOR 주도섹터",
    "sector_focus_v30": "V-SECTOR 주도섹터(trail-30%)",
    "v10": "V10 이익폭발", "recovery": "V-RECOVERY 낙폭과대반등",
    "earnings_conviction": "V-EARNINGS 실적가속", "moonshot_turnaround": "V-MOONSHOT 대량발굴",
}

# 4개 등록조합 — component/priority는 backtest_run_specs.parameter_json(component_run_hashes
# 경유) 실측값 그대로 (2026-07-23 registration 기준)
COMBO_DEFS = {
    # 2026-07-25: merged_simulator.py 자체의 daily mark-to-market 버그 수정(Codex 발견,
    # signal_experiment_ledger 'merged_simulator_infrastructure' 참조 — 주문일에만 marks가
    # 갱신되어 장기보유 포지션이 진입가에 고정되던 결함) 이후 전 콤보 재계산. 순위가 바뀌어
    # (546>539>605>510>474, 기존엔 605가 1위였음) 🏆🥈🥉 메달 표기는 더 이상 신뢰 못할 주장이라
    # 제거 — 퍼센트만 정직하게 표시. 라벨의 ①②③④⑤는 등록 순서일 뿐 순위 아님.
    # 2026-07-24: bear_gate(V10 약세장 게이트) 시도했다가 철회함 — 반드시 재도입 전 이 노트를 읽을 것.
    # Codex가 제안한 "core_sector_trail"(sector_trail30 + recovery@0.3 조합, +611.30%→+628.71%)
    # 구성으로 학습/검증 홀드아웃까지 통과해 최초 채택했으나, 그 구성이 실제 이 combo_605 라이브
    # 구성(sector_focus + recovery@0.4 — sector_trail30이 아님!)과 100% 동일하지 않음을 뒤늦게
    # 발견. combo_605의 정확한 구성으로 재검증한 결과 bear_gate 적용 시 오히려 악화 —
    # Codex의 core_sector_trail 조합에서는 유효했던 신호가 combo_605의 정확한 구성에서는
    # 재현되지 않음(컴포넌트 조합에 따라 신호 유효성이 달라지는 사례). 원상복구(bear_gate 제거)
    # 완료, 재검증 스크립트는 scratch/verify_beargate_on_combo605_exact.py 참조.
    "combo_605": {
        "label": "조합①552% (7전략)",
        "max_positions": 20,
        "components": [
            ("earnings_conviction", 4.0), ("moonshot_turnaround", 3.0), ("sector_focus", 1.0),
            ("v2", 0.8), ("v4", 0.6), ("recovery", 0.4), ("v10", 0.3),
        ],
    },
    # 2026-07-24: sector_focus -> sector_focus_v30(trail=-0.30, 신선한 데이터)으로 교체.
    # combo_605와 달리 이 콤보들엔 recovery/v10이 없어 trail=-0.30이 순수 개선으로 작용함을
    # 확인(scratch/test_percombo_sector_split_20260724.py).
    "combo_539": {
        "label": "조합②577% (5전략, sector우선)",
        "max_positions": 20,
        "components": [
            ("earnings_conviction", 4.0), ("moonshot_turnaround", 3.0), ("sector_focus_v30", 1.0),
            ("v2", 0.8), ("v4", 0.6),
        ],
    },
    "combo_510": {
        "label": "조합③530% (5전략, v4우선)",
        "max_positions": 20,
        "components": [
            ("earnings_conviction", 4.0), ("moonshot_turnaround", 3.0), ("v4", 1.0),
            ("v2", 0.8), ("sector_focus_v30", 0.6),
        ],
    },
    "combo_474": {
        "label": "조합④487% (3전략)",
        "max_positions": 10,
        "components": [
            ("sector_focus_v30", 4.0), ("v4", 3.0), ("v2", 1.0),
        ],
    },
    # 2026-07-24 신규: combo_539와 구성전략 동일(sector_focus_v30 포함) + Codex 제안(수주공시
    # 우선순위 가산) 적용. 독립 매수 sleeve로 섞으면 악화(scratch/codex_combo_
    # with_contract_20260723.py 재현 확인)하지만, "기존 매수신호 종목 중 최근 120일 내 해외
    # 수주공시(계약금액이 매출 10%+, ratio>=10 & overseas)가 있으면 우선순위만 가산"하는 방식은
    # 개선 확인. V-CONTRACT-MOMENTUM 신호 자체는 2022-2023 학습/2024-2026 검증 홀드아웃 통과
    # (scratch/claude_holdout_contract_momentum_20260724.py). 2026-07-25 시뮬레이터 수정 후
    # 재계산 결과 5개 콤보 중 실제 최고 성과(584.59%).
    "combo_546": {
        "label": "조합⑤585% (577%+수주부스트)",
        "max_positions": 20,
        "components": [
            ("earnings_conviction", 4.0), ("moonshot_turnaround", 3.0), ("sector_focus_v30", 1.0),
            ("v2", 0.8), ("v4", 0.6),
        ],
        "contract_boost": {"lookback_days": 120, "boost_priority": 1.5, "min_ratio": 10.0},
    },
}

_combo_component_cache: dict[str, dict] = {}   # component_key -> {"date","buys","sells","run_id"}
_combo_reco_cache: dict[str, dict] = {}         # combo_key -> {"updated_epoch","data"}
_COMBO_CACHE_TTL_SEC = 1800


def _combo_latest_trading_day(conn) -> str:
    row = conn.execute(
        """SELECT MAX(date) FROM price_history
           WHERE stock_code NOT LIKE '%^%' AND stock_code NOT LIKE 'GC%'
             AND stock_code NOT LIKE 'CL%' AND stock_code NOT LIKE '%-F'
             AND stock_code NOT LIKE '%=%' AND stock_code NOT LIKE 'NQ%'
             AND stock_code NOT LIKE 'ES%' AND close>0"""
    ).fetchone()
    return str(row[0])[:10] if row and row[0] else _dt.now().strftime("%Y-%m-%d")


# 백테스트 엔진이 시뮬레이션 "기간종료" 시점에 아직 보유 중인 포지션을 강제청산(마킹)할 때
# 붙이는 사유 문자열 — 진짜 매도신호가 아니라 순수 회계상 마감처리이므로 반드시 제외해야 함.
# end_date=최신거래일(오늘)로 항상 실행하므로, 이 마커를 거르지 않으면 "오늘 보유 중인
# 모든 포지션"이 매번 매도신호로 오인된다(실측 확인: v10/v4/v2/earnings/moonshot/recovery
# 전부 각기 다른 문자열을 씀 — 기간종료/기간종료(시세부재 전액손실)/종료청산/final/end).
# 2026-08-29: 전략센터상위5(sc_golden_cross)에 golden_cross를 새로 편입하며 재발견 —
# run_backtest_golden_cross(backtest.py)만 유일하게 "잔존"이라는 또 다른 문자열을 써서
# 이 필터를 통과하지 못하고 있었음. 아직 sc_golden_cross가 한 번도 매수를 못해(보유0건)
# 실제 오매도로 이어지진 않았지만, 첫 매수가 체결되는 즉시 다음날 재실행에서 방금 산
# 종목을 전부 "매도신호"로 오인해 하루 만에 되팔 뻔한 잠재 버그였음(사전 발견·수정).
_COMBO_PERIOD_END_MARKERS = {"기간종료", "기간종료(시세부재 전액손실)", "종료청산", "final", "end", "잔존"}


def _combo_parse_trades(trades_json_raw: str) -> list[dict]:
    """backtest_runs.trades_json → 표준화된 거래 리스트.
    이벤트스트림({"action":"BUY/SELL",...})과 종결레코드({"entry_date","exit_date",...})
    두 포맷 모두 지원 (codex_combo_search_20260723.py의 load_orders()와 동일 로직).
    단, 기간종료(회계상 강제청산) 마커가 붙은 매도 레코드는 진짜 신호가 아니므로 제외."""
    try:
        data = json.loads(trades_json_raw)
    except Exception:
        return []
    trades = data.get("trades", data) if isinstance(data, dict) else data
    out: list[dict] = []
    for t in trades:
        if not isinstance(t, dict):
            continue
        action = str(t.get("action") or "").upper()
        if action in ("BUY", "SELL"):
            code = t.get("code") or t.get("stock_code")
            date = str(t.get("date") or "")[:10]
            price = t.get("price")
            reason = str(t.get("reason") or "signal")
            if action == "SELL" and reason in _COMBO_PERIOD_END_MARKERS:
                continue
            if code and date and price and float(price) > 0:
                out.append({"side": action.lower(), "date": date, "code": str(code),
                            "price": float(price), "reason": reason})
            continue
        code = t.get("stock_code") or t.get("code")
        b_date = str(t.get("entry_date") or t.get("buy_date") or "")[:10]
        s_date = str(t.get("exit_date") or t.get("sell_date") or "")[:10]
        b_px = t.get("entry_price") or t.get("entry") or t.get("buy_price")
        s_px = t.get("exit_price") or t.get("exit") or t.get("sell_price")
        s_reason = str(t.get("exit_reason") or t.get("reason") or "exit")
        if s_reason in _COMBO_PERIOD_END_MARKERS:
            s_date = ""  # 기간종료 강제청산 — 매도신호로 채택하지 않음
        if code and b_date and b_px and float(b_px) > 0:
            out.append({"side": "buy", "date": b_date, "code": str(code),
                        "price": float(b_px), "reason": str(t.get("reason") or "signal")})
        if code and s_date and s_px and float(s_px) > 0:
            out.append({"side": "sell", "date": s_date, "code": str(code),
                        "price": float(s_px), "reason": str(t.get("exit_reason") or t.get("reason") or "exit")})
    return out


def _combo_refresh_component(conn, component_key: str, latest_date: str) -> dict:
    """컴포넌트 전략을 2020-03-01~latest_date로 재실행해 latest_date 당일 발생한
    매수/매도 신호만 추출. 프로세스 캐시로 하루 1회만 재계산(콤보 여러 개가
    같은 컴포넌트를 공유해도 중복실행 방지, 컴포넌트 1개당 1~20초 소요)."""
    cached = _combo_component_cache.get(component_key)
    if cached and cached.get("date") == latest_date:
        return cached
    fn, kwargs, _stop = COMBO_COMPONENTS[component_key]
    run_name = f"LIVE_COMBO_SRC_{component_key}_{latest_date}"
    run_id = fn("2020-03-01", latest_date, run_name=run_name, **kwargs)
    row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    trades = _combo_parse_trades(row[0]) if row and row[0] else []
    buys_today = [t for t in trades if t["side"] == "buy" and t["date"] == latest_date]
    sells_today = [t for t in trades if t["side"] == "sell" and t["date"] == latest_date]
    result = {"date": latest_date, "buys": buys_today, "sells": sells_today, "run_id": run_id}
    _combo_component_cache[component_key] = result
    return result


def _combo_stock_name(conn, code: str) -> str:
    row = conn.execute("SELECT stock_name FROM stock_universe WHERE stock_code=?", (code,)).fetchone()
    return row[0] if row else code


def _combo_current_price(conn, code: str) -> float:
    row = conn.execute(
        "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 1", (code,)
    ).fetchone()
    return float(row[0]) if row else 0.0


def _load_contract_boost_codes(conn, lookback_days: int, min_ratio: float = 10.0) -> set[str]:
    """실시간(오늘 기준) 해외수주공시 우선순위 부스트 대상 종목 집합.
    V-CONTRACT-MOMENTUM 검증필터(ratio>=해외수주공시, overseas=1, 종가>=MA20)와 동일 조건
    (scratch/codex_research_contract_momentum_20260723.py build_events()/2026-07-24 홀드아웃
    통과 파라미터 그대로 재사용) — 새 매수 sleeve가 아니라 기존 콤보 매수후보의 우선순위만
    가산하는 용도."""
    cutoff = (_dt.now() - _timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT DISTINCT stock_code, disclosed_at FROM dart_contracts
           WHERE contract_ratio_pct >= ? AND is_overseas = 1 AND disclosed_at IS NOT NULL""",
        (min_ratio,),
    ).fetchall()
    candidates: set[str] = set()
    for code, dt in rows:
        if not dt or len(str(dt)) < 8:
            continue
        iso = f"{str(dt)[:4]}-{str(dt)[4:6]}-{str(dt)[6:8]}"
        if iso >= cutoff:
            candidates.add(code)
    if not candidates:
        return set()
    out: set[str] = set()
    for code in candidates:
        closes = [r[0] for r in conn.execute(
            "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 20",
            (code,),
        ).fetchall()]
        if len(closes) < 20:
            continue
        if closes[0] >= sum(closes) / len(closes):
            out.add(code)
    return out


def _build_combo_recommendations(combo_key: str) -> dict:
    combo = COMBO_DEFS[combo_key]
    conn = _db()
    latest_date = _combo_latest_trading_day(conn)

    # 1) 구성 컴포넌트 today-signal 리프레시 (컴포넌트 단위 캐시 공유)
    component_signals: dict[str, dict] = {}
    for comp_key, _prio in combo["components"]:
        component_signals[comp_key] = _combo_refresh_component(conn, comp_key, latest_date)

    # 2) 현재 보유 포지션 (이 콤보 자신의 가상계좌)
    holdings = conn.execute(
        """SELECT id, stock_code, stock_name, buy_price, quantity, entry_date, entry_reason_json
           FROM peak_holding WHERE strategy=? AND is_active=1""",
        (combo_key,)
    ).fetchall()

    # 3) 매도 후보: (a) 원천 컴포넌트가 오늘 매도신호 (b) 컴포넌트 stop_loss 안전망 하회
    sell_candidates = []
    held_codes = set()
    for h_id, code, name, buy_price, qty, entry_date, reason_json in holdings:
        held_codes.add(code)
        cur = _combo_current_price(conn, code)
        if cur <= 0:
            continue
        bp = float(buy_price or 0)
        pct = (cur - bp) / bp if bp > 0 else 0.0
        try:
            origin = json.loads(reason_json or "{}").get("source_strategy")
        except Exception:
            origin = None
        reason = None
        sold_by_signal = origin and any(
            s["code"] == code for s in component_signals.get(origin, {}).get("sells", [])
        )
        if sold_by_signal:
            reason = f"{COMBO_LABELS.get(origin, origin)} 매도신호"
        else:
            stop = COMBO_COMPONENTS.get(origin, (None, None, -0.20))[2]
            if pct <= stop:
                reason = f"안전망 손절({stop*100:.0f}%): {pct*100:.1f}%"
        if reason:
            sell_candidates.append({
                "stock_code": code, "stock_name": name, "current_price": cur,
                "buy_price": bp, "profit_pct": round(pct * 100, 2), "reason": reason,
                "holding_id": h_id, "quantity": int(qty),
            })

    # 4) 매수 후보: 콤보 우선순위 기준 랭킹, 이미 보유중이거나 오늘 매도예정 종목 제외
    sell_codes_today = {s["stock_code"] for s in sell_candidates}
    buy_pool: dict[str, dict] = {}
    for comp_key, prio in combo["components"]:
        for t in component_signals[comp_key]["buys"]:
            code = t["code"]
            if code in held_codes or code in sell_codes_today:
                continue
            # 같은 종목을 여러 컴포넌트가 동시에 신호내면 콤보 내 최고 우선순위만 채택
            if code not in buy_pool or prio > buy_pool[code]["priority"]:
                # 2026-07-25: 대부분의 컴포넌트(v4/v2/v10/recovery/earnings_conviction/
                # moonshot_turnaround)는 백테스트 trades_json에 종목별 진입사유를 저장하지
                # 않아 t['reason']이 항상 영문 placeholder "signal"로 채워짐(V-SECTOR만
                # 실제 한글 사유 보유) — 없는 사유를 지어내지 않고 정직하게 생략.
                raw_reason = t.get("reason") or ""
                label = COMBO_LABELS.get(comp_key, comp_key)
                buy_pool[code] = {
                    "stock_code": code, "priority": prio, "source_strategy": comp_key,
                    "reason": f"{label} 매수신호: {raw_reason}" if raw_reason and raw_reason != "signal" else f"{label} 매수신호",
                }

    # 4-1) 수주공시 우선순위 가산 (조합⑤ 전용, 신규 매수 sleeve 아님 — 기존 후보 재랭킹만)
    boost_cfg = combo.get("contract_boost")
    if boost_cfg and buy_pool:
        boost_codes = _load_contract_boost_codes(
            conn, boost_cfg["lookback_days"], boost_cfg.get("min_ratio", 10.0)
        )
        for code in buy_pool:
            if code in boost_codes:
                buy_pool[code]["priority"] += boost_cfg["boost_priority"]
                buy_pool[code]["reason"] += f" + 🚢해외수주{boost_cfg['lookback_days']}일이내(+{boost_cfg['boost_priority']})"

    # 2026-08-08 정상화: stock_code를 동점 타이브레이크로 쓰면 코드가 낮은(=오래되고 큰)
    # 종목을 체계적으로 선호하는 팩터 틸트가 주입된다(merged_simulator.py의 동일 버그와
    # 같은 클래스, signal_experiment_ledger 'stock_code_tiebreak_path_luck_20260808' 참조).
    # 백테스트 재현 경로(merged_simulator._neutral_tiebreak)와 동일한 중립 해시로 통일.
    ranked_buys = sorted(
        buy_pool.values(),
        key=lambda b: (-b["priority"], _combo_neutral_tiebreak(latest_date, b["stock_code"], b["source_strategy"])),
    )
    buy_candidates = []
    for b in ranked_buys:
        cur = _combo_current_price(conn, b["stock_code"])
        if cur <= 0:
            continue
        b["current_price"] = cur
        b["stock_name"] = _combo_stock_name(conn, b["stock_code"])
        buy_candidates.append(b)

    total_invested = conn.execute(
        "SELECT COALESCE(SUM(buy_price*quantity),0) FROM peak_holding WHERE strategy=? AND is_active=1",
        (combo_key,)
    ).fetchone()[0] or 0.0
    active_cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (combo_key,)
    ).fetchone()[0] or 0
    investable_cap = COMBO_CAPITAL_KRW * (1 - COMBO_CASH_RESERVE)
    avail_cash = max(0.0, investable_cap - float(total_invested))

    conn.close()
    return {
        "combo_key": combo_key,
        "label": combo["label"],
        "components": [{"strategy": k, "label": COMBO_LABELS.get(k, k), "priority": p} for k, p in combo["components"]],
        "max_positions": combo["max_positions"],
        "capital_krw": COMBO_CAPITAL_KRW,
        "ticket_krw": COMBO_TICKET_KRW,
        # 자본비례 티켓을 쓰는 콤보는 실제 티켓이 계좌 규모에 따라 달라지므로 별도 노출
        "ticket_pct": COMBO_TICKET_PCT.get(combo_key),
        "active_positions": active_cnt,
        "total_invested": round(total_invested),
        "avail_cash": round(avail_cash),
        "sell_candidates": sell_candidates,
        "buy_candidates": buy_candidates,
        "latest_date": latest_date,
        "updated_at": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _get_combo_cached_or_build(combo_key: str, force: bool = False) -> dict:
    if combo_key not in COMBO_DEFS:
        raise HTTPException(status_code=404, detail=f"unknown combo: {combo_key}")
    now = time.time()
    cached = _combo_reco_cache.get(combo_key)
    if not force and cached and (now - cached.get("updated_epoch", 0)) < _COMBO_CACHE_TTL_SEC:
        return cached["data"]
    data = _build_combo_recommendations(combo_key)
    _combo_reco_cache[combo_key] = {"updated_epoch": now, "data": data}
    return data


@router.get("/combo/{combo_key}/status")
def get_combo_status(combo_key: str):
    data = _get_combo_cached_or_build(combo_key, force=False)
    return {"ok": True, **data}


@router.post("/combo/{combo_key}/execute")
def execute_combo_now(combo_key: str):
    """병합조합 가상매매 즉시 실행 (매도 → 매수 순)."""
    data = _get_combo_cached_or_build(combo_key, force=True)
    combo = COMBO_DEFS[combo_key]
    conn = _db()
    _ensure_peak_holding_reason_columns(conn)
    today = _dt.now().strftime("%Y-%m-%d")
    now_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
    sold, bought = 0, 0

    for s in data["sell_candidates"]:
        row = conn.execute(
            "SELECT id, quantity, buy_price FROM peak_holding WHERE id=? AND strategy=? AND is_active=1",
            (s["holding_id"], combo_key),
        ).fetchone()
        if not row:
            continue
        h_id, qty, buy_price = row
        qty = int(qty or 0)
        if qty <= 0:
            continue
        price = float(s["current_price"])
        bp = float(buy_price or 0)
        profit = round((price - bp) * qty)
        profit_pct = round((price - bp) / bp * 100, 2) if bp > 0 else 0.0
        conn.execute(
            "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=? WHERE id=?",
            (price, now_ts, price, profit_pct, now_ts, h_id),
        )
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) VALUES (?,?,?,?,?,?,?,?,?)",
            (s["stock_name"], "sell", price, qty, round(price * qty), profit, profit_pct, now_ts, combo_key),
        )
        _record_paper_trade(
            conn, strategy=combo_key, side="sell", code=s["stock_code"], name=s["stock_name"],
            holding_id=int(h_id), qty=qty, price=price, trade_id=int(trade_cursor.lastrowid),
            occurred_at=now_ts, gross_profit=profit,
        )
        sold += 1

    avail_cash = _investable_cash(conn, combo_key, COMBO_CAPITAL_KRW, COMBO_CASH_RESERVE)
    active_cnt = conn.execute(
        "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (combo_key,)
    ).fetchone()[0] or 0

    # 자본비례 티켓: 검증 통과한 콤보만 equity 기준으로 티켓을 키운다(위 COMBO_TICKET_PCT 주석 참조).
    # equity = 현재 평가액(보유 평가 + 미투입 현금)이 아니라, 백테스트와 동일하게
    # "시드 + 실현손익 + 보유 평가손익"으로 산출해 계좌가 불어난 만큼 티켓도 커지게 한다.
    ticket_krw = COMBO_TICKET_KRW
    _pct = COMBO_TICKET_PCT.get(combo_key)
    if _pct:
        _realized = conn.execute(
            "SELECT COALESCE(SUM(profit),0) FROM peak_trade WHERE strategy=? AND tx_type='sell'",
            (combo_key,),
        ).fetchone()[0] or 0.0
        _unreal = conn.execute(
            "SELECT COALESCE(SUM((current_price-buy_price)*quantity),0) FROM peak_holding WHERE strategy=? AND is_active=1",
            (combo_key,),
        ).fetchone()[0] or 0.0
        _equity = COMBO_CAPITAL_KRW + float(_realized) + float(_unreal)
        ticket_krw = max(COMBO_TICKET_KRW, _equity * float(_pct))

    for b in data["buy_candidates"]:
        if avail_cash < ticket_krw:
            break
        if active_cnt >= combo["max_positions"]:
            break
        code = b["stock_code"]
        cur = float(b["current_price"])
        if cur <= 0:
            continue
        existing = conn.execute(
            "SELECT COUNT(*) FROM peak_holding WHERE stock_code=? AND strategy=? AND is_active=1",
            (code, combo_key),
        ).fetchone()[0]
        if existing > 0:
            continue
        qty = int(min(ticket_krw, avail_cash) // cur)
        if qty <= 0:
            continue
        gate = _paper_buy_gate(code, combo_key, qty, cur)
        if gate["decision"] != "BUY_ALLOWED":
            logger.warning("[%s 매수차단] %s %s", combo_key, code, gate["decision"])
            continue
        holding_cursor = conn.execute(
            """INSERT INTO peak_holding
               (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at,entry_reason_text,entry_reason_json,entry_reason_updated_at)
               VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)""",
            (code, b["stock_name"], "", cur, cur, qty, today, combo_key, b["reason"],
             json.dumps({"source_strategy": b["source_strategy"], "reason": b["reason"]}, ensure_ascii=False)),
        )
        trade_cursor = conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) VALUES (?,?,?,?,?,0,0.0,?,?)",
            (b["stock_name"], "buy", cur, qty, round(cur * qty), now_ts, combo_key),
        )
        _record_paper_trade(
            conn, strategy=combo_key, side="buy", code=code, name=b["stock_name"],
            holding_id=int(holding_cursor.lastrowid), qty=qty, price=cur,
            trade_id=int(trade_cursor.lastrowid), occurred_at=now_ts,
        )
        avail_cash -= qty * cur
        active_cnt += 1
        bought += 1

    conn.commit()
    conn.close()
    return {"ok": True, "combo_key": combo_key, "sold": sold, "bought": bought,
            "sell_rows": data["sell_candidates"], "updated_at": now_ts}


def execute_all_combos_now() -> dict:
    """4개 병합조합 전체 순차 실행 (스케줄러용)."""
    results = {}
    for key in COMBO_DEFS:
        try:
            results[key] = execute_combo_now(key)
            _record_virtual_strategy_run(
                key, "success", sold=results[key].get("sold", 0), bought=results[key].get("bought", 0),
            )
        except Exception as e:
            logger.error(f"[콤보가상매매] {key} 실행 오류: {e}", exc_info=True)
            try:
                _record_virtual_strategy_run(key, "error", message=str(e))
            except Exception:
                logger.exception("[콤보가상매매] %s 실행 오류 상태 저장 실패", key)
            results[key] = {"ok": False, "error": str(e)}
    return results


# ══════════════════════════════════════════════════════════════════
#  전략센터 상위 5개 가상매매
#
# 과거 병합조합의 고정 구성/성과 라벨과 분리한다. 전략센터 매트릭스의
# 현재 검증 결과를 읽어, 가상매매로 실행 가능한 전략 중 상위 5개를 매일
# 다시 선정한다. 이 경로는 paper-only이며 실주문으로 연결되지 않는다.
# ══════════════════════════════════════════════════════════════════
STRATEGY_CENTER_PAPER_PREFIX = "sc_"
STRATEGY_CENTER_PAPER_CAPITAL_KRW = 100_000_000
STRATEGY_CENTER_PAPER_CASH_RESERVE = 0.20
STRATEGY_CENTER_PAPER_TICKET_KRW = 10_000_000
STRATEGY_CENTER_PAPER_COUNT = 5

# 순위는 전략센터 매트릭스가 결정한다. 이 표는 검증된 전략 로직을
# 가상매매 실행 함수에 연결하는 어댑터일 뿐 성과나 순위를 갖지 않는다.
# 2026-08-26: v5/v10이 2026-08-24 price_jump_audit 재검증에서 retired로 강등되고
# 새로 paper_core/validation_queue에 오른 v8/v2/earnings_conviction/v11에는 애초에
# 어댑터가 없어 실행가능 전략이 3개로 줄어듦 — _select_strategy_center_top_five()의
# fail-close 설계(5개 미달 시 예외)로 매일 18:35 "success"로 기록되면서도 실제로는
# 매매가 통째로 스킵되고 있었음(peak_trade 최종 거래 7/31 이후 정지 확인). v8/v2 추가.
STRATEGY_CENTER_PAPER_ENGINES = {
    "golden_cross": (_bt.run_backtest_golden_cross, {"per_stock": 10_000_000, "max_positions": 10}),
    "sector_focus": (_bt.run_backtest_sector, {"per_stock": 10_000_000, "max_positions": 9}),
    "v5": (_bt.run_backtest_v5, {"per_stock": 10_000_000, "max_positions": 10}),
    "v10": (_bt.run_backtest_v10, {"per_stock": 10_000_000, "max_positions": 10}),
    "v8": (_bt.run_backtest_v8, {"per_stock": 10_000_000, "max_positions": 10}),
    "v2": (_bt.run_backtest_v2, {"per_stock": 10_000_000, "max_positions": 10}),
    "contract_momentum": (_bt.run_backtest_contract_momentum, {"total_capital": 100_000_000, "per_stock": 10_000_000, "max_positions": 10}),
}
_strategy_center_paper_cache: dict[str, dict] = {}


def _strategy_center_paper_key(source_strategy: str) -> str:
    return f"{STRATEGY_CENTER_PAPER_PREFIX}{source_strategy}"


def _select_strategy_center_top_five() -> list[dict]:
    """Read the current Strategy Center matrix instead of persisting a ranked list.

    Retired strategies and strategies without a paper execution adapter are excluded.
    This is intentionally fail-closed: if fewer than five executable, non-retired
    strategies are verified by the matrix, the scheduler records an error rather
    than silently substituting a legacy or hard-coded strategy.
    """
    from routes.backtest import get_backtest_matrix

    matrix = get_backtest_matrix(include_legacy=False)
    candidates: list[dict] = []
    for row in matrix.get("strategies") or []:
        key = str(row.get("strategy") or "")
        governance = row.get("governance") or {}
        if key not in STRATEGY_CENTER_PAPER_ENGINES or governance.get("tier") == "retired":
            continue
        metrics = governance.get("metrics") or {}
        average_return = metrics.get("average_return_pct")
        if average_return is None:
            continue
        try:
            average_return = float(average_return)
        except (TypeError, ValueError):
            continue
        candidates.append({
            "source_strategy": key,
            "strategy": _strategy_center_paper_key(key),
            "label": str(row.get("label") or key),
            "tier": str(governance.get("tier") or "unknown"),
            "average_return_pct": round(average_return, 2),
        })
    candidates.sort(key=lambda item: (-item["average_return_pct"], item["source_strategy"]))
    selected = candidates[:STRATEGY_CENTER_PAPER_COUNT]
    if len(selected) != STRATEGY_CENTER_PAPER_COUNT:
        raise RuntimeError(
            f"strategy center executable top-five unavailable: selected={len(selected)}"
        )
    for rank, item in enumerate(selected, start=1):
        item["rank"] = rank
    return selected


def _strategy_center_refresh_signal(conn, source_strategy: str, latest_date: str) -> dict:
    cache_key = f"{source_strategy}:{latest_date}"
    cached = _strategy_center_paper_cache.get(cache_key)
    if cached:
        return cached
    fn, kwargs = STRATEGY_CENTER_PAPER_ENGINES[source_strategy]
    run_id = fn("2020-03-01", latest_date, run_name=f"LIVE_STRATEGY_CENTER_{source_strategy}_{latest_date}", **kwargs)
    row = conn.execute("SELECT trades_json FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    trades = _combo_parse_trades(row[0]) if row and row[0] else []
    result = {
        "date": latest_date,
        "run_id": run_id,
        "buys": [trade for trade in trades if trade["side"] == "buy" and trade["date"] == latest_date],
        "sells": [trade for trade in trades if trade["side"] == "sell" and trade["date"] == latest_date],
    }
    _strategy_center_paper_cache[cache_key] = result
    return result


def _execute_strategy_center_paper(selected: dict, latest_date: str) -> dict:
    source_strategy = selected["source_strategy"]
    paper_strategy = selected["strategy"]
    conn = _db()
    try:
        _ensure_peak_holding_reason_columns(conn)
        signals = _strategy_center_refresh_signal(conn, source_strategy, latest_date)
        today = _dt.now().strftime("%Y-%m-%d")
        now_ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        sold = bought = 0
        sell_codes = {str(item["code"]) for item in signals["sells"]}

        holdings = conn.execute(
            """SELECT id,stock_code,stock_name,quantity,buy_price
               FROM peak_holding WHERE strategy=? AND is_active=1""",
            (paper_strategy,),
        ).fetchall()
        for holding_id, code, name, quantity, buy_price in holdings:
            if str(code) not in sell_codes:
                continue
            price = _combo_current_price(conn, str(code))
            qty = int(quantity or 0)
            if price <= 0 or qty <= 0:
                continue
            base = float(buy_price or 0)
            profit = round((price - base) * qty)
            profit_pct = round((price / base - 1) * 100, 2) if base > 0 else 0.0
            conn.execute(
                """UPDATE peak_holding SET is_active=0,sell_price=?,sold_at=?,current_price=?,
                   profit_pct=?,updated_at=? WHERE id=?""",
                (price, now_ts, price, profit_pct, now_ts, holding_id),
            )
            trade_cursor = conn.execute(
                """INSERT INTO peak_trade
                   (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (name, "sell", price, qty, round(price * qty), profit, profit_pct, now_ts, paper_strategy),
            )
            _record_paper_trade(
                conn, strategy=paper_strategy, side="sell", code=str(code), name=str(name or code),
                holding_id=int(holding_id), qty=qty, price=price, trade_id=int(trade_cursor.lastrowid),
                occurred_at=now_ts, gross_profit=profit,
            )
            sold += 1

        available = _investable_cash(
            conn, paper_strategy, STRATEGY_CENTER_PAPER_CAPITAL_KRW, STRATEGY_CENTER_PAPER_CASH_RESERVE
        )
        active_count = conn.execute(
            "SELECT COUNT(*) FROM peak_holding WHERE strategy=? AND is_active=1", (paper_strategy,)
        ).fetchone()[0] or 0
        max_positions = int(STRATEGY_CENTER_PAPER_ENGINES[source_strategy][1].get("max_positions", 10))
        unique_buys: dict[str, dict] = {}
        for buy in signals["buys"]:
            unique_buys.setdefault(str(buy["code"]), buy)
        ranked_buys = sorted(
            unique_buys.values(),
            key=lambda item: _combo_neutral_tiebreak(latest_date, str(item["code"]), source_strategy),
        )
        for buy in ranked_buys:
            if available < STRATEGY_CENTER_PAPER_TICKET_KRW or active_count >= max_positions:
                break
            code = str(buy["code"])
            price = _combo_current_price(conn, code)
            if price <= 0:
                continue
            exists = conn.execute(
                "SELECT 1 FROM peak_holding WHERE strategy=? AND stock_code=? AND is_active=1",
                (paper_strategy, code),
            ).fetchone()
            if exists:
                continue
            qty = int(min(available, STRATEGY_CENTER_PAPER_TICKET_KRW) // price)
            if qty <= 0:
                continue
            gate = _paper_buy_gate(code, paper_strategy, qty, price)
            if gate.get("decision") != "BUY_ALLOWED":
                logger.warning("[%s 매수차단] %s %s", paper_strategy, code, gate.get("decision"))
                continue
            name = _combo_stock_name(conn, code)
            reason = str(buy.get("reason") or "strategy signal")
            holding_cursor = conn.execute(
                """INSERT INTO peak_holding
                   (stock_code,stock_name,sector,buy_price,current_price,quantity,entry_date,hold_days,profit_pct,is_active,strategy,detected_at,updated_at,entry_reason_text,entry_reason_json,entry_reason_updated_at)
                   VALUES (?,?,?,?,?,?,?,0,0.0,1,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,?,?,CURRENT_TIMESTAMP)""",
                (code, name, "", price, price, qty, today, paper_strategy, reason,
                 json.dumps({"source_strategy": source_strategy, "rank": selected["rank"], "reason": reason}, ensure_ascii=False)),
            )
            trade_cursor = conn.execute(
                """INSERT INTO peak_trade
                   (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy)
                   VALUES (?,?,?,?,?,0,0.0,?,?)""",
                (name, "buy", price, qty, round(price * qty), now_ts, paper_strategy),
            )
            _record_paper_trade(
                conn, strategy=paper_strategy, side="buy", code=code, name=name,
                holding_id=int(holding_cursor.lastrowid), qty=qty, price=price,
                trade_id=int(trade_cursor.lastrowid), occurred_at=now_ts,
            )
            available -= qty * price
            active_count += 1
            bought += 1
        conn.commit()
        return {"ok": True, "strategy": paper_strategy, "source_strategy": source_strategy, "sold": sold, "bought": bought, "latest_date": latest_date}
    finally:
        conn.close()


def execute_strategy_center_top_five_now() -> dict:
    """Run the current Strategy Center top-five paper portfolios once each."""
    selected = _select_strategy_center_top_five()
    conn = _db()
    try:
        latest_date = _combo_latest_trading_day(conn)
    finally:
        conn.close()
    results = {}
    for item in selected:
        key = item["strategy"]
        try:
            result = _execute_strategy_center_paper(item, latest_date)
            _record_virtual_strategy_run(
                key, "success", sold=result["sold"], bought=result["bought"],
                message=json.dumps({"source_strategy": item["source_strategy"], "rank": item["rank"], "average_return_pct": item["average_return_pct"], "latest_date": latest_date}),
            )
            results[key] = result
        except Exception as exc:
            logger.error("[전략센터 가상매매] %s 실행 오류: %s", key, exc, exc_info=True)
            _record_virtual_strategy_run(key, "error", message=str(exc))
            results[key] = {"ok": False, "error": str(exc)}
    return {"ok": all(result.get("ok") for result in results.values()), "selected": selected, "results": results}


@router.get("/strategy-center/top-five")
def get_strategy_center_top_five_status():
    """Expose the live matrix selection and each paper account's latest heartbeat."""
    selected = _select_strategy_center_top_five()
    performance = get_virtual_performance().get("strategies") or {}
    for item in selected:
        item["performance"] = performance.get(item["strategy"], {
            "initial_cash": STRATEGY_CENTER_PAPER_CAPITAL_KRW,
            "cash": STRATEGY_CENTER_PAPER_CAPITAL_KRW,
            "equity": STRATEGY_CENTER_PAPER_CAPITAL_KRW,
            "return_pct": 0.0,
            "active_positions": 0,
            "last_run_at": None,
            "last_run_status": "pending",
            "last_sold": 0,
            "last_bought": 0,
            "last_message": None,
        })
    return {"ok": True, "selection_source": "strategy_center_matrix", "strategies": selected}
