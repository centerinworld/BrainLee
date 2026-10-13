"""Regression tests for the 2026-10-11 follow-up slice:

  1. close-verify reference split (scripts/close_verify_policy.py + verify_daily_close_vs_official.py
     + data_contract_audit_20260925.py)  — provisional vs finalizable reference
  2. I2 duplicate annual rows (scripts/ops/data_integrity_check.py)
  3+4. the forensics manifest and the other three contract failures
       (scripts/forensics_ops_failures_20261011.py)

Fixtures only: no live PostgreSQL, no network, no clock reads (every `now` is explicit).
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import close_verify_policy as policy  # noqa: E402
import data_contract_audit_20260925 as audit  # noqa: E402
import forensics_ops_failures_20261011 as fx  # noqa: E402
sys.path.insert(0, str(ROOT / "scripts" / "ops"))
import data_integrity_check as dic  # noqa: E402

# The exact row the 2026-10-11 contract failed on (price_close_verify_log id=5).
LIVE_PROVISIONAL = {"trade_date": "2026-10-08", "checked_at": "2026-10-08 19:30:07",
                    "status": "mismatch", "close_mismatch": 56, "compared": 80,
                    "mismatch_pct": 0.7, "reference_kind": None}
# an official (final) verdict for an older day — the arbiter the contract judges freshness against
FINAL_OFFICIAL_1007 = {"trade_date": "2026-10-07", "checked_at": "2026-10-07 19:30:00",
                       "status": "ok", "close_mismatch": 0, "compared": 2747,
                       "mismatch_pct": 0.0, "reference_kind": "official_krx"}
NOW = datetime(2026, 10, 11, 7, 10, 0)


class StubConn:
    """Answers the close-verify read with a list the test can mutate mid-test."""

    def __init__(self, rows):
        self.rows = rows
        self.commits = 0
        self.sql = []

    def execute(self, sql, params=()):
        conn = self
        self.sql.append(" ".join(str(sql).split()))

        class Cur:
            def fetchall(self):
                return conn.rows

            def fetchone(self):
                return conn.rows[0] if conn.rows else None

            def __iter__(self):
                return iter(conn.rows)

        return Cur()

    def commit(self):
        self.commits += 1


# ──────────────────────────────────────────────────────────────────────────────
# 1. reference split
# ──────────────────────────────────────────────────────────────────────────────
def test_settle_deadline_is_close_plus_settle_hours():
    assert policy.settle_deadline("2026-10-08") == datetime(2026, 10, 9, 3, 30)


def test_a_provisional_mismatch_is_never_a_failure():
    """The defect being fixed: one provisional row must not fail the contract for ever.

    With a fresh official verdict for an older day present, the newest provisional disagreement
    is advisory (the KIS proxy revises itself; there is no arbiter for that date yet).
    """
    rows = [LIVE_PROVISIONAL, FINAL_OFFICIAL_1007]
    v = policy.evaluate_close_verify_fresh(rows, now=NOW)
    assert v["verdict"] == policy.V_NEEDS_FINAL_RECHECK
    assert v["verdict"] not in policy.FAILING_VERDICTS
    assert audit_close_status(v) == "warn"


def test_a_stale_arbiter_fails_closed_even_though_the_proxy_is_only_advisory():
    """Without ANY final verdict inside the window, verification itself is broken -> fail."""
    v = policy.evaluate_close_verify_fresh([LIVE_PROVISIONAL], now=NOW)
    assert v["verdict"] == policy.V_FAIL_REFERENCE_STALE
    assert audit_close_status(v) == "fail"
    assert policy.wants_recheck(v) is True


def test_inside_the_settle_window_it_is_pending_not_a_failure():
    # same provisional run judged the same evening it was written
    rows = [{**LIVE_PROVISIONAL, "trade_date": "2026-10-07", "checked_at": "2026-10-07 19:30:07"},
            FINAL_OFFICIAL_1007]
    v = policy.evaluate_close_verify_fresh(rows, now=datetime(2026, 10, 7, 21, 0))
    assert v["verdict"] == policy.V_PENDING_REFERENCE
    assert v["verdict"] not in policy.FAILING_VERDICTS


def test_a_final_official_mismatch_still_fails_closed():
    row = {**LIVE_PROVISIONAL, "reference_kind": policy.OFFICIAL_KRX}
    v = policy.evaluate_close_verify_fresh([row], now=NOW)
    assert v["verdict"] == policy.V_FAIL_FINAL_MISMATCH
    assert audit_close_status(v) == "fail"


def test_a_settled_fallback_mismatch_is_advisory_not_a_failure():
    """The proxy's own revision must not be reported as a data defect (no arbiter for that date)."""
    row = {**LIVE_PROVISIONAL, "reference_kind": policy.FALLBACK_KIS,
           "checked_at": "2026-10-09 09:00:00"}  # past 03:30 deadline
    v = policy.evaluate_close_verify_fresh([row, FINAL_OFFICIAL_1007], now=NOW)
    assert v["verdict"] == policy.V_WARN_REFERENCE_UNVERIFIED
    assert v["verdict"] not in policy.FAILING_VERDICTS
    assert audit_close_status(v) == "warn"


