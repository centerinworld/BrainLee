#!/usr/bin/env python3
"""W5a(REVIEW_PLAN §32) 결과 분석 — research_outputs/w5a_20261008_<전략>.json → 전략×구간 분포표(마크다운)와 요약 JSON.

판정 기준(D14): 주 수치 = 무작위 12회 중앙값, 함께 하위25%·범위·MDD 중앙. score(점수 순)는 재현용 참고값 — 분포 내 위치만 표시.
같은 데이터 지문(v2: fingerprint_version + 가격 합계 정확값 + extras) 안의 run만 센다. 지문이 갈리면 표에 '지문 n종'을 적는다.
사용: analyze_w5a_20261008.py [--prefix w5a_20261008|w5b_20261008] [--out …_summary.json]
"""
import argparse, glob, json, statistics as st, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def q(v, p):
    v = sorted(v); k = (len(v) - 1) * p; f = int(k); c = min(f + 1, len(v) - 1)
    return v[f] + (v[c] - v[f]) * (k - f)


def _rank(rets, v):
    below = sum(1 for x in rets if x < v - 1e-9); ties = sum(1 for x in rets if abs(x - v) <= 1e-9)
    return f"{below}/{len(rets)}" + (f"(+같음 {ties})" if ties else "")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--prefix", default="w5a_20261008")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    a.out = a.out or str(ROOT / "research_outputs" / f"{a.prefix}_summary.json")
    from db_compat import connect_primary_db
    files = sorted(glob.glob(str(ROOT / "research_outputs" / f"{a.prefix}_*.json")))
    files = [f for f in files if not f.endswith("_summary.json")]
    data = {Path(f).stem.replace(f"{a.prefix}_", ""): json.load(open(f)) for f in files}
    ids = [r["run_id"] for d in data.values() for per in d["runs"].values() for r in per.values()]
    c = connect_primary_db(timeout=60, readonly=True)
    spec = {}
    for i in range(0, len(ids), 200):
        ch = ids[i:i + 200]
        for rid, pj in c.execute(f"select run_id, parameter_json from backtest_run_specs where run_id in ({','.join('?'*len(ch))})", tuple(ch)).fetchall():
            p = json.loads(pj)
            spec[rid] = {"data": json.dumps({"d": (p.get("_source_snapshot") or {}).get("datasets"), "x": p.get("_data_revision_extras")}, sort_keys=True, default=str),
                         "code": json.dumps(p.get("_code_fingerprint"), sort_keys=True)}
    c.close()
    rows, summ = [], {}
    for strat, d in sorted(data.items()):
        per_strat = summ.setdefault(strat, {})
        codes = {spec.get(r["run_id"], {}).get("code") for per in d["runs"].values() for r in per.values()}
        for lab in sorted(d["runs"], key=lambda x: (x[:2], x)):
            runs = d["runs"][lab]
            fps = {spec.get(r["run_id"], {}).get("data") for r in runs.values()}
            ok = [r for r in runs.values() if r.get("status") == "done"]
            if "fixed" in runs:
                r = runs["fixed"]
                rows.append((strat, lab, "—", f"{r['ret']:+.1f}", 1, f"{r['ret']:+.1f}", "—", "—", f"{r['mdd']:.1f}" if r.get('mdd') is not None else "—",
                             "순위 고정(분포 없음)", len(fps)))
                per_strat[lab] = {"median": r["ret"], "q25": None, "min": r["ret"], "max": r["ret"], "mdd_median": r.get("mdd"),
                                  "score": r["ret"], "n_random": 0, "order_note": "fixed_rank", "data_fp_kinds": len(fps)}
                continue
            rnd = [r for k, r in runs.items() if k.startswith("random:") and r.get("status") == "done"]
            rets = [r["ret"] for r in rnd]; mdds = [r["mdd"] for r in rnd if r.get("mdd") is not None]
            if len(rets) < 3:
                rows.append((strat, lab, "—", "—", len(rets), "", "", "", "", "무작위 run 부족", len(fps))); continue
            cc, ss = runs["code"]["ret"], runs["score"]["ret"]
            same = max(rets) - min(rets) <= 1e-9 and abs(cc - rets[0]) <= 1e-9 and abs(ss - rets[0]) <= 1e-9
            pos = "동일(순서 무관)" if same else f"{_rank(rets, cc)}·{_rank(rets, ss)}"
            rows.append((strat, lab, f"{cc:+.1f}", f"{ss:+.1f}", len(rets), f"{st.median(rets):+.1f}", f"{q(rets, .25):+.1f}",
                         f"{min(rets):+.1f}~{max(rets):+.1f}", f"{st.median(mdds):.1f}" if mdds else "—", pos, len(fps)))
            per_strat[lab] = {"median": st.median(rets), "q25": q(rets, .25), "min": min(rets), "max": max(rets),
                              "mdd_median": st.median(mdds) if mdds else None, "score": ss, "code": cc, "n_random": len(rets),
                              "order_note": "insensitive" if same else "sensitive", "data_fp_kinds": len(fps)}
        per_strat["_code_fp_kinds"] = len(codes)
    print("| 전략 | 구간 | code | score | 무작위 n | 중앙값 | 하위25% | 범위 | MDD중앙 | 분포 내 위치 code·score | 데이터 지문 종류 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print("| " + " | ".join(str(x) for x in r) + " |")
    print()
    print("| 전략 | 6구간 중앙값 평균 | 6구간 하위25% 평균 | 최악 구간 중앙값 | 코드 지문 종류 |")
    print("|---|---|---|---|---|")
    for strat, per in sorted(summ.items()):
        labs = [v for k, v in per.items() if not k.startswith("_")]
        meds = [v["median"] for v in labs]; q25s = [v["q25"] for v in labs if v["q25"] is not None]
        print(f"| {strat} | {st.mean(meds):+.1f} | {(st.mean(q25s) if q25s else float('nan')):+.1f} | {min(meds):+.1f} | {per['_code_fp_kinds']} |")
    all_fp = set()
    for d in data.values():
        for per in d["runs"].values():
            for r in per.values():
                all_fp.add(spec.get(r["run_id"], {}).get("data"))
    print(f"\n전체 run {len(ids)}건, 구간을 섞은 데이터 지문 문자열 종류 {len(all_fp)} (구간마다 기간이 달라 구간 수 이상은 정상)")
    Path(a.out).write_text(json.dumps(summ, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
