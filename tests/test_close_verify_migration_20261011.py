"""2026-10-11 (planner review, second round) — the close-verify schema is now owned by a migration.

The Planner blocked the previous slice because a **scheduled run ALTERed a live table**: the verifier
added `price_close_verify_log.reference_kind` by itself. This suite pins the replacement contract:

  A. the migration's shape — up/down together, **no statement may touch rows** (no back-fill: a legacy
     row's source is never inferred), checksum marker, down never drops the table
  B. a **real PostgreSQL** round-trip in a throwaway schema — up / dry-run / re-run / drift refusal /
     unmarked-column refusal / down / privileges / `public` untouched
  C. the three-state provenance contract `unknown | provisional | final`, including the rule change:
     a row whose source is unrecorded is never treated as final (its timestamp is not used to guess)
  D. the verifier performs **no DDL** at all and writes rows with the column set the schema has

`public` is read-only throughout: the migration is only ever executed inside `close_verify_test_*`.
"""
from __future__ import annotations

import ast
import inspect
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import close_verify_policy as policy  # noqa: E402
import data_contract_audit_20260925 as audit  # noqa: E402
import verify_daily_close_vs_official as verifier  # noqa: E402
from db_compat import connect_primary_db  # noqa: E402
from migrations import MIGRATION  # noqa: E402
from migrations import close_verify_0001_reference_kind as mig  # noqa: E402
from migrations.runner import catalog_state, run  # noqa: E402

from close_verify_migration_support import (  # noqa: E402
    LIVE_LEGACY_ROWS, apply_down, apply_up, columns_of, isolated_schema, public_fingerprint,
    StatementRecorder, schema_exists,
)

NOW = datetime(2026, 10, 11, 7, 10, 0)


def row(status="mismatch", *, trade_date="2026-10-08", checked_at="2026-10-08 19:30:07",
        reference_kind=None, close_mismatch=56, compared=80):
    return {"trade_date": trade_date, "checked_at": checked_at, "status": status,
            "close_mismatch": close_mismatch, "compared": compared, "mismatch_pct": 0.7,
            "reference_kind": reference_kind}


# ──────────────────────────────────────────────────────────────────────────────
# A. migration shape — offline
# ──────────────────────────────────────────────────────────────────────────────
def test_up_and_down_are_submitted_together():
    assert len(MIGRATION.up()) >= 3 and len(MIGRATION.down()) >= 1


def test_no_statement_may_touch_a_row():
    """The core ruling: a legacy row's source is never inferred, so nothing may back-fill it."""
    mig.assert_no_data_statements()  # raises on violation
    for stmt in (*MIGRATION.up(), *MIGRATION.down()):
        head = stmt.render().upper()
        assert not head.startswith(("INSERT", "UPDATE", "DELETE", "TRUNCATE", "GRANT", "REVOKE")), head


def test_a_backfill_statement_would_be_rejected(monkeypatch):
    """Mutation: if someone adds an UPDATE, the guard must fire (not silently pass)."""
    bad_up = MIGRATION.up() + (mig.Stmt("UPDATE public.x SET reference_kind='fallback_kis'"),)
    monkeypatch.setattr(mig, "up_statements", lambda *a, **k: bad_up)
    with pytest.raises(AssertionError):
        mig.assert_no_data_statements()


def test_down_never_drops_the_table():
    rendered = " ".join(s.render().upper() for s in MIGRATION.down())
    assert "DROP TABLE" not in rendered
    assert "DROP COLUMN IF EXISTS REFERENCE_KIND" in rendered


def test_the_comment_marker_carries_the_checksum():
    comment = [s for s in MIGRATION.up() if s.render().upper().startswith("COMMENT")][0]
    assert mig.COMMENT_MARKER + MIGRATION.checksum in comment.render()
    assert "unknown" in comment.render()   # the vocabulary is stated where it is created
    # COMMENT is a utility statement: no bind parameters allowed (PG would reject `$1`)
    assert comment.params == () and "%s" not in comment.render()


def test_checksum_is_stable_and_ddl_edits_change_it():
    assert mig.compute_checksum() == mig.compute_checksum() == MIGRATION.checksum
    original = mig.up_statements
    try:
        mig.up_statements = lambda schema=mig.DEFAULT_SCHEMA, **kw: original(schema, **kw) + (
            mig.Stmt("ALTER TABLE x ADD COLUMN extra_thing TEXT"),)
        assert mig.compute_checksum() != MIGRATION.checksum
    finally:
        mig.up_statements = original


