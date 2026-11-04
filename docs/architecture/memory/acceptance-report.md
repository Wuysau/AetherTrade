# Strategy Memory Acceptance Report

Version: 1.0 · 2026-11-04
Baseline: `b15a825` (`public-snapshot`)
Implementation head at report time: see `git log -1` (MEM-00..MEM-15 commit plus this docs commit).
Environment: Windows, Python 3.14.2, SQLite unit/light tests only; no Docker, no PostgreSQL,
no TESTNET/LIVE, no paid model calls.

## 1. Work-package status

| Work package | Status | Evidence / limitation |
| --- | --- | --- |
| MEM-00 baseline | PASS | `current-state.md`, `existing-capability-map.json`, `gap-and-conflict-report.md`; baseline 121 passed, 7 skipped |
| MEM-01 contracts | PASS | `tests/unit/test_memory_contracts.py` (17) |
| MEM-02 persistence/migration | PASS (SQLite) / BLOCKED (PG execution) | `memory/repository.py`, `migrations/0032_strategy_memory.sql`; PG migration not executed |
| MEM-03 events/reducer/CAS | PASS | `tests/unit/test_memory_repository.py` (9) |
| MEM-04 decision history/checkpoint | PASS | `tests/unit/test_memory_context.py`, `tests/server/test_memory_integration.py` |
| MEM-05 execution facts | PASS (aggregate refs) | `memory/execution_facts.py`; individual fill IDs only if projection exposes them |
| MEM-06 memory service | PASS | `tests/unit/test_memory_service.py` (6) |
| MEM-07 assembler/validator | PASS | `tests/unit/test_memory_context.py`, acceptance T01-T12 |
| MEM-08 retrieval/token budget | PASS | `memory/retrieval.py`, acceptance T12 |
| MEM-09 prompt framework | PASS | `memory/prompting.py`, acceptance T15/T18 |
| MEM-10 single-model integration | PASS (assessment/automation) | `tests/server/test_memory_integration.py`; legacy wire payload intentionally unchanged |
| MEM-11 cross-model integration | PASS (shared checkpoint) | `model_versions`/one checkpoint; per-model component results retained |
| MEM-12 review scheduling | PASS | `tests/unit/test_memory_governance.py` (trigger idempotency) |
| MEM-13 restart/model switch/recovery | PASS (SQLite) / BLOCKED (PG disaster restore) | `memory/recovery.py`, `tests/unit/test_memory_recovery.py` |
| MEM-14 knowledge governance | PASS | `memory/knowledge.py`, `tests/unit/test_memory_governance.py` |
| MEM-15 observability | PARTIAL | Read-only API + tests; no React/web memory panel added |
| MEM-16 safety/backup/restore | PARTIAL / BLOCKED | Runbook + retention policy delivered; real PostgreSQL restore drill **BLOCKED** |

## 2. Test evidence

### Memory-specific suite

```
.venv/Scripts/python.exe -m pytest \
  tests/unit/test_memory_contracts.py tests/unit/test_memory_repository.py \
  tests/unit/test_memory_context.py tests/unit/test_memory_service.py \
  tests/unit/test_memory_governance.py tests/unit/test_memory_recovery.py \
  tests/unit/test_memory_acceptance.py tests/server/test_memory_integration.py \
  tests/server/test_memory_api.py -q --disable-warnings
```

Result: `65 passed, 1 warning` in 7.81 s (exit 0).

### Full light regression (no Docker/PostgreSQL)

```
.venv/Scripts/python.exe -m pytest tests/contracts tests/intents tests/unit tests/security \
  tests/server tests/api tests/terminal tests/research tests/faults tests/ops \
  -q --disable-warnings -m "not paper_runtime"
```

Result: `3313 passed, 12 skipped, 56 deselected, 1 warning` in 136.31 s (exit 0).

The 56 deselected tests are `paper_runtime` scenarios that require a real isolated
PostgreSQL console and were **NOT_RUN** under the approved no-Docker policy.

## 3. Acceptance scenarios T01-T18

