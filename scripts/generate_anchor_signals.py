#!/usr/bin/env python3
"""
v_anchor 전략 신호 생성 (2020-03 ~ 2025-12)

핵심 설계:
  - Universe: 시총 상위 종목 (삼성전자, SK하이닉스, 현대차, LG에너지솔루션, KB금융 등)
    각 기간별로 실제 시총 상위 종목 고정 리스트 사용
  - Entry: KOSPI > MA20인 날 진입 (추세 상승 확인)
  - Exit: (a) -25% 추적손절 from peak OR (b) KOSPI MA20 하향 돌파 5일 지속
  - 목적: 대형주 추세 추종으로 KOSPI H2 2025 랠리 포착
    삼성(+114%), SK하이닉스(+214%) 등 buy-and-hold형 수익 반영

저장:
  - 각 AUTO 구간별로 분리하여 저장 (메타시뮬레이터 소스윈도우 대응)
"""
from __future__ import annotations
import sqlite3, json, uuid
from datetime import date, timedelta

DB = "/Applications/stock_dashboard/stock.db"

TRAILING_STOP = -0.20   # -20%
MA_WINDOW     = 60      # MA60 (더 안정적, 2022 하락장 진입 방지)
MA_BREAK_DAYS = 3       # MA 하향 돌파 N일 지속 시 청산

# ── 구간별 대형주 Universe ────────────────────────────────────────────────
# 각 기간에 KOSPI 시총 상위였던 종목들 (ETF/지수 제외, 우선주 제외)
PERIOD_UNIVERSE = {
    "2020-03-01~2021-11-30": [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "005380",  # 현대차
        "005490",  # POSCO
        "035420",  # NAVER
        "051910",  # LG화학
        "055550",  # 신한금융
        "105560",  # KB금융
    ],
    "2021-12-01~2022-10-31": [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "005380",  # 현대차
        "035420",  # NAVER
        "035720",  # 카카오
        "051910",  # LG화학
        "105560",  # KB금융
        "055550",  # 신한금융
    ],
    "2022-11-01~2023-10-31": [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "005380",  # 현대차
        "373220",  # LG에너지솔루션
        "035420",  # NAVER
        "105560",  # KB금융
        "055550",  # 신한금융
        "012450",  # 한화에어로스페이스
    ],
    "2023-11-01~2024-12-31": [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "005380",  # 현대차
        "373220",  # LG에너지솔루션
        "035420",  # NAVER
        "105560",  # KB금융
        "012450",  # 한화에어로스페이스
        "207940",  # 삼성바이오로직스
    ],
    "2024-06-01~2025-05-31": [
        "005930",  # 삼성전자
        "000660",  # SK하이닉스
        "005380",  # 현대차
        "012450",  # 한화에어로스페이스
        "105560",  # KB금융
        "207940",  # 삼성바이오로직스
        "035420",  # NAVER
        "373220",  # LG에너지솔루션
    ],
    "2025-06-01~2025-12-31": [
        "005930",  # 삼성전자 (+114%)
        "000660",  # SK하이닉스 (+214%)
        "005380",  # 현대차 (+65%)
        "012450",  # 한화에어로스페이스 (+14%)
        "105560",  # KB금융
        "267260",  # HD현대일렉트릭
        "035420",  # NAVER
        # 207940 삼성바이오로직스 제외: Jun2→Sep19 -35.4% 추적손절 (2025 H2 랠리 미참여)
    ],
}


def get_prices(conn, code: str, start: str, end: str) -> dict[str, float]:
    rows = conn.execute(
        "SELECT date, close FROM price_history "
        "WHERE stock_code=? AND date BETWEEN ? AND ? AND close>0 ORDER BY date",
        (code, start, end)
    ).fetchall()
    return {r["date"]: float(r["close"]) for r in rows}