def test_create_statement_covers_the_live_shape_plus_the_new_column():
    create = [s for s in MIGRATION.up() if s.render().upper().startswith("CREATE TABLE")][0].render()
    for col in mig.EXPECTED_TABLE_COLUMNS + (mig.COLUMN,):
        assert col in create, col
    assert "BIGSERIAL PRIMARY KEY" in create


# ──────────────────────────────────────────────────────────────────────────────
# B. real PostgreSQL round-trip in a throwaway schema
# ──────────────────────────────────────────────────────────────────────────────
def test_dry_run_creates_nothing_and_reports_the_plan():
    with isolated_schema() as (conn, schema):
        rec = StatementRecorder(conn)
        result = apply_up(rec, schema, dry_run=True)
        assert result.status == "dry_run"
        assert result.executed == 0 and rec.write_statements() == []
        assert not catalog_state(conn, MIGRATION, schema)["table_exists"]


def test_up_creates_the_table_and_the_column():
    with isolated_schema() as (conn, schema):
        result = apply_up(conn, schema)
        assert result.status == "applied" and result.executed == 3
        cat = catalog_state(conn, MIGRATION, schema)
        assert cat["table_exists"] and cat["column_exists"]
        assert cat["recorded_checksum"] == MIGRATION.checksum
        assert mig.COLUMN in columns_of(conn, schema, MIGRATION.table)


def test_second_up_is_already_applied_with_zero_statements():
    with isolated_schema() as (conn, schema):
        assert apply_up(conn, schema).status == "applied"
        rec = StatementRecorder(conn)
        again = apply_up(rec, schema)
        assert again.status == "already_applied" and again.executed == 0
        assert rec.write_statements() == []
        assert columns_of(conn, schema, MIGRATION.table).count(mig.COLUMN) == 1


def test_legacy_rows_keep_null_and_are_exposed_as_unknown():
    """The whole point: migration adds a COLUMN, not a source. Rows stay `unknown` provenance."""
    cols = "id, trade_date, checked_at, status, close_mismatch, compared, detail"
    with isolated_schema(pre_create_legacy=True) as (conn, schema):
        t = f'"{schema}".{MIGRATION.table}'
        before = [tuple(r) for r in conn.execute(f"SELECT {cols} FROM {t} ORDER BY id")]
        rec = StatementRecorder(conn)
        result = apply_up(rec, schema)
        assert result.status == "applied"
        assert rec.data_statements() == []          # nothing wrote a row
        after = [tuple(r) for r in conn.execute(f"SELECT {cols}, {mig.COLUMN} FROM {t} ORDER BY id")]
        assert len(after) == len(before) == len(LIVE_LEGACY_ROWS)
        for old, new in zip(before, after):
            assert new[:len(old)] == old            # payload untouched, row count unchanged
            assert new[len(old)] is None            # provenance still NULL == unknown
        rows = [{"trade_date": t_[1], "checked_at": t_[2], "status": t_[3],
                 "reference_kind": t_[7], "close_mismatch": t_[4], "compared": t_[5]} for t_ in after]
        assert all(policy.provenance_state(r["reference_kind"], r["trade_date"], r["checked_at"])
                   == policy.PROV_UNKNOWN for r in rows)
        assert policy.provenance_summary(rows)["unknown"] == len(LIVE_LEGACY_ROWS)
        assert policy.last_finalizable_day(rows, now=NOW) is None


def test_drift_is_refused_without_executing_anything():
    import dataclasses
    with isolated_schema() as (conn, schema):
        assert apply_up(conn, schema).status == "applied"
        drifted = dataclasses.replace(MIGRATION, checksum="deadbeefdeadbeefdeadbeefdeadbeef")
        rec = StatementRecorder(conn)
        result = run(rec, drifted, direction="up", dry_run=False, schema=schema)
        assert result.status == "refused_drift" and result.executed == 0
        assert rec.write_statements() == []
        # and the marker is unchanged (still the applied checksum)
        assert catalog_state(conn, MIGRATION, schema)["recorded_checksum"] == MIGRATION.checksum


def test_an_unmarked_column_is_refused():
    with isolated_schema() as (conn, schema):
        conn.execute(f'CREATE TABLE "{schema}".{MIGRATION.table} (id BIGSERIAL PRIMARY KEY, '
                     f'{mig.COLUMN} TEXT)')
        conn.commit()
        result = apply_up(conn, schema)
        assert result.status == "refused_unmarked" and result.executed == 0
        assert result.reason and mig.COMMENT_MARKER in result.reason


