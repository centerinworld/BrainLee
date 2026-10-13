#!/usr/bin/env python3
"""P1 잔여 미확정 칸 원인군 집계(기본 DB 읽기 전용, 2026-10-10).

분모: 고정 분모 기준선(financial_field_baseline 최신, 기본 B20261009) ∩ P1 필드(손익·재무상태 6 + 현금흐름 3) ∩ 리츠 제외(D-8 별도 KPI).
48시간 분모 고정(사용자 10-10): 기준선 밖 새 칸은 new_unverified로 따로 센다.
원인군(우선순위 순, 칸마다 첫 해당):
  REIT            리츠(별도 KPI — 여기선 분모 밖)
  FX              외화 보고통화 종목(fs_quirk:reporting_currency)
  NONDEC          비12월 결산(fs_quirk:fiscal_month ≠ 12)
  DB_MISSING_OFS  DB 없음 — 별도(OFS)
  DB_MISSING_CFS  DB 없음 — 연결(CFS)
  CF_RESTATED     현금흐름 분기 재작성 보류(restated_quarterly_hold) 또는 2025 1~3분기 현금흐름 원인 조사
  SPECIAL         상장 첫해·합병 등(그 해 DART 사업보고서 원문 없음)
  EXPLAINED       설명된 예외 등록(financial_field_exception)
  HUMAN           사람 판정 대상(financial_field_cause_review 최신 판정 D)
  OTHER           그 밖의 원인 조사
출력: research_outputs/financial_rereview_20261002/residual_cause_groups_<run>.json (+ csv).
      표 residual_cause_groups_daily 기록은 --write-metrics 명시 시에만 수행한다.
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402

RAW = Path("/Volumes/Realtek_NVME/stock_dashboard/data_raw/dart_fnltt")
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
P1 = {"revenue": "손익·재무상태", "operating_profit": "손익·재무상태", "net_income": "손익·재무상태", "total_assets": "손익·재무상태",
      "total_liabilities": "손익·재무상태", "total_equity": "손익·재무상태", "operating_cf": "현금흐름", "investing_cf": "현금흐름", "financing_cf": "현금흐름"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-metrics", action="store_true", help="DB 표 residual_cause_groups_daily에 집계 결과를 기록한다.")
    args = ap.parse_args()

    conn = connect_primary_db(timeout=300, readonly=not args.write_metrics)
    bid = conn.execute("SELECT baseline_id FROM financial_field_baseline ORDER BY created_at DESC LIMIT 1").fetchone()[0]
    base = {tuple(r) for r in conn.execute("SELECT stock_code, year, quarter, report_type, field FROM financial_field_baseline WHERE baseline_id=?", (bid,)).fetchall()}
    cfg = collections.defaultdict(dict)
    for code, k, v in conn.execute("SELECT stock_code, config_key, config_value FROM stock_collection_config").fetchall():
        cfg[code][k] = v
    reit = {r[0] for r in conn.execute("SELECT stock_code FROM stock_universe WHERE stock_name LIKE '%%리츠%%'").fetchall()} | \
           {c for c, d in cfg.items() if (d.get("fs_quirk:fiscal_period") or "").startswith("REIT") or d.get("entity_category") == "reit"}
    exc = {tuple(r[:5]): r[5] for r in conn.execute("SELECT stock_code, year, quarter, report_type, field, reason_code FROM financial_field_exception").fetchall()}
    human = {tuple(r[:5]) for r in conn.execute("""SELECT DISTINCT ON (stock_code,year,quarter,report_type,field) stock_code,year,quarter,report_type,field,verdict
        FROM financial_field_cause_review ORDER BY stock_code,year,quarter,report_type,field, created_at DESC""").fetchall() if (r[5] or "").startswith("D")}
    hold = set()
    p = OUT / "restated_quarterly_hold_20261010.csv"
    if p.exists():
        for r in csv.DictReader(open(p)):
            hold.add((r["stock_code"], int(r["year"]), int(r["quarter"]), r["fs"], r["field"]))
    run = conn.execute("SELECT run_id FROM financial_field_verification LIMIT 1").fetchone()[0]
    st = collections.Counter(); new = collections.Counter(); tot = collections.Counter(); conf = collections.Counter(); rows = []
    for code, y, q, fs, f, status, cause in conn.execute(
            "SELECT stock_code, year, quarter, report_type, field, status, cause FROM financial_field_verification").fetchall():
        if f not in P1:
            continue
        key = (code, y, q, fs, f)
        area = P1[f]
        if code in reit:
            st[(area, "REIT(별도 KPI)")] += 0 if status.startswith("확정") else 1
            continue
        if key not in base:
            new[(area, "확정" if status.startswith("확정") else status)] += 1
            continue
        tot[area] += 1
        if status.startswith("확정"):
            conf[area] += 1
            continue
        d = cfg.get(code, {})
        if "fs_quirk:reporting_currency" in d:
            g = "FX"
        elif d.get("fs_quirk:fiscal_month", "12") != "12":
            g = "NONDEC"
        elif status.startswith("미확인"):
            g = "DB_MISSING_OFS" if fs == "OFS" else "DB_MISSING_CFS"
        elif key in hold or (f.endswith("_cf") and y == 2025 and q in (1, 2, 3)):
            g = "CF_RESTATED"
        elif not (RAW / code / f"{y}_0_{fs}.json.gz").exists() and q == 0:
            g = "SPECIAL"
        elif key in exc:
            g = "EXPLAINED:" + exc[key]
        elif key in human:
            g = "HUMAN"
        else:
            g = "OTHER"
        st[(area, g)] += 1
        rows.append((code, y, q, fs, f, area, g, status, cause))
    now = datetime.now().isoformat(timespec="seconds")
    res = {"at": now, "verification_run": run, "baseline_id": bid,
           "fixed_denominator": {a: {"total": tot[a], "confirmed": conf[a], "rate": round(100 * conf[a] / tot[a], 4) if tot[a] else None,
                                     "residual": tot[a] - conf[a]} for a in tot},
           "residual_by_group": {f"{a}|{g}": n for (a, g), n in sorted(st.items(), key=lambda x: (x[0][0], -x[1]))},
           "new_unverified": {f"{a}|{s}": n for (a, s), n in sorted(new.items())}}
    if args.write_metrics:
        conn.execute("""CREATE TABLE IF NOT EXISTS residual_cause_groups_daily (measured_at TEXT, verification_run TEXT, baseline_id TEXT, area TEXT, cause_group TEXT, cells INT)""")
        conn.executemany("INSERT INTO residual_cause_groups_daily VALUES (?,?,?,?,?,?)", [(now, run, bid, a, g, n) for (a, g), n in st.items()])
        conn.commit()
    (OUT / f"residual_cause_groups_{run}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    with open(OUT / f"residual_cause_groups_{run}.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["stock_code", "year", "quarter", "fs", "field", "area", "group", "status", "cause"]); w.writerows(rows)
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
