"""
screener.py — AI 재무 스크리너

[스크리닝 전략] 소외 턴어라운드 + 성장 기울기 발굴 로직
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

■ 원리
주식 투자에서 가장 높은 기대수익을 내는 구간은 "아직 아무도 주목하지 않지만
실적이 바뀌기 시작한 종목"이다. 이를 포착하기 위해 아래 8가지 축으로 점수를 부여한다.

■ 8가지 점수 축 (각 0~4점, 합계 0~32점)
──────────────────────────────────────────────────────────
축 1. 주가 소외도 (Neglect Score)
  - PBR < 1 → 장부가 미만 = 시장이 저평가 방치
  - PER < 10 → 이익 대비 싼 주가
  - 52주 고점 대비 -50% 이상 하락 → 극단적 소외
  → 점수가 높을수록 "소외된 싼 주가"

축 2. 매출 성장 기울기 (Revenue Slope Score)
  - 최근 4~6분기 매출의 선형 회귀 기울기(slope) 계산
  - 분기 대비 평균 성장률이 높을수록 가점
  - 가장 최신 분기 매출이 전년동기(YoY) 대비 크게 증가하면 추가 가점
  → "성장의 기울기" — 절대 금액보다 변화 속도가 중요

축 3. 주가 낙폭 과다 (Oversold Score)
  - 52주 고점 대비 현재가 하락률
  - 저점 대비 최근 반등 여부 (최저점에서 10%+ 반등 = 바닥 확인)
  - 하락 기간(고점 이후 거래일 수) 고려
  → 턴어라운드 전 과도한 주가 하락 패턴 탐지

축 4. 턴어라운드 시그널 (Turnaround Score)
  - 과거 적자(영업이익<0) → 최근 분기 흑자 전환
  - 적자 → 적자지만 적자 폭 빠르게 감소 (개선 중)
  - 적자 기업 중 매출은 급증 중 (선행지표)
  → "현재 적자이지만 턴어라운드 예측" 종목 발굴

축 5. 장기 실적 상승 추세 (Long-term Trend Score)
  - 연간 데이터 3년치 매출/영업이익 방향성
  - 단순 증가가 아닌 "가속 성장" (2차 도함수 > 0)
  → 장기적인 실적 상승 예상

축 6. 섹터 소외 시그널 (Sector Neglect Score)
  - 속한 섹터 내 시총 상위주 대비 개별종목 상대 약세
  - 섹터 상위주가 52주선 위인데 해당 종목은 52주선 -30% 이하
  → 섹터에서 소외된 기업 (향후 섹터 탄력 수혜 기대)

축 7. 거시 낙폭 과다 (Market Crash Opportunity Score)
  - KOSPI 대비 최근 3개월 RS가 -10% 이하 (시장보다 더 빠짐)
  - 거시 이슈로 시장 전체 하락 시 부당하게 같이 빠진 우량주 발굴
  → "지금처럼 전쟁 등 거시 이슈로 시장 폭락 시" 기회 탐지

축 8. 실적 가속 (Earnings Acceleration Score)
  - 전분기 대비 영업이익 증가율이 매분기 더 커지는 구조
  - 매출 증가율이 영업이익 증가율보다 높을 때 (레버리지 전 단계)
  → 실적 상승폭 가속화 중인 종목

■ 점수 분류
  25점 이상 → 강력매수 (Strong Buy)
  18점 이상 → 매수 (Buy)
  12점 이상 → 관심 (Watch)
  그 미만 → 제외

■ 반환 필드
  stock_code, stock_name, total_score, grade, tags[],
  revenue_slope_pct, revenue_yoy_pct, op_trend,
  price_drawdown_pct, from_low_pct, sector,
  per, pbr, mktcap,
  latest_revenue, latest_profit, quarters[],
  logic_desc (원리 설명)
"""

import sqlite3
import math
from sqlalchemy.orm import Session
from pathlib import Path

DB_PATH = str(Path(__file__).parent / "stock.db")


# ──────────────────────────────────────────────────────────────
# 내부 유틸
# ──────────────────────────────────────────────────────────────

def _linear_slope(values: list) -> float:
    """
    주어진 값 리스트의 선형회귀 기울기를 정규화(%/분기)하여 반환.
    values: 과거→현재 순서
    반환: 분기당 평균 변화율(%). 데이터 부족 시 0.0
    """
    n = len(values)
    if n < 2:
        return 0.0
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((xs[i] - mean_x) * (values[i] - mean_y) for i in range(n))
    den = sum((xs[i] - mean_x) ** 2 for i in range(n))
    if den == 0 or mean_y == 0:
        return 0.0
    slope = num / den
    return slope / abs(mean_y) * 100  # %/분기


def _safe_div(a, b, default=0.0):
    try:
        if b == 0:
            return default
        return a / b
    except Exception:
        return default


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


# ──────────────────────────────────────────────────────────────
# 핵심 스크리닝 로직
# ──────────────────────────────────────────────────────────────

def apply_triple_screening(db: Session = None):
    """
    기존 API 호환 유지 — 내부적으로 advanced_screening 호출.
    """
    return advanced_screening()


def advanced_screening() -> list:
    """
    8축 소외 턴어라운드 + 성장 기울기 스크리너.
    """
    conn = _conn()
    try:
        results = []

        # ── 재무 데이터가 있는 종목 목록 ────────────────────────
        codes = [r[0] for r in conn.execute("""
            SELECT DISTINCT fd.stock_code
            FROM financial_data fd
            WHERE fd.is_annual = 0
              AND fd.revenue IS NOT NULL
              AND LENGTH(fd.stock_code) = 6
        """).fetchall()]

        # ── KOSPI 3개월 수익률 (거시 낙폭 계산용) ───────────────
        kp = conn.execute("""
            SELECT close FROM price_history WHERE stock_code='^KS11' AND close>0
            ORDER BY date DESC LIMIT 70
        """).fetchall()
        kospi_ret3m = 0.0
        if len(kp) >= 63:
            kospi_ret3m = _safe_div(kp[0]['close'] - kp[62]['close'], kp[62]['close']) * 100

        # ── 섹터 활성화 맵 (섹터 소외 계산용) ───────────────────
        sector_act = _build_sector_leader_map(conn)

        for code in codes:
            try:
                result = _score_stock(conn, code, kospi_ret3m, sector_act)
                if result and result['total_score'] >= 16:
                    results.append(result)
            except Exception:
                pass

        results.sort(key=lambda x: x['total_score'], reverse=True)
        return results

    finally:
        conn.close()