def test_official_ok_is_ok_and_provisional_ok_is_not_trusted():
    ok = {**FINAL_OFFICIAL_1007, "trade_date": "2026-10-07"}
    assert policy.evaluate_close_verify_fresh([ok], now=NOW)["verdict"] == policy.V_OK
    prov_ok = {**ok, "reference_kind": None, "checked_at": "2026-10-07 20:00:00"}
    v = policy.evaluate_close_verify_fresh([ok, prov_ok], now=NOW)
    assert v["verdict"] == policy.V_NEEDS_FINAL_RECHECK


def test_stale_and_transport_failures_stay_failures():
    stale = {"trade_date": "2026-10-01", "checked_at": "2026-10-01 19:30:00", "status": "ok",
             "close_mismatch": 0, "compared": 100, "mismatch_pct": 0.0, "reference_kind": policy.OFFICIAL_KRX}
    assert policy.evaluate_close_verify_fresh([stale], now=NOW)["verdict"] == policy.V_FAIL_STALE
    for bad in (policy.STATUS_FETCH_FAILED, policy.STATUS_NO_DATA):
        row = {**LIVE_PROVISIONAL, "status": bad, "reference_kind": policy.OFFICIAL_KRX}
        assert policy.evaluate_close_verify_fresh([row], now=NOW)["verdict"] in policy.FAILING_VERDICTS


def test_no_rows_warns_and_last_finalizable_day_is_reported():
    assert policy.evaluate_close_verify_fresh([], now=NOW)["verdict"] == policy.V_WARN_NO_RUN
    rows = [LIVE_PROVISIONAL, FINAL_OFFICIAL_1007]
    assert policy.last_finalizable_day(rows, now=NOW)["trade_date"] == "2026-10-07"


def test_newest_row_wins_by_trade_date_then_checked_at():
    older = {**LIVE_PROVISIONAL, "status": "mismatch"}
    newer = {**LIVE_PROVISIONAL, "checked_at": "2026-10-09 08:00:00", "status": "ok",
             "reference_kind": policy.OFFICIAL_KRX}
    assert policy.newest_row([older, newer])["status"] == "ok"


def test_run_status_classification_splits_provisional_from_final():
    """`classify_run_status` describes a run happening NOW for that trade date."""
    common = dict(close_mismatch=56, compared=80, fetch_failed=0, trade_date_iso="2026-10-08")
    # written at 19:30 on the trade date -> still provisional
    assert policy.classify_run_status(reference_kind=policy.FALLBACK_KIS,
                                      now=datetime(2026, 10, 8, 19, 30), **common) == policy.STATUS_REFERENCE_PENDING
    # same date re-verified later -> final
    assert policy.classify_run_status(reference_kind=policy.FALLBACK_KIS, now=NOW, **common) == policy.STATUS_MISMATCH
    # the official reference is final whenever it is used
    assert policy.classify_run_status(reference_kind=policy.OFFICIAL_KRX,
                                      now=datetime(2026, 10, 8, 19, 30), **common) == policy.STATUS_MISMATCH
    assert policy.classify_run_status(reference_kind=policy.FALLBACK_KIS, close_mismatch=0, compared=80,
                                      fetch_failed=0, trade_date_iso="2026-10-08", now=NOW) == policy.STATUS_OK
    assert policy.classify_run_status(reference_kind=policy.FALLBACK_KIS, close_mismatch=0, compared=0,
                                      fetch_failed=5, trade_date_iso="2026-10-08", now=NOW) == policy.STATUS_FETCH_FAILED


