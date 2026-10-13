"""2026-10-11 (planner review) regression slice for `close_verify_fresh`:

  1. **Root cause outranks freshness** — `evaluate_close_verify_fresh` must report a failure the
     newest row itself names (official final mismatch / fetch failure / no-data) even when that row
     has aged past `max_age_days` or no day was finalizable, instead of relabelling it `fail_stale`
     / `fail_reference_stale`. Verified with (a) direct verdicts, (b) a **mutation** re-implementation
     of the pre-fix order, (c) a full status × reference-kind × age grid proving the reordering can
     never downgrade a failure, and (d) a source-order assertion for the same mutation.
  2. **The recheck is read-only** — `recheck_and_reevaluate` may read the rows the verifier
     subprocess appended, but must not commit, must not write through the audit connection, and may
     never promote a verdict on its own.

Fixtures only: no live PostgreSQL, no network, no clock reads (every `now` is explicit).
"""
from __future__ import annotations

import inspect
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / "scripts")):
    if p not in sys.path:
        sys.path.insert(0, p)

import close_verify_policy as policy  # noqa: E402
import data_contract_audit_20260925 as audit  # noqa: E402

NOW = datetime(2026, 10, 11, 7, 10, 0)


def row(status="mismatch", *, trade_date="2026-10-08", checked_at="2026-10-08 19:30:07",
        reference_kind=None, close_mismatch=56, compared=80):
    return {"trade_date": trade_date, "checked_at": checked_at, "status": status,
            "close_mismatch": close_mismatch, "compared": compared, "mismatch_pct": 0.7,
            "reference_kind": reference_kind}


def day(offset_days: int) -> str:
    return (NOW.date() - timedelta(days=offset_days)).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# 1a. the verdicts the planner asked for
# ──────────────────────────────────────────────────────────────────────────────
def test_a_stale_official_final_mismatch_is_reported_as_a_mismatch_not_as_stale():
    """The row aged past the window AND it is a real official disagreement — cause must survive.

    Before the fix this returned `fail_stale`, which is verified by the mutation test below; the
    contract's promise is "a genuine final mismatch keeps failing as a mismatch".
    """
    stale = row(reference_kind=policy.OFFICIAL_KRX, trade_date=day(30),
                checked_at=f"{day(30)} 19:30:00")
    v = policy.evaluate_close_verify_fresh([stale], now=NOW)
    assert v["verdict"] == policy.V_FAIL_FINAL_MISMATCH
    assert v["verdict"] in policy.FAILING_VERDICTS
    assert v["root_cause_priority"] is True
    assert v["freshness_verdict_shadowed"] == policy.V_FAIL_STALE
    assert v["age_days"] == 30 and v["max_age_days"] == 5          # freshness data still reported
    assert audit_status(v) == "fail"


def test_a_stale_official_final_mismatch_outranks_a_stale_arbiter_too():
    """No finalizable day inside the window would say `fail_reference_stale`; mismatch still wins."""
    stale = row(reference_kind=policy.OFFICIAL_KRX, trade_date=day(40),
                checked_at=f"{day(40)} 19:30:00")
    v = policy.evaluate_close_verify_fresh([stale], now=NOW)
    assert v["verdict"] == policy.V_FAIL_FINAL_MISMATCH
    assert v["freshness_verdict_shadowed"] == policy.V_FAIL_STALE
    # and with a fresh row whose date is inside the window but which is NOT finalizable:
    prov = row(reference_kind=None, trade_date=day(3), checked_at=f"{day(3)} 19:30:00")
    v2 = policy.evaluate_close_verify_fresh([prov], now=NOW)
    assert v2["verdict"] == policy.V_FAIL_REFERENCE_STALE          # unchanged for the non-root case


def test_stale_fetch_failure_and_no_data_keep_their_cause():
    for status, expected in ((policy.STATUS_FETCH_FAILED, policy.V_FAIL_FETCH),
                             (policy.STATUS_NO_DATA, policy.V_FAIL_NO_DATA)):
        stale = row(status, reference_kind=policy.OFFICIAL_KRX, trade_date=day(30),
                    checked_at=f"{day(30)} 19:30:00")
        v = policy.evaluate_close_verify_fresh([stale], now=NOW)
        assert v["verdict"] == expected, status
        assert v["freshness_verdict_shadowed"] == policy.V_FAIL_STALE
        assert audit_status(v) == "fail"


def test_a_fresh_official_mismatch_reports_no_shadowed_verdict():
    """No freshness conflict ⇒ no shadow field; the priority flag alone is not evidence of masking."""
    v = policy.evaluate_close_verify_fresh([row(reference_kind=policy.OFFICIAL_KRX)], now=NOW)
    assert v["verdict"] == policy.V_FAIL_FINAL_MISMATCH
    assert v["root_cause_priority"] is True
    assert "freshness_verdict_shadowed" not in v