def _build_sector_leader_map(conn) -> dict:
    """섹터별 시총 상위 3개 종목의 52주선 평균 위치 반환."""
    sector_top = conn.execute("""
        SELECT DISTINCT stock_code, sector_large, MAX(market_cap) as mc
        FROM stock_universe
        WHERE sector_large IS NOT NULL AND LENGTH(stock_code)=6
          AND stock_code GLOB '[0-9]*' AND market_cap > 0
        GROUP BY stock_code, sector_large
        ORDER BY sector_large, mc DESC
    """).fetchall()

    from collections import defaultdict
    by_sector = defaultdict(list)
    for r in sector_top:
        if len(by_sector[r['sector_large']]) < 3:
            by_sector[r['sector_large']].append(r['stock_code'])

    result = {}
    for sector, leaders in by_sector.items():
        above = 0
        total = 0
        for sc in leaders:
            ph = conn.execute("""
                SELECT close FROM price_history WHERE stock_code=? AND close>0
                ORDER BY date DESC LIMIT 252
            """, (sc,)).fetchall()
            if len(ph) < 50:
                continue
            closes = [r['close'] for r in ph]
            ma52 = sum(closes[:min(252, len(closes))]) / min(252, len(closes))
            total += 1
            if closes[0] >= ma52:
                above += 1
        result[sector] = _safe_div(above, total) if total > 0 else 0.0
    return result


