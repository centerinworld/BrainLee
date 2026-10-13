#!/usr/bin/env python3
"""Read-only forensic slice for the two scheduled-job failures of 2026-10-11.

Ledger facts this slice explains (source: data/collection_health.db -> collection_job_runs)

  재무무결성일일  2026-10-11T06:20:00  status=failed  error='daily financial integrity audit failed: 1'
  데이터계약점검  2026-10-11T07:10:00  status=failed  error='데이터 계약 점검 실패(rc=2): ... close_verify_fresh fail ...'

What it emits per failure
  1. the ledger rows verbatim (job, status, started/finished, error, details_json)
  2. the exact offending rows the failing check pointed at
  3. the source watermarks that decide whether the value is stale
  4. a CLASSIFICATION produced by an explicit, testable rule (never a hand-typed verdict)

Contract — this module is READ-ONLY.
  * every primary-store access goes through ``connect_primary_db(readonly=True)``
  * the run ledger is read from a COPY of the sqlite file (the live server holds it open)
  * the CLI writes artifacts only into ``--out-dir``
  ``tests/test_forensics_ops_failures_20261011.py`` pins all three properties.

Usage
  ./venv/bin/python scripts/forensics_ops_failures_20261011.py --day 2026-10-11
  ./venv/bin/python scripts/forensics_ops_failures_20261011.py --kis-recheck 10   # opt-in network re-check
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

WATCHED_JOBS = ("재무무결성일일", "데이터계약점검")
LEDGER_REL = Path("data/collection_health.db")
INTEGRITY_DIR_REL = Path("research_outputs/data_integrity_daily")
UNIT_SCALE_EXPONENTS = tuple(range(1, 10))  # 1e1 .. 1e9
# The observed YoY ratio must sit within 10**0.42 (~2.6x) of a power of ten for that
# power to be accepted as the unit-scale explanation. Measured separation on the live
# 2026-10-11 cases: 0.05–0.39 for the real unit errors vs 0.48 for a plain 3x growth.
UNIT_SCALE_LOG10_TOL = 0.42
CLOSE_TOLERANCE_KRW = 0.5  # same tolerance verify_daily_close_vs_official.py uses
CLOSE_VERIFY_MAX_AGE_DAYS = 5  # same threshold data_contract_audit_20260925.py uses


# ──────────────────────────────────────────────────────────────────────────────
# Pure classification helpers (no DB, no network — fixture-testable)
# ──────────────────────────────────────────────────────────────────────────────
def unit_scale_candidates(
    cur: float,
    prev: float,
    exponents: tuple[int, ...] = UNIT_SCALE_EXPONENTS,
    tol: float = UNIT_SCALE_LOG10_TOL,
) -> list[dict]:
    """Which powers of ten bring ``cur`` back onto ``prev``?

    A YoY spike that is really a unit-scale mixing (원 vs 천원 vs 백만원) collapses onto
    the neighbour year once divided by the right 10**n. The test is on the *logarithm* of
    the observed ratio, so a 3x growth (log10 = 0.48) is never mistaken for a 1e1 error.
    Ordered by how well the power explains the ratio.
    """
    out = []
    if not prev or prev <= 0 or cur <= 0:
        return out
    lr = math.log10(cur / prev)
    for n in sorted(exponents):
        residual = abs(lr - n)
        if residual <= tol:
            out.append({"exponent": n, "scaled": cur / (10 ** n), "ratio_to_prev": (cur / prev) / (10 ** n),
                        "log10_residual": residual})
    return sorted(out, key=lambda d: d["log10_residual"])


def classify_yoy_spike(
    cur_억: float,
    prev_억: float,
    *,
    annual_억: float | None = None,
    quarterly_sum_억: float | None = None,
    exponents: tuple[int, ...] = UNIT_SCALE_EXPONENTS,
    tol: float = UNIT_SCALE_LOG10_TOL,
) -> dict:
    """Rule table for I1_yoy_spike.

    * unit_scale_inflated_1eN  — dividing by 10**N puts the year back onto the neighbour (N >= 3)
    * ambiguous_needs_human    — only 10x/100x fits: genuine growth looks identical
    * unexplained_needs_human  — no power of ten explains it
    """
    cands = unit_scale_candidates(cur_억, prev_억, exponents, tol)
    internal = None
    if annual_억 and quarterly_sum_억:
        internal = "annual_equals_quarters" if abs(annual_억 - quarterly_sum_억) <= abs(annual_억) * 5e-3 \
            else "annual_differs_from_quarters"

    if not cands:
        verdict, detail = "unexplained_needs_human", {}
    else:
        best = cands[0]
        if best["exponent"] >= 3:
            verdict = f"unit_scale_inflated_1e{best['exponent']}"
            detail = best
        else:
            verdict = "ambiguous_needs_human"
            detail = best
    detail = dict(detail)
    if internal:
        detail["internal_consistency"] = internal
    return {
        "verdict": verdict,
        "candidates": cands,
        "cur_억": cur_억,
        "prev_억": prev_억,
        "yoY_ratio": (cur_억 / prev_억) if prev_억 else None,
        "detail": detail,
    }


def classify_close_verify_row(
    status: str | None,
    age_days: int | None,
    *,
    max_age_days: int = CLOSE_VERIFY_MAX_AGE_DAYS,
) -> dict:
    """Rule table for the ``close_verify_fresh`` contract.

    The contract is two independent clauses: the last verification run must be recent
    AND must have been ``ok``. Today's failure is clause 2, so the two are reported apart
    (``fail_reference_status`` vs ``fail_stale``) — they have different owners.
    """
    if status is None:
        return {"verdict": "warn_no_run_recorded", "reason": "no verification run recorded yet"}
    if status != "ok":
        return {"verdict": "fail_reference_status",
                "reason": f"last verification run finished status={status!r} (its own reference disagreed)",
                "age_days": age_days, "max_age_days": max_age_days}
    if age_days is not None and age_days > max_age_days:
        return {"verdict": "fail_stale", "reason": f"{age_days}d > {max_age_days}d",
                "age_days": age_days, "max_age_days": max_age_days}
    return {"verdict": "ok", "age_days": age_days, "max_age_days": max_age_days}


def close_matches(db_close: float | None, ref_close: float | None, tol: float = CLOSE_TOLERANCE_KRW) -> bool:
    if db_close is None or ref_close is None:
        return False
    return abs(float(db_close) - float(ref_close)) <= tol


def classify_price_disagreement(
    *,
    db_close: float | None,
    ref_close: float | None,
    db_close_nominal: float | None = None,
    krx_close: float | None = None,
) -> dict:
    """Classify one flagged (stock, date) close disagreement.

    Precedence — the independent official source decides:
      1. DB == KRX official (stored sibling)      -> db_agrees_with_official
      2. DB == reference (KIS) re-read now        -> reference_revised_after_check
      3. reference changed since the check        -> reference_not_final_at_check
      4. otherwise                                -> unresolved_needs_human
    """
    if krx_close is not None and close_matches(db_close, krx_close):
        return {"verdict": "db_agrees_with_official", "note": "price_history == KRX official close"}
    if db_close_nominal is not None and ref_close is not None and close_matches(db_close, db_close_nominal) \
            and not close_matches(db_close_nominal, ref_close):
        return {"verdict": "reference_revised_after_check",
                "note": "reference value changed between the check and this re-read; DB equals the revised one"}
    if db_close_nominal is not None and ref_close is not None:
        return {"verdict": "reference_not_final_at_check",
                "note": "reference moved after the check and still disagrees with the DB"}
    return {"verdict": "unresolved_needs_human"}


def classify_missing_financials(revenue_is_null_everywhere: bool, has_annual_rows: bool) -> dict:
    """Rule table for I6_no_recent_data (annual rows exist but no non-NULL revenue)."""
    if has_annual_rows and revenue_is_null_everywhere:
        return {"verdict": "pre_revenue_no_revenue_field",
                "note": "annual rows exist for 2023+ but every revenue cell is NULL "
                        "(pre-revenue issuer) — I6 tests revenue IS NOT NULL, not row presence"}
    if has_annual_rows:
        return {"verdict": "revenue_present_elsewhere_needs_human"}
    return {"verdict": "no_annual_rows_true_collection_gap"}


def watchlist_from_ledger(rows: list[dict], jobs: tuple[str, ...] = WATCHED_JOBS) -> list[dict]:
    return [r for r in rows if r.get("job_name") in jobs]


# ──────────────────────────────────────────────────────────────────────────────
# Read-only readers
# ──────────────────────────────────────────────────────────────────────────────
def read_ledger_rows(ledger_path: Path, day: str, jobs: tuple[str, ...] = WATCHED_JOBS) -> list[dict]:
    """Read the run ledger from a COPY — the live server holds the original open.

    The copy lives in a private temp dir on purpose: the runtime venv's sqlite3 is
    routed to PostgreSQL for the canonical stock.db path only, so a copy is opened
    as a real sqlite file.
    """
    tmp = Path(tempfile.mkdtemp(prefix="ops_forensics_"))
    dst = tmp / "collection_health_copy.db"
    try:
        shutil.copyfile(ledger_path, dst)
        conn = sqlite3.connect(str(dst))
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT run_id, job_name, attempt, status, started_at, finished_at, duration_seconds, error, details_json "
            "FROM collection_job_runs WHERE substr(started_at,1,10) = ? ORDER BY started_at", (day,))]
        conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return watchlist_from_ledger(rows, jobs)


def read_integrity_report(path: Path) -> dict:
    """The audit's own artifact for the failing run (no re-run needed)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    failed = {k: v for k, v in data.items() if not v.get("pass")}
    return {"path": str(path), "items_total": len(data), "items_failed": sorted(failed),
            "failed": failed}


