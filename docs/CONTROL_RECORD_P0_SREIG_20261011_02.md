# Immutable Control Record — P0-SREIG-20261011-02

**State:** `implementing`  
**Issued:** 2026-10-11 KST.  
**Supersedes:** P0-SREIG-20261011-01 (`review`: contaminated worktree); does not reuse any historical P0 artifact, branch, commit, or untracked file.

## User-valued artifact and single blocker

Deliver a clean, attributable candidate for the P0 strategy-research fail-closed consumer gate. The only blocker is that no clean candidate exists: the live runtime tree has unrelated dirty files and cannot prove the origin or scope of prior P0 code.

## Isolation boundary and allowed commands

All implementation and test activity must occur in a new detached Git worktree based **exactly** on `d08d692ad72665bdd26adcebce90740bb7241cc2`, at:

`/Users/brainlee/.hermes/profiles/planner/cache/scratch/p0-sreig-20261011-02`

Allowed setup commands only:

```bash
git -C /Volumes/Realtek_NVME/stock_dashboard/runtime worktree add --detach /Users/brainlee/.hermes/profiles/planner/cache/scratch/p0-sreig-20261011-02 d08d692ad72665bdd26adcebce90740bb7241cc2
git -C /Users/brainlee/.hermes/profiles/planner/cache/scratch/p0-sreig-20261011-02 status --short
git -C /Users/brainlee/.hermes/profiles/planner/cache/scratch/p0-sreig-20261011-02 rev-parse HEAD
```

The initial status must be empty and HEAD must exactly equal the fixed base. Any mismatch is `review` and no implementation may begin.

## Allowed paths

Only these files in that detached worktree may change:

- `strategy_research_guard.py`
- `routes/backtest.py`
- `frontend/src/views/StrategyHub.jsx`
- `frontend/src/views/strategyResearchGate.js`
- `frontend/src/views/strategyResearchGate.test.js`
- `tests/test_strategy_research_guard.py`

All requirements, forbidden effects, and acceptance behavior in P0-SREIG-20261011-01 §§User-valued artifact, Required behavior, and Forbidden side effects apply verbatim. Specifically: no commit/push/build/restart/API call/live DB access/DDL/DML/collection/deployment or static-summary modification.

## Frozen verification commands

Run only from the isolated worktree, after confirming no live datastore connection is possible:

```bash
env -u PYTHONPATH -u PYTHONHOME /Volumes/Realtek_NVME/stock_dashboard/runtime/venv/bin/python -m pytest -q tests/test_strategy_research_guard.py
cd frontend && npm test -- --run src/views/strategyResearchGate.test.js
git diff --check -- strategy_research_guard.py routes/backtest.py frontend/src/views/StrategyHub.jsx frontend/src/views/strategyResearchGate.js frontend/src/views/strategyResearchGate.test.js tests/test_strategy_research_guard.py
git status --short
```

The UI test may use only local fixture/mocked data. The Python test must use only injected fixtures/temporary data and must prove it made no DB connection.

## Acceptance evidence

Return raw outputs for initial base/status, changed-path status, diff check, both frozen tests, exact diff, and proof that no other path changed. A scope change, non-empty initial status, test environment correction beyond the frozen command, or failed check sets this record to `review`. Independent verification—not this evidence—remains required before Planner approval; live activation remains separately user-authorized only.
