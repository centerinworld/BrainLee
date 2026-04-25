"""
scripts/backtest_finetune.py — Logic #6 파라미터 미세 조정기

optimizer_results.csv의 1위 파라미터를 중심으로 더 세밀한 그리드를 탐색한다.
3개 기간(A/B/C)으로 과적합 방지.

실행:
    cd /Applications/stock_dashboard
    python scripts/backtest_finetune.py [--csv scripts/optimizer_results.csv] [--top 10]
"""
from __future__ import annotations

import argparse
import csv
import itertools
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import backtest as _bt

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# 평가 기간 3개 (과적합 방지)
PERIOD_A = ("2022-01-01", "2023-06-30", "④2022~2023상 대하락")
PERIOD_B = ("2023-07-01", "2025-04-30", "⑤2023하~2025 AI랠리")
PERIOD_C = ("2020-01-01", "2021-12-31", "③2020~2021 코로나반등")


def _run_period(start, end, label, params):
    try:
        run_id = _bt.run_backtest(
            start_date=start, end_date=end,
            per_stock=10_000_000, max_positions=10,
            run_name=f"[FT] {label}", strategy="v7",
            _override_params=params,
        )
        import sqlite3
        DB_PATH = os.path.join(_ROOT, "stock.db")
        if not os.path.exists(DB_PATH):
            DB_PATH = "/Applications/stock_dashboard/stock.db"
        conn = sqlite3.connect(DB_PATH, timeout=30)
        row = conn.execute(
            "SELECT total_return_pct, max_drawdown_pct, win_rate, total_trades "
            "FROM backtest_runs WHERE run_id=?", (run_id,)
        ).fetchone()
        conn.execute("DELETE FROM backtest_runs WHERE run_id=?", (run_id,))
        conn.commit(); conn.close()
        if row:
            return {"total_return": float(row[0] or 0), "mdd": float(row[1] or 0),
                    "win_rate": float(row[2] or 0), "trades": int(row[3] or 0)}
    except Exception as e:
        logger.debug(f"failed: {e}")
    return {"total_return": -999, "mdd": -999, "win_rate": 0, "trades": 0}


def _score(ra, rb, rc):
    bonus_a = ra["total_return"] if ra["total_return"] > 0 else ra["total_return"] * 2
    bonus_c = rc["total_return"] * 0.5  # 코로나 반등 보너스
    mdd_p   = max(abs(ra["mdd"]), abs(rb["mdd"]), abs(rc["mdd"])) * 0.4
    return bonus_a * 0.35 + rb["total_return"] * 0.50 + bonus_c * 0.15 - mdd_p