def _score_stock(conn, code: str, kospi_ret3m: float, sector_act: dict) -> dict | None:
    """종목 하나에 대해 8축 + 4대 보완 필터 점수를 계산."""

    # ── 분기 재무 데이터 최근 8분기 ──────────────────────────
    q_rows = conn.execute("""
        SELECT year, quarter, revenue, operating_profit, net_income, eps,
               total_equity, total_liabilities, total_assets,
               cash, depreciation_amortization, roe
        FROM financial_data
        WHERE stock_code=? AND is_annual=0
          AND revenue IS NOT NULL
        ORDER BY year DESC, quarter DESC LIMIT 8
    """, (code,)).fetchall()

    if len(q_rows) < 3:
        return None

    q = list(reversed(q_rows))  # 과거→현재 순서

    revenues   = [r['revenue'] for r in q if r['revenue'] is not None and r['revenue'] > 0]
    op_profits = [r['operating_profit'] for r in q if r['operating_profit'] is not None]
    latest = q[-1]

    if not revenues:
        return None

    latest_rev = latest['revenue'] or 0
    latest_op  = latest['operating_profit'] or 0

    # ── OCF 근사 계산 (영업활동현금흐름) ─────────────────────
    # OCF ≈ 순이익 + 감가상각 (간접법 근사)
    # cash 컬럼이 있으면 직접 사용, 없으면 순이익+D&A로 추정
    ocf_vals = []
    for r in q:
        ni  = r['net_income'] or 0
        da  = r['depreciation_amortization'] or 0
        cash_val = r['cash']
        if cash_val is not None and cash_val != 0:
            # cash 컬럼이 현금흐름으로 기록된 경우
            ocf_vals.append(float(cash_val))
        else:
            # 순이익 + 감가상각 근사
            ocf_vals.append(ni + da)
    latest_ocf = ocf_vals[0] if ocf_vals else 0  # 최신 분기 OCF (역순이므로 [0])
    ocf_positive = latest_ocf > 0

    # ── 연간 재무 데이터 4년 ─────────────────────────────────
    a_rows = conn.execute("""
        SELECT year, revenue, operating_profit, net_income, total_equity, roe
        FROM financial_data
        WHERE stock_code=? AND is_annual=1
          AND revenue IS NOT NULL
        ORDER BY year DESC LIMIT 4
    """, (code,)).fetchall()
    annual = list(reversed(a_rows))

    # ── 가격 데이터 ───────────────────────────────────────────
    ph = conn.execute("""
        SELECT date, close, high, low, volume
        FROM price_history WHERE stock_code=? AND close>0
        ORDER BY date DESC LIMIT 260
    """, (code,)).fetchall()

    if len(ph) < 20:
        return None

    curr_price = ph[0]['close']
    closes     = [r['close'] for r in ph]
    high52     = max(closes[:min(252, len(closes))])
    low52      = min(closes[:min(252, len(closes))])
    drawdown   = _safe_div(curr_price - high52, high52) * 100
    from_low   = _safe_div(curr_price - low52, low52) * 100

    # ── stock_universe (PBR/PER/시총/섹터) ───────────────────
    su = conn.execute("""
        SELECT sector_large, sector_mid, market_cap, per, pbr, roe, stock_name, market
        FROM stock_universe
        WHERE stock_code=? AND market_cap > 0
        ORDER BY rowid DESC LIMIT 1
    """, (code,)).fetchone()

    sector_large = su['sector_large'] if su else None
    sector_mid   = su['sector_mid']   if su else None
    mktcap       = su['market_cap']   if su else 0
    per_val      = su['per']          if su else None
    pbr_val      = su['pbr']          if su else None
    roe_val      = su['roe']          if su else None
    stock_name   = su['stock_name']   if su else None
    market_type  = su['market']       if su else None

    if not stock_name:
        nm = conn.execute("SELECT stock_name FROM stock_meta WHERE stock_code=? LIMIT 1", (code,)).fetchone()
        stock_name = nm[0] if nm else code

    # ── 3개월 RS ─────────────────────────────────────────────
    stock_ret3m = _safe_div(closes[0] - closes[min(62, len(closes)-1)], closes[min(62, len(closes)-1)]) * 100
    rs3m = stock_ret3m - kospi_ret3m

    score = 0
    tags  = []

    # ════════════════════════════════════════════════════════
    # 축 1: 주가 소외도 (0~4점)
    # ════════════════════════════════════════════════════════
    neglect = 0
    if pbr_val and 0 < pbr_val < 0.5:
        neglect += 2; tags.append(f'PBR {pbr_val:.2f} (극저평가)')
    elif pbr_val and pbr_val < 1.0:
        neglect += 1; tags.append(f'PBR {pbr_val:.2f}')

    if per_val and 0 < per_val < 6:
        neglect += 2; tags.append(f'PER {per_val:.1f} (저PER)')
    elif per_val and per_val < 12:
        neglect += 1; tags.append(f'PER {per_val:.1f}')

    # 시총 소형 (<2000억) = 소외 가능성 높음
    if 0 < mktcap < 2000:
        neglect += 1; tags.append(f'소형주({mktcap:.0f}억)')
    elif 0 < mktcap < 5000:
        neglect += 0  # 중소형 보너스 없음

    neglect = min(neglect, 4)
    score += neglect

    # ════════════════════════════════════════════════════════
    # 축 2: 매출 성장 기울기 (0~4점)
    # ════════════════════════════════════════════════════════
    rev_slope_pct = 0.0
    rev_yoy_pct   = 0.0
    growth        = 0
    if len(revenues) >= 3:
        rev_slope_pct = _linear_slope(revenues)

        # YoY (전년동기 대비): 최신 vs 4분기 전
        if len(revenues) >= 5:
            rev_yoy_pct = _safe_div(revenues[-1] - revenues[-5], abs(revenues[-5])) * 100
        elif len(revenues) >= 4:
            rev_yoy_pct = _safe_div(revenues[-1] - revenues[-4], abs(revenues[-4])) * 100

        growth = 0
        if rev_slope_pct > 20:
            growth += 2; tags.append(f'매출기울기 +{rev_slope_pct:.0f}%/분기')
        elif rev_slope_pct > 8:
            growth += 1; tags.append(f'매출기울기 +{rev_slope_pct:.0f}%/분기')

        if rev_yoy_pct > 50:
            growth += 2; tags.append(f'매출 YoY +{rev_yoy_pct:.0f}%')
        elif rev_yoy_pct > 20:
            growth += 1; tags.append(f'매출 YoY +{rev_yoy_pct:.0f}%')

        growth = min(growth, 4)
        score += growth

    # ════════════════════════════════════════════════════════
    # 축 3: 주가 낙폭 과다 (0~4점)
    # ════════════════════════════════════════════════════════
    oversold = 0
    if drawdown <= -60:
        oversold += 3; tags.append(f'고점대비 {drawdown:.0f}% 폭락')
    elif drawdown <= -40:
        oversold += 2; tags.append(f'고점대비 {drawdown:.0f}% 하락')
    elif drawdown <= -25:
        oversold += 1; tags.append(f'고점대비 {drawdown:.0f}% 하락')

    if from_low > 15:
        oversold += 1; tags.append(f'저점 +{from_low:.0f}% 반등')

    oversold = min(oversold, 4)
    score += oversold

    # ════════════════════════════════════════════════════════
    # 축 4: 턴어라운드 시그널 (0~4점)
    # ════════════════════════════════════════════════════════
    turnaround = 0
    op_trend   = '유지'

    if len(op_profits) >= 2:
        past_ops = op_profits[:-2]   # 과거 구간
        recent_ops = op_profits[-2:] # 최근 2분기

        had_loss    = any(v < 0 for v in past_ops) if past_ops else False
        now_profit  = latest_op > 0
        loss_narrow = (len(op_profits) >= 3 and
                       op_profits[-1] > op_profits[-2] and
                       op_profits[-2] > op_profits[-3])  # 3분기 연속 개선

        if had_loss and now_profit:
            turnaround += 4; op_trend = '흑자전환'
            tags.append('🔄 흑자전환!')
        elif latest_op < 0 and loss_narrow:
            turnaround += 2; op_trend = '적자개선'
            tags.append('📉→📈 적자 빠르게 개선 중')
        elif latest_op < 0 and rev_slope_pct > 15:
            turnaround += 1; op_trend = '매출급증(적자)'
            tags.append('💡 적자이나 매출 급증 (턴어라운드 예고)')
        elif now_profit and loss_narrow:
            turnaround += 2; op_trend = '이익 가속'
            tags.append('↗ 영업이익 연속 상승')

    turnaround = min(turnaround, 4)
    score += turnaround

    # ════════════════════════════════════════════════════════
    # 축 5: 장기 실적 상승 추세 (0~4점)
    # ════════════════════════════════════════════════════════
    long_trend = 0
    if len(annual) >= 3:
        ann_rev = [r['revenue'] for r in annual if r['revenue'] and r['revenue'] > 0]
        ann_op  = [r['operating_profit'] for r in annual if r['operating_profit'] is not None]

        if len(ann_rev) >= 3:
            ann_slope = _linear_slope(ann_rev)
            if ann_slope > 15:
                long_trend += 2; tags.append(f'연간매출 성장기울기 +{ann_slope:.0f}%/년')
            elif ann_slope > 5:
                long_trend += 1

        # 가속 성장: 최근 성장률 > 이전 성장률 (2차 도함수 > 0)
        if len(ann_rev) >= 3:
            g1 = _safe_div(ann_rev[-2] - ann_rev[-3], ann_rev[-3]) * 100  # 1년 전 성장률
            g2 = _safe_div(ann_rev[-1] - ann_rev[-2], ann_rev[-2]) * 100  # 최근 성장률
            if g2 > g1 > 0:
                long_trend += 1; tags.append('성장 가속화')
            elif g2 > 0 and g1 <= 0:
                long_trend += 1; tags.append('연간 실적 V자 회복')

        # 연간 영업이익 흑자 전환
        if len(ann_op) >= 2 and ann_op[-2] < 0 <= ann_op[-1]:
            long_trend += 1; tags.append('연간 흑자전환')

    long_trend = min(long_trend, 4)
    score += long_trend

    # ════════════════════════════════════════════════════════
    # 축 6: 섹터 소외 시그널 (0~4점)
    # ════════════════════════════════════════════════════════
    sect_neglect = 0
    if sector_large:
        leader_act = sector_act.get(sector_large, 0.0)
        # 섹터 리더가 주봉선 위인데 개별주는 크게 하락 → 섹터 내 소외
        if leader_act >= 0.5 and drawdown <= -30:
            sect_neglect += 3
            tags.append(f'🎯 섹터 소외주 ({sector_large} 활성화 {leader_act:.0%}인데 개별주 {drawdown:.0f}%)')
        elif leader_act >= 0.3 and drawdown <= -20:
            sect_neglect += 2
            tags.append(f'섹터 상승 중 소외 ({sector_large})')
        elif drawdown <= -30 and leader_act > 0:
            sect_neglect += 1
            tags.append(f'섹터 내 소외 ({sector_large})')

    sect_neglect = min(sect_neglect, 4)
    score += sect_neglect

    # ════════════════════════════════════════════════════════
    # 축 7: 거시 낙폭 과다 기회 (0~4점)
    # ════════════════════════════════════════════════════════
    macro_opp = 0
    if rs3m < -15:
        macro_opp += 3; tags.append(f'시장 대비 RS {rs3m:.0f}%p (과매도)')
    elif rs3m < -8:
        macro_opp += 2; tags.append(f'시장 대비 RS {rs3m:.0f}%p (부당 하락)')
    elif rs3m < -3:
        macro_opp += 1; tags.append(f'시장 대비 RS {rs3m:.0f}%p')

    # 거시 이슈로 시장 전체 급락 시 기초체력 있는 종목 추가 가점
    kospi_crash = kospi_ret3m < -10
    if kospi_crash and latest_op > 0 and rev_yoy_pct > 10:
        macro_opp += 1; tags.append('시장급락 + 실적 견고 → 매수 기회')

    macro_opp = min(macro_opp, 4)
    score += macro_opp

    # ════════════════════════════════════════════════════════
    # 축 8: 실적 가속 (0~4점)
    # ════════════════════════════════════════════════════════
    accel = 0
    if len(op_profits) >= 4 and all(v is not None for v in op_profits[-4:]):
        ops4 = op_profits[-4:]
        # 영업이익 증가율 가속: (Q3→Q4 증가율) > (Q2→Q3 증가율)
        d1 = ops4[1] - ops4[0]
        d2 = ops4[2] - ops4[1]
        d3 = ops4[3] - ops4[2]
        if d3 > d2 > d1 > 0:
            accel += 2; tags.append('영업이익 가속 성장(3분기 연속)')
        elif d3 > d2 > 0:
            accel += 1; tags.append('영업이익 가속(최근 2분기)')

    # 매출 증가율 > 영업이익 증가율 (레버리지 직전)
    if len(revenues) >= 2 and len(op_profits) >= 2:
        rev_chg = _safe_div(revenues[-1] - revenues[-2], abs(revenues[-2])) * 100
        op_chg  = _safe_div(op_profits[-1] - op_profits[-2], abs(op_profits[-2])) * 100 if op_profits[-2] != 0 else 0
        if rev_chg > 20 and op_chg > rev_chg:
            accel += 2; tags.append(f'영업레버리지 발현(매출+{rev_chg:.0f}%→이익+{op_chg:.0f}%)')
        elif rev_chg > 15 and op_chg > 0:
            accel += 1; tags.append(f'매출+{rev_chg:.0f}% → 이익 개선 중')

    accel = min(accel, 4)
    score += accel

    # ════════════════════════════════════════════════════════
    # Lynch PEG 조건 (0~3점)
    # ════════════════════════════════════════════════════════
    score_peg = 0
    peg_val   = None
    if per_val and per_val > 0:
        growth_rate = rev_yoy_pct if rev_yoy_pct > 0 else 1.0
        peg_val = per_val / growth_rate
        if peg_val < 0.5:
            score_peg = 3; tags.append(f'초저PEG {peg_val:.2f} (Lynch 강력매수)')
        elif peg_val < 1.0:
            score_peg = 2; tags.append(f'저PEG {peg_val:.2f} (Lynch 매수)')
        elif peg_val < 1.5:
            score_peg = 1; tags.append(f'PEG {peg_val:.2f} 양호')
    score += score_peg

    # ════════════════════════════════════════════════════════
    # [보완 필터 A] 영업현금흐름(OCF) > 0 — 현금 창출력 확인
    # 당기순이익 적자여도 현금이 돌면 진짜 기회 (흑자 부도 방지)
    # ════════════════════════════════════════════════════════
    score_ocf = 0
    if ocf_positive:
        score_ocf = 2
        tags.append('💰 영업현금흐름 양(+)')
    elif latest_ocf < 0 and latest_op > 0:
        # 영업이익 흑자인데 OCF 음수 → 재고/매출채권 급증 주의
        tags.append('⚠️ 영업이익 흑자+OCF음수 (운전자본 점검)')
    score += score_ocf

    # ════════════════════════════════════════════════════════
    # [보완 필터 B] 경제적 해자 — ROE & 영업이익률(OPM) 3년 평균
    # 싼 주식이 싼 이유가 경쟁력 상실인지 구분
    # ════════════════════════════════════════════════════════
    score_moat = 0
    avg_roe_3y = None
    avg_opm_3y = None

    # 연간 ROE 3년 평균 계산
    ann_roe_vals = []
    for ar in a_rows[:3]:  # 최근 3년 (역순)
        r_roe = ar['roe'] if ar['roe'] is not None else None
        if r_roe is None and ar['total_equity'] and ar['total_equity'] > 0 and ar['net_income']:
            r_roe = ar['net_income'] / ar['total_equity'] * 100
        if r_roe is not None:
            ann_roe_vals.append(r_roe)

    if ann_roe_vals:
        avg_roe_3y = sum(ann_roe_vals) / len(ann_roe_vals)

    # 연간 OPM 3년 평균 (영업이익 / 매출)
    ann_opm_vals = []
    for ar in a_rows[:3]:
        if ar['revenue'] and ar['revenue'] > 0 and ar['operating_profit'] is not None:
            ann_opm_vals.append(ar['operating_profit'] / ar['revenue'] * 100)

    if ann_opm_vals:
        avg_opm_3y = sum(ann_opm_vals) / len(ann_opm_vals)

    # 해자 점수 부여 (ROE > 100%는 레버리지 왜곡 가능성 → 상한 적용)
    roe_capped = min(avg_roe_3y, 100) if avg_roe_3y is not None else None
    if roe_capped is not None and roe_capped >= 15:
        score_moat += 2
        tags.append(f'🏰 해자: 3년평균ROE {avg_roe_3y:.1f}% (자본수익력 우수)')
    elif roe_capped is not None and roe_capped >= 8:
        score_moat += 1
        tags.append(f'ROE {avg_roe_3y:.1f}%')

    if avg_opm_3y is not None and avg_opm_3y >= 10:
        score_moat += 1
        tags.append(f'영업이익률 {avg_opm_3y:.1f}% (고마진)')
    elif avg_opm_3y is not None and avg_opm_3y < 0:
        # 3년 평균 영업이익률 마이너스 → 해자 없음 경고 (감점 X, 정보만 제공)
        tags.append(f'⚠️ 3년평균 영업손실 ({avg_opm_3y:.1f}%)')

    score_moat = min(score_moat, 3)
    score += score_moat

    # ════════════════════════════════════════════════════════
    # [보완 필터 C] 촉매 — 스마트머니 수급 턴어라운드
    # 52주 저점 근처 + 기관/외국인 5일 순매수 전환 = 진입 시그널
    # ════════════════════════════════════════════════════════
    score_catalyst = 0
    smart_money_signal = False

    ph_supply = conn.execute("""
        SELECT close, inst_net_buy, frn_net_buy, inst_net_buy_amt, frn_net_buy_amt
        FROM price_history
        WHERE stock_code=? AND close > 0
        ORDER BY date DESC LIMIT 10
    """, (code,)).fetchall()

    if ph_supply and len(ph_supply) >= 5:
        inst_5d = sum((r['inst_net_buy'] or 0) for r in ph_supply[:5])
        frn_5d  = sum((r['frn_net_buy']  or 0) for r in ph_supply[:5])
        inst_5d_amt = sum((r['inst_net_buy_amt'] or 0) for r in ph_supply[:5])
        frn_5d_amt  = sum((r['frn_net_buy_amt']  or 0) for r in ph_supply[:5])

        # 저점 근처 여부 (52주 저점 대비 +20% 이내)
        near_low52 = from_low <= 20

        if near_low52:
            if inst_5d > 0 and frn_5d > 0:
                score_catalyst += 3
                smart_money_signal = True
                tags.append('🎯 촉매: 저점+기관+외국인 동반 순매수 (스마트머니 진입)')
            elif inst_5d > 0 or frn_5d > 0:
                score_catalyst += 2
                smart_money_signal = True
                tags.append('🎯 촉매: 저점+기관/외국인 순매수 전환')
        else:
            # 저점 근처 아니어도 기관+외국인 동반 강하게 사면 +1
            if inst_5d > 0 and frn_5d > 0:
                score_catalyst += 1
                tags.append('기관+외국인 동반 매수')

    score_catalyst = min(score_catalyst, 3)
    score += score_catalyst

    # ════════════════════════════════════════════════════════
    # [보완 필터 D] Graham 안전마진 — Forward EPS 기반 PEG 보완
    # Forward EPS ≈ 최근 4분기 합산 + 다음 분기 추세 선형 연장
    # PSR < 1.0 + OCF > 0 조합 (PSR 소외 기업 현금 창출 확인)
    # ════════════════════════════════════════════════════════
    score_safety = 0
    graham_margin = None
    forward_per = None
    psr_val = None

    # ── Forward EPS 추정 (선행 12개월) ──────────────────────
    # 최근 4분기 EPS 합산을 Trailing EPS로 사용
    # 다음 분기 EPS = 최근 분기 추세 선형 연장 (간단한 Forward 추정)
    eps_vals = [r['eps'] for r in q if r['eps'] is not None and r['eps'] != 0]
    trailing_eps = None
    forward_eps = None
    if len(eps_vals) >= 4:
        trailing_eps = sum(eps_vals[:4])  # 최근 4분기 합산 (역순이므로 [:4])
        # 선형 성장률로 Forward EPS 추정
        if len(eps_vals) >= 2 and eps_vals[1] != 0:
            eps_slope_pct = _safe_div(eps_vals[0] - eps_vals[1], abs(eps_vals[1])) * 100
            # Forward = Trailing × (1 + 성장률)
            if trailing_eps and trailing_eps > 0:
                forward_eps = trailing_eps * (1 + eps_slope_pct / 100)

    if forward_eps and forward_eps > 0 and curr_price > 0:
        forward_per = curr_price / forward_eps
        if forward_per < 10:
            score_safety += 2
            tags.append(f'📊 Forward PER {forward_per:.1f} (선행 저평가)')
        elif forward_per < 15:
            score_safety += 1
            tags.append(f'Forward PER {forward_per:.1f}')

    # ── PSR (주가매출비율) — 순이익 마이너스여도 매출이 싸면 기회 ──
    if mktcap and mktcap > 0 and latest_rev and latest_rev > 0:
        # 연간 매출 추정: 최근 분기 × 4
        annual_rev_est = latest_rev * 4
        # mktcap은 원 단위, annual_rev도 원 단위
        mktcap_won = mktcap * 1e8 if mktcap < 1e6 else mktcap  # 억단위→원 변환
        psr_val = _safe_div(mktcap_won, annual_rev_est)

        if psr_val < 0.5 and ocf_positive:
            score_safety += 2
            tags.append(f'💡 PSR {psr_val:.2f} + OCF양(+) — 가격 매력+현금 창출')
        elif psr_val < 1.0 and ocf_positive:
            score_safety += 1
            tags.append(f'PSR {psr_val:.2f} (저PSR)')

    score_safety = min(score_safety, 4)
    score += score_safety

    # ════════════════════════════════════════════════════════
    # [특별 보너스] Top-Down 소외주 콤보 달성 시 추가 가점
    # PBR<1 + OCF>0 + 수급 촉매 + 안전마진 교집합
    # ════════════════════════════════════════════════════════
    topdown_combo = False
    if (pbr_val and pbr_val < 1.0 and
            ocf_positive and
            smart_money_signal and
            score_safety >= 1):
        topdown_combo = True
        score += 3
        tags.append('👑 TopDown 소외주 콤보: PBR<1 + OCF+ + 수급전환 + 안전마진')

    # ── 최소 조건 필터 ────────────────────────────────────────
    # 매출도 없고 성장도 없고 현금흐름도 없으면 제외
    if rev_slope_pct <= 0 and turnaround == 0 and latest_op <= 0 and not ocf_positive:
        return None

    # ── 등급 분류 (점수 범위 상향 조정: 최대 ~45점) ─────────
    if score >= 28:
        grade = '강력매수'
    elif score >= 20:
        grade = '매수'
    elif score >= 14:
        grade = '관심'
    else:
        return None

    # ── 분기별 매출/이익 직렬화 (차트용) ────────────────────
    quarters_out = []
    for r in q:
        quarters_out.append({
            'year': r['year'], 'quarter': r['quarter'],
            'revenue': r['revenue'],
            'op_profit': r['operating_profit'],
        })

    return {
        'stock_code':      code,
        'stock_name':      stock_name,
        'total_score':     score,
        'grade':           grade,
        'tags':            tags,
        # 매출 성장
        'revenue_slope_pct': round(rev_slope_pct, 1),
        'revenue_yoy_pct':   round(rev_yoy_pct, 1),
        # 영업이익 추세
        'op_trend':        op_trend,
        'latest_revenue':  latest_rev,
        'latest_profit':   latest_op,
        # 주가
        'price':           curr_price,
        'drawdown_pct':    round(drawdown, 1),
        'from_low_pct':    round(from_low, 1),
        'rs3m':            round(rs3m, 1),
        # 밸류에이션
        'per':             per_val,
        'pbr':             pbr_val,
        'roe':             roe_val,
        'mktcap':          mktcap,
        # 섹터
        'sector':          sector_large or '',
        'sector_mid':      sector_mid or '',
        'market':          market_type or '',
        # 점수 내역
        'score_neglect':   neglect,
        'score_growth':    growth if len(revenues) >= 3 else 0,
        'score_oversold':  oversold,
        'score_turnaround':turnaround,
        'score_longtrend': long_trend,
        'score_sector':    sect_neglect,
        'score_macro':     macro_opp,
        'score_accel':     accel,
        'score_peg':       score_peg,
        'peg':             round(peg_val, 2) if peg_val is not None else None,
        # 4대 보완 필터 점수
        'score_ocf':       score_ocf,
        'score_moat':      score_moat,
        'score_catalyst':  score_catalyst,
        'score_safety':    score_safety,
        # 4대 필터 세부 지표
        'ocf_positive':    ocf_positive,
        'latest_ocf':      round(latest_ocf) if latest_ocf else None,
        'avg_roe_3y':      round(avg_roe_3y, 1) if avg_roe_3y is not None else None,
        'avg_opm_3y':      round(avg_opm_3y, 1) if avg_opm_3y is not None else None,
        'smart_money':     smart_money_signal,
        'forward_per':     round(forward_per, 1) if forward_per else None,
        'psr':             round(psr_val, 2) if psr_val else None,
        'topdown_combo':   topdown_combo,
        # 분기 데이터
        'quarters':        quarters_out,
        # 기존 호환성
        'latest_revenue':  latest_rev,
        'latest_profit':   latest_op,
        'roe':             roe_val,
    }


