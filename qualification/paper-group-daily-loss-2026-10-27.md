# A.3/A.4 — shared persistent daily-loss applicability

Implementation: `9f3fc42bd2515b0cbf55b4472f1c5f9dc7612c02`.
Dependency: approved PostgreSQL resolver commit `788d808` (locally cherry-picked
as `b795e01`); integrate that dependency once, not twice. This followup changes
only RiskLockRepository within `storage/uow.py`, the shared risk-service/verifier
read path, tests and the qualification launcher. It does not change command
repository code, migrations, native qualification or live/testnet permission.

## Reproduction

On the dependency baseline, a real isolated Linux/PostgreSQL run returned
**exit 1, 2 failed, 2 passed**. `RiskLockService.active(ETH)` returned empty and
`allowed_actions(ETH)` still contained NEW_RISK after a BTC DAILY_LOSS lock was
persisted on the same bound risk account/collateral group. The existing exact
instrument/leg lookup hid the lock from the other instrument. An initial test
fixture correction bound ETH's session metadata to ETH before this final red run.

Raw output and exit status:
[regression](evidence/group-daily-loss-2026-10-27/01-regression.txt),
[exit](evidence/group-daily-loss-2026-10-27/01-regression.exit.txt),
[database metadata](evidence/group-daily-loss-2026-10-27/01-regression.json).

## Trust and applicability

`RiskLockRepository.applicable(scope)` returns original lock records. It does
not copy BTC locks into ETH or accept caller lock IDs, a payload group label,
or a caller boolean as evidence of applicability.

The read revalidates the current account/product's `account_bindings` row against
the transaction group and reacquires that group's `collateral_mutex FOR UPDATE`.
For local locks and historical DAILY_LOSS identities it checks stored scope hash,
record ID, typed scope, immutable identity/history and the source account/product
binding. Historical daily-loss identities remain candidates if a later malformed
row attempts to change their cause. Missing/ambiguous bindings, corrupted history,
duplicate identities and weaker daily-loss policy data fail closed.

All active local locks apply. An original DAILY_LOSS lock additionally applies
across instruments/accounts/products only when its source binding proves the
same environment and authoritative collateral group. That existing durable
group mapping defines this risk domain; neither a model nor instrument-name
similarity defines it. Runtime roles cannot mutate the binding rows. Other causes,
including PROTECTION_GAP and TOTAL_DRAWDOWN, retain their original scope in this
change. A valid lock in another collateral group does not affect this group.

RiskLockService active/allowed-actions and resolution status use this same view.
Reservation entry and both dispatcher checks use it through
LockedActionVerifier. Verified reduction and bounded native protection repair
remain subject to quantity, ownership, authorization, proof freshness and the
intersection of all permissions. A local EXIT_ONLY still denies protection
repair. Partial protection remains GAP until a separate full-coverage verifier
establishes protection; this permission read never labels it VERIFIED_NATIVE.

Resolution is still scoped to the original BTC lock. An ETH alias request cannot
resolve it. Migration 0002 requires the separately provisioned resolver identity;
the execution-owner role cannot call the definer resolution function or mint
private approvals. The new test additionally stores its synthetic review record
in an immutable resolver-private archive: the owner cannot forge that record.
The trusted verifier reads the stored review, original lock, authorization and
evidence before source resolution. A source resolution produces one sequence-2
record on BTC; there are no ETH lock aliases to resolve independently.

## Verification on the fixed commit

All commands run from this worktree. The launcher reads the explicitly authorized
sibling `paper-execution/.runtime/paper-postgres.json` and environment file,
creates a fresh `ip08_test_group_*` database, and copies this checkout's sources
to a separate temporary directory in the existing Linux Python runner. It does
not write to the runner's mounted project or existing qualification database.
Environment probes report **Linux, Python 3.12.16, PostgreSQL 17.11**.

```powershell
& ./scripts/run_group_daily_probes.ps1 -Label committed-group -TestPaths tests/integration/test_group_daily_locks.py,tests/unit/test_locked_actions.py,tests/unit/test_risk_applicability.py
# Exit 0: 143 passed, 0 skipped (5.54s)
# Fresh database: ip08_test_group_b527258243b8
& ./scripts/run_group_daily_probes.ps1 -Label committed-persistent -TestPaths tests/integration/test_persistent_risk_locks.py
# Exit 0: 36 passed, 0 skipped (2.65s)
# Fresh database: ip08_test_group_8a0cb1ed157f
```

Each recorded run identifies the exact implementation commit and SHA-256 of the
applicable source/test/migration files:

- [Group and safety output](evidence/group-daily-loss-2026-10-27/02-group-and-safety.txt),
  [exit](evidence/group-daily-loss-2026-10-27/02-group-and-safety.exit.txt),
  [commit/environment/source hashes](evidence/group-daily-loss-2026-10-27/02-group-and-safety.json).
- [Persistent-lock/resolver output](evidence/group-daily-loss-2026-10-27/03-persistent-and-resolver.txt),
  [exit](evidence/group-daily-loss-2026-10-27/03-persistent-and-resolver.exit.txt),
  [commit/environment/source hashes](evidence/group-daily-loss-2026-10-27/03-persistent-and-resolver.json).

The ten new real PostgreSQL cases cover same-account BTC/ETH, another bound
account/product, another group, verified reduction and partial repair, local lock
intersection, original-source resolution, rejected owner forgery, and malformed
local records with no command/reservation bundle. The concurrency probe observes
the source writer waiting on the actual group lock through `pg_stat_activity`.
Fourteen new unit cases cover missing/ambiguous bindings, broken source identity,
changed history, duplicate identities, weakened policy and forged position aliases.

An adjacent Windows compatibility run also returned **exit 0, 523 passed,
133 skipped**. Its skips are NOT_RUN, not database qualification. The explicit
Linux runs above establish the group-lock/resolver assertions without skips.

## Limits

The review and account/order facts in these tests are synthetic PAPER fixtures
persisted in real PostgreSQL, not real human approval or venue evidence. The
default production action-proof reader and resolver composition remain disabled
until separately configured with trusted local evidence and separate credentials.
An unreadable/orphaned historical DAILY_LOSS candidate fails closed rather than
being silently discarded, so ledger corruption can require operator repair.
This followup does not establish the complete unified PAPER lifecycle, all crash
boundaries, external fencing, native/Testnet qualification or real-money readiness.
No real account or venue API was accessed.
