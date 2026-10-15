#!/usr/bin/env python3
"""OFS 사업보고서 **본문 표 후보** 40칸 안전 적용기 (계약: docs/EXECUTION_CONTRACT_OFS_DOC_CANDIDATES_20261010.md).

배경 (PLAN_99_99_NUMERIC_20261009.md §7, 10-10 10:41~10:43)
    DART 재무 API가 별도(OFS) 값을 주지 않는 'DB 없음' 칸 중, 이미 받아 둔 사업보고서 본문
    zip(`4. 재무제표` 절)에서 추출한 후보 파일이 `ofs_doc_candidates_20261010_104125.csv`다.
    본문 후보는 **DART 후보일 뿐**이므로(원칙 0) 런타임 FnGuide 재검증을 통과한 칸만 채운다.

계약 불변 조건
    1. 입력 41칸 중 `fg_match_2m == True`인 40칸만 진행한다. `065150/2023/OFS/operating_cf`
       (1,000배 불일치)는 **어떠한 경우에도** 쓰지 않고 `rejected_source_scale`로 남긴다.
    2. 대상은 `fs=OFS`, `quarter=0`, 필드 allowlist(손익·재무상태 6 + 현금흐름 3)인 연간값만이다.
       Q1~Q4 행을 만들거나 계산하지 않는다. CFS/외화/비12월/잠금/재작성·환산 보호는 우회하지 않는다.
    3. CSV의 과거 판정을 신뢰하지 않고, **매 실행** `financial_field_verification`에서 FnGuide를
       다시 읽어 `abs(candidate - fnguide) <= 2,000,000원`일 때만 진행한다. 이때 쓰는 FnGuide는
       **연간(quarter=0) 행 전용**이다 — 분기(q1~q4) 값으로 연간 후보를 검증하면 판정이 분기 실적에
       좌우된다(2026-10-10 Planner/Checker BLOCK). q0 행이 없거나 q0 값이 서로 모순이면 키를 만들지
       않고 `held_fnguide_changed_or_missing`으로 보류한다.
    4. 기존 DB 값이 NULL/행 없음인 칸만 채운다. 기존 비NULL은 같으면 noop, 다르면
       `held_existing_value`로 남기고 **덮어쓰지 않는다**.
    5. 기원(provenance)은 `parse_reason == "section4_table"`만 허용(fail-closed)하고, `doc`는
       **basename으로 정규화**해 결과·fix log에 남긴다 — 절대경로·상위참조·제어문자는 저장하지 않는다.
    6. 기본 실행은 read-only dry-run(DB write 0). `--apply`는 별도 승인 전 실행하지 않는다.

쓰기 경로(`--apply` 승인 후): 표준 `data_write_gate` 게이트·정본 upsert만 사용.
    신규 연간 행은 **(table, 종목, 연도) 그룹당 1행으로 병합**해 검증 통과 칸만 채운다 — 칸별
    INSERT는 같은 연간키(종목·연도·quarter=0·OFS)에 중복 연간 행을 만든다(2026-10-10 Planner 검증).
    정본 동기화 대상에는 갱신 행과 **신규 행**을 함께 넣고, 원장이 NULL인 칸은 기존 정본 값을
    유지한다(신규 병합 행은 후보 몇 칸만 채워져 있어 그대로 upsert하면 정본의 다른 칸이 NULL로 지워진다).

산출물: `research_outputs/financial_rereview_20261002/ofs_doc_candidates_20261010_<dry|apply|apply_noop>.json`
사용:   apply_ofs_doc_candidates_20261010.py [--apply] [--input-csv 경로] [--output 경로]
"""
from __future__ import annotations

import argparse
import collections
import csv
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from db_compat import connect_primary_db  # noqa: E402
from data_write_gate import (  # noqa: E402
    gate_cashflow_row,
    gate_financial_row,
    upsert_canonical_cashflow,
    upsert_canonical_financial,
)

OUT_DIR = ROOT / "research_outputs" / "financial_rereview_20261002"
CSV_DEFAULT = OUT_DIR / "ofs_doc_candidates_20261010_104125.csv"

TAG = "dart_ofs_doc_20261010"
FS = "OFS"
FIN_ALLOW = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity")
CF_ALLOW = ("operating_cf", "investing_cf", "financing_cf")
ALLOW = FIN_ALLOW + CF_ALLOW
TABLE_OF = {f: "financial_data" for f in FIN_ALLOW}
TABLE_OF.update({f: "cash_flow_data" for f in CF_ALLOW})
COLS_OF = {
    "financial_data": ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities",
                       "total_equity", "capital_stock", "eps", "bps", "dps", "roe"),
    "cash_flow_data": ("operating_cf", "investing_cf", "financing_cf", "capex", "cash_end", "depreciation"),
}
CANON_OF = {"financial_data": "canonical_financial_data", "cash_flow_data": "canonical_cashflow_data"}
BACKUP_OF = {"financial_data": "financial_data_backup_ofs_doc_candidates_20261010",
             "cash_flow_data": "cash_flow_data_backup_ofs_doc_candidates_20261010"}
CANON_BACKUP_OF = {"financial_data": "canonical_financial_data_backup_ofs_doc_candidates_20261010",
                   "cash_flow_data": "canonical_cashflow_data_backup_ofs_doc_candidates_20261010"}
