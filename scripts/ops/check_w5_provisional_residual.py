#!/usr/bin/env python3
"""W5 시작 직전 점검 — 날짜 컷에 안 걸린 잔여 잠정 행이 W5 유니버스와 겹치는지(REVIEW_PLAN §27-2 ①, 2026-10-07). 읽기 전용.

자체 `price_history` 조회를 쓰는 전략 30개는 잠정 '날짜 컷'만 받고 개별 잔여 행은 그대로 읽는다(`backtest_common.provisional_rows`는
공용 로더·일반 엔진에만). 그래서 W5 직전에:
  1) 날짜 컷 날짜(그날 잠정 ≥ PROVISIONAL_DATE_CUT_RATIO)와 그 앞 잔여 잠정 행 수
  2) 잔여 행 종목 중 W5 유니버스(마스터상 그날 거래 가능한 코스피·코스닥 일반주, ETF·ETN·우선주 제외)에 든 것
판정: 겹침 0 → W5 진행 가능 / 1 이상 → KRX 반영(check_price_vs_krx_daily.py 교체) 뒤로 W5 연기.
W5 구간 끝(기본 = 선택 suite 6구간의 가장 늦은 끝 날짜, `--max-date`로 지정)보다 뒤 날짜의 잔여 행은 W5가 읽지 않으므로
겹침에서 빼고 `outside_w5_periods`로만 센다(2026-10-08 — 10-06 잔여 86행이 3월 말에 끝나는 W5를 막던 오판 수정).
종료 코드: 0 = 진행 가능, 2 = 연기. 결과 한 줄을 research_outputs/w5_precheck/history.jsonl에 누적.
"""
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from backtest_common import PROVISIONAL_DATE_CUT_RATIO, _provisional_dates  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

OUT = ROOT / "research_outputs" / "w5_precheck"


def _w5_max_end():
    try:
        from scripts.rerun_all_after_audit_rebuild import _all_selected_specs
        return max(s["end"] for v in _all_selected_specs(None).values() for s in v)[:10]
    except Exception:
        return "9999-12-31"


def main():
    import argparse
    ap = argparse.ArgumentParser(); ap.add_argument("--max-date", default=None); a = ap.parse_args()
    max_date = a.max_date or _w5_max_end()
    conn = connect_primary_db(readonly=True)
    dates = _provisional_dates(conn) or {}
    cut = sorted(d for d, (n, t) in dates.items() if t and n / t >= PROVISIONAL_DATE_CUT_RATIO)
    first_cut = cut[0] if cut else "9999-12-31"
    residual = [tuple(r) for r in conn.execute(
        "SELECT stock_code, date FROM price_provisional_rows WHERE date < ? ORDER BY date, stock_code", (first_cut,)).fetchall()]
    overlap = []
    outside = [f"{c} {str(d)[:10]}" for c, d in residual if str(d)[:10] > max_date]
    for code, d in [(c, d) for c, d in residual if str(d)[:10] <= max_date]:
        hit = conn.execute(
            """SELECT 1 FROM security_master_history WHERE stock_code=? AND effective_from<=? AND (effective_to IS NULL OR ?<effective_to)
               AND is_tradable=1 AND is_etf_etn=0 AND market IN ('KOSPI','KOSDAQ')
               AND (security_type IS NULL OR security_type!='preferred') LIMIT 1""", (code, str(d)[:10], str(d)[:10])).fetchone()
        if hit:
            overlap.append(f"{code} {str(d)[:10]}")
    res = {"at": datetime.now().isoformat(timespec="seconds"),
           "cut_dates": {d: f"{dates[d][0]}/{dates[d][1]}" for d in cut},
           "residual_rows": len(residual), "residual_sample": [f"{c} {str(d)[:10]}" for c, d in residual[:20]],
           "w5_max_date": max_date, "outside_w5_periods": len(outside),
           "w5_universe_overlap": overlap, "verdict": "ok" if not overlap else "postpone_until_krx"}
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "history.jsonl", "a") as f:
        f.write(json.dumps(res, ensure_ascii=False) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return 0 if not overlap else 2


if __name__ == "__main__":
    sys.exit(main())
