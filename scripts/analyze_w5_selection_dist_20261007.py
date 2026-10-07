#!/usr/bin/env python3
"""§26-2 ①: w5_selection_dist_20261007_*.json → 전략×구간 분포표(마크다운). 같은 데이터 지문 안의 run만 유효로 센다."""
import glob, json, statistics as st, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
files = sorted(glob.glob(str(ROOT / "research_outputs" / "w5_selection_dist_20261007_*.json")))
def q(v, p):
    v = sorted(v); k = (len(v) - 1) * p; f = int(k); c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)
def _norm(x):
    # 지문의 가격 합계(SUM(close) 등)는 병렬 집계 순서 때문에 소수 넷째 자리에서 흔들린다(14:29~14:37 실측: 건수·날짜·거래량 합 동일, 합계만 ±0.0001).
    # 정수로 반올림해 비교한다 — 값이 실제로 바뀐 경우(원 단위 이상)는 그대로 잡힌다.
    if isinstance(x, float): return round(x)
    if isinstance(x, list): return [_norm(i) for i in x]
    if isinstance(x, dict): return {k: _norm(v) for k, v in x.items()}
    return x
_SPEC = {}
def _load_specs(run_ids):
    import os; sys.path.insert(0, str(ROOT))
    from db_compat import connect_primary_db
    c = connect_primary_db(timeout=60)
    ids = list(run_ids)
    for i in range(0, len(ids), 200):
        ch = ids[i:i + 200]
        for rid, pj in c.execute(f"select run_id, parameter_json from backtest_run_specs where run_id in ({','.join('?'*len(ch))})", tuple(ch)).fetchall():
            p = json.loads(pj); _SPEC[rid] = json.dumps(_norm({"d": (p.get("_source_snapshot") or {}).get("datasets"), "x": p.get("_data_revision_extras")}), sort_keys=True)
    c.close()
def fp(r):
    return _SPEC.get(r["run_id"]) or r.get("run_id")
rows = []; summary = []
_all = [json.load(open(f)) for f in files]
_load_specs(r['run_id'] for d in _all for per in d['strategies'].values() for lab in per.values() for r in lab.values())
for f, d in zip(files, _all):
    for strat, per in d["strategies"].items():
        all_fp = {fp(r) for lab in per.values() for r in lab.values()}
        for lab, runs in sorted(per.items()):
            rnd = [r for k, r in runs.items() if k.startswith("random:") and r["status"] == "done"]
            ref = fp(runs["score"]) if "score" in runs else None
            valid = [r for r in rnd if fp(r) == ref]
            rets = [r["ret"] for r in valid]; mdds = [r["mdd"] for r in valid]
            if len(rets) < 3:
                rows.append((strat, lab, "—", "—", len(rets), "", "", "", "", "")); continue
            c, s = runs["code"]["ret"], runs["score"]["ret"]
            rank_c = sum(1 for x in rets if x < c); rank_s = sum(1 for x in rets if x < s)
            rows.append((strat, lab, f"{c:+.1f}", f"{s:+.1f}", len(rets), f"{st.median(rets):+.1f}", f"{q(rets,.25):+.1f}",
                         f"{min(rets):+.1f}~{max(rets):+.1f}", f"{st.median(mdds):.1f}", f"{rank_c}/{len(rets)}·{rank_s}/{len(rets)}"))
        summary.append((strat, len(all_fp)))
print("| 전략 | 구간 | code(옛) | score | 무작위 n | 중앙값 | 하위25% | 범위 | MDD중앙 | 분포 내 순위 code·score |")
print("|---|---|---|---|---|---|---|---|---|---|")
for r in rows:
    print("| " + " | ".join(str(x) for x in r) + " |")
print()
print("데이터 지문 종류(1이면 전 run 동일):", dict(summary))
