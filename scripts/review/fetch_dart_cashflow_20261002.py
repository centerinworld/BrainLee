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
import gzip
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

RAW_DART = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002" / "dart_cf_full.jsonl"
JOBS = [(y, q) for y in (2023, 2024, 2025) for q in (1, 2, 3, 0)] + [(2026, 1), (2026, 2)]
lock = threading.Lock()
SYSTEM_STATUS = {"010", "011", "012", "021", "100", "101", "800", "900", "901"}  # 키·IP·점검·시스템 오류
NO_FALLBACK = False  # --ofs-extra: 지정한 fs만 요청(연결 회사의 별도 재무제표를 따로 받음)


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
        st = d.get("status")
        if st == "020":
            raise RuntimeError("quota")
        if st == "000":
            return d.get("list") or [], st
        if st == "013":  # 조회된 데이터 없음 = 정상 빈 응답(그 구분의 보고서가 없음)
            return [], st
        if st in SYSTEM_STATUS:  # 2026-10-09 검토 반영: 점검(800)·키 오류·시스템 오류를 '빈 성공'으로 저장하지 않고 중단
            raise RuntimeError(f"dart_status_{st}")
        return None, st  # 그 밖의 비정상 상태: 실패로 기록(ok=false, 다음 실행에서 재시도)
    return None, "network"


