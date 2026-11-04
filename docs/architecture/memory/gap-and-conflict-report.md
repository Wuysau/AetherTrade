# MEM-00 — Gap and Conflict Report

Recorded: 2026-11-04 · HEAD `b15a825` · no product code changed.

## Gap summary

| Work package | Current state | Required delta |
| --- | --- | --- |
| MEM-01 | No strategy-memory domain types | Add versioned, provider-neutral scope/state/thesis/plan/event/checkpoint/knowledge contracts with strict validation |
| MEM-02 | No memory tables/migration | Add SQLAlchemy tables registered on console metadata + PostgreSQL migration `0032_strategy_memory.sql` |
| MEM-03 | No reducer/CAS/rebuild | Add append-only events, idempotency, state projection, CAS and deterministic rebuild |
| MEM-04 | Partial run journal | Add checkpoint identity, memory revision, prompt/schema/policy versions, input hash and exact input references; keep `console_model_runs` |
| MEM-05 | Execution evidence summary only | Add stable execution-fact refs linked to the existing ledger; no second order/fill table |
| MEM-06 | No read service | Add `StrategyMemoryService` with scope-filtered reads and controlled writes |
| MEM-07 | No assembler/validator | Add unified `DecisionContextAssembler` and `ContextValidator` |
| MEM-08 | No retrieval/budget | Add bounded relevance retrieval and `CONTEXT_INCOMPLETE` |
| MEM-09 | Prompt constants only | Add P0–P6 versioned registry and memory-use protocol |
| MEM-10 | Per-run context only | Wire assembler/checkpoint/memory into single-model manual and automation paths |
| MEM-11 | Shared snapshot for models | Share one checkpoint across all models and keep per-model raw opinions |
| MEM-12 | Watch conditions exist | Add persisted event cursor, deduplication and change-triggered review |
| MEM-13 | Job recovery only | Add strategy-state rebuild and interrupted-run recovery without replay |
| MEM-14 | No knowledge | Add PROPOSED/VALIDATED/REJECTED/EXPIRED governance with evidence and sample rules |
| MEM-15 | No memory UI/API | Add read-only scope-authorized memory/checkpoint endpoints |
| MEM-16 | No memory acceptance | Add runbook, retention policy and acceptance report; PostgreSQL restore drill is BLOCKED without approved DB access |

## Conflicts and resolutions

### C1. Program `previous_proposal` cannot appear in `analysis_snapshot`

- `program_model_context.parse_program_assessment()` requires
  `snapshot['portfolio']['previous_proposal']` for `HOLD`/`UPDATE_EXIT`
  (`program_model_context.py:172-177`).
- `analysis_snapshot.validate_snapshot()` uses `additionalProperties=False` for
  `portfolio` and does not list `previous_proposal` (`analysis_snapshot.py:240-242`).
- Resolution: do **not** weaken the approved snapshot contract. Add a separate
  `strategy_memory.previous_plan` / `previous_proposal_ref` envelope and let program
  validation read the prior plan from memory when the snapshot field is absent. Existing
  snapshots remain byte-identical.

### C2. Two decision journals

- Console decisions live in `console_model_runs`; the execution ledger schema already
  defines `ledger.decision_runs` and `ledger.proposals`.
- Resolution: keep `console_model_runs` as the model-run journal. Add
  `console_decision_checkpoints` and memory event references. Do not write a third
  decision table and do not duplicate order/fill facts.

### C3. Memory must not change the snapshot hash

- The snapshot schema is an exact allowlist and the hash covers all snapshot content.
- Resolution: carry memory in a separate, independently hashed
  `strategy_memory` envelope outside `analysis_snapshot`. Snapshot IDs/hashes remain
  stable for audit.

### C4. Process-local reference memory

- `decision/memory.py` is explicitly process-local and unwired.
- Resolution: leave it untouched for the offline sandbox. Implement production memory in
  `trading_system.memory` with PostgreSQL/SQLAlchemy persistence and tests.

### C5. Provider conversation IDs are not strategy identity

- Current model profiles and run keys are provider-scoped.
- Resolution: memory scope is `tenant/user/account/product/instrument/strategy_id`;
  provider/profile/model are recorded as versions only, never as the primary key.

### C6. Model proposals must not become ACTIVE plans

- Current automation accepts proposals through risk/execution, but there is no plan state.
- Resolution: model output only creates `PROPOSED` plans. `ACCEPTED`/`ACTIVE` requires a
  control/execution event source. `COMPLETED` requires an execution-ledger or manual
  event. Model-sourced events cannot write authorization/risk/owner fields.

### C7. Heavy PostgreSQL tests are not permitted in this environment

- `AGENTS.md` forbids Agent Docker startup, container/database provisioning and heavy
  integration runs until the unified scheduler is available.
- Resolution: implement and run SQLite unit/contract/integration-light tests. Write the
  PostgreSQL migration and PG-backed repository code, but record PostgreSQL migration
  and restore tests as `BLOCKED` (no approved DB/Docker access), not PASS.

## MEM-01 implementation plan

1. Add `src/trading_system/memory/contracts.py` with:
   - `StrategyMemoryScope`, `StrategyIdentity`, `StrategyState`, `StrategyMemoryEvent`,
     `MarketThesis`, `StrategyPlan`, `DecisionHistoryRef`, `ExecutionFactRef`,
     `ValidatedKnowledge`, `DecisionContext`, `DecisionContextCheckpoint`.
   - StrEnums for thesis/plan/event/knowledge/completeness status.
   - Strict validation: full scope, schema revisions, source references, occurred/recorded/
     valid times, Decimal-safe serialization, model-unwritable reserved fields.
2. Add `reducer.py` with legal thesis/plan transitions and deterministic state hash.
3. Add tests first:
   - cross-account/strategy/product isolation;
   - unknown schema revision rejection;
   - illegal transition rejection;
   - missing source rejection;
   - Decimal/time/ID round-trip;
   - model output cannot overwrite authorization/risk fields.
4. Only after MEM-01 tests pass, add MEM-02 persistence and MEM-03 append/CAS/rebuild.

No blocking architecture conflict. Proceeding in dependency order.