def test_stale_rows_without_a_named_cause_are_still_stale():
    """The reordering must not swallow the staleness gate itself."""
    ok = row("ok", close_mismatch=0, compared=100, reference_kind=policy.OFFICIAL_KRX,
             trade_date=day(30), checked_at=f"{day(30)} 19:30:00")
    v = policy.evaluate_close_verify_fresh([ok], now=NOW)
    assert v["verdict"] == policy.V_FAIL_STALE
    assert "root_cause_priority" not in v
    # a settled fallback disagreement aged past the window stays stale (still fail-closed)
    fb = row("mismatch", reference_kind=policy.FALLBACK_KIS, trade_date=day(30),
             checked_at=f"{day(30)} 19:30:00")
    assert policy.evaluate_close_verify_fresh([fb], now=NOW)["verdict"] == policy.V_FAIL_STALE


def test_every_root_cause_verdict_is_a_failing_verdict():
    """The hoist is only safe because all of these already fail the contract."""
    cases = [row(policy.STATUS_FETCH_FAILED), row(policy.STATUS_NO_DATA),
             row("mismatch", reference_kind=policy.OFFICIAL_KRX),
             row(policy.STATUS_REFERENCE_PENDING, reference_kind=policy.OFFICIAL_KRX),
             row("", reference_kind=policy.OFFICIAL_KRX),
             row("ok"), row("mismatch"), row("mismatch", reference_kind=policy.FALLBACK_KIS)]
    seen = {policy._named_root_cause(c, NOW)[0] for c in cases if policy._named_root_cause(c, NOW)}
    assert seen and seen <= set(policy.FAILING_VERDICTS)
    # the ambiguous ones must NOT be hoisted
    assert policy._named_root_cause(row("ok"), NOW) is None
    assert policy._named_root_cause(row("mismatch"), NOW) is None
    assert policy._named_root_cause(row("mismatch", reference_kind=policy.FALLBACK_KIS), NOW) is None


# ──────────────────────────────────────────────────────────────────────────────
# 1b. mutation: the pre-fix order must FAIL these assertions
# ──────────────────────────────────────────────────────────────────────────────
def pre_fix_order(rows, *, now: datetime, max_age_days: int = 5) -> str:
    """The exact pre-fix evaluation order (freshness gates first), reconstructed for mutation."""
    r = policy.newest_row(rows)
    if r is None:
        return policy.V_WARN_NO_RUN
    age = (now.date() - datetime.fromisoformat(r["trade_date"]).date()).days
    arbiter = (policy.last_finalizable_day(rows, now=now) or {}).get("trade_date")
    if age > max_age_days:
        return policy.V_FAIL_STALE
    if arbiter is None or (now.date() - datetime.fromisoformat(arbiter).date()).days > max_age_days:
        return policy.V_FAIL_REFERENCE_STALE
    return policy.V_OK


def test_the_pre_fix_order_would_hide_the_cause_so_this_suite_catches_a_revert():
    """Mutation guard: swapping the two blocks back makes the verdict — and these tests — revert."""
    stale_official = row(reference_kind=policy.OFFICIAL_KRX, trade_date=day(30),
                         checked_at=f"{day(30)} 19:30:00")
    assert pre_fix_order([stale_official], now=NOW) == policy.V_FAIL_STALE        # the regression
    assert policy.evaluate_close_verify_fresh([stale_official], now=NOW)["verdict"] \
        == policy.V_FAIL_FINAL_MISMATCH                                          # the fix
    stale_fetch = row(policy.STATUS_FETCH_FAILED, reference_kind=policy.OFFICIAL_KRX,
                      trade_date=day(30), checked_at=f"{day(30)} 19:30:00")
    assert pre_fix_order([stale_fetch], now=NOW) == policy.V_FAIL_STALE
    assert policy.evaluate_close_verify_fresh([stale_fetch], now=NOW)["verdict"] == policy.V_FAIL_FETCH


def test_the_root_cause_lookup_precedes_the_freshness_gates_in_the_source():
    """Structural mutation guard — reordering the blocks fails here even if fixtures are edited."""
    src = inspect.getsource(policy.evaluate_close_verify_fresh)
    root_at = src.index("_named_root_cause(row")
    assert root_at < src.index("if age > max_age_days")
    assert root_at < src.index("V_FAIL_REFERENCE_STALE,")
    # the fragile part: the early return must actually sit before the age gate, not merely be named
    assert src.index("root_cause_priority") < src.index("if age > max_age_days")


