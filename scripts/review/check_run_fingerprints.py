#!/usr/bin/env python3
"""측정 run 묶음의 코드·데이터 지문 고정 확인 — 읽기 전용(REVIEW_PLAN §28-2 ①②, 2026-10-07).

`backtest_run_specs.parameter_json`에는 run마다 이미
  _code_fingerprint      : 실행 순간 backtest.py·backtest_common.py·전략 파일 등의 **파일 내용** sha256(미커밋 변경 포함)
  _source_snapshot.revision_fingerprint : 데이터 지문(재무·가격·마스터 행 수·최종 수정 시각)
  _data_revision_extras  : 기업행위 계수·data_fix_log·공시일·PIT 표 버전
가 남는다. 이 스크립트는 이름 패턴·시작 시각으로 묶은 run들에서
  ① 코드 지문이 한 값인지 + 지금 작업본 파일·git HEAD(커밋본)와 같은지
  ② 데이터 지문이 한 값인지(갈렸으면 지문별 run 수 — 같은 지문 안의 분포만 사용)
를 판정하고, 결과를 `--out`(기본 research_outputs/run_fingerprints/<라벨>.json)에 남긴다.
사용: check_run_fingerprints.py --name-like 'w2_sel_%' --since '2026-10-07 13:31:20' --label w5_selection_dist_20261007
"""
import argparse
import hashlib
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402


def _file_hash(rel):
    p = ROOT / rel
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.is_file() else None


def _head_hash(rel):
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"HEAD:{rel}"], capture_output=True)
    return hashlib.sha256(r.stdout).hexdigest()[:16] if r.returncode == 0 else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name-like", required=True)
    ap.add_argument("--since", required=True)
    ap.add_argument("--label", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    conn = connect_primary_db(readonly=True)
    rows = [tuple(r) for r in conn.execute(
        """SELECT r.run_id, r.name, r.status, r.created_at, s.git_commit, s.parameter_json
           FROM backtest_runs r LEFT JOIN backtest_run_specs s USING(run_id)
           WHERE r.name LIKE ? AND r.created_at >= ? ORDER BY r.created_at""", (a.name_like, a.since)).fetchall()]
    code_fp, data_fp, extras_fp = Counter(), Counter(), Counter()
    by_data = defaultdict(list)
    files = {}
    no_spec = []
    for rid, name, status, created, git, pj in rows:
        if not pj:
            no_spec.append(f"{rid} {name} {status}")
            continue
        p = json.loads(pj)
        cf = p.get("_code_fingerprint") or {}
        for k, v in cf.items():
            files.setdefault(k, Counter())[v] += 1
        # 전략 파일은 run마다 다르므로 공통 파일만으로 코드 지문을 만든다
        common = {k: v for k, v in cf.items() if not k.startswith("backtest_strategies/")}
        code_fp[json.dumps(common, sort_keys=True)] += 1
        rf = (p.get("_source_snapshot") or {}).get("revision_fingerprint")
        data_fp[rf] += 1
        extras_fp[json.dumps(p.get("_data_revision_extras"), sort_keys=True)] += 1
        by_data[rf].append(rid)
    file_check = {}
    for rel, cnt in sorted(files.items()):
        rec = list(cnt)
        file_check[rel] = {"recorded": rec, "worktree_now": _file_hash(rel), "git_head": _head_hash(rel),
                           "worktree_same_as_recorded": len(rec) == 1 and rec[0] == _file_hash(rel),
                           "head_same_as_recorded": len(rec) == 1 and rec[0] == _head_hash(rel)}
    res = {
        "checked_at": datetime.now().isoformat(timespec="seconds"), "label": a.label,
        "filter": {"name_like": a.name_like, "since": a.since},
        "runs": len(rows), "status": dict(Counter(r[2] for r in rows)),
        "git_commit_recorded": dict(Counter(r[4] for r in rows)),
        "runs_without_spec": no_spec,
        "code_fingerprint_variants": len(code_fp), "code_fingerprint": json.loads(next(iter(code_fp))) if len(code_fp) == 1 else None,
        "files": file_check,
        "data_fingerprint_variants": dict(data_fp), "data_extras_variants": len(extras_fp),
        "verdict": {
            "code_single": len(code_fp) <= 1,
            "code_equals_head": all(v["head_same_as_recorded"] for v in file_check.values()) if file_check else None,
            "data_single": len(data_fp) <= 1 and len(extras_fp) <= 1,
        },
    }
    out = Path(a.out) if a.out else ROOT / "research_outputs" / "run_fingerprints" / f"{a.label}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    hist = out.with_suffix(".history.jsonl")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    with open(hist, "a") as f:
        f.write(json.dumps({"at": res["checked_at"], "runs": res["runs"], **res["verdict"],
                            "data": res["data_fingerprint_variants"]}, ensure_ascii=False) + "\n")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
