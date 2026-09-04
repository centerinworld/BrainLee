"""
tenbagger_engine.py — 텐버거 종목 발굴 엔진

DB에 저장된 재무·가격·수급 데이터를 분석해 10배 성장 가능성이 있는
종목을 자동으로 발굴하고, OpenAI 분석 의견과 함께 텔레그램으로 알림.

⚠️ 점수 신뢰도 경고 (2026-07-12 실증 검증 — CLAUDE.md 변경이력 참조):
  strategy_feature_snapshot 15.5만 라벨행(12개월 3배, 학습/검증 시기분리) 실측 결과
  아래 6축 가중치는 손으로 정한 값이며 대부분의 축이 단독 예측력 없음이 확인됨.
  - 낙폭 -30~-70%: 3x율 7.4%/6.4% vs 기준율 8.2%/6.3% → 무효
    ("3배 종목의 70.5%가 낙폭 출발"은 P(낙폭|3배)이지 P(3배|낙폭)이 아님 — 조건부확률 방향 오류)
  - PBR<0.6/소형/수급/거래량 축: 검증기 lift 소멸 → 무효
  - 원시 라벨의 초낙폭 효과도 가격 아티팩트·비영업 급등을 제거한 지속형
    라벨에서는 검증 lift 1 미만으로 소멸(2026-08-10 재감사).
  → total_score는 "관심 후보 필터"로만 사용, 점수 크기를 확신도로 해석 금지.
  → tenbagger_results 실측 승률도 점수 상하위 무차별(37%).

발굴 기준 (6축 스코어링 — 가중치는 휴리스틱, 위 경고 참조):
  1. 낙폭과대   (25점) — 52주 고가 대비 -30~-85% 구간 (실제 3배 종목 70.5%)
  2. 펀더멘털   (25점) — 흑자전환(+15)/매출50%+(+12)/매출30%+(+9)
  3. 저평가     (20점) — PBR≤0.5(+10)/PBR≤1.0(+8)/소형주(+5)
  4. 촉매       (15점) — 수주공시/기술이전/자사주/임원매수
  5. 수급반전   (10점) — 기관+외인 동반 유입 (2024+ 신뢰성)
  6. 섹터       ( 5점) — IT/의료/경기소비재/산업재

임계값: 총점 ≥ 52점 + 최소 품질 게이트 (fundamental≥12 OR value≥12 OR drawdown≥18)

백테스트 검증 결과 (2020-2026.6 6.5년, v8 tb_hybrid_TP50 최적화):
  tb_hybrid_TP50 (현재 엔진):  전기간 플러스, 실전기+193.3%, 하락장+122.7%, AI랠리+200.2%, 전체+251.7%
  - STRONG_BULL에서 TP=+50%가 최적 (v7 trail-only +149%, v6 TP=+150%→+189% 대비 우수)
  - 핵심: 낮은 TP → 높은 턴오버 → 컴파운딩 극대화 (1.5^2.5사이클 효과)
  - 구조적 한계: KOSPI +235%(AI 주도 강세장) 초과는 낙폭주 전략으로 불가

레짐별 매도 파라미터 (check_sell_signal):
  STRONG_BULL (KOSPI > MA60>MA120 + ret60 > 20%): TP=+50%,  SL=-12%, hold=300일
  BULL        (KOSPI > MA60 + ret20 > 2%):         TP=+90%,  SL=-15%, hold=250일
  NEUTRAL:                                          TP=+70%,  SL=-15%, hold=200일
  BEAR        (KOSPI < MA120 + ret20 < -3%):        TP=+50%,  SL=-12%, hold=150일
"""

from __future__ import annotations

import json
import logging
from services.gemini_openai_compat import OpenAI
import sqlite3
import time
from datetime import datetime, date
from typing import Optional

import config
from db_compat import connect_primary_db
from db_utils import STOCK_DB_PATH

logger = logging.getLogger(__name__)

DB_PATH = str(STOCK_DB_PATH)
# 백테스트 검증: 52 이상에서 최적 리스크/리턴 균형
# (이전 55 → 52로 낮춤: 회복장 기회 증가 +94.8% 반영)
SCORE_THRESHOLD = 52       # 선정 최소 점수
MAX_RESULTS     = 20       # 회차당 최대 결과 수
TENBAGGER_MIN_AVG_TVOL5_억 = 3.0   # 자동 추천/알림 유동성 hard cut
TENBAGGER_MAX_DILUTION_PCT = 30.0  # 최근 1년 누적 잠재 희석 상한

# 품질 게이트: 아래 조건 중 하나라도 충족해야 최종 선정
# (tb_combo 연구 결과: 낙폭 단독은 AI랠리에서 -14% — 펀더멘털 OR 가치 필수)
QUALITY_GATE_RULES = {
    "fundamental_min": 12,   # fundamental 축 ≥ 12 (흑자전환 or 매출 30%+)
    "value_min":       12,   # value 축 ≥ 12 (PBR≤1.0 + 소형주)
    "drawdown_min":    18,   # drawdown 축 ≥ 18 (52주 -50%+ 극심 낙폭)
}

# ──────────────────────────────────────────────
# 백테스트 검증 매수/매도 파라미터 (2020-2025, 6년)
# tb_base: +161.9%, MDD -29.1% / tb_combo: +254.3%, MDD -29.3%
# ──────────────────────────────────────────────
BUY_PARAMS = {
    "min_score":    50,    # 엔진 총점 최소값 (백테스트 tb_base 기준)
    "drawdown_req": True,  # 52주 고가 대비 -30~-85% 구간 필수
    "vol_req":      1.5,   # 거래량 20일 평균 대비 1.5배 이상
    "max_cap_억":   5000,  # 시총 상한 (억원)
}
SELL_PARAMS = {
    "stop_loss":    -0.15, # -15% 손절
    "take_profit":  +0.70, # +70% 익절 (NEUTRAL 기준)
    "max_hold_days": 200,  # 200일 최대 보유 (NEUTRAL 기준)
    "score_exit":    30,   # 총점 30 미만 → 펀더멘털 악화 청산
}

# 레짐별 동적 매도 파라미터 (v8 백테스트 최적화: STRONG_BULL TP=+50% 복리 극대화)
# v6→v7→v8 최적화 결론: TP 낮을수록 턴오버 증가 → 컴파운딩 강화
# STRONG_BULL에서 TP=50%: 실전기 +193.3% (TP=150%의 +189.2%보다 우수)
REGIME_SELL_PARAMS = {
    "STRONG_BULL": {"stop_loss": -0.12, "take_profit": +0.50, "max_hold_days": 300},
    "BULL":        {"stop_loss": -0.15, "take_profit": +0.90, "max_hold_days": 250},
    "NEUTRAL":     {"stop_loss": -0.15, "take_profit": +0.70, "max_hold_days": 200},
    "BEAR":        {"stop_loss": -0.12, "take_profit": +0.50, "max_hold_days": 150},
}


# ──────────────────────────────────────────────
# DB 초기화
# ──────────────────────────────────────────────