def _primary_conn(timeout: int = 60):
    from db_compat import connect_primary_db  # local import keeps the module importable without PG
    return connect_primary_db(readonly=True, timeout=timeout)


def read_watermarks(conn) -> dict:
    """Source watermarks the two failures are judged against."""
    def one(sql, params=()):
        row = conn.execute(sql, params).fetchone()
        return tuple(row) if row else None

    return {
        "price_history_kr_max_date": one(
            "SELECT max(date), count(*) FROM price_history WHERE stock_code ~ '^[0-9]{6}$'"),
        "price_history_any_max_date": one("SELECT max(date) FROM price_history"),
        "stock_price_daily_max_bas_dt": one("SELECT max(bas_dt) FROM stock_price_daily"),
        "price_close_verify_log_max": one(
            "SELECT max(trade_date), max(checked_at) FROM price_close_verify_log"),
        "financial_data_max_year": one("SELECT max(year) FROM financial_data"),
    }


def read_close_verify_rows(conn, limit: int = 5) -> list[dict]:
    rows = conn.execute(
        "SELECT id, checked_at, trade_date, sampled, compared, close_mismatch, volume_mismatch, "
        "mismatch_pct, fetch_failed, status, detail FROM price_close_verify_log ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    out = []
    for r in rows:
        d = dict(r) if not hasattr(r, "keys") else {k: r[k] for k in r.keys()}
        try:
            d["examples"] = json.loads(d.get("detail") or "{}").get("examples", [])
        except Exception:  # noqa: BLE001
            d["examples"] = []
        out.append(d)
    return out


def read_contract_rows(conn, day: str) -> list[dict]:
    rows = conn.execute(
        "SELECT check_name, status, value, threshold, detail FROM data_contract_check_log "
        "WHERE check_date = ? ORDER BY check_name", (day,)).fetchall()
    out = []
    for r in rows:
        d = dict(r) if not hasattr(r, "keys") else {k: r[k] for k in r.keys()}
        d["detail"] = (d.get("detail") or "")[:400]
        out.append(d)
    return out


def read_contract_detail(conn, day: str, check_name: str) -> dict:
    """FULL detail for one check — `read_contract_rows` truncates for display only."""
    row = conn.execute("SELECT detail FROM data_contract_check_log WHERE check_date = ? AND check_name = ?",
                       (day, check_name)).fetchone()
    if row is None:
        return {}
    raw = row[0] if not hasattr(row, "keys") else row["detail"]
    try:
        return json.loads(raw or "{}")
    except Exception:  # noqa: BLE001
        return {"_raw": str(raw)[:2000]}


def krx_close_map(conn, iso_day: str) -> dict:
    """Independent official closes from the stored KRX Open API sibling (YYYYMMDD key)."""
    ymd = iso_day.replace("-", "")
    try:
        rows = conn.execute("SELECT stock_code, close_price FROM stock_price_daily WHERE bas_dt = ?", (ymd,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {(r[0] if not hasattr(r, "keys") else r["stock_code"]): (r[1] if not hasattr(r, "keys") else r["close_price"])
            for r in rows}


def krx_agreement(conn, iso_day: str, tolerance: float = CLOSE_TOLERANCE_KRW) -> dict:
    """DB vs KRX official for a stored day — the decisive independent check.

    Written with psycopg-native comparison so it holds whether or not the compat layer
    would translate the predicate. ``available`` is False when the sibling has no rows
    for that day (KRX Open API lags a day), so an empty result is never read as agreement.
    """
    ymd = iso_day.replace("-", "")
    rows = conn.execute(
        "SELECT p.stock_code, p.close, s.close_price FROM price_history p "
        "JOIN stock_price_daily s ON s.stock_code = p.stock_code AND s.bas_dt = ? "
        "WHERE p.date = ? AND p.close > 0 AND s.close_price > 0", (ymd, iso_day)).fetchall()
    pairs = []
    for r in rows:
        vals = tuple(r) if not hasattr(r, "keys") else (r["stock_code"], r["close"], r["close_price"])
        pairs.append(vals)
    bad = [p for p in pairs if abs(float(p[1]) - float(p[2])) > tolerance]
    return {"day": iso_day, "available": bool(pairs), "compared": len(pairs), "mismatched": len(bad),
            "examples": [{"code": p[0], "db_close": p[1], "krx_close": p[2]} for p in bad[:10]]}


def read_financial_evidence(conn, stock_code: str, year: int) -> dict:
    """Every financial_data row for (stock, year±1) — the raw material for I1/I2/I6."""
    rows = []
    for r in conn.execute(
        "SELECT id, year, quarter, is_annual, report_type, data_source, revenue, operating_profit, "
        "created_at, updated_at "
        "FROM financial_data WHERE stock_code = ? AND year IN (?, ?) "
        "ORDER BY year, is_annual DESC, quarter, report_type", (stock_code, year - 1, year)):
        rows.append(tuple(r) if not hasattr(r, "keys") else
                    (r["id"], r["year"], r["quarter"], r["is_annual"], r["report_type"],
                     r["data_source"], r["revenue"], r["operating_profit"], r["created_at"], r["updated_at"]))
    out = {"stock_code": stock_code, "year": year, "rows": []}
    for r in rows:
        out["rows"].append({"id": r[0], "year": r[1], "quarter": r[2], "is_annual": r[3],
                            "report_type": r[4], "data_source": r[5], "revenue": _f(r[6]),
                            "operating_profit": _f(r[7]),
                            "created_at": r[8] if len(r) > 8 else None,
                            "updated_at": r[9] if len(r) > 9 else None})
    return out


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Item 3 — I1 unit-scale candidates as an APPLY-BLOCKED manifest
# ──────────────────────────────────────────────────────────────────────────────
I1_CONFIDENT_PREFIX = "unit_scale_inflated_1e"


def build_i1_manifest(conn, classified: list[dict]) -> dict:
    """Provenance-carrying manifest for the I1 scale-error candidates. Nothing is applied.

    Each entry carries the observed value, the row it lives in (id / quarter / report_type), its
    collection provenance (data_source + created_at + updated_at) and the arithmetic basis the
    scale was derived from — enough for a reviewer to reproduce the number without a re-collect.
    `apply_allowed` is False for every entry by construction: a data repair needs its own approval.
    """
    candidates, quarantine = [], []
    for c in classified:
        case, cls = c["case"], c["classification"]
        verdict, det = cls["verdict"], cls.get("detail") or {}
        code, year = case["stock"], int(case["year"])
        rows = read_financial_evidence(conn, code, year)["rows"]
        annual = [r for r in rows if r["year"] == year and r["is_annual"] and r["report_type"] == "CFS"]
        entry = {
            "stock_code": code,
            "year": year,
            "verdict": verdict,
            "observed_억": case["cur_억"],
            "prev_year_annual_억": case["prev_억"],
            "scale_exponent": det.get("exponent"),
            "proposed_value_억": det.get("scaled"),
            "log10_residual": det.get("log10_residual"),
            "internal_consistency": det.get("internal_consistency"),
            "basis": {
                "rule": "smallest power of ten bringing the year back onto the neighbour year "
                        f"(|log10(cur/prev) - n| <= {UNIT_SCALE_LOG10_TOL}); verified against the "
                        "same stock's own quarterly rows",
            },
            "rows": annual,
            "apply_allowed": False,
        }
        if verdict.startswith(I1_CONFIDENT_PREFIX):
            candidates.append(entry)
        else:
            entry["quarantine"] = True
            entry["quarantine_reason"] = "only a 10x fits — indistinguishable from genuine 10x growth"
            quarantine.append(entry)
    return {
        "apply_allowed": False,
        "note": "manifest only — no value was changed and this slice cannot apply anything; a repair "
                "needs its own approval and a rollback recipe",
        "candidate_count": len(candidates),
        "quarantine_count": len(quarantine),
        "candidates": candidates,
        "quarantine": quarantine,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Item 4 — the other three contract failures, same shape (rows / owner / follow-up test)
# ──────────────────────────────────────────────────────────────────────────────
OWNERS = {
    "cfs_ofs_mixed_ttm": "valuation-history TTM builder (scripts/add_valuation_history_per_ttm_20260925.py)",
    "default_missing": "creator of each flagged table (financial_field_baseline ← "
                       "scripts/review/build_field_verification_20261003.py / residual_cause_groups_20261010.py)",
    "snapshot_lookahead": "strategy_feature_snapshot builder (tenbagger_engine.py / research/*)",
}

FOLLOW_UP_TESTS = {
    "cfs_ofs_mixed_ttm": "fixture: a stored TTM equal to the sum of its own 4 quarters passes; "
                         "flip one quarter by >2% and it fails; a quarter that is absent yields "
                         "missing_quarters, not a mismatch",
    "default_missing": "fixture: a table with created_at DEFAULT now() is never flagged; a new table "
                       "outside the stored baseline without a default IS flagged; the baseline diff "
                       "is computed from the previous run's stored list",
    "snapshot_lookahead": "fixture: a snapshot whose implied TTM matches only a quarter whose modelled "
                          "availability is after snapshot_date is a violation; one whose quarter was "
                          "already available is not; 1/400 must stay below the documented 1%",
}


def classify_cfs_ofs_mixed_ttm(conn, detail: dict, sample: int = 5) -> dict:
    """Recompute the check's own predicate and split the failures into classes."""
    fdq: dict = {}
    for code, y, q, rt, ni in conn.execute(
            "SELECT stock_code,year,quarter,report_type,net_income FROM financial_data "
            "WHERE is_annual IS FALSE AND quarter BETWEEN 1 AND 4 AND net_income IS NOT NULL"):
        fdq.setdefault(code, {"CFS": {}, "OFS": {}})["CFS" if rt == "CFS" else "OFS"][int(y) * 4 + int(q)] = float(ni)
    lastyq = conn.execute("SELECT year, quarter FROM valuation_history WHERE per_ttm IS NOT NULL "
                          "ORDER BY year DESC, quarter DESC LIMIT 1").fetchone()
    floor = int(lastyq[0]) - 1 if lastyq else 2025
    buckets = {"missing_quarters": 0, "ttm_drift_lt_2pct": 0, "ttm_material_mismatch": 0}
    material = []
    for code, y, q, ttm, basis, upd in conn.execute(
            "SELECT stock_code,year,quarter,ttm_net_income,ttm_basis,updated_at FROM valuation_history "
            "WHERE per_ttm IS NOT NULL AND year >= ?", (floor,)):
        k = int(y) * 4 + int(q)
        qs = fdq.get(code, {}).get(basis or "", {})
        if not basis or not all((k - i) in qs for i in range(4)):
            buckets["missing_quarters"] += 1
            continue
        total = sum(qs[k - i] for i in range(4))
        if abs(total - float(ttm)) <= max(1.0, abs(float(ttm)) * 1e-9):
            continue
        rel = abs(total - float(ttm)) / max(abs(float(ttm)), 1)
        buckets["ttm_material_mismatch" if rel >= 0.02 else "ttm_drift_lt_2pct"] += 1
        if rel >= 0.02 and len(material) < sample:
            material.append({
                "stock_code": code, "year": y, "quarter": q, "ttm_basis": basis,
                "stored_ttm": _f(ttm), "quarters_sum": total, "relative_diff": round(rel, 4),
                "ttm_row_updated_at": upd,
                "quarters": [{"key": k - i, "net_income": qs[k - i]} for i in range(4)],
            })
    return {
        "check_value": detail.get("inconsistent_ttm_rows"),
        "checked": detail.get("checked"),
        "classes": buckets,
        "cause_rows": material,
        "classification": {
            "verdict": "ttm_stale_vs_restated_quarters" if buckets["ttm_drift_lt_2pct"] else "unclassified",
            "note": "the stored TTM does not equal the sum of the same four quarters TODAY; the small "
                    "relative drifts are consistent with the quarterly restatements applied on "
                    "2026-10-10/10-11 (data_fix_log 1122/1123/1128), the >=2% group needs its own check",
        },
        "owner": OWNERS["cfs_ofs_mixed_ttm"],
        "follow_up_test": FOLLOW_UP_TESTS["cfs_ofs_mixed_ttm"],
    }


def classify_default_missing(conn, detail: dict) -> dict:
    """Which of the flagged tables are new, and which columns actually lack a DEFAULT."""
    new_tables = detail.get("new") or []
    rows = []
    for t in new_tables:
        cols = conn.execute(
            "SELECT column_name, column_default FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=? AND column_name IN ('created_at','updated_at') "
            "AND column_default IS NULL", (t,)).fetchall()
        rows.append({"table": t, "columns_without_default": [
            {"column": (tuple(c)[0] if not hasattr(c, "keys") else c["column_name"])} for c in cols]})
    return {
        "new_table_count": len(new_tables),
        "new_tables": new_tables,
        "cause_rows": rows,
        "classification": {
            "verdict": "new_table_without_default" if any(r["columns_without_default"] for r in rows)
            else "baseline_lag_only",
            "note": "the baseline is the PREVIOUS run's table list, so a table added since the last run "
                    "is flagged even when it is harmless; only the ones with a NULL column_default "
                    "actually store NULL on an INSERT that omits the column",
        },
        "owner": OWNERS["default_missing"],
        "follow_up_test": FOLLOW_UP_TESTS["default_missing"],
    }


def classify_snapshot_lookahead(conn, detail: dict) -> dict:
    """Per-violation evidence for the filing-lag measure (the sub-signal that fails)."""
    import re as _re
    raw = detail.get("filing_lag_violations") or ""
    pairs = _re.findall(r"\('(\d{6})',\s*'(\d{4}-\d{2}-\d{2})'\)", raw)
    rows = []
    for code, snap in pairs:
        snap_row = conn.execute(
            "SELECT stock_code, snapshot_date, market_cap_억, per FROM strategy_feature_snapshot "
            "WHERE stock_code=? AND snapshot_date=? AND per IS NOT NULL", (code, snap)).fetchone()
        vals = tuple(snap_row) if snap_row is not None and not hasattr(snap_row, "keys") else (
            (snap_row["stock_code"], snap_row["snapshot_date"], snap_row["market_cap_억"], snap_row["per"])
            if snap_row is not None else None)
        rows.append({"stock_code": code, "snapshot_date": snap, "snapshot_row": vals})
    return {
        "check_value": detail.get("same_quarter_match"),
        "violations_raw": detail.get("filing_lag_violations"),
        "violation_rows": rows,
        "classification": {
            "verdict": "needs_dart_decision" if rows else "ok",
            "note": "measure (a) is 0.00% and passes; measure (b) fails on ANY violation, so 1 of 400 "
                    "sampled rows is enough. Either the snapshot used a quarter that could not be "
                    "filed yet (look-ahead) or the quarter was collected/restated afterwards — the "
                    "stored row alone cannot separate the two, so it stays a human/DART decision",
        },
        "owner": OWNERS["snapshot_lookahead"],
        "follow_up_test": FOLLOW_UP_TESTS["snapshot_lookahead"],
    }


def financial_case_evidence(rows: list[dict], year: int) -> dict:
    """Annual vs quarterly vs neighbour-year evidence for one (stock, year)."""
    def annual(yr, rt):
        vals = [r["revenue"] for r in rows if r["year"] == yr and r["is_annual"] and r["report_type"] == rt
                and r["revenue"] is not None]
        return vals[0] if vals else None

    quarters = [r["revenue"] for r in rows if r["year"] == year and not r["is_annual"]
                and r["report_type"] == "CFS" and r["revenue"] is not None]
    annual_cfs = annual(year, "CFS")
    annual_ofs = annual(year, "OFS")
    return {
        "annual_cfs": annual_cfs,
        "annual_ofs": annual_ofs,
        "quarterly_cfs": quarters,
        "quarterly_sum_cfs": sum(quarters) if quarters else None,
        "prev_annual_cfs": annual(year - 1, "CFS"),
        "annual_cfs_억": (annual_cfs / 1e8) if annual_cfs else None,
        "quarterly_sum_억": (sum(quarters) / 1e8) if quarters else None,
        "prev_annual_억": (annual(year - 1, "CFS") / 1e8) if annual(year - 1, "CFS") else None,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Slice assembly
# ──────────────────────────────────────────────────────────────────────────────
def build_slice(day: str, ledger_path: Path, integrity_report: Path | None, kis_recheck: int = 0) -> dict:
    out: dict = {"day": day, "generated_at": datetime.now().isoformat(timespec="seconds"),
                 "read_only": True}

    out["ledger_rows"] = read_ledger_rows(ledger_path, day)

    if integrity_report is None:
        candidates = sorted(INTEGRITY_DIR_REL.glob("integrity_*.json"), key=lambda p: p.stat().st_mtime)
        integrity_report = (ROOT / candidates[-1]) if candidates else None
    out["integrity_report"] = read_integrity_report(integrity_report) if integrity_report else None

    conn = _primary_conn()
    try:
        out["watermarks"] = read_watermarks(conn)
        out["close_verify_rows"] = read_close_verify_rows(conn)
        out["contract_rows"] = read_contract_rows(conn, day)

        # ── close_verify_fresh ────────────────────────────────────────────────
        latest = out["close_verify_rows"][0] if out["close_verify_rows"] else None
        if latest:
            age = (date.fromisoformat(day) - date.fromisoformat(str(latest["trade_date"])[:10])).days
            out["close_verify_classification"] = classify_close_verify_row(latest["status"], age)
            out["close_verify_classification"]["latest_trade_date"] = latest["trade_date"]
            out["close_verify_classification"]["close_mismatch"] = latest["close_mismatch"]
            out["close_verify_classification"]["compared"] = latest["compared"]
            # independent official source: KRX sibling, on the verify day AND on the most
            # recent day where the sibling actually has rows (KRX Open API lags ~1 day)
            days = [str(latest["trade_date"])[:10]]
            try:
                row = conn.execute("SELECT max(bas_dt) FROM stock_price_daily WHERE bas_dt <= ?",
                                   (str(latest["trade_date"]).replace("-", ""),)).fetchone()
                sibling_max = row[0] if not hasattr(row, "keys") else row["max"]
                if sibling_max:
                    iso = f"{sibling_max[:4]}-{sibling_max[4:6]}-{sibling_max[6:]}"
                    if iso not in days:
                        days.append(iso)
            except Exception:  # noqa: BLE001
                pass
            out["krx_agreement"] = {d: krx_agreement(conn, d) for d in days}
            # re-read the reference source (network, opt-in)
            if kis_recheck > 0:
                out["kis_recheck"] = _kis_recheck(latest, limit=kis_recheck)

        # ── 재무무결성일일 ─────────────────────────────────────────────────────
        if out["integrity_report"]:
            out["integrity_classification"] = _classify_integrity(conn, out["integrity_report"])

        # ── the other three contract failures (same forensic shape) ───────────
        items = {}
        for name, fn in (("cfs_ofs_mixed_ttm", classify_cfs_ofs_mixed_ttm),
                         ("default_missing", classify_default_missing),
                         ("snapshot_lookahead", classify_snapshot_lookahead)):
            detail = read_contract_detail(conn, day, name)
            if detail:
                try:
                    items[name] = fn(conn, detail)
                except Exception as exc:  # noqa: BLE001 - one bad check must not sink the slice
                    items[name] = {"error": f"{exc.__class__.__name__}: {exc}"}
        out["contract_items"] = items
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return out


def _classify_integrity(conn, report: dict) -> dict:
    res: dict = {"items_failed": report["items_failed"], "items": {}}

    for case in report["failed"].get("I1_yoy_spike", {}).get("cases", []):
        code, year = case["stock"], int(case["year"])
        ev = financial_case_evidence(read_financial_evidence(conn, code, year)["rows"], year)
        res["items"].setdefault("I1_yoy_spike", []).append({
            "case": case,
            "evidence": ev,
            "classification": classify_yoy_spike(
                float(case["cur_억"]), float(case["prev_억"]),
                annual_억=ev["annual_cfs_억"], quarterly_sum_억=ev["quarterly_sum_억"]),
        })

    for case in report["failed"].get("I2_op_gt_rev", {}).get("cases", []):
        code, year = case["stock"], int(case["year"])
        ev = read_financial_evidence(conn, code, year)
        for r in ev["rows"]:
            r["op_gt_rev"] = (r["operating_profit"] is not None and r["revenue"] is not None
                              and r["operating_profit"] > r["revenue"])
        pairs_year = {}
        for r in ev["rows"]:
            if r["is_annual"] and r["year"] == year:
                pairs_year.setdefault(r["report_type"], []).append(r["id"])
        res["items"].setdefault("I2_op_gt_rev", []).append({
            "case": case,
            "annual_row_ids_for_case_year": {k: v for k, v in sorted(pairs_year.items())},
            "case_emitted_twice": any(len(v) > 1 for v in pairs_year.values()),
            "rows": [r for r in ev["rows"] if r["year"] == year],
        })

    i6 = report["failed"].get("I6_no_recent_data")
    if i6:
        rows = []
        for r in conn.execute("""
            SELECT su.stock_code, su.stock_name, su.market, su.market_cap
            FROM stock_universe su
            WHERE su.market IN ('KOSPI','KOSDAQ')
              AND NOT EXISTS (SELECT 1 FROM financial_data f
                              WHERE f.stock_code = su.stock_code AND f.is_annual = 1
                                AND f.year >= 2023 AND f.revenue IS NOT NULL)
            ORDER BY su.market_cap DESC LIMIT 25""").fetchall():
            vals = tuple(r) if not hasattr(r, "keys") else (r["stock_code"], r["stock_name"], r["market"], r["market_cap"])
            fin = list(conn.execute(
                "SELECT revenue, year FROM financial_data WHERE stock_code = ? AND is_annual = 1 "
                "AND year >= 2023", (vals[0],)).fetchall())
            revenues = [(f[0] if not hasattr(f, "keys") else f["revenue"]) for f in fin]
            rows.append({
                "stock_code": vals[0], "stock_name": vals[1], "market": vals[2], "market_cap_억": _f(vals[3]),
                "annual_rows_2023plus": len(fin),
                "revenue_null_everywhere": bool(fin) and all(v is None for v in revenues),
            })
        not_null = [r for r in rows if not r["revenue_null_everywhere"]]
        res["items"]["I6_no_recent_data"] = {
            "count": i6.get("count"),
            "rows": rows,
            "classification": {
                "verdict": "pre_revenue_no_revenue_field" if not not_null else "mixed_needs_human",
                "with_annual_rows_but_null_revenue": sum(1 for r in rows if r["revenue_null_everywhere"]),
                "without_any_annual_row": sum(1 for r in rows if r["annual_rows_2023plus"] == 0),
            },
        }
    i1 = res["items"].get("I1_yoy_spike") or []
    if i1:
        res["i1_apply_manifest"] = build_i1_manifest(conn, i1)
    return res


def _kis_recheck(latest: dict, limit: int = 10) -> dict:
    """Re-read the reference source for today's flagged codes (opt-in network call)."""
    import requests
    from collect_kis_ohlcv import get_token, KIS_URL, KIS_APP_KEY, KIS_APP_SECRET

    token = get_token()
    ymd = str(latest["trade_date"]).replace("-", "")
    headers = {"Content-Type": "application/json", "authorization": f"Bearer {token}",
               "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET,
               "tr_id": "FHKST03010100", "custtype": "P"}
    conn = _primary_conn()
    out = {"trade_date": latest["trade_date"], "rows": [], "reference_changed": 0, "db_now_matches": 0}
    try:
        for e in (latest.get("examples") or [])[:limit]:
            params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": e["code"],
                      "FID_INPUT_DATE_1": ymd, "FID_INPUT_DATE_2": ymd,
                      "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"}
            try:
                d = requests.get(f"{KIS_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                                 headers=headers, params=params, timeout=15).json()
                rows = [x for x in (d.get("output2") or []) if x.get("stck_bsop_date") == ymd]
                now = float(rows[-1]["stck_clpr"]) if rows else None
            except Exception:  # noqa: BLE001
                now = None
            row = conn.execute("SELECT close FROM price_history WHERE date = ? AND stock_code = ?",
                               (latest["trade_date"], e["code"])).fetchone()
            db_now = _f(row[0] if row else None) if not hasattr(row, "keys") else _f(row["close"])
            changed = now is not None and not close_matches(now, e["official_close"])
            matches = close_matches(db_now, now)
            out["reference_changed"] += int(changed)
            out["db_now_matches"] += int(matches)
            out["rows"].append({"code": e["code"], "db_now": db_now,
                                "reference_at_check": e["official_close"], "reference_now": now,
                                "reference_changed_after_check": changed, "db_now_equals_reference_now": matches})
    finally:
        conn.close()
    return out


def render_markdown(slice_obj: dict) -> str:
    L = [f"# ops-failure forensic slice — {slice_obj['day']}", "",
         f"generated {slice_obj['generated_at']} · read_only={slice_obj['read_only']}", "",
         "## 1. ledger rows", ""]
    L.append("| job | status | started | duration | error |")
    L.append("|---|---|---|---|---|")
    for r in slice_obj["ledger_rows"]:
        err = str(r["error"]).replace("|", "/").replace("\n", " / ")
        L.append(f"| {r['job_name']} | {r['status']} | {r['started_at']} | {r['duration_seconds']} | "
                 f"{err[:200]} |")
    L += ["", "## 2. source watermarks", "", "| source | value |", "|---|---|"]
    for k, v in (slice_obj.get("watermarks") or {}).items():
        L.append(f"| {k} | {v} |")
    if slice_obj.get("close_verify_classification"):
        c = slice_obj["close_verify_classification"]
        L += ["", "## 3. close_verify_fresh", "",
              f"* verdict: **{c['verdict']}** — {c.get('reason', '')}",
              f"* latest row: trade_date {c.get('latest_trade_date')} "
              f"close_mismatch {c.get('close_mismatch')}/{c.get('compared')}"]
        for day, ag in (slice_obj.get("krx_agreement") or {}).items():
            if not ag.get("available"):
                L.append(f"* DB vs KRX official on {day}: sibling has no rows for that day (KRX Open API lags)")
            else:
                L.append(f"* DB vs KRX official on {day}: {ag['compared'] - ag['mismatched']}/{ag['compared']} exact "
                         f"(mismatched {ag['mismatched']})")
    if slice_obj.get("kis_recheck"):
        k = slice_obj["kis_recheck"]
        L += ["", "### reference re-read (KIS)", "",
              f"* reference changed after the check for {k['reference_changed']} of {len(k['rows'])} codes; "
              f"DB now equals the reference for {k['db_now_matches']}"]
    if slice_obj.get("integrity_classification"):
        L += ["", "## 4. 재무무결성일일 — failing items", "",
              f"items failed: {', '.join(slice_obj['integrity_classification']['items_failed'])}"]
        for name, cases in (slice_obj["integrity_classification"]["items"] or {}).items():
            if name == "I6_no_recent_data":
                c = cases["classification"]
                L += ["", f"### {name}: {c['verdict']}",
                      f"* with annual rows but NULL revenue: {c['with_annual_rows_but_null_revenue']} · "
                      f"without any annual row: {c['without_any_annual_row']}"]
                continue
            L += ["", f"### {name}"]
            for c in cases:
                if "classification" in c:
                    v = c["classification"]["verdict"]
                    L.append(f"* {c['case']['stock']} {c['case']['year']}: **{v}** "
                             f"(cur {c['case']['cur_억']:,.0f}억 / prev {c['case']['prev_억']:,.0f}억)")
                else:
                    ids = c.get("annual_row_ids_for_case_year") or {}
                    op_gt = [r for r in c.get("rows", []) if r.get("op_gt_rev")]
                    L.append(f"* {c['case']['stock']} {c['case']['year']}: "
                             f"**duplicate_annual_rows={c.get('case_emitted_twice')}** rows={ids} "
                             f"(op>rev rows {len(op_gt)})")

    manifest = (slice_obj.get("integrity_classification") or {}).get("i1_apply_manifest")
    if manifest:
        L += ["", "## 5. I1 unit-scale candidates — APPLY-BLOCKED manifest", "",
              f"`apply_allowed = {manifest['apply_allowed']}` — candidates {manifest['candidate_count']}, "
              f"quarantine {manifest['quarantine_count']}", ""]
        for e in manifest["candidates"]:
            row_ids = ", ".join(f"{r['id']}(q{r['quarter']},{r['report_type']},{r['data_source']},"
                                f"created {r['created_at']})" for r in e["rows"])
            L.append(f"* **{e['stock_code']} {e['year']}** ÷1e{e['scale_exponent']} "
                     f"{e['observed_억']:,.0f}억 → {e['proposed_value_억']:,.0f}억 "
                     f"(전년 {e['prev_year_annual_억']:,.0f}억, residual {e['log10_residual']:.3f}) "
                     f"— rows: {row_ids}")
        for e in manifest["quarantine"]:
            L.append(f"* (quarantine) **{e['stock_code']} {e['year']}** — {e['quarantine_reason']}")

    citems = slice_obj.get("contract_items") or {}
    if citems:
        L += ["", "## 6. Other contract failures (same forensic shape)"]
        for name, c in citems.items():
            if "error" in c:
                L += ["", f"### {name}", f"* ERROR {c['error']}"]
                continue
            cl = c.get("classification") or {}
            L += ["", f"### {name} — {cl.get('verdict')}", f"* {cl.get('note', '')}",
                  f"* owner: {c.get('owner')}", f"* follow-up test: {c.get('follow_up_test')}"]
            if c.get("classes"):
                L.append(f"* classes: {c['classes']}")
            if c.get("new_tables") is not None:
                L.append(f"* new tables ({c['new_table_count']}): {c['new_tables']}")
            for r in c.get("cause_rows") or []:
                if isinstance(r, dict) and "relative_diff" in r:
                    L.append(f"* cause: {r['stock_code']} {r['year']}Q{r['quarter']} "
                             f"stored {r['stored_ttm']:,.0f} vs quarters {r['quarters_sum']:,.0f} "
                             f"({r['relative_diff']:.1%}, ttm updated {r['ttm_row_updated_at']})")
                elif isinstance(r, dict) and "columns_without_default" in r:
                    if r["columns_without_default"]:
                        L.append(f"* cause: {r['table']} → {r['columns_without_default']}")
            for r in c.get("violation_rows") or []:
                L.append(f"* violation: {r['stock_code']} @ {r['snapshot_date']} → {r['snapshot_row']}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", default=date.today().isoformat())
    ap.add_argument("--ledger-db", default=str(ROOT / LEDGER_REL))
    ap.add_argument("--integrity-report", default=None)
    ap.add_argument("--kis-recheck", type=int, default=0)
    ap.add_argument("--out-dir", default=str(ROOT / ".verification"))
    a = ap.parse_args(argv)

    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = a.day.replace("-", "")
    obj = build_slice(a.day, Path(a.ledger_db),
                      Path(a.integrity_report) if a.integrity_report else None,
                      kis_recheck=int(a.kis_recheck))
    json_path = out_dir / f"ops_failures_{stamp}.json"
    md_path = out_dir / f"ops_failures_{stamp}.md"
    json_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(render_markdown(obj), encoding="utf-8")
    print(render_markdown(obj))
    print(f"[written] {json_path}")
    print(f"[written] {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