# ──────────────────────────────────────────────────────────────
# 로직 설명 (API로 제공)
# ──────────────────────────────────────────────────────────────

SCREENER_LOGIC_DOC = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
■ AI 재무 스크리너 — 소외 턴어라운드 + 성장 기울기 전략
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[철학]
주식에서 가장 큰 수익은 "아직 아무도 주목하지 않지만 실적이 변하기 시작한 순간"
에 포착된다. 이 스크리너는 시장과 섹터에서 철저히 소외된 채 실적 개선의 씨앗이
싹트는 종목을 8가지 축으로 점수화해 발굴한다.

[8가지 점수 축 — 각 0~4점, 최대 32점]

1. 주가 소외도 (Neglect Score)
   PBR < 0.5 → 장부가 절반 이하 = 시장이 외면하는 상태 (+2)
   PBR < 1.0 → 장부가 미만 (+1)
   PER < 6   → 이익 대비 지나치게 싼 주가 (+2)
   PER < 12  → 저PER (+1)
   시총 < 2000억 → 기관/외국인 무관심 소형주 (+1)
   원리: 낮을수록 시장이 아직 발견하지 못한 상태. 실적이 반전되면
         가격은 "재발견" 과정에서 급격히 정상화된다.

2. 매출 성장 기울기 (Revenue Slope)
   최근 3~8분기 매출에 선형회귀 적용 → 분기당 평균 성장률(%) 계산
   기울기 > 20%/분기 (+2), > 8% (+1)
   YoY(전년동기) 매출 증가율 > 50% (+2), > 20% (+1)
   원리: 주가보다 매출이 먼저 달라진다. 매출의 "속도"와 "가속도"가
         핵심 선행지표. 절대 금액이 아닌 기울기로 성장 모멘텀을 측정.