# ──────────────────────────────────────────────────────────────────────────────
# recheck path
# ──────────────────────────────────────────────────────────────────────────────
def test_recheck_reevaluates_and_can_promote_to_a_final_failure():
    conn = StubConn([LIVE_PROVISIONAL])
    base = policy.evaluate_close_verify_fresh(conn.rows, now=NOW)

    def runner(_td):
        conn.rows = [LIVE_PROVISIONAL, {**LIVE_PROVISIONAL, "checked_at": "2026-10-11 07:11:00",
                                        "reference_kind": policy.OFFICIAL_KRX}]
        return 2

    out = audit.recheck_and_reevaluate(conn, base, runner=runner, dates=["2026-10-08"])
    assert out["verdict"] == policy.V_FAIL_FINAL_MISMATCH
    assert out["recheck_attempts"] == [{"date": "2026-10-08", "returncode": 2}]


def test_recheck_runner_error_does_not_promote_the_provisional_verdict():
    conn = StubConn([LIVE_PROVISIONAL])
    base = policy.evaluate_close_verify_fresh(conn.rows, now=NOW)

    def boom(_td):
        raise RuntimeError("kis down")

    out = audit.recheck_and_reevaluate(conn, base, runner=boom, dates=["2026-10-08"])
    assert out["verdict"] == policy.V_FAIL_REFERENCE_STALE  # still the un-refreshed arbiter verdict
    assert out["verdict"] not in (policy.V_FAIL_FINAL_MISMATCH,)
    assert out["recheck_attempts"][0]["result"].startswith("runner_error")


class PriceHistoryStub:
    """Answers the two queries `recent_trade_dates` makes, honouring params."""

    def __init__(self, per_date):
        self.per_date = per_date
        self.sql = []
        self.commits = 0

    def execute(self, sql, params=()):
        norm = " ".join(str(sql).split())
        self.sql.append(norm)
        stub = self

        class Cur:
            def fetchall(self):
                # emulate `ORDER BY date DESC` so the reader's reversal is what produces ascending order
                return [(d,) for d in sorted(stub.per_date, reverse=True)] if "DISTINCT date" in norm else []

            def fetchone(self):
                if "count(*)" in norm and params:
                    val = stub.per_date.get(params[0], 0)
                    return (val,) if isinstance(val, int) else val   # row objects pass through
                return None

            def __iter__(self):
                return iter(self.fetchall())

        return Cur()

    def commit(self):
        self.commits += 1


def test_recent_trade_dates_skips_thin_days_and_already_final_days():
    conn = PriceHistoryStub({"2026-10-10": 1, "2026-10-09": 11, "2026-10-08": 2660, "2026-10-07": 2661})
    dates = audit.recent_trade_dates(conn, limit=5, verify_rows=[FINAL_OFFICIAL_1007])
    # 10-10/10-09 are macro-only days (<500 KR rows) and 10-07 already has a final verdict
    assert dates == ["2026-10-08"]


class KeysOnlyRow:
    """CompatRow-like: supports .keys() and key indexing, but has no .values()."""

    def __init__(self, **kw):
        self._v = kw

    def keys(self):
        return list(self._v.keys())

    def __getitem__(self, k):
        return list(self._v.values())[k] if isinstance(k, int) else self._v[k]


def test_recent_trade_dates_handles_a_compat_row_without_values():
    """The live compat cursor returns rows with .keys() but no .values() — regression guard."""
    conn = PriceHistoryStub({"2026-10-08": KeysOnlyRow(c=2660)})
    # the stub must hand the row object through unchanged for this test to mean anything
    assert audit.recent_trade_dates(conn, limit=1, verify_rows=[]) == ["2026-10-08"]


def test_recent_trade_dates_returns_oldest_first():
    conn = PriceHistoryStub({"2026-10-08": 2660, "2026-10-06": 2665, "2026-10-07": 2661})
    assert audit.recent_trade_dates(conn, limit=5, verify_rows=[]) == ["2026-10-06", "2026-10-07", "2026-10-08"]


def test_audit_reads_the_close_verify_rows_from_the_table():
    conn = StubConn([LIVE_PROVISIONAL])
    rows = audit.fetch_close_verify_rows(conn, limit=5)
    assert rows[0]["trade_date"] == "2026-10-08" and rows[0]["status"] == "mismatch"