LOG_OF = {"financial_data": "financial_fix_log", "cash_flow_data": "cashflow_fix_log"}
FIX_IDX = {"financial_data": {"revenue": 1, "operating_profit": 2, "net_income": 3, "total_assets": 4,
                              "total_liabilities": 5, "total_equity": 6},
           "cash_flow_data": {"operating_cf": 1, "investing_cf": 2, "financing_cf": 3}}

FG_TOL = 2_000_000.0        # FnGuide 절대 허용오차(원) — 계약 §3.2
EQ_TOL = 0.5                # 기존 값 동일 판정(원)
PARSE_REASON_OK = "section4_table"            # 계약 §3.4 — 다른 값은 fail-closed
DOC_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")  # basename 화이트리스트(경로/제어문자 금지)
REQUIRED_CSV = ("stock_code", "year", "quarter", "fs", "field", "candidate", "doc", "parse_reason",
                "fnguide_value", "fg_match_2m")
COUNTER_KEYS = ("input_total", "csv_fg_match", "rejected_source_scale", "invalid_input", "eligible",
                "held_fnguide_changed_or_missing", "held_existing_value", "excluded_by_safety_gate",
                "planned_financial_fields", "planned_cashflow_fields", "planned_new_rows",
                "planned_new_row_fields", "merged_new_row_groups", "planned_updates",
                "doc_path_normalized", "noop")
DEFINITIONS = {
    "input_total": "CSV 데이터 행 수",
    "csv_fg_match": "CSV fg_match_2m=True(1,000배 불일치 1칸 제외)",
    "rejected_source_scale": "스케일(1,000배 등) 불일치로 영구 거부 — 쓰지 않음",
    "invalid_input": "fs/quarter/field/수치 유효성 실패(fail-closed)",
    "eligible": "모든 게이트를 통과해 쓰기 계획 또는 noop으로 판정된 칸",
    "held_fnguide_changed_or_missing": "런타임 FnGuide 재조회 실패 또는 200만원 초과 — 보류",
    "held_existing_value": "기존 비NULL 값이 후보와 다름 — 덮어쓰지 않음",
    "excluded_by_safety_gate": "data_lock/외화/비12월/단위오류/재작성·환산 보호 — 제외",
    "planned_financial_fields": "financial_data에 쓸 칸 수",
    "planned_cashflow_fields": "cash_flow_data에 쓸 칸 수",
    "planned_new_rows": "새로 만들 연간 행 수 — (table, 종목, 연도) 그룹당 1행으로 병합",
    "planned_new_row_fields": "신규 행에 채울 칸 수(칸별 INSERT 금지, 행에 병합)",
    "merged_new_row_groups": "2개 이상 칸을 한 신규 행으로 병합한 그룹 수",
    "planned_updates": "기존 NULL 칸 채우기(행 단위) 수",
    "doc_path_normalized": "doc provenance가 경로였고 basename으로 정규화된 칸 수(절대경로 미저장)",
    "noop": "이미 같은 값이 있어 아무것도 하지 않는 칸",
}


# ── 순수 헬퍼 ───────────────────────────────────────────────────────────────
def finite(v) -> bool:
    try:
        return v is not None and math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def near(a, b, tol: float) -> bool:
    return finite(a) and finite(b) and abs(float(a) - float(b)) <= tol


def scale_jump(a, b) -> bool:
    """1,000배·1,000,000배 계열 스케일 불일치(방향 무관)."""
    if not finite(a) or not finite(b) or float(a) == 0 or float(b) == 0:
        return False
    r = abs(float(b) / float(a))
    return any(abs(r / k - 1) < 0.01 for k in (1e3, 1e6, 1e-3, 1e-6))


def truthy(v) -> bool:
    return v is True or v == 1 or str(v).strip().lower() in ("1", "true", "t", "y", "yes")


def sanitize_doc(raw) -> tuple:
    """provenance 경로 → basename. 반환 (basename, 경로성 입력이었는지).

    DART 원본 zip 경로가 CSV에 절대경로/상위참조로 들어와도 결과 JSON·fix log에 경로를 남기지 않는다.
    POSIX(`/`)·Windows(`\\`) 구분자를 모두 처리하고, 남은 이름은 화이트리스트로 검증한다.
    """
    s = (raw or "").strip()
    if not s:
        return "", False
    base = re.split(r"[\\/]", s)[-1].strip()
    return base, base != s


def invalid_reason(row: dict) -> str | None:
    """fail-closed 입력 검사(계약 §3.1·§3.4). 문제가 있으면 사유 문자열."""
    if str(row.get("fs")) != FS:
        return f"fs={row.get('fs')}"
    if str(row.get("quarter")) != "0":
        return f"quarter={row.get('quarter')}"
    if row.get("field") not in ALLOW:
        return f"field={row.get('field')}"
    if not finite(row.get("candidate")):
        return "candidate_not_finite"
    if row.get("parse_reason") != PARSE_REASON_OK:
        return f"parse_reason={row.get('parse_reason')!r}"
    if not DOC_NAME_RE.match(row.get("doc") or ""):
        return "doc_provenance_invalid"
    return None