| ID | Scenario | Result | Test evidence |
| --- | --- | --- | --- |
| T01 | Continuous rounds reference prior thesis/plan | PASS | `test_t01_second_round_references_previous_thesis_and_plan` |
| T02 | No new evidence does not force a change | PASS | `test_t02_no_new_evidence_is_deterministic` |
| T03 | Reverse evidence weakens/invalidates thesis | PASS | `test_t03_reverse_evidence_weakens_thesis` |
| T04 | Normal restart restores state/plan | PASS | `test_t04_restart_restores_state_and_plan` |
| T05 | Model/provider switch keeps state | PASS | `test_t05_model_switch_does_not_reset_state` |
| T06 | Multiple strategies same instrument isolated | PASS | `test_t06_multiple_strategies_same_instrument_are_isolated` |
| T07 | Unfilled limit order remains active | PASS | `test_t07_limit_order_unfilled_is_active_and_blocks_new_risk` |
| T08 | Partial fill uses aggregate filled quantity | PASS | `test_t08_partial_fill_keeps_plan_and_reports_aggregate` |
| T09 | Manual reduction overrides old plan | PASS | `test_t09_manual_reduction_overrides_model_plan` |
| T10 | UNKNOWN order blocks new risk | PASS | `test_t10_unknown_order_blocks_new_risk` |
| T11 | Stale market data is not current fact | PASS | `test_t11_stale_snapshot_is_not_current_fact` |
| T12 | Over-budget context is CONTEXT_INCOMPLETE | PASS | `test_t12_over_budget_returns_context_incomplete` |
| T13 | Cross-model shares one checkpoint | PASS | `test_t13_cross_model_uses_one_checkpoint` |
| T14 | Projection rebuild/restore from events | PASS (SQLite projection) | `test_t14_projection_rebuild_repairs_state`; PG restore **BLOCKED** |
| T15 | Old prompt/schema versions remain readable | PASS | `test_t15_old_prompt_version_remains_readable` |
| T16 | Corrupt memory source is rejected | PASS | `test_t16_corrupt_checkpoint_is_rejected` |
| T17 | Duplicate trigger is idempotent | PASS | `test_t17_duplicate_trigger_is_idempotent` |
| T18 | Malicious memory cannot change authority | PASS | `test_t18_malicious_memory_cannot_change_authority` |

## 4. Resource usage

| Item | Value |
| --- | --- |
| D: free before | 284,787,748,864 bytes (~265.2 GiB) |
| D: free after | 284,781,223,936 bytes (~265.2 GiB) |
| Net change | ~6.5 MB (source/tests/docs/pytest temp only) |
| Docker containers/images/volumes created | 0 |
| PostgreSQL databases provisioned | 0 |
| Paid model calls | 0 |
| Heavy test duration | 136.31 s |
| Memory test duration | 7.81 s |

No cleanup, prune, VHDX, WSL, cross-drive migration or project-external file
operation was performed.

## 5. Remaining risks / limitations

1. **PostgreSQL migration and restore are unexecuted.** `0032_strategy_memory.sql`
   is written but has not been applied to a real PostgreSQL instance in this
   environment. Do not mark MEM-02/MEM-16 fully accepted until the approved
   migration + backup/restore drill passes.
2. **Web UI panel is not implemented.** MEM-15 delivers the scope-authorized
   read-only API and tests; the React/web dashboard still needs a memory view.
3. **Legacy model wire payload is unchanged.** The existing exact-key legacy
   contract test is preserved; legacy `generate()` does not receive memory
   sections. Assessment and program paths do. Legacy strategies already require
   explicit reauthorization for execution.
4. **RULE has no plan lifecycle object.** RULE decisions and execution facts are
   recorded, but a rule action does not create a `StrategyPlan`; the execution
   ledger remains authoritative. This is a deliberate scope limitation, not a
   second ledger.
5. **Fill references are aggregate.** `paper_runtime.projection()` exposes order
   aggregates, not individual fill IDs. `ExecutionFactRef` carries a fill ID only
   when the projection provides one; otherwise it references the order aggregate.
6. **Program `previous_proposal` fallback.** It now reads the active plan's
   preserved `source_proposal` from memory when the schema-locked snapshot lacks
   the field. This was unit-tested through assembler sections; a full program
   HOLD/UPDATE_EXIT PG scenario remains part of the blocked integration suite.
7. **No automatic retention job.** Retention is policy only; no deletion is
   implemented or authorized.

## 6. Rollback

- Application rollback: revert the memory commits. Existing `console_*` and
  `ledger.*` tables are untouched.
- Migration rollback: `0032_strategy_memory.sql` is additive. A rollback script
  must not delete business events; archive the tables first and obtain approval.
  No rollback was executed because the migration was not applied.
- Runtime rollback does not require touching Docker, volumes or VHDX.

## 7. Final principle check

- Memory is continuous: events + state revision + checkpoints.
- Facts are verifiable: execution ledger remains authoritative; UNKNOWN/STALE is
  explicit.
- Plans are traceable: PROPOSED → ACCEPTED → ACTIVE → COMPLETED/REJECTED/EXPIRED,
  with source and revision.
- Execution is independently controlled: model output cannot accept/activate a
  plan, alter risk/authorization, or write orders.
