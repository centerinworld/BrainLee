#!/usr/bin/env python3
"""조정계수 이중 적용 수정(2026-09-24, backtest_common._load_corp_action_factors) 이후
turnaround / regime_adaptive / composite 를 표준 6기간으로 재백테스트하고 수정 전 결과와 비교한다.

실행: cd runtime && set -a; . ./.env; set +a; PYTHONPATH=runtime_pg_bootstrap:. nice -n 10 venv/bin/python3 scripts/rerun_adjfactor_backtests_20260924.py
산출: docs/backtest_rerun_adjfactor_20260924.md, run 은 backtest_runs 에 새 행으로 저장(수정 전 행은 그대로 보존).
"""
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db_compat import connect_primary_db  # noqa: E402
import backtest as _bt  # noqa: E402

PERIODS = [
    ("2020-03-01", "2021-11-30", "상승장"),
    ("2021-12-01", "2022-10-31", "하락장"),
    ("2022-11-01", "2023-10-31", "회복장"),
    ("2023-11-01", "2024-12-31", "AI랠리"),
    ("2024-06-01", "2025-05-31", "최근"),
    ("2025-06-01", "2026-03-31", "최신"),
]
STRATS = [
    ("turnaround", "V-TURNAROUND 흑자전환", _bt.run_backtest_turnaround, {}),
    ("regime_adaptive", "Meta-V 레짐 적응형", _bt.run_backtest_regime_adaptive, {}),
    ("composite", "V11 복합스코어링", _bt.run_backtest_composite, {"score_threshold": 60}),
]
START_TS = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def before(conn, strat, s, e):
    r = conn.execute(
        "SELECT total_return_pct, win_rate, total_trades, max_drawdown_pct FROM backtest_runs "
        "WHERE strategy=? AND start_date=? AND end_date=? AND status='done' AND created_at < ? "
        "ORDER BY created_at DESC LIMIT 1", (strat, s, e, START_TS)).fetchone()
    return r


def main():
    rows = []
    for key, label, fn, extra in STRATS:
        for s, e, pname in PERIODS:
            name = f"{label} {pname} 재검증(조정계수수정) {s[:7]}~{e[:7]}"
            t0 = time.time()
            print(f"[{key}] {pname} {s}~{e} ...", flush=True)
            try:
                run_id = fn(s, e, per_stock=10_000_000, run_name=name, **extra)
                c = connect_primary_db(timeout=60)
                a = c.execute("SELECT total_return_pct, win_rate, total_trades, max_drawdown_pct, status "
                              "FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
                b = before(c, key, s, e)
                c.close()
                rows.append((key, pname, b, a))
                print(f"   -> {a[0]}% trades={a[2]} status={a[4]} ({time.time()-t0:.0f}s) | before={b[0] if b else None}", flush=True)
            except Exception as ex:
                rows.append((key, pname, None, None))
                print(f"   !! 실패: {ex}", flush=True)
    out = ["# 조정계수 이중 적용 수정 후 재백테스트 (2026-09-24)", "",
           "수정 전 = 같은 전략·기간의 직전 완료 run(`backtest_runs`), 수정 후 = 이번 재실행. per_stock=1천만원, 표준 6기간.", "",
           "| 전략 | 기간 | 수정 전 수익률 | 수정 후 수익률 | Δ | 수정 후 승률 | 거래수 | MDD |", "|---|---|--:|--:|--:|--:|--:|--:|"]
    for key, pname, b, a in rows:
        if a is None:
            out.append(f"| {key} | {pname} | - | 실패 | | | | |"); continue
        bp = f"{b[0]:.1f}%" if b and b[0] is not None else "-"
        d = f"{a[0]-b[0]:+.1f}%p" if b and b[0] is not None and a[0] is not None else "-"
        out.append(f"| {key} | {pname} | {bp} | {a[0]:.1f}% | {d} | {a[1] if a[1] is not None else '-'} | {a[2]} | {a[3]} |")
    (ROOT / "docs" / "backtest_rerun_adjfactor_20260924.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("DONE -> docs/backtest_rerun_adjfactor_20260924.md")


if __name__ == "__main__":
    main()