def read_candidates(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in REQUIRED_CSV if c not in (reader.fieldnames or [])]
        if missing:
            raise SystemExit(f"입력 CSV 스키마 오류 — 없는 열: {missing}")
        out = []
        for raw in reader:
            doc, doc_was_path = sanitize_doc(raw.get("doc"))
            out.append({
                "stock_code": (raw.get("stock_code") or "").strip(),
                "year": int(str(raw.get("year")).strip()),
                "quarter": int(str(raw.get("quarter")).strip()),
                "fs": (raw.get("fs") or "").strip(),
                "field": (raw.get("field") or "").strip(),
                "candidate": float(raw["candidate"]) if finite(raw.get("candidate")) else None,
                "doc": doc,
                "doc_was_path": doc_was_path,
                "parse_reason": (raw.get("parse_reason") or "").strip(),
                "csv_fnguide": float(raw["fnguide_value"]) if finite(raw.get("fnguide_value")) else None,
                "fg_match_2m": truthy(raw.get("fg_match_2m")),
            })
    return out


# ── DB 읽기 ────────────────────────────────────────────────────────────────
def annual_fnguide_map(rows_in) -> dict:
    """(code, year, field, value) 시퀀스 → {(code, year, field): value}. **연간(q0) 전용** 순수 헬퍼.

    호출자는 이미 `quarter=0 AND fnguide_value IS NOT NULL` 행만 넘긴다. 같은 연간키에 값이 2개 이상이고
    서로 다르면 어느 쪽이 정답인지 알 수 없으므로 **키를 만들지 않는다**(fail-closed → 보류).
    """
    seen: dict = {}
    ambiguous = set()
    for code, year, field, val in rows_in:
        k = (code, int(year), field)
        v = float(val)
        prev = seen.get(k)
        if prev is None:
            seen[k] = v
        elif not near(prev, v, EQ_TOL):
            ambiguous.add(k)
    for k in ambiguous:
        seen.pop(k, None)
    return seen


def load_fnguide(conn, keys) -> dict:
    """(code, year, field) → 런타임 FnGuide **연간(quarter=0)** 값. CSV 판정을 신뢰하지 않는다.

    q0 행이 없으면 키를 만들지 않는다 → `held_fnguide_changed_or_missing`. 분기(q1~q4) 행으로
    연간 후보를 대체 검증하지 않는다(2026-10-10 Planner/Checker BLOCK).
    """
    codes = sorted({k[0] for k in keys})
    years = sorted({int(k[1]) for k in keys})
    if not codes or not years:
        return {}
    ph = ", ".join("?" * len(codes))
    yh = ", ".join("?" * len(years))
    rows = conn.execute(
        f"SELECT stock_code, year, field, fnguide_value FROM financial_field_verification "
        f"WHERE report_type=? AND quarter=0 AND fnguide_value IS NOT NULL "
        f"AND stock_code IN ({ph}) AND year IN ({yh})",
        [FS] + codes + years).fetchall()
    return annual_fnguide_map(tuple(r) for r in rows)


def safety_sets(conn) -> tuple:
    locks = {(r[0], int(r[1]), r[2]) for r in
             conn.execute("SELECT stock_code, year, table_name FROM data_lock WHERE is_locked=1").fetchall()}
    excl = set()
    for key in ("fs_quirk:reporting_currency", "fs_quirk:unit_scale_fixed", "fs_quirk:dart_unit_error"):
        excl |= {r[0] for r in conn.execute(
            "SELECT stock_code FROM stock_collection_config WHERE config_key=? AND config_value IS NOT NULL",
            (key,)).fetchall()}
    excl |= {r[0] for r in conn.execute(
        "SELECT stock_code FROM stock_collection_config WHERE config_key='fs_quirk:fiscal_month' AND config_value<>'12'"
    ).fetchall()}
    protected = set()
    for tbl, log in (("financial_data", "financial_fix_log"), ("cash_flow_data", "cashflow_fix_log")):
        for r in conn.execute(
                f"SELECT row_id, field_name FROM {log} WHERE run_id LIKE 'restated_annual_%' OR run_id LIKE 'fx_krw_%'"
        ).fetchall():
            protected.add((tbl, int(r[0]), r[1]))
    return locks, excl, protected


def load_annual_rows(conn, keys) -> dict:
    """(code, year, table) → 연간 행 목록(quarter 0·4 두 벌 포함). 정본 대표 결정은 아래 대표 선택 규칙."""
    out: dict = collections.defaultdict(list)
    codes = sorted({k[0] for k in keys})
    years = sorted({int(k[1]) for k in keys})
    if not codes or not years:
        return out
    ph = ", ".join("?" * len(codes))
    yh = ", ".join("?" * len(years))
    for table, cols in COLS_OF.items():
        sql = (f"SELECT id, stock_code, year, quarter, is_annual, report_type, data_source, {','.join(cols)} "
               f"FROM {table} WHERE report_type=? AND stock_code IN ({ph}) AND year IN ({yh})")
        for r in conn.execute(sql, [FS] + codes + years).fetchall():
            r = tuple(r)
            if not truthy(r[4]):
                continue
            out[(r[1], int(r[2]), table)].append({
                "id": int(r[0]), "quarter": int(r[3]), "data_source": r[6],
                "vals": {c: (None if v is None else float(v)) for c, v in zip(cols, r[7:])},
            })
    return out


