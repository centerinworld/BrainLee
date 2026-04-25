"""
scripts/backtest_optimizer.py — Logic #6 (v7) 파라미터 최적화기

두 핵심 기간에 백테스트를 반복 실행하며 최적 파라미터 조합을 탐색한다.
  - 기간 A: 2022-01 ~ 2023-06 (금리인상 대하락)  ← 손실 방어 테스트
  - 기간 B: 2023-07 ~ 2025-04 (AI랠리)           ← 수익 극대화 테스트

탐색 파라미터:
  [1] 팩터 가중치 (overseas / tech / value / hs / emp) — 합계 1.0
  [2] 국면별 임계값 (강세/중립/약세)
  [3] 글로벌 패널티 강도 (off / mild / strong)
  [4] ov_global 섹터/글로벌 혼합비 (sec_weight)
  [5] 거래량 요건 (vol_mult: 1.3 / 1.5 / 2.0)
  [6] RSI 범위 (rsi_mid: 45 / 50 / 55)

실행:
    cd /Applications/stock_dashboard
    python scripts/backtest_optimizer.py [--top 20] [--quick]

결과:
    scripts/optimizer_results.csv — 전체 결과 CSV
    scripts/optimizer_best.txt   — 상위 20개 요약 출력
"""
from __future__ import annotations

import argparse
import csv
import itertools
import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

# backtest.py가 있는 경로를 sys.path에 추가
_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import backtest as _bt

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── 평가 기간 ────────────────────────────────────────────────────────────────
PERIOD_A = ("2022-01-01", "2023-06-30", "④2022~2023상 대하락")
PERIOD_B = ("2023-07-01", "2025-04-30", "⑤2023하~2025 AI랠리")

# ─── 탐색 그리드 ──────────────────────────────────────────────────────────────
# [overseas_w, tech_w, value_w, hs_w, emp_w] — 합계 1.0만 허용
WEIGHT_SETS: List[Tuple[float, float, float, float, float]] = [
    # (overseas, tech, value, hs, emp)  — all sum to 1.0
    (0.25, 0.30, 0.22, 0.15, 0.08),  # baseline light overseas
    (0.28, 0.30, 0.20, 0.14, 0.08),
    (0.30, 0.28, 0.17, 0.15, 0.10),  # current default
    (0.30, 0.30, 0.17, 0.15, 0.08),
    (0.32, 0.28, 0.17, 0.15, 0.08),
    (0.35, 0.28, 0.17, 0.12, 0.08),
    (0.35, 0.30, 0.15, 0.12, 0.08),
    (0.30, 0.32, 0.17, 0.13, 0.08),
    (0.28, 0.32, 0.20, 0.13, 0.07),
    (0.25, 0.35, 0.20, 0.13, 0.07),
]

# 국면별 임계값 (강세3, 중립2, 약세1)
THRESHOLD_SETS: List[Tuple[float, float, float]] = [
    (0.50, 0.55, 0.62),  # 원래 (공격형)
    (0.52, 0.57, 0.64),  # 현재 default
    (0.54, 0.59, 0.66),  # 보수형
    (0.56, 0.61, 0.68),  # 초보수형
    (0.50, 0.57, 0.64),  # 강세만 느슨하게
    (0.52, 0.59, 0.66),  # 중간+
    (0.48, 0.55, 0.62),  # 강세 매우 공격적
    (0.50, 0.56, 0.65),
]

# 글로벌 패널티: (low_boundary, high_boundary, low_add, high_add)
# ov_global < high_boundary → +high_add, < low_boundary → +low_add
PENALTY_SETS: List[Tuple[float, float, float, float]] = [
    (0.0,  0.0,   0.00, 0.00),  # 패널티 없음
    (1.0,  1.3,   0.10, 0.05),  # 현재 default
    (1.0,  1.2,   0.08, 0.04),  # 약한 패널티
    (1.0,  1.5,   0.12, 0.06),  # 강한 패널티
    (1.1,  1.3,   0.10, 0.05),  # 경계 높임
    (0.9,  1.3,   0.12, 0.06),  # 경계 낮춤
]

# ov_sec : ov_global 혼합비 (overseas = sec*sec_w + global*(1-sec_w))
SEC_WEIGHTS: List[float] = [0.60, 0.65, 0.70, 0.75]

# 거래량 배수 임계값 (v7 _is_buy_signal_v7 vol_mult_strong, vol_mult_mild)
VOL_MULTS: List[Tuple[float, float]] = [
    (2.0, 1.3),  # 현재 default
    (1.8, 1.2),  # 약간 느슨
    (2.5, 1.5),  # 엄격
    (1.5, 1.1),  # 더 느슨
]