# ──────────────────────────────────────────────────────────────────────────────
# 1c. grid: the reordering can never downgrade a failure to a warning
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("status", ["ok", "mismatch", policy.STATUS_REFERENCE_PENDING,
                                    policy.STATUS_FETCH_FAILED, policy.STATUS_NO_DATA])
@pytest.mark.parametrize("kind", [None, policy.OFFICIAL_KRX, policy.FALLBACK_KIS])
@pytest.mark.parametrize("age_days", [0, 3, 30])
def test_no_failure_is_downgraded_by_the_priority_reordering(status, kind, age_days):
    checked = f"{day(age_days)} 19:30:00" if age_days else "2026-10-11 05:00:00"
    r = row(status, trade_date=day(age_days), checked_at=checked, reference_kind=kind)
    rows = [r]
    fresh_arbiter = row("ok", close_mismatch=0, compared=100, reference_kind=policy.OFFICIAL_KRX,
                        trade_date=day(1), checked_at=f"{day(1)} 19:30:00")
    both = [r, fresh_arbiter]
    for sample in (rows, both):
        legacy = pre_fix_order(sample, now=NOW)
        v = policy.evaluate_close_verify_fresh(sample, now=NOW)
        if legacy in policy.FAILING_VERDICTS:
            assert v["verdict"] in policy.FAILING_VERDICTS, (status, kind, age_days, legacy, v)
        if v.get("root_cause_priority"):
            assert v["verdict"] in policy.FAILING_VERDICTS
            assert audit_status(v) == "fail"


def test_shadowed_field_equals_what_the_gates_alone_would_have_said():
    """Whenever a root cause wins, the shadowed value must be the freshness verdict — no invention."""
    for age_days, expected_shadow in ((30, policy.V_FAIL_STALE), (3, None)):
        r = row(reference_kind=policy.OFFICIAL_KRX, trade_date=day(age_days),
                checked_at=f"{day(age_days)} 19:30:00")
        v = policy.evaluate_close_verify_fresh([r], now=NOW)
        assert v["verdict"] == policy.V_FAIL_FINAL_MISMATCH
        assert v.get("freshness_verdict_shadowed") == expected_shadow
        assert policy._freshness_verdict(v["age_days"], v["last_finalizable_day"],
                                         now=NOW, max_age_days=5) == expected_shadow


def test_a_provisional_run_is_still_only_ever_a_warning():
    """The priority hoist must not touch the provisional path (the original defect's fix)."""
    prov = row(reference_kind=policy.FALLBACK_KIS, trade_date="2026-10-08",
               checked_at="2026-10-08 19:30:07")
    arb = row("ok", close_mismatch=0, compared=2747, reference_kind=policy.OFFICIAL_KRX,
              trade_date="2026-10-07", checked_at="2026-10-07 19:30:00")
    v = policy.evaluate_close_verify_fresh([prov, arb], now=NOW)
    assert v["verdict"] == policy.V_NEEDS_FINAL_RECHECK
    assert v["verdict"] not in policy.FAILING_VERDICTS
    assert audit_status(v) == "warn"


# ──────────────────────────────────────────────────────────────────────────────
# 2. the recheck may read, never write
# ──────────────────────────────────────────────────────────────────────────────
class RecordingConn:
    """Records every statement + commit/rollback so write behaviour is directly assertable."""

    def __init__(self, rows):
        self.rows = rows
        self.statements = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql, params=()):
        self.statements.append(" ".join(str(sql).split()))
        conn = self

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

    def rollback(self):
        self.rollbacks += 1


def audit_status(verdict):
    if verdict["verdict"] in policy.FAILING_VERDICTS:
        return "fail"
    if verdict["verdict"] in policy.WARN_VERDICTS:
        return "warn"
    return "ok"


WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "ALTER", "DROP", "TRUNCATE", "CREATE")


def test_recheck_only_reads_and_never_commits():
    conn = RecordingConn([row()])
    base = policy.evaluate_close_verify_fresh(conn.rows, now=NOW)

    def runner(_d):
        conn.rows = [row(), row(reference_kind=policy.OFFICIAL_KRX,
                                checked_at="2026-10-11 07:11:00")]
        return 2

    out = audit.recheck_and_reevaluate(conn, base, runner=runner, dates=["2026-10-08"])
    assert out["verdict"] == policy.V_FAIL_FINAL_MISMATCH      # re-derived, not promoted
    assert conn.commits == 0
    assert conn.rollbacks == 1                                 # snapshot released, nothing persisted
    assert out["recheck_readonly"] is True and out["recheck_writes_persisted"] == 0
    assert conn.statements and all(s.upper().startswith(("SELECT", "WITH")) for s in conn.statements)
    for s in conn.statements:
        assert not any(v in s.upper().split() for v in WRITE_VERBS), s