# ── 계획 ──────────────────────────────────────────────────────────────────
def plan_cells(conn, rows, fg: dict) -> dict:
    """후보 칸마다 게이트를 적용해 (쓰기 계획, 보류, 결정 목록)을 만든다. DB를 쓰지 않는다."""
    st = collections.Counter({k: 0 for k in COUNTER_KEYS})
    locks, excl_codes, protected = safety_sets(conn)
    annual = load_annual_rows(conn, [(r["stock_code"], int(r["year"])) for r in rows])
    decisions, updates, insert_groups = [], [], {}

    for row in rows:
        code, year, field = row["stock_code"], int(row["year"]), row["field"]
        cand = row["candidate"]
        dec = {"stock_code": code, "year": year, "quarter": row["quarter"], "fs": row["fs"], "field": field,
               "doc": row["doc"], "doc_path_normalized": row.get("doc_was_path", False),
               "parse_reason": row["parse_reason"], "candidate": cand,
               "csv_fnguide": row["csv_fnguide"], "csv_fg_match_2m": row["fg_match_2m"],
               "fresh_fnguide": None, "fresh_fnguide_quarter": None, "decision": "", "note": ""}
        st["input_total"] += 1
        if row.get("doc_was_path"):
            st["doc_path_normalized"] += 1
            dec["doc_note"] = "doc 경로 → basename 정규화(절대경로 미저장)"

        bad = invalid_reason(row)
        if bad:
            dec["decision"], dec["note"] = "invalid_input", bad
            st["invalid_input"] += 1
            decisions.append(dec)
            continue

        # ① CSV 단계 스케일 거부(065150 2023 OFS operating_cf) — fg_match_2m=False는 쓰지 않는다
        if not row["fg_match_2m"]:
            dec["decision"] = "rejected_source_scale"
            dec["note"] = "CSV fg_match_2m=False(1,000배 불일치)"
            st["rejected_source_scale"] += 1
            decisions.append(dec)
            continue
        st["csv_fg_match"] += 1

        # ② 런타임 FnGuide 재검증(매 실행) — 연간(q0) 행만. q0 없음/모순은 대체검증 없이 보류
        fgv = fg.get((code, year, field))
        dec["fresh_fnguide"] = fgv
        dec["fresh_fnguide_quarter"] = 0 if fgv is not None else None
        if fgv is not None and scale_jump(fgv, cand):  # 1,000배 계열은 근접 검사 이전에 거부(쓰지 않음)
            dec["decision"] = "rejected_source_scale"
            dec["note"] = f"스케일 불일치(후보 {cand:,.0f} vs FnGuide {fgv:,.0f})"
            st["rejected_source_scale"] += 1
            decisions.append(dec)
            continue
        if fgv is None or not near(cand, fgv, FG_TOL):
            dec["decision"] = "held_fnguide_changed_or_missing"
            dec["note"] = ("FnGuide 연간(q0) 값 없음 — 분기 값으로 대체검증하지 않음" if fgv is None
                           else f"|후보-FnGuide|={abs(cand - fgv):,.0f}원 > 2,000,000원")
            st["held_fnguide_changed_or_missing"] += 1
            decisions.append(dec)
            continue

        table = TABLE_OF[field]
        # ③ 안전 게이트 — 잠금·외화·비12월·단위오류·보호 칸
        if code.startswith("9"):
            dec["decision"], dec["note"] = "excluded_by_safety_gate", "외국기업(9xxxxx)"
            st["excluded_by_safety_gate"] += 1
            decisions.append(dec)
            continue
        if code in excl_codes:
            dec["decision"], dec["note"] = "excluded_by_safety_gate", "fs_quirk(외화/단위오류/비12월)"
            st["excluded_by_safety_gate"] += 1
            decisions.append(dec)
            continue
        if (code, year, table) in locks:
            dec["decision"], dec["note"] = "excluded_by_safety_gate", "data_lock"
            st["excluded_by_safety_gate"] += 1
            decisions.append(dec)
            continue

        present = annual.get((code, year, table), [])
        if any((table, r["id"], field) in protected for r in present):
            dec["decision"], dec["note"] = "excluded_by_safety_gate", "재작성·환산 보호 칸"
            st["excluded_by_safety_gate"] += 1
            decisions.append(dec)
            continue

        # ④ 기존 값 판정 — 비NULL 불일치는 덮어쓰지 않는다
        existing_vals = [(r["id"], r["vals"].get(field)) for r in present]
        differing = [(rid, v) for rid, v in existing_vals if v is not None and not near(v, cand, EQ_TOL)]
        if differing:
            dec["decision"] = "held_existing_value"
            dec["note"] = "; ".join(f"row {rid} 기존 {v:,.0f} ≠ 후보 {cand:,.0f}" for rid, v in differing)
            st["held_existing_value"] += 1
            decisions.append(dec)
            continue

        null_rows = [(rid, r["quarter"]) for r, (rid, v) in zip(present, existing_vals) if v is None]
        if not present:
            # 연간 행이 없는 (table, 종목, 연도) 그룹은 **행 1개로 병합**한다. 칸마다 INSERT하면 같은
            # 연간키(종목·연도·quarter=0·OFS)에 중복 연간 행이 생긴다(2026-10-10 Planner 검증).
            grp = insert_groups.get((table, code, year))
            if grp is None:
                grp = insert_groups[(table, code, year)] = {
                    "table": table, "stock_code": code, "year": year, "cols": {}, "fields": [],
                    "docs": [], "parse_reasons": [], "run_kind": "doc_candidate"}
                st["planned_new_rows"] += 1
            grp["cols"][field] = cand
            grp["fields"].append(field)
            if row["doc"] and row["doc"] not in grp["docs"]:
                grp["docs"].append(row["doc"])
            if row["parse_reason"] and row["parse_reason"] not in grp["parse_reasons"]:
                grp["parse_reasons"].append(row["parse_reason"])
            dec["decision"] = "planned_insert"
            dec["merge_group"] = f"{table}:{code}:{year}"
            st["planned_new_row_fields"] += 1
            st[f"planned_{'financial' if table == 'financial_data' else 'cashflow'}_fields"] += 1
            st["eligible"] += 1
        elif null_rows:
            filled = 0
            for rid, q in null_rows:
                updates.append({"table": table, "row_id": rid, "stock_code": code, "year": year, "quarter": q,
                                "is_annual": True, "col": field, "old": None, "new": cand, "doc": row["doc"],
                                "parse_reason": row["parse_reason"], "run_kind": "doc_candidate"})
                filled += 1
            st["planned_updates"] += filled
            st[f"planned_{'financial' if table == 'financial_data' else 'cashflow'}_fields"] += 1
            st["eligible"] += 1
            dec["decision"] = "planned_update"
            dec["note"] = f"NULL {filled}행 채움" + (" (연간 두 벌)" if len(present) > 1 else "")
        else:
            dec["decision"] = "noop"
            dec["note"] = "이미 같은 값"
            st["noop"] += 1
            st["eligible"] += 1
        decisions.append(dec)

    # 신규 행 병합 마무리: (table, 종목, 연도) 그룹당 1행 — 칸은 결정적 순서, provenance는 합집합
    inserts = []
    for gkey in sorted(insert_groups):
        g = insert_groups[gkey]
        g["fields"] = sorted(g["fields"])
        g["cols"] = {c: g["cols"][c] for c in g["fields"]}
        g["doc"] = "; ".join(g.pop("docs"))
        g["parse_reason"] = (g.pop("parse_reasons") or [""])[0]
        if len(g["cols"]) > 1:
            st["merged_new_row_groups"] += 1
        inserts.append(g)
    for dec in decisions:
        if dec["decision"] == "planned_insert":
            g = insert_groups[(TABLE_OF[dec["field"]], dec["stock_code"], dec["year"])]
            dec["note"] = f"신규 연간 행 1개로 병합({len(g['cols'])}칸) — {', '.join(g['fields'])}"

    return {"stats": st, "decisions": decisions, "updates": updates, "inserts": inserts,
            "targets": sorted({(r["stock_code"], int(r["year"])) for r in rows})}


