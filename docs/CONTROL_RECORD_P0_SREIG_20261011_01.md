# Immutable Control Record — P0-SREIG-20261011-01

**State:** `review`  
**Issued:** 2026-10-11 KST, under the active-room user direction to proceed and `shared-directive.md` items 14–20.  
**Supersedes:** none. The historical P0 files/branches remain `review/rejected` and are not evidence or an activation candidate.

## State transition — 2026-10-11 20:22 KST

**Verdict: `review`; not eligible for independent validation or Planner approval.** The attempted implementation occurred in a contaminated runtime working tree that already contained the historical rejected P0 files and an out-of-scope modified `research_outputs/strategy_research_summary.json`. At read-back, the six P0 paths were dirty and the forbidden static summary was also modified. Therefore an exact fresh-candidate diff cannot be attributed or proved from this tree, and the task's clean-scope premise failed.

The implementer reported fixture-only tests passing after clearing inherited Hermes `PYTHONPATH`/`PYTHONHOME`; this is evidence of a test command environment defect, not approval evidence. No production build, restart, API call, DB write, collection, commit, or deployment occurred.

**Next bounded blocker-removing task:** P0-SREIG-20261011-02 must create the candidate in a new detached worktree at fixed base `d08d692ad72665bdd26adcebce90740bb7241cc2`, with no pre-existing P0 artifact. Owner: Implementer. Artifact: path-scoped diff and fixture-only test evidence from that isolated worktree. Blocker: until this clean candidate exists, there is nothing eligible for independent validation. Re-verification: `git -C <isolated-worktree> status --short`, `git -C <isolated-worktree> diff --check -- <allowed paths>`, and the frozen Python/UI fixture commands.

## User-valued artifact

The served `GET /api/backtest/strategy-research/summary` and its Strategy Hub consumer must fail closed: if the static strategy-research summary cannot be proven against live canonical `strategy_feature_snapshot`, `backtest_run_specs`, and point-in-time (PIT) availability, no ranking, return, score, or quality-derived result can appear as a strategy-selection or promotion basis. The response instead exposes `strategy_research_status="not_usable"`, machine-readable reasons, provenance, and evidence.

## Single current blocker

A newly bounded, clean implementation candidate is required. Existing P0 artifacts have been rejected and cannot be repaired, activated, committed, or used as proof. This task removes only that blocker; it does not authorize live activation.

## Allowed implementation paths

- `strategy_research_guard.py`
- `routes/backtest.py`
- `frontend/src/views/StrategyHub.jsx`
- `frontend/src/views/strategyResearchGate.js`
- `frontend/src/views/strategyResearchGate.test.js`
- `tests/test_strategy_research_guard.py`

No other path may be modified. If a required change lies outside this list, stop and return `review` with the exact path and reason.

## Required behavior

1. Canonical source is exactly `strategy_feature_snapshot`; sibling/stage/legacy/rebuild tables are non-canonical.
2. For each response, read only the static summary and live primary PostgreSQL metadata/data necessary to compare: summary hash/path/generation time; canonical table existence, row count, date extent, stock count and declared metrics; referenced `backtest_run_specs` hash; and PIT maturity.
3. Any missing, unreadable, stale, mismatched, non-canonical, unlinked run-spec, absent/naive timestamp, or PIT-unprovable condition yields `not_usable`.
4. In `not_usable`, omit—not null—every ranking/return/score/quality-derived payload section added either by the static summary or route assembly. Keep only status, reason codes, evidence/provenance, and a non-promotional notice.
5. A proven usable control fixture preserves the legitimate sections.
6. The React consumer must render an explicit non-usable state and no zero/placeholder return, ranking, score, or quality panel when the API says `not_usable`, is malformed, or has no status.

## Forbidden side effects

- No commit, push, merge, build, restart, deployment, service/API invocation, DB write/DDL/migration, collection, external publication, order, parameter/weight change, or modification of `research_outputs/strategy_research_summary.json`.
- Tests must use fixtures/temporary databases only. No test may contact the live primary datastore.
- Do not execute the historical verification script or reuse historical P0 branches/commits as evidence.

## Acceptance evidence

The Implementer must provide, with raw output:

1. `git diff --check -- <allowed paths>` exits 0 and the changed-path list contains only the allowed paths.
2. `./venv/bin/python -m pytest -q tests/test_strategy_research_guard.py` passes, including negative cases for each condition in Required behavior 3 and a usable control.
3. `cd frontend && npm test -- --run src/views/strategyResearchGate.test.js` passes (or an existing project-specific equivalent, recorded verbatim if the command is unavailable before execution).
4. Static inspection or tests prove the route does not re-add any withheld sections, and UI tests prove no numeric fallback is rendered for `not_usable`/malformed/missing status.
5. Report exact diff, every command/result, and unexpected files. A failed check, changed scope, or factual correction automatically sets this record to `review`.

## Mandatory next gate

After independent validation, Planner may approve the candidate only. The separate live state-changing command set—frontend build, one service restart, then exact API/UI read-back—remains forbidden unless the user explicitly authorizes that frozen command set and expected read-back.
