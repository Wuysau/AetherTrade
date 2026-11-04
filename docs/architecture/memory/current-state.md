# MEM-00 — Baseline and Current-State Inventory

Status: PASS (inventory), no product code changed by this work package.
Recorded: 2026-11-04
Repository: `D:/Projects/Python/AetherTrade/AetherTrade`
Branch: `public-snapshot`
HEAD: `b15a825` (`docs: record targeted fix rerun on fixed DEV and WSL`)
Worktree: primary checkout, clean except `docs/architecture/memory/AGENT_WORK_PACKAGES.md` (untracked at start).
Baseline test command:

```
.venv/Scripts/python.exe -m pytest tests/security/test_strategy_sandbox.py tests/server/test_decision_assessment.py tests/server/test_program_model_context.py -q
```

Result: `121 passed, 7 skipped` in 5.55 s (exit 0).

Host/resource observation: D: free ≈ 265 GiB at start; no Docker daemon access was used
(`docker system df` exited non-zero). No container, image, volume, VPS or database was created.

## 1. What already exists

### 1.1 Market/analysis evidence

`src/trading_system/server/analysis_snapshot.py`

- `build_analysis_context()` builds a bounded, hashed `analysis-snapshot-v1` containing
  quote, closed candles/features, optional book, PAPER account/position, constraints,
  costs and quality sections. `MAX_BYTES = 96000`; exact nested allowlist in
  `validate_snapshot()`.
- It already pulls `active_orders`, `reserved_funds` and `protection_status` from
  `store.execution.analysis_evidence()` (`analysis_snapshot.py:199-206`).
- It hard-codes `previous_assessment=None` (`analysis_snapshot.py:196`) and the schema
  rejects any other top-level/portfolio field (`previous_assessment={'type':'null'}` at
  line 244). There is no cross-round thesis/plan injection.
- Tests: `tests/server/test_analysis_snapshot.py`, `tests/server/test_decision_assessment.py`,
  `tests/unit/test_snapshot_provenance.py`.

### 1.2 Model request/decision journal

`src/trading_system/server/model_service.py`, `model_repository.py`, `model_client.py`

- `console_model_runs` (SQLAlchemy JSON table) stores a durable run receipt per idempotency
  key: fingerprint, status `IN_FLIGHT/SUCCEEDED/FAILED`, exact `requests[]` bodies,
  input hashes, snapshot, component results, prompt revision and final decision.
  `model_repository.py:63-70`.
- `_assess()` persists the exact provider request body before network I/O
  (`model_service.py:486-491`), parsed provider content only (no hidden reasoning),
  component failures, review/adjudication history, and a final decision with
  `snapshot_id`, `snapshot_hash`, `strategy_revision`, `prompt_revision`,
  `decision_policy`.
- `generate()` (legacy) persists `console_model_runs` too (`model_service.py:279-316`).
- `ModelService.decisions()` exposes the last 200 successful non-component decisions.
- Tests: `tests/server/test_model_api.py`, `test_decision_assessment.py`,
  `test_model_consensus.py`, `test_multi_model_api.py`, `test_model_output_limit.py`.

### 1.3 Cross-model / review behaviour

`src/trading_system/server/model_service.py:318-620`

- Single and consensus modes share one cleaned context and one snapshot hash.
- Consensus stores each model's raw parsed output separately in `component_results`.
- Evidence-review policy stores `review_report` and `adjudication_history`.
- `AutomationService._assessment_guard()` re-checks participant set, revisions, snapshot
  hash, policy and review status before execution (`automation.py:333-368`).
- Tests: `tests/server/test_model_consensus.py`, `tests/server/test_multi_model_api.py`,
  `tests/server/test_multi_routing.py`.

### 1.4 Durable automation and restart recovery

`src/trading_system/server/automation.py`

- `console_ai_schedules` and `console_ai_jobs` are durable JSON tables.
- `_claim()` recovers expired `RUNNING` jobs as `SKIPPED` without replaying a model call
  or executing old advice (`automation.py:163-170`); `_attach_late_result()` keeps late
  provider output associated with the terminal job (`automation.py:121-133`).
- `_complete()` persists watch conditions, baseline, last assessment and next review time
  into the schedule (`automation.py:135-157`).