# ── 쓰기 ──────────────────────────────────────────────────────────────────
def _seq(conn, table: str) -> None:
    try:
        conn.execute(f"SELECT setval('{table}_id_seq', (SELECT COALESCE(MAX(id),1) FROM {table}))")
    except Exception:  # sqlite(테스트)·시퀀스 없는 스키마
        pass


def apply_plan(conn, plan: dict, run_id: str, now: str) -> dict:
    """계획 실행: 백업 → UPDATE/INSERT → fix log → data_fix_log → readback. 반환값에 쓰기 문장 수를 담는다."""
    out = {"write_statements": 0, "backup_rows": 0, "updated_rows": 0, "updated_cells": 0,
           "inserted_rows": collections.Counter(), "inserted_cells": collections.Counter(),
           "inserted_row_ids": {}, "duplicate_annual_rows": 0,
           "log_rows": 0, "log_sample": [], "readback_checked": 0, "readback_mismatch": 0, "mismatch_sample": []}
    logs = []  # (table, row_id, code, year, q, is_annual, col, old, new, rule, doc)

    # 1) 백업(바뀌는 기존 행 전부, run_id 태깅)
    for table, ids in _affected(plan).items():
        bk = BACKUP_OF[table]
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {table} WHERE false")
        out["write_statements"] += 1
        idl = sorted(ids)
        for i in range(0, len(idl), 500):
            part = idl[i:i + 500]
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {table} WHERE id IN ({','.join('?' * len(part))})",
                         [run_id] + part)
            out["write_statements"] += 1
            out["backup_rows"] += len(part)

    # 2) UPDATE — 후보 검증된 그 칸만
    for u in plan["updates"]:
        conn.execute(f"UPDATE {u['table']} SET {u['col']}=?, updated_at=? WHERE id=?",
                     (u["new"], now, u["row_id"]))
        out["write_statements"] += 1
        out["updated_cells"] += 1
        logs.append((u["table"], u["row_id"], u["stock_code"], u["year"], u["quarter"], 1, u["col"],
                     u["old"], u["new"], "빈 칸 = DART 사업보고서 본문 후보(4. 재무제표)", u["doc"]))
    out["updated_rows"] = len({u["row_id"] for u in plan["updates"]})
    for table, ids in _affected(plan).items():
        idl = sorted(ids)
        for i in range(0, len(idl), 500):
            part = idl[i:i + 500]
            conn.execute(f"UPDATE {table} SET data_source=COALESCE(data_source,'')||? "
                         f"WHERE id IN ({','.join('?' * len(part))})", [f"+{TAG}"] + part)
            out["write_statements"] += 1

    # 3) INSERT — (table, 종목, 연도) 그룹당 **1행**으로 병합, 검증 통과 칸만 채운다
    for ins in plan["inserts"]:
        table = ins["table"]
        _seq(conn, table)
        cols = sorted(ins["cols"])
        allc = (["stock_code", "year", "quarter", "is_annual", "report_type", "data_source", "created_at",
                 "updated_at"] + cols)
        params = ([ins["stock_code"], ins["year"], 0, True, FS, TAG, now, now] + [ins["cols"][c] for c in cols])
        rid = int(conn.execute(
            f"INSERT INTO {table} ({','.join(allc)}) VALUES ({','.join('?' * len(allc))}) RETURNING id",
            params).fetchone()[0])
        out["write_statements"] += 1
        out["inserted_row_ids"].setdefault(table, []).append(rid)
        out["inserted_rows"][table] += 1
        out["inserted_cells"][table] += len(cols)
        for c in cols:
            logs.append((table, rid, ins["stock_code"], ins["year"], 0, 1, c, None, ins["cols"][c],
                         f"새 별도 연간 행({len(cols)}칸 병합) = DART 사업보고서 본문 후보(4. 재무제표)", ins["doc"]))
        # 3b) 중복 연간 행 가드: 같은 (종목, 연도, quarter=0, OFS)에 행이 2개 이상이면 결함 재발이다
        n_rows = int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE stock_code=? AND year=? AND quarter=0 AND report_type=?",
            (ins["stock_code"], ins["year"], FS)).fetchone()[0])
        if n_rows != 1:
            out["duplicate_annual_rows"] += 1
        # 3c) readback — 병합한 **모든 칸**을 다시 읽어 확인(칸별 INSERT였을 때의 '마지막 행만 검사' 구멍 제거)
        got = conn.execute(f"SELECT data_source, {','.join(cols)} FROM {table} WHERE id=?", (rid,)).fetchone()
        for i, c in enumerate(cols):
            out["readback_checked"] += 1
            if (got is None or got[1 + i] is None or abs(float(got[1 + i]) - float(ins["cols"][c])) > EQ_TOL
                    or TAG not in (got[0] or "")):
                out["readback_mismatch"] += 1
                if len(out["mismatch_sample"]) < 20:
                    out["mismatch_sample"].append((table, rid, c, None if got is None else got[1 + i],
                                                   ins["cols"][c]))

    # 4) fix log + data_fix_log
    for table, log in LOG_OF.items():
        rows_ = [l for l in logs if l[0] == table]
        if not rows_:
            continue
        _seq(conn, log)
        conn.executemany(
            f"INSERT INTO {log}(fixed_at,row_id,stock_code,year,quarter,is_annual,report_type,field_name,"
            f"old_value,new_value,fix_rule,source,run_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(now, rid, code, year, q, ia, FS, col, old, new, rule,
              f"DART 사업보고서 본문 4.재무제표 {FS} doc={doc} ({TAG})", run_id)
             for _, rid, code, year, q, ia, col, old, new, rule, doc in rows_])
        out["write_statements"] += 1
        out["log_rows"] += len(rows_)
        out["log_sample"] += [(table, rid, code, year, col, new, doc) for _, rid, code, year, q, ia, col, old, new, rule, doc in rows_[:20]]
    _seq(conn, "data_fix_log")
    conn.execute(
        "INSERT INTO data_fix_log(fixed_at,table_name,scope,row_count,fix_rule,old_value_summary,new_value_summary,source,run_id)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (now, "financial_data+cash_flow_data", f"{FS} 본문 표 후보 ({TAG})", out["updated_cells"] + sum(out["inserted_cells"].values()),
         "DART 사업보고서 본문 표 후보 → 빈 칸/NULL 연간값만 채움(런타임 FnGuide 200만원 이내 일치 시)",
         f"updates {out['updated_cells']}·inserts {sum(out['inserted_cells'].values())} "
         f"(신규 {sum(out['inserted_rows'].values())}행에 병합)",
         f"planned new rows {sum(out['inserted_rows'].values())}",
         "scripts/review/apply_ofs_doc_candidates_20261010.py", run_id))
    out["write_statements"] += 1
    conn.commit()

    # 5) readback(갱신 칸 = 다시 읽은 칸, 값 불일치 0) — 신규 행은 3c)에서 병합 칸 전부를 이미 확인했다
    for u in plan["updates"]:
        r = conn.execute(f"SELECT {u['col']} FROM {u['table']} WHERE id=?", (u["row_id"],)).fetchone()
        out["readback_checked"] += 1
        if r is None or r[0] is None or abs(float(r[0]) - float(u["new"])) > EQ_TOL:
            out["readback_mismatch"] += 1
            if len(out["mismatch_sample"]) < 20:
                out["mismatch_sample"].append((u["table"], u["row_id"], u["col"], None if r is None else r[0], u["new"]))
    out["inserted_rows"] = dict(out["inserted_rows"])
    out["inserted_cells"] = dict(out["inserted_cells"])
    out["pass"] = out["readback_mismatch"] == 0 and out["duplicate_annual_rows"] == 0
    return out