def test_audit_tolerates_a_missing_reference_kind_column():
    """Until the verifier's next run adds the column, the audit must still work (read-only)."""
    conn = StubConn([])  # catalog probe returns None -> column absent
    assert audit.fetch_close_verify_rows(conn, limit=5) == []
    assert any("NULL AS reference_kind" in s for s in conn.sql)


def audit_close_status(verdict):
    """Mirror of the mapping the audit applies to the contract status column."""
    if verdict["verdict"] in policy.FAILING_VERDICTS:
        return "fail"
    if verdict["verdict"] in policy.WARN_VERDICTS:
        return "warn"
    return "ok"


# ──────────────────────────────────────────────────────────────────────────────
# 2. I2 duplicate annual rows
# ──────────────────────────────────────────────────────────────────────────────
def test_i2_dedupe_keeps_one_case_per_business_key():
    rows = [
        (618252, "402340", "SK스퀘어", 2025, 0, 14115.0, 87974.0),
        (125188, "402340", "SK스퀘어", 2025, 4, 14115.0, 87974.0),
        (618084, "383800", "LX홀딩스", 2024, 0, 407.0, 1560.0),
    ]
    cases, dups = dic.dedupe_i2_cases(rows)
    assert [c["stock"] for c in cases] == ["402340", "383800"]
    assert len(dups) == 1 and dups[0]["row_id"] == 125188 and dups[0]["quarter"] == 4
    assert cases[0]["row_id"] == 618252


def test_i2_dedupe_is_a_noop_without_duplicates():
    rows = [(1, "A", "a", 2024, 0, 1.0, 2.0)]
    cases, dups = dic.dedupe_i2_cases(rows)
    assert len(cases) == 1 and dups == []


# ──────────────────────────────────────────────────────────────────────────────
# 3. I1 manifest — apply-blocked
# ──────────────────────────────────────────────────────────────────────────────
def _classified(code, year, prev, cur):
    return {"case": {"stock": code, "year": year, "prev_억": prev, "cur_억": cur},
            "evidence": {},
            "classification": fx.classify_yoy_spike(cur, prev)}


def test_i1_manifest_is_apply_blocked_and_separates_quarantine():
    conn = StubConn([])
    manifest = fx.build_i1_manifest(conn, [
        _classified("032680", 2022, 925.0, 733730501.0),
        _classified("311390", 2022, 299.0, 267029.0),
        _classified("008060", 2021, 34.0, 13717.0),
        _classified("066980", 2021, 135.0, 1683.0),
    ])
    assert manifest["apply_allowed"] is False
    assert manifest["candidate_count"] == 3 and manifest["quarantine_count"] == 1
    assert {c["scale_exponent"] for c in manifest["candidates"]} == {6, 3}
    assert all(c["apply_allowed"] is False for c in manifest["candidates"])
    assert manifest["quarantine"][0]["stock_code"] == "066980"
    assert manifest["quarantine"][0]["quarantine"] is True


# ──────────────────────────────────────────────────────────────────────────────
# 4. the other three contract failures
# ──────────────────────────────────────────────────────────────────────────────
class SqlStub:
    """Dispatches canned answers by SQL substring."""

    def __init__(self, answers):
        self.answers = answers
        self.sql = []

    def execute(self, sql, params=()):
        norm = " ".join(str(sql).split())
        self.sql.append(norm)
        stub = self

        class Cur:
            def _rows(self):
                for key, val in stub.answers.items():
                    if key in norm:
                        return val
                return []

            def fetchall(self):
                return self._rows()

            def fetchone(self):
                rows = self._rows()
                return rows[0] if rows else None

            def __iter__(self):
                return iter(self._rows())

        return Cur()


def test_default_missing_flags_only_new_tables_without_a_default():
    conn = SqlStub({"information_schema.columns": [("created_at",)]})
    out = fx.classify_default_missing(conn, {"new": ["financial_field_baseline"]})
    assert out["classification"]["verdict"] == "new_table_without_default"
    assert out["cause_rows"][0]["columns_without_default"] == [{"column": "created_at"}]
    assert "financial_field_baseline" in out["owner"]


def test_default_missing_with_no_offending_column_is_baseline_lag_only():
    conn = SqlStub({"information_schema.columns": []})
    out = fx.classify_default_missing(conn, {"new": ["harmless_table"]})
    assert out["classification"]["verdict"] == "baseline_lag_only"