def test_down_drops_only_the_column_and_keeps_every_row():
    with isolated_schema(pre_create_legacy=True) as (conn, schema):
        apply_up(conn, schema)
        rec = StatementRecorder(conn)
        result = apply_down(rec, schema)
        assert result.status == "applied" and result.executed == 1
        assert not catalog_state(conn, MIGRATION, schema)["column_exists"]
        assert catalog_state(conn, MIGRATION, schema)["table_exists"]      # table survives
        n = conn.execute(f'SELECT count(*) AS n FROM "{schema}".{MIGRATION.table}').fetchone()["n"]
        assert n == len(LIVE_LEGACY_ROWS)
        assert not any(s.upper().startswith(("DROP TABLE", "DELETE")) for s in rec.statements)


def test_down_on_an_unapplied_schema_is_skipped():
    with isolated_schema() as (conn, schema):
        result = apply_down(conn, schema)
        assert result.status == "skipped" and result.executed == 0


def test_down_then_up_returns_to_the_same_state():
    with isolated_schema() as (conn, schema):
        apply_up(conn, schema)
        apply_down(conn, schema)
        assert apply_up(conn, schema).status == "applied"
        cat = catalog_state(conn, MIGRATION, schema)
        assert cat["column_exists"] and cat["recorded_checksum"] == MIGRATION.checksum


def test_a_failing_statement_leaves_no_partial_application():
    class Broken:
        version, table, column, checksum = "broken", MIGRATION.table, mig.COLUMN, MIGRATION.checksum

        def up(self, schema=mig.DEFAULT_SCHEMA):
            return (mig.Stmt(f'ALTER TABLE "{schema}".{MIGRATION.table} ADD COLUMN ok_col TEXT'),
                    mig.Stmt(f'ALTER TABLE "{schema}".{MIGRATION.table} ADD COLUMN bad_col nosuchtype_xyz'))

        def down(self, schema=mig.DEFAULT_SCHEMA):
            return ()

    with isolated_schema(pre_create_legacy=True) as (conn, schema):
        result = run(conn, Broken(), direction="up", dry_run=False, schema=schema)
        assert result.status == "failed" and result.executed == 0
        assert "ok_col" not in columns_of(conn, schema, MIGRATION.table)   # rolled back
        assert mig.COLUMN not in columns_of(conn, schema, MIGRATION.table)


def test_privileges_stay_owner_only():
    """Fail-closed default: adding a column must not open the table (and no GRANT/REVOKE is issued)."""
    with isolated_schema() as (conn, schema):
        apply_up(conn, schema)
        meta = conn.execute(
            "SELECT c.relacl::text AS relacl FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname=%s AND c.relname=%s", (schema, MIGRATION.table)).fetchone()
        assert meta["relacl"] is None                       # owner-only, same as the live table
        privs = conn.execute(
            "SELECT has_table_privilege('public', %s, 'SELECT') AS sel, "
            "has_table_privilege('public', %s, 'INSERT') AS ins, "
            "has_table_privilege('public', %s, 'UPDATE') AS upd, "
            "has_table_privilege('public', %s, 'DELETE') AS del",
            tuple([f'"{schema}".{MIGRATION.table}'] * 4)).fetchone()
        assert privs["sel"] is False and privs["ins"] is False
        assert privs["upd"] is False and privs["del"] is False
        assert not any(s.render().upper().startswith(("GRANT", "REVOKE")) for s in MIGRATION.up())


def test_temp_schema_is_dropped_and_public_is_untouched():
    probe = connect_primary_db(readonly=True, timeout=60)
    try:
        before = public_fingerprint(probe)
    finally:
        probe.close()
    with isolated_schema(pre_create_legacy=True) as (conn, schema):
        apply_up(conn, schema)
        apply_down(conn, schema)
        assert schema_exists(conn, schema)
    after_conn = connect_primary_db(readonly=True, timeout=60)
    try:
        assert not schema_exists(after_conn, schema)
        assert public_fingerprint(after_conn) == before
    finally:
        after_conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# C. provenance contract
# ──────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("kind,checked,expected", [
    (policy.OFFICIAL_KRX, "2026-10-08 19:30:07", policy.PROV_FINAL),
    (policy.FALLBACK_KIS, "2026-10-08 19:30:07", policy.PROV_PROVISIONAL),   # before 10-09 03:30
    (policy.FALLBACK_KIS, "2026-10-09 09:00:00", policy.PROV_FINAL),         # after the deadline
    (None, "2026-10-08 19:30:07", policy.PROV_UNKNOWN),
    (None, "2026-09-25 16:04:50", policy.PROV_UNKNOWN),   # legacy row, checked past the deadline
    (None, "2026-10-11 07:00:00", policy.PROV_UNKNOWN),
    ("", "2026-10-08 19:30:07", policy.PROV_UNKNOWN),
    ("something_else", "2026-10-08 19:30:07", policy.PROV_UNKNOWN),
    (policy.FALLBACK_KIS, None, policy.PROV_UNKNOWN),
    (policy.FALLBACK_KIS, "not-a-timestamp", policy.PROV_UNKNOWN),
])
def test_provenance_state_is_three_valued(kind, checked, expected):
    assert policy.provenance_state(kind, "2026-10-08", checked) == expected
    assert policy.provenance_state(kind, "2026-10-08", checked) in policy.PROVENANCE_STATES


