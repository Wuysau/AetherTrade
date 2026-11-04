# Strategy Memory Recovery Runbook

Version: 1.0 · 2026-11-04
Scope: `console_strategy_memory_events`, `console_strategy_memory_state`,
`console_market_theses`, `console_strategy_plans`,
`console_decision_checkpoints`, `console_memory_knowledge`,
`console_prompt_templates` in the console PostgreSQL database.

The execution ledger (`ledger.commands`, `ledger.fills`, `ledger.positions`,
`ledger.order_projections`, `ledger.protection_plans`) remains the sole authority
for orders, fills, positions, fees and protection. Memory recovery never creates
a second order/fill ledger and never re-sends a trading command.

## 1. Normal startup

1. Apply migration `migrations/0032_strategy_memory.sql` through the approved
   shared-DEV migration process. Do not run it as the runtime owner role unless
   that role is explicitly approved for DDL.
2. Start the console. `Store.initialize()` registers the SQLAlchemy tables and
   `metadata.create_all()` creates any missing tables in SQLite/dev.
3. `MemoryRecoveryService.recover()` runs once in the application lifespan:
   - reads all strategies;
   - rebuilds each non-empty state projection from append-only events;
   - verifies `state_hash` and `state_revision`;
   - reads execution facts and reports UNKNOWN/STALE order IDs;
   - reports `IN_FLIGHT` model runs without replaying them.
4. Inspect `app.state.memory_recovery` (or the `/api/strategies/{id}/memory/health`
   endpoint) for `PASS` / `DEGRADED`.

`DEGRADED` is not a trading failure by itself, but any UNKNOWN execution fact
must block new risk until reconciliation.

## 2. Recovery checks

```sql
-- Event/state revision agreement per scope
SELECT e.scope_id, max(e.sequence) AS last_sequence, s.revision
FROM public.console_strategy_memory_events e
LEFT JOIN public.console_strategy_memory_state s USING (scope_id)
GROUP BY e.scope_id, s.revision;

-- Duplicate/missing idempotency keys (should return no rows)
SELECT scope_id, idempotency_key, count(*)
FROM public.console_strategy_memory_events
GROUP BY scope_id, idempotency_key HAVING count(*) > 1;

-- Active plans and theses
SELECT scope_id, plan_id, status, revision, updated_at
FROM public.console_strategy_plans WHERE status = 'ACTIVE';
SELECT scope_id, thesis_id, status, revision, updated_at
FROM public.console_market_theses WHERE status IN ('ACTIVE','WEAKENED');

-- Checkpoint completeness and blocking
SELECT checkpoint_id, run_id, completeness, created_at
FROM public.console_decision_checkpoints
ORDER BY created_at DESC LIMIT 20;
```

Rebuild a projection from events (application code, never SQL UPDATE):

```python
from trading_system.memory.repository import MemoryRepository
from trading_system.memory.assembler import scope_for_strategy

repo = MemoryRepository(store)
report = repo.rebuild_projection(scope_for_strategy(strategy))
print(report.verified, report.reason, report.state.state_hash)
```

If `report.reason == "PROJECTION_REPAIRED"`, the event log was intact and the
projection was rewritten. If event hashes fail, stop and restore a verified
backup; do not edit events.

## 3. Backup and restore drill

Use the existing approved PostgreSQL backup tooling. This runbook does not
authorize Docker, VHDX, volume or cross-drive operations.

1. Take a full backup of the console database.
2. Restore into a fresh isolated database (approved test database only).
3. Run the SQL checks above.
4. Run `MemoryRecoveryService.recover()` against the restored database.
5. Verify:
   - `state_hash` and `state_revision` match the event projection;
   - no duplicate `(scope_id, idempotency_key)`;
   - active plans/theses match the pre-backup snapshot;
   - execution facts still resolve to the authoritative ledger;
   - no order/fill row was created by memory recovery.
6. Record the backup ID, restore database, command, exit code, event count and
   state hash in `docs/architecture/memory/acceptance-report.md`.

**Status in this change: BLOCKED.** No approved PostgreSQL/Docker access was
available; only SQLite unit evidence was run. A process restart is not a
disaster-recovery pass.

## 4. UNKNOWN order / partial fill

1. Do not release reserved budget and do not resend.
2. Query the execution ledger / venue for the command/order.
3. Reconcile through the existing `paper_runtime` reconcile path.
4. Append a `MANUAL_INTERVENTION` or `EXECUTION_FACT_LINKED` event with the
   observed source ID; never edit the order/fill ledger from memory code.
5. Rebuild the context; `block_new_risk` clears only when the UNKNOWN/STALE fact
   is gone or explicitly reconciled.

## 5. Model switch

1. Memory scope is independent of provider/profile.
2. Record `MODEL_SWITCHED` with the run ID.
3. Keep the same `StrategyState`, thesis and plan; only model versions in the
   next checkpoint change.
4. Never use a provider conversation ID as a strategy key.

## 6. Incident: memory source corrupt or missing

- Missing checkpoint: mark `memory_checkpoint_missing`; do not treat the
  decision as a valid memory-backed plan.
- Hash mismatch: `CONTEXT_INCOMPLETE`; block new risk.
- Missing thesis/plan with open exposure: block new risk and reconcile.
- Corrupt event log: restore backup; do not delete or rewrite events.
- Prompt/schema version mismatch: keep the old checkpoint read-only; create a
  new checkpoint with the new version.

## 7. Resource and host rules

- No Agent Docker startup, container/database provisioning, prune, volume
  deletion, VHDX operation or cross-drive migration.
- Heavy PostgreSQL restore drills require the unified scheduler and explicit
  approval.
- Preserve evidence under `docs/evidence/memory/` with a size cap; do not keep
  runner containers or duplicate dependency downloads.
