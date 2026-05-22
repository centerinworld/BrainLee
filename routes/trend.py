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
from datetime import date as _date, datetime as _dt
from statistics import mean
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from db_utils import STOCK_DB_PATH, connect_stock_db

logger = logging.getLogger(__name__)
router = APIRouter()

DB_PATH = str(STOCK_DB_PATH)
V18_STRATEGY = "gpt_v18"
V18_TICKET_KRW   = 12_000_000     # 종목당 1회 진입금액 (1,200만원)
VIRTUAL_CAPITAL  = 100_000_000    # 총 가상 예산 (1억원)
CASH_RESERVE_PCT = 0.20           # 최소 현금 보유 비율 (20% = 2,000만원 상시 예수금)
# 최대 투자 가능 금액 = 1억 × 80% = 8,000만원
# 1,200만원 티켓 기준 최대 동시 보유 티켓 수 ≈ 6개
# → 종목당 max=2이더라도 예산 소진 시 추가 매수 불가

PYRAMID_MIN_DAYS = 2  # 동일 종목 2번째 티켓은 1번째 매수 후 최소 N일 이후에만 허용
# (백테스트: 2nd 티켓은 다음 신호 발생일에 진입 → 즉시 전량 매수 방지)

_v18_cache: dict = {"updated_at": None, "data": {"buy_candidates": [], "sell_candidates": [], "summary": {}}}
_V18_CACHE_TTL_SEC = 600


def _db():
    return connect_stock_db(timeout=30)


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


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


def _get_recently_sold_codes(conn, strategy: str, cooldown_days: int) -> set[str]:
    """최근 cooldown_days 일 이내에 매도한 종목 코드 집합 반환 → 재매수 방지."""
    rows = conn.execute(
        """
        SELECT DISTINCT ph.stock_code
        FROM peak_holding ph
        WHERE ph.strategy = ?
          AND ph.is_active = 0
          AND ph.sold_at >= datetime('now', ?, 'localtime')
        """,
        (strategy, f"-{cooldown_days} days"),
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
    # cold cache fallback: trigger compute once
    try:
        _main._run_screener_precompute()
    except Exception:
        logger.exception("[v14] failed to run screener precompute")
    return _main._signal_cache.get("combo_candidates", {}).get("data", []) or []


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

    # ── 종목코드별 보유 티켓 수 + 가장 최근 매수일 ───────────────────────
    tickets_by_code: dict[str, int] = {}
    latest_entry_by_code: dict[str, str] = {}   # 종목별 가장 최근 매수일 (YYYY-MM-DD)
    hold_by_code: dict[str, Any] = {}
    for r in holdings:
        code = str(r[1] or "")
        if not code:
            continue
        hold_by_code[code] = r
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
            if not can_buy_more:
                continue  # 예산 소진
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
        if not can_buy_more:
            continue  # 예산 소진
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
            }
        )
        if len(buy_candidates) >= 15:
            break

    return {
        "buy_candidates": buy_candidates,
        "sell_candidates": sell_candidates,
        "kospi_status": {
            "above_ma60": kospi_above_ma,
            "close": round(kospi_close, 2),
            "ma60": round(kospi_ma60, 2),
            "break_days": kospi_break_days,
        },
        "summary": {
            "holdings_count": len(holdings),
            "buy_count": len(buy_candidates),
            "sell_count": len(sell_candidates),
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
        if is_active and stock_code:
            rows2 = conn.execute(
                "SELECT close FROM price_history WHERE stock_code=? AND close>0 ORDER BY date DESC LIMIT 2",
                (stock_code,)
            ).fetchall()
            if rows2:
                current_price = rows2[0][0]
                prev_price = rows2[1][0] if len(rows2) > 1 else current_price
                daily_change = (current_price - prev_price) if prev_price else 0.0
                daily_change_pct = ((current_price - prev_price) / prev_price * 100.0) if prev_price else 0.0
            else:
                current_price = (r[3] or buy_price)
        else:
            current_price = r[3] or buy_price

        profit      = round((current_price - buy_price) * quantity) if buy_price and quantity else 0
        profit_pct  = round((current_price - buy_price) / buy_price * 100, 2) if buy_price else 0
        total_value = round(current_price * quantity) if current_price and quantity else 0

        result.append({
            "id": r[0], "stock_code": stock_code, "stock_name": stock_name,
            "buy_price": buy_price, "current_price": current_price, "quantity": quantity,
            "profit_pct": profit_pct, "profit": profit, "total_value": total_value,
            "daily_change": round(daily_change, 2),
            "daily_change_pct": round(daily_change_pct, 2),
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
        conn.execute(
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
        conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) "
            "VALUES (?,?,?,?,?,0,0.0,CURRENT_TIMESTAMP,?)",
            (stock_name, "buy", buy_price, quantity, round(buy_price * quantity), strategy)
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
    conn.execute(
        "UPDATE peak_holding SET is_active=0, sell_price=?, sold_at=?, current_price=?, profit_pct=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE stock_name=? AND strategy=? AND is_active=1",
        (sell_price, sold_at, sell_price, profit_pct, stock_name, strategy)
    )
    row = conn.execute(
        "SELECT id, quantity, strategy FROM peak_holding WHERE stock_name=? AND strategy=? ORDER BY id DESC LIMIT 1",
        (stock_name, strategy)
    ).fetchone()
    if row:
        qty = row[1] or 0
        conn.execute(
            "INSERT INTO peak_trade (stock_name,tx_type,price,quantity,total_amount,profit,profit_pct,tx_at,strategy) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (stock_name, "sell", sell_price, qty, round(sell_price * qty), profit, profit_pct, sold_at, row[2] or "peak")
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
def get_trend_summary(db: Session = Depends(get_db)):
    conn = _db()
    row  = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN profit_pct>0 THEN 1 ELSE 0 END), "
        "SUM((sell_price-buy_price)*quantity) FROM peak_holding WHERE is_active=0"
    ).fetchone()
    conn.close()
    total = row[0] or 0
    wins  = row[1] or 0
    return {
        "total_trades": total,
        "win_count":    wins,
        "win_rate":     round(wins / total * 100, 1) if total else None,
        "total_profit": round(row[2] or 0),
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