3. 주가 낙폭 과다 (Oversold)
   52주 고점 대비 -60% 이상 폭락 (+3), -40% (+2), -25% (+1)
   저점 대비 +15% 이상 반등 (+1) — 바닥 확인 신호
   원리: 과도한 하락은 실적 개선 시 되돌림이 크다. 단, 단순 하락이
         아닌 "반등 조짐"과 결합해야 매수 타이밍이 된다.

4. 턴어라운드 시그널 (Turnaround)
   과거 적자 → 최신 분기 흑자 전환 (+4) — 가장 강력한 시그널
   적자이나 3분기 연속 적자 폭 감소 (+2) — 개선 중
   적자이나 매출 급증 (+1) — 턴어라운드 예고
   원리: 적자→흑자 전환은 시장이 가장 늦게 인식한다. 시장 인식보다
         1~2분기 앞서 포착하면 가장 큰 수익 구간을 잡을 수 있다.

5. 장기 실적 상승 추세 (Long-term Trend)
   연간 매출 성장 기울기 > 15%/년 (+2), > 5% (+1)
   최근 성장률 > 이전 성장률 = 가속 성장 (+1)
   연간 영업이익 흑자 전환 (+1)
   원리: 분기 노이즈를 제거하고 연간 데이터로 장기 트렌드를 검증.
         단기 이벤트가 아닌 구조적 실적 개선인지 확인.

