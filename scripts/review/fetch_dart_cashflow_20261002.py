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
import dart_keys  # noqa: E402
from financial_rereview_20261002 import Dart, REPRT, extract  # noqa: E402

OUT = ROOT / "research_outputs" / "financial_rereview_20261002" / "dart_cf_full.jsonl"
JOBS = [(y, q) for y in (2023, 2024, 2025) for q in (1, 2, 3, 0)] + [(2026, 1), (2026, 2)]
lock = threading.Lock()


def fetch(key, corp, year, q, fs):
    if not dart_keys.allow(key):  # KEY2 일괄 사용 상한(공시 수집 몫 보호)
        raise RuntimeError("quota")
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
            rec = {"code": code, "year": year, "q": q, "fs": used, "vals": extract(rows) if rows else {}, "ok": rows is not None,
                   "parent_checked": True}
            # 2026-10-03: 사업보고서는 전기 칸(직전 연도 재작성값)도 저장 — FnGuide·네이버가 표시하는 최신 재작성값의 원문
            if rows and q == 0:
                rec["vals_prev"] = extract(rows, "frmtrm_amount")
            with lock:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
                fh.flush()
                done.add((code, year, q))  # 키 교체 후 이어 받을 때 다시 받지 않게
            time.sleep(0.35)  # 2026-10-02: 키 3개 병렬 초당 ~14건으로 OpenDART IP 차단을 유발 → 단일 스레드 초당 ~1건


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--codes", default="", help="쉼표 구분 종목코드만 재수집(예: 005930,000660)")
    ap.add_argument("--max-workers", type=int, default=2, help="동시 DART 키 수. 기본 2, KEY2는 제외")
    ap.add_argument("--repair-parent", action="store_true",
                    help="이미 받은 CFS 보고서 중 지배주주 순이익/자본이 없는 것만 개선된 추출기로 다시 받는다(뒤 줄이 앞 줄을 덮어씀)")
    ap.add_argument("--prev-only", action="store_true",
                    help="사업보고서 중 전기 칸(vals_prev)이 없는 것만 다시 받는다(재작성값 확보용, 2026-10-03)")
    ap.add_argument("--years", default="", help="예: 2016-2022 (지정 시 해당 연도 1Q·반기·3Q·사업보고서, 출력 dart_cf_<범위>.jsonl)")
    a = ap.parse_args()
    global OUT, JOBS
    if a.prev_only:
        JOBS = [j for j in JOBS if j[1] == 0]
    if a.years:
        y0, y1 = (int(x) for x in a.years.split("-"))
        JOBS = [(y, q) for y in range(y0, y1 + 1) for q in ((0,) if a.prev_only else (1, 2, 3, 0))]
        OUT = OUT.with_name(f"dart_cf_{y0}_{y1}.jsonl")
    conn = connect_primary_db(timeout=120, readonly=True)
    y_lo, y_hi = min(j[0] for j in JOBS), max(j[0] for j in JOBS)
    rows = conn.execute("""SELECT stock_code, MIN(CASE WHEN report_type='CFS' THEN 0 ELSE 1 END)
                           FROM cash_flow_data WHERE year BETWEEN ? AND ? AND stock_code ~ '^[0-9]{5}[0-9A-Z]$' GROUP BY 1 ORDER BY 1""",
                        (y_lo, y_hi)).fetchall()
    conn.close()
    targets = [(r[0], "CFS" if r[1] == 0 else "OFS") for r in rows]
    if a.codes:
        keep = {c.strip() for c in a.codes.split(",") if c.strip()}
        targets = [t for t in targets if t[0] in keep]
    if a.limit:
        targets = targets[: a.limit]
    corp = Dart().corp_codes()
    done = set()
    if OUT.exists():
        last = {}
        for line in open(OUT):
            d = json.loads(line)
            if d.get("ok"):
                last[(d["code"], d["year"], d["q"])] = d
        for k, d in last.items():
            v = d.get("vals") or {}
            needs = (a.repair_parent and d.get("fs") == "CFS" and v and not d.get("parent_checked")
                     and ("ni_parent" not in v or "equity_parent" not in v))
            if a.prev_only:
                needs = d["q"] == 0 and bool(d.get("fs")) and "vals_prev" not in d
            if not needs:
                done.add(k)
    items = [(c, corp[c], fs) for c, fs in targets if c in corp]
    print(f"대상 {len(items)}종목 × {len(JOBS)}보고서, 이미 완료 {len(done)}", flush=True)
    # 2026-10-03 사용자 지시: 키 4개를 순차적으로 모두 사용 — KEY1 → KEY3 → KEY4 → KEY2(일괄 상한, dart_keys.py).
    # 동시 스레드는 2개(초당 ~2~3건) 그대로. 스레드가 쓰던 키가 한도에 걸리면 공유 대기열의 다음 키로 넘어가 남은 일을 잇는다.
    order = dart_keys.ordered_keys()
    worker_count = max(1, min(a.max_workers, 2, len(order)))
    queue = order[worker_count:]
    chunks = [items[i::worker_count] for i in range(worker_count)]

    def run(key, chunk):
        cur = key
        while cur:
            try:
                worker(cur, chunk, fh, done)
                return
            except RuntimeError as e:
                with lock:
                    nxt = queue.pop(0) if queue else None
                print(f"키 한도 소진({e}) → 다음 키 {'있음' if nxt else '없음 — 내일 재개'}", flush=True)
                cur = nxt

    with open(OUT, "a") as fh:
        ths = [threading.Thread(target=run, args=(k, ch)) for k, ch in zip(order[:worker_count], chunks)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    print("완료", flush=True)


if __name__ == "__main__":
    main()