- `_authorized()` re-checks strategy revision, activation, model profiles and lease before
  dispatch (`automation.py:374-382`).
- Tests: `tests/server/test_automation.py` (23 tests), including
  `test_expired_job_recovered_without_resend_or_duplicate_notification`,
  `test_late_model_completion_after_recovery_remains_annotated`,
  `test_cross_store_claim_and_restart_receipt`.

### 1.5 Watch/review triggering

`src/trading_system/server/decision_execution.py:18-30` and
`automation.py:182-188`, `458-488`.

- `watch_triggered()` supports `NEW_BAR`, `PRICE_ABOVE`, `PRICE_BELOW`.
- Automation wakes NEW_BAR watches, persists `next_analysis_at`, and avoids model calls
  when the bar/quote is unchanged or missing.
- Tests: `tests/server/test_automation.py`, `tests/unit/test_decision_scheduling.py`.

### 1.6 Execution facts and manual intervention

`src/trading_system/server/paper_runtime.py`, `store.py`, `decision_execution.py`

- The unified PostgreSQL PAPER runtime is the authoritative order/fill/position ledger
  (`ledger.commands`, `ledger.fills`, `ledger.positions`, `ledger.order_projections`).
- `analysis_evidence()` projects active orders, reserved USDT and protection status into
  the analysis snapshot (`paper_runtime.py:474-487`).
- `RuntimeRequest`/command rows carry `decision_run_id` and `strategy_id`
  (`automation.py:278-280`, `paper_runtime.py:489+`).
- Manual reduction is detected from ledger audit events and blocks stale strategy targets
  (`paper_runtime.py:543-547`).
- Tests: `tests/integration/test_program_*`, `test_paper_*`, `test_protection_*` (require
  PostgreSQL and were NOT_RUN in this work package).

### 1.7 Existing generic event/audit primitives

- `console_events` (`store.py:43`) is a generic console activity log, not a typed strategy
  memory event log.
- `ledger.audit_events` / `ledger.domain_events` / `ledger.outbox` are the execution
  ledger's append-only history (`migrations/0001_business_ledger.sql`).
- `decision/memory.py` is an explicit **process-local reference implementation** of a
  text memory index (`MemoryStore`), with no production wiring. Its own docstring says:
  “Process-local reference implementation. Production persistence and authenticated
  evidence resolution are explicit integration dependencies, not durability claims.”
- `tests/security/test_strategy_sandbox.py:363-445` tests that reference implementation.

### 1.8 Offline decision orchestrator (not the live console path)

`src/trading_system/decision/orchestrator.py`, `scheduler.py`, `providers.py`

- A separate, process-local offline research orchestrator with an append-only SQLite
  `DecisionAudit`. It is used by security/qualification tests, not by
  `server/app.py` or `AutomationService`.
- `DecisionScheduler` explicitly says restart loses research state.
- Tests: `tests/security/support_ip_19.py`, `tests/security/test_strategy_sandbox.py`.

## 2. What does not exist

| Capability | Status | Evidence |
| --- | --- | --- |
| Persistent per-strategy state (revision, thesis, plan) | NOT_IMPLEMENTED | `console_strategies` is a single mutable JSON row; no history/state table |
| Append-only strategy memory events | NOT_IMPLEMENTED | `decision/memory.py` is process-local; `console_events` is untyped UI activity |
| State reducer / CAS revision | NOT_IMPLEMENTED | no reducer or `state_revision` anywhere |
| Thesis lifecycle (ACTIVE/WEAKENED/INVALIDATED/EXPIRED/SUPERSEDED) | NOT_IMPLEMENTED | no type/table |
| Plan lifecycle (PROPOSED/ACCEPTED/ACTIVE/REVISED/CANCEL_PENDING/COMPLETED/EXPIRED/REJECTED) | NOT_IMPLEMENTED | proposal exists per run only |
| Decision context checkpoint (actual input + versions + memory revision) | PARTIAL | run record stores snapshot/requests/input hash, but no memory revision, no checkpoint identity, no validator |
| Cross-round `previous_assessment` / prior plan injection | NOT_IMPLEMENTED | `analysis_snapshot.py:196` hard-codes `None`; schema rejects it |
| Unified Context Assembler for single/consensus/RULE/HYBRID/automation | NOT_IMPLEMENTED | each entry builds context independently; RULE never builds a memory context |
| Token-budgeted relevant history retrieval | NOT_IMPLEMENTED | no retrieval layer |
| Versioned P0–P6 prompt framework | PARTIAL | `model_client.INSTRUCTIONS`/`ASSESSMENT_INSTRUCTIONS` are constants; `PROMPT_REVISION` strings only; no template registry |
| Execution fact projection linked to memory | PARTIAL | `analysis_evidence()` summary only; no stable execution fact refs or linkage events |
| Strategy Memory Service read API | NOT_IMPLEMENTED | no service |
| Restart recovery of strategy memory | NOT_IMPLEMENTED | automation recovers jobs, not strategy state/thesis/plan |
| Model-switch continuity | PARTIAL | model config revisions are re-checked; no strategy memory independent of provider |
| Validated knowledge governance | NOT_IMPLEMENTED | no knowledge type/table |
| Memory observability API/UI | NOT_IMPLEMENTED | dashboard shows decisions/events but not memory/checkpoint |
| PostgreSQL memory schema/migration | NOT_IMPLEMENTED | migrations 0001–0031 contain no strategy memory tables |
| Backup/restore acceptance for memory | NOT_IMPLEMENTED | no runbook/acceptance report |