def init_tenbagger_tables():
    if config.IS_POSTGRES:
        conn = connect_primary_db(timeout=60)
        try:
            conn.execute(
                "ALTER TABLE tenbagger_results ALTER COLUMN telegram_sent SET DEFAULT 0"
            )
            conn.execute(
                "ALTER TABLE tenbagger_results ALTER COLUMN created_at "
                "SET DEFAULT TO_CHAR(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Seoul', 'YYYY-MM-DD HH24:MI:SS')"
            )
            conn.execute(
                "UPDATE tenbagger_results SET telegram_sent=0 WHERE telegram_sent IS NULL"
            )
            conn.execute(
                "UPDATE tenbagger_results SET created_at=run_time WHERE created_at IS NULL"
            )
            conn.commit()
        finally:
            conn.close()
        return
    conn = connect_primary_db(timeout=60, wal=True)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tenbagger_results (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                run_time        TEXT NOT NULL,
                run_type        TEXT NOT NULL DEFAULT 'auto',
                stock_code      TEXT NOT NULL,
                stock_name      TEXT NOT NULL,
                total_score     REAL NOT NULL,
                score_detail    TEXT,        -- JSON {growth_rev, growth_op, profit, trend, supply, value}
                reasons         TEXT,        -- 선정 사유 요약 (한국어 bullet)
                ai_analysis     TEXT,        -- OpenAI 분석 의견 전문
                current_price   REAL,
                market_cap      REAL,        -- 억원
                per             REAL,
                pbr             REAL,
                roe             REAL,
                revenue_growth  REAL,        -- % YoY
                op_growth       REAL,        -- % YoY
                op_margin       REAL,        -- %
                inst_net_10d    REAL,        -- 억원 (최근 10일)
                frn_net_10d     REAL,        -- 억원 (최근 10일)
                telegram_sent   INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now','localtime'))
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tenbagger_run ON tenbagger_results(run_time)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tenbagger_code ON tenbagger_results(stock_code)")
        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 데이터 조회 헬퍼
# ──────────────────────────────────────────────

def _fetch_candidates(conn) -> list[dict]:
    """stock_universe에서 기본 필터 통과 종목 목록."""
    rows = conn.execute("""
        SELECT su.stock_code, su.stock_name, su.market,
               su.market_cap, su.per, su.pbr, su.roe, su.roa,
               su.shares_issued
        FROM   stock_universe su
        WHERE  su.market IN ('KOSPI','KOSDAQ')
          AND  su.market_cap IS NOT NULL
          -- 하한 500억 → 100억 (2026-08-08). label_10x_24m walk-forward 실측:
          --   시총 500억~3조   lift 0.98x(학습)→0.82x(검증) = 기준율 '이하'
          --   시총 <500억      lift 1.39x      →2.02x
          --   시총 <300억      lift 1.42x      →3.34x (검증에서 오히려 강화)
          -- 구 하한이 확률 높은 구간을 통째로 배제해, 3년내 10배 종목 251개 중
          -- 41.8%가 저점 시점에 후보 목록에 오르지도 못했음. 100억은 기존
          -- scripts/build_strategy_research_dataset.py _filter_liquid_rankings와 같은 기준.
          -- 상한 3조 유지: 초과 구간은 10배 종목의 1.7%뿐이고 megatrend/earnings_conviction이 커버.
          AND  su.market_cap BETWEEN 100 AND 30000   -- 100억~3조 (억원)
          AND  su.stock_code NOT LIKE '%^%'
          AND  su.stock_code NOT LIKE 'GC%'
          AND  su.stock_code NOT LIKE 'CL%'
          AND  length(su.stock_code) = 6
    """).fetchall()
    return [dict(r) for r in rows]


def _fetch_price_data(conn, stock_code: str) -> dict:
    """최근 250일 가격 데이터에서 MA, 52주 고저, 거래대금 계산."""
    rows = conn.execute("""
        SELECT date, close, volume
        FROM   price_history
        WHERE  stock_code = ?
          AND  close > 0
        ORDER  BY date DESC
        LIMIT  250
    """, (stock_code,)).fetchall()

    if len(rows) < 60:
        return {}

    closes = [float(r[1]) for r in rows]   # 최신→오래된 순
    volumes = [r[2] for r in rows]

    def ma(n):
        if len(closes) < n:
            return None
        return sum(closes[:n]) / n

    current = closes[0]
    ma20    = ma(20)
    ma60    = ma(60)
    ma120   = ma(120)
    ma200   = ma(200) if len(closes) >= 200 else None

    high_52w = max(closes[:min(250, len(closes))])
    low_52w  = min(closes[:min(250, len(closes))])

    # 최근 5일 평균 거래대금
    avg_vol5  = sum(volumes[:5]) / 5 if len(volumes) >= 5 else 0
    avg_tvol5 = avg_vol5 * current / 1e8  # 억원

    # 거래량 비율 (당일 vs 20일 평균) — 백테스트 매수 조건
    avg_vol20 = sum(volumes[:20]) / 20 if len(volumes) >= 20 else 0
    vol_ratio = (volumes[0] / avg_vol20) if avg_vol20 > 0 else 0

    # 52주 대비 위치
    from_high_pct = (current / high_52w - 1) * 100 if high_52w else 0
    from_low_pct  = (current / low_52w  - 1) * 100 if low_52w  else 999

    return {
        "stock_code": stock_code,
        "current_price": current,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ma200": ma200,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "avg_tvol5_억": avg_tvol5,
        "vol_ratio": round(vol_ratio, 2),
        "from_high_pct": round(from_high_pct, 1),
        "from_low_pct":  round(from_low_pct,  1),
        "market_cap_억": round(current * (closes[0] if closes else 0) / 1e8, 0) if False else None,  # 시총은 uni에서 사용
    }


def _fetch_financials(conn, stock_code: str) -> dict:
    """최근 2개년 연간 재무 데이터 → 성장률 계산."""
    rows = conn.execute("""
        SELECT year, revenue, operating_profit, net_income,
               total_equity, total_assets, eps
        FROM   financial_data
        WHERE  stock_code = ?
          AND  is_annual = 1
          AND  quarter = 4
          AND  revenue IS NOT NULL
          AND  revenue > 0
        ORDER  BY year DESC
        LIMIT  3
    """, (stock_code,)).fetchall()

    if len(rows) < 2:
        return {}

    def safe_pct(new, old):
        if not old or old <= 0:
            return None
        return (new - old) / old * 100

    cols = ["year", "revenue", "operating_profit", "net_income", "total_equity", "total_assets", "eps"]
    r0 = dict(zip(cols, rows[0]))
    r1 = dict(zip(cols, rows[1]))

    revenue_growth = safe_pct(r0["revenue"], r1["revenue"])
    op_growth      = safe_pct(r0["operating_profit"], r1["operating_profit"]) if r0["operating_profit"] and r1["operating_profit"] else None
    op_margin      = (r0["operating_profit"] / r0["revenue"] * 100) if r0["operating_profit"] and r0["revenue"] > 0 else None
    roe_calc       = None
    if r0["net_income"] and r0["total_equity"] and r0["total_equity"] > 0:
        roe_calc = r0["net_income"] / r0["total_equity"] * 100

    cf_row = conn.execute("""
        SELECT year, quarter, operating_cf_q, capex_q
        FROM cash_flow_data
        WHERE stock_code = ?
          AND is_annual = 0
          AND report_type = 'CFS'
          AND operating_cf_q IS NOT NULL
        ORDER BY year DESC, quarter DESC
        LIMIT 1
    """, (stock_code,)).fetchone()
    latest_q_ocf = cf_row[2] if cf_row else None
    latest_q_capex = cf_row[3] if cf_row else None

    return {
        "revenue_growth": revenue_growth,
        "op_growth": op_growth,
        "op_margin": op_margin,
        "roe_calc": roe_calc,
        "latest_revenue": r0["revenue"],
        "latest_op": r0["operating_profit"],
        "op_profit": r0["operating_profit"],       # 흑자전환 감지용
        "op_profit_prev": r1["operating_profit"],  # 흑자전환 감지용
        "latest_q_operating_cf": latest_q_ocf,
        "latest_q_capex": latest_q_capex,
    }


def _fetch_supply(conn, stock_code: str) -> dict:
    """최근 10영업일 기관/외국인 순매수 합계 (억원) + 공매도 신호."""
    rows = conn.execute("""
        SELECT inst_net_buy_amt, frn_net_buy_amt,
               inst_net_buy, frn_net_buy, close
        FROM   price_history
        WHERE  stock_code = ?
          AND  close > 0
        ORDER  BY date DESC
        LIMIT  10
    """, (stock_code,)).fetchall()

    if not rows:
        return {"inst_net_10d": 0, "frn_net_10d": 0, "short_signal": None,
                "supply_data_quality": 0.0}

    inst_total = 0.0
    frn_total  = 0.0
    # 수급 금액 데이터 품질 체크: amt 컬럼에 실제값이 있는 행 비율
    # 백테스트: 2021-22년 inst_net_buy_amt 거의 0% → 수급 신호 신뢰 불가
    amt_rows_count = sum(1 for r in rows if r[0] and r[0] != 0)
    supply_data_quality = amt_rows_count / len(rows)  # 0.0~1.0

    for r in rows:
        inst_amt, frn_amt, inst_qty, frn_qty, close = r
        if inst_amt and inst_amt != 0:
            inst_total += inst_amt / 100.0
        elif inst_qty and close:
            inst_total += inst_qty * close / 1e8
        if frn_amt and frn_amt != 0:
            frn_total += frn_amt / 100.0
        elif frn_qty and close:
            frn_total += frn_qty * close / 1e8

    # 공매도 잔고 급감 신호 (숏커버 = 반등 선행)
    short_signal = None
    short_rows = conn.execute("""
        SELECT borrow_bal_qty, borrow_bal_pct
        FROM   short_sell_daily
        WHERE  stock_code = ?
        ORDER  BY bas_dt DESC
        LIMIT  20
    """, (stock_code,)).fetchall()
    if len(short_rows) >= 10:
        recent_bal  = sum(r[0] for r in short_rows[:5]  if r[0]) / 5
        prev_bal    = sum(r[0] for r in short_rows[10:15] if r[0]) / 5 if short_rows[10:15] else 0
        if prev_bal > 0 and recent_bal < prev_bal * 0.6:
            drop_pct = (1 - recent_bal / prev_bal) * 100
            short_signal = {"type": "급감", "drop_pct": round(drop_pct)}

    # 신용잔고 트렌드 — kiwoom_credit_balance 우선, 없으면 margin_balance_daily
    credit_trend = None
    try:
        credit_rows = conn.execute("""
            SELECT credit_balance_qty FROM kiwoom_credit_balance
            WHERE stock_code = ? AND credit_balance_qty > 0
            ORDER BY dt DESC LIMIT 30
        """, (stock_code,)).fetchall()
        if len(credit_rows) < 10:  # kiwoom 없으면 margin_balance_daily fallback
            credit_rows = conn.execute("""
                SELECT credit_balance FROM margin_balance_daily
                WHERE stock_code = ? AND credit_balance > 0
                ORDER BY dt DESC LIMIT 30
            """, (stock_code,)).fetchall()
        if len(credit_rows) >= 15:
            recent_credit = sum(r[0] for r in credit_rows[:5]) / 5
            prev_credit   = sum(r[0] for r in credit_rows[15:20]) / 5
            if prev_credit > 0:
                chg_pct = (recent_credit - prev_credit) / prev_credit * 100
                if chg_pct <= -20:
                    credit_trend = {"type": "급감", "chg_pct": round(chg_pct)}
                elif chg_pct >= 30:
                    credit_trend = {"type": "급증", "chg_pct": round(chg_pct)}
    except Exception:
        pass

    # 외국인 지분율 추이 (kiwoom_foreign_flow) — 최근 20일 증가 여부
    foreign_hold_trend = None
    try:
        fh_rows = conn.execute("""
            SELECT weight FROM kiwoom_foreign_flow
            WHERE stock_code = ? AND weight > 0
            ORDER BY dt DESC LIMIT 30
        """, (stock_code,)).fetchall()
        if len(fh_rows) >= 10:
            recent_w = sum(r[0] for r in fh_rows[:5]) / 5
            prev_w   = sum(r[0] for r in fh_rows[15:20]) / 5 if fh_rows[15:20] else 0
            if prev_w > 0:
                delta = recent_w - prev_w
                if delta >= 1.5:
                    foreign_hold_trend = {"type": "증가", "delta_pct": round(delta, 2),
                                          "current": round(recent_w, 2)}
                elif delta <= -2.0:
                    foreign_hold_trend = {"type": "감소", "delta_pct": round(delta, 2),
                                          "current": round(recent_w, 2)}
    except Exception:
        pass

    # 임원 매매 신호 (dart_insider_holdings) — 최근 90일 CEO/임원 순매수
    insider_signal = None
    try:
        insider_rows = conn.execute("""
            SELECT change_amount, is_ceo, rcept_dt, isu_exctv_ofcps
            FROM dart_insider_holdings
            WHERE stock_code = ?
              AND rcept_dt >= date('now', '-90 days')
              AND change_amount IS NOT NULL
            ORDER BY rcept_dt DESC
        """, (stock_code,)).fetchall()
        if insider_rows:
            buy_total  = sum(r[0] for r in insider_rows if r[0] and r[0] > 0)
            sell_total = sum(abs(r[0]) for r in insider_rows if r[0] and r[0] < 0)
            ceo_buy    = any(r[1] == 1 and r[0] and r[0] > 0 for r in insider_rows)
            net = buy_total - sell_total
            if net > 0 or ceo_buy:
                insider_signal = {
                    "type": "매수",
                    "buy_qty": int(buy_total),
                    "sell_qty": int(sell_total),
                    "net_qty": int(net),
                    "ceo_buy": ceo_buy,
                    "cnt": len(insider_rows),
                }
            elif net < -10000:
                insider_signal = {
                    "type": "매도",
                    "buy_qty": int(buy_total),
                    "sell_qty": int(sell_total),
                    "net_qty": int(net),
                    "ceo_buy": False,
                    "cnt": len(insider_rows),
                }
    except Exception:
        pass

    return {
        "inst_net_10d": round(inst_total),
        "frn_net_10d": round(frn_total),
        "short_signal": short_signal,
        "credit_trend": credit_trend,
        "foreign_hold_trend": foreign_hold_trend,
        "insider_signal": insider_signal,
        "supply_data_quality": round(supply_data_quality, 2),
    }


def _fetch_extra_signals(conn, stock_code: str) -> dict:
    """수주잔고, CB/BW 희석 이력 + 원가구조 개선 조회."""
    # 수주잔고 (최신 분기)
    backlog = conn.execute("""
        SELECT ob.backlog_amount, ob.backlog_to_rev, ob.year, ob.quarter
        FROM order_backlog ob
        JOIN dart_backlog_quarterly dbq
          ON dbq.stock_code=ob.stock_code
         AND dbq.fiscal_year=ob.year
         AND dbq.fiscal_quarter=ob.quarter
        WHERE ob.stock_code = ? AND ob.backlog_amount > 0
          AND dbq.backlog_confidence >= 0.95
        ORDER BY ob.year DESC, ob.quarter DESC
        LIMIT  1
    """, (stock_code,)).fetchone()

    # CB/BW 최근 1년 내 발행 여부 (희석 악재)
    dilution = conn.execute("""
        SELECT COUNT(*) as cnt, SUM(dilution_pct) as total_pct
        FROM   dilution_events
        WHERE  stock_code = ?
          AND  disclosed_at >= date('now', '-365 days')
          AND  event_type IN ('CB', 'BW', 'EB', 'RIGHTS', 'RIGHTS_BONUS', '유상증자')
          AND  COALESCE(report_nm, '') NOT LIKE '%정정%'
    """, (stock_code,)).fetchone()

    # 수주잔고 추이 분석 (최근 6분기)
    backlog_rows = conn.execute("""
        SELECT ob.year, ob.quarter, ob.backlog_to_rev, ob.backlog_normalized
        FROM order_backlog ob
        JOIN dart_backlog_quarterly dbq
          ON dbq.stock_code=ob.stock_code
         AND dbq.fiscal_year=ob.year
         AND dbq.fiscal_quarter=ob.quarter
        WHERE ob.stock_code = ? AND ob.backlog_amount > 0
          AND dbq.backlog_confidence >= 0.95
        ORDER BY ob.year DESC, ob.quarter DESC
        LIMIT 6
    """, (stock_code,)).fetchall()

    backlog_data = None
    if backlog and backlog[0]:
        # 추세 분석: 최근 2분기 연속 증가 여부
        trend_up = False
        if len(backlog_rows) >= 2:
            latest_to_rev = backlog_rows[0][2]
            prev_to_rev   = backlog_rows[1][2]
            if latest_to_rev is not None and prev_to_rev is not None:
                trend_up = latest_to_rev > prev_to_rev
        backlog_data = {
            "amount": backlog[0],
            "to_rev": backlog[1],
            "year": backlog[2],
            "quarter": backlog[3],
            "trend_up": trend_up,
            "quarters_available": len(backlog_rows),
        }

    dilution_risk = False
    dilution_pct  = 0.0
    if dilution and dilution[0] > 0:
        dilution_risk = True
        dilution_pct  = dilution[1] or 0.0

    dilution_count_1y = 0
    dilution_count_3y = 0
    repeat_dilution_risk = False
    repeat_dilution_level = None
    future_put_liquidity_risk = None
    try:
        dilution_count_1y = int(conn.execute("""
            SELECT COUNT(*)
            FROM dilution_events
            WHERE stock_code=?
              AND disclosed_at >= date('now', '-365 days')
              AND event_type IN ('CB', 'BW', 'EB', 'RIGHTS', 'RIGHTS_BONUS')
              AND COALESCE(risk_amount_status, 'amount_confirmed') != 'not_amount_applicable'
              AND COALESCE(report_nm, '') NOT LIKE '%정정%'
        """, (stock_code,)).fetchone()[0] or 0)
        dilution_count_3y = int(conn.execute("""
            SELECT COUNT(*)
            FROM dilution_events
            WHERE stock_code=?
              AND disclosed_at >= date('now', '-1095 days')
              AND event_type IN ('CB', 'BW', 'EB', 'RIGHTS', 'RIGHTS_BONUS')
              AND COALESCE(risk_amount_status, 'amount_confirmed') != 'not_amount_applicable'
              AND COALESCE(report_nm, '') NOT LIKE '%정정%'
        """, (stock_code,)).fetchone()[0] or 0)
        if dilution_count_1y >= 5 or dilution_count_3y >= 10:
            repeat_dilution_risk = True
            repeat_dilution_level = "severe"
        elif dilution_count_1y >= 3 or dilution_count_3y >= 6:
            repeat_dilution_risk = True
            repeat_dilution_level = "high"

        cash_row = conn.execute("""
            SELECT cash_end
            FROM cash_flow_data
            WHERE stock_code=? AND cash_end IS NOT NULL
            ORDER BY year DESC, quarter DESC, (report_type='CFS') DESC
            LIMIT 1
        """, (stock_code,)).fetchone()
        latest_cash = float(cash_row[0]) if cash_row and cash_row[0] is not None else None
        put_rows = conn.execute("""
            SELECT put_option_date, issue_amount, event_type, report_nm
            FROM dilution_events
            WHERE stock_code=?
              AND put_option_date IS NOT NULL
              AND issue_amount IS NOT NULL
              AND put_option_date >= date('now')
              AND COALESCE(report_nm, '') NOT LIKE '%정정%'
            ORDER BY put_option_date ASC
            LIMIT 5
        """, (stock_code,)).fetchall()
        for row in put_rows:
            issue_amount = float(row[1] or 0)
            shortfall = (issue_amount - latest_cash) if latest_cash is not None else None
            if shortfall is not None and shortfall > 0:
                future_put_liquidity_risk = {
                    "put_option_date": row[0],
                    "issue_amount_억": round(issue_amount / 1e8, 1),
                    "current_cash_억": round(latest_cash / 1e8, 1) if latest_cash is not None else None,
                    "shortfall_억": round(shortfall / 1e8, 1),
                    "event_type": row[2],
                    "report_nm": row[3],
                }
                break
    except Exception:
        pass

    original_contract_count_1y = 0
    try:
        original_contract_count_1y = int(conn.execute("""
            SELECT COUNT(*)
            FROM dart_contracts
            WHERE stock_code=?
              AND disclosed_at >= date('now', '-365 days')
              AND COALESCE(report_nm, '') NOT LIKE '%정정%'
        """, (stock_code,)).fetchone()[0] or 0)
    except Exception:
        pass

    # 원가구조 개선 신호 (cost_structure) — cogs_ratio YoY 감소 = 마진 개선 선행 신호
    cost_improvement = None
    try:
        cost_rows = conn.execute("""
            SELECT year, quarter, cogs_ratio, total_cogs, revenue
            FROM cost_structure
            WHERE stock_code = ? AND cogs_ratio IS NOT NULL AND cogs_ratio > 0
            ORDER BY year DESC, quarter DESC LIMIT 4
        """, (stock_code,)).fetchall()
        if len(cost_rows) >= 2:
            latest = cost_rows[0]
            prev_yr = next((r for r in cost_rows if r[0] == latest[0] - 1 and r[1] == latest[1]), None)
            if prev_yr:
                delta = latest[2] - prev_yr[2]  # cogs_ratio 변화 (음수 = 개선)
                if delta <= -3:
                    cost_improvement = {"type": "개선", "delta_pct": round(delta, 1),
                                        "current_ratio": round(latest[2], 1)}
                elif delta >= 5:
                    cost_improvement = {"type": "악화", "delta_pct": round(delta, 1),
                                        "current_ratio": round(latest[2], 1)}
    except Exception:
        pass

    # 고정비/변동비 구조 분석 (cost_breakdown) — 레버리지 효과 판단
    cost_leverage = None
    try:
        cb_rows = conn.execute("""
            SELECT year, quarter, fixed_cost_ratio, variable_cost_ratio,
                   labor_ratio, material_ratio, total_cogs, revenue
            FROM cost_breakdown
            WHERE stock_code=? AND fixed_cost_ratio IS NOT NULL
            ORDER BY year DESC, quarter DESC LIMIT 4
        """, (stock_code,)).fetchall()
        if cb_rows:
            latest_cb = cb_rows[0]
            fixed_r = latest_cb[2]   # 고정비율
            var_r   = latest_cb[3]   # 변동비율
            # 고정비 레버리지: 고정비 비율이 높을수록 매출 증가 시 이익 급증
            # 단, 매출 감소 시 위험도 높음
            if fixed_r is not None and var_r is not None:
                # YoY 고정비율 변화
                prev_yr_cb = next((r for r in cb_rows if r[0] == latest_cb[0] - 1 and r[1] == latest_cb[1]), None)
                fixed_ratio_delta = None
                if prev_yr_cb and prev_yr_cb[2] is not None:
                    fixed_ratio_delta = fixed_r - prev_yr_cb[2]
                cost_leverage = {
                    "fixed_ratio": round(fixed_r, 1),
                    "variable_ratio": round(var_r, 1),
                    "leverage_type": "고레버리지" if fixed_r > 40 else "저레버리지",
                    "fixed_ratio_delta": round(fixed_ratio_delta, 1) if fixed_ratio_delta else None,
                }
    except Exception:
        pass

    # 자사주 취득 신호 (최근 180일 내 취득결정/결과)
    buyback_signal = None
    try:
        bb_rows = conn.execute("""
            SELECT COUNT(*) as cnt, MAX(rcept_dt) as last_dt
            FROM treasury_buyback
            WHERE stock_code=? AND event_type IN ('취득결정','취득결과')
              AND rcept_dt >= date('now', '-180 days')
        """, (stock_code,)).fetchone()
        if bb_rows and bb_rows[0] > 0:
            cancel_cnt = conn.execute("""
                SELECT COUNT(*) FROM treasury_buyback
                WHERE stock_code=? AND event_type='소각'
                  AND rcept_dt >= date('now', '-365 days')
            """, (stock_code,)).fetchone()[0]
            buyback_signal = {
                "cnt": bb_rows[0], "last_dt": bb_rows[1],
                "has_cancellation": cancel_cnt > 0
            }
    except Exception:
        pass

    # PBR 역사적 백분위 (valuation_history)
    pbr_percentile = None
    try:
        vh_rows = conn.execute("""
            SELECT pbr FROM valuation_history
            WHERE stock_code=? AND pbr IS NOT NULL AND pbr > 0
            ORDER BY year DESC, quarter DESC LIMIT 20
        """, (stock_code,)).fetchall()
        if len(vh_rows) >= 4:
            pbrs = sorted(r[0] for r in vh_rows)
            current_pbr = pbrs[-1] if vh_rows[0][0] else None
            # current is the most recent (first row before sort)
            current_pbr = vh_rows[0][0]
            pct_rank = sum(1 for p in pbrs if p <= current_pbr) / len(pbrs)
            pbr_percentile = {
                "current": round(current_pbr, 2),
                "pct_rank": round(pct_rank, 2),
                "historical_min": round(pbrs[0], 2),
                "historical_max": round(pbrs[-1], 2),
                "quarters": len(pbrs),
            }
    except Exception:
        pass

    # 특허/기술이전/R&D 신호 (최근 1년 내)
    rd_patent_signal = None
    try:
        rp_rows = conn.execute("""
            SELECT signal_type, COUNT(*) as cnt, MAX(rcept_dt) as latest
            FROM dart_rd_patent_signals
            WHERE stock_code = ? AND rcept_dt >= date('now', '-365 days')
            GROUP BY signal_type
        """, (stock_code,)).fetchall()
        if rp_rows:
            by_type = {r[0]: {"cnt": r[1], "latest": r[2]} for r in rp_rows}
            rd_patent_signal = {
                "patent": by_type.get("patent"),
                "tech_transfer": by_type.get("tech_transfer"),
                "rd_contract": by_type.get("rd_contract"),
                "license": by_type.get("license"),
            }
    except Exception:
        pass

    # 연간 매입재료비 YoY 신호 + 3중 검증 복합 시그널
    # Codex event study 결과 (2026-06-21):
    #   - 단독 YoY: Spearman 0.06 (무의미), 100%+ 급증은 오히려 역관계(-19.4%)
    #   - 복합 시그널: 25~100% + 매출15%+ + 재고감소 → 수요주도 성장 확인 시 유효
    material_signal = None
    material_composite = None
    try:
        mat_rows = conn.execute("""
            SELECT year, material_purchase_krw
            FROM dart_material_purchase
            WHERE stock_code=? AND material_purchase_krw IS NOT NULL
              AND material_purchase_krw > 0
            ORDER BY year DESC LIMIT 3
        """, (stock_code,)).fetchall()
        if len(mat_rows) >= 2:
            cur_yr, cur_mat = mat_rows[0]
            prev_yr, prev_mat = mat_rows[1]
            if prev_mat and prev_mat > 0:
                yoy_pct = (cur_mat - prev_mat) / prev_mat * 100
                material_signal = {
                    "year": cur_yr,
                    "amount_억": round(cur_mat / 1e8, 0),
                    "yoy_pct": round(yoy_pct, 1),
                    "trend": "급증" if yoy_pct >= 50 else "증가" if yoy_pct >= 20 else "보합" if yoy_pct >= -10 else "감소",
                }

                # ── 3중 검증: 25~100% 구간에서만 의미 (Codex 분석 결과) ──
                if 25.0 <= yoy_pct <= 100.0:
                    # 1) 동연도 매출 성장률 확인
                    rev_rows = conn.execute("""
                        SELECT year, revenue FROM financial_data
                        WHERE stock_code=? AND is_annual=1
                          AND revenue IS NOT NULL AND revenue > 0
                        ORDER BY year DESC LIMIT 2
                    """, (stock_code,)).fetchall()
                    rev_growth = None
                    if len(rev_rows) >= 2:
                        r_cy, r_cur = rev_rows[0]
                        r_py, r_prev = rev_rows[1]
                        if r_prev > 0 and r_cy == cur_yr:
                            rev_growth = (r_cur - r_prev) / r_prev * 100

                    # 2) 재고자산 YoY 변화 (dart_cost_quarterly — 연말 Q4 또는 최신 분기)
                    inv_rows = conn.execute("""
                        SELECT fiscal_year, inventory_assets_krw
                        FROM dart_cost_quarterly
                        WHERE stock_code=? AND inventory_assets_krw IS NOT NULL
                          AND inventory_assets_krw > 0
                        ORDER BY fiscal_year DESC, fiscal_quarter DESC LIMIT 2
                    """, (stock_code,)).fetchall()
                    inv_yoy_pct = None
                    if len(inv_rows) >= 2:
                        inv_cur  = inv_rows[0][1]
                        inv_prev = inv_rows[1][1]
                        if inv_prev > 0:
                            inv_yoy_pct = (inv_cur - inv_prev) / inv_prev * 100

                    rev_ok = rev_growth is not None and rev_growth >= 15.0
                    inv_ok = inv_yoy_pct is not None and inv_yoy_pct < 0.0

                    material_composite = {
                        "mat_yoy":       round(yoy_pct, 1),
                        "rev_growth":    round(rev_growth, 1) if rev_growth is not None else None,
                        "inv_yoy":       round(inv_yoy_pct, 1) if inv_yoy_pct is not None else None,
                        "rev_ok":        rev_ok,
                        "inv_ok":        inv_ok,
                        # 3중 통과 = 수요 주도 성장 (생산비↑ + 매출↑ + 재고소화)
                        "composite_pass": rev_ok and inv_ok,
                        # 2중 통과 = 매출 성장 확인 (재고 데이터 없거나 증가)
                        "rev_pass_only":  rev_ok and not inv_ok,
                    }
    except Exception:
        pass

    # ── QoQ 분기 실적 기반 조기 변화 시그널 ──────────────────────────
    # 설계 기준 (사용자 2026-06-21):
    #   수주잔고 QoQ 증가 + 감가상각비 QoQ 증가 + EPS/ROE QoQ 개선
    #   + 재고자산 QoQ 급증은 감점 + 외국인/기관 수급 동반 + RS 추세 개선
    #   → QoQ 펀더멘털 변화 + 수급 + RS 3중 복합 조기 신호
    qoq_signals: dict = {}
    try:
        # 1) 수주잔고 QoQ
        bl_rows = conn.execute("""
            SELECT ob.year, ob.quarter, ob.backlog_amount
            FROM order_backlog ob
            JOIN dart_backlog_quarterly dbq
              ON dbq.stock_code=ob.stock_code
             AND dbq.fiscal_year=ob.year
             AND dbq.fiscal_quarter=ob.quarter
            WHERE ob.stock_code=? AND ob.backlog_amount > 0
              AND dbq.backlog_confidence >= 0.95
            ORDER BY ob.year DESC, ob.quarter DESC LIMIT 2
        """, (stock_code,)).fetchall()
        if len(bl_rows) >= 2:
            bl_cur, bl_prev = bl_rows[0][2], bl_rows[1][2]
            if bl_prev > 0:
                qoq_signals["backlog_qoq"] = round((bl_cur - bl_prev) / bl_prev * 100, 1)

        # 2) 최근 2개 분기 매출 QoQ (Q4 파생 제외)
        rev_q_rows = conn.execute("""
            SELECT year, quarter, revenue
            FROM financial_data
            WHERE stock_code=? AND is_annual=0
              AND revenue IS NOT NULL AND revenue > 0
              AND quarter BETWEEN 1 AND 3
            ORDER BY year DESC, quarter DESC LIMIT 2
        """, (stock_code,)).fetchall()
        if len(rev_q_rows) >= 2:
            rq_cur, rq_prev = rev_q_rows[0][2], rev_q_rows[1][2]
            if rq_prev > 0:
                qoq_signals["revenue_qoq"] = round((rq_cur - rq_prev) / rq_prev * 100, 1)

        # 3) 영업이익 QoQ (흑자전환 포함)
        op_q_rows = conn.execute("""
            SELECT year, quarter, operating_profit
            FROM financial_data
            WHERE stock_code=? AND is_annual=0
              AND operating_profit IS NOT NULL
              AND quarter BETWEEN 1 AND 3
            ORDER BY year DESC, quarter DESC LIMIT 2
        """, (stock_code,)).fetchall()
        if len(op_q_rows) >= 2:
            oq_cur, oq_prev = op_q_rows[0][2], op_q_rows[1][2]
            if oq_prev < 0 and oq_cur > 0:
                qoq_signals["op_turnaround"] = True
            elif oq_prev > 0:
                qoq_signals["op_qoq"] = round((oq_cur - oq_prev) / oq_prev * 100, 1)

        # 4) 재고자산 QoQ (dart_cost_quarterly)
        inv_q_rows = conn.execute("""
            SELECT fiscal_year, fiscal_quarter, inventory_assets_krw
            FROM dart_cost_quarterly
            WHERE stock_code=? AND inventory_assets_krw IS NOT NULL
              AND inventory_assets_krw > 0
            ORDER BY fiscal_year DESC, fiscal_quarter DESC LIMIT 2
        """, (stock_code,)).fetchall()
        if len(inv_q_rows) >= 2:
            iq_cur, iq_prev = inv_q_rows[0][2], inv_q_rows[1][2]
            if iq_prev > 0:
                qoq_signals["inventory_qoq"] = round((iq_cur - iq_prev) / iq_prev * 100, 1)

        # 5) 감가상각비 QoQ (설비 투자 확대 증거)
        dep_q_rows = conn.execute("""
            SELECT year, quarter, depreciation
            FROM cash_flow_data
            WHERE stock_code=? AND is_annual=0
              AND depreciation IS NOT NULL AND depreciation > 0
            ORDER BY year DESC, quarter DESC LIMIT 2
        """, (stock_code,)).fetchall()
        if len(dep_q_rows) >= 2:
            dq_cur, dq_prev = dep_q_rows[0][2], dep_q_rows[1][2]
            if dq_prev > 0:
                qoq_signals["depreciation_qoq"] = round((dq_cur - dq_prev) / dq_prev * 100, 1)

        # 6) EPS QoQ
        eps_q_rows = conn.execute("""
            SELECT year, quarter, eps
            FROM financial_data
            WHERE stock_code=? AND is_annual=0
              AND eps IS NOT NULL AND quarter BETWEEN 1 AND 3
            ORDER BY year DESC, quarter DESC LIMIT 2
        """, (stock_code,)).fetchall()
        if len(eps_q_rows) >= 2:
            eq_cur, eq_prev = eps_q_rows[0][2], eps_q_rows[1][2]
            if eq_prev is not None and eq_prev < 0 and eq_cur > 0:
                qoq_signals["eps_turnaround"] = True
            elif eq_prev is not None and eq_prev > 0 and eq_cur is not None:
                qoq_signals["eps_qoq"] = round((eq_cur - eq_prev) / eq_prev * 100, 1)

    except Exception:
        pass

    return {
        "backlog": backlog_data,
        "dilution_risk": dilution_risk,
        "dilution_pct": dilution_pct,
        "dilution_count_1y": dilution_count_1y,
        "dilution_count_3y": dilution_count_3y,
        "repeat_dilution_risk": repeat_dilution_risk,
        "repeat_dilution_level": repeat_dilution_level,
        "future_put_liquidity_risk": future_put_liquidity_risk,
        "original_contract_count_1y": original_contract_count_1y,
        "cost_improvement": cost_improvement,
        "buyback_signal": buyback_signal,
        "pbr_percentile": pbr_percentile,
        "cost_leverage": cost_leverage,
        "rd_patent_signal": rd_patent_signal,
        "material_signal": material_signal,
        "material_composite": material_composite,
        "qoq_signals": qoq_signals,
    }


def _passes_tenbagger_guardrails(price: dict, fin: dict, supply: dict, uni: dict,
                                 extra: dict) -> tuple[bool, list[str]]:
    failures: list[str] = []

    tvol5 = float(price.get("avg_tvol5_억") or 0)
    if tvol5 < TENBAGGER_MIN_AVG_TVOL5_억:
        failures.append(f"5일 평균 거래대금 {tvol5:.1f}억 < {TENBAGGER_MIN_AVG_TVOL5_억:.0f}억")

    dilution_pct = float(extra.get("dilution_pct") or 0)
    if dilution_pct >= TENBAGGER_MAX_DILUTION_PCT:
        failures.append(f"최근 1년 누적 희석 {dilution_pct:.1f}% >= {TENBAGGER_MAX_DILUTION_PCT:.0f}%")

    repeat_level = extra.get("repeat_dilution_level")
    if repeat_level == "severe":
        failures.append(
            f"반복 희석 위험 과다 (1년 {int(extra.get('dilution_count_1y') or 0)}건 / 3년 {int(extra.get('dilution_count_3y') or 0)}건)"
        )

    put_risk = extra.get("future_put_liquidity_risk") or {}
    if put_risk.get("shortfall_억", 0) and float(put_risk["shortfall_억"]) > 0:
        failures.append(
            f"향후 풋옵션 상환 부족 {float(put_risk['shortfall_억']):.1f}억 ({put_risk.get('put_option_date')})"
        )

    rev_g = fin.get("revenue_growth")
    op_g = fin.get("op_growth")
    op_m = fin.get("op_margin")
    op_cur = fin.get("op_profit")
    op_prev = fin.get("op_profit_prev")
    latest_q_ocf = fin.get("latest_q_operating_cf")

    if rev_g is not None and rev_g >= 30:
        if (op_m is None or op_m <= 0) and (op_g is None or op_g < -50):
            failures.append(
                f"매출은 +{rev_g:.0f}% 성장했지만 수익성 방어 미충족"
            )

    if op_g is not None and op_g < -200:
        failures.append(f"영업이익 성장률 {op_g:.0f}% < -200%")

    if op_prev is not None and op_cur is not None and op_prev < 0 < op_cur:
        if latest_q_ocf is None or latest_q_ocf <= 0:
            failures.append("영업이익 흑자전환이지만 최근 분기 영업현금흐름이 양수가 아님")

    return (len(failures) == 0), failures


# ──────────────────────────────────────────────
# 스코어링 엔진
# ──────────────────────────────────────────────

def _score_stock(price: dict, fin: dict, supply: dict, uni: dict,
                 extra: dict | None = None) -> tuple[float, dict, list[str]]:
    """6축 스코어 계산 → (total, detail, reasons)

    데이터 기반 재설계 (2026-06-18):
    실제 3배 달성 1,991종목 역산 분석 결과:
    - 70.5%가 52주 고가 대비 -30~70% 낙폭과대 상태에서 출발
    - 40.5%가 적자 상태에서 3배 달성 (적자 패널티 제거)
    - 기관 매수 비율: 20.8%만 순매수 (기관매수 과대평가 수정)

    새 구조: 낙폭과대(25) + 펀더멘털변화(25) + 저평가(20) + 촉매(15) + 수급반전(10) + 섹터(5) = 100점
    """
    scores = {
        "drawdown":   0.0,   # 낙폭과대/바닥권 (25점) ← 데이터상 핵심신호
        "fundamental": 0.0,  # 펀더멘털 변화 (25점) — 흑자전환/매출급증/수주폭발
        "value":      0.0,   # 저평가+소형주 (20점)
        "catalyst":   0.0,   # 촉매 (15점) — 수주/기술이전/자사주
        "supply":     0.0,   # 수급 반전 (10점)
        "sector":     0.0,   # 섹터 모멘텀 (5점)
    }
    reasons = []

    cur   = price.get("current_price")
    ma20  = price.get("ma20")
    ma60  = price.get("ma60")
    ma120 = price.get("ma120")
    h52   = price.get("high_52w")
    l52   = price.get("low_52w")
    tvol  = price.get("avg_tvol5_억", 0)

    # ── 1. 낙폭과대 / 바닥권 포착 (25점) ──────────────────────────
    # 실제 3배 종목 70.5%: 52주 고가 대비 -30% 이상 하락 상태에서 출발
    # 5배+ 종목: 52주 고가 대비 중앙값 -90.5% (극심 낙폭)
    if cur and h52 and h52 > 0:
        from_high_pct = (cur / h52 - 1) * 100  # 음수: 얼마나 하락했는지
        dd_score = 0

        # 핵심 낙폭과대 구간: -30~70% (실제 3배 종목의 황금지대)
        if -70 <= from_high_pct <= -30:
            dd_score = 20
            reasons.append(f"📉 52주 고가 대비 {from_high_pct:.0f}% 낙폭과대 (바닥권)")
        elif -85 <= from_high_pct < -70:
            dd_score = 18  # 극심 낙폭 — 5배+ 종목 패턴
            reasons.append(f"📉 52주 고가 대비 {from_high_pct:.0f}% 극심 낙폭 (초바닥)")
        elif -30 < from_high_pct <= -15:
            dd_score = 10  # 중간 낙폭
        elif -15 < from_high_pct <= -5:
            dd_score = 5   # 소폭 조정
        elif from_high_pct > -5:
            dd_score = 0   # 신고가권 — 이미 상승 중 (텐버거 후보 아님)

        # 52주 저가 근접 보너스 (바닥 포착 강화)
        if l52 and l52 > 0:
            from_low_pct = (cur / l52 - 1) * 100  # 저가 대비 반등률
            if from_low_pct <= 5:
                dd_score = min(dd_score + 5, 25)
                reasons.append(f"🔻 52주 저점 ±5% 이내 (바닥권 집중)")
            elif from_low_pct <= 15:
                dd_score = min(dd_score + 3, 25)
                reasons.append(f"🔻 52주 저점 +{from_low_pct:.0f}% (저점 근접)")

        # 거래대금 최소 유동성 체크
        if tvol < TENBAGGER_MIN_AVG_TVOL5_억:
            dd_score = max(0, dd_score - 5)  # 유동성 부족 패널티

        scores["drawdown"] = max(0, min(25, dd_score))

    # ── 2. 펀더멘털 변화 신호 (25점) ──────────────────────────────
    # 흑자전환 > 매출급증 > 수주폭발 > 안정성장
    # 적자 패널티 제거: 실제 40.5% 3배 종목은 적자 상태에서 출발
    rev_g   = fin.get("revenue_growth")
    op_g    = fin.get("op_growth")
    op_m    = fin.get("op_margin")
    op_prev = fin.get("op_profit_prev")
    op_cur  = fin.get("op_profit")

    fund_score = 0

    # 흑자전환 — 가장 강력한 펀더멘털 변화 신호
    if op_prev is not None and op_cur is not None:
        if op_prev < 0 and op_cur > 0:
            fund_score += 15
            reasons.append(f"🔄 영업이익 흑자전환 (전년 적자→올해 흑자, +15점)")
        elif op_prev < 0 and op_cur < 0 and op_cur > op_prev:
            # 적자 축소 = 흑자전환 전단계
            reduction = abs(op_cur - op_prev)
            if reduction / (abs(op_prev) + 1) > 0.3:
                fund_score += 8
                reasons.append(f"📈 적자 대폭 축소 (흑자전환 전단계, +8점)")

    # 매출 성장 — 흑자전환 없을 때 차선
    if rev_g is not None:
        if rev_g >= 50:
            fund_score += 12; reasons.append(f"🚀 매출 YoY +{rev_g:.0f}% 폭발 성장")
        elif rev_g >= 30:
            fund_score += 9; reasons.append(f"📈 매출 YoY +{rev_g:.0f}% 급성장")
        elif rev_g >= 15:
            fund_score += 6; reasons.append(f"📈 매출 YoY +{rev_g:.0f}% 성장")
        elif rev_g >= 5:
            fund_score += 3
        # 매출 감소 패널티 없음 — 적자 기업도 3배 달성 (데이터 근거)

    # 영업이익 성장 보완
    if op_g is not None and fund_score < 20:
        if op_g >= 100:
            fund_score += 8; reasons.append(f"💥 영업이익 YoY +{op_g:.0f}% 폭발")
        elif op_g >= 50:
            fund_score += 5; reasons.append(f"📈 영업이익 YoY +{op_g:.0f}% 급증")
        elif op_g >= 30:
            fund_score += 3

    # ── QoQ 분기 복합 시그널 (조기 변화 감지) ─────────────────────────
    # 설계: 수주잔고↑ + D&A↑ + EPS개선 + 재고↓ (개별은 약하나 3개+ 동반 시 유효)
    if extra:
        qoq_sig = extra.get("qoq_signals", {})
        if qoq_sig:
            qoq_pts = 0
            qoq_labels = []

            # 수주잔고 QoQ ≥ 10% 증가
            bl_qoq = qoq_sig.get("backlog_qoq")
            if bl_qoq is not None and bl_qoq >= 10:
                qoq_pts += 2; qoq_labels.append(f"수주잔고QoQ+{bl_qoq:.0f}%")
            elif bl_qoq is not None and bl_qoq >= 5:
                qoq_pts += 1

            # 매출 QoQ (핵심 선행 신호)
            rv_qoq = qoq_sig.get("revenue_qoq")
            if rv_qoq is not None and rv_qoq >= 10:
                qoq_pts += 2; qoq_labels.append(f"매출QoQ+{rv_qoq:.0f}%")
            elif rv_qoq is not None and rv_qoq >= 5:
                qoq_pts += 1; qoq_labels.append(f"매출QoQ+{rv_qoq:.0f}%")
            elif rv_qoq is not None and rv_qoq < -15:
                qoq_pts -= 2  # 매출 급감 패널티

            # 영업이익 QoQ 흑자전환 (분기 단위 조기 포착)
            if qoq_sig.get("op_turnaround"):
                qoq_pts += 4; qoq_labels.append("영업이익분기흑자전환")
            elif qoq_sig.get("op_qoq") is not None:
                op_qoq = qoq_sig["op_qoq"]
                if op_qoq >= 30:
                    qoq_pts += 3; qoq_labels.append(f"영업이익QoQ+{op_qoq:.0f}%")
                elif op_qoq >= 15:
                    qoq_pts += 1

            # 재고 QoQ 급증은 감점 (재고 과잉 = 수요 약화 선행 지표)
            inv_qoq = qoq_sig.get("inventory_qoq")
            if inv_qoq is not None and inv_qoq >= 30:
                qoq_pts -= 2; qoq_labels.append(f"⚠️재고QoQ+{inv_qoq:.0f}%급증")
            elif inv_qoq is not None and inv_qoq < -10:
                qoq_pts += 1; qoq_labels.append(f"재고QoQ{inv_qoq:.0f}%소화")

            # 감가상각비 QoQ 증가 (설비 투자 확대 — 생산능력 확장 전조)
            dep_qoq = qoq_sig.get("depreciation_qoq")
            if dep_qoq is not None and dep_qoq >= 10:
                qoq_pts += 1; qoq_labels.append(f"D&A QoQ+{dep_qoq:.0f}%")

            # EPS QoQ 개선
            if qoq_sig.get("eps_turnaround"):
                qoq_pts += 2; qoq_labels.append("EPS분기흑자전환")
            elif qoq_sig.get("eps_qoq") is not None and qoq_sig["eps_qoq"] >= 20:
                qoq_pts += 1

            # 수급 + RS 추세 확인 (3중 복합 보너스)
            supply_ok = (supply.get("inst_net_10d", 0) > 0 or supply.get("frn_net_10d", 0) > 0)
            rs_ok = (ma20 and ma60 and ma20 >= ma60 * 0.97)  # MA20 ≥ MA60

            if qoq_pts >= 5 and qoq_labels:
                label_str = " · ".join(qoq_labels[:3])
                if supply_ok and rs_ok:
                    # QoQ + 수급 + RS 3중 복합 — 가장 신뢰도 높은 조기 신호
                    fund_score += 8
                    reasons.append(f"🔬 QoQ 3중복합({label_str}) + 수급·RS 확인 (+8점)")
                elif supply_ok or rs_ok:
                    fund_score += 5
                    reasons.append(f"🔬 QoQ 복합({label_str}) + {'수급' if supply_ok else 'RS'} 확인 (+5점)")
                else:
                    fund_score += 3
                    reasons.append(f"🔬 QoQ 다중변화: {label_str} (+3점)")
            elif qoq_pts >= 3 and qoq_labels:
                label_str = " · ".join(qoq_labels[:2])
                fund_score += 2
                reasons.append(f"🔬 QoQ: {label_str} (+2점)")

    scores["fundamental"] = max(0, min(25, fund_score))

    # ── 3. 저평가 + 소형주 (20점) ─────────────────────────────────
    per   = uni.get("per")
    pbr   = uni.get("pbr")
    mkcap = uni.get("market_cap") or price.get("market_cap_억", 0)

    val_score = 0

    # PBR 저평가 (핵심) — 3배 종목의 38.8%가 PBR<1.0
    if pbr is not None and 0 < pbr < 100:
        if pbr <= 0.5:
            val_score += 10; reasons.append(f"💎 PBR {pbr:.2f}배 극저평가 (자산가치 이하)")
        elif pbr <= 1.0:
            val_score += 8; reasons.append(f"💎 PBR {pbr:.2f}배 저평가")
        elif pbr <= 1.5:
            val_score += 5; reasons.append(f"PBR {pbr:.2f}배 자산 저평가")
        elif pbr <= 3.0:
            val_score += 2
        # 고PBR 패널티 없음 — 성장기 기업은 PBR 높을 수 있음

    # PER 저평가 (보조)
    if per is not None and 0 < per < 200:
        if per <= 10:
            val_score += 5; reasons.append(f"PER {per:.1f}배 저평가")
        elif per <= 20:
            val_score += 3

    # 소형주 보너스 — 3배 종목 중앙값 1,580억
    if mkcap and 0 < mkcap < 300000:
        if 200 <= mkcap <= 3000:
            val_score += 5; reasons.append(f"🔍 소형주 {mkcap:.0f}억 (텐버거 주요 구간)")
        elif mkcap < 200:
            val_score += 3  # 초소형 — 유동성 위험 있음
        elif 3000 < mkcap <= 10000:
            val_score += 2  # 중형주

    scores["value"] = max(0, min(20, val_score))

    # ── 4. 기술적 추세 (trend → sector 점수로 통합) ──────────────
    # MA 정배열은 이미 상승 중 = 텐버거 후보 아님 (데이터 근거: 3배 종목은 저점에서 발굴)
    # 대신 섹터 모멘텀 (IT/의료/경기소비재 = 3배 종목 65.9%)
    sector = uni.get("sector_large", "")
    sec_score = 0
    STRONG_SECTORS = {"IT", "의료", "경기소비재", "산업재"}  # 3배 종목 상위 4개 섹터
    if sector in STRONG_SECTORS:
        sec_score = 5; reasons.append(f"🏭 {sector} 섹터 (텐버거 선호 섹터)")
    elif sector in {"필수소비재", "소재", "에너지"}:
        sec_score = 2

    # MA 추세 — 바닥 탈출 초기 감지 (정배열 완성보다 바닥권이 중요)
    if cur and ma20 and ma60:
        # MA20이 MA60을 막 돌파 = 추세 전환 초기 신호
        if cur > ma20 and ma20 > ma60 * 0.98 and cur < ma60 * 1.10:
            sec_score = min(sec_score + 2, 5)  # 바닥 탈출 초기

    scores["sector"] = max(0, min(5, sec_score))

    # ── 5. 수급 반전 감지 (10점) ──────────────────────────────────
    # 핵심 변화: 기관매수 일변도 → 기관의 '저점 유입' 감지
    # 실제 3배 종목 저점: 기관 순매도 36.5%, 중립 42.7%, 매수 20.8%
    # → 기관매수가 없어도 텐버거 가능. 단, 반전 신호는 중요
    # 백테스트: tb_supply가 2024년 이후 +334.8% — 수급 데이터 신뢰성 중요
    inst     = supply.get("inst_net_10d", 0)
    frn      = supply.get("frn_net_10d", 0)
    inst_5d  = supply.get("inst_net_5d", 0)
    dq       = supply.get("supply_data_quality", 0.0)  # 0.0~1.0, 금액 데이터 비율

    sup_score = 0

    # 수급 금액 데이터 품질이 낮으면 수급 신호 무시 (거짓 신호 방지)
    # 백테스트: 2021-22년 수급 데이터 공백 → tb_supply 성과 -8% 확인
    if dq < 0.3:
        # 데이터 부족 — 수급 스코어 0 처리 (패널티 없이 중립)
        scores["supply"] = 0
    else:
        # 기관+외인 동반 저점 매수 유입 = 스마트머니 감지
        if inst > 0 and frn > 0:
            if inst + frn > 100:
                sup_score = 10
                reasons.append(f"💰 기관+외국인 동반 유입 ({inst:+.0f}억/{frn:+.0f}억)")
            else:
                sup_score = 7
                reasons.append(f"💰 기관+외국인 동반 매수")
        elif inst > 30:
            sup_score = 7; reasons.append(f"🏦 기관 저점 매수 유입 {inst:+.0f}억")
        elif inst > 0:
            sup_score = 5; reasons.append(f"🏦 기관 매수 시작 {inst:+.0f}억")
        elif frn > 30:
            sup_score = 6; reasons.append(f"🌍 외국인 저점 유입 {frn:+.0f}억")
        elif frn > 0:
            sup_score = 4; reasons.append(f"🌍 외국인 매수 시작 {frn:+.0f}억")
        # 대규모 기관/외인 매도 중 = 아직 저점 아님 (-5 → 0으로 완화)
        elif inst < -200 or frn < -200:
            sup_score = -3  # 대규모 매도 경고 (패널티 완화)
        # 중립/소규모 매도는 패널티 없음 (데이터 근거: 79% 기관 중립/매도)
        scores["supply"] = max(0, min(10, sup_score))

    # ── 6. catalyst (15점) → 보너스 섹션으로 이동 ─────────────────
    # (아래 보너스 섹션에서 처리)

    # ── 7. 촉매 보너스: 수주공시 + 기술이전 + 자사주 + 수급이벤트 (최대 +15점) ──
    # 데이터 기반: 낙폭과대 종목에서 촉매 이벤트가 겹치면 3배+ 확률 급상승
    extra = extra or {}
    try:
        import sys as _sys, os as _os
        _root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        from signal_engine import (_load_contract_bonus_map,
                                   _load_order_contract_surge_bonus_map,
                                   _load_contract_advance_bonus_map,
                                   _load_inventory_sales_bonus_map,
                                   _load_cash_conversion_bonus_map,
                                   _load_hs_export_bonus_map)
        sc = price.get("stock_code", "")
        bonus_total = 0
        if sc:
            cb = _load_contract_bonus_map(days=90).get(sc)
            ob = _load_order_contract_surge_bonus_map(window_months=3).get(sc)
            ab = _load_contract_advance_bonus_map(min_score=4).get(sc)
            ib = _load_inventory_sales_bonus_map(min_score=4).get(sc)
            qb = _load_cash_conversion_bonus_map(min_score=4).get(sc)
            hb = _load_hs_export_bonus_map(months=6).get(sc)
            if cb:
                b = min(cb["bonus"], 5)
                bonus_total += b
                reasons.append(f'📋 수주공시 {cb["label"]} (+{b}점)')
            if ob:
                b = min(ob["bonus"], 6)
                bonus_total += b
                reasons.append(f'📈 수주급증 {ob["label"]} (+{b}점)')
            if ab:
                b = min(ab["bonus"], 5)
                bonus_total += b
                reasons.append(f'💰 {ab["label"]} ({ab["period"]}, +{b}점)')
            if ib:
                b = min(ib.get("bonus") or 0, 4)
                p = min(ib.get("risk_penalty") or 0, 4)
                if b > 0:
                    bonus_total += b
                    reasons.append(f'📦 {ib["label"]} ({ib["period"]}, +{b}점)')
                if p > 0:
                    bonus_total -= p
                    reasons.append(f'⚠️ {ib["label"]} ({ib["period"]}, -{p}점)')
            if qb:
                b = min(qb.get("bonus") or 0, 4)
                p = min(qb.get("risk_penalty") or 0, 4)
                if b > 0:
                    bonus_total += b
                    reasons.append(f'💵 {qb["label"]} ({qb["period"]}, +{b}점)')
                if p > 0:
                    bonus_total -= p
                    reasons.append(f'⚠️ {qb["label"]} ({qb["period"]}, -{p}점)')
            if hb:
                b = min(hb["bonus"], 5)
                bonus_total += b
                reasons.append(f'🚢 {hb["label"]} (+{b}점)')

        # 수주잔고 (order_backlog) 보너스 — 세분화 점수
        backlog = extra.get("backlog")
        if backlog:
            to_rev = backlog.get("to_rev")
            backlog_pts = 0
            if to_rev and to_rev >= 3.0:
                backlog_pts = 4; reasons.append(f"📦 수주잔고/매출 {to_rev:.1f}배 초대형 (+4점)")
            elif to_rev and to_rev >= 1.5:
                backlog_pts = 2; reasons.append(f"📦 수주잔고/매출 {to_rev:.1f}배 대형 (+2점)")
            elif to_rev and to_rev >= 0.5:
                backlog_pts = 1; reasons.append(f"📦 수주잔고/매출 {to_rev:.1f}배 중형 (+1점)")
            elif backlog.get("amount", 0) > 0:
                backlog_pts = 1  # 수주잔고 있음 (비율 미산출)
            # 수주잔고 증가 추세 추가 보너스
            if backlog.get("trend_up") and backlog_pts > 0:
                backlog_pts += 1; reasons.append("📈 수주잔고 증가 추세 (+1점)")
            bonus_total += backlog_pts

        # 공매도 급감 = 숏커버 반등 선행 신호
        short_sig = supply.get("short_signal")
        if short_sig and short_sig.get("type") == "급감":
            drop = short_sig["drop_pct"]
            b = 3 if drop >= 50 else 2
            bonus_total += b
            reasons.append(f"📉 공매도잔고 {drop:.0f}% 급감 (숏커버 신호, +{b}점)")

        # 신용잔고 급감 = 강제청산 이후 바닥 신호
        credit_sig = supply.get("credit_trend")
        if credit_sig and credit_sig.get("type") == "급감":
            chg = abs(credit_sig["chg_pct"])
            b = 3 if chg >= 40 else 2
            bonus_total += b
            reasons.append(f"💳 신용잔고 {chg:.0f}% 급감 (바닥신호, +{b}점)")

        # 원가구조 개선 = 마진 확대 선행 신호
        cost_sig = extra.get("cost_improvement")
        if cost_sig and cost_sig.get("type") == "개선":
            delta = abs(cost_sig["delta_pct"])
            b = 3 if delta >= 8 else 2
            bonus_total += b
            reasons.append(f"🏭 원가율 {delta:.1f}%p 개선 → 마진확대 (+{b}점)")
        elif cost_sig and cost_sig.get("type") == "악화":
            bonus_total -= 1  # 원가 악화 패널티

        # 임원 매수 신호 — CEO/임원 순매수 = 내부자 확신 신호
        insider_sig = supply.get("insider_signal")
        if insider_sig and insider_sig.get("type") == "매수":
            if insider_sig.get("ceo_buy") and (price.get("avg_tvol5_억") or 0) >= TENBAGGER_MIN_AVG_TVOL5_억:
                bonus_total += 4
                reasons.append(f"👔 CEO 직접 매수 (최근 90일, +4점)")
            elif insider_sig.get("net_qty", 0) > 50000:
                bonus_total += 3
                reasons.append(f"👔 임원 순매수 {insider_sig['net_qty']:,}주 (+3점)")
            else:
                bonus_total += 2
                reasons.append(f"👔 임원 매수 신호 ({insider_sig['cnt']}건, +2점)")
        elif insider_sig and insider_sig.get("type") == "매도":
            bonus_total -= 2
            reasons.append(f"⚠️ 임원 순매도 ({abs(insider_sig.get('net_qty',0)):,}주, -2점)")

        # 프로그램 매매 순매수 신호 (broker_program_stock_daily, 최근 5거래일)
        if sc:
            try:
                _conn_prog = connect_primary_db(timeout=5, row_factory=sqlite3.Row)
                prog_row = _conn_prog.execute(
                    "SELECT SUM(net_buy_amt_krw) AS net_amt"
                    " FROM broker_program_stock_daily"
                    " WHERE stock_code=? AND dt >= date('now','-7 days')",
                    (sc,)
                ).fetchone()
                _conn_prog.close()
                if prog_row and prog_row["net_amt"] is not None:
                    prog_amt_억 = float(prog_row["net_amt"]) / 1e8  # KRW → 억원
                    mkcap_억 = float(uni.get("market_cap") or 0)
                    if prog_amt_억 > 0 and mkcap_억 > 0:
                        pct = prog_amt_억 / mkcap_억 * 100
                        if pct >= 1.0:
                            bonus_total += 3
                            reasons.append(f"🤖 프로그램 순매수 {prog_amt_억:.0f}억 (시총대비 {pct:.1f}%, +3점)")
                        elif pct >= 0.3:
                            bonus_total += 2
                            reasons.append(f"🤖 프로그램 순매수 {prog_amt_억:.0f}억 (+2점)")
                        elif pct >= 0.1:
                            bonus_total += 1
                            reasons.append(f"🤖 프로그램 순매수 {prog_amt_억:.0f}억 (+1점)")
                    elif prog_amt_억 < -50 and mkcap_억 > 0 and (abs(prog_amt_억) / mkcap_억) >= 0.005:
                        bonus_total -= 1
                        reasons.append(f"⚠️ 프로그램 순매도 {abs(prog_amt_억):.0f}억 (-1점)")
            except Exception:
                pass

        # 외국인 지분율 증가 = 스마트머니 유입 신호
        fh_sig = supply.get("foreign_hold_trend")
        if fh_sig and fh_sig.get("type") == "증가":
            delta = fh_sig["delta_pct"]
            b = 3 if delta >= 3.0 else 2
            bonus_total += b
            reasons.append(f"🌍 외국인 지분율 +{delta:.1f}%p 증가 (+{b}점)")
        elif fh_sig and fh_sig.get("type") == "감소":
            bonus_total -= 1

        # 자사주 취득/소각 신호 — 경영진 자신감 + 주주가치 증대
        buyback_sig = extra.get("buyback_signal")
        if buyback_sig:
            b = 3 if buyback_sig.get("has_cancellation") else 2
            bonus_total += b
            label = "취득+소각" if buyback_sig.get("has_cancellation") else "취득결정"
            reasons.append(f"🔄 자사주 {label} ({buyback_sig['cnt']}건, +{b}점)")

        # 고정비 레버리지 — 고정비율 높고 매출 급증 = 이익 폭발 신호
        lev = extra.get("cost_leverage")
        if lev:
            fixed_r = lev.get("fixed_ratio", 0)
            delta   = lev.get("fixed_ratio_delta")
            if fixed_r > 45 and delta is not None and delta <= -3:
                # 고정비 높은데 고정비율이 하락 = 매출 급증으로 레버리지 효과 발현 중
                bonus_total += 3
                reasons.append(f"⚙️ 고정비 레버리지 발현 (고정비율 {fixed_r:.0f}%, YoY -{abs(delta):.0f}%p, +3점)")
            elif fixed_r > 40 and delta is not None and delta <= -5:
                bonus_total += 2
                reasons.append(f"⚙️ 고정비 레버리지 (+2점)")

        # PBR 역사적 저점 — 저평가 가치 신호
        pbr_pct = extra.get("pbr_percentile")
        if pbr_pct and pbr_pct.get("pct_rank") is not None:
            rank = pbr_pct["pct_rank"]
            if rank <= 0.15:
                bonus_total += 3
                reasons.append(f"📊 PBR 역사적 하위 {rank*100:.0f}% 저점 구간 (+3점)")
            elif rank <= 0.25:
                bonus_total += 2
                reasons.append(f"📊 PBR 역사적 하위 {rank*100:.0f}% 구간 (+2점)")
            elif rank >= 0.90:
                bonus_total -= 1  # 역사적 고점 PBR = 과열 위험

        # 특허/기술이전/R&D 신호 보너스
        rd_sig = extra.get("rd_patent_signal")
        if rd_sig:
            if rd_sig.get("tech_transfer") or rd_sig.get("license"):
                bonus_total += 3
                reasons.append("🔬 기술이전/라이선스 공시 최근 1년 (+3점)")
            elif rd_sig.get("patent"):
                patent_cnt = rd_sig["patent"]["cnt"]
                bonus_total += 2
                reasons.append(f"💡 특허 취득 {patent_cnt}건 최근 1년 (+2점)")
            elif rd_sig.get("rd_contract"):
                bonus_total += 1
                reasons.append("🔬 R&D 계약 공시 최근 1년 (+1점)")

        # 연간 매입재료비 — 3중 검증 복합 시그널 (Codex event study 결과 반영)
        # 25~100% 구간: 수요주도 성장 확인 시 유효 / 100%+: 역관계 데이터 → 보너스 없음
        mat_sig       = extra.get("material_signal")
        mat_composite = extra.get("material_composite")
        if mat_composite:
            if mat_composite.get("composite_pass"):
                # 3중 통과: 재료비 25~100% + 매출 15%+ + 재고 감소
                _my = mat_composite["mat_yoy"]
                _rg = mat_composite["rev_growth"]
                bonus_total += 5
                reasons.append(f"🏭 매입재료비 3중복합: 재료비+{_my:.0f}% · 매출+{_rg:.0f}% · 재고↓ (+5점)")
            elif mat_composite.get("rev_pass_only"):
                # 2중 통과: 재료비 25~100% + 매출 15%+ (재고 데이터 없거나 증가)
                _my = mat_composite["mat_yoy"]
                bonus_total += 3
                reasons.append(f"🏭 매입재료비+매출 확인: 재료비+{_my:.0f}% · 매출성장 (+3점)")
            else:
                # 재료비 25~100% 단독 — 단독 시그널은 약함 (Codex: +4.3% 중간값)
                _my = mat_composite["mat_yoy"]
                bonus_total += 1
                reasons.append(f"🏭 매입재료비 YoY +{_my:.0f}% (단독, +1점)")
        elif mat_sig and mat_sig.get("yoy_pct") is not None:
            yoy = mat_sig["yoy_pct"]
            if yoy > 100:
                # 100%+ 급증: Codex 분석 역관계(-19.4% median) → 보너스 없음, 경고만
                reasons.append(f"⚠️ 매입재료비 YoY +{yoy:.0f}% 급증 — 100%+ 증가 후 수익률 부진 데이터 (보너스 없음)")
            elif 25 <= yoy <= 100:
                bonus_total += 1
                reasons.append(f"🏭 매입재료비 YoY +{yoy:.0f}% (+1점)")

        # 촉매 점수 (catalyst) — 보너스로 적재 (최대 15점)
        scores["catalyst"] = max(min(bonus_total, 15), 0)
    except Exception:
        scores["catalyst"] = 0

    # CB/BW 희석 악재 패널티 (최근 1년 내 대규모 발행 시)
    if extra.get("dilution_risk"):
        dpct = extra.get("dilution_pct", 0)
        if dpct >= 20:
            scores["value"] = max(0, scores["value"] - 4)
            reasons.append(f"⚠️ CB/BW 대규모 희석 {dpct:.0f}% (패널티)")
        elif dpct >= 10:
            scores["value"] = max(0, scores["value"] - 2)
            reasons.append(f"⚠️ CB/BW 희석 {dpct:.0f}% (패널티)")

    repeat_level = extra.get("repeat_dilution_level")
    if repeat_level == "high":
        scores["value"] = max(0, scores["value"] - 3)
        scores["catalyst"] = max(0, scores["catalyst"] - 1)
        reasons.append(
            f"⚠️ 반복 희석 위험 1년 {int(extra.get('dilution_count_1y') or 0)}건 / 3년 {int(extra.get('dilution_count_3y') or 0)}건"
        )
    elif repeat_level == "severe":
        scores["value"] = max(0, scores["value"] - 6)
        scores["catalyst"] = max(0, scores["catalyst"] - 2)
        reasons.append(
            f"🚫 반복 희석 과다 1년 {int(extra.get('dilution_count_1y') or 0)}건 / 3년 {int(extra.get('dilution_count_3y') or 0)}건"
        )

    put_risk = extra.get("future_put_liquidity_risk")
    if put_risk:
        scores["value"] = max(0, scores["value"] - 6)
        scores["catalyst"] = max(0, scores["catalyst"] - 2)
        reasons.append(
            f"🚫 풋옵션 현금부족 {float(put_risk.get('shortfall_억') or 0):.1f}억 ({put_risk.get('put_option_date')})"
        )

    # 실적형 초낙폭 회복주 보완 — 과거 실증 감사에서 순수 점수 탈락군은
    # 캡처군보다 52주 고점 대비 낙폭이 훨씬 깊고(평균 -82.6%), 모델점수는 오히려 더 높았다.
    # 즉 "실적은 살아났지만 주가가 아직 처참한" 회복형 승자를 기존 휴리스틱이 과소평가.
    if extra:
        latest_q_ocf = fin.get("latest_q_operating_cf")
        rev_g = fin.get("revenue_growth")
        op_g = fin.get("op_growth")
        op_prev = fin.get("op_profit_prev")
        op_cur = fin.get("op_profit")
        fh = price.get("from_high_pct") or 0
        tvol = price.get("avg_tvol5_억") or 0
        earnings_recovery = False
        if rev_g is not None and rev_g >= 15 and latest_q_ocf is not None and latest_q_ocf > 0:
            if (op_g is not None and op_g >= 30) or (op_prev is not None and op_cur is not None and op_prev < 0 < op_cur):
                earnings_recovery = True
        if earnings_recovery and fh <= -75 and tvol >= TENBAGGER_MIN_AVG_TVOL5_억:
            scores["fundamental"] = min(25, scores["fundamental"] + 4)
            scores["drawdown"] = min(25, scores["drawdown"] + 2)
            reasons.append("🧪 실적형 초낙폭 회복 패턴 (실증 보정 +6점)")

    # ── 콤보 시너지 보너스 ─────────────────────────────────────────────────────
    # 백테스트 결과: tb_combo(낙폭+흑자전환)이 6년 누적 +254.3%로 최고
    # → 두 신호의 교집합에 추가 가중치 부여

    # A) 낙폭과대 + 펀더멘털 콤보 (핵심 시너지, +8점)
    # tb_combo 분석: 하락장 +240%, 전체 +254% — 가장 강력한 조합
    if scores["drawdown"] >= 18 and scores["fundamental"] >= 15:
        combo_bonus = 8
        scores["catalyst"] = min(scores["catalyst"] + combo_bonus, 15)
        reasons.append(f"⚡ 낙폭과대+흑자전환 핵심 콤보 (+{combo_bonus}점 시너지)")
    # B) 낙폭과대 + 저평가 콤보 (보조 시너지, +5점)
    # tb_value: 하락장 +253% — 저PBR 낙폭과대 조합도 매우 강력
    elif scores["drawdown"] >= 15 and scores["value"] >= 12:
        combo_bonus = 5
        scores["catalyst"] = min(scores["catalyst"] + combo_bonus, 15)
        reasons.append(f"⚡ 낙폭과대+저평가 콤보 (+{combo_bonus}점 시너지)")
    # C) 기존 낙폭과대+촉매 콤보 (촉매 보너스)
    elif scores["drawdown"] >= 15 and scores["catalyst"] >= 5:
        combo_bonus = 3
        scores["catalyst"] = min(scores["catalyst"] + combo_bonus, 15)
        reasons.append(f"⚡ 낙폭과대+촉매 동시 포착 (+{combo_bonus}점 시너지)")

    total = sum(scores.values())

    # ── 최소 품질 게이트 검증 ──────────────────────────────────────────────────
    # 백테스트 결과: 낙폭 단독(tb_drawdown)은 AI랠리에서 -14% 실패
    # → fundamental/value/extreme_drawdown 중 하나는 반드시 존재해야 함
    qg = QUALITY_GATE_RULES
    quality_passed = (
        scores["fundamental"] >= qg["fundamental_min"]  # 흑자전환 or 매출30%+
        or scores["value"] >= qg["value_min"]            # PBR≤1 + 소형주
        or scores["drawdown"] >= qg["drawdown_min"]      # 52주 -50%+ 극심 낙폭
    )
    # 품질 게이트 미통과 시 점수를 낮춰 임계값 미달로 처리
    if not quality_passed:
        total = min(total, SCORE_THRESHOLD - 1)

    return total, scores, reasons


# ──────────────────────────────────────────────
# OpenAI 분석
# ──────────────────────────────────────────────

def _ai_analyze(stock_name: str, stock_code: str, score: float,
                reasons: list[str], fin: dict, uni: dict, price: dict) -> str:
    api_key = getattr(config, "GEMINI_API_KEY", "")
    if not api_key:
        return "DeepSeek API 키 미설정 — 자동 분석 불가"

    try:
        client = OpenAI()

        context_parts = [f"종목명: {stock_name} ({stock_code})"]
        context_parts.append(f"시가총액: {uni.get('market_cap', 'N/A')}억원")
        context_parts.append(f"PER: {uni.get('per', 'N/A')}배 / PBR: {uni.get('pbr', 'N/A')}배 / ROE: {uni.get('roe', 'N/A')}%")
        if fin.get("revenue_growth") is not None:
            context_parts.append(f"매출 성장률(YoY): {fin['revenue_growth']:.1f}%")
        if fin.get("op_growth") is not None:
            context_parts.append(f"영업이익 성장률(YoY): {fin['op_growth']:.1f}%")
        if fin.get("op_margin") is not None:
            context_parts.append(f"영업이익률: {fin['op_margin']:.1f}%")
        context_parts.append(f"현재가: {price.get('current_price', 'N/A')}원")
        context_parts.append(f"선정 점수: {score:.1f}/100점")
        context_parts.append(f"선정 사유: {', '.join(reasons)}")

        prompt = f"""다음은 텐버거(10배 성장) 가능성이 있는 종목으로 선별된 한국 주식입니다.

{chr(10).join(context_parts)}

이 종목이 왜 텐버거 후보로 선정되었는지 투자자 관점에서 3~5문장으로 분석해 주세요.
분석 내용: 성장 동력, 리스크 요인, 투자 포인트, 주의사항을 포함해 주세요.
형식: 한국어로 간결하게 작성 (이모지 사용 OK)"""

        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.7,
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        logger.warning(f"[AI분석] {stock_name}: {e}")
        return "AI 분석 실패: DeepSeek API 응답 오류"


# ──────────────────────────────────────────────
# 텔레그램 알림
# ──────────────────────────────────────────────

def _send_telegram_alert(candidates: list[dict], run_type: str):
    try:
        from notifier import send as _notify
        from telegram_stock_dedup import filter_new as _filter_new_alerts
        from telegram_stock_dedup import mark_sent as _mark_alert_sent
    except ImportError:
        logger.warning("[텐버거] notifier 모듈 없음 — 텔레그램 스킵")
        return

    # 텐버거 헌터 후보는 장중 여러 번 재계산되지만, 사용자는 같은 종목의
    # 반복 알림이 아니라 "새로 진입한 종목"만 원한다. 모든 텐버거 후보
    # 알림 경로가 같은 namespace를 공유해 오전/정오/오후 중복을 막는다.
    alert_namespace = "tenbagger_hunter_candidate"
    new_candidates = _filter_new_alerts(candidates, alert_namespace)
    if not new_candidates:
        logger.info("[텐버거] 신규 후보 없음 — 텔레그램 발송 스킵 (전체 %d종목 유지)", len(candidates))
        return

    type_label = {
        "morning":   "🌅 오전 9시 발굴",
        "noon":      "☀️ 정오 발굴",
        "afternoon": "🌇 오후 3시 발굴",
        "manual":    "🔍 수동 발굴",
    }.get(run_type, "📊 발굴")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        f"💎 텐버거 신규 후보 [{type_label}] — {now_str}\n"
        f"신규 {len(new_candidates)}종목 / 전체 {len(candidates)}종목\n"
        f"{'─'*30}"
    )

    try:
        _notify(header, key=f"tenbagger_new_header_{now_str}")
    except Exception as e:
        logger.warning(f"[텔레그램] 헤더 전송 실패: {e}")

    sent_candidates = []
    for i, c in enumerate(new_candidates, 1):
        lines = [
            f"[신규 {i}/{len(new_candidates)}] 📈 {c['stock_name']} ({c['stock_code']})",
            f"   현재가: {c['current_price']:,.0f}원 | 시총: {c['market_cap']:.0f}억",
            f"   점수: {c['total_score']:.1f}점",
        ]
        if c.get("revenue_growth") is not None:
            lines.append(f"   매출성장: {c['revenue_growth']:+.1f}% YoY")
        if c.get("op_growth") is not None:
            lines.append(f"   영업이익성장: {c['op_growth']:+.1f}% YoY")
        if c.get("per"):
            lines.append(f"   PER: {c['per']:.1f}배 / PBR: {c['pbr']:.1f}배")

        lines.append("\n📌 선정 사유:")
        for r in json.loads(c.get("reasons") or "[]"):
            lines.append(f"   • {r}")

        if c.get("ai_analysis"):
            lines.append("\n🤖 AI 분석:")
            lines.append(c["ai_analysis"])

        msg = "\n".join(lines)
        try:
            ok = _notify(msg, key=f"tenbagger_new_{c['stock_code']}")
            if ok:
                sent_candidates.append(c)
        except Exception as e:
            logger.warning(f"[텔레그램] {c['stock_name']} 전송 실패: {e}")
    if sent_candidates:
        _mark_alert_sent(alert_namespace, sent_candidates, payload_key="reasons")


