#!/usr/bin/env python3
"""외화 종목 잔여 칸 판정 → apply_reviewed_cells 입력 csv 생성 (2026-10-12). DB는 읽기만.

fix_fx_cells_20261011 후보(원래 공시 × 환율)에 재작성 후보(다음 해 보고서 전기 칸 × 환율)와 연결 미작성 회사의 별도 원문(effective_fs)을 더한다.
판정(사용자 10-12 위임 — 10-11 승인 '환율도 큰 범위에서 차이가 나지 않는다면 FnGuide 값으로 대체'를 수치로):
  |후보 − FnGuide| ≤ max(200만원, 0.1%)  → 후보 값(DART × ECOS)
  ≤ 3%                                     → FnGuide 값, FNGUIDE_SUBSTITUTED_FX_RATE(환율 기준일·평균 방식 차이)
  원문 보고서 자체 없음(013·미수집)         → FnGuide 값, FNGUIDE_SUBSTITUTED_NO_DART
  그 밖                                     → 보류 목록(원인 미확인 — 자동 대체 안 함)
"""
import csv, importlib.util, json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
import fin_norm as fn  # noqa: E402
_s = importlib.util.spec_from_file_location("fxc", ROOT / "scripts" / "review" / "fix_fx_cells_20261011.py")
fxc = importlib.util.module_from_spec(_s); _s.loader.exec_module(fxc)
OUT = ROOT / "research_outputs" / "financial_rereview_20261002"
CF = ("operating_cf", "investing_cf", "financing_cf")


def restated(code, cc, y, q, fs, f):
    out = []
    if q == 0:
        A = fn.latest_annual(code, y, fs, f)[0]
        kinds = ("end",) if f in fn.BS else ("avg", "end")
        out += [(f"재작성 연간 × {k}", fxc.mul(A, fxc.fxr(cc, code, y, 4, 12, k))) for k in kinds]
    elif q == 4 and f in fn.BS:
        out.append(("재작성 연간 × 기말", fxc.mul(fn.latest_annual(code, y, fs, f)[0], fxc.fxr(cc, code, y, 4, 3, "end"))))
    elif q == 4:
        A = fn.latest_annual(code, y, fs, f)[0]
        c3 = fn.latest_cf_cum(code, y, 3, fs, f)[0] if f in CF else None
        if f not in CF:
            qr = [fn.raw(code, y + 1, qq, fs, f, "frmtrm_q_amount") for qq in (1, 2, 3)]
            c3 = sum(qr) if None not in qr else None
        for k in ("avg", "end"):
            a, b = fxc.mul(A, fxc.fxr(cc, code, y, 4, 12, k)), fxc.mul(c3, fxc.fxr(cc, code, y, 3, 9, k))
            out.append((f"재작성 4분기 {k}", None if a is None or b is None else a - b))
    return out


def main():
    conn = connect_primary_db(timeout=600, readonly=True)
    src = sorted(OUT.glob("residual_cause_groups_field_verif_*.csv"))[-1]
    run = src.stem.replace("residual_cause_groups_", "")
    ccy = {}
    for code, v in conn.execute("SELECT stock_code, config_value FROM stock_collection_config WHERE config_key='fs_quirk:reporting_currency'").fetchall():
        c3 = (v or "")[:3]
        if c3 in ("USD", "CNY", "JPY"):
            ccy[code] = "JPY100" if c3 == "JPY" else c3
    rows, hold = [], []
    for x in csv.DictReader(open(src)):
        if x["group"] != "FX":
            continue
        code, y, q, fs, f = x["stock_code"], int(x["year"]), int(x["quarter"]), x["fs"], x["field"]
        v = conn.execute("SELECT db_value, fnguide_value FROM financial_field_verification WHERE run_id=? AND stock_code=? AND year=? AND quarter=? AND report_type=? AND field=?",
                         (run, code, y, q, fs, f)).fetchone()
        db, fg = (v[0], v[1]) if v else (None, None)
        if fg is None:
            hold.append((code, y, q, fs, f, db, fg, "FnGuide 없음")); continue
        col = f + "_q" if (f in CF and q in (1, 2, 3, 4)) else f
        tbl = "cash_flow_data" if f in CF else "financial_data"
        if code not in ccy:
            hold.append((code, y, q, fs, f, db, fg, "통화 미확인")); continue
        rf = fn.effective_fs(code, y, fs)
        rcur = fn.raw_currency(code, y, 0 if q in (0, 4) else q, rf)
        cc = {"USD": "USD", "CNY": "CNY", "JPY": "JPY100"}.get(rcur or "", ccy[code])
        cs = [(l, c) for l, c in fxc.cands(code, cc, y, q, rf, f) + restated(code, cc, y, q, rf, f) if c is not None]
        tol = max(2e6, abs(fg) * 0.001)
        exact = [(l, c) for l, c in cs if abs(c - fg) <= tol]
        near = sorted([(abs(c - fg) / abs(fg), l, c) for l, c in cs if fg and abs(c - fg) <= abs(fg) * 0.03])
        by, bq = (y, 0) if q in (0, 4) else fn.dart_key_for_fiscal(code, y, q)
        has_report = (fn.RAW_DART / code / f"{by}_{bq}_{rf}.json.gz").exists()
        if exact:
            rows.append(dict(table=tbl, stock_code=code, year=y, quarter=q, report_type=fs, field=col, new_value=round(exact[0][1]),
                             rule=f"외화 환산: {exact[0][0]} = FnGuide(0.1% 이내)", reason_code="", evidence=f"후보 {exact[0][1]:,.0f} / FnGuide {fg:,.0f}"))
        elif near:
            rows.append(dict(table=tbl, stock_code=code, year=y, quarter=q, report_type=fs, field=col, new_value=round(fg),
                             rule=f"외화: DART × 환율({near[0][1]})이 FnGuide와 {near[0][0]*100:.2f}% 차이 — 환율 기준 차이로 FnGuide 값(사용자 10-11·10-12)",
                             reason_code="FNGUIDE_SUBSTITUTED_FX_RATE", evidence=f"후보 {near[0][2]:,.0f} / FnGuide {fg:,.0f} / DB {db}"))
        elif not has_report:
            rows.append(dict(table=tbl, stock_code=code, year=y, quarter=q, report_type=fs, field=col, new_value=round(fg),
                             rule="외화: OpenDART 원문 보고서 없음 → FnGuide(원인 명확 — 사용자 10-11)", reason_code="FNGUIDE_SUBSTITUTED_NO_DART", evidence=f"{by}_{bq}_{rf} 없음"))
        else:
            hold.append((code, y, q, fs, f, db, fg, [(l, round(c)) for l, c in cs]))
    p = OUT / "reviewed_cells_fx_20261012.csv"
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["table", "stock_code", "year", "quarter", "report_type", "field", "new_value", "rule", "reason_code", "evidence"])
        w.writeheader(); w.writerows(rows)
    with open(OUT / "fx_residual_hold_20261012.csv", "w", newline="") as fh:
        w = csv.writer(fh); w.writerow(["stock_code", "year", "quarter", "fs", "field", "db", "fnguide", "candidates"]); w.writerows(hold)
    import collections
    print(json.dumps({"src": src.name, "rows": len(rows), "by": dict(collections.Counter(r["reason_code"] or "DART×환율" for r in rows)), "hold": len(hold)}, ensure_ascii=False))
    for h in hold:
        print("HOLD", h[:7], str(h[7])[:200])


if __name__ == "__main__":
    main()