def test_recheck_source_contains_no_write_statement_and_no_commit():
    src = inspect.getsource(audit.recheck_and_reevaluate)
    assert ".commit(" not in src
    for verb in WRITE_VERBS:
        assert verb not in src.upper().replace("READ-ONLY", ""), verb
    # the only writer in this path is the verifier subprocess it shells out to
    run_src = inspect.getsource(audit.run_recheck)
    assert "verify_daily_close_vs_official.py" in run_src and "--no-alert" in run_src


def test_recheck_with_no_new_rows_cannot_flip_the_verdict():
    """A recheck that "succeeds" but produces no row must leave the verdict exactly as it was."""
    conn = RecordingConn([row()])
    base = policy.evaluate_close_verify_fresh(conn.rows, now=NOW)
    out = audit.recheck_and_reevaluate(conn, base, runner=lambda _d: 0, dates=["2026-10-08"])
    assert base["verdict"] == policy.V_FAIL_REFERENCE_STALE
    assert out["verdict"] == base["verdict"]
    assert conn.commits == 0
    assert out["recheck_attempts"] == [{"date": "2026-10-08", "returncode": 0}]


def test_recheck_runner_error_still_cannot_promote_and_leaves_no_write():
    conn = RecordingConn([row()])

    def boom(_d):
        raise RuntimeError("kis down")

    base = policy.evaluate_close_verify_fresh(conn.rows, now=NOW)
    out = audit.recheck_and_reevaluate(conn, base, runner=boom, dates=["2026-10-08"])
    assert out["verdict"] == policy.V_FAIL_REFERENCE_STALE
    assert out["verdict"] != policy.V_FAIL_FINAL_MISMATCH      # an infra error is not a finding
    assert conn.commits == 0 and conn.rollbacks == 0            # not even a snapshot release happened
    assert out["recheck_attempts"][0]["result"].startswith("runner_error")


def test_release_read_snapshot_tolerates_a_driver_without_rollback():
    class Bare:
        pass

    audit._release_read_snapshot(Bare())  # must not raise: a stub connection simply keeps its rows


class DispatchStub:
    """Answers the audit's close-verify SELECTs the way the live schema would (with reference_kind)."""

    def __init__(self, per_date, rows):
        self.per_date = per_date
        self.rows = rows
        self.statements = []

    def execute(self, sql, params=()):
        norm = " ".join(str(sql).split())
        self.statements.append(norm)
        stub = self

        class Cur:
            def _rows(self):
                if "information_schema.columns" in norm:
                    return [(1,)]
                if "DISTINCT date" in norm:
                    return [(d,) for d in sorted(stub.per_date, reverse=True)]
                if "count(*)" in norm and params:
                    return [(stub.per_date.get(params[0], 0),)]
                if "price_close_verify_log" in norm:
                    return stub.rows
                return []

            def fetchall(self):
                return self._rows()

            def fetchone(self):
                rows = self._rows()
                return rows[0] if rows else None

            def __iter__(self):
                return iter(self._rows())

        return Cur()

    def commit(self):
        raise AssertionError("the recheck must not commit")

    def rollback(self):
        pass


def test_recheck_is_capped_at_five_dates_per_day_and_runs_them_oldest_first():
    """The approved plan is ≤5 read-only re-verifications a day — pin the cap, not just the intent."""
    per_date = {f"2026-09-{d:02d}": 2000 for d in range(10, 22)}
    conn = DispatchStub(per_date, rows=[row(trade_date="2026-09-10", checked_at="2026-09-10 19:30:07")])
    calls = []

    def runner(d):
        calls.append(d)
        return 0          # no new rows -> the verdict keeps asking for a recheck, so the cap is what stops it

    base = policy.evaluate_close_verify_fresh(conn.rows, now=NOW)
    out = audit.recheck_and_reevaluate(conn, base, runner=runner)
    assert len(calls) <= 5 and len(out["recheck_attempts"]) == len(calls)
    assert calls == sorted(calls)                       # oldest first: an older official day must land first
    assert out["recheck_readonly"] is True


def test_run_recheck_shells_out_with_an_argv_list_and_no_alert():
    src = inspect.getsource(audit.run_recheck)
    assert "shell=True" not in src
    assert "--no-alert" in src and "--date" in src


def test_priority_and_readonly_slices_do_not_touch_live_db_or_network():
    for mod in (policy, audit):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "requests" not in src
    assert "subprocess" not in Path(policy.__file__).read_text(encoding="utf-8")