def test_unknown_provenance_is_never_final_even_when_the_timestamp_looks_settled():
    """The rule change: a legacy row past the settle deadline is `unknown`, not a settled fallback."""
    assert policy.row_is_final(None, "2026-09-23", "2026-09-25 16:04:50") is False
    assert policy.row_is_final(policy.FALLBACK_KIS, "2026-09-23", "2026-09-25 16:04:50") is True
    legacy = [{**row(), "trade_date": "2026-09-23", "checked_at": "2026-09-25 16:04:50"}]
    assert policy.last_finalizable_day(legacy, now=NOW) is None


def test_legacy_rows_never_fail_and_never_pass_on_their_own():
    """unknown + mismatch past its settle window asks for a re-verification instead of being dismissed."""
    arbiter = row(status="ok", close_mismatch=0, compared=2747, reference_kind=policy.OFFICIAL_KRX,
                  trade_date="2026-10-07", checked_at="2026-10-07 19:30:00")
    legacy = row(status="mismatch", trade_date="2026-10-08", checked_at="2026-10-09 09:00:00")
    assert policy.provenance_state(None, "2026-10-08", "2026-10-09 09:00:00") == policy.PROV_UNKNOWN
    v = policy.evaluate_close_verify_fresh([legacy, arbiter], now=NOW)
    assert v["provenance"] == policy.PROV_UNKNOWN
    assert v["verdict"] == policy.V_NEEDS_FINAL_RECHECK
    assert v["verdict"] not in policy.FAILING_VERDICTS
    assert "unknown" in v["reason"] and "inferred" in v["reason"]
    # the pre-fix behaviour would have called this a settled fallback disagreement (advisory);
    # that inference is exactly what the Planner banned, so assert the verdict is NOT that one
    assert v["verdict"] != policy.V_WARN_REFERENCE_UNVERIFIED
    # an unknown row that AGREES is not a verified OK either
    ok = policy.evaluate_close_verify_fresh([row(status="ok", close_mismatch=0, compared=80), arbiter], now=NOW)
    assert ok["provenance"] == policy.PROV_UNKNOWN and ok["verdict"] == policy.V_NEEDS_FINAL_RECHECK


def test_every_verdict_exposes_provenance():
    samples = [
        [row(reference_kind=policy.OFFICIAL_KRX)],                      # final mismatch
        [row(reference_kind=policy.FALLBACK_KIS, checked_at="2026-10-09 09:00:00")],  # advisory
        [row()],                                                        # unknown
        [row(status="ok", reference_kind=policy.OFFICIAL_KRX)],          # ok
        [row(status="no_data")],                                        # root cause
        [],                                                             # no run
    ]
    for rows in samples:
        v = policy.evaluate_close_verify_fresh(rows, now=NOW)
        if v["verdict"] == policy.V_WARN_NO_RUN:
            assert "provenance" not in v          # nothing to describe
        else:
            assert v["provenance"] in policy.PROVENANCE_STATES, v


def test_provenance_summary_counts_the_live_legacy_window():
    rows = [{"trade_date": td, "checked_at": ca, "status": st, "reference_kind": None,
             "close_mismatch": cm, "compared": cp} for td, ca, st, cm, cp in LIVE_LEGACY_ROWS]
    assert policy.provenance_summary(rows) == {"unknown": 5, "provisional": 0, "final": 0}
    rows.append(row(reference_kind=policy.OFFICIAL_KRX))
    rows.append(row(reference_kind=policy.FALLBACK_KIS, checked_at="2026-10-08 19:30:07"))
    assert policy.provenance_summary(rows) == {"unknown": 5, "provisional": 1, "final": 1}


def test_the_audit_exposes_provenance_counts_in_the_contract_detail():
    """Exposure: the daily contract log must say how many rows are unknown/provisional/final."""
    src = inspect.getsource(audit)
    assert '"provenance_counts"' in src and "policy.provenance_summary(rows_cv)" in src