def worker(key, items, fh, done):
    for code, corp, fs0 in items:
        for year, q in JOBS:
            if (code, year, q) in done:
                continue
            rows, used, statuses = None, None, []
            for fs in ([fs0] if NO_FALLBACK else [fs0] + [x for x in ("CFS", "OFS") if x != fs0]):
                got, st = fetch(key, corp, year, q, fs)
                statuses.append(st)
                if got is None:  # 비정상 응답 — 빈 결과로 덮지 않는다
                    rows = None
                    break
                rows = got
                if rows:
                    used = fs
                    break
            if rows:  # 2026-10-04: 원문 계정 행 전체 저장 — 정의 판정(CapEx 합산·재고자산 등)을 재호출 없이 재파싱
                rawp = RAW_DART / code
                rawp.mkdir(parents=True, exist_ok=True)
                (rawp / f"{year}_{q}_{used}.json.gz").write_bytes(gzip.compress(json.dumps(rows, ensure_ascii=False).encode()))
            rec = {"code": code, "year": year, "q": q, "fs": used, "vals": extract(rows) if rows else {}, "ok": rows is not None,
                   "parent_checked": True, "dart_status": statuses}
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
    ap.add_argument("--key4-only", action="store_true", help="DART_API_KEY4만 사용해 재조회(#4 키 검증용)")
    ap.add_argument("--repair-parent", action="store_true",
                    help="이미 받은 CFS 보고서 중 지배주주 순이익/자본이 없는 것만 개선된 추출기로 다시 받는다(뒤 줄이 앞 줄을 덮어씀)")
    ap.add_argument("--refetch-raw", action="store_true",
                    help="원문 계정 행(data_raw/dart_fnltt)이 없는 보고서를 다시 받는다 — 재고자산·CapEx 세부 등 재파싱용(2026-10-04)")
    ap.add_argument("--prev-only", action="store_true",
                    help="사업보고서 중 전기 칸(vals_prev)이 없는 것만 다시 받는다(재작성값 확보용, 2026-10-03)")
    ap.add_argument("--years", default="", help="예: 2016-2022 (지정 시 해당 연도 1Q·반기·3Q·사업보고서, 출력 dart_cf_<범위>.jsonl)")
    ap.add_argument("--ofs-extra", action="store_true",
                    help="연결(CFS) 회사의 별도(OFS) 재무제표를 따로 받는다 — 2025~2026 분기·연간, 출력 dart_cf_ofs_extra.jsonl(2026-10-09: "
                         "기본 모드는 CFS가 있으면 OFS를 요청하지 않아 별도 분기 현금흐름·재무상태가 DB에 없음 = 현행 검증 '미확인(DB 없음)' 최대 원인)")
    ap.add_argument("--out-name", default="", help="출력 jsonl 파일명(기본 dart_cf_full.jsonl 또는 dart_cf_<연도범위>.jsonl)")
    a = ap.parse_args()
    global OUT, JOBS, NO_FALLBACK
    if a.ofs_extra:
        NO_FALLBACK = True
        JOBS = [(2025, 1), (2025, 2), (2025, 3), (2025, 0), (2026, 1), (2026, 2)]
        OUT = OUT.with_name("dart_cf_ofs_extra.jsonl")
    if a.prev_only:
        JOBS = [j for j in JOBS if j[1] == 0]
    if a.years:
        y0, y1 = (int(x) for x in a.years.split("-"))
        JOBS = [(y, q) for y in range(y0, y1 + 1) for q in ((0,) if a.prev_only else (1, 2, 3, 0))]
        OUT = OUT.with_name(f"dart_cf_{y0}_{y1}.jsonl")
    if a.out_name:
        OUT = OUT.with_name(a.out_name)
    conn = connect_primary_db(timeout=120, readonly=True)
    y_lo, y_hi = min(j[0] for j in JOBS), max(j[0] for j in JOBS)
    rows = conn.execute("""SELECT stock_code, MIN(CASE WHEN report_type='CFS' THEN 0 ELSE 1 END)
                           FROM cash_flow_data WHERE year BETWEEN ? AND ? AND stock_code ~ '^[0-9]{5}[0-9A-Z]$' GROUP BY 1 ORDER BY 1""",
                        (y_lo, y_hi)).fetchall()
    conn.close()
    targets = [(r[0], "CFS" if r[1] == 0 else "OFS") for r in rows]
    if a.ofs_extra:  # CFS 회사만, 별도로 요청(원래 OFS 회사는 기본 모드가 이미 받음)
        targets = [(c, "OFS") for c, fs in targets if fs == "CFS"]
    if a.codes:
        keep = {c.strip() for c in a.codes.split(",") if c.strip()}
        targets = [t for t in targets if t[0] in keep]
    if a.limit:
        targets = targets[: a.limit]
    try:
        corp = Dart().corp_codes()
    except Exception as e:  # 2026-10-09: corpCode.xml만 점검(상태 800)인 날이 있음 → 캐시 사용(신규 상장 일부 누락 가능)
        corp = json.load(open(ROOT / "data_cache" / "dart_corp_codes.json"))
        print(f"기업코드 API 실패({type(e).__name__}) → 캐시 {len(corp)}사 사용", flush=True)
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
            if a.refetch_raw:
                needs = bool(d.get("fs")) and not (RAW_DART / k[0] / f"{k[1]}_{k[2]}_{d['fs']}.json.gz").exists()
            if a.ofs_extra and not d.get("fs") and "013" not in (d.get("dart_status") or []):
                needs = True  # 2026-10-09: 상태 없이 빈 행(구버전 '빈 성공')은 정상 '데이터 없음'(013)으로 확인될 때까지 재요청
            if not needs:
                done.add(k)
    items = [(c, corp[c], fs) for c, fs in targets if c in corp]
    print(f"대상 {len(items)}종목 × {len(JOBS)}보고서, 이미 완료 {len(done)}", flush=True)
    # 2026-10-03 사용자 지시: 키 4개를 순차적으로 모두 사용 — KEY1 → KEY3 → KEY4 → KEY2(일괄 상한, dart_keys.py).
    # 동시 스레드는 2개(초당 ~2~3건) 그대로. 스레드가 쓰던 키가 한도에 걸리면 공유 대기열의 다음 키로 넘어가 남은 일을 잇는다.
    if a.key4_only:
        order = [getattr(config, "DART_API_KEY4", None)]
        order = [k for k in order if k]
        if not order:
            raise SystemExit("DART_API_KEY4 not configured")
    else:
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

    import fcntl  # 2026-10-09: 같은 출력 파일을 두 프로세스가 동시에 쓰면 중복 수집 → 이미 실행 중이면 종료
    _lk = open(str(OUT) + ".lock", "w")
    try:
        fcntl.flock(_lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"이미 실행 중({OUT.name}) — 종료", flush=True)
        return
    with open(OUT, "a") as fh:
        ths = [threading.Thread(target=run, args=(k, ch)) for k, ch in zip(order[:worker_count], chunks)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
    print("완료", flush=True)


if __name__ == "__main__":
    main()
