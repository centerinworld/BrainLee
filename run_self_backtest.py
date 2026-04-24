"""
run_self_backtest.py — Logic #6 자동 백테스트 실행기

여러 기간에 걸쳐 v7(Logic #6) 백테스트를 자동 실행하고
결과를 backtest_runs 테이블에 저장한다.

실행:
    python run_self_backtest.py [--strategy v7] [--quick]

기간 구성 (2025-05 이전 기준):
    ① 2010-01 ~ 2015-12  장기 강세장 + 2011 유럽위기
    ② 2016-01 ~ 2019-12  박스권 + 반도체 슈퍼사이클
    ③ 2020-01 ~ 2021-12  코로나 충격 + 강한 반등
    ④ 2022-01 ~ 2023-06  금리인상 대하락 + 회복
    ⑤ 2023-07 ~ 2025-04  AI/반도체 랠리 ~ 현재
    ⑥ 2018-01 ~ 2025-04  통합 장기 (비교 기준)
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import time
from pathlib import Path

import backtest as _bt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DB_PATH = "/Applications/stock_dashboard/stock.db"

PERIODS = [
    ("2010-01-01", "2015-12-31", "①2010~2015 강세+유럽위기"),
    ("2016-01-01", "2019-12-31", "②2016~2019 박스권+반도체"),
    ("2020-01-01", "2021-12-31", "③2020~2021 코로나충격+반등"),
    ("2022-01-01", "2023-06-30", "④2022~2023상 금리인상대하락"),
    ("2023-07-01", "2025-04-30", "⑤2023하~2025 AI랠리"),
    ("2018-01-01", "2025-04-30", "⑥2018~2025 장기통합"),
]

QUICK_PERIODS = [
    ("2022-01-01", "2023-06-30", "④2022~2023상 금리인상대하락"),
    ("2023-07-01", "2025-04-30", "⑤2023하~2025 AI랠리"),
]


def _print_result(run_id: str) -> None:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    row = conn.execute(
        "SELECT name, total_return_pct, ann_return_pct, win_rate, "
        "total_trades, profit_trades, max_drawdown_pct, summary_text "
        "FROM backtest_runs WHERE run_id=?", (run_id,)
    ).fetchone()
    conn.close()
    if not row:
        logger.warning(f"  run_id={run_id} 결과 없음")
        return
    name, tr, ar, wr, tt, pt, mdd, summary = row
    print(f"\n{'='*60}")
    print(f"  {name}")
    print(f"  총수익률: {tr:+.1f}%  연환산: {ar:+.1f}%")
    print(f"  승률: {wr:.1f}%  ({pt}/{tt}거래)")
    print(f"  MDD: {mdd:.1f}%")
    if summary:
        for line in summary.split("\n")[:5]:
            print(f"  {line}")


def run_all(strategy: str = "v7", periods: list | None = None, per_stock: float = 10_000_000) -> None:
    _bt.init_backtest_db()
    periods = periods or PERIODS
    logger.info(f"자동 백테스트 시작 — 전략={strategy}, {len(periods)}개 기간")

    for start, end, label in periods:
        logger.info(f"\n▶ {label} ({start} ~ {end})")
        try:
            run_id = _bt.run_backtest(
                start_date=start,
                end_date=end,
                per_stock=per_stock,
                max_positions=10,
                run_name=f"[AUTO][{strategy}] {label}",
                strategy=strategy,
            )
            _print_result(run_id)
            time.sleep(2)   # DB 안정화
        except Exception as e:
            logger.error(f"  실패: {e}")

    # 전략별 비교 요약 출력
    print(f"\n\n{'='*60}")
    print(f"  [전략 {strategy}] 전체 기간 요약")
    print(f"{'='*60}")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    rows = conn.execute(
        "SELECT name, total_return_pct, ann_return_pct, max_drawdown_pct "
        "FROM backtest_runs WHERE name LIKE ? ORDER BY created_at DESC",
        (f"[AUTO][{strategy}]%",)
    ).fetchall()
    conn.close()
    print(f"  {'기간':40s} {'총수익':>8s} {'연환산':>8s} {'MDD':>8s}")
    print(f"  {'-'*40} {'-'*8} {'-'*8} {'-'*8}")
    for name, tr, ar, mdd in rows[:len(periods)]:
        label_part = name.replace(f"[AUTO][{strategy}] ", "")
        print(f"  {label_part:40s} {tr:+7.1f}% {ar:+7.1f}% {mdd:7.1f}%")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Logic #6 자동 백테스트")
    parser.add_argument("--strategy",  default="v7", choices=["v5","v6","v7"])
    parser.add_argument("--quick",     action="store_true", help="빠른 검증용 2개 기간만")
    parser.add_argument("--per-stock", type=float, default=10_000_000)
    args = parser.parse_args()

    periods = QUICK_PERIODS if args.quick else PERIODS
    run_all(strategy=args.strategy, periods=periods, per_stock=args.per_stock)
