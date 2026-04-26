"""
tenbagger_engine.py — 텐버거 종목 발굴 엔진

DB에 저장된 재무·가격·수급 데이터를 분석해 10배 성장 가능성이 있는
종목을 자동으로 발굴하고, OpenAI 분석 의견과 함께 텔레그램으로 알림.

발굴 기준 (6축 스코어링):
  1. 매출 성장    (20점) — 최근 연간 매출 YoY ≥ 15%
  2. 영업이익 성장 (20점) — 최근 연간 영업이익 YoY ≥ 20%
  3. 수익성       (15점) — ROE ≥ 10%, 영업이익률 ≥ 8%
  4. 기술적 추세  (15점) — MA20 > MA60 > MA120, 52주 고점 근접
  5. 수급 모멘텀  (15점) — 최근 10일 기관/외국인 순매수
  6. 밸류에이션   (15점) — PER ≤ 30, PBR ≤ 5

임계값: 총점 ≥ 55점 이상 종목만 후보로 선정
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, date
from typing import Optional

import config

logger = logging.getLogger(__name__)

DB_PATH = "stock.db"
SCORE_THRESHOLD = 55       # 선정 최소 점수
MAX_RESULTS     = 20       # 회차당 최대 결과 수


# ──────────────────────────────────────────────
# DB 초기화
# ──────────────────────────────────────────────

def init_tenbagger_tables():
    conn = sqlite3.connect(DB_PATH)
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
          AND  su.market_cap BETWEEN 500 AND 30000   -- 500억~3조 (억원)
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

    closes = [r[0] for r in rows]   # 최신→오래된 순
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

    return {
        "current_price": current,
        "ma20": ma20,
        "ma60": ma60,
        "ma120": ma120,
        "ma200": ma200,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "avg_tvol5_억": avg_tvol5,
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

    r0, r1 = dict(rows[0]), dict(rows[1])

    revenue_growth = safe_pct(r0["revenue"], r1["revenue"])
    op_growth      = safe_pct(r0["operating_profit"], r1["operating_profit"]) if r0["operating_profit"] and r1["operating_profit"] else None
    op_margin      = (r0["operating_profit"] / r0["revenue"] * 100) if r0["operating_profit"] and r0["revenue"] > 0 else None
    roe_calc       = None
    if r0["net_income"] and r0["total_equity"] and r0["total_equity"] > 0:
        roe_calc = r0["net_income"] / r0["total_equity"] * 100

    return {
        "revenue_growth": revenue_growth,
        "op_growth": op_growth,
        "op_margin": op_margin,
        "roe_calc": roe_calc,
        "latest_revenue": r0["revenue"],
        "latest_op": r0["operating_profit"],
    }


def _fetch_supply(conn, stock_code: str) -> dict:
    """최근 10영업일 기관/외국인 순매수 합계 (억원)."""
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
        return {"inst_net_10d": 0, "frn_net_10d": 0}

    inst_total = 0.0
    frn_total  = 0.0
    for r in rows:
        inst_amt, frn_amt, inst_qty, frn_qty, close = r
        # amt는 백만원 단위 → 억원 변환
        if inst_amt and inst_amt != 0:
            inst_total += inst_amt / 100.0
        elif inst_qty and close:
            inst_total += inst_qty * close / 1e8
        if frn_amt and frn_amt != 0:
            frn_total += frn_amt / 100.0
        elif frn_qty and close:
            frn_total += frn_qty * close / 1e8

    return {"inst_net_10d": round(inst_total), "frn_net_10d": round(frn_total)}


# ──────────────────────────────────────────────
# 스코어링 엔진
# ──────────────────────────────────────────────

def _score_stock(price: dict, fin: dict, supply: dict, uni: dict) -> tuple[float, dict, list[str]]:
    """6축 스코어 계산 → (total, detail, reasons)"""
    scores = {
        "growth_rev": 0.0,   # 매출 성장 (20점)
        "growth_op":  0.0,   # 영업이익 성장 (20점)
        "profit":     0.0,   # 수익성 (15점)
        "trend":      0.0,   # 기술적 추세 (15점)
        "supply":     0.0,   # 수급 (15점)
        "value":      0.0,   # 밸류에이션 (15점)
    }
    reasons = []

    # ── 1. 매출 성장 (20점) ──
    rev_g = fin.get("revenue_growth")
    if rev_g is not None:
        if rev_g >= 40:   scores["growth_rev"] = 20; reasons.append(f"매출 YoY +{rev_g:.0f}% 고성장")
        elif rev_g >= 25: scores["growth_rev"] = 16; reasons.append(f"매출 YoY +{rev_g:.0f}% 성장")
        elif rev_g >= 15: scores["growth_rev"] = 12; reasons.append(f"매출 YoY +{rev_g:.0f}% 꾸준 성장")
        elif rev_g >= 5:  scores["growth_rev"] = 6
        elif rev_g < 0:   scores["growth_rev"] = -5  # 패널티

    # ── 2. 영업이익 성장 (20점) ──
    op_g = fin.get("op_growth")
    if op_g is not None:
        if op_g >= 50:    scores["growth_op"] = 20; reasons.append(f"영업이익 YoY +{op_g:.0f}% 급증")
        elif op_g >= 30:  scores["growth_op"] = 16; reasons.append(f"영업이익 YoY +{op_g:.0f}% 성장")
        elif op_g >= 15:  scores["growth_op"] = 10; reasons.append(f"영업이익 YoY +{op_g:.0f}% 개선")
        elif op_g >= 0:   scores["growth_op"] = 5
        elif op_g < -20:  scores["growth_op"] = -5

    # ── 3. 수익성 (15점) ──
    roe = uni.get("roe") or fin.get("roe_calc")
    op_margin = fin.get("op_margin")

    roe_score = 0
    if roe is not None:
        if roe >= 25:   roe_score = 8; reasons.append(f"ROE {roe:.1f}% 우수")
        elif roe >= 15: roe_score = 6; reasons.append(f"ROE {roe:.1f}%")
        elif roe >= 10: roe_score = 4
        elif roe < 0:   roe_score = -3

    margin_score = 0
    if op_margin is not None:
        if op_margin >= 20:   margin_score = 7; reasons.append(f"영업이익률 {op_margin:.1f}% 고마진")
        elif op_margin >= 12: margin_score = 5; reasons.append(f"영업이익률 {op_margin:.1f}%")
        elif op_margin >= 8:  margin_score = 3
        elif op_margin < 3:   margin_score = -2

    scores["profit"] = min(15, roe_score + margin_score)

    # ── 4. 기술적 추세 (15점) ──
    cur   = price.get("current_price")
    ma20  = price.get("ma20")
    ma60  = price.get("ma60")
    ma120 = price.get("ma120")
    h52   = price.get("high_52w")
    tvol  = price.get("avg_tvol5_억", 0)

    if cur and ma20 and ma60 and ma120:
        trend_score = 0
        if cur > ma20 > ma60 > ma120:
            trend_score += 8; reasons.append("정배열 (MA20>MA60>MA120) 상승 추세")
        elif cur > ma60 > ma120:
            trend_score += 5
        elif cur > ma120:
            trend_score += 2

        if h52 and cur >= h52 * 0.80:
            trend_score += 5; reasons.append(f"52주 고점 {(cur/h52*100):.0f}% 수준 (신고가 근접)")
        elif h52 and cur >= h52 * 0.65:
            trend_score += 2

        if tvol >= 50:
            trend_score += 2; reasons.append(f"거래대금 {tvol:.0f}억 활발")
        elif tvol < 5:
            trend_score -= 3  # 유동성 부족 패널티

        scores["trend"] = max(0, min(15, trend_score))

    # ── 5. 수급 모멘텀 (15점) ──
    inst = supply.get("inst_net_10d", 0)
    frn  = supply.get("frn_net_10d", 0)

    sup_score = 0
    if inst > 0 and frn > 0:
        sup_score = 15
        reasons.append(f"기관+외국인 동반매수 (기관{inst:+.0f}억/외국인{frn:+.0f}억)")
    elif inst > 50:
        sup_score = 10; reasons.append(f"기관 순매수 {inst:+.0f}억 집중매집")
    elif inst > 0:
        sup_score = 6; reasons.append(f"기관 순매수 {inst:+.0f}억")
    elif frn > 50:
        sup_score = 10; reasons.append(f"외국인 순매수 {frn:+.0f}억 집중매집")
    elif frn > 0:
        sup_score = 6; reasons.append(f"외국인 순매수 {frn:+.0f}억")
    elif inst < -100 or frn < -100:
        sup_score = -5  # 대규모 매도 패널티

    scores["supply"] = max(0, min(15, sup_score))

    # ── 6. 밸류에이션 (15점) ──
    per = uni.get("per")
    pbr = uni.get("pbr")

    val_score = 0
    if per is not None and per > 0:
        if per <= 10:   val_score += 8; reasons.append(f"PER {per:.1f}배 저평가")
        elif per <= 20: val_score += 6; reasons.append(f"PER {per:.1f}배 적정")
        elif per <= 30: val_score += 3
        else:           val_score -= 2  # 고PER 패널티

    if pbr is not None and pbr > 0:
        if pbr <= 1.5:   val_score += 7; reasons.append(f"PBR {pbr:.1f}배 자산 저평가")
        elif pbr <= 3.0: val_score += 4; reasons.append(f"PBR {pbr:.1f}배")
        elif pbr <= 5.0: val_score += 1
        else:            val_score -= 1

    scores["value"] = max(0, min(15, val_score))

    total = sum(scores.values())
    return total, scores, reasons


# ──────────────────────────────────────────────
# OpenAI 분석
# ──────────────────────────────────────────────

def _ai_analyze(stock_name: str, stock_code: str, score: float,
                reasons: list[str], fin: dict, uni: dict, price: dict) -> str:
    api_key = getattr(config, "OPENAI_API_KEY", "")
    if not api_key:
        return "OpenAI API 키 미설정 — 자동 분석 불가"

    try:
        import openai
        client = openai.OpenAI(api_key=api_key)

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
        return f"AI 분석 실패: {e}"


# ──────────────────────────────────────────────
# 텔레그램 알림
# ──────────────────────────────────────────────

def _send_telegram_alert(candidates: list[dict], run_type: str):
    try:
        from notifier import send as _notify
    except ImportError:
        logger.warning("[텐버거] notifier 모듈 없음 — 텔레그램 스킵")
        return

    type_label = {
        "morning":   "🌅 오전 9시 발굴",
        "noon":      "☀️ 정오 발굴",
        "afternoon": "🌇 오후 3시 발굴",
        "manual":    "🔍 수동 발굴",
    }.get(run_type, "📊 발굴")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"💎 텐버거 종목 발굴 [{type_label}] — {now_str}\n총 {len(candidates)}종목 선정\n{'─'*30}"

    try:
        _notify(header, key=f"tenbagger_header_{now_str}")
    except Exception as e:
        logger.warning(f"[텔레그램] 헤더 전송 실패: {e}")

    for i, c in enumerate(candidates, 1):
        lines = [
            f"[{i}/{len(candidates)}] 📈 {c['stock_name']} ({c['stock_code']})",
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
            _notify(msg, key=f"tenbagger_{c['stock_code']}_{now_str[:13]}")
        except Exception as e:
            logger.warning(f"[텔레그램] {c['stock_name']} 전송 실패: {e}")


# ──────────────────────────────────────────────
# 메인 발굴 함수
# ──────────────────────────────────────────────

def run_discovery(run_type: str = "auto") -> list[dict]:
    """
    텐버거 후보 발굴 실행.

    Args:
        run_type: 'morning' | 'noon' | 'afternoon' | 'manual'

    Returns:
        선정된 종목 list[dict]
    """
    init_tenbagger_tables()

    run_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"[텐버거] 발굴 시작 ({run_type}) — {run_time}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        candidates = _fetch_candidates(conn)
        logger.info(f"[텐버거] 기본 필터 통과 {len(candidates)}종목 분석 시작")

        results = []
        for uni in candidates:
            code = uni["stock_code"]
            try:
                price  = _fetch_price_data(conn, code)
                if not price or price.get("avg_tvol5_억", 0) < 3:
                    continue  # 유동성 최소 기준 미달

                fin    = _fetch_financials(conn, code)
                supply = _fetch_supply(conn, code)

                total, detail, reasons = _score_stock(price, fin, supply, uni)

                if total >= SCORE_THRESHOLD:
                    results.append({
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

        # 점수 내림차순 정렬, 상위 MAX_RESULTS
        results.sort(key=lambda x: x["total_score"], reverse=True)
        results = results[:MAX_RESULTS]

        logger.info(f"[텐버거] {len(results)}종목 선정 (임계값 {SCORE_THRESHOLD}점)")

        # OpenAI 분석 및 DB 저장
        saved = []
        for r in results:
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
        if saved:
            _send_telegram_alert(saved, run_type)
            # 전송 완료 표시
            codes = [s["stock_code"] for s in saved]
            placeholders = ",".join("?" * len(codes))
            conn.execute(
                f"UPDATE tenbagger_results SET telegram_sent=1 WHERE run_time=? AND stock_code IN ({placeholders})",
                [run_time] + codes
            )
            conn.commit()
        else:
            logger.info("[텐버거] 선정 종목 없음 — 텔레그램 스킵")

        return saved

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