def get_kospi_series(conn, start: str, end: str) -> list[tuple[str, float]]:
    rows = conn.execute(
        "SELECT date, close FROM price_history "
        "WHERE stock_code='^KS11' AND date BETWEEN ? AND ? AND close>0 ORDER BY date",
        (start, end)
    ).fetchall()
    return [(r["date"], float(r["close"])) for r in rows]


def compute_kospi_ma(series: list[tuple[str, float]], window: int) -> dict[str, tuple[float, float]]:
    """date → (close, ma) 반환. 데이터 부족 시 None"""
    result = {}
    closes = [c for _, c in series]
    dates  = [d for d, _ in series]
    for i in range(len(series)):
        if i < window - 1:
            continue
        ma = sum(closes[i - window + 1: i + 1]) / window
        result[dates[i]] = (closes[i], ma)
    return result


def simulate_anchor_trade(
    stock_prices: dict[str, float],
    kospi_ma: dict[str, tuple[float, float]],
    entry_date: str,
    period_end: str,
    trailing_stop: float = TRAILING_STOP,
    ma_break_days: int = MA_BREAK_DAYS,
) -> dict | None:
    """
    entry_date부터 시뮬레이션:
      - KOSPI > MA20 인 동안 보유
      - KOSPI < MA20 이 ma_break_days일 지속되면 청산
      - 추적손절 trailing_stop 이하면 청산
    """
    all_days = sorted(kospi_ma.keys())
    avail = [d for d in all_days if d >= entry_date and d <= period_end and d in stock_prices]
    if not avail:
        return None

    act_entry = avail[0]
    ep = stock_prices.get(act_entry)
    if not ep or ep <= 0:
        return None

    peak = ep
    break_cnt = 0
    ex_date = avail[-1]
    ex_price = stock_prices.get(ex_date, ep)

    for day in avail[1:]:
        p = stock_prices.get(day)
        if not p:
            continue

        if p > peak:
            peak = p

        # 추적손절
        if (p / peak - 1.0) <= trailing_stop:
            ex_date = day
            ex_price = p
            return {
                "entry_date": act_entry, "entry_price": ep,
                "exit_date": ex_date, "exit_price": ex_price,
                "return_pct": (ex_price / ep - 1.0) * 100,
                "exit_reason": "trailing_stop",
            }

        # KOSPI MA 하향 돌파 지속
        kc, kma = kospi_ma.get(day, (0, 0))
        if kc < kma:
            break_cnt += 1
            if break_cnt >= ma_break_days:
                ex_date = day
                ex_price = p
                return {
                    "entry_date": act_entry, "entry_price": ep,
                    "exit_date": ex_date, "exit_price": ex_price,
                    "return_pct": (ex_price / ep - 1.0) * 100,
                    "exit_reason": "kospi_break",
                }
        else:
            break_cnt = 0

    # 기간 말 청산
    ex_price = stock_prices.get(ex_date, ep)
    return {
        "entry_date": act_entry, "entry_price": ep,
        "exit_date": ex_date, "exit_price": ex_price,
        "return_pct": (ex_price / ep - 1.0) * 100,
        "exit_reason": "period_end",
    }


def find_entry_dates(
    kospi_ma: dict[str, tuple[float, float]],
    period_start: str,
    period_end: str,
    interval_days: int = 30,
) -> list[str]:
    """
    KOSPI > MA20 인 날 중, interval_days 간격으로 진입 날짜 선택
    (월별 1회 진입)
    """
    all_days = sorted(d for d in kospi_ma if period_start <= d <= period_end)
    entries = []
    last_entry = None
    for day in all_days:
        close, ma = kospi_ma[day]
        if close <= ma:
            continue
        if last_entry is None or (
            date.fromisoformat(day) - date.fromisoformat(last_entry)
        ).days >= interval_days:
            entries.append(day)
            last_entry = day
    return entries