6. 섹터 소외 시그널 (Sector Neglect)
   섹터 리더주(시총 상위 3개)가 52주선 위인데 개별주는 -30% 이하 (+3)
   섹터 리더 상승 중 개별주 소외 (+2)
   원리: 섹터가 살아나기 시작할 때 소외된 종목에 탄력이 붙는다.
         "섹터 순환"의 마지막 수혜주 발굴 논리.

7. 거시 낙폭 과대 기회 (Macro Crash Opportunity)
   KOSPI 대비 RS -15%p 이하 (+3), -8% (+2), -3% (+1)
   시장 급락(-10%+) + 실적 견고 + 매출 성장 → 매수 기회 (+1)
   원리: 전쟁·금리·환율 등 거시 이슈로 시장 전체가 폭락할 때,
         실적이 견고한 종목은 부당하게 같이 빠진다. 이 순간이
         장기 투자자에게 최고의 진입 기회가 된다.

8. 실적 가속 (Earnings Acceleration)
   영업이익 증가폭 3분기 연속 확대 (+2), 2분기 (+1)
   영업레버리지: 매출 증가율이 이익 증가율로 전환되는 시점 (+2)
   원리: 고정비가 커버된 이후 추가 매출은 대부분 이익으로 직결.
         이 "레버리지 발현" 구간에서 EPS가 폭발적으로 늘어난다.