def _load_best(csv_path: str) -> Dict:
    """optimizer_results.csv에서 1위 파라미터를 읽는다."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = sorted(reader, key=lambda r: float(r["score"]), reverse=True)
    if not rows:
        raise ValueError("CSV가 비어 있습니다")
    best = rows[0]
    print(f"\n1위 파라미터 로드: score={best['score']}, "
          f"A={best['ret_a']}%, B={best['ret_b']}%")
    return {k: float(v) for k, v in best.items()
            if k in ("overseas_w","tech_w","value_w","hs_w","emp_w",
                     "thr_bull","thr_neutral","thr_bear",
                     "pen_low_bnd","pen_high_bnd","pen_low_add","pen_high_add",
                     "sec_weight","vol_strong","vol_mild")}


def _fine_grid(best: Dict) -> List[Dict]:
    """best 파라미터 주변 ±1단계 미세 그리드를 생성한다."""
    def _r(v, step, n=3):
        return sorted(set(round(v + step * i, 4) for i in range(-n//2, n//2+1)))

    overseas_opts = _r(best["overseas_w"], 0.02)
    tech_opts     = _r(best["tech_w"],     0.02)
    thr_bull_opts = _r(best["thr_bull"],   0.02)
    thr_neut_opts = _r(best["thr_neutral"],0.02)
    thr_bear_opts = _r(best["thr_bear"],   0.02)
    sec_w_opts    = _r(best["sec_weight"], 0.05)
    pen_lo_opts   = [(best["pen_low_bnd"], best["pen_high_bnd"],
                      best["pen_low_add"] + d1,
                      best["pen_high_add"] + d2)
                     for d1, d2 in [(0,0),(-0.02,0),(0.02,0),(0,-0.01),(0,0.01)]]

    combos = []
    for ov, te, tb, tn, tbr, sw, (plb, phb, pla, pha) in itertools.product(
        overseas_opts, tech_opts, thr_bull_opts, thr_neut_opts,
        thr_bear_opts, sec_w_opts, pen_lo_opts
    ):
        # 나머지 가중치는 비례 분배 유지
        remainder = 1.0 - ov - te
        if remainder < 0.10 or remainder > 0.50:
            continue
        va  = best["value_w"] / (best["value_w"] + best["hs_w"] + best["emp_w"])
        hsa = best["hs_w"]    / (best["value_w"] + best["hs_w"] + best["emp_w"])
        ema = best["emp_w"]   / (best["value_w"] + best["hs_w"] + best["emp_w"])
        p = {
            "overseas_w":  round(ov, 3),
            "tech_w":      round(te, 3),
            "value_w":     round(remainder * va, 3),
            "hs_w":        round(remainder * hsa, 3),
            "emp_w":       round(remainder * ema, 3),
            "thr_bull":    round(max(0.40, tb), 3),
            "thr_neutral": round(max(0.45, tn), 3),
            "thr_bear":    round(max(0.50, tbr), 3),
            "sec_weight":  round(max(0.5, min(0.9, sw)), 3),
            "pen_low_bnd": round(plb, 2),
            "pen_high_bnd":round(phb, 2),
            "pen_low_add": round(max(0, pla), 3),
            "pen_high_add":round(max(0, pha), 3),
            "vol_strong":  best["vol_strong"],
            "vol_mild":    best["vol_mild"],
        }
        combos.append(p)
    return combos


def run_finetune(csv_path: str, top_n: int = 10) -> None:
    _bt.init_backtest_db()
    best = _load_best(csv_path)
    combos = _fine_grid(best)
    total = len(combos)

    out_dir = Path(__file__).parent
    ft_csv = out_dir / "finetune_results.csv"
    ft_txt = out_dir / "finetune_best.txt"

    print(f"\n{'='*60}")
    print(f"  미세 조정  —  {total}개 조합  (3기간 평가)")
    print(f"{'='*60}\n")

    results = []
    fieldnames = ["rank","score","ret_a","mdd_a","ret_b","mdd_b","ret_c","mdd_c",
                  "overseas_w","tech_w","value_w","hs_w","emp_w",
                  "thr_bull","thr_neutral","thr_bear","sec_weight",
                  "pen_low_bnd","pen_high_bnd","pen_low_add","pen_high_add",
                  "vol_strong","vol_mild"]

    with open(ft_csv, "w", newline="", encoding="utf-8") as csvf:
        writer = csv.DictWriter(csvf, fieldnames=fieldnames)
        writer.writeheader()
        for idx, params in enumerate(combos, 1):
            ra = _run_period(*PERIOD_A, params)
            rb = _run_period(*PERIOD_B, params)
            rc = _run_period(*PERIOD_C, params)
            s  = _score(ra, rb, rc)
            row = {"rank":0, "score":round(s,3),
                   "ret_a":round(ra["total_return"],1), "mdd_a":round(ra["mdd"],1),
                   "ret_b":round(rb["total_return"],1), "mdd_b":round(rb["mdd"],1),
                   "ret_c":round(rc["total_return"],1), "mdd_c":round(rc["mdd"],1),
                   **{k:round(v,4) for k,v in params.items()}}
            results.append(row)
            writer.writerow(row)
            csvf.flush()
            print(f"  [{idx:4d}/{total}] A:{ra['total_return']:+6.1f}% "
                  f"B:{rb['total_return']:+6.1f}% C:{rc['total_return']:+6.1f}% "
                  f"score:{s:+6.2f}")

    results.sort(key=lambda r: r["score"], reverse=True)
    for i, r in enumerate(results, 1):
        r["rank"] = i

    with open(ft_csv, "w", newline="", encoding="utf-8") as csvf:
        writer = csv.DictWriter(csvf, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    with open(ft_txt, "w", encoding="utf-8") as f:
        f.write(f"Logic #6 미세 조정 결과 — 상위 {top_n}개\n")
        f.write(f"{'='*80}\n")
        for r in results[:top_n]:
            f.write(
                f"#{r['rank']:3d}  score:{r['score']:+6.2f}  "
                f"A:{r['ret_a']:+6.1f}%/{r['mdd_a']:.1f}%  "
                f"B:{r['ret_b']:+6.1f}%/{r['mdd_b']:.1f}%  "
                f"C:{r['ret_c']:+6.1f}%/{r['mdd_c']:.1f}%\n"
            )
            f.write(
                f"     ov={r['overseas_w']:.3f} te={r['tech_w']:.3f} "
                f"va={r['value_w']:.3f} hs={r['hs_w']:.3f} em={r['emp_w']:.3f} "
                f"thr={r['thr_bull']:.2f}/{r['thr_neutral']:.2f}/{r['thr_bear']:.2f} "
                f"sw={r['sec_weight']:.2f}\n\n"
            )

    print(f"\n{'='*60}")
    print(f"  미세 조정 완료")
    print(f"  CSV: {ft_csv}")
    print(f"  TXT: {ft_txt}")
    with open(ft_txt) as f:
        print(f.read())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Logic #6 미세 조정")
    parser.add_argument("--csv", default="scripts/optimizer_results.csv")
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    run_finetune(csv_path=args.csv, top_n=args.top)