def test_a_reverification_that_records_its_source_is_what_restores_finality():
    """An unknown-source row can never be OK; re-verifying it WITH a source can."""
    arbiter = row(status="ok", close_mismatch=0, compared=2747, reference_kind=policy.OFFICIAL_KRX,
                  trade_date="2026-10-07", checked_at="2026-10-07 19:30:00")
    legacy = row(status="ok", close_mismatch=0, compared=80)          # 2026-10-08, kind NULL
    v = policy.evaluate_close_verify_fresh([legacy, arbiter], now=NOW)
    assert v["provenance"] == policy.PROV_UNKNOWN and v["verdict"] == policy.V_NEEDS_FINAL_RECHECK
    fixed = {**legacy, "reference_kind": policy.OFFICIAL_KRX}
    v2 = policy.evaluate_close_verify_fresh([fixed, arbiter], now=NOW)
    assert v2["verdict"] == policy.V_OK and v2["provenance"] == policy.PROV_FINAL
    # without any finalizable day the unknown row alone cannot even be judged -> stale arbiter (fail)
    assert policy.evaluate_close_verify_fresh([legacy], now=NOW)["verdict"] == policy.V_FAIL_REFERENCE_STALE


# ──────────────────────────────────────────────────────────────────────────────
# D. the verifier performs no DDL
# ──────────────────────────────────────────────────────────────────────────────
def test_verifier_source_contains_no_ddl():
    src = Path(verifier.__file__).read_text(encoding="utf-8").upper()
    for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "DROP COLUMN", "ADD COLUMN",
                 "COMMENT ON", "GRANT ", "REVOKE "):
        assert verb not in src, verb
    assert not hasattr(verifier, "ensure_schema")
    assert not hasattr(verifier, "DDL") and not hasattr(verifier, "DDL_REFERENCE_KIND")


def test_verifier_refuses_to_run_when_the_table_is_absent_instead_of_creating_it():
    class Stub:
        def execute(self, sql, params=()):
            class Cur:
                def fetchone(self):
                    return None
            return Cur()

    state = verifier.schema_state(Stub())
    assert state == {"table": False, "reference_kind": False}
    src = inspect.getsource(verifier.main)
    assert "schema_not_migrated" in src and "return 3" in src


def test_schema_state_reports_the_column_separately():
    calls = []

    class Stub:
        def __init__(self, answers):
            self.answers = list(answers)

        def execute(self, sql, params=()):
            calls.append(" ".join(str(sql).split()))
            answer = self.answers.pop(0) if self.answers else None

            class Cur:
                def fetchone(self):
                    return (1,) if answer else None
            return Cur()

    assert verifier.schema_state(Stub([True, True])) == {"table": True, "reference_kind": True}
    assert verifier.schema_state(Stub([True, False])) == {"table": True, "reference_kind": False}
    assert any("information_schema.columns" in c for c in calls)


class InsertStub:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        self.statements.append((" ".join(str(sql).split()), params))

        class Cur:
            def fetchone(self):
                return None
        return Cur()

    def commit(self):
        pass


def test_insert_log_row_follows_the_schema_and_never_alters():
    common = dict(trade_date="2026-10-08", sampled=80, compared=80, close_mismatch=0, volume_mismatch=0,
                  mismatch_pct=0.0, fetch_failed=0, status="ok", detail="{}")
    with_col = InsertStub()
    verifier.insert_log_row(with_col, {"table": True, "reference_kind": True},
                            reference_kind=policy.OFFICIAL_KRX, **common)
    sql, params = with_col.statements[-1]
    assert "reference_kind" in sql and policy.OFFICIAL_KRX in params

    without_col = InsertStub()
    verifier.insert_log_row(without_col, {"table": True, "reference_kind": False},
                            reference_kind=policy.OFFICIAL_KRX, **common)
    sql, params = without_col.statements[-1]
    assert "reference_kind" not in sql and policy.OFFICIAL_KRX not in params
    assert len(params) == 9
    for _sql, _params in (with_col.statements + without_col.statements):
        assert not _sql.upper().startswith(("ALTER", "CREATE", "COMMENT"))


def test_verifier_only_ever_writes_the_log_table():
    """Static guard: the only write literal targets the log-table constant, and no DDL exists."""
    src = Path(verifier.__file__).read_text(encoding="utf-8")
    assert "INSERT INTO {LOG_TABLE}" in src
    tree = ast.parse(src)
    literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not [s for s in literals
                if s.strip().upper().startswith(("CREATE ", "ALTER ", "DROP ", "COMMENT ON", "GRANT ", "REVOKE "))]
