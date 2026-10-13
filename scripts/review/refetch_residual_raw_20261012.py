#!/usr/bin/env python3
"""2023~26 잔여 칸의 DART 원문 다시 받기 (2026-10-12) — 우리가 받은 뒤 정정공시로 값이 바뀌었는지 확인.

대상: residual_cause_groups 최신 csv 중 외화·DB 없음 제외 칸의 (종목, 회계연도→DART 보고서, fs). 4분기 손익은 연간·3분기 보고서 둘 다.
동작: OpenDART fnlttSinglAcntAll 재요청 → 기존 파일과 다르면 기존 파일을 `<이름>.prev_20261012.json.gz`로 남기고 새 원문 저장.
출력: research_outputs/financial_rereview_20261002/refetch_residual_raw_20261012.json (바뀐 파일·값 차이 요약). DB는 건드리지 않는다.
"""
import csv, gzip, json, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "scripts" / "review"))
import fin_norm as fn  # noqa: E402
import dart_keys  # noqa: E402
import fetch_dart_cashflow_20261002 as fd  # noqa: E402
from financial_rereview_20261002 import extract  # noqa: E402
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"


def main():
    src = sorted(OUT.glob("residual_cause_groups_field_verif_*.csv"))[-1]
    keys = set()
    for x in csv.DictReader(open(src)):
        if x["group"] in ("FX", "DB_MISSING_OFS", "DB_MISSING_CFS", "REIT"):
            continue
        code, y, q, fs = x["stock_code"], int(x["year"]), int(x["quarter"]), x["fs"]
        rf = fn.effective_fs(code, y, fs)
        for qq in ((0, 3) if q == 4 else (q,)):
            by, bq = fn.dart_key_for_fiscal(code, y, qq) if qq in (1, 2, 3) else (y, 0)
            keys.add((code, by, bq, rf))
    corp = json.load(open(ROOT / "data_cache" / "dart_corp_codes.json"))
    key = dart_keys.ordered_keys()[0]
    res = {"src": src.name, "requested": len(keys), "changed": [], "same": 0, "new": [], "nodata": [], "fail": []}
    for code, by, bq, rf in sorted(keys):
        if code not in corp:
            res["fail"].append((code, by, bq, rf, "corp")); continue
        rows, st = fd.fetch(key, corp[code], by, bq, rf)
        if rows is None:
            res["fail"].append((code, by, bq, rf, st)); continue
        if not rows:
            res["nodata"].append((code, by, bq, rf, st)); continue
        p = fn.RAW_DART / code / f"{by}_{bq}_{rf}.json.gz"
        old = json.load(gzip.open(p)) if p.exists() else None
        if old is None:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False).encode())); res["new"].append((code, by, bq, rf))
        else:
            eo, en = extract(old), extract(rows)
            diff = {k: (eo.get(k), en.get(k)) for k in set(eo) | set(en) if eo.get(k) != en.get(k)}
            if diff:
                p.with_name(p.name.replace(".json.gz", ".prev_20261012.json.gz")).write_bytes(p.read_bytes())
                p.write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False).encode()))
                res["changed"].append((code, by, bq, rf, diff))
            else:
                res["same"] += 1
        time.sleep(0.4)
    (OUT / "refetch_residual_raw_20261012.json").write_text(json.dumps(res, ensure_ascii=False, indent=0, default=str))
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in res.items()}, ensure_ascii=False))


if __name__ == "__main__":
    main()
