#!/usr/bin/env python3
"""연결 회사의 별도(OFS) 2025~26 원문(dart_cf_ofs_extra.jsonl)으로 financial_data·cash_flow_data 별도 칸을 채운다/바로잡는다(2026-10-09).

배경: 기본 DART 재수집(fetch_dart_cashflow_20261002.py)은 CFS가 있으면 OFS를 요청하지 않아 별도 분기 행이 대부분 없었다
(현행 검증 '미확인(DB 없음)' 25,071칸 중 21,363칸이 별도). `--ofs-extra` 모드로 받은 원문을 여기서 넣는다.

원칙 0(DART 값은 후보):
  - 빈 칸(행 없음 포함)  → DART 원문값으로 채운다. 이후 build_field_verification이 FnGuide와 대조해 확정/원인 조사로 판정.
  - 값이 있는 칸이 다름 → FnGuide 값이 새 DART 값과 일치할 때만 교체(FnGuide 없거나 불일치면 보류·기록).
  기간 규칙은 apply_dart_refetch_20261002.py와 같다:
    financial_data  분기 Q1~Q3 = DART 보고서(손익 3개월), Q4 = 연간 − (Q1+Q2+Q3) / 재무상태 = 연간, 연간 = 사업보고서. 별도는 순이익·자본 = 전체.
    cash_flow_data  Q1~Q3 누적 = DART YTD, 3개월 = YTD 차분(Q1=YTD) / Q4 누적 = 연간, 3개월 = 연간 − Q3 YTD / 연간 누적 = 연간.
    CapEx 절대값. 감가상각은 이 원문에 없어 건드리지 않는다(XBRL 경로).
  FnGuide 대조 칸: 분기·Q4는 3개월 값(_q·손익), 연간은 누적. 같은 칸의 누적 값은 3개월 칸 판정을 따른다.
제외: 보고통화 외화·비12월 결산·단위 오류 종목, 재작성값·원화 환산 보호 칸, data_lock, 1,000배·100만 배 급변.
백업: 바뀐 기존 행 전체 → financial_data_backup_ofs_extra_20261009 / cash_flow_data_backup_ofs_extra_20261009(run_id).
새 행은 data_source='dart_ofs_extra_20261009', 칸별 financial_fix_log / cashflow_fix_log(old=NULL), data_fix_log 1행.
적용 후 readback(계획 칸 수 = 다시 읽은 칸 수, 값 불일치 0, 보류 수)과 재측정 결과를 결과 JSON에 남긴다.
사용: apply_ofs_extra_20261009.py [--apply] [--codes 009150,...] [--src 파일명]
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from data_write_gate import gate_cashflow_row, gate_financial_row, upsert_canonical_cashflow, upsert_canonical_financial  # noqa: E402

SRC = ROOT / "research_outputs" / "financial_rereview_20261002" / "dart_cf_ofs_extra.jsonl"
TAG = "dart_ofs_extra_20261009"
FS = "OFS"
CF = {"ocf": "operating_cf", "icf": "investing_cf", "fcf": "financing_cf", "capex": "capex"}
FIN = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity")
FLOW = {"revenue", "operating_profit", "net_income"}


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(1e6, abs(b) * 0.005)


def scale_jump(old, new):
    if not old or not new:
        return False
    r = abs(new / old)
    return any(abs(r / k - 1) < 0.01 for k in (1e3, 1e6, 1e-3, 1e-6))


def load(codes):
    t = {}
    for line in open(SRC):
        d = json.loads(line)
        if d.get("ok") and d.get("fs") == FS and d.get("vals") and (not codes or d["code"] in codes):
            t[(d["code"], d["year"], d["q"])] = d["vals"]
    return t


def targets(truth, code, year):
    """(키, 손익·재무상태 dict, 현금흐름 누적 dict, 현금흐름 3개월 dict). 키 = 'Q1'..'Q4' | 'A'."""
    g = lambda q: truth.get((code, year, q))  # noqa: E731
    out = {}
    fin_of = lambda v: {"revenue": v.get("revenue"), "operating_profit": v.get("operating_profit"), "net_income": v.get("ni_total"),  # noqa: E731
                        "total_assets": v.get("total_assets"), "total_liabilities": v.get("total_liabilities"),
                        "total_equity": v.get("equity_total")}
    for q in (1, 2, 3):
        v = g(q)
        if not v:
            continue
        cum = {c: (abs(v[k]) if k == "capex" else v[k]) if v.get(k) is not None else None for k, c in CF.items()}
        prev = g(q - 1) if q > 1 else {}
        qv = {}
        for k, c in CF.items():
            if v.get(k) is None:
                continue
            if q == 1:
                qv[c] = cum[c]
            elif prev and prev.get(k) is not None:
                qv[c] = abs(v[k]) - abs(prev[k]) if k == "capex" else v[k] - prev[k]
        _sum = lambda x: (abs(x["capex"]) + abs(x.get("capex_intangible") or 0)) if x and x.get("capex") is not None else None  # noqa: E731
        cum["capex_sum"] = _sum(v)
        if cum["capex_sum"] is not None:
            qv["capex_sum"] = cum["capex_sum"] if q == 1 else (cum["capex_sum"] - _sum(prev) if _sum(prev) is not None else None)
        out[f"Q{q}"] = (fin_of(v), cum, qv)
    a = g(0)
    if a:
        cum = {c: (abs(a[k]) if k == "capex" else a[k]) if a.get(k) is not None else None for k, c in CF.items()}
        _sum = lambda x: (abs(x["capex"]) + abs(x.get("capex_intangible") or 0)) if x and x.get("capex") is not None else None  # noqa: E731
        cum["capex_sum"] = _sum(a)
        out["A"] = (fin_of(a), cum, {})
        q1, q2, q3 = g(1), g(2), g(3)
        fin4 = {"total_assets": a.get("total_assets"), "total_liabilities": a.get("total_liabilities"), "total_equity": a.get("equity_total")}
        if q1 and q2 and q3:
            fa, f1, f2, f3 = fin_of(a), fin_of(q1), fin_of(q2), fin_of(q3)
            for f in FLOW:
                if all(x.get(f) is not None for x in (fa, f1, f2, f3)):
                    fin4[f] = fa[f] - f1[f] - f2[f] - f3[f]
        qv4 = {}
        if q3:
            for k, c in CF.items():
                if a.get(k) is not None and q3.get(k) is not None:
                    qv4[c] = abs(a[k]) - abs(q3[k]) if k == "capex" else a[k] - q3[k]
        if q3 and _sum(a) is not None and _sum(q3) is not None:
            qv4["capex_sum"] = _sum(a) - _sum(q3)
        out["Q4"] = (fin4, cum, qv4)
    return out


FD_COLS = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity",
           "capital_stock", "eps", "bps", "dps", "roe")
CFC_COLS = ("operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation")


def canonical_sync(conn, run_id, now, fd_ids, cf_ids):
    """원장 행 → 게이트 → 정본 upsert(연간 키는 quarter=0), 기존 정본 행 백업, readback. 게이트 거부 행은 정본을 건드리지 않고 보류."""
    out = {"fd_rows": len(fd_ids), "cf_rows": len(cf_ids), "fd_synced": 0, "cf_synced": 0,
           "fd_refused": 0, "cf_refused": 0, "refused_sample": [], "readback_mismatch": 0, "mismatch_sample": []}
    specs = (("financial_data", "canonical_financial_data", FD_COLS, fd_ids, gate_financial_row, upsert_canonical_financial, "fd"),
             ("cash_flow_data", "canonical_cashflow_data", CFC_COLS, cf_ids, gate_cashflow_row, upsert_canonical_cashflow, "cf"))
    synced_keys = []
    for raw, canon, cols, ids, gate, upsert, tag in specs:
        if not ids:
            continue
        bk = f"{canon}_backup_ofs_extra_20261009"
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {canon} WHERE false")
        rows = []
        idl = sorted(ids)
        for i in range(0, len(idl), 1000):
            part = idl[i:i + 1000]
            rows += [dict(r) for r in conn.execute(
                f"SELECT id, stock_code, year, quarter, is_annual, report_type, data_source, {','.join(cols)} FROM {raw} "
                f"WHERE id IN ({','.join('?' * len(part))})", part).fetchall()]
        # 2026-10-09: 원장 연간 두 벌(quarter 0·4)이 같은 정본 키로 들어오면 마지막 행이 덮어써 대표가 바뀐다 →
        # 정본 키마다 대표 행 하나만(정본이 가리키는 원장 행 → quarter 0 → id 작은 순), 나머지는 건너뜀
        by_key = {}
        for r in rows:
            q = 0 if r["is_annual"] else r["quarter"]
            by_key.setdefault((r["stock_code"], r["year"], q, bool(r["is_annual"]), r["report_type"]), []).append(r)
        rows = []
        for (sc, yy, qq, ia, rt), grp in by_key.items():
            if len(grp) > 1:
                cur_src = conn.execute(f"SELECT source_row_id FROM {canon} WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?",
                                       (sc, yy, qq, 1 if ia else 0, rt)).fetchone()
                cur_src = cur_src[0] if cur_src else None
                grp.sort(key=lambda r: (r["id"] != cur_src, r["quarter"], r["id"]))
                out[f"{tag}_sibling_skipped"] = out.get(f"{tag}_sibling_skipped", 0) + len(grp) - 1
            rows.append(grp[0])
        for r in rows:
            q = 0 if r["is_annual"] else r["quarter"]
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {canon} WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?",
                         (run_id, r["stock_code"], r["year"], q, 1 if r["is_annual"] else 0, r["report_type"]))
            payload = {k: r[k] for k in ("stock_code", "year", "is_annual", "report_type", "data_source") + cols}
            payload["quarter"] = q
            ok, fixed, reason = gate(conn, payload)
            if not ok:
                out[f"{tag}_refused"] += 1
                if len(out["refused_sample"]) < 30:
                    out["refused_sample"].append((raw, r["id"], r["stock_code"], r["year"], q, reason))
                continue
            upsert(conn, fixed, source_row_id=r["id"], decision_reason=run_id)
            out[f"{tag}_synced"] += 1
            synced_keys.append((canon, cols, r, q))
    conn.commit()
    for canon, cols, r, q in synced_keys:
        c = conn.execute(f"SELECT {','.join(cols)} FROM {canon} WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? AND report_type=?",
                         (r["stock_code"], r["year"], q, 1 if r["is_annual"] else 0, r["report_type"])).fetchone()
        # 게이트가 고친 칸(예: 부채=자산 오매핑 수리)은 원장과 다를 수 있어 원장 값이 아니라 '행 존재 + 원장과 같은 칸 수'로 보지 않고,
        # 핵심 손익·현금흐름 칸만 원장과 비교한다(재무상태는 게이트 수리 가능).
        keyc = ("revenue", "operating_profit", "net_income") if canon == "canonical_financial_data" else ("operating_cf", "investing_cf", "financing_cf")
        bad = c is None or any((r[k] is None) != (c[cols.index(k)] is None) or
                               (r[k] is not None and abs(float(r[k]) - float(c[cols.index(k)])) > 0.5) for k in keyc)
        if bad:
            out["readback_mismatch"] += 1
            if len(out["mismatch_sample"]) < 20:
                out["mismatch_sample"].append((canon, r["stock_code"], r["year"], q, r["report_type"]))
    out["pass"] = out["readback_mismatch"] == 0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--codes", default="")
    ap.add_argument("--src", default="", help="원문 jsonl 파일명(기본 dart_cf_ofs_extra.jsonl)")
    a = ap.parse_args()
    global SRC
    if a.src:
        SRC = SRC.with_name(a.src)
    codes = {c for c in a.codes.split(",") if c}
    truth = load(codes)
    conn = connect_primary_db(timeout=900, readonly=not a.apply)
    excl = {r[0] for r in conn.execute("""SELECT stock_code FROM stock_collection_config WHERE config_key IN
        ('fs_quirk:reporting_currency','fs_quirk:unit_scale_fixed','fs_quirk:dart_unit_error')
        OR (config_key='fs_quirk:fiscal_month' AND config_value<>'12')""").fetchall()}
    truth = {k: v for k, v in truth.items() if k[0] not in excl}
    keys = sorted({(k[0], k[1]) for k in truth})
    print(f"원문 {len(truth)}건, 종목-연도 {len(keys)} (제외 종목 {len(excl)})", flush=True)
    locks = {(r[0], r[1], r[2]) for r in conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    protected = set()
    for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
        for r in conn.execute(f"SELECT row_id, field_name FROM {log} WHERE run_id LIKE 'restated_annual_%%' OR run_id LIKE 'fx_krw_%%'").fetchall():
            protected.add((tbl, r[0], r[1]))
    fg = {}
    for r in conn.execute("""SELECT stock_code, year, quarter, field, fnguide_value FROM financial_field_verification
                             WHERE report_type='OFS' AND year>=2025 AND fnguide_value IS NOT NULL""").fetchall():
        fg[(r[0], r[1], r[2], r[3])] = float(r[4])
    code_set = sorted({k[0] for k in keys})
    fd_rows, cf_rows = {}, {}
    fd_all, cf_all = {}, {}  # 2026-10-09 검토 반영: 연간 원장 행 quarter 0·4 두 벌 — 정정은 같은 기간 행 전부(값 다른 것만)
    for i in range(0, len(code_set), 400):
        ch = code_set[i:i + 400]
        ph = ",".join("?" * len(ch))
        for r in conn.execute(f"""SELECT id,stock_code,year,quarter,is_annual,{','.join(FIN)} FROM financial_data
                                  WHERE report_type='OFS' AND year>=2025 AND stock_code IN ({ph}) ORDER BY quarter ASC""", ch).fetchall():
            r = tuple(r)
            k = "A" if r[4] else f"Q{r[3]}"
            fd_rows.setdefault((r[1], r[2], k), r)
            fd_all.setdefault((r[1], r[2], k), []).append(r)
        for r in conn.execute(f"""SELECT id,stock_code,year,quarter,is_annual,operating_cf,investing_cf,financing_cf,capex,
                                         operating_cf_q,investing_cf_q,financing_cf_q,capex_q FROM cash_flow_data
                                  WHERE report_type='OFS' AND year>=2025 AND stock_code IN ({ph}) ORDER BY quarter ASC""", ch).fetchall():
            r = tuple(r)
            k = "A" if r[4] else f"Q{r[3]}"
            cf_rows.setdefault((r[1], r[2], k), r)
            cf_all.setdefault((r[1], r[2], k), []).append(r)

    st = collections.Counter()
    upd_fd, upd_cf = [], []          # (row_id, code, year, quarter, is_ann, col, old, new, rule)
    ins_fd, ins_cf = [], []          # (code, year, quarter, is_ann, {col: val}, value_type)
    held = []
    capex_cand = []  # (row_id, code, year, 기간, 칸, 기존=유형+무형, 새 유형값, 합, FnGuide) — 값 유지, 후보만
    for code, year in keys:
        for k, (fin, cum, qv) in targets(truth, code, year).items():
            fq = 0 if k == "A" else int(k[1])
            quarter, is_ann = (0, True) if k == "A" else (fq, False)
            # financial_data
            row = fd_rows.get((code, year, k))
            if row is None:
                vals = {f: v for f, v in fin.items() if v is not None}
                if vals and (code, year, "financial_data") not in locks:
                    ins_fd.append((code, year, quarter, is_ann, vals, None))
                    st["fd_insert_row"] += 1
                    st["fd_insert_cell"] += len(vals)
            elif (code, year, "financial_data") not in locks:
                for j, f in enumerate(FIN):
                    new, old = fin.get(f), row[5 + j]
                    if new is None or close(old, new):
                        st["fd_same" if new is not None else "fd_nodart"] += 1
                        continue
                    if ("financial_data", row[0], f) in protected or scale_jump(old, new):
                        st["fd_protected_or_scale"] += 1
                        continue
                    if old is None:
                        upd_fd.append((row[0], code, year, row[3], row[4], f, None, new, "빈 칸 = DART 별도 원문"))
                        st["fd_fill"] += 1
                        continue
                    ext = fg.get((code, year, fq, f))
                    op_now = row[5 + FIN.index("operating_profit")]
                    if f == "revenue" and op_now is not None and old >= 0 and op_now > old * 1.05 and new >= op_now:
                        # 내부 항등식: 영업이익 > 매출은 불가능(data_write_gate OPERATING_PROFIT_EXCEEDS_REVENUE) → 원문값으로 정정
                        upd_fd.append((row[0], code, year, row[3], row[4], f, old, new, "기존 매출 < 영업이익(불가능) → DART 별도 원문"))
                        st["fd_fix_identity"] += 1
                    elif ext is not None and close(new, ext) and not close(old, ext):
                        upd_fd.append((row[0], code, year, row[3], row[4], f, old, new, "DART 별도 원문 = FnGuide, 기존 값 불일치"))
                        st["fd_fix_fnguide"] += 1
                    else:
                        st["fd_hold"] += 1
                        held.append(("financial_data", code, year, k, f, old, new, ext))
            # cash_flow_data
            crow = cf_rows.get((code, year, k))
            if crow is None:
                vals = {c: v for c, v in cum.items() if v is not None and c != "capex_sum"}
                vals.update({c + "_q": v for c, v in qv.items() if v is not None and c != "capex_sum"})
                if vals and (code, year, "cash_flow_data") not in locks:
                    vt = None if k == "A" else ("derived_q4" if k == "Q4" else "cumulative→derived")
                    ins_cf.append((code, year, quarter, is_ann, vals, vt))
                    st["cf_insert_row"] += 1
                    st["cf_insert_cell"] += len(vals)
            elif (code, year, "cash_flow_data") not in locks:
                cols = ["operating_cf", "investing_cf", "financing_cf", "capex"]
                for j, c in enumerate(cols):
                    ext = fg.get((code, year, fq, "capex" if c == "capex" else c))
                    # 판정 칸: 연간 = 누적, 분기 = 3개월
                    jn = c if k == "A" else c + "_q"
                    judge_new = cum.get(c) if k == "A" else qv.get(c)
                    judge_old = crow[5 + j] if k == "A" else crow[9 + j]
                    if c == "capex":
                        judge_old = abs(judge_old) if judge_old is not None else None
                    confirmed = ext is not None and judge_new is not None and close(judge_new, abs(ext) if c == "capex" else ext)
                    pairs = [(c, crow[5 + j], cum.get(c), cum.get("capex_sum"))] + \
                        ([] if k == "A" else [(c + "_q", crow[9 + j], qv.get(c), qv.get("capex_sum"))])
                    for col, old, new, sumv in pairs:
                        if c != "capex":
                            sumv = None
                        oc = abs(old) if (old is not None and c == "capex") else old
                        if new is None or close(oc, new):
                            st["cf_same" if new is not None else "cf_nodart"] += 1
                            continue
                        if ("cash_flow_data", crow[0], col) in protected or scale_jump(old, new):
                            st["cf_protected_or_scale"] += 1
                            continue
                        if old is None:
                            upd_cf.append((crow[0], code, year, crow[3], crow[4], col, None, new, "빈 칸 = DART 별도 원문"))
                            st["cf_fill"] += 1
                        elif sumv is not None and close(oc, sumv) and not close(sumv, new):
                            # §2 정의: capex = 유형자산의 증가(무형은 별도). 기존 값 = 같은 원문의 유형+무형 합(행마다 확인).
                            # 10-09 검토 반영: 교체는 그 칸 FnGuide가 새 유형값과 일치할 때만, FnGuide 없으면 '후보'로만 기록(값 유지)
                            if confirmed:
                                upd_cf.append((crow[0], code, year, crow[3], crow[4], col, old, new,
                                               f"정의 정정: 기존=유형+무형 합(같은 DART 원문), 새 유형값 = FnGuide({jn}), §2 capex=유형만"))
                                st["cf_fix_capex_def"] += 1
                            else:
                                capex_cand.append((crow[0], code, year, k, col, old, new, sumv, ext))
                                st["cf_capex_def_candidate"] += 1
                        elif confirmed and not close(judge_old, abs(ext) if c == "capex" else ext):
                            upd_cf.append((crow[0], code, year, crow[3], crow[4], col, old, new, f"DART 별도 원문 = FnGuide({jn}), 기존 값 불일치"))
                            st["cf_fix_fnguide"] += 1
                        else:
                            st["cf_hold"] += 1
                            held.append(("cash_flow_data", code, year, k, col, old, new, ext))
    # 연간 두 벌 동기화: 대표 행(quarter 0 우선)에 계획된 정정을 같은 기간의 다른 연간 행에도(값이 다를 때만) 적용
    FIN_IDX = {f: 5 + j for j, f in enumerate(FIN)}
    CF_IDX = {"operating_cf": 5, "investing_cf": 6, "financing_cf": 7, "capex": 8}
    for ups, allmap, idx, kind in ((upd_fd, fd_all, FIN_IDX, "fd"), (upd_cf, cf_all, CF_IDX, "cf")):
        extra = []
        for rid, code, year, q, ia, col, old, new, rule in ups:
            if not ia or col not in idx:
                continue
            for sib in allmap.get((code, year, "A"), []):
                if sib[0] == rid:
                    continue
                sv = sib[idx[col]]
                if sv is not None and abs(float(sv) - float(new)) <= 0.5:
                    continue
                extra.append((sib[0], code, year, sib[3], sib[4], col, sv, new, rule + " (연간 두 벌 동기화)"))
                st[f"{kind}_annual_sibling_sync"] += 1
        ups.extend(extra)
    res = {"at": datetime.now().isoformat(timespec="seconds"), "mode": "apply" if a.apply else "dry_run", "stats": dict(st),
           "fd_update": len(upd_fd), "cf_update": len(upd_cf), "fd_insert_rows": len(ins_fd), "cf_insert_rows": len(ins_cf),
           "held": len(held), "held_sample": held[:40],
           "capex_def_candidates": len(capex_cand), "capex_def_candidate_sample": capex_cand[:40], "update_sample": (upd_fd + upd_cf)[:40]}
    if a.apply:
        run_id = f"ofs_extra_apply_{datetime.now():%Y%m%d_%H%M%S}"
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute("""CREATE TABLE IF NOT EXISTS capex_def_candidates_ofs_extra_20261009
                        (run_id TEXT, row_id BIGINT, stock_code TEXT, year INT, period TEXT, field TEXT,
                         current_value DOUBLE PRECISION, candidate_value DOUBLE PRECISION, dart_ppe_plus_intangible DOUBLE PRECISION,
                         fnguide_value DOUBLE PRECISION, created_at TEXT)""")
        conn.executemany("INSERT INTO capex_def_candidates_ofs_extra_20261009 VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         [(run_id,) + tuple(c) + (now,) for c in capex_cand])
        for tbl, ups in (("financial_data", upd_fd), ("cash_flow_data", upd_cf)):
            bk = f"{tbl}_backup_ofs_extra_20261009"
            conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {tbl} WHERE false")
            ids = sorted({u[0] for u in ups})
            for i in range(0, len(ids), 1000):
                part = ids[i:i + 1000]
                conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", [run_id] + part)
            for rid, code, year, q, ia, col, old, new, rule in ups:
                conn.execute(f"UPDATE {tbl} SET {col}=?, updated_at=? WHERE id=?", (new, now, rid))
            if ids:
                conn.execute(f"UPDATE {tbl} SET data_source=COALESCE(data_source,'')||'+{TAG}' WHERE id IN (SELECT id FROM {bk} WHERE run_id=?)", (run_id,))
        logs = {"financial_data": [], "cash_flow_data": []}
        for tbl, ins in (("financial_data", ins_fd), ("cash_flow_data", ins_cf)):
            conn.execute(f"SELECT setval('{tbl}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {tbl}))")
            for code, year, q, ia, vals, vt in ins:
                cols = list(vals)
                extra = ["value_type"] if (tbl == "cash_flow_data" and vt) else []
                allc = ["stock_code", "year", "quarter", "is_annual", "report_type", "data_source", "created_at", "updated_at"] + cols + extra
                params = [code, year, q, ia, FS, TAG, now, now] + [vals[c] for c in cols] + ([vt] if extra else [])
                rid = conn.execute(f"INSERT INTO {tbl} ({','.join(allc)}) VALUES ({','.join('?' * len(allc))}) RETURNING id", params).fetchone()[0]
                logs[tbl] += [(rid, code, year, q, ia, c, None, vals[c], "새 별도 행 = DART 별도 원문") for c in cols]
        for tbl, log, rows in (("financial_data", "financial_fix_log", upd_fd + logs["financial_data"]),
                               ("cash_flow_data", "cashflow_fix_log", upd_cf + logs["cash_flow_data"])):
            conn.execute(f"SELECT setval('{log}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {log}))")
            conn.executemany(f"""INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,old_value,new_value,fix_rule,source,run_id)
                                 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                             [(now, rid, code, year, q, 1 if ia else 0, FS, col, old, new, rule, "DART fnlttSinglAcntAll OFS(--ofs-extra)", run_id)
                              for rid, code, year, q, ia, col, old, new, rule in rows])
        conn.execute("SELECT setval('data_fix_log_id_seq', (SELECT MAX(id) FROM data_fix_log))")
        conn.execute("""INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                     (now, "financial_data+cash_flow_data", f"별도(OFS) 2025~26 {len(keys)}종목-연도",
                      len(upd_fd) + len(upd_cf) + st["fd_insert_cell"] + st["cf_insert_cell"],
                      "빈 칸·빈 행 = DART 별도 원문, 기존 값 교체는 FnGuide가 새 값과 일치할 때만",
                      json.dumps(dict(st), ensure_ascii=False)[:900], f"insert fd {len(ins_fd)}·cf {len(ins_cf)} rows",
                      "scripts/review/apply_ofs_extra_20261009.py", run_id))
        conn.commit()
        res.update({"applied": True, "run_id": run_id})
        # 적용 후 다시 읽어 확인(readback, 10-09 검토 반영): 계획한 칸이 계획값으로 들어갔는지
        checks, bad = 0, []
        for tbl, rows in (("financial_data", upd_fd + logs["financial_data"]), ("cash_flow_data", upd_cf + logs["cash_flow_data"])):
            by_col = collections.defaultdict(list)
            for rid, code, year, q, ia, col, old, new, rule in rows:
                by_col[col].append((rid, new))
            for col, items in by_col.items():
                want = dict(items)
                ids = list(want)
                for i in range(0, len(ids), 1000):
                    part = ids[i:i + 1000]
                    for r in conn.execute(f"SELECT id, {col} FROM {tbl} WHERE id IN ({','.join('?' * len(part))})", part).fetchall():
                        checks += 1
                        if r[1] is None or abs(float(r[1]) - float(want[r[0]])) > 0.5:
                            bad.append((tbl, r[0], col, r[1], want[r[0]]))
        planned = len(upd_fd) + len(upd_cf) + sum(len(v) for v in logs.values())
        res["post_readback"] = {"planned_cells": planned, "cells_read": checks, "value_mismatch": len(bad), "mismatch_sample": bad[:20],
                                "updated_cells": len(upd_fd) + len(upd_cf), "inserted_rows": {"financial_data": len(ins_fd), "cash_flow_data": len(ins_cf)},
                                "inserted_cells": {k: len(v) for k, v in logs.items()}, "held": len(held),
                                "pass": checks == planned and not bad}
        print("readback", json.dumps({k: v for k, v in res["post_readback"].items() if k != "mismatch_sample"}, ensure_ascii=False), flush=True)
        # 정본(canonical) 동기화 — 10-09 검토 반영(완료 조건): 원장(financial_data·cash_flow_data)만 바꾸면
        # crud.upsert_financial_data 경로의 정본과 어긋난다 → 바뀐/새 행을 게이트 통과 후 정본에 반영, 기존 정본 행 백업, readback
        res["canonical_sync"] = canonical_sync(conn, run_id, now,
                                               {u[0] for u in upd_fd} | {r[0] for r in logs["financial_data"]},
                                               {u[0] for u in upd_cf} | {r[0] for r in logs["cash_flow_data"]})
        print("정본 동기화", json.dumps({k: v for k, v in res["canonical_sync"].items() if not k.endswith("sample")}, ensure_ascii=False), flush=True)
        res["post_readback"]["pass"] = res["post_readback"]["pass"] and res["canonical_sync"]["pass"]
        # 원문 후보 적재 뒤 FnGuide 대조로 재측정(확정 여부는 이 결과로만 말한다)
        import subprocess
        conn.close()
        pr = subprocess.run([sys.executable, str(ROOT / "scripts" / "review" / "build_field_verification_20261003.py")],
                            capture_output=True, text=True, cwd=str(ROOT))
        res["field_verification_after"] = (pr.stdout or pr.stderr).strip().splitlines()[-1:] 
        print("재측정", res["field_verification_after"], flush=True)
    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"ofs_extra_apply_20261009_{'apply' if a.apply else 'dry'}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    print(json.dumps({k: res[k] for k in ("mode", "stats", "fd_update", "cf_update", "fd_insert_rows", "cf_insert_rows", "held", "capex_def_candidates")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