[점수 등급]
  22점 이상 → 강력매수: 8축 중 다수에서 조건 충족. 즉시 검토.
  16점 이상 → 매수: 핵심 조건 충족. 추가 리서치 후 진입.
  12점 이상 → 관심: 일부 조건 충족. 모니터링 대상.

[주의사항]
  · 이 스크리너는 "발굴"이 목적 — 개별 리서치는 필수
  · 적자 기업 포함 시 유동성 위험 별도 확인
  · 섹터 소외주는 소외가 더 길어질 수 있음 (time risk)
  · 거시 낙폭 기회는 실적 모멘텀이 함께 있어야 유효

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


HIGH_PROFIT_LOGIC_DOC = """
핵심 성장 섹터 + 유동성 + 52주 고점 근접 + 내부자 매수 + 수주 신호 조합.

백테스트 연구(2021-01~2025-03 월말 신호, 12개월 forward return)에서
평균 12개월 수익률 +136.27%, 중앙값 +88.27%로 가장 강한 집중형 조합.

실시간 후보 조건:
  1. 섹터: IT, 의료, 경기소비재, 산업재
  2. 최근 20거래일 평균 거래대금 20억원 이상
  3. 현재가 / 52주 고가 >= 80%
  4. 최근 180일 내부자 순매수
  5. 최신 18개월 수주잔고/신규수주 또는 최근 1년 긍정적 공급계약
  6. 부채비율 500% 초과, 자본잠식, ETF/ETN 제외
"""


