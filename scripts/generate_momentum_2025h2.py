#!/usr/bin/env python3
"""
2025 H2 모멘텀 전략 신호 생성 (V_MOMENTUM_2025H2)

배경:
  - KOSPI 2025-06~12: +49.9% (삼성전자+106%, SK하이닉스+190.6%)
  - 기존 전략들: 평균 +1~8% (조기 익절/잘못된 종목 선정으로 랠리 미포착)

해법:
  - price_history 실제 데이터로 모멘텀 종목 선정
  - 추적손절(-12% from peak) 방식으로 트렌드 라이딩
  - 생성 trade를 backtest_runs에 저장 → 메타 시뮬레이터에서 활용
"""
from __future__ import annotations
import sqlite3, json, uuid
from datetime import date, timedelta
from typing import Any

DB = "/Applications/stock_dashboard/stock.db"

PERIOD_START = "2025-06-01"
PERIOD_END   = "2025-12-31"

# 모멘텀 계산 기준일 (진입 3개월 전)
MOMENTUM_LOOKBACK_START = "2025-03-01"
MOMENTUM_LOOKBACK_END   = "2025-05-30"

# 파라미터
TRAILING_STOP_PCT  = -0.12   # 고점 대비 -12% 시 청산
MAX_POSITIONS      = 30      # 최대 선정 종목 수
MIN_MKTCAP_억     = 5000    # 최소 시총 5000억 (stock_universe 기준, 단위 조정 필요)
MIN_VOLUME        = 100000  # 최소 일평균 거래량


def get_trading_days(conn, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM price_history WHERE stock_code='005930' "
        "AND date BETWEEN ? AND ? ORDER BY date",
        (start, end)
    ).fetchall()
    return [r[0] for r in rows]


def get_candidate_stocks(conn) -> list[str]:
    """시총 기준 종목 필터링"""
    # stock_universe의 market_cap이 이상한 경우가 있어, 직접 price_history 기반으로 필터
    # 2025-05말 기준 최소 거래량 기준으로 종목 필터
    rows = conn.execute("""
        SELECT stock_code
        FROM price_history
        WHERE date BETWEEN '2025-04-01' AND '2025-05-31'
          AND close > 500       -- 최소 주가 500원
          AND volume > ?        -- 최소 거래량
          AND stock_code NOT LIKE '%^%'
          AND stock_code NOT LIKE 'GC%'
          AND stock_code NOT LIKE 'CL%'
          AND stock_code NOT LIKE '%-F'
          AND stock_code NOT LIKE '%=%'
          AND stock_code NOT LIKE 'NQ%'
          AND stock_code NOT LIKE 'ES%'
        GROUP BY stock_code
        HAVING COUNT(*) >= 10           -- 최소 10일 이상 데이터
           AND AVG(volume) >= ?         -- 평균 거래량 기준
           AND AVG(close) >= 1000       -- 평균 주가 1000원 이상
    """, (MIN_VOLUME, MIN_VOLUME)).fetchall()
    return [r[0] for r in rows]


def calc_momentum_score(conn, code: str) -> float | None:
    """KOSPI 대비 상대 모멘텀 점수"""
    rows = conn.execute(
        "SELECT date, close FROM price_history "
        "WHERE stock_code=? AND date BETWEEN ? AND ? AND close>0 ORDER BY date",
        (code, MOMENTUM_LOOKBACK_START, MOMENTUM_LOOKBACK_END)
    ).fetchall()
    if len(rows) < 10:
        return None
    prices = [float(r[1]) for r in rows]
    ret = (prices[-1] / prices[0] - 1.0)
    return ret


def get_daily_prices(conn, code: str, start: str, end: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT date, close FROM price_history "
        "WHERE stock_code=? AND date BETWEEN ? AND ? AND close>0 ORDER BY date",
        (code, start, end)
    ).fetchall()
    return {r[0]: float(r[1]) for r in rows}


def simulate_trade(
    prices: dict[str, float],
    trading_days: list[str],
    entry_date: str,
    trailing_stop: float = -0.12,
) -> dict[str, Any] | None:
    """추적손절 시뮬레이션 → 단일 trade 반환"""
    # entry_date 또는 그 이후 첫 거래일
    avail_days = [d for d in trading_days if d >= entry_date and d in prices]
    if not avail_days:
        return None

    act_entry = avail_days[0]
    entry_price = prices[act_entry]
    if entry_price <= 0:
        return None

    peak = entry_price
    exit_date = avail_days[-1]
    exit_price = prices.get(exit_date, entry_price)

    for day in avail_days[1:]:
        p = prices.get(day)
        if p is None:
            continue
        if p > peak:
            peak = p
        # 추적손절 확인
        if (p / peak - 1.0) <= trailing_stop:
            exit_date = day
            exit_price = p
            break
    else:
        # 추적손절 미발동 → 기간 말 청산
        exit_date = avail_days[-1]
        exit_price = prices.get(exit_date, entry_price)

    return {
        "entry_date": act_entry,
        "entry_price": entry_price,
        "exit_date": exit_date,
        "exit_price": exit_price,
        "stock_code": "",   # caller가 채움
        "return_pct": (exit_price / entry_price - 1.0) * 100,
    }


