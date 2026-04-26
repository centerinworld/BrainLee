"""
tenbagger_ai.py — 텐배거 종목 AI 검증 모듈

Claude claude-sonnet-4-6 API를 사용해 다음을 분석:
  1. 메가트렌드 부합 여부 (EV/AI/방산/K-수출/바이오)
  2. 가짜 펌핑 리스크 (CB/BW 과잉, 실적 없는 내러티브, 데드캣 바운스)
  3. 텐배거 잠재 스코어 (0~100)

캐시: {stock_code}_{date} 단위로 메모리 캐싱 (프로세스 재시작 시 초기화)
폴백: ANTHROPIC_API_KEY 미설정 시 정량 지표만으로 판단
"""

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"

# ── 캐시 ────────────────────────────────────────────────────────
_cache: dict = {}          # key: "{sc}_{date}" → result dict
_CACHE_TTL = 86400         # 24시간


def _cache_get(key: str) -> Optional[dict]:
    entry = _cache.get(key)
    if entry and (time.time() - entry["at"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(key: str, data: dict):
    _cache[key] = {"data": data, "at": time.time()}


# ── 정량 폴백: API 없을 때 순수 수치 기반 판단 ────────────────────
def _quant_only_verdict(
    stock_code: str,
    stock_name: str,
    curr_price: float,
    ma200: Optional[float],
    high_250d: Optional[float],
    vol_ratio: float,          # 당일 거래량 / 60일 평균
    op_profit: Optional[float],
    rev_yoy_pct: Optional[float],
    mktcap: Optional[float],   # 원 단위
    sector: str,
) -> dict:
    """ANTHROPIC_API_KEY 없을 때 수치만으로 텐배거 잠재 점수 산출."""
    score = 0
    reasons = []
    warnings = []

    # 메가트렌드 섹터
    megatrend_sectors = {
        "반도체": 20, "전기차": 20, "배터리": 20, "방위산업": 20, "방산": 20,
        "AI": 20, "인공지능": 20, "바이오": 15, "제약": 15, "수출": 10,
        "K-방산": 20, "자동차": 10, "로봇": 15, "우주항공": 20, "원전": 15,
    }
    is_megatrend = False
    for key, pts in megatrend_sectors.items():
        if key in (sector or ""):
            score += pts
            is_megatrend = True
            reasons.append(f"메가트렌드 섹터: {sector}")
            break

    # MA200 위
    if ma200 and curr_price > ma200:
        score += 15
        reasons.append(f"MA200 상방 (현재:{curr_price:,.0f} / MA200:{ma200:,.0f})")
    else:
        warnings.append("MA200 하방 — 추세 미형성")

    # 52주 신고가 근접
    if high_250d and curr_price >= high_250d * 0.88:
        score += 15
        reasons.append(f"52주 고가 {(curr_price/high_250d*100):.0f}% 근접")
    else:
        warnings.append("52주 고가권 미달")

    # 거래량 폭발
    if vol_ratio >= 3.0:
        score += 10
        reasons.append(f"거래량 {vol_ratio:.1f}배 폭발")

    # 실적
    if op_profit and op_profit > 0:
        score += 10
        reasons.append("영업이익 흑자")
    elif rev_yoy_pct and rev_yoy_pct >= 30:
        score += 8
        reasons.append(f"매출 YoY +{rev_yoy_pct:.0f}% 고성장")
    else:
        warnings.append("실적 미확인 또는 적자")

    # 시총 제한 (소형주 선호)
    if mktcap and mktcap <= 1_500_000_000_000:
        score += 10
        reasons.append(f"시총 {mktcap/1e8:.0f}억원 (소형)")
    elif mktcap and mktcap <= 5_000_000_000_000:
        score += 5
        reasons.append(f"시총 {mktcap/1e8:.0f}억원 (중형)")
    else:
        warnings.append("대형주 — 텐배거 확률 낮음")

    score = min(100, score)
    verdict = (
        "strong_buy" if score >= 70
        else "buy" if score >= 50
        else "watch" if score >= 35
        else "avoid"
    )

    return {
        "verdict":        verdict,
        "tenbagger_score": score,
        "is_megatrend":   is_megatrend,
        "is_fake_risk":   len(warnings) >= 3,
        "reasons":        reasons,
        "warnings":       warnings,
        "ai_summary":     f"[정량분석] {stock_name} 텐배거 잠재점수 {score}/100 — {verdict}",
        "source":         "quant_fallback",
    }


# ── 재무 조회 헬퍼 ────────────────────────────────────────────────
def _get_fundamentals(stock_code: str, as_of_date: str) -> dict:
    """stock.db에서 재무 기초 데이터 조회."""
    result = {
        "op_profit": None, "revenue": None, "revenue_prev": None,
        "mktcap": None, "sector": "기타", "stock_name": stock_code,
        "curr_price": None, "ma200": None, "high_250d": None,
        "vol_today": None, "vol60_avg": None,
    }
    try:
        conn = sqlite3.connect(DB_PATH, timeout=15)

        # 종목명 + 섹터 + 시총
        row = conn.execute(
            "SELECT stock_name, sector_large, market_cap FROM stock_universe WHERE stock_code=?",
            (stock_code,)
        ).fetchone()
        if row:
            result["stock_name"] = row[0] or stock_code
            result["sector"]     = row[1] or "기타"
            result["mktcap"]     = row[2]

        # 재무: 최근 영업이익
        fin = conn.execute("""
            SELECT operating_profit, revenue FROM financial_data
            WHERE stock_code=? AND year*100+quarter <= ?
            ORDER BY year DESC, quarter DESC LIMIT 1
        """, (stock_code, int(as_of_date[:4]) * 100 + 4)).fetchone()
        if fin:
            result["op_profit"] = fin[0]
            result["revenue"]   = fin[1]

        # 전년 매출 (YoY 계산용)
        fin_prev = conn.execute("""
            SELECT revenue FROM financial_data
            WHERE stock_code=? AND year*100+quarter <= ?
            ORDER BY year DESC, quarter DESC LIMIT 1
        """, (stock_code, (int(as_of_date[:4]) - 1) * 100 + 4)).fetchone()
        if fin_prev:
            result["revenue_prev"] = fin_prev[0]

        # 주가 + 이동평균 + 고점
        warmup = (datetime.strptime(as_of_date, "%Y-%m-%d")
                  - timedelta(days=300)).strftime("%Y-%m-%d")
        price_rows = conn.execute("""
            SELECT date, close, volume FROM price_history
            WHERE stock_code=? AND date>=? AND date<=? AND close>0
            ORDER BY date ASC
        """, (stock_code, warmup, as_of_date)).fetchall()

        if price_rows:
            prices  = [float(r[1]) for r in price_rows]
            volumes = [float(r[2]) for r in price_rows if r[2]]
            result["curr_price"] = prices[-1]
            result["high_250d"]  = max(prices[-250:]) if len(prices) >= 250 else max(prices)
            if len(prices) >= 200:
                result["ma200"] = sum(prices[-200:]) / 200
            result["vol_today"] = volumes[-1] if volumes else 0
            if len(volumes) >= 60:
                result["vol60_avg"] = sum(volumes[-60:]) / 60

        conn.close()
    except Exception as e:
        logger.warning("tenbagger_ai _get_fundamentals error: %s", e)
    return result


# ── Claude API 호출 ─────────────────────────────────────────────
def _call_claude(stock_name: str, sector: str, fundamentals: dict) -> dict:
    """claude-sonnet-4-6 API로 텐배거 잠재성 분석."""
    try:
        import anthropic
    except ImportError:
        raise RuntimeError("anthropic 패키지 미설치: pip install anthropic")

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 미설정")

    client = anthropic.Anthropic(api_key=api_key)

    op = fundamentals.get("op_profit")
    rev = fundamentals.get("revenue")
    rev_prev = fundamentals.get("revenue_prev")
    mktcap = fundamentals.get("mktcap")
    curr = fundamentals.get("curr_price")
    ma200 = fundamentals.get("ma200")
    high_250 = fundamentals.get("high_250d")
    vol_today = fundamentals.get("vol_today", 0)
    vol60 = fundamentals.get("vol60_avg", 1)
    vol_ratio = (vol_today / vol60) if vol60 and vol60 > 0 else 0

    rev_yoy = None
    if rev and rev_prev and rev_prev > 0:
        rev_yoy = (rev - rev_prev) / rev_prev * 100

    prompt = f"""당신은 한국 주식 시장 전문 애널리스트입니다.
다음 종목이 향후 3~5년 내 텐배거(10배 상승) 잠재력이 있는지 분석해주세요.

종목명: {stock_name}
섹터: {sector}
현재가: {f'{curr:,.0f}원' if curr else '불명'}
MA200 대비: {f'{curr/ma200*100:.1f}%' if curr and ma200 else '불명'}
52주 고가 대비: {f'{curr/high_250*100:.1f}%' if curr and high_250 else '불명'}
거래량 비율 (60일평균 대비): {f'{vol_ratio:.1f}배' if vol_ratio else '불명'}
최근 영업이익: {f'{op/1e8:.0f}억원' if op else '적자/불명'}
최근 매출: {f'{rev/1e8:.0f}억원' if rev else '불명'}
매출 YoY: {f'+{rev_yoy:.0f}%' if rev_yoy and rev_yoy > 0 else (f'{rev_yoy:.0f}%' if rev_yoy else '불명')}
시가총액: {f'{mktcap/1e8:.0f}억원' if mktcap else '불명'}

분석 기준:
1. 메가트렌드 부합: EV/배터리/AI/반도체/방산/K-수출/바이오/원전/로봇/우주항공
2. 가짜 리스크: CB/BW 과잉 발행(부채비율 200%+), 실적 없는 테마, 데드캣 바운스
3. 구조적 EPS 턴어라운드: 영업이익 흑자전환 또는 매출 30%+ 고성장
4. 독점적 기술/포지션: 글로벌 시장점유율, 특허, 고객 고착도

반드시 아래 JSON 형식으로만 응답하세요 (마크다운 코드블록 없이):
{{
  "verdict": "strong_buy|buy|watch|avoid",
  "tenbagger_score": 0~100,
  "is_megatrend": true|false,
  "is_fake_risk": true|false,
  "reasons": ["긍정 근거1", "긍정 근거2"],
  "warnings": ["위험 요소1", "위험 요소2"],
  "ai_summary": "한 문장 종합 판단"
}}"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )

    text = message.content[0].text.strip()
    # JSON 파싱
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    parsed = json.loads(text)
    parsed["source"] = "claude_api"
    return parsed


# ══════════════════════════════════════════════════════════════
#  공개 API
# ══════════════════════════════════════════════════════════════
def analyze_tenbagger_potential(
    stock_code: str,
    as_of_date: Optional[str] = None,
    force_refresh: bool = False,
) -> dict:
    """
    텐배거 잠재성 분석. 캐시 우선, API 폴백.

    Returns:
        {
          verdict:        "strong_buy" | "buy" | "watch" | "avoid"
          tenbagger_score: int (0~100)
          is_megatrend:   bool
          is_fake_risk:   bool
          reasons:        list[str]
          warnings:       list[str]
          ai_summary:     str
          source:         "claude_api" | "quant_fallback"
        }
    """
    if as_of_date is None:
        as_of_date = datetime.today().strftime("%Y-%m-%d")

    cache_key = f"{stock_code}_{as_of_date}"
    if not force_refresh:
        cached = _cache_get(cache_key)
        if cached:
            return cached

    fund = _get_fundamentals(stock_code, as_of_date)
    stock_name  = fund["stock_name"]
    sector      = fund["sector"]
    curr        = fund["curr_price"] or 0
    ma200       = fund["ma200"]
    high_250    = fund["high_250d"]
    vol_today   = fund["vol_today"] or 0
    vol60       = fund["vol60_avg"] or 1
    vol_ratio   = vol_today / vol60 if vol60 > 0 else 0
    op_profit   = fund["op_profit"]
    rev         = fund["revenue"]
    rev_prev    = fund["revenue_prev"]
    mktcap      = fund["mktcap"]

    rev_yoy = None
    if rev and rev_prev and rev_prev > 0:
        rev_yoy = (rev - rev_prev) / rev_prev * 100

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if api_key:
        try:
            result = _call_claude(stock_name, sector, fund)
        except Exception as e:
            logger.warning("Claude API 호출 실패, 정량 폴백: %s", e)
            result = _quant_only_verdict(
                stock_code, stock_name, curr, ma200, high_250,
                vol_ratio, op_profit, rev_yoy, mktcap, sector
            )
    else:
        result = _quant_only_verdict(
            stock_code, stock_name, curr, ma200, high_250,
            vol_ratio, op_profit, rev_yoy, mktcap, sector
        )

    result["stock_code"] = stock_code
    result["stock_name"] = stock_name
    result["sector"]     = sector
    result["as_of_date"] = as_of_date
    result["curr_price"] = curr
    result["mktcap"]     = mktcap

    _cache_set(cache_key, result)
    return result


def batch_analyze(
    stock_codes: list,
    as_of_date: Optional[str] = None,
    min_score: int = 50,
) -> list:
    """여러 종목 일괄 분석. min_score 이상만 반환."""
    if as_of_date is None:
        as_of_date = datetime.today().strftime("%Y-%m-%d")

    results = []
    for sc in stock_codes:
        try:
            r = analyze_tenbagger_potential(sc, as_of_date)
            if r.get("tenbagger_score", 0) >= min_score:
                results.append(r)
        except Exception as e:
            logger.debug("batch_analyze skip %s: %s", sc, e)

    results.sort(key=lambda x: -x.get("tenbagger_score", 0))
    return results
