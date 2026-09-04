#!/usr/bin/env python3
"""
V_LARGE_TREND: KOSPI 대형주 추세 추종 전략 (2025-06~12)

핵심 인사이트:
  - 2025 H2 랠리는 삼성전자(+106%), SK하이닉스(+190%) 등 시총 상위 대형주 주도
  - 이들은 5월까지 underperform → 기존 momentum 전략 미포착
  - KOSPI가 추세 상승 시 시총 상위 종목을 보유하는 단순 전략이 최적

전략 로직:
  - Universe: stock_universe 시총 상위 30개 (market_cap 기준)
  - Entry: 월별 첫 거래일 + KOSPI > 20일 이평선 (추세 확인)
  - Exit: 추적손절 -12% from peak OR 기간 말
  - 목표: pre-computed strategies의 조기 이탈 문제 해결
"""
from __future__ import annotations
import sqlite3, json, uuid
from datetime import date, timedelta

DB = "/Volumes/Realtek_NVME/stock_dashboard/runtime/stock.db"
PERIOD_START = "2025-06-01"
PERIOD_END   = "2025-12-31"
TRAILING_STOP = -0.12
MA_WINDOW = 20
MAX_PICKS_PER_MONTH = 8


def get_trading_days(conn, start: str, end: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM price_history WHERE stock_code='005930' "
        "AND date BETWEEN ? AND ? ORDER BY date", (start, end)
    ).fetchall()
    return [r[0] for r in rows]


def get_kospi_ma(conn, day: str, window: int) -> tuple[float, float] | None:
    """KOSPI 현재가와 MA 반환"""
    rows = conn.execute(
        "SELECT date, close FROM price_history WHERE stock_code='^KS11' "
        "AND date <= ? AND close > 0 ORDER BY date DESC LIMIT ?",
        (day, window + 5)
    ).fetchall()
    if len(rows) < window:
        return None
    current = float(rows[0]["close"])
    ma = sum(float(r["close"]) for r in rows[:window]) / window
    return current, ma


def get_top_large_caps(conn, n: int = 30) -> list[str]:
    """시총 상위 N개 종목 코드 반환 (삼성전자 포함 보장)"""
    rows = conn.execute("""
        SELECT stock_code FROM stock_universe
        WHERE market_cap > 0
          AND stock_code NOT LIKE '%^%'
          AND stock_code NOT LIKE '%우%'
          AND stock_code NOT LIKE '%-F'
          AND market_cap < 1e18
        ORDER BY market_cap DESC
        LIMIT ?
    """, (n,)).fetchall()
    codes = [r[0] for r in rows]

    # 삼성전자 (005930) 강제 포함
    if "005930" not in codes:
        codes.insert(0, "005930")
    return codes[:n]


def get_prices_range(conn, code: str, start: str, end: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT date, close FROM price_history WHERE stock_code=? "
        "AND date BETWEEN ? AND ? AND close > 0 ORDER BY date",
        (code, start, end)
    ).fetchall()
    return {r["date"]: float(r["close"]) for r in rows}