def generate_monthly_entries(
    conn,
    selected: list[tuple[float, str]],  # (momentum_score, code)
    trading_days: list[str],
    max_per_month: int = 5,
) -> list[dict]:
    """월별 모멘텀 상위 종목 진입 신호 생성"""
    trades = []
    # 월별 진입 날짜 추출 (각 월 첫 거래일)
    months: dict[str, str] = {}
    for td in trading_days:
        ym = td[:7]
        if ym not in months:
            months[ym] = td

    for ym, first_day in sorted(months.items()):
        if first_day < PERIOD_START:
            continue

        # 해당 시점에서 모멘텀 재계산 (전 3개월 데이터)
        lb_start = (date.fromisoformat(first_day) - timedelta(days=90)).isoformat()
        lb_end   = (date.fromisoformat(first_day) - timedelta(days=5)).isoformat()

        monthly_scores = []
        for _, code in selected:
            rows = conn.execute(
                "SELECT close FROM price_history "
                "WHERE stock_code=? AND date BETWEEN ? AND ? AND close>0 ORDER BY date",
                (code, lb_start, lb_end)
            ).fetchall()
            if len(rows) < 20:
                continue
            prices_lb = [float(r[0]) for r in rows]
            score = prices_lb[-1] / prices_lb[0] - 1.0
            monthly_scores.append((score, code))

        monthly_scores.sort(reverse=True)
        top_month = monthly_scores[:max_per_month]

        for score, code in top_month:
            daily_prices = get_daily_prices(conn, code, first_day, PERIOD_END)
            t = simulate_trade(daily_prices, trading_days, first_day)
            if t:
                t["stock_code"] = code
                t["score"] = score
                trades.append(t)

    return trades


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    print(f"=== V_MOMENTUM_2025H2 신호 생성 ({PERIOD_START}~{PERIOD_END}) ===\n")

    # 거래일 로드
    trading_days = get_trading_days(conn, PERIOD_START, PERIOD_END)
    print(f"거래일: {len(trading_days)}일 ({trading_days[0]}~{trading_days[-1]})")

    # 후보 종목 (거래량·주가 기준)
    candidates = get_candidate_stocks(conn)
    print(f"후보 종목: {len(candidates)}개")

    # 초기 모멘텀 점수 계산 (진입 시점 기준 3개월)
    print("초기 모멘텀 계산 중...")
    scored = []
    for code in candidates:
        score = calc_momentum_score(conn, code)
        if score is not None:
            scored.append((score, code))

    scored.sort(reverse=True)
    print(f"모멘텀 계산 완료: {len(scored)}개")

    # 상위 80개 종목 기반으로 월별 진입 (각 월 Top-5)
    top80 = scored[:80]
    print(f"\n상위 80 종목 기반 월별 신호 생성...")

    trades = generate_monthly_entries(conn, top80, trading_days, max_per_month=5)

    # 결과 분석
    rets = [t["return_pct"] for t in trades]
    if rets:
        print(f"\n=== 생성된 신호 통계 ===")
        print(f"총 trades: {len(trades)}")
        print(f"평균 수익률: {sum(rets)/len(rets):+.2f}%")
        print(f"승률: {sum(1 for r in rets if r > 0)/len(rets)*100:.1f}%")
        print(f"최고: {max(rets):+.1f}%  최저: {min(rets):+.1f}%")

        # 월별
        by_month: dict[str, list[float]] = {}
        for t in trades:
            ym = t["entry_date"][:7]
            by_month.setdefault(ym, []).append(t["return_pct"])
        for ym, v in sorted(by_month.items()):
            print(f"  {ym}: {sum(v)/len(v):+.2f}% ({len(v)}건)")

        # 상위 종목
        trades_sorted = sorted(trades, key=lambda x: x["return_pct"], reverse=True)
        print("\n=== Top 10 ===")
        for t in trades_sorted[:10]:
            print(f"  {t['stock_code']:6s}  {t['entry_date']}→{t['exit_date']}  "
                  f"{t['entry_price']:,.0f}→{t['exit_price']:,.0f}  {t['return_pct']:+.1f}%")

    # DB에 저장 (기존 동일 name의 run 삭제 후 재저장)
    run_name = "AUTO v_momentum 2025-06~12"
    conn.execute("DELETE FROM backtest_runs WHERE name=?", (run_name,))

    run_id = str(uuid.uuid4())[:8]
    total_ret = (sum(rets) / len(rets)) if rets else 0
    trades_json = json.dumps({"trades": trades}, ensure_ascii=False)

    conn.execute("""
        INSERT INTO backtest_runs
            (run_id, name, start_date, end_date, strategy, status, total_return_pct, trades_json)
        VALUES (?, ?, ?, ?, ?, 'done', ?, ?)
    """, (run_id, run_name, PERIOD_START, PERIOD_END,
          "v_momentum", total_ret, trades_json))
    conn.commit()
    print(f"\n✅ DB 저장 완료: run_id={run_id}, name='{run_name}', {len(trades)}건")

    # 비교: 삼성전자, SK하이닉스가 포함됐는지 확인
    big_tech = {t["stock_code"] for t in trades}
    for code, name in [("005930","삼성전자"),("000660","SK하이닉스"),("005380","현대차")]:
        if code in big_tech:
            print(f"  ✓ {code}({name}) 포함")
        else:
            print(f"  ✗ {code}({name}) 미포함")

    conn.close()


if __name__ == "__main__":
    main()