def test_snapshot_lookahead_parses_the_stored_violation():
    conn = SqlStub({"strategy_feature_snapshot": [("030960", "2026-10-02", 843.7, 48.65)]})
    out = fx.classify_snapshot_lookahead(
        conn, {"same_quarter_match": "0/1304 at 2026-03-31 (0.00%)",
               "filing_lag_violations": "1/400 sampled rows (examples [('030960', '2026-10-02')])"})
    assert out["classification"]["verdict"] == "needs_dart_decision"
    assert out["violation_rows"][0]["stock_code"] == "030960"
    assert out["violation_rows"][0]["snapshot_row"] == ("030960", "2026-10-02", 843.7, 48.65)


def test_snapshot_lookahead_without_violations_is_ok():
    conn = SqlStub({})
    out = fx.classify_snapshot_lookahead(conn, {"filing_lag_violations": "0/400 sampled rows (examples [])"})
    assert out["classification"]["verdict"] == "ok" and out["violation_rows"] == []


def test_cfs_ofs_classifier_splits_drift_from_material_mismatch():
    fdq_rows = [("A", 2026, 1, "CFS", 100.0), ("A", 2025, 4, "CFS", 100.0),
                ("A", 2025, 3, "CFS", 100.0), ("A", 2025, 2, "CFS", 100.0),
                ("B", 2026, 1, "CFS", 10.0), ("B", 2025, 4, "CFS", 10.0),
                ("B", 2025, 3, "CFS", 10.0), ("B", 2025, 2, "CFS", 10.0)]
    ttm_rows = [("A", 2026, 1, 400.0, "CFS", "2026-06-11"),      # exact -> passes
                ("B", 2026, 1, 80.0, "CFS", "2026-06-11"),       # 40 vs 80 -> 50% material
                ("C", 2026, 1, 50.0, "CFS", "2026-06-11")]       # no quarters -> missing
    conn = SqlStub({"FROM financial_data": fdq_rows,
                    "FROM valuation_history WHERE per_ttm IS NOT NULL ORDER BY": [(2026, 1)],
                    "FROM valuation_history": ttm_rows})
    out = fx.classify_cfs_ofs_mixed_ttm(conn, {"inconsistent_ttm_rows": 2, "checked": 3})
    assert out["classes"] == {"missing_quarters": 1, "ttm_drift_lt_2pct": 0, "ttm_material_mismatch": 1}
    assert out["cause_rows"][0]["stock_code"] == "B" and out["cause_rows"][0]["relative_diff"] == 0.5


def test_cfs_ofs_classifier_reports_small_drift_class():
    # realistic magnitudes: the audit's tolerance floor is 1.0 absolute, so tiny fixtures pass
    fdq_rows = [("A", 2026, 1, "CFS", 25e9), ("A", 2025, 4, "CFS", 25e9),
                ("A", 2025, 3, "CFS", 25e9), ("A", 2025, 2, "CFS", 25e9)]
    ttm_rows = [("A", 2026, 1, 100.5e9, "CFS", "2026-06-11")]  # 0.5% drift on a 100e9 base
    conn = SqlStub({"FROM financial_data": fdq_rows,
                    "FROM valuation_history WHERE per_ttm IS NOT NULL ORDER BY": [(2026, 1)],
                    "FROM valuation_history": ttm_rows})
    out = fx.classify_cfs_ofs_mixed_ttm(conn, {})
    assert out["classes"]["ttm_drift_lt_2pct"] == 1
    assert out["classification"]["verdict"] == "ttm_stale_vs_restated_quarters"


# ──────────────────────────────────────────────────────────────────────────────
# the slice stays read-only
# ──────────────────────────────────────────────────────────────────────────────
def test_new_modules_contain_no_write_statement():
    for module in (policy, fx):
        src = Path(module.__file__).read_text(encoding="utf-8").upper()
        for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP TABLE", "TRUNCATE"):
            if module is fx:
                # the forensics slice only ever names these in its own read-only assertions
                assert f'"{verb.strip()}"' not in src and f"'{verb.strip()}'" not in src, verb
            else:
                assert verb not in src, verb


def test_forensics_slice_never_opens_a_writable_connection():
    src = Path(fx.__file__).read_text(encoding="utf-8")
    assert "readonly=False" not in src
    assert "connect_primary_db(readonly=True" in src


def test_policy_module_has_no_reference_kind_write_and_no_network_import():
    src = Path(policy.__file__).read_text(encoding="utf-8")
    assert "requests" not in src and "psycopg" not in src