def _affected(plan: dict) -> dict:
    aff: dict = collections.defaultdict(set)
    for u in plan["updates"]:
        aff[u["table"]].add(u["row_id"])
    return aff


def build_canonical_payload(conn, canon: str, cols, r, quarter: int, is_annual: bool) -> tuple:
    """원장 대표행 → 정본 upsert payload. 원장이 NULL인 칸은 **기존 정본 값을 유지**한다.

    신규 병합 행은 후보 몇 칸만 채워져 있고 나머지는 NULL이다. 그대로 upsert하면
    `ON CONFLICT DO UPDATE SET <col>=excluded.<col>` 때문에 정본의 다른 칸이 NULL로 지워진다
    (실측 2026-10-10: 241820/2022 OFS q0 정본 행에 capital_stock·eps·bps·roe가 있고
    원장에는 OFS 연간 행 자체가 없어 새 병합 행이 그 칸들을 비운다).

    반환: (payload, 기존값을 유지한 칸 수)
    """
    raws = {c: (None if v is None else float(v)) for c, v in zip(cols, r[7:])}
    prev = conn.execute(
        f"SELECT {','.join(cols)} FROM {canon} WHERE stock_code=? AND year=? AND quarter=? AND is_annual=? "
        f"AND report_type=?", (r[1], int(r[2]), int(quarter), 1 if is_annual else 0, r[5])).fetchone()
    preserved = 0
    payload = dict(raws)
    if prev is not None:
        for i, c in enumerate(cols):
            if raws[c] is None and prev[i] is not None:
                payload[c] = float(prev[i])
                preserved += 1
    payload.update({"_id": r[0], "stock_code": r[1], "year": r[2], "quarter": quarter, "is_annual": is_annual,
                    "report_type": r[5], "data_source": r[6]})
    return payload, preserved


