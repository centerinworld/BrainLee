#!/usr/bin/env python3
"""W5 분포 통계(2026-10-13 재실행분) → strategy_w5_distribution 표.

scripts/ops/load_w5_distribution_20261008.py와 동일 로직, 입력 접두사만 w5a_20261013/w5b_20261013로 갱신.
입력: research_outputs/w5a_20261013_summary.json, w5b_20261013_summary.json(analyze_w5a_20261008.py --prefix로 생성)
사용: python3 scripts/ops/load_w5_distribution_20261013.py [--label w5_20261013]
"""
import argparse, json, sys
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
    ap = argparse.ArgumentParser(); ap.add_argument("--label", default="w5_20261013"); a = ap.parse_args()
    conn = connect_primary_db(timeout=120)
    conn.execute(DDL)
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for prefix in ("w5a_20261013", "w5b_20261013"):
        summ_path = ROOT / "research_outputs" / f"{prefix}_summary.json"
        summ = json.loads(summ_path.read_text())
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
