#!/usr/bin/env python3
"""FnGuide(wcomp JSON) 재무 원문을 '응답 그대로' 저장한다 — 읽기 전용 수집(2026-10-03).

왜: 기존 수집기(fnguide_financial_collector)는 받은 즉시 자기 키워드로 몇 항목만 뽑고 원문을 버린다
(부채총계 없음, CapEx='유형자산의취득', 감가상각='감가상각비' 등 FINANCIAL_STATEMENTS.md §2-1과 다른 정의).
원문을 남기면 정의를 고쳐도 다시 호출하지 않고 재파싱할 수 있다.

- 종목당 연결(C)·별도(P) × 연간(Y)·분기(Q) × 손익/재무상태/현금흐름 = 최대 12회. (consol_typ D/B는 별도를 반환하므로 쓰지 않음)
- 한도: api_rate_limiter 'FNGUIDE'(일 1,500, 다른 잡과 공유) + 이 실행의 --max-calls.
- 순서: fs_quirk 기록 종목 → 원문이 없거나 --stale-days보다 오래된 종목.
- 별도(P)가 연결(C)과 같은 회사(종속회사 없음)는 FnGuide가 빈 응답을 준다 — 빈 응답도 그대로 저장.
저장: /Volumes/Realtek_NVME/stock_dashboard/data_raw/fnguide_wcomp/<code>/<YYYYMMDD>_<C|P>_<Y|Q>_<endpoint>.json.gz
"""
import argparse
import gzip
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from api_rate_limiter import api_limiter  # noqa: E402
from collectors.fnguide_financial_collector import FNGUIDE_HEADERS, WCOMP_BASE  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/fnguide_wcomp")
ENDPOINTS = ("getFinIncome", "getFinBalance", "getFinCashFlow")


def last_fetch(code):
    d = RAW / code
    if not d.exists():
        return None
    days = sorted({p.name[:8] for p in d.glob("*.json.gz")})
    return datetime.strptime(days[-1], "%Y%m%d") if days else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-calls", type=int, default=1000)
    ap.add_argument("--stale-days", type=int, default=30)
    ap.add_argument("--codes", default="", help="쉼표 구분 종목코드(지정 시 그 종목만)")
    a = ap.parse_args()
    conn = connect_primary_db(timeout=60, readonly=True)
    universe = [r[0] for r in conn.execute("""SELECT DISTINCT stock_code FROM financial_data
                 WHERE year>=2024 AND stock_code ~ '^[0-9]{6}$' ORDER BY 1""").fetchall()]
    quirk = {r[0] for r in conn.execute("SELECT DISTINCT stock_code FROM stock_collection_config WHERE config_key LIKE 'fs_quirk:%'").fetchall()}
    conn.close()
    if a.codes:
        targets = a.codes.split(",")
    else:
        cut = datetime.now() - timedelta(days=a.stale_days)
        due = [c for c in universe if (last_fetch(c) or datetime(2000, 1, 1)) < cut]
        targets = [c for c in due if c in quirk] + [c for c in due if c not in quirk]
    today = datetime.now().strftime("%Y%m%d")
    calls = done = 0
    print(f"대상 {len(targets)}종목(우선 {len(quirk & set(targets))}), 호출 한도 {a.max_calls}", flush=True)
    for code in targets:
        if calls + 12 > a.max_calls:
            break
        out = RAW / code
        out.mkdir(parents=True, exist_ok=True)
        ok = True
        for consol in ("C", "P"):
            for freq in ("Y", "Q"):
                for ep in ENDPOINTS:
                    f = out / f"{today}_{consol}_{freq}_{ep}.json.gz"
                    if f.exists():
                        continue
                    url = f"{WCOMP_BASE}/CompanyInfo/{ep}?cmp_cd={code}&freq_typ={freq}&consol_typ={consol}"
                    resp = api_limiter.get("FNGUIDE", url, timeout=20, headers={
                        **FNGUIDE_HEADERS, "Referer": f"{WCOMP_BASE}/CompanyInfo/Finance?cmp_cd={code}",
                        "X-Requested-With": "XMLHttpRequest"})
                    calls += 1
                    if resp is None:  # 일일 한도·차단 — 내일 이어서
                        print(f"FnGuide 한도/차단으로 중단 — 호출 {calls}, 완료 {done}종목", flush=True)
                        return
                    if resp.status_code != 200:
                        ok = False
                        continue
                    f.write_bytes(gzip.compress(resp.content))
        done += ok
    print(f"완료 — 호출 {calls}, 종목 {done}", flush=True)


if __name__ == "__main__":
    main()