def _score(ret_a: float, ret_b: float, mdd_a: float, mdd_b: float) -> float:
    """최적화 목적함수.
    목표: 기간A 손실 최소화 + 기간B 수익 극대화 + MDD 관리
    - 기간A가 양수면 보너스, 음수면 페널티
    - 기간B 수익률 × 가중
    - MDD 큰 쪽 페널티
    """
    bonus_a = ret_a if ret_a > 0 else ret_a * 2  # 손실엔 2배 페널티
    mdd_penalty = max(abs(mdd_a), abs(mdd_b)) * 0.5
    return bonus_a * 0.4 + ret_b * 0.6 - mdd_penalty


def _run_period(start: str, end: str, label: str, params: Dict[str, Any]) -> Dict[str, float]:
    """단일 기간 백테스트 실행 후 결과 반환."""
    try:
        run_id = _bt.run_backtest(
            start_date=start,
            end_date=end,
            per_stock=10_000_000,
            max_positions=10,
            run_name=f"[OPT] {label}",
            strategy="v7",
            _override_params=params,
        )
        import sqlite3
        DB_PATH = os.path.join(_ROOT, "stock.db")
        if not os.path.exists(DB_PATH):
            DB_PATH = "/Applications/stock_dashboard/stock.db"
        conn = sqlite3.connect(DB_PATH, timeout=30)
        row = conn.execute(
            "SELECT total_return_pct, max_drawdown_pct, ann_return_pct, win_rate, total_trades "
            "FROM backtest_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        conn.execute("DELETE FROM backtest_runs WHERE run_id=?", (run_id,))
        conn.commit()
        conn.close()
        if row:
            return {
                "total_return": float(row[0] or 0),
                "mdd": float(row[1] or 0),
                "cagr": float(row[2] or 0),
                "win_rate": float(row[3] or 0),
                "trades": int(row[4] or 0),
            }
    except Exception as e:
        logger.debug(f"run failed: {e}")
    return {"total_return": -999, "mdd": -999, "cagr": -999, "win_rate": 0, "trades": 0}


def _build_params(
    weights: Tuple,
    thresholds: Tuple,
    penalty: Tuple,
    sec_w: float,
    vol_mult: Tuple,
) -> Dict[str, Any]:
    return {
        "overseas_w":    weights[0],
        "tech_w":        weights[1],
        "value_w":       weights[2],
        "hs_w":          weights[3],
        "emp_w":         weights[4],
        "thr_bull":      thresholds[0],
        "thr_neutral":   thresholds[1],
        "thr_bear":      thresholds[2],
        "pen_low_bnd":   penalty[0],
        "pen_high_bnd":  penalty[1],
        "pen_low_add":   penalty[2],
        "pen_high_add":  penalty[3],
        "sec_weight":    sec_w,
        "vol_strong":    vol_mult[0],
        "vol_mild":      vol_mult[1],
    }


def run_optimization(top_n: int = 20, quick: bool = False) -> None:
    _bt.init_backtest_db()

    out_dir = Path(__file__).parent
    csv_path = out_dir / "optimizer_results.csv"
    txt_path = out_dir / "optimizer_best.txt"

    # 그리드 구성
    weight_sets   = WEIGHT_SETS
    thresh_sets   = THRESHOLD_SETS
    penalty_sets  = PENALTY_SETS
    sec_weights   = SEC_WEIGHTS
    vol_mults     = VOL_MULTS

    if quick:
        # 빠른 검증: 핵심 조합만
        weight_sets  = WEIGHT_SETS[:4]
        thresh_sets  = THRESHOLD_SETS[:4]
        penalty_sets = PENALTY_SETS[:3]
        sec_weights  = [0.65, 0.70]
        vol_mults    = VOL_MULTS[:2]

    combos = list(itertools.product(weight_sets, thresh_sets, penalty_sets, sec_weights, vol_mults))
    total = len(combos)
    print(f"\n{'='*65}")
    print(f"  Logic #6 파라미터 최적화  —  총 {total}개 조합")
    print(f"  기간A: {PERIOD_A[0]}~{PERIOD_A[1]}")
    print(f"  기간B: {PERIOD_B[0]}~{PERIOD_B[1]}")
    print(f"{'='*65}\n")

    results: List[Dict] = []

    with open(csv_path, "w", newline="", encoding="utf-8") as csvf:
        fieldnames = [
            "rank", "score",
            "ret_a", "mdd_a", "trades_a",
            "ret_b", "mdd_b", "trades_b",
            "overseas_w", "tech_w", "value_w", "hs_w", "emp_w",
            "thr_bull", "thr_neutral", "thr_bear",
            "pen_low_bnd", "pen_high_bnd", "pen_low_add", "pen_high_add",
            "sec_weight", "vol_strong", "vol_mild",
        ]
        writer = csv.DictWriter(csvf, fieldnames=fieldnames)
        writer.writeheader()

        for idx, (w, th, pen, sw, vm) in enumerate(combos, 1):
            params = _build_params(w, th, pen, sw, vm)
            t0 = time.time()

            ra = _run_period(*PERIOD_A, params)
            rb = _run_period(*PERIOD_B, params)

            s = _score(ra["total_return"], rb["total_return"], ra["mdd"], rb["mdd"])

            row = {
                "rank": 0,
                "score": round(s, 3),
                "ret_a": round(ra["total_return"], 1),
                "mdd_a": round(ra["mdd"], 1),
                "trades_a": ra["trades"],
                "ret_b": round(rb["total_return"], 1),
                "mdd_b": round(rb["mdd"], 1),
                "trades_b": rb["trades"],
                **{k: round(v, 4) if isinstance(v, float) else v for k, v in params.items()},
            }
            results.append(row)
            writer.writerow(row)
            csvf.flush()

            elapsed = time.time() - t0
            eta = elapsed * (total - idx)
            print(
                f"  [{idx:4d}/{total}] "
                f"A:{ra['total_return']:+6.1f}% "
                f"B:{rb['total_return']:+6.1f}% "
                f"score:{s:+6.2f}  "
                f"eta:{eta/60:.0f}분  "
                f"w={w[0]:.2f}/{w[1]:.2f}/{w[2]:.2f} "
                f"thr={th[0]:.2f}/{th[1]:.2f}/{th[2]:.2f}"
            )

    # 정렬 및 상위 출력
    results.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    # CSV 재작성 (순위 포함)
    with open(csv_path, "w", newline="", encoding="utf-8") as csvf:
        writer = csv.DictWriter(csvf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    # 텍스트 요약
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"Logic #6 파라미터 최적화 결과 — 상위 {top_n}개\n")
        f.write(f"기간A: {PERIOD_A[0]}~{PERIOD_A[1]}  기간B: {PERIOD_B[0]}~{PERIOD_B[1]}\n")
        f.write(f"{'='*80}\n")
        f.write(
            f"{'순위':>4}  {'점수':>6}  "
            f"{'A수익':>7}  {'A_MDD':>6}  "
            f"{'B수익':>7}  {'B_MDD':>6}  "
            f"{'overseas':>8}  {'tech':>5}  {'value':>5}  {'hs':>5}  {'emp':>4}  "
            f"{'thr강세':>6}  {'thr중립':>6}  {'thr약세':>6}  "
            f"{'sec_w':>5}  {'pen_low':>7}  {'pen_hi':>7}\n"
        )
        f.write(f"{'-'*80}\n")
        for r in results[:top_n]:
            f.write(
                f"{r['rank']:>4}  {r['score']:>+6.2f}  "
                f"{r['ret_a']:>+6.1f}%  {r['mdd_a']:>5.1f}%  "
                f"{r['ret_b']:>+6.1f}%  {r['mdd_b']:>5.1f}%  "
                f"{r['overseas_w']:>8.2f}  {r['tech_w']:>5.2f}  {r['value_w']:>5.2f}  "
                f"{r['hs_w']:>5.2f}  {r['emp_w']:>4.2f}  "
                f"{r['thr_bull']:>6.2f}  {r['thr_neutral']:>6.2f}  {r['thr_bear']:>6.2f}  "
                f"{r['sec_weight']:>5.2f}  "
                f"{r['pen_low_bnd']:>4.1f}→+{r['pen_low_add']:.2f}  "
                f"{r['pen_high_bnd']:>4.1f}→+{r['pen_high_add']:.2f}\n"
            )

    print(f"\n\n{'='*65}")
    print(f"  최적화 완료. 상위 {top_n}개:")
    print(f"{'='*65}")
    with open(txt_path) as f:
        print(f.read())

    print(f"\n  결과 CSV: {csv_path}")
    print(f"  요약 TXT: {txt_path}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Logic #6 파라미터 최적화")
    parser.add_argument("--top", type=int, default=20, help="상위 N개 출력")
    parser.add_argument("--quick", action="store_true", help="빠른 검증 (핵심 조합만)")
    args = parser.parse_args()
    run_optimization(top_n=args.top, quick=args.quick)