def simulate_trade(code: str, prices: dict[str, float],
                   trading_days: list[str], entry_date: str) -> dict | None:
    avail = [d for d in trading_days if d >= entry_date and d in prices]
    if not avail:
        return None
    act_entry = avail[0]
    ep = prices[act_entry]
    if ep <= 0:
        return None

    peak = ep
    ex_date = avail[-1]
    ex_price = prices.get(ex_date, ep)

    for day in avail[1:]:
        p = prices.get(day)
        if not p:
            continue
        if p > peak:
            peak = p
        if (p / peak - 1.0) <= TRAILING_STOP:
            ex_date = day
            ex_price = p
            break
    else:
        ex_date = avail[-1]
        ex_price = prices.get(ex_date, ep)

    return {
        "stock_code": code,
        "entry_date": act_entry,
        "entry_price": ep,
        "exit_date": ex_date,
        "exit_price": ex_price,
        "return_pct": (ex_price / ep - 1.0) * 100,
    }


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    print(f"=== V_LARGE_TREND 신호 생성 ({PERIOD_START}~{PERIOD_END}) ===\n")

    trading_days = get_trading_days(conn, PERIOD_START, PERIOD_END)
    print(f"거래일: {len(trading_days)}일")

    large_caps = get_top_large_caps(conn, 30)
    print(f"대형주 유니버스: {len(large_caps)}개")
    # 삼성전자, SK하이닉스 포함 확인
    for code, name in [("005930","삼성전자"), ("000660","SK하이닉스"),
                       ("005380","현대차"), ("012450","한화에어로스페이스")]:
        mark = "✓" if code in large_caps else "✗"
        print(f"  {mark} {code}({name})")

    # 사전 가격 데이터 로드
    print("\n가격 데이터 로드 중...")
    all_prices: dict[str, dict[str, float]] = {}
    for code in large_caps:
        all_prices[code] = get_prices_range(conn, code, "2025-05-01", PERIOD_END)

    # 월별 진입 신호 생성
    trades = []
    months: dict[str, str] = {}
    for td in trading_days:
        ym = td[:7]
        if ym not in months:
            months[ym] = td

    print("\n월별 진입 처리:")
    for ym, first_day in sorted(months.items()):
        # KOSPI 추세 확인
        kospi_info = get_kospi_ma(conn, first_day, MA_WINDOW)
        if kospi_info is None:
            print(f"  {ym}: KOSPI MA 데이터 부족 — 스킵")
            continue

        kospi_now, kospi_ma = kospi_info
        trend_ok = kospi_now > kospi_ma
        trend_str = f"KOSPI={kospi_now:,.0f} MA20={kospi_ma:,.0f} ({'↑ OK' if trend_ok else '↓ SKIP'})"
        print(f"  {ym} ({first_day}): {trend_str}")

        if not trend_ok:
            continue  # 추세 하락 시 진입 안함

        # 이 달에 선정할 종목: 전월 대비 성과 상위 (KOSPI 상대 초과수익)
        lb_start = (date.fromisoformat(first_day) - timedelta(days=30)).isoformat()
        scored = []
        for code in large_caps:
            prices_code = all_prices[code]
            # 전월 가격
            prev_prices = [v for d, v in prices_code.items() if lb_start <= d < first_day]
            curr_price = prices_code.get(first_day)
            if not prev_prices or not curr_price:
                continue
            prev_p = prev_prices[0]
            ret_1m = (curr_price / prev_p - 1.0)
            # KOSPI 전월 수익
            kospi_row = conn.execute(
                "SELECT close FROM price_history WHERE stock_code='^KS11' AND date >= ? AND close>0 ORDER BY date LIMIT 1",
                (lb_start,)
            ).fetchone()
            kospi_prev = float(kospi_row["close"]) if kospi_row else kospi_now
            kospi_1m = (kospi_now / kospi_prev - 1.0)
            # 상대 수익
            rel = ret_1m - kospi_1m
            scored.append((rel, ret_1m, code))

        # 상위 MAX_PICKS_PER_MONTH 종목 선정 (절대 상위 기준으로도 보완)
        scored.sort(reverse=True)
        picks = [code for _, _, code in scored[:MAX_PICKS_PER_MONTH]]

        for code in picks:
            prices_code = all_prices[code]
            t = simulate_trade(code, prices_code, trading_days, first_day)
            if t:
                trades.append(t)
                print(f"    + {code:6s} {t['entry_date']}→{t['exit_date']}  "
                      f"{t['entry_price']:>9,.0f}→{t['exit_price']:>9,.0f}  "
                      f"{t['return_pct']:+.1f}%")

    # 결과 분석
    print(f"\n=== 최종 결과 ===")
    rets = [t["return_pct"] for t in trades]
    if rets:
        print(f"총 trades: {len(trades)}")
        print(f"평균 수익: {sum(rets)/len(rets):+.2f}%")
        print(f"승률: {sum(1 for r in rets if r > 0)/len(rets)*100:.1f}%")
        print(f"최고: {max(rets):+.1f}%  최저: {min(rets):+.1f}%")

        # 월별 수익
        by_month: dict[str, list[float]] = {}
        for t in trades:
            ym = t["entry_date"][:7]
            by_month.setdefault(ym, []).append(t["return_pct"])
        for ym, v in sorted(by_month.items()):
            print(f"  {ym}: {sum(v)/len(v):+.2f}% ({len(v)}건)")

        # 상위 종목
        top10 = sorted(trades, key=lambda x: x["return_pct"], reverse=True)[:10]
        print("\n=== Top 10 ===")
        for t in top10:
            name_row = conn.execute("SELECT stock_name FROM stock_universe WHERE stock_code=?", (t["stock_code"],)).fetchone()
            name = name_row["stock_name"] if name_row else ""
            print(f"  {t['stock_code']:6s} {name:12s}  {t['entry_date']}→{t['exit_date']}  "
                  f"{t['entry_price']:>10,.0f}→{t['exit_price']:>10,.0f}  {t['return_pct']:+.1f}%")

        # KOSPI 대비 초과수익 계산
        kospi_may = get_prices_range(conn, "^KS11", "2025-06-01", "2025-06-05")
        kospi_dec = get_prices_range(conn, "^KS11", "2025-12-28", "2025-12-31")
        if kospi_may and kospi_dec:
            k1 = list(kospi_may.values())[0]
            k2 = list(kospi_dec.values())[-1]
            kospi_ret = (k2/k1-1)*100
            print(f"\n  KOSPI 기간 수익: {kospi_ret:+.1f}%  vs  전략 평균: {sum(rets)/len(rets):+.2f}%")

    # DB 저장
    run_name = "AUTO v_largetrend 2025-06~12"
    conn.execute("DELETE FROM backtest_runs WHERE name=?", (run_name,))
    run_id = str(uuid.uuid4())[:8]
    total_ret = sum(rets) / len(rets) if rets else 0
    trades_json = json.dumps({"trades": trades}, ensure_ascii=False)
    conn.execute("""
        INSERT INTO backtest_runs
            (run_id, name, start_date, end_date, strategy, status, total_return_pct, trades_json)
        VALUES (?, ?, ?, ?, 'v_largetrend', 'done', ?, ?)
    """, (run_id, run_name, PERIOD_START, PERIOD_END, total_ret, trades_json))
    conn.commit()
    print(f"\n✅ DB 저장: run_id={run_id}, '{run_name}', {len(trades)}건, avg={total_ret:+.2f}%")
    conn.close()


if __name__ == "__main__":
    main()
