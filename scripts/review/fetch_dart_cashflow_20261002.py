#!/usr/bin/env python3
"""DART 현금흐름(+손익 핵심) 재수집 — 읽기 전용, 결과는 jsonl 파일에만 쓴다(재개 가능).

목적(2026-10-02 독립 재검토): cash_flow_data 의 2023년 이후 분기 행 중 FnGuide 출처 10,152건은 누적/3개월 값이
DART 와 30~60% 어긋나고, Q4 행의 누적 칸은 표본 100% 불일치였다. 같은 기간을 DART 원문으로 다시 받아
apply_dart_cashflow_20261002.py 가 교체한다.

대상: cash_flow_data 에 2023년 이후 행이 있는 상장 종목. 연도 2023~2026, 보고서 1Q·반기·3Q·사업보고서(2026은 1Q·반기).
재무제표 구분: 종목의 cash_flow_data report_type(CFS 우선). CFS 응답이 없으면 OFS.
출력: research_outputs/financial_rereview_20261002/dart_cf_full.jsonl  (한 줄 = 종목·연도·보고서·fs·값)
사용: venv/bin/python scripts/review/fetch_dart_cashflow_20261002.py [--limit N]
"""
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
import requests  # noqa: E402

import config  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
from financial_rereview_20261002 import Dart, REPRT, extract  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002" / "dart_cf_full.jsonl"
JOBS = [(y, q) for y in (2023, 2024, 2025) for q in (1, 2, 3, 0)] + [(2026, 1), (2026, 2)]
lock = threading.Lock()


def fetch(key, corp, year, q, fs):
    for attempt in range(4):
        try:
            d = requests.get("https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
                             params={"crtfc_key": key, "corp_code": corp, "bsns_year": str(year), "reprt_code": REPRT[q], "fs_div": fs},
                             timeout=25).json()
        except Exception:
            time.sleep(2 + attempt * 2)
            continue
        if d.get("status") == "020":
            raise RuntimeError("quota")
        return d.get("list") or [] if d.get("status") == "000" else []
    return None


def worker(key, items, fh, done):
    for code, corp, fs0 in items:
        for year, q in JOBS:
            if (code, year, q) in done:
                continue
            rows, used = None, None
            for fs in ([fs0] + [x for x in ("CFS", "OFS") if x != fs0]):
                rows = fetch(key, corp, year, q, fs)
                if rows:
                    used = fs
                    break
            rec = {"code": code, "year": year, "q": q, "fs": used, "vals": extract(rows) if rows else {}, "ok": rows is not None}
            with lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
            time.sleep(0.35)  # 2026-10-02: 키 3개 병렬 초당 ~14건으로 OpenDART IP 차단을 유발 → 단일 스레드 초당 ~1건


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--years", default="", help="예: 2016-2022 (지정 시 해당 연도 1Q·반기·3Q·사업보고서, 출력 dart_cf_<범위>.jsonl)")
    a = ap.parse_args()
    global OUT, JOBS
    if a.years:
        y0, y1 = (int(x) for x in a.years.split("-"))
        JOBS = [(y, q) for y in range(y0, y1 + 1) for q in (1, 2, 3, 0)]
        OUT = OUT.with_name(f"dart_cf_{y0}_{y1}.jsonl")
    conn = connect_primary_db(timeout=120, readonly=True)
    y_lo, y_hi = min(j[0] for j in JOBS), max(j[0] for j in JOBS)
    rows = conn.execute("""SELECT stock_code, MIN(CASE WHEN report_type='CFS' THEN 0 ELSE 1 END)
                           FROM cash_flow_data WHERE year BETWEEN ? AND ? AND stock_code ~ '^[0-9]{5}[0-9A-Z]$' GROUP BY 1 ORDER BY 1""",
                        (y_lo, y_hi)).fetchall()
    conn.close()
    targets = [(r[0], "CFS" if r[1] == 0 else "OFS") for r in rows]
    if a.limit:
        targets = targets[: a.limit]
    corp = Dart().corp_codes()
    done = set()
    if OUT.exists():
        for line in open(OUT):
            d = json.loads(line)
            if d.get("ok"):
                done.add((d["code"], d["year"], d["q"]))
    items = [(c, corp[c], fs) for c, fs in targets if c in corp]
    keys = list(config.DART_API_KEYS)
    print(f"대상 {len(items)}종목 × {len(JOBS)}보고서, 이미 완료 {len(done)}", flush=True)
    # 2026-10-03: 단일 스레드(분당 ~45건)는 9시간 → 키 2개 2스레드(합계 초당 ~1.5건). 차단은 초당 ~14건에서 발생했다.
    # 키2(DART_API_KEY2)는 운영 공시 수집 전용 — 재수집이 소진하지 않게 제외한다(2026-10-03)
    workers = [k for k in keys if k != getattr(config, "DART_API_KEY2", None)][:2] or keys[:1]
    chunks = [items[i::len(workers)] for i in range(len(workers))]

    def run(key, chunk):
        try:
            worker(key, chunk, fh, done)
        except RuntimeError as e:
            spare = []  # 예비 키 사용 안 함(공시 키 보호) — 다음 날 재개
            print("키 한도 소진:", e, "→ 예비 키", bool(spare), flush=True)
            if spare:
                worker(spare[0], chunk, fh, done)

    with open(OUT, "a") as fh:
        ths = [threading.Thread(target=run, args=(k, ch)) for k, ch in zip(workers, chunks)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    print("완료", flush=True)


if __name__ == "__main__":
    main()