# ──────────────────────────────────────────────
# 메인 발굴 함수
# ──────────────────────────────────────────────

def run_discovery(
    run_type: str = "auto",
    *,
    send_telegram: bool = True,
    generate_ai: bool = True,
) -> list[dict]:
    """
    텐버거 후보 발굴 실행.

    Args:
        run_type: 'morning' | 'noon' | 'afternoon' | 'manual'
        send_telegram: 텔레그램 신규 후보 알림 발송 여부
        generate_ai: OpenAI 심층 분석 생성 여부

    Returns:
        선정된 종목 list[dict]
    """
    init_tenbagger_tables()

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"[텐버거] 발굴 시작 ({run_type}) — {run_time}")

    conn = connect_primary_db(timeout=120, row_factory=sqlite3.Row, wal=True)

    try:
        candidates = _fetch_candidates(conn)
        logger.info(f"[텐버거] 기본 필터 통과 {len(candidates)}종목 분석 시작")

        results = []
        for uni in candidates:
            code = uni["stock_code"]
            try:
                price  = _fetch_price_data(conn, code)
                if not price:
                    continue  # 유동성 최소 기준 미달

                fin    = _fetch_financials(conn, code)
                supply = _fetch_supply(conn, code)
                extra  = _fetch_extra_signals(conn, code)
                passed_guardrails, guardrail_failures = _passes_tenbagger_guardrails(price, fin, supply, uni, extra)
                if not passed_guardrails:
                    logger.info("[텐버거] 제외 %s(%s): %s", uni["stock_name"], code, " / ".join(guardrail_failures))
                    continue

                total, detail, reasons = _score_stock(price, fin, supply, uni, extra)

                # Raw 10x labels made deep-drawdown grades look strong, but the
                # effect disappears after price artifacts and non-operating
                # spikes are removed. Keep only the independently validated
                # business-signal intersection as a non-trading research tag.
                contract_count = int(extra.get("original_contract_count_1y") or 0)
                op_growth = fin.get("op_growth")
                tvol = float(price.get("avg_tvol5_억") or 0)
                clean_signal_match = bool(
                    contract_count >= 2
                    and op_growth is not None
                    and op_growth >= 50
                    and tvol >= 10
                )
                grade = "R" if clean_signal_match else "-"
                if clean_signal_match:
                    reasons.insert(
                        0,
                        "🧪 지속형 연구신호 R — 원공시 수주 2건+·영업이익 50%+·거래대금 10억+ "
                        "(정밀도 목표 미달, 추천/자동매매 근거로 사용 금지)",
                    )

                if total >= SCORE_THRESHOLD:
                    results.append({
                        "evidence_grade": grade,
                        "stock_code":     code,
                        "stock_name":     uni["stock_name"],
                        "total_score":    total,
                        "score_detail":   detail,
                        "reasons":        reasons,
                        "current_price":  price.get("current_price"),
                        "market_cap":     uni.get("market_cap"),
                        "per":            uni.get("per"),
                        "pbr":            uni.get("pbr"),
                        "roe":            uni.get("roe"),
                        "revenue_growth": fin.get("revenue_growth"),
                        "op_growth":      fin.get("op_growth"),
                        "op_margin":      fin.get("op_margin"),
                        "inst_net_10d":   supply.get("inst_net_10d"),
                        "frn_net_10d":    supply.get("frn_net_10d"),
                        "_fin":           fin,
                        "_uni":           uni,
                        "_price":         price,
                    })
            except Exception as e:
                logger.debug(f"[텐버거] {code} 처리 오류: {e}")

        # The clean research tag is intentionally not a production ranking key.
        results.sort(key=lambda x: x["total_score"], reverse=True)
        results = results[:MAX_RESULTS]

        logger.info(f"[텐버거] {len(results)}종목 선정 (임계값 {SCORE_THRESHOLD}점)")
        try:
            from telegram_stock_dedup import load_sent_codes as _load_sent_alert_codes
            already_alerted_codes = _load_sent_alert_codes("tenbagger_hunter_candidate")
        except Exception:
            already_alerted_codes = set()

        # OpenAI 분석 및 DB 저장
        saved = []
        for r in results:
            if not generate_ai or r["stock_code"] in already_alerted_codes:
                ai_text = ""
            else:
                ai_text = _ai_analyze(
                    r["stock_name"], r["stock_code"],
                    r["total_score"], r["reasons"],
                    r["_fin"], r["_uni"], r["_price"]
                )
            r["ai_analysis"] = ai_text

            reasons_json = json.dumps(r["reasons"], ensure_ascii=False)
            detail_json  = json.dumps(r["score_detail"], ensure_ascii=False)

            conn.execute("""
                INSERT INTO tenbagger_results
                    (run_time, run_type, stock_code, stock_name, total_score,
                     score_detail, reasons, ai_analysis,
                     current_price, market_cap, per, pbr, roe,
                     revenue_growth, op_growth, op_margin,
                     inst_net_10d, frn_net_10d)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                run_time, run_type,
                r["stock_code"], r["stock_name"], r["total_score"],
                detail_json, reasons_json, ai_text,
                r["current_price"], r["market_cap"],
                r["per"], r["pbr"], r["roe"],
                r["revenue_growth"], r["op_growth"], r["op_margin"],
                r["inst_net_10d"], r["frn_net_10d"],
            ))

            saved.append({
                **{k: v for k, v in r.items() if not k.startswith("_")},
                "reasons":     reasons_json,
                "ai_analysis": ai_text,
            })

        conn.commit()

        # 텔레그램 전송 (결과 있을 때만)
        if saved and send_telegram:
            _send_telegram_alert(saved, run_type)
            # 전송 완료 표시
            codes = [s["stock_code"] for s in saved]
            placeholders = ",".join("?" * len(codes))
            conn.execute(
                f"UPDATE tenbagger_results SET telegram_sent=1 WHERE run_time=? AND stock_code IN ({placeholders})",
                [run_time] + codes
            )
            conn.commit()
        elif saved:
            logger.info("[텐버거] 알림 비활성화 모드 — 결과만 저장 (%d종목)", len(saved))
        else:
            logger.info("[텐버거] 선정 종목 없음 — 텔레그램 스킵")

        return saved

    finally:
        conn.close()


# ──────────────────────────────────────────────
# 백테스트 기반 매수/매도 신호 (2026-06-18 추가)
# 검증: 2019-2024 6년, +105% / KOSPI 5.43x / MDD -33.7%
# ──────────────────────────────────────────────

def check_buy_signal(total_score: float, price: dict, uni: dict) -> dict:
    """백테스트 검증 매수 조건 체크.

    조건:
      1. 엔진 총점 ≥ 50
      2. 52주 고가 대비 -30%~-85% 낙폭과대 구간
      3. 거래량 비율 ≥ 1.5x (20일 평균 대비)
      4. 시총 5,000억 이하 (소형주 집중)
    """
    reasons = []
    passed = True

    # 조건 1: 점수
    if total_score < BUY_PARAMS["min_score"]:
        return {"signal": False, "reason": f"점수 미달 ({total_score:.0f} < {BUY_PARAMS['min_score']})", "conditions": {}}

    reasons.append(f"✅ 점수 {total_score:.0f}점 (기준 {BUY_PARAMS['min_score']})")

    # 조건 2: 낙폭과대 구간 (-30% ~ -85%)
    from_high = price.get("from_high_pct", 0)
    in_zone = (-85 <= from_high <= -30) if BUY_PARAMS["drawdown_req"] else True
    if not in_zone:
        passed = False
        reasons.append(f"❌ 낙폭 구간 미해당 ({from_high:.1f}% — 목표: -30~-85%)")
    else:
        reasons.append(f"✅ 낙폭과대 구간 ({from_high:.1f}%)")

    # 조건 3: 거래량 급증
    vol_ratio = price.get("vol_ratio", 0)
    vol_ok = vol_ratio >= BUY_PARAMS["vol_req"]
    if not vol_ok:
        passed = False
        reasons.append(f"❌ 거래량 미달 ({vol_ratio:.1f}x < {BUY_PARAMS['vol_req']}x)")
    else:
        reasons.append(f"✅ 거래량 {vol_ratio:.1f}x (기준 {BUY_PARAMS['vol_req']}x)")

    # 조건 4: 시총 (선택적)
    mkcap = uni.get("market_cap", 0) or 0
    cap_ok = (mkcap <= BUY_PARAMS["max_cap_억"]) if BUY_PARAMS["max_cap_억"] > 0 else True
    if not cap_ok:
        passed = False
        reasons.append(f"❌ 시총 초과 ({mkcap:.0f}억 > {BUY_PARAMS['max_cap_억']}억)")
    else:
        reasons.append(f"✅ 시총 {mkcap:.0f}억")

    strength = "강" if (total_score >= 60 and in_zone and vol_ratio >= 2.0) else \
               "중" if (total_score >= 50 and in_zone and vol_ok) else "약"

    failed = [r.replace("❌ ", "") for r in reasons if r.startswith("❌")]
    return {
        "signal": passed,
        "strength": strength,
        "reasons": reasons,
        "failed_conditions": failed,
        "conditions": {
            "score_ok":    total_score >= BUY_PARAMS["min_score"],
            "zone_ok":     in_zone,
            "vol_ok":      vol_ok,
            "cap_ok":      cap_ok,
            "from_high":   from_high,
            "vol_ratio":   vol_ratio,
            "total_score": total_score,
        },
    }


def get_market_regime() -> str:
    """현재 KOSPI 레짐 판단.

    반환값: STRONG_BULL / BULL / NEUTRAL / BEAR
    판단 기준: KOSPI vs MA60/MA120 + 최근 20일/60일 수익률
    """
    try:
        conn = connect_primary_db(timeout=30)
        rows = conn.execute(
            "SELECT date, close FROM price_history "
            "WHERE stock_code='^KS11' AND close>0 ORDER BY date DESC LIMIT 130"
        ).fetchall()
        conn.close()
        if len(rows) < 130:
            return "NEUTRAL"
        closes = [float(r[1]) for r in reversed(rows)]
        cur    = closes[-1]
        ma60   = sum(closes[-60:]) / 60
        ma120  = sum(closes[-120:]) / 120
        ret20  = (cur / closes[-21] - 1) * 100 if len(closes) >= 21 else 0
        ret60  = (cur / closes[-61] - 1) * 100 if len(closes) >= 61 else 0
        if cur > ma60 > ma120:
            if ret60 > 20 or ret20 > 8:
                return "STRONG_BULL"
            if ret20 > 2:
                return "BULL"
            return "NEUTRAL"
        elif cur < ma120 and (ret20 < -3 or ret60 < -10):
            return "BEAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"


def check_sell_signal(entry_price: float, current_price: float,
                      entry_date: str, total_score: float = None,
                      regime: str = None) -> dict:
    """레짐별 동적 매도 조건 체크.

    regime 미지정 시 get_market_regime()으로 자동 감지.

    레짐별 파라미터:
      STRONG_BULL: TP=+150%, SL=-15%, hold=365일 (강세장에서 조기 익절 방지)
      BULL:        TP=+90%,  SL=-15%, hold=250일
      NEUTRAL:     TP=+70%,  SL=-15%, hold=200일
      BEAR:        TP=+50%,  SL=-12%, hold=150일
    """
    if not entry_price or entry_price <= 0:
        return {"signal": False, "reason": "매수가 정보 없음"}

    if regime is None:
        regime = get_market_regime()
    rp = REGIME_SELL_PARAMS.get(regime, REGIME_SELL_PARAMS["NEUTRAL"])

    ret = (current_price - entry_price) / entry_price

    # 손절
    if ret <= rp["stop_loss"]:
        return {
            "signal": True,
            "type": "STOP_LOSS",
            "reason": f"손절 ({ret*100:+.1f}% ≤ {rp['stop_loss']*100:.0f}%, 레짐:{regime})",
            "ret_pct": round(ret * 100, 1),
            "regime": regime,
        }

    # 익절
    if ret >= rp["take_profit"]:
        return {
            "signal": True,
            "type": "TAKE_PROFIT",
            "reason": f"익절 ({ret*100:+.1f}% ≥ +{rp['take_profit']*100:.0f}%, 레짐:{regime})",
            "ret_pct": round(ret * 100, 1),
            "regime": regime,
        }

    # 기간 초과
    if entry_date:
        try:
            hold_days = (datetime.now() - datetime.strptime(entry_date[:10], "%Y-%m-%d")).days
            if hold_days >= rp["max_hold_days"]:
                return {
                    "signal": True,
                    "type": "TIME_STOP",
                    "reason": f"보유기간 초과 ({hold_days}일 ≥ {rp['max_hold_days']}일, 레짐:{regime})",
                    "ret_pct": round(ret * 100, 1),
                    "hold_days": hold_days,
                    "regime": regime,
                }
        except Exception:
            pass

    # 점수 급락 (펀더멘털 악화)
    if total_score is not None and total_score < SELL_PARAMS["score_exit"]:
        return {
            "signal": True,
            "type": "SCORE_EXIT",
            "reason": f"펀더멘털 악화 (점수 {total_score:.0f} < {SELL_PARAMS['score_exit']})",
            "ret_pct": round(ret * 100, 1),
            "regime": regime,
        }

    return {
        "signal": False,
        "reason": "보유 유지",
        "ret_pct": round(ret * 100, 1),
        "regime": regime,
        "hold_days": (datetime.now() - datetime.strptime(entry_date[:10], "%Y-%m-%d")).days
        if entry_date else None,
    }


_ACTION_SIGNALS_CACHE: dict = {}


def _action_signals_ttl_secs() -> float:
    now = datetime.now()
    if now.weekday() < 5 and 9 <= now.hour < 15 or (now.hour == 15 and now.minute <= 30):
        return 300.0  # 장중 5분
    return 1800.0  # 장외 30분


def get_action_signals(limit: int = 30) -> list[dict]:
    """현재 시점 매수 신호 종목 반환 (백테스트 검증 파라미터 적용).

    tenbagger_results 최신 런에서 score ≥ 50인 종목 중
    buy_signal 조건을 추가로 충족하는 종목만 반환.

    종목당 가격/재무/수급/특수신호를 개별 재조회하는 구조라(N+1) 100종목 기준
    수십 초가 걸릴 수 있어 캐싱한다(장중 5분/장외 30분 TTL).
    """
    cached = _ACTION_SIGNALS_CACHE.get(limit)
    if cached and (time.time() - cached["at"]) < _action_signals_ttl_secs():
        return cached["data"]

    conn = connect_primary_db(timeout=60, row_factory=sqlite3.Row)
    try:
        # 최신 run_time 가져오기
        row = conn.execute(
            "SELECT run_time FROM tenbagger_results ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return []
        run_time = row["run_time"]

        # 해당 런의 결과
        rows = conn.execute("""
            SELECT tr.*, su.market_cap, su.market, su.sector_large
            FROM tenbagger_results tr
            LEFT JOIN stock_universe su ON su.stock_code = tr.stock_code
            WHERE tr.run_time = ?
            ORDER BY tr.total_score DESC
            LIMIT 100
        """, (run_time,)).fetchall()

        results = []
        for r in rows:
            code = r["stock_code"]
            uni  = {"market_cap": r["market_cap"], "market": r["market"],
                    "sector_large": r["sector_large"]}

            # 최신 가격 데이터 재조회 (run_time 이후 변동 반영)
            price = _fetch_price_data(conn, code)
            if not price:
                continue
            fin = _fetch_financials(conn, code)
            supply = _fetch_supply(conn, code)
            extra = _fetch_extra_signals(conn, code)
            passed_guardrails, guardrail_failures = _passes_tenbagger_guardrails(
                price, fin, supply, uni, extra
            )
            if not passed_guardrails:
                continue

            total = r["total_score"]
            buy   = check_buy_signal(total, price, uni)

            results.append({
                "stock_code":   code,
                "stock_name":   r["stock_name"],
                "total_score":  total,
                "current_price": price.get("current_price"),
                "from_high_pct": price.get("from_high_pct"),
                "from_low_pct":  price.get("from_low_pct"),
                "vol_ratio":     price.get("vol_ratio"),
                "market_cap":    r["market_cap"],
                "per":           r["per"],
                "pbr":           r["pbr"],
                "buy_signal":    buy["signal"],
                "buy_strength":  buy.get("strength"),
                "buy_reasons":   buy.get("reasons", []),
                "buy_conditions": buy.get("conditions", {}),
                "buy_failed":    buy.get("failed_conditions", []),
                "guardrail_failures": guardrail_failures,
                "reasons":       r["reasons"],
                "run_time":      run_time,
            })

        # 매수신호 종목 우선 정렬
        results.sort(key=lambda x: (x["buy_signal"], x["total_score"]), reverse=True)
        final = results[:limit]
        _ACTION_SIGNALS_CACHE[limit] = {"data": final, "at": time.time()}
        return final

    finally:
        conn.close()


# ──────────────────────────────────────────────
# CLI 직접 실행
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    run_type = sys.argv[1] if len(sys.argv) > 1 else "manual"
    results = run_discovery(run_type)
    print(f"\n선정 종목 {len(results)}개:")
    for r in results:
        print(f"  {r['stock_name']} ({r['stock_code']}) — {r['total_score']:.1f}점")
