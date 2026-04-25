"""
scripts/apply_best_params.py — 최적 파라미터를 backtest.py에 영구 적용

finetune_results.csv (없으면 optimizer_results.csv)에서 1위를 읽어
backtest.py의 _is_buy_signal_v7 기본값을 업데이트한다.

실행:
    cd /Applications/stock_dashboard
    python scripts/apply_best_params.py [--csv scripts/finetune_results.csv] [--dry-run]
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
_BT   = _ROOT / "backtest.py"


def _load_best(csv_path: str) -> dict:
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = sorted(reader, key=lambda r: float(r["score"]), reverse=True)
    if not rows:
        raise ValueError("CSV가 비어 있습니다")
    b = rows[0]
    print(f"\n1위 파라미터: score={b['score']}")
    print(f"  A:{b.get('ret_a','?')}%  B:{b.get('ret_b','?')}%  C:{b.get('ret_c','?')}%")
    return {k: float(v) for k, v in b.items()
            if k in ("overseas_w","tech_w","value_w","hs_w","emp_w",
                     "thr_bull","thr_neutral","thr_bear",
                     "pen_low_bnd","pen_high_bnd","pen_low_add","pen_high_add",
                     "sec_weight","vol_strong","vol_mild")}


def _update_backtest(params: dict, dry_run: bool) -> None:
    src = _BT.read_text(encoding="utf-8")

    # 각 기본값을 정규식으로 교체
    replacements = [
        (r'overseas_w = p\.get\("overseas_w",\s*[\d.]+\)',
         f'overseas_w = p.get("overseas_w",  {params["overseas_w"]})'),
        (r'tech_w\s+= p\.get\("tech_w",\s*[\d.]+\)',
         f'tech_w     = p.get("tech_w",      {params["tech_w"]})'),
        (r'value_w\s+= p\.get\("value_w",\s*[\d.]+\)',
         f'value_w    = p.get("value_w",     {params["value_w"]})'),
        (r'hs_w\s+= p\.get\("hs_w",\s*[\d.]+\)',
         f'hs_w       = p.get("hs_w",        {params["hs_w"]})'),
        (r'emp_w\s+= p\.get\("emp_w",\s*[\d.]+\)',
         f'emp_w      = p.get("emp_w",        {params["emp_w"]})'),
        (r'sec_weight = p\.get\("sec_weight",\s*[\d.]+\)',
         f'sec_weight = p.get("sec_weight",  {params["sec_weight"]})'),
        (r'vol_strong = p\.get\("vol_strong",\s*[\d.]+\)',
         f'vol_strong = p.get("vol_strong",  {params["vol_strong"]})'),
        (r'vol_mild\s+= p\.get\("vol_mild",\s*[\d.]+\)',
         f'vol_mild   = p.get("vol_mild",    {params["vol_mild"]})'),
        (r'thr_bull\s+= p\.get\("thr_bull",\s*[\d.]+\)',
         f'thr_bull   = p.get("thr_bull",    {params["thr_bull"]})'),
        (r'thr_neut\s+= p\.get\("thr_neutral",\s*[\d.]+\)',
         f'thr_neut   = p.get("thr_neutral", {params["thr_neutral"]})'),
        (r'thr_bear\s+= p\.get\("thr_bear",\s*[\d.]+\)',
         f'thr_bear   = p.get("thr_bear",    {params["thr_bear"]})'),
        (r'pen_lo_bnd = p\.get\("pen_low_bnd",\s*[\d.]+\)',
         f'pen_lo_bnd = p.get("pen_low_bnd",  {params["pen_low_bnd"]})'),
        (r'pen_hi_bnd = p\.get\("pen_high_bnd",\s*[\d.]+\)',
         f'pen_hi_bnd = p.get("pen_high_bnd", {params["pen_high_bnd"]})'),
        (r'pen_lo_add = p\.get\("pen_low_add",\s*[\d.]+\)',
         f'pen_lo_add = p.get("pen_low_add",  {params["pen_low_add"]})'),
        (r'pen_hi_add = p\.get\("pen_high_add",\s*[\d.]+\)',
         f'pen_hi_add = p.get("pen_high_add", {params["pen_high_add"]})'),
    ]

    new_src = src
    for pattern, replacement in replacements:
        new_src, n = re.subn(pattern, replacement, new_src)
        if n == 0:
            print(f"  ⚠️  패턴 불일치: {pattern[:60]}")
        else:
            print(f"  ✓  {replacement[:60]}")

    if dry_run:
        print("\n[DRY RUN] 변경 내용 미리보기 — 실제 파일은 수정하지 않음")
        # diff 출력
        old_lines = src.splitlines()
        new_lines = new_src.splitlines()
        for i, (a, b) in enumerate(zip(old_lines, new_lines)):
            if a != b:
                print(f"  L{i+1} - {a.strip()}")
                print(f"  L{i+1} + {b.strip()}")
    else:
        _BT.write_text(new_src, encoding="utf-8")
        print(f"\n  backtest.py 업데이트 완료")
        print(f"  git add backtest.py && git commit -m 'Apply optimized v7 params'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="최적 파라미터 적용")
    parser.add_argument("--csv", default="scripts/finetune_results.csv",
                        help="결과 CSV (없으면 optimizer_results.csv 사용)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    csv_path = args.csv
    if not Path(csv_path).exists():
        alt = "scripts/optimizer_results.csv"
        if Path(alt).exists():
            print(f"  {csv_path} 없음 → {alt} 사용")
            csv_path = alt
        else:
            print(f"  ERROR: {csv_path} 없음")
            sys.exit(1)

    params = _load_best(csv_path)
    _update_backtest(params, dry_run=args.dry_run)