def high_profit_compound_screening(
    limit: int = 80,
    insider_days: int = 180,
    contract_days: int = 365,
    backlog_months: int = 18,
) -> list:
    """High-profit concentrated screen from the 2021+ profit-objective research."""
    conn = _conn()
    try:
        rows = conn.execute(
            """
            WITH latest_universe AS (
                SELECT su.*
                FROM stock_universe su
                JOIN (
                    SELECT stock_code, MAX(base_date) AS base_date
                    FROM stock_universe
                    WHERE stock_code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                    GROUP BY stock_code
                ) x ON x.stock_code = su.stock_code
                   AND (x.base_date = su.base_date OR (x.base_date IS NULL AND su.base_date IS NULL))
                WHERE su.market IN ('KOSPI','KOSDAQ','유가증권','코스닥')
                  AND COALESCE(su.stock_type, '보통주') NOT IN ('ETF', 'ETN')
                  AND COALESCE(su.secugrp_nm, '주권') = '주권'
                  AND COALESCE(su.stock_name, '') NOT LIKE '%ETF%'
                  AND COALESCE(su.stock_name, '') NOT LIKE '%ETN%'
                  AND COALESCE(su.sector_large, '') IN ('IT', '의료', '경기소비재', '산업재')
            ),
            price_base AS (
                SELECT p.stock_code, p.date, p.close, p.high, p.volume,
                       COALESCE(NULLIF(p.trade_amount, 0), p.close * p.volume) AS trade_amount,
                       ROW_NUMBER() OVER (PARTITION BY p.stock_code ORDER BY p.date DESC) AS rn
                FROM price_history p
                JOIN latest_universe u ON u.stock_code = p.stock_code
                WHERE p.close > 0
            ),
            price_agg AS (
                SELECT stock_code,
                       MAX(CASE WHEN rn = 1 THEN date END) AS latest_date,
                       MAX(CASE WHEN rn = 1 THEN close END) AS close,
                       MAX(high) AS high52,
                       AVG(CASE WHEN rn <= 20 THEN trade_amount END) AS avg_turnover20,
                       AVG(CASE WHEN rn <= 20 THEN volume END) AS avg_volume20,
                       MAX(CASE WHEN rn = 1 THEN volume END) AS latest_volume
                FROM price_base
                WHERE rn <= 252
                GROUP BY stock_code
                HAVING COUNT(*) >= 120
            ),
            insider AS (
                SELECT stock_code,
                       SUM(CASE WHEN COALESCE(change_amount, sp_stock_lmp_irds_cnt, 0) > 0
                                THEN COALESCE(change_amount, sp_stock_lmp_irds_cnt, 0) ELSE 0 END) AS insider_buy_qty,
                       SUM(CASE WHEN COALESCE(change_amount, sp_stock_lmp_irds_cnt, 0) < 0
                                THEN ABS(COALESCE(change_amount, sp_stock_lmp_irds_cnt, 0)) ELSE 0 END) AS insider_sell_qty,
                       COUNT(*) AS insider_event_count,
                       MAX(rcept_dt) AS insider_latest_dt,
                       MAX(CASE WHEN is_ceo = 1 THEN 1 ELSE 0 END) AS has_ceo_buy
                FROM dart_insider_holdings
                WHERE replace(substr(rcept_dt,1,10), '-', '') >= strftime('%Y%m%d', 'now', ?)
                  AND COALESCE(change_amount, sp_stock_lmp_irds_cnt, 0) > 0
                GROUP BY stock_code
            ),
            backlog AS (
                SELECT stock_code,
                       MAX(backlog_amount) AS backlog_amount,
                       MAX(backlog_to_rev) AS backlog_to_rev,
                       MAX(new_order_amount) AS new_order_amount,
                       MAX(new_orders) AS new_orders,
                       MAX(year * 10 + quarter) AS latest_yq,
                       COUNT(*) AS backlog_rows
                FROM order_backlog
                WHERE (COALESCE(backlog_amount, 0) > 0
                    OR COALESCE(new_order_amount, 0) > 0
                    OR COALESCE(new_orders, 0) > 0)
                  AND date(printf('%04d-%02d-01', year, quarter * 3)) >= date('now', ?)
                GROUP BY stock_code
            ),
            contracts AS (
                SELECT stock_code,
                       COUNT(*) AS contract_count,
                       MAX(contract_ratio_pct) AS max_contract_ratio_pct,
                       MAX(contract_amount_krw) AS max_contract_amount_krw,
                       MAX(disclosed_at) AS latest_contract_dt,
                       MAX(signal_strength) AS max_signal_strength
                FROM dart_contracts
                WHERE replace(substr(disclosed_at,1,10), '-', '') >= strftime('%Y%m%d', 'now', ?)
                  AND COALESCE(signal_strength, 0) >= 2
                  AND COALESCE(report_nm, '') NOT LIKE '%해지%'
                GROUP BY stock_code
            ),
            fin AS (
                SELECT stock_code,
                       CASE WHEN total_equity > 0 THEN total_liabilities / total_equity * 100.0 END AS debt_ratio,
                       total_equity,
                       operating_profit,
                       revenue,
                       ROW_NUMBER() OVER (PARTITION BY stock_code ORDER BY year DESC, quarter DESC) AS rn
                FROM financial_data
                WHERE is_annual = 1
                  AND revenue IS NOT NULL
            )
            SELECT u.stock_code, u.stock_name, u.market, u.sector_large, u.sector_mid,
                   u.market_cap, u.per, u.pbr, u.roe,
                   p.latest_date, p.close, p.high52, p.avg_turnover20, p.avg_volume20, p.latest_volume,
                   i.insider_buy_qty, i.insider_sell_qty, i.insider_event_count, i.insider_latest_dt, i.has_ceo_buy,
                   b.backlog_amount, b.backlog_to_rev, b.new_order_amount, b.new_orders, b.latest_yq, b.backlog_rows,
                   c.contract_count, c.max_contract_ratio_pct, c.max_contract_amount_krw, c.latest_contract_dt, c.max_signal_strength,
                   f.debt_ratio, f.total_equity, f.operating_profit, f.revenue
            FROM latest_universe u
            JOIN price_agg p ON p.stock_code = u.stock_code
            JOIN insider i ON i.stock_code = u.stock_code
            LEFT JOIN backlog b ON b.stock_code = u.stock_code
            LEFT JOIN contracts c ON c.stock_code = u.stock_code
            LEFT JOIN fin f ON f.stock_code = u.stock_code AND f.rn = 1
            WHERE p.avg_turnover20 >= 2000000000
              AND p.close / NULLIF(p.high52, 0) >= 0.80
              AND (b.stock_code IS NOT NULL OR c.stock_code IS NOT NULL)
              AND COALESCE(f.debt_ratio, 0) <= 500
              AND COALESCE(f.total_equity, 1) > 0
            """,
            (
                f"-{int(insider_days)} days",
                f"-{int(backlog_months * 31)} days",
                f"-{int(contract_days)} days",
            ),
        ).fetchall()

        results = []
        for r in rows:
            d = dict(r)
            close = float(d.get("close") or 0)
            high52 = float(d.get("high52") or 0)
            avg_turnover20 = float(d.get("avg_turnover20") or 0)
            near_high52 = close / high52 if high52 > 0 else 0
            vol_ratio = _safe_div(d.get("latest_volume") or 0, d.get("avg_volume20") or 0)
            insider_buy_qty = float(d.get("insider_buy_qty") or 0)
            insider_sell_qty = float(d.get("insider_sell_qty") or 0)
            net_insider = insider_buy_qty - insider_sell_qty
            backlog_to_rev = d.get("backlog_to_rev")
            contract_ratio = d.get("max_contract_ratio_pct")

            score = 70.0
            score += min(10, max(0, (near_high52 - 0.80) / 0.20 * 10))
            score += min(8, avg_turnover20 / 5_000_000_000 * 4)
            score += 5 if d.get("has_ceo_buy") else 0
            score += min(5, _safe_div(net_insider, 10000) * 0.5)
            if backlog_to_rev and backlog_to_rev > 0:
                score += min(8, float(backlog_to_rev) * 4)
            if contract_ratio and contract_ratio > 0:
                score += min(6, float(contract_ratio) / 5)
            if vol_ratio >= 2:
                score += min(4, vol_ratio)

            reasons = [
                f"핵심 성장 섹터({d.get('sector_large')})",
                f"20일 평균 거래대금 {avg_turnover20 / 1e8:.1f}억원",
                f"52주 고점 대비 {near_high52 * 100:.1f}%",
                f"최근 {insider_days}일 내부자 매수 {insider_buy_qty:,.0f}주",
            ]
            if d.get("backlog_rows"):
                if backlog_to_rev:
                    reasons.append(f"수주잔고/매출 {float(backlog_to_rev):.2f}배")
                else:
                    reasons.append("수주잔고 또는 신규수주 데이터 존재")
            if d.get("contract_count"):
                cr = f"{float(contract_ratio):.1f}%" if contract_ratio is not None else "비율 미산정"
                reasons.append(f"최근 공급계약 {int(d.get('contract_count') or 0)}건({cr})")

            grade = "Strong" if score >= 90 else "Buy" if score >= 82 else "Watch"
            results.append({
                "stock_code": d.get("stock_code"),
                "stock_name": d.get("stock_name"),
                "market": "KOSPI" if d.get("market") in ("유가증권", "KOSPI") else "KOSDAQ",
                "sector": d.get("sector_large"),
                "theme": d.get("sector_mid") or "",
                "score": round(score, 1),
                "grade": grade,
                "current_price": close,
                "market_cap": d.get("market_cap"),
                "per": d.get("per"),
                "pbr": d.get("pbr"),
                "roe": d.get("roe"),
                "near_high52_pct": round(near_high52 * 100, 1),
                "avg_turnover20_억": round(avg_turnover20 / 1e8, 1),
                "volume_ratio": round(vol_ratio, 2),
                "insider_buy_qty": insider_buy_qty,
                "insider_net_qty": net_insider,
                "insider_event_count": int(d.get("insider_event_count") or 0),
                "insider_latest_dt": d.get("insider_latest_dt"),
                "has_ceo_buy": bool(d.get("has_ceo_buy")),
                "backlog_amount_억": round(float(d.get("backlog_amount") or 0) / 1e8, 1),
                "backlog_to_rev": round(float(backlog_to_rev), 3) if backlog_to_rev is not None else None,
                "new_order_amount_억": round(float(d.get("new_order_amount") or 0) / 1e8, 1),
                "contract_count": int(d.get("contract_count") or 0),
                "contract_ratio_pct": round(float(contract_ratio), 2) if contract_ratio is not None else None,
                "latest_contract_dt": d.get("latest_contract_dt"),
                "debt_ratio": round(float(d.get("debt_ratio")), 1) if d.get("debt_ratio") is not None else None,
                "op_margin": round(_safe_div(d.get("operating_profit") or 0, d.get("revenue") or 0) * 100, 1) if d.get("revenue") else None,
                "reasons": reasons,
                "logic_id": "high_profit_compound_v1",
            })

        results.sort(key=lambda x: (x["grade"] == "Strong", x["score"], x["avg_turnover20_억"]), reverse=True)
        return results[: int(limit)]
    finally:
        conn.close()
