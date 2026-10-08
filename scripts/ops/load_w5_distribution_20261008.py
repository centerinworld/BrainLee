#!/usr/bin/env python3
"""W5 분포 통계 → strategy_w5_distribution 표 (REVIEW_PLAN §34-4 ①, 2026-10-08).

매트릭스(routes/backtest.get_backtest_matrix)가 이 표가 있으면 구간 수익률을 'W5 무작위 12회 중앙값'(D14)으로 바꾸고,
이전 선택 run 값은 pre_correction_return_pct로 보존한다(D9). 순서 고정 전략은 단일 run 값.
입력: research_outputs/w5a_20261008_summary.json, w5b_20261008_summary.json(analyze_w5a_20261008.py 출력)
      + 각 묶음 원자료(대표 run_id·데이터 지문). 같은 (w5_label, strategy, period_label)은 덮어쓴다.
사용: python3 scripts/ops/load_w5_distribution_20261008.py [--label w5_20261008]
"""
import argparse, json, statistics as st, sys
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

DDL = """CREATE TABLE IF NOT EXISTS strategy_w5_distribution (
  w5_label TEXT, strategy TEXT, period_label TEXT,
  median_return_pct DOUBLE PRECISION, q25_return_pct DOUBLE PRECISION,
  min_return_pct DOUBLE PRECISION, max_return_pct DOUBLE PRECISION, mdd_median_pct DOUBLE PRECISION,
  score_return_pct DOUBLE PRECISION, code_return_pct DOUBLE PRECISION, n_random INTEGER, order_note TEXT,
  data_fingerprint TEXT, representative_run_id TEXT, created_at TEXT,
  PRIMARY KEY (w5_label, strategy, period_label))"""


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--label", default="w5_20261008"); a = ap.parse_args()
    conn = connect_primary_db(timeout=120)
    conn.execute(DDL)
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for prefix in ("w5a_20261008", "w5b_20261008"):
        summ = json.loads((ROOT / "research_outputs" / f"{prefix}_summary.json").read_text())
        for strat, per in summ.items():
            raw = json.loads((ROOT / "research_outputs" / f"{prefix}_{strat}.json").read_text())
            for lab, v in per.items():
                if lab.startswith("_"):
                    continue
                runs = raw["runs"][lab]
                rep = runs.get("fixed") or runs.get("score")
                fp = (rep.get("data_fp") or {}).get("source_snapshot") if rep else None
                conn.execute("""INSERT INTO strategy_w5_distribution VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT (w5_label, strategy, period_label) DO UPDATE SET
                      median_return_pct=EXCLUDED.median_return_pct, q25_return_pct=EXCLUDED.q25_return_pct,
                      min_return_pct=EXCLUDED.min_return_pct, max_return_pct=EXCLUDED.max_return_pct,
                      mdd_median_pct=EXCLUDED.mdd_median_pct, score_return_pct=EXCLUDED.score_return_pct,
                      code_return_pct=EXCLUDED.code_return_pct, n_random=EXCLUDED.n_random, order_note=EXCLUDED.order_note,
                      data_fingerprint=EXCLUDED.data_fingerprint, representative_run_id=EXCLUDED.representative_run_id,
                      created_at=EXCLUDED.created_at""",
                    (a.label, strat, lab, v["median"], v.get("q25"), v.get("min"), v.get("max"), v.get("mdd_median"),
                     v.get("score"), v.get("code"), v.get("n_random"), v.get("order_note"), fp,
                     rep.get("run_id") if rep else None, now))
                n += 1
    conn.commit()
    print("적재", n, "행 →", a.label)


if __name__ == "__main__":
    main()