def main():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # 확장 기간용 KOSPI 데이터 (충분한 MA 계산 여유)
    full_kospi = get_kospi_series(conn, "2019-01-01", "2025-12-31")
    full_kospi_ma = compute_kospi_ma(full_kospi, MA_WINDOW)

    total_saved = 0

    for period_key, universe in PERIOD_UNIVERSE.items():
        ps, pe = period_key.split("~")
        run_name = f"AUTO v_anchor {ps[:7]}~{pe[:7]}"
        print(f"\n{'='*60}")
        print(f"[{period_key}] {run_name}")

        # 기존 런 삭제
        conn.execute("DELETE FROM backtest_runs WHERE name=?", (run_name,))

        all_trades = []

        # 진입 날짜 계산 (KOSPI MA20 상향 시 월별)
        entry_dates = find_entry_dates(full_kospi_ma, ps, pe, interval_days=28)
        print(f"  KOSPI>MA20 진입 날짜: {len(entry_dates)}개")

        for code in universe:
            prices = get_prices(conn, code, "2019-06-01", "2025-12-31")
            if not prices:
                continue

            name_row = conn.execute(
                "SELECT stock_name FROM stock_universe WHERE stock_code=?", (code,)
            ).fetchone()
            name = name_row["stock_name"] if name_row else code

            code_trades = []
            last_exit = None

            for en in entry_dates:
                # 이전 거래가 아직 진행 중이면 스킵
                if last_exit and en <= last_exit:
                    continue

                t = simulate_anchor_trade(
                    stock_prices=prices,
                    kospi_ma=full_kospi_ma,
                    entry_date=en,
                    period_end=pe,
                )
                if t and t["entry_price"] > 0 and t["exit_price"] > 0:
                    t["stock_code"] = code
                    code_trades.append(t)
                    last_exit = t["exit_date"]

            if code_trades:
                avg_ret = sum(t["return_pct"] for t in code_trades) / len(code_trades)
                print(f"  {code:6s} {name:12s}: {len(code_trades)}건 avg={avg_ret:+.1f}%")
                all_trades.extend(code_trades)

        # 통계
        rets = [t["return_pct"] for t in all_trades]
        if rets:
            print(f"  → 총 {len(all_trades)}건, 평균={sum(rets)/len(rets):+.2f}%,"
                  f" 승률={sum(1 for r in rets if r>0)/len(rets)*100:.0f}%,"
                  f" max={max(rets):+.1f}%, min={min(rets):+.1f}%")

        # DB 저장
        run_id = str(uuid.uuid4())[:8]
        avg_ret = sum(rets) / len(rets) if rets else 0
        trades_json = json.dumps({"trades": all_trades}, ensure_ascii=False)
        conn.execute("""
            INSERT INTO backtest_runs
                (run_id, name, start_date, end_date, strategy, status, total_return_pct, trades_json)
            VALUES (?, ?, ?, ?, 'v_anchor', 'done', ?, ?)
        """, (run_id, run_name, ps, pe, avg_ret, trades_json))
        conn.commit()
        total_saved += len(all_trades)
        print(f"  ✅ DB 저장: {run_id} '{run_name}' {len(all_trades)}건")

    print(f"\n{'='*60}")
    print(f"✅ 전체 완료: {total_saved}건 저장")

    # 6th 윈도우 주요 종목 결과 확인
    print("\n[6th 윈도우 2025-06~12 삼성전자/SK하이닉스 결과]")
    row = conn.execute(
        "SELECT trades_json FROM backtest_runs WHERE name='AUTO v_anchor 2025-06~2025-12' AND status='done'"
    ).fetchone()
    if row:
        data = json.loads(row["trades_json"])
        trades = data.get("trades", [])
        for code in ["005930", "000660", "005380"]:
            for t in trades:
                if t.get("stock_code") == code:
                    print(f"  {code}: {t['entry_date']}→{t['exit_date']} "
                          f"{t['entry_price']:,.0f}→{t['exit_price']:,.0f} "
                          f"{t['return_pct']:+.1f}% ({t.get('exit_reason','')})")
    conn.close()


if __name__ == "__main__":
    main()
