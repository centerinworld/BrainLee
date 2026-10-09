#!/usr/bin/env python3
"""Normalize canonical financial annual keys and remove orphan canonical rows.

Context: canonical_financial_data is derived from financial_data via the write
gate. Some annual rows exist twice (quarter=0 and quarter=4), and many canonical
rows point at financial_data ids that no longer exist. This script makes the
derived table consistent without changing the raw financial_data ledger:

- annual canonical rows use quarter=0
- source rows are reselected from financial_data by normalized key
- stale/orphan canonical rows are backed up before deletion
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from data_write_gate import ensure_canonical_schema, gate_financial_row, upsert_canonical_financial  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402

BACKUP = "canonical_financial_data_backup_annual_norm_20261008"
RUN_PREFIX = "canonical_fin_annual_norm_20261008"
FIN_FIELDS = ("revenue", "operating_profit", "net_income", "total_assets", "total_liabilities", "total_equity")

FIN_COLS = (
    "id", "stock_code", "year", "quarter", "is_annual", "report_type",
    "revenue", "operating_profit", "net_income", "total_assets",
    "total_liabilities", "total_equity", "capital_stock", "eps", "bps",
    "dps", "roe", "data_source", "created_at", "updated_at",
)


def rows(conn, sql: str, params=()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def is_true(v: Any) -> bool:
    return bool(v) and str(v).lower() not in {"0", "false", "f"}


def norm_quarter(r: dict[str, Any]) -> int:
    q = int(r["quarter"] or 0)
    return 0 if is_true(r["is_annual"]) and q in (0, 4) else q


def norm_key(r: dict[str, Any]) -> tuple[str, int, int, int, str]:
    return (
        str(r["stock_code"]),
        int(r["year"]),
        norm_quarter(r),
        1 if is_true(r["is_annual"]) else 0,
        str(r.get("report_type") or "CFS"),
    )


def source_sort_key(r: dict[str, Any]) -> tuple[int, str, int]:
    # Annual quarter=0 is the current canonical convention. For duplicate raw
    # rows with the same normalized key, prefer the most recently touched row.
    q_pref = 1 if is_true(r["is_annual"]) and int(r["quarter"] or 0) == 0 else 0
    touched = str(r.get("updated_at") or r.get("created_at") or "")
    return (q_pref, touched, int(r["id"]))


def payload_from_source(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": r["stock_code"],
        "year": int(r["year"]),
        "quarter": norm_quarter(r),
        "is_annual": 1 if is_true(r["is_annual"]) else 0,
        "report_type": r.get("report_type") or "CFS",
        "revenue": r.get("revenue"),
        "operating_profit": r.get("operating_profit"),
        "net_income": r.get("net_income"),
        "total_assets": r.get("total_assets"),
        "total_liabilities": r.get("total_liabilities"),
        "total_equity": r.get("total_equity"),
        "capital_stock": r.get("capital_stock"),
        "eps": r.get("eps"),
        "bps": r.get("bps"),
        "dps": r.get("dps"),
        "roe": r.get("roe"),
        "data_source": r.get("data_source"),
    }


def close(a: Any, b: Any) -> bool:
    # REVIEW_PLAN §44 ①: FnGuide 대조 스크립트(compare_db_vs_fnguide_raw_20261003.close)와 같은 기준.
    # 예전 1억 원 하한은 작은 회사의 서로 다른 값(영업이익 수억 원)을 '같음'으로 봤다.
    return a is not None and b is not None and abs(float(a) - float(b)) <= max(2e6, abs(float(b)) * 0.005)


def classify_gate_refusal(payload: dict[str, Any]) -> str:
    """§44 ④: 게이트 거부가 'CFS 비지배지분 상한(diff ≤ 지배자본)' 때문만인지 구분."""
    try:
        ta, tl, te = (float(payload[k]) for k in ("total_assets", "total_liabilities", "total_equity"))
    except Exception:
        return "other"
    diff = ta - tl - te
    scale = max(abs(ta), 1.0)
    liab_match = abs(tl - ta) / scale < 0.03
    eq_match = abs(te - ta) / scale < 0.03
    if (payload.get("report_type") or "CFS") == "CFS" and diff > 0 and not liab_match and not eq_match and not ((te > 0 and te >= (ta - tl) * 0.05) or (te < 0 and ta - tl < 0)):
        return "nci_cap_only"
    return "other"


def material_values_differ(candidates: list[dict[str, Any]]) -> bool:
    if len(candidates) <= 1:
        return False
    base = candidates[0]
    for other in candidates[1:]:
        for f in FIN_FIELDS:
            a, b = base.get(f), other.get(f)
            if a is None and b is None:
                continue
            if a is None or b is None or not close(a, b):
                return True
    return False


def fnguide_score(src: dict[str, Any], fg: dict[tuple[str, int, int, str, str], float]) -> tuple[int, int]:
    key = norm_key(src)
    matched = compared = 0
    for f in FIN_FIELDS:
        ev = fg.get((key[0], key[1], key[2], key[4], f))
        if ev is None or src.get(f) is None:
            continue
        compared += 1
        if close(src.get(f), ev):
            matched += 1
    return matched, compared


def select_source(
    candidates: list[dict[str, Any]],
    fg: dict[tuple[str, int, int, str, str], float],
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
    if not candidates:
        return None, "no_source", []
    if not material_values_differ(candidates):
        return max(candidates, key=source_sort_key), "sources_equivalent", []
    scored = []
    for src in candidates:
        matched, compared = fnguide_score(src, fg)
        scored.append({
            "id": src["id"],
            "quarter": src["quarter"],
            "data_source": src.get("data_source"),
            "matched": matched,
            "compared": compared,
        })
    best = max(scored, key=lambda x: (x["matched"], x["compared"]))
    tied = [s for s in scored if (s["matched"], s["compared"]) == (best["matched"], best["compared"])]
    if best["matched"] > 0 and len(tied) == 1:
        chosen = next(src for src in candidates if int(src["id"]) == int(best["id"]))
        return chosen, "fnguide_selected", scored
    return None, "ambiguous_keep_existing", scored


def row_from_canonical(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "stock_code": c["stock_code"],
        "year": int(c["year"]),
        "quarter": norm_quarter(c),
        "is_annual": 1 if is_true(c["is_annual"]) else 0,
        "report_type": c.get("report_type") or "CFS",
        **{f: c.get(f) for f in FIN_FIELDS},
    }


def canonical_sort_key(r: dict[str, Any]) -> tuple[int, str, int]:
    q_pref = 1 if is_true(r["is_annual"]) and int(r["quarter"] or 0) == 0 else 0
    touched = str(r.get("updated_at") or "")
    return (q_pref, touched, int(r.get("id") or 0))


def best_by_norm_key(recs: list[dict[str, Any]]) -> dict[tuple[str, int, int, int, str], dict[str, Any]]:
    out: dict[tuple[str, int, int, int, str], dict[str, Any]] = {}
    for r in recs:
        key = norm_key(r)
        cur = out.get(key)
        if cur is None or canonical_sort_key(r) > canonical_sort_key(cur):
            out[key] = r
    return out


def fnguide_sample_quality(best: dict[tuple[str, int, int, int, str], dict[str, Any]],
                           fnguide: dict[tuple[str, int, int, str, str], float],
                           sample_n: int = 300) -> dict[str, Any]:
    items = sorted(
        fnguide.items(),
        key=lambda kv: hashlib.md5("|".join(map(str, kv[0])).encode()).hexdigest(),
    )[:sample_n]
    compared = matched = missing = 0
    for (code, year, quarter, fs, field), ev in items:
        row = best.get((code, year, quarter, 1 if quarter == 0 else 0, fs))
        if not row or row.get(field) is None:
            missing += 1
            continue
        compared += 1
        if close(row.get(field), ev):
            matched += 1
    return {
        "sample_n": len(items),
        "compared": compared,
        "matched": matched,
        "missing": missing,
        "match_rate": (matched / compared if compared else None),
    }


def nci_cap_refusal_checks(conn, refused: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    """Attach external evidence for rows refused only by the NCI cap.

    FnGuide snapshots store total equity for some rows, while the canonical
    field is parent equity. A matching total-equity snapshot proves A-L is
    plausible, but without parent equity it is still a hold, not an apply.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    for r in refused:
        if r.get("refusal_class") != "nci_cap_only":
            continue
        code, year, _quarter, _is_annual, fs = r["key"]
        key = (str(code), int(year), str(fs))
        if key in seen:
            continue
        seen.add(key)
        vals = r["values"]
        total_equity_by_source = None
        if vals.get("total_assets") is not None and vals.get("total_liabilities") is not None:
            total_equity_by_source = float(vals["total_assets"]) - float(vals["total_liabilities"])
        snap = conn.execute(
            """SELECT total_assets,total_liabilities,total_equity,source_url,fetched_at
               FROM financial_source_snapshot
               WHERE stock_code=? AND year=? AND report_type=? AND is_annual=1 AND data_source='fnguide'
                 AND source_url LIKE 'https://wcomp.fnguide.com%'
               ORDER BY fetched_at DESC LIMIT 1""",
            (code, year, fs),
        ).fetchone()
        ver_rows = rows(
            conn,
            """SELECT field, db_value, fnguide_value, status, cause, fnguide_source
               FROM financial_field_verification
               WHERE stock_code=? AND year=? AND quarter=0 AND report_type=?
                 AND field IN ('total_assets','total_liabilities','total_equity')
               ORDER BY field""",
            (code, year, fs),
        )
        fg_total_equity = snap["total_equity"] if snap else None
        out.append({
            "stock_code": code,
            "year": year,
            "report_type": fs,
            "source_total_assets": vals.get("total_assets"),
            "source_total_liabilities": vals.get("total_liabilities"),
            "source_parent_equity": vals.get("total_equity"),
            "source_total_equity_by_assets_minus_liabilities": total_equity_by_source,
            "parent_equity_to_total_equity_ratio": (
                float(vals["total_equity"]) / total_equity_by_source
                if vals.get("total_equity") is not None and total_equity_by_source not in (None, 0)
                else None
            ),
            "fnguide_snapshot_total_assets": snap["total_assets"] if snap else None,
            "fnguide_snapshot_total_liabilities": snap["total_liabilities"] if snap else None,
            "fnguide_snapshot_total_equity": fg_total_equity,
            "fnguide_total_equity_matches_source_total": (
                close(fg_total_equity, total_equity_by_source)
                if fg_total_equity is not None and total_equity_by_source is not None
                else None
            ),
            "field_verification": ver_rows,
            "decision": "hold_review_parent_equity_missing",
        })
        if len(out) >= limit:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--stock-code", help="limit repair to one stock_code for a smaller reviewed apply")
    args = ap.parse_args()

    run_id = f"{RUN_PREFIX}_{datetime.now():%H%M%S}"
    now = datetime.now().isoformat(timespec="seconds")
    conn = connect_primary_db(timeout=180, readonly=not args.apply)
    gate_conn = conn if args.apply else sqlite3.connect(":memory:")
    ensure_canonical_schema(gate_conn)

    source_where = " WHERE stock_code=?" if args.stock_code else ""
    source_params = (args.stock_code,) if args.stock_code else ()
    sources = rows(conn, f"SELECT {','.join(FIN_COLS)} FROM financial_data{source_where}", source_params)
    candidates_by_key: dict[tuple[str, int, int, int, str], list[dict[str, Any]]] = defaultdict(list)
    candidates_per_key: Counter[tuple[str, int, int, int, str]] = Counter()
    for src in sources:
        key = norm_key(src)
        candidates_per_key[key] += 1
        candidates_by_key[key].append(src)

    fnguide = {
        (str(r["stock_code"]), int(r["year"]), int(r["quarter"]), str(r["report_type"]), str(r["field"])): r["fnguide_value"]
        for r in rows(
            conn,
            """SELECT stock_code,year,quarter,report_type,field,fnguide_value
               FROM financial_field_verification
               WHERE quarter=0 AND field IN ('revenue','operating_profit','net_income','total_assets','total_liabilities','total_equity')
                 AND fnguide_value IS NOT NULL"""
        )
    }

    selected: dict[tuple[str, int, int, int, str], dict[str, Any]] = {}
    selection_reason: dict[tuple[str, int, int, int, str], str] = {}
    selection_scores: dict[tuple[str, int, int, int, str], list[dict[str, Any]]] = {}
    for key, cand in candidates_by_key.items():
        src, reason, scores = select_source(cand, fnguide)
        selection_reason[key] = reason
        if scores:
            selection_scores[key] = scores
        if src is not None:
            selected[key] = src

    stock_filter = " AND c.stock_code=?" if args.stock_code else ""
    affected_params = (args.stock_code,) if args.stock_code else ()
    affected = rows(
        conn,
        f"""SELECT c.*
           FROM canonical_financial_data c
           LEFT JOIN financial_data f ON f.id=c.source_row_id
           WHERE ((c.is_annual=1 AND c.quarter=4)
              OR (c.source_row_id IS NOT NULL AND f.id IS NULL))
             {stock_filter}
           ORDER BY c.stock_code, c.year, c.quarter, c.is_annual, c.report_type, c.id""",
        affected_params,
    )

    all_canonical_for_affected_keys = rows(
        conn,
        f"""SELECT c.*
            FROM canonical_financial_data c
            WHERE EXISTS (
              SELECT 1 FROM canonical_financial_data x
              LEFT JOIN financial_data f ON f.id=x.source_row_id
              WHERE ((x.is_annual=1 AND x.quarter=4)
                 OR (x.source_row_id IS NOT NULL AND f.id IS NULL))
                {stock_filter.replace('c.', 'x.')}
                AND x.stock_code=c.stock_code AND x.year=c.year AND x.is_annual=c.is_annual
                AND COALESCE(x.report_type,'CFS')=COALESCE(c.report_type,'CFS')
                AND (CASE WHEN x.is_annual=1 AND x.quarter IN (0,4) THEN 0 ELSE x.quarter END)
                    = (CASE WHEN c.is_annual=1 AND c.quarter IN (0,4) THEN 0 ELSE c.quarter END)
            )""",
        affected_params,
    )
    canonical_by_key: dict[tuple[str, int, int, int, str], list[dict[str, Any]]] = defaultdict(list)
    for c in all_canonical_for_affected_keys:
        canonical_by_key[norm_key(c)].append(c)

    upserts: dict[tuple[str, int, int, int, str], dict[str, Any]] = {}
    deletes: list[dict[str, Any]] = []
    no_source: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    kept_review: list[dict[str, Any]] = []
    action_counts: Counter[str] = Counter()

    for canon in affected:
        key = norm_key(canon)
        src = selected.get(key)
        delete_needed = is_true(canon["is_annual"]) and int(canon["quarter"]) == 4
        source_missing = not candidates_by_key.get(key)
        if source_missing:
            # §47: 원장 근거가 없어도 FnGuide와 한 항목 이상 맞으면 지우지 않고 보류(원장 복구 대상). 맞는 근거가 없으면 삭제(백업).
            _m, _n = fnguide_score(canon, fnguide)
            if _m > 0:
                kept_review.append({"canonical_id": canon["id"], "key": key, "reason": "no_source_fnguide_match",
                                    "scores": [{"matched": _m, "compared": _n}], "_canon": canon})
                action_counts["keep_existing_review"] += 1
                continue
            no_source.append(canon)
            deletes.append({**canon, "_action": "delete_no_source"})
            action_counts["delete_no_source"] += 1
            continue
        if src is None:
            kept_review.append({
                "canonical_id": canon["id"],
                "key": key,
                "reason": selection_reason.get(key, "ambiguous_keep_existing"),
                "scores": selection_scores.get(key, []),
                "_canon": canon,
            })
            action_counts["keep_existing_review"] += 1
            continue

        payload = payload_from_source(src)
        ok, fixed, reason = gate_financial_row(gate_conn, payload)
        if not ok:
            refused.append({
                "canonical_id": canon["id"],
                "source_id": src["id"],
                "key": key,
                "reason": reason,
                "refusal_class": classify_gate_refusal(payload),
                "values": {k: payload.get(k) for k in ("total_assets", "total_liabilities", "total_equity")},
            })
            kept_review.append({
                "canonical_id": canon["id"],
                "source_id": src["id"],
                "key": key,
                "reason": f"gate_refused:{reason}",
                "_canon": canon,
            })
            action_counts["keep_existing_review"] += 1
            continue
        upserts[key] = {"source": src, "payload": fixed}

        canon_key = (
            str(canon["stock_code"]),
            int(canon["year"]),
            int(canon["quarter"]),
            1 if is_true(canon["is_annual"]) else 0,
            str(canon.get("report_type") or "CFS"),
        )
        target_key = (key[0], key[1], key[2], key[3], key[4])
        if delete_needed and canon_key != target_key:
            deletes.append({**canon, "_action": "delete_annual_q4"})
            action_counts["delete_annual_q4"] += 1
        elif int(canon.get("source_row_id") or 0) != int(src["id"]):
            action_counts["update_orphan_same_key"] += 1

    # §44 ③: 보류 행이 연간 q4면 값은 그대로 두고 quarter만 0으로 재표기(같은 키 q0 정본이 없을 때). 있으면 '두 벌'로 표시.
    deleted_ids = {int(d["id"]) for d in deletes}
    rekeys: list[dict[str, Any]] = []
    dual_review: list[dict[str, Any]] = []
    seen_rekey_keys: set = set()
    for k in kept_review:
        canon = k.get("_canon") or {}
        if not (is_true(canon.get("is_annual")) and int(canon.get("quarter") or 0) == 4):
            continue
        key = norm_key(canon)
        has_q0 = any(int(c.get("quarter") or 0) == 0 and int(c["id"]) not in deleted_ids
                     for c in canonical_by_key.get(key, []))
        if has_q0 and key not in upserts and key not in seen_rekey_keys:
            # §45: 같은 키 q0 정본이 있으면 두 벌 판정 — 값이 같거나 q0가 FnGuide에 더 맞으면 q4 삭제,
            # q4가 더 맞으면 q0를 지우고 q4를 q0로 재표기, 그 외(동점·FnGuide 없음)는 둘 다 유지·보류.
            q0row = next(c for c in canonical_by_key.get(key, [])
                         if int(c.get("quarter") or 0) == 0 and int(c["id"]) not in deleted_ids)
            if not material_values_differ([q0row, canon]):
                deletes.append({**canon, "_action": "delete_dual_q4_same"}); action_counts["delete_dual_q4_same"] += 1
                deleted_ids.add(int(canon["id"])); k["resolved"] = "dual_q4_same_deleted"; continue
            s0, n0 = fnguide_score(q0row, fnguide)
            s4, n4 = fnguide_score(canon, fnguide)
            if s0 > s4:
                deletes.append({**canon, "_action": "delete_dual_q4_fnguide"}); action_counts["delete_dual_q4_fnguide"] += 1
                deleted_ids.add(int(canon["id"])); k["resolved"] = "dual_q0_fnguide_kept"; continue
            if s4 > s0:
                deletes.append({**q0row, "_action": "delete_dual_q0_fnguide"}); action_counts["delete_dual_q0_fnguide"] += 1
                deleted_ids.add(int(q0row["id"])); rekeys.append(canon); seen_rekey_keys.add(key)
                k["resolved"] = "dual_q4_fnguide_rekeyed"; continue
        if has_q0 or key in upserts or key in seen_rekey_keys:
            dual_review.append({"canonical_id": canon["id"], "key": key, "reason": k["reason"]})
            k["dual"] = True
        else:
            rekeys.append(canon)
            seen_rekey_keys.add(key)
    for k in kept_review:
        k.pop("_canon", None)

    current_annual_q4 = conn.execute(
        "SELECT COUNT(*) FROM canonical_financial_data WHERE is_annual=1 AND quarter=4"
        + (" AND stock_code=?" if args.stock_code else ""),
        source_params,
    ).fetchone()[0]
    current_orphans = conn.execute(
        """SELECT COUNT(*) FROM canonical_financial_data c
           LEFT JOIN financial_data f ON f.id=c.source_row_id
           WHERE c.source_row_id IS NOT NULL AND f.id IS NULL"""
        + (" AND c.stock_code=?" if args.stock_code else ""),
        source_params,
    ).fetchone()[0]
    delete_annual_q4 = sum(1 for d in deletes if d["_action"] == "delete_annual_q4")
    # §44 ②: 어떤 이유로든 지워지는 연간 q4(delete_no_source 포함)와 재표기 행을 모두 뺀다
    removed_annual_q4 = sum(1 for d in deletes if is_true(d.get("is_annual")) and int(d.get("quarter") or 0) == 4)
    resolved_orphans = action_counts["update_orphan_same_key"] + sum(
        1 for d in deletes if d.get("source_row_id") is not None and d["_action"] in {"delete_no_source", "delete_annual_q4"}
    )

    canonical_scope_where = " WHERE stock_code=?" if args.stock_code else ""
    canonical_rows = rows(
        conn,
        "SELECT id,stock_code,year,quarter,is_annual,report_type,"
        + ",".join(FIN_FIELDS)
        + ",source_row_id,updated_at FROM canonical_financial_data"
        + canonical_scope_where,
        source_params,
    )
    current_best = best_by_norm_key(canonical_rows)
    projected_rows_by_id = {int(r["id"]): dict(r) for r in canonical_rows}
    for d in deletes:
        projected_rows_by_id.pop(int(d["id"]), None)
    next_negative_id = -1
    for item in upserts.values():
        payload = item["payload"]
        key = norm_key(payload)
        hit_id = None
        for rid, row in projected_rows_by_id.items():
            if norm_key(row) == key:
                hit_id = rid
                break
        if hit_id is None:
            hit_id = next_negative_id
            next_negative_id -= 1
        projected_rows_by_id[hit_id] = {
            "id": hit_id,
            **payload,
            "source_row_id": int(item["source"]["id"]),
            "updated_at": now,
        }
    projected_best = best_by_norm_key(list(projected_rows_by_id.values()))
    sample_before = fnguide_sample_quality(current_best, fnguide)
    sample_after = fnguide_sample_quality(projected_best, fnguide)

    result = {
        "run_id": run_id,
        "mode": "apply" if args.apply else "dry_run",
        "stock_code": args.stock_code,
        "financial_source_rows": len(sources),
        "selected_source_keys": len(selected),
        "affected_canonical_rows": len(affected),
        "upsert_keys": len(upserts),
        "delete_rows": len(deletes),
        "delete_by_action": dict(action_counts),
        "kept_review_count": len([k for k in kept_review if not k.get("resolved")]),
        "kept_review_resolved_dual": dict(Counter(k["resolved"] for k in kept_review if k.get("resolved"))),
        "kept_review_by_reason": dict(Counter(str(k["reason"]).split(":", 1)[0] for k in kept_review if not k.get("resolved"))),
        "kept_review_examples": kept_review[:25],
        "no_source_rows": len(no_source),
        "refused_count": len(refused),
        "refused_by_class": dict(Counter(r["refusal_class"] for r in refused)),
        "refused_examples": refused[:25],
        "nci_cap_refusal_checks": nci_cap_refusal_checks(conn, refused),
        "refused_all": refused,
        "dual_review": dual_review,
        "multi_source_keys": sum(1 for n in candidates_per_key.values() if n > 1),
        "selection_reason_counts": dict(Counter(selection_reason.values())),
        "selection_score_examples": [
            {"key": key, "reason": selection_reason.get(key), "scores": scores}
            for key, scores in list(selection_scores.items())[:25]
        ],
        "projected_completion": {
            "annual_q4_rows_before": current_annual_q4,
            "annual_q4_rows_after": max(0, current_annual_q4 - removed_annual_q4 - len(rekeys)),
            "annual_q4_removed_by_delete": removed_annual_q4,
            "annual_q4_rekeyed_to_q0": len(rekeys),
            "annual_q4_dual_kept": len(dual_review),
            "orphan_source_rows_before": current_orphans,
            "orphan_source_rows_after_excluding_kept_review": max(0, current_orphans - resolved_orphans),
            "fnguide_sample_before": sample_before,
            "fnguide_sample_after": sample_after,
            "fnguide_sample_not_worse": (
                sample_after["match_rate"] is not None
                and sample_before["match_rate"] is not None
                and sample_after["match_rate"] >= sample_before["match_rate"]
            ),
        },
        "samsung_examples_before": rows(
            conn,
            """SELECT id,year,quarter,is_annual,report_type,operating_profit,net_income,source_row_id,decision_reason
               FROM canonical_financial_data
               WHERE stock_code='009150' AND is_annual=1 AND report_type='OFS'
               ORDER BY year, quarter""",
        ),
    }

    if args.apply:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {BACKUP} (run_id TEXT, action TEXT, backed_up_at TEXT, row_json TEXT)")
        backup_rows = [(run_id, d["_action"], now, json.dumps({k: v for k, v in d.items() if k != "_action"}, ensure_ascii=False, default=str)) for d in deletes]
        if backup_rows:
            conn.executemany(f"INSERT INTO {BACKUP} (run_id, action, backed_up_at, row_json) VALUES (?,?,?,?)", backup_rows)
        # §47: 덮어쓰기 전 기존 정본 행도 백업(같은 키 갱신 9천여 행의 이전 값이 남지 않던 문제)
        _del_ids = {int(d["id"]) for d in deletes}
        pre_upsert = [r for key in upserts for r in canonical_by_key.get(key, []) if int(r["id"]) not in _del_ids]
        if pre_upsert:
            conn.executemany(f"INSERT INTO {BACKUP} (run_id, action, backed_up_at, row_json) VALUES (?,?,?,?)",
                             [(run_id, "pre_upsert", now, json.dumps(r, ensure_ascii=False, default=str)) for r in pre_upsert])
        result["pre_upsert_backup_rows"] = len(pre_upsert)
        for item in upserts.values():
            upsert_canonical_financial(
                conn,
                item["payload"],
                source_row_id=int(item["source"]["id"]),
                decision_reason=run_id,
                quality_score=None,
            )
        if deletes:
            ids = [int(d["id"]) for d in deletes]
            for i in range(0, len(ids), 1000):
                chunk = ids[i:i + 1000]
                conn.execute(f"DELETE FROM canonical_financial_data WHERE id IN ({','.join('?' for _ in chunk)})", tuple(chunk))
        # 재표기는 삭제 뒤에(지워질 q0와 키가 겹치지 않게)
        if rekeys:
            conn.executemany(f"INSERT INTO {BACKUP} (run_id, action, backed_up_at, row_json) VALUES (?,?,?,?)",
                             [(run_id, "rekey_annual_q4_to_q0", now, json.dumps(r, ensure_ascii=False, default=str)) for r in rekeys])
            for r in rekeys:
                conn.execute("UPDATE canonical_financial_data SET quarter=0 WHERE id=?", (int(r["id"]),))
        conn.execute("SELECT setval('data_fix_log_id_seq',(SELECT MAX(id) FROM data_fix_log))")   # §47: 번호 동기화(다른 정정 스크립트와 같음)
        conn.execute(
            """INSERT INTO data_fix_log
               (fixed_at, table_name, scope, row_count, fix_rule, old_value_summary, new_value_summary, source, run_id)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                now,
                "canonical_financial_data",
                "연간 canonical quarter=0 정규화 및 source_row_id 고아 행 제거",
                len(deletes) + len(upserts),
                "financial_data 원장 normalized key 기준 재동기화; annual q4 canonical 삭제; source 없는 canonical 삭제",
                json.dumps({"affected": len(affected), "delete_by_action": dict(action_counts), "refused": len(refused)}, ensure_ascii=False),
                json.dumps({"upsert_keys": len(upserts), "delete_rows": len(deletes), "rekey_q4_to_q0": len(rekeys), "backup_table": BACKUP}, ensure_ascii=False),
                "scripts/review/repair_canonical_financial_annual_keys_20261008.py",
                run_id,
            ),
        )
        conn.commit()
        result["post"] = {
            "annual_q0q4_dups": conn.execute(
                """WITH g AS (
                     SELECT stock_code,year,is_annual,report_type,
                            COUNT(*) FILTER (WHERE quarter=0) q0,
                            COUNT(*) FILTER (WHERE quarter=4) q4
                     FROM canonical_financial_data WHERE is_annual=1 GROUP BY 1,2,3,4)
                   SELECT COUNT(*) FROM g WHERE q0>0 AND q4>0"""
            ).fetchone()[0],
            "orphan_source_rows": conn.execute(
                """SELECT COUNT(*) FROM canonical_financial_data c
                   LEFT JOIN financial_data f ON f.id=c.source_row_id
                   WHERE c.source_row_id IS NOT NULL AND f.id IS NULL"""
            ).fetchone()[0],
            "annual_q4_rows": conn.execute(
                "SELECT COUNT(*) FROM canonical_financial_data WHERE is_annual=1 AND quarter=4"
            ).fetchone()[0],
            "samsung_ofs_annual": rows(
                conn,
                """SELECT id,year,quarter,is_annual,report_type,operating_profit,net_income,source_row_id,decision_reason
                   FROM canonical_financial_data
                   WHERE stock_code='009150' AND is_annual=1 AND report_type='OFS'
                   ORDER BY year, quarter""",
            ),
        }
        result["applied"] = True
    else:
        result["applied"] = False

    out = ROOT / "research_outputs" / "financial_rereview_20261002" / f"{run_id}_{'apply' if args.apply else 'dry'}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({k: result[k] for k in (
        "run_id", "mode", "affected_canonical_rows", "upsert_keys",
        "delete_rows", "delete_by_action", "no_source_rows",
        "refused_count", "refused_by_class", "kept_review_count", "kept_review_by_reason", "kept_review_resolved_dual", "projected_completion", "applied"
    )}, ensure_ascii=False, indent=2))
    if args.apply:
        print("post", json.dumps(result["post"], ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
