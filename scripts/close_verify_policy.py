#!/usr/bin/env python3
"""Policy for the daily official-close verification (`close_verify_fresh`).

Why this module exists
----------------------
The 2026-10-11 failure of `데이터계약점검` was **not** a data failure: `price_history` matched the
KRX Open API official close exactly (2,747/2,747 on 2026-10-07, 0 mismatched) while the *reference*
the verifier compared against — the KIS daily bar read at 19:30 on the trade date — was still
moving (re-reading the same reference later returned a different value for **9 of the 10** flagged
codes). A single `status != 'ok'` on that provisional run then failed the contract every single day,
for ever, because the contract only ever looks at the newest row.

So the reference is split into two kinds:

* ``official_krx``   — the KRX Open API sibling (`stock_price_daily`). Final by construction; a
                       disagreement against it is a REAL mismatch and must stay fail-closed.
* ``fallback_kis``   — the KIS daily bar. Provisional until ``settle_deadline(trade_date)``
                       (trade date 15:30 close + ``REFERENCE_SETTLE_HOURS``), because that endpoint
                       revises the bar after the check.

and the verdict is split accordingly: a provisional disagreement is ``pending_reference`` /
``needs_final_recheck`` (never a permanent failure), a final disagreement is ``fail_final_mismatch``.

Source-time provenance is a three-state property of each row — ``unknown`` / ``provisional`` /
``final`` (``provenance_state()``) — and it is the ONLY thing that may make a row final. A row whose
source is not recorded (``reference_kind IS NULL``: everything written before the column existed)
stays ``unknown``: its status is never inferred from its timestamp, and it can neither fail the
contract nor be accepted as a verified OK. It is exposed on every verdict as ``provenance``.

The log table's schema is owned by ``scripts/migrations/close_verify_0001_reference_kind.py``
(dry-run by default, ``--apply`` separately approved). Nothing in this module — or in the verifier
that writes the table — performs DDL: a scheduled run must never ALTER a live table.

Pure functions only — no DB, no network, no clock reads unless the caller passes one. The verifier
script and the contract audit both import this, so the two can never drift.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta

# KRX regular session ends 15:30; the daily bar is the closing auction print for that moment.
KR_CLOSE = time(15, 30)
# Hours after the close before the KIS fallback bar is treated as settled. Measured 2026-10-11:
# the reference was still revising 17h after the check (9/10 codes changed), so do not go below 12.
REFERENCE_SETTLE_HOURS = 12
CLOSE_TOLERANCE_KRW = 0.5
CLOSE_VERIFY_MAX_AGE_DAYS = 5
CLOSE_MISMATCH_SHARE_THRESHOLD = 0.05
OFFICIAL_MIN_ROWS = 500  # fewer rows than this for the date ⇒ the official sibling cannot judge it

OFFICIAL_KRX = "official_krx"
FALLBACK_KIS = "fallback_kis"

# Source-time provenance of ONE verification run — the only three states a row may be in.
#   unknown     the row does not record which source judged it (legacy rows: reference_kind IS NULL).
#               It is a THIRD state, never a silent fallback to "final" and never a silent "provisional".
#   provisional the KIS fallback bar before settle_deadline(trade_date) — it still revises itself.
#   final       official_krx (final by construction), or the KIS bar after its settle deadline.
PROV_UNKNOWN = "unknown"
PROV_PROVISIONAL = "provisional"
PROV_FINAL = "final"
PROVENANCE_STATES = (PROV_UNKNOWN, PROV_PROVISIONAL, PROV_FINAL)

# run-level statuses stored in price_close_verify_log.status
STATUS_OK = "ok"
STATUS_MISMATCH = "mismatch"
STATUS_REFERENCE_PENDING = "reference_pending"
STATUS_FETCH_FAILED = "fetch_failed"
STATUS_NO_DATA = "no_data"

# contract-level verdicts
V_OK = "ok"
V_FAIL_FINAL_MISMATCH = "fail_final_mismatch"
V_FAIL_FETCH = "fail_fetch"
V_FAIL_NO_DATA = "fail_no_data"
V_FAIL_STALE = "fail_stale"
V_FAIL_REFERENCE_STALE = "fail_reference_stale"
V_PENDING_REFERENCE = "pending_reference"
V_NEEDS_FINAL_RECHECK = "needs_final_recheck"
V_WARN_REFERENCE_UNVERIFIED = "warn_reference_unverified"
V_WARN_NO_RUN = "warn_no_run"

# Verdicts that fail the contract. `warn_reference_unverified` is deliberately NOT one of them:
# it is a disagreement against the KIS fallback, i.e. against a proxy we measured to revise its own
# bar after the check, with no official arbiter for that date. Failing on it would report the
# proxy's instability as a data defect. The arbiter going stale DOES fail (below), so a silent
# loss of independent verification cannot pass as healthy.
FAILING_VERDICTS = frozenset({V_FAIL_FINAL_MISMATCH, V_FAIL_FETCH, V_FAIL_NO_DATA, V_FAIL_STALE,
                              V_FAIL_REFERENCE_STALE})
WARN_VERDICTS = frozenset({V_WARN_NO_RUN, V_NEEDS_FINAL_RECHECK, V_PENDING_REFERENCE,
                           V_WARN_REFERENCE_UNVERIFIED})


def settle_deadline(trade_date_iso: str, settle_hours: int = REFERENCE_SETTLE_HOURS) -> datetime:
    """Moment after which the fallback (KIS) reference for ``trade_date`` stops being provisional."""
    d = datetime.fromisoformat(str(trade_date_iso)[:10])
    return datetime.combine(d.date(), KR_CLOSE) + timedelta(hours=settle_hours)


def classify_run_status(
    *,
    close_mismatch: int,
    compared: int,
    fetch_failed: int,
    reference_kind: str,
    trade_date_iso: str,
    now: datetime,
    threshold: float = CLOSE_MISMATCH_SHARE_THRESHOLD,
) -> str:
    """Status for ONE verification run (what the verifier stores)."""
    if compared <= 0:
        return STATUS_FETCH_FAILED if fetch_failed else STATUS_NO_DATA
    share = close_mismatch / compared
    if share <= threshold:
        return STATUS_OK
    if reference_kind == OFFICIAL_KRX or now >= settle_deadline(trade_date_iso):
        return STATUS_MISMATCH
    return STATUS_REFERENCE_PENDING


def provenance_state(reference_kind: str | None, trade_date_iso: str, checked_at=None,
                     now: datetime | None = None) -> str:
    """Source-time provenance of ONE verification run: ``unknown`` / ``provisional`` / ``final``.

    * ``official_krx``  → ``final`` by construction (the KRX sibling cannot be revised).
    * ``fallback_kis``  → ``provisional`` until ``settle_deadline(trade_date)``, ``final`` after it.
    * anything else (NULL, "", an unrecognised value) → ``unknown`` — and that is NOT a fallback to
      the timestamp rule. A row written before ``reference_kind`` existed does not record which
      source judged it, so "it was checked after the deadline, therefore it is final" would invent a
      source attribution for a past row. Unknown rows are therefore **never final**: they can neither
      fail the contract on their own nor be accepted as a verified OK. The fix is a re-verification
      that records its source, not a guess.

    ``now`` is accepted for call-site symmetry and deliberately unused: provenance is a property of
    the row, not of the moment it is read.
    """
    if reference_kind == OFFICIAL_KRX:
        return PROV_FINAL
    if reference_kind == FALLBACK_KIS:
        if checked_at is None:
            return PROV_UNKNOWN
        ts = _parse_ts(checked_at)
        if ts is None:
            return PROV_UNKNOWN
        return PROV_FINAL if ts >= settle_deadline(trade_date_iso) else PROV_PROVISIONAL
    return PROV_UNKNOWN


def row_is_final(reference_kind: str | None, trade_date_iso: str, checked_at, now: datetime | None = None) -> bool:
    """Is this verification run's verdict final (i.e. allowed to fail the contract)?

    Now a thin wrapper over `provenance_state`: only ``final`` provenance may fail the contract.
    Rows with ``unknown`` provenance are excluded — including the historic 2026-10-08 19:30 row,
    which is excluded because its source is unrecorded rather than because of its timestamp.
    """
    return provenance_state(reference_kind, trade_date_iso, checked_at, now) == PROV_FINAL


def _parse_ts(checked_at) -> datetime | None:
    if isinstance(checked_at, datetime):
        return checked_at
    raw = str(checked_at).strip().replace("T", " ")
    try:
        return datetime.fromisoformat(raw[:19])
    except ValueError:
        return None


def provenance_summary(rows, *, now: datetime | None = None) -> dict:
    """Counts per provenance state over the loaded window — the exposure the audit/verifier report."""
    out = {state: 0 for state in PROVENANCE_STATES}
    for r in rows or []:
        td = str(_row_value(r, "trade_date") or "")[:10]
        if not td:
            continue
        out[provenance_state(_row_value(r, "reference_kind"), td, _row_value(r, "checked_at"), now)] += 1
    return out


def _row_value(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    try:
        return row[key]
    except Exception:  # noqa: BLE001 - sqlite3.Row / CompatRow both support [] but differ on missing
        return default


def _named_root_cause(row, now: datetime) -> tuple[str, str] | None:
    """The failure the row itself names, independent of how old that row is.

    Every verdict this can return is already in ``FAILING_VERDICTS``, so hoisting it ABOVE the
    freshness gates can only preserve *which* failure is reported — it can never turn a failure
    into a warning. What it prevents: a genuine official mismatch whose row has aged past
    ``max_age_days`` being relabelled ``fail_stale``, or (when no other day was finalizable)
    ``fail_reference_stale``. Both are still failures, but they hide the cause on exactly the day an
    operator needs it — the contract's promise is "a real final mismatch keeps failing *as* a
    mismatch".
    """
    status = row["status"]
    if status == STATUS_FETCH_FAILED:
        return V_FAIL_FETCH, "reference fetch failed for the sampled codes"
    if status == STATUS_NO_DATA:
        return V_FAIL_NO_DATA, "price collection was incomplete for that date"
    if status in (STATUS_MISMATCH, STATUS_REFERENCE_PENDING, "") and row["reference_kind"] == OFFICIAL_KRX \
            and row_is_final(row["reference_kind"], row["trade_date"], row["checked_at"]):
        return V_FAIL_FINAL_MISMATCH, (f"final official reference disagrees on {row['close_mismatch']}/"
                                       f"{row['compared']} sampled codes")
    return None


def _freshness_verdict(age: int, arbiter_day: str | None, *, now: datetime, max_age_days: int) -> str | None:
    """What the freshness gates ALONE would say — kept as evidence when a root cause outranks them."""
    if age > max_age_days:
        return V_FAIL_STALE
    if arbiter_day is None or (now.date() - datetime.fromisoformat(arbiter_day).date()).days > max_age_days:
        return V_FAIL_REFERENCE_STALE
    return None


def newest_row(rows) -> dict | None:
    """The row the contract must judge: newest trade_date, then newest checked_at within it."""
    if not rows:
        return None
    norm = []
    for r in rows:
        norm.append({
            "trade_date": str(_row_value(r, "trade_date") or "")[:10],
            "checked_at": str(_row_value(r, "checked_at") or ""),
            "status": str(_row_value(r, "status") or ""),
            "close_mismatch": _row_value(r, "close_mismatch"),
            "compared": _row_value(r, "compared"),
            "mismatch_pct": _row_value(r, "mismatch_pct"),
            "reference_kind": _row_value(r, "reference_kind"),
        })
    return sorted(norm, key=lambda d: (d["trade_date"], d["checked_at"]))[-1]


def evaluate_close_verify_fresh(
    rows,
    *,
    now: datetime,
    max_age_days: int = CLOSE_VERIFY_MAX_AGE_DAYS,
) -> dict:
    """Contract verdict for ``close_verify_fresh``.

    Evaluation order is part of the contract:

      1. a failure the NEWEST row itself names (official final mismatch, fetch failure, no-data) —
         reported as that failure, whatever its age, so a faded/stale verification cannot mask it;
      2. the freshness gates (row older than ``max_age_days``; no finalizable day inside the window);
      3. the reference-kind logic (final agreement = ok, provisional = pending/recheck, fallback
         disagreement with no arbiter = advisory).

    Fail-closed is preserved for every *final* failure (official-reference disagreement, fetch
    failure, no-data, staleness). A provisional reference can only ever produce a non-failing
    verdict, because re-reading it later gives a different answer.
    """
    row = newest_row(rows)
    if row is None:
        return {"verdict": V_WARN_NO_RUN, "reason": "no verification run recorded yet"}
    age = (now.date() - datetime.fromisoformat(row["trade_date"]).date()).days
    arbiter = last_finalizable_day(rows, now=now)
    arbiter_day = (arbiter or {}).get("trade_date")
    base = {"trade_date": row["trade_date"], "checked_at": row["checked_at"],
            "status": row["status"], "reference_kind": row["reference_kind"],
            "close_mismatch": row["close_mismatch"], "compared": row["compared"],
            "age_days": age, "max_age_days": max_age_days,
            "last_finalizable_day": arbiter_day}
    # Source-time provenance is exposed on EVERY verdict (unknown / provisional / final) so the log
    # itself says whether the row recorded its source — never inferred from status or timestamps.
    prov = provenance_state(row["reference_kind"], row["trade_date"], row["checked_at"], now)
    base["provenance"] = prov

    # (1) Root cause outranks freshness: see _named_root_cause. Only failing verdicts can come back,
    # so this can never downgrade a failure to a warning.
    root = _named_root_cause(row, now)
    if root is not None:
        verdict, reason = root
        shadowed = _freshness_verdict(age, arbiter_day, now=now, max_age_days=max_age_days)
        out = {**base, "verdict": verdict, "reason": reason, "root_cause_priority": True}
        if shadowed is not None:
            # evidence that the gates were evaluated and could not hide the real cause
            out["freshness_verdict_shadowed"] = shadowed
        return out

    # (2) Freshness gates
    if age > max_age_days:
        return {**base, "verdict": V_FAIL_STALE,
                "reason": f"last verification trade_date {row['trade_date']} is {age}d old (>{max_age_days}d)"}

    # The independent arbiter itself must stay fresh: if no verdict has been final for more than
    # max_age_days, the verification capability is broken and that IS a failure (fail-closed).
    if arbiter_day is None or (now.date() - datetime.fromisoformat(arbiter_day).date()).days > max_age_days:
        return {**base, "verdict": V_FAIL_REFERENCE_STALE,
                "reason": f"no final (arbitrated) close verification within {max_age_days}d "
                          f"(last finalizable day: {arbiter_day}); the fallback reference alone cannot "
                          f"arbitrate, so the verification capability itself is stale"}

    # (3) Reference-kind / provenance logic
    status = row["status"]

    if status == STATUS_FETCH_FAILED:  # unreachable while (1) holds; kept so a reordering cannot lose it
        return {**base, "verdict": V_FAIL_FETCH, "reason": "reference fetch failed for the sampled codes"}
    if status == STATUS_NO_DATA:
        return {**base, "verdict": V_FAIL_NO_DATA, "reason": "price collection was incomplete for that date"}

    if status == STATUS_OK:
        if prov == PROV_FINAL:
            return {**base, "verdict": V_OK, "reason": "final reference agrees"}
        if prov == PROV_PROVISIONAL:
            # a provisional agreement is not trustworthy either — the reference was still moving
            return {**base, "verdict": V_NEEDS_FINAL_RECHECK,
                    "reason": "verdict came from a provisional reference that is now past its settle deadline; "
                              "re-verify before trusting it"}
        return {**base, "verdict": V_NEEDS_FINAL_RECHECK,
                "reason": "this row does not record which source judged it (provenance unknown), so an "
                          "agreement cannot be accepted as verified; re-verify to record the source"}

    if status in (STATUS_MISMATCH, STATUS_REFERENCE_PENDING, ""):
        if prov == PROV_FINAL and row["reference_kind"] == OFFICIAL_KRX:
            return {**base, "verdict": V_FAIL_FINAL_MISMATCH,
                    "reason": f"final official reference disagrees on {row['close_mismatch']}/"
                              f"{row['compared']} sampled codes"}
        if prov == PROV_FINAL:
            return {**base, "verdict": V_WARN_REFERENCE_UNVERIFIED,
                    "reason": f"the fallback reference disagrees on {row['close_mismatch']}/{row['compared']} "
                              f"sampled codes but no official reference exists for {row['trade_date']} — "
                              f"the fallback is a proxy that revises itself after the check, so this is "
                              f"advisory until an official close for that date is available"}
        if now >= settle_deadline(row["trade_date"]):
            detail = ("provisional disagreement; the reference has since settled — re-verify "
                      "(a final disagreement still fails)") if prov == PROV_PROVISIONAL else \
                     ("unknown provenance: this row does not record which source judged it, so its "
                      "disagreement can neither fail the contract nor be dismissed — re-verify to "
                      "record the source (no source is inferred for past rows)")
            return {**base, "verdict": V_NEEDS_FINAL_RECHECK, "reason": detail}
        return {**base, "verdict": V_PENDING_REFERENCE,
                "reason": "reference is still inside its settle window — provisional, not a failure"}

    return {**base, "verdict": V_FAIL_FINAL_MISMATCH, "reason": f"unknown verifier status {status!r}"}


def needs_final_recheck(verdict: dict) -> bool:
    return verdict.get("verdict") == V_NEEDS_FINAL_RECHECK


def wants_recheck(verdict: dict) -> bool:
    """Should the caller try to produce a final verdict (re-verify) before judging?

    Two cases: a provisional verdict whose reference has settled, and a stale ARBITER (no final
    verdict within the age window) — the latter must attempt a refresh, and still fails if the
    refresh cannot produce one.
    """
    return verdict.get("verdict") in (V_NEEDS_FINAL_RECHECK, V_FAIL_REFERENCE_STALE)


def last_finalizable_day(rows, *, now: datetime) -> dict | None:
    """Newest trade_date whose verdict is final — the 'independently finalizable' reference day."""
    out = None
    for r in rows or []:
        td = str(_row_value(r, "trade_date") or "")[:10]
        if not td:
            continue
        if row_is_final(_row_value(r, "reference_kind"), td, _row_value(r, "checked_at"), now):
            if out is None or td > out:
                out = td
    return {"trade_date": out} if out else None