def canonical_sync(conn, run_id: str, fd_ids, cf_ids) -> dict:
    """원장 행(갱신 + **신규 병합 행**) → 게이트 → 정본 upsert(연간 대표행 하나).

    게이트 거부는 정본 미변경 + canonical_refused. 원장이 NULL인 칸은 기존 정본 값을 유지한다.
    """
    out = {"fd_rows": len(fd_ids), "cf_rows": len(cf_ids), "fd_synced": 0, "cf_synced": 0,
           "fd_refused": 0, "cf_refused": 0, "refused": 0, "refused_sample": [], "preserved_cells": 0,
           "readback_mismatch": 0, "mismatch_sample": [], "write_statements": 0}
    specs = (("financial_data", "canonical_financial_data", COLS_OF["financial_data"], fd_ids,
              gate_financial_row, upsert_canonical_financial, "fd"),
             ("cash_flow_data", "canonical_cashflow_data", COLS_OF["cash_flow_data"], cf_ids,
              gate_cashflow_row, upsert_canonical_cashflow, "cf"))
    synced = []
    for raw, canon, cols, ids, gate, upsert, tag in specs:
        if not ids:
            continue
        bk = CANON_BACKUP_OF[raw]
        conn.execute(f"CREATE TABLE IF NOT EXISTS {bk} AS SELECT *, CAST(NULL AS TEXT) run_id FROM {canon} WHERE false")
        out["write_statements"] += 1
        idl = sorted(ids)
        rows = []
        for i in range(0, len(idl), 500):
            part = idl[i:i + 500]
            rows += [tuple(r) for r in conn.execute(
                f"SELECT id, stock_code, year, quarter, is_annual, report_type, data_source, {','.join(cols)} "
                f"FROM {raw} WHERE id IN ({','.join('?' * len(part))})", part).fetchall()]
        # 정본 키마다 대표 행 하나: 정본이 가리키는 원장 행 → quarter 0 → id 작은 순
        by_key: dict = {}
        for r in rows:
            q = 0 if truthy(r[4]) else int(r[3] or 0)
            by_key.setdefault((r[1], int(r[2]), q, truthy(r[4]), r[5]), []).append(r)
        for (sc, yy, qq, ia, rt), grp in by_key.items():
            if len(grp) > 1:
                cur = conn.execute(f"SELECT source_row_id FROM {canon} WHERE stock_code=? AND year=? AND quarter=? "
                                   f"AND is_annual=? AND report_type=?", (sc, yy, qq, 1 if ia else 0, rt)).fetchone()
                src = int(cur[0]) if cur and cur[0] is not None else None
                grp.sort(key=lambda r: (int(r[0]) != src, 0 if r[3] == 0 else 1, int(r[0])))
                out[f"{tag}_sibling_skipped"] = out.get(f"{tag}_sibling_skipped", 0) + len(grp) - 1
            r = grp[0]
            conn.execute(f"INSERT INTO {bk} SELECT *, ? FROM {canon} WHERE stock_code=? AND year=? AND quarter=? "
                         f"AND is_annual=? AND report_type=?", (run_id, sc, yy, qq, 1 if ia else 0, rt))
            out["write_statements"] += 1
            payload, preserved = build_canonical_payload(conn, canon, cols, r, qq, ia)
            out["preserved_cells"] += preserved
            ok, fixed, reason = gate(conn, payload)
            if not ok:
                out[f"{tag}_refused"] += 1
                out["refused"] += 1
                if len(out["refused_sample"]) < 30:
                    out["refused_sample"].append((raw, int(r[0]), sc, yy, qq, reason))
                continue
            upsert(conn, fixed, source_row_id=int(r[0]), decision_reason=run_id)
            out["write_statements"] += 1
            out[f"{tag}_synced"] += 1
            synced.append((canon, cols, r, qq))
    conn.commit()
    for canon, cols, r, qq in synced:
        c = conn.execute(f"SELECT {','.join(cols)} FROM {canon} WHERE stock_code=? AND year=? AND quarter=? "
                         f"AND is_annual=? AND report_type=?",
                         (r[1], int(r[2]), qq, 1 if truthy(r[4]) else 0, r[5])).fetchone()
        # 원장이 값을 가진 칸은 정본에서도 같은 값이어야 한다(원장이 NULL인 칸은 기존 정본 값 유지 대상)
        bad = c is None
        if not bad:
            for i, k in enumerate(cols):
                raw = r[7 + i]
                if raw is None:
                    continue
                if c[i] is None or abs(float(raw) - float(c[i])) > EQ_TOL:
                    bad = True
                    break
        if bad:
            out["readback_mismatch"] += 1
            if len(out["mismatch_sample"]) < 20:
                out["mismatch_sample"].append((canon, r[1], int(r[2]), qq, r[5]))
    out["readback_pass"] = out["readback_mismatch"] == 0
    # 게이트 거부가 하나라도 있으면 '통과'로 보고하지 않는다(성공 위장 금지 — 계약 §4.9)
    out["pass"] = out["readback_pass"] and out["refused"] == 0
    return out


