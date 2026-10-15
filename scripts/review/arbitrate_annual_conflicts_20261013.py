#!/usr/bin/env python3
"""원장 연간 중복 행 값 충돌 판정 (2026-10-13). 기본 dry-run(CSV만 만든다).

입력: canonical_cf_annual_conflicts_20261013.csv (대표행 값 vs 같은 묶음 다른 원장 연간 행 값이 반올림 오차를 넘게 다른 칸).
판정: 로컬 DART 원문(사업보고서 당기 칸 + 다음 해 사업보고서 전기 칸) 중 어느 한쪽만 2e6 이내로 일치하면 그 값이 맞다(원칙 0 — DART는 후보이고 이 단계는 OpenDART 단일 경로 내부 판정).
  둘 다 일치(원문 안에서도 두 값)·둘 다 불일치·원문 없음 → 판정 안 함(목록에 남김).
출력(필드군별로 나눠 apply_reviewed_cells_20261012.py 입력으로 쓴다 — 연간 중복 행 모두 판정값으로 맞춘다):
  reviewed_cells_annual_conflicts_20261013.csv            영업·투자·재무현금흐름·CapEx (원문 판정 그대로 적용)
  reviewed_cells_annual_conflicts_cash_end_20261013.csv   기말현금 (현금흐름표 '기말현금및현금성자산' 행)
  annual_conflicts_depreciation_held_20261013.csv         감가상각 원문 판정 전체 (정의 미정리 단계에서는 적용하지 않고 보류했던 목록)
  reviewed_cells_annual_conflicts_depreciation_20261013.csv / annual_conflicts_depreciation_hold_20261013.csv
                                                          감가상각 적용 게이트(C14, 10-13): FnGuide 연간 값이 있으면 원문 판정이 FnGuide와 일치할 때만, 없으면 2016~2022(OpenDART 단일 경로)만 적용·나머지 보류. 음수는 보류.
                                                          ※ 이 게이트로 22:57에 적용한 값은 한 행 기준이었고, 이후 C14 정의 구체화(조정 감가상각 행들의 합)에 따라 align_depreciation_annual_20261013.py가 다시 정렬했다.
  annual_conflicts_undecided_20261013.csv                 판정 못 한 칸
"""
from __future__ import annotations

import collections
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "review"))
import fin_norm as fn  # noqa: E402


OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
T = 2e6
MAPPED = {"operating_cf", "investing_cf", "financing_cf", "capex"}


def _cash_end(code, y, fs, col):
    """현금흐름표 '기말현금및현금성자산' 행(계정 ID …CashAndCashEquivalentsAtEndOfPeriodCf 또는 이름)."""
    import gzip, json, re
    p = fn.RAW_DART / code / f"{y}_0_{fs}.json.gz"
    if not p.exists():
        return None
    for r in json.loads(gzip.decompress(p.read_bytes())):
        if r.get("sj_div") == "CF" and (str(r.get("account_id") or "").endswith("CashAndCashEquivalentsAtEndOfPeriodCf")
                                         or re.sub(r"\s", "", r.get("account_nm") or "") in ("기말현금및현금성자산", "기말의현금및현금성자산")):
            try:
                return float(str(r.get(col) or "").replace(",", ""))
            except ValueError:
                return None
    return None


def raw_cands(code, y, fs, field):
    if field == "cash_end":
        return [c for c in (_cash_end(code, y, fs, "thstrm_amount"), _cash_end(code, y + 1, fs, "frmtrm_amount")) if c is not None]
    if field in MAPPED:
        return [c for c in (fn.latest_annual(code, y, fs, field)[0], fn.raw(code, y, 0, fs, field)) if c is not None]
    if field == "depreciation":  # 현금흐름표 '감가상각비' 조정 행(정본 정의: 양수) — fin_norm에 키가 없어 추출기를 직접 호출
        out = [fn._extract(code, y, 0, fs, "thstrm_amount").get("depreciation"), fn._extract(code, y + 1, 0, fs, "frmtrm_amount").get("depreciation")]
        return [abs(c) for c in out if c is not None]
    return []


def main():
    from db_compat import connect_primary_db
    conn = connect_primary_db(timeout=300, readonly=True)
    rows = list(csv.DictReader(open(OUT / "canonical_cf_annual_conflicts_20261013.csv")))
    stat = collections.Counter(); decided, undecided = [], []
    for r in rows:
        code, y, rt, f = r["stock_code"], int(r["year"]), r["report_type"], r["field"]
        a, b = float(r["representative_value"]), float(r["other_ledger_value"])
        fs = fn.effective_fs(code, y, rt)
        cand = raw_cands(code, y, fs, f)
        if not cand:
            stat[f"{f}|원문 없음/미지원"] += 1; undecided.append((*r.values(), "원문 없음/미지원")); continue
        ma, mb = any(abs(c - a) <= T for c in cand), any(abs(c - b) <= T for c in cand)
        if ma == mb:
            why = "둘 다 원문과 일치" if ma else "둘 다 원문과 다름"
            stat[f"{f}|{why}"] += 1; undecided.append((*r.values(), why)); continue
        win = a if ma else b
        stat[f"{f}|{'대표행이 맞음' if ma else '다른 행이 맞음'}"] += 1
        decided.append(["cash_flow_data", code, y, 0, rt, f, f"{win:.0f}", f"연간 중복 행 값 충돌 — DART 원문({'대표행' if ma else '다른 행'} 값)과 일치하는 값으로 통일", "", f"원문 후보 {[round(c) for c in cand][:3]}"])
    header = "table stock_code year quarter report_type field new_value rule reason_code evidence".split()

    def _write(name, rows_, head=header):
        with open(OUT / name, "w", newline="") as fh:
            w = csv.writer(fh); w.writerow(head); w.writerows(rows_)

    by_field = collections.defaultdict(list)
    for d in decided:
        by_field[d[5]].append(d)
    _write("reviewed_cells_annual_conflicts_20261013.csv", [d for f in ("operating_cf", "investing_cf", "financing_cf", "capex") for d in by_field.get(f, [])])
    _write("reviewed_cells_annual_conflicts_cash_end_20261013.csv", by_field.get("cash_end", []))
    dep = by_field.get("depreciation", [])
    _write("annual_conflicts_depreciation_held_20261013.csv", dep)
    fg = {(r[0], r[1], r[2]): r[3] for r in conn.execute("SELECT stock_code, year, report_type, fnguide_value FROM financial_field_verification "
                                                       "WHERE field='depreciation' AND quarter=0 AND fnguide_value IS NOT NULL").fetchall()} if conn else {}
    dep_apply, dep_hold = [], []
    for d in dep:
        code, y, rt, w = d[1], int(d[2]), d[4], float(d[6]); e = fg.get((code, y, rt))
        if w < 0:
            dep_hold.append(d)
        elif e is not None and abs(w - e) <= max(2e6, abs(e) * 0.001):
            dep_apply.append(d[:7] + [d[7] + " (FnGuide 일치)"] + d[8:])
        elif e is None and y <= 2022:
            dep_apply.append(d[:7] + [d[7] + " (OpenDART 단일 경로)"] + d[8:])
        else:
            dep_hold.append(d)
    _write("reviewed_cells_annual_conflicts_depreciation_20261013.csv", dep_apply)
    _write("annual_conflicts_depreciation_hold_20261013.csv", dep_hold)
    with open(OUT / "annual_conflicts_undecided_20261013.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow("stock_code year report_type field representative_value other_ledger_value why".split()); w.writerows(undecided)
    print(len(rows), "충돌 칸 → 판정", len(decided), "/ 미판정", len(undecided))
    for k, v in sorted(stat.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
