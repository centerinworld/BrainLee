#!/usr/bin/env python3
"""C2 항등식 실패 원인인 분기 원문 미수집 43개 조합 수집.
targets: /tmp/dart_fetch_targets_20261014.json (code, dart_year, dart_quarter, fs)
2026-10-14
"""
from __future__ import annotations

import gzip
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))

import config
import requests
from financial_rereview_20261002 import Dart, REPRT

RAW_DART = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
TARGETS_FILE = Path("/tmp/dart_fetch_targets_20261014.json")
SYSTEM_STATUS = {"010", "011", "012", "021", "100", "101", "800", "900", "901"}


def fetch_one(key: str, corp_code: str, bsns_year: int, q: int, fs: str):
    for attempt in range(4):
        try:
            d = requests.get(
                "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                params={"crtfc_key": key, "corp_code": corp_code,
                        "bsns_year": str(bsns_year), "reprt_code": REPRT[q], "fs_div": fs},
                timeout=25,
            ).json()
        except Exception as e:
            print(f"  network error: {e}, retry {attempt+1}", flush=True)
            time.sleep(3 + attempt * 3)
            continue
        st = d.get("status")
        if st == "020":
            return None, "quota"
        if st == "000":
            return d.get("list") or [], st
        if st == "013":
            return [], "013"  # 정상 빈 응답
        if st in SYSTEM_STATUS:
            return None, f"dart_{st}"
        return None, st
    return None, "network"


def main():
    targets = json.load(open(TARGETS_FILE))
    print(f"수집 대상: {len(targets)}개", flush=True)

    # corp_code 조회
    try:
        corp = Dart().corp_codes()
    except Exception as e:
        corp = json.load(open(ROOT / "data_cache" / "dart_corp_codes.json"))
        print(f"기업코드 API 실패 → 캐시 사용 ({type(e).__name__})", flush=True)

    # DART API key 선택
    keys = []
    for attr in ("DART_API_KEY", "DART_API_KEY3", "DART_API_KEY4", "DART_API_KEY2"):
        k = getattr(config, attr, None)
        if k:
            keys.append(k)
    if not keys:
        raise SystemExit("DART_API_KEY 없음")
    key_idx = 0

    done = 0
    skip_no_corp = 0
    skip_exists = 0
    fail = 0

    for i, t in enumerate(targets):
        code = t["stock_code"]
        dart_year = t["dart_year"]
        dart_q = t["dart_quarter"]
        fs = t["fs"]

        dest = RAW_DART / code / f"{dart_year}_{dart_q}_{fs}.json.gz"
        if dest.exists():
            skip_exists += 1
            continue

        if code not in corp:
            print(f"  [{i+1}/{len(targets)}] {code}: corp_code 없음 (skip)", flush=True)
            skip_no_corp += 1
            continue

        corp_code = corp[code]
        key = keys[key_idx % len(keys)]

        print(f"  [{i+1}/{len(targets)}] {code} {dart_year}Q{dart_q} {fs} ... ", end="", flush=True)
        rows, st = fetch_one(key, corp_code, dart_year, dart_q, fs)

        if st == "quota":
            key_idx += 1
            if key_idx >= len(keys):
                print(f"\nAPI 키 전부 소진. {done}건 완료 후 중단.", flush=True)
                break
            key = keys[key_idx % len(keys)]
            rows, st = fetch_one(key, corp_code, dart_year, dart_q, fs)

        if rows is None:
            print(f"FAIL ({st})", flush=True)
            fail += 1
        elif not rows:
            print(f"빈응답 ({st})", flush=True)
            # 빈 응답은 파일을 만들지 않음 (파일 없음 = 데이터 없음으로 유지)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False).encode()))
            print(f"OK ({len(rows)}행)", flush=True)
            done += 1

        time.sleep(0.4)

    print(f"\n완료: {done}건 저장 / {skip_exists}건 이미 있음 / {skip_no_corp}건 corp없음 / {fail}건 실패", flush=True)


if __name__ == "__main__":
    main()