# ── 진입점 ────────────────────────────────────────────────────────────────
def _connect(readonly: bool):
    return connect_primary_db(timeout=900, readonly=readonly)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="OFS 사업보고서 본문 표 후보 40칸 안전 적용(기본 dry-run)")
    ap.add_argument("--apply", action="store_true", help="별도 승인 후에만 — 승인 전 실행 금지")
    ap.add_argument("--input-csv", default=str(CSV_DEFAULT))
    ap.add_argument("--output", default="")
    a = ap.parse_args(argv)

    csv_path = Path(a.input_csv)
    rows = read_candidates(csv_path)
    conn = _connect(readonly=not a.apply)
    try:
        fg = load_fnguide(conn, [(r["stock_code"], r["year"]) for r in rows])
        plan = plan_cells(conn, rows, fg)
        res = {
            "at": datetime.now().isoformat(timespec="seconds"),
            "mode": "apply" if a.apply else "dry_run",
            "script": "scripts/review/apply_ofs_doc_candidates_20261010.py",
            "input_csv": csv_path.name,
            "definitions": DEFINITIONS,
            "stats": {k: plan["stats"][k] for k in COUNTER_KEYS},
            "db_read": "connect_primary_db(readonly=True)" if not a.apply else "connect_primary_db(readonly=False)",
            "db_write_statements_in_dry_run": 0 if not a.apply else None,
            "decisions": plan["decisions"],
            "planned_update_sample": plan["updates"][:40],
            "planned_insert_sample": plan["inserts"][:40],
            "run_id": None,
        }
        print(json.dumps({k: res["stats"][k] for k in COUNTER_KEYS}, ensure_ascii=False), flush=True)

        if a.apply and not (plan["updates"] or plan["inserts"]):
            # 0변경 apply: 쓰기 경로를 아예 실행하지 않는다(빈 재실행이 산출물·감사표를 늘리지 않게)
            res["run_id"] = None
            res["apply_skipped_noop"] = "계획 0건 — apply_plan()/canonical_sync() 미호출, DB 쓰기 0"
            res["db_write_statements"] = 0
            res["readback_checked"] = 0
            res["readback_mismatch"] = 0
            res["pass"] = True
        elif a.apply:
            run_id = f"ofs_doc_candidates_{datetime.now():%Y%m%d_%H%M%S}"
            now = datetime.now().isoformat(timespec="seconds")
            applied = apply_plan(conn, plan, run_id, now)
            res["run_id"] = run_id
            res["applied"] = {k: v for k, v in applied.items() if k not in ("log_sample", "mismatch_sample")}
            res["applied"]["log_sample"] = applied["log_sample"]
            res["readback_checked"] = applied["readback_checked"]
            res["readback_mismatch"] = applied["readback_mismatch"]
            res["readback_mismatch_sample"] = applied["mismatch_sample"]
            res["db_write_statements"] = applied["write_statements"]
            ins_ids = applied.get("inserted_row_ids") or {}
            res["canonical_sync"] = canonical_sync(
                conn, run_id,
                {u["row_id"] for u in plan["updates"] if u["table"] == "financial_data"}
                | set(ins_ids.get("financial_data", [])),
                {u["row_id"] for u in plan["updates"] if u["table"] == "cash_flow_data"}
                | set(ins_ids.get("cash_flow_data", [])))
            res["pass"] = bool(applied["pass"] and res["canonical_sync"]["pass"])
            print("readback", json.dumps({k: v for k, v in res["applied"].items() if k != "log_sample"},
                                        ensure_ascii=False), flush=True)
            print("canonical_sync", json.dumps({k: v for k, v in res["canonical_sync"].items()
                                                 if not k.endswith("sample")}, ensure_ascii=False), flush=True)
        else:
            res["db_write_statements"] = 0
            res["write_path_not_executed"] = "dry-run — apply_plan()/canonical_sync() 미호출"
    finally:
        conn.close()

    # 산출물 이름: 모드별 분리 + 0변경 apply가 실제 적용 결과를 덮지 않게(_apply_noop)
    planned = (res["stats"]["planned_updates"] + res["stats"]["planned_new_rows"])
    kind = "dry" if not a.apply else ("apply" if planned else "apply_noop")
    out = Path(a.output) if a.output else OUT_DIR / f"ofs_doc_candidates_20261010_{kind}.json"
    if a.apply and out.exists() and kind == "apply":
        out = out.with_name(out.stem + f"_{datetime.now():%H%M%S}.json")
    out.write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"result={out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