## 3. Real integration paths observed

### Single/consensus model path

`POST /api/decisions/analyze` → `app.decision_context()` → `build_analysis_context()` →
`ModelService.analyze()` → `_assess()` or `generate()` → `console_model_runs`.

### Automation path

`AutomationService.run()` → `tick()` → `_claim()` (durable job) →
`build_analysis_context()` → `models.analyze()`/`models.generate()` → `_finish()` →
`_execute()`/`_execute_assessment()`/`_execute_program_decision()` → unified PAPER
runtime (`store.execution.accept_proposal`/`accept`) → `_complete()` + schedule update.

### RULE/HYBRID path

RULE in `automation.tick()` computes BUY/HOLD from `_rule_matches()` without a model.
HYBRID currently follows the AI branch and then applies `_rule_matches()` only in the
legacy `_execute()` path. Neither reads a persisted strategy state or prior plan.

## 4. Notable conflicts / risks found

1. **Program `previous_proposal` is unreachable.** `program_model_context.parse_program_assessment()`
   requires `snapshot['portfolio']['previous_proposal']` for `HOLD`/`UPDATE_EXIT`
   (`program_model_context.py:172-177`), but `analysis_snapshot.validate_snapshot()`
   forbids that key (`portfolio` is `additionalProperties=False`, schema at
   `analysis_snapshot.py:240-242`). Therefore program HOLD/UPDATE_EXIT cannot validate
   with a snapshot produced by the current assembler. This must be fixed by the memory
   context (a separate, explicit `previous_plan`/`previous_proposal` field), not by
   weakening snapshot validation.
2. **Two decision journals.** The console uses `console_model_runs`; the execution ledger
   schema already has `ledger.decision_runs`/`ledger.proposals`. The work package says to
   extend existing decision records, not create a third. The implementation will keep
   `console_model_runs` as the model-run journal and add memory references/checkpoints
   alongside it; it will not write a second order/fill ledger.
3. **`analysis_snapshot` cannot carry memory.** Its schema is an exact allowlist. Memory
   must travel as a separate validated envelope (`strategy_memory`) outside the snapshot
   hash, so old snapshots remain byte-identical and auditable.
4. **Prompt revisions are constants, not stored templates.** `PROMPT_REVISION` exists but
   no versioned template registry/table. MEM-09 will add an immutable registry and keep
   old versions readable.
5. **`decision/memory.py` is not production memory.** It must not be mistaken for the
   durable strategy memory. It remains a separate reference implementation for the
   offline sandbox; the new subsystem is `trading_system.memory`.

## 5. Baseline conclusion

There is no persistent strategy memory, no reducer, no context checkpoint validator, no
unified assembler and no memory-aware execution-fact linkage. Existing model-run
journalling, snapshot hashing, automation recovery and execution-ledger facts are solid
foundations and will be reused rather than duplicated. No architectural conflict requires
stopping; the `previous_proposal` schema conflict is resolved by adding a separate
memory/previous-plan envelope, not by modifying the approved snapshot contract.
