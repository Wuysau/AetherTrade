# A.4 — observed domain DAILY_LOSS monitor

Implementation: `472fd90fc6711e63512f05b0aeae24e89413d3d4`.
Validated-original read API/design prerequisite:
`19033d60b5eae0c306bd7e3388c2bbbb2cff6e76`.
These build on the already reviewed shared-group applicability and separate
PostgreSQL resolver authority. No command-repository/migration/native code is
changed. This is a domain component, not completion of the console/AI/manual
integration requested by the full engineering task.

## Engineering choices and trust

The approved SPEC defines daily P&L as `E(t)-E(day_start)-F`, in a configured IANA
risk day, but does not specify the loss-fraction denominator. The coordinating
task explicitly approved the fixed, positively evidenced `E(day_start)>0` as an
engineering choice. Current equity, a model output, or an unbound number never
replaces that denominator. The threshold is opening equity times the authorized
persisted daily_loss_fraction, computed with sufficient exact Decimal precision.

The monitor calls approved RiskDayService.pnl after proving complete stream
coverage. It checks every supplied observed equity point, not just the latest
point, against that fixed threshold. An observed breach followed by recovery in
the same complete report still latches. Complete observed coverage does not mean
the system knows an unobserved market trough. External net deposits/withdrawals
are removed; internal pairs must reconcile; fees/funding already included in
equity are not deducted twice.

`DailyLossMonitor.assess(scope)` accepts scope only. It opens the PostgreSQL UoW,
holds the existing collateral mutex and reads original applicable locks. A
protected `ledger.collateral_groups` record in the daily-loss-policy namespace
binds the authoritative group to its policy source, ID, revision and hash. The
monitor reads the exact current persisted policy back from `ledger.policies` and
checks the source against current account/product group members. The ordinary
execution owner has SELECT, not INSERT, on both metadata tables. No client
threshold, caller completeness boolean or request policy object is accepted.

Only trusted server composition may install `read_report(tx, scope, policy,
members)`. That local reader must prove provenance and complete group valuation,
cashflows and cost history from protected archives; it must not perform network
or model work inside the transaction. Its typed report binds this transaction,
scope, group, current policy hash and all authoritative members. All eight
streams are required: equity, valuation/FX, external flows, internal transfers,
fees, funding, borrow costs and securities cashflows. Evidenced zero is valid;
absence of a stream is unknown. Hash syntax or a source label is not provenance.

The current report requires the exact opening of the current configured IANA day,
chronological actual equity/cashflow timestamps, valid historical ingestion
evidence, and fresh latest equity/archive coverage. Historical opening evidence
is not incorrectly required to have a fresh event timestamp. All coverage must
reach the latest observation. Missing opening/costs, stale/future data, malformed
policy, bad group members or missing trusted reader deny new risk without
fabricating a DAILY_LOSS observation. Historical/backlog reports outside the
current risk day are not used to authorize new risk; recovery of such gaps is
separate review/integration work.

Healthy trusted UTC and monotonic clocks are refreshed after authority and report
readers and immediately before writes. The final check follows report hashing
and evidence preparation too. A stalled/backwards wall clock cannot extend the
observation validity window. The assessment's action set is only a diagnostic
ceiling: ordinary authorization/risk/reservation/owner/quantity checks and A.3
proofs remain mandatory.

## Latch, commit and recovery

On actual observed breach, the monitor appends the original t.RiskLock with
DAILY_LOSS/NO_NEW_RISK, its DomainEvent/outbox, and a DailyLossBreach evidence
record in the same transaction. The evidence retains the exact protected policy
binding, full report/hash, original opening basis, threshold, first observed
crossing and latest P&L. It returns the denied business result only after the
UoW commit succeeds. It does not raise a business rejection inside the transaction
and thereby roll back its own lock. SQL failure or lost commit acknowledgement
does not return a successful assessment.

An existing applicable daily lock is reused under the same mutex, irrespective
of report rebound, new process or day rollover. Concurrent BTC/ETH triggers
therefore create one original group lock, not symbol aliases. Independent causes
retain their intersection and are never cleared by this monitor. Missing data
denies new risk while leaving independently proved exit/protection subject to
those existing intersections. Partial stop repair remains GAP.

The separate resolver remains the only source-resolution authority. The monitor
reads validated resolved originals and the original ResolutionAudit. That audit
does not define a complete reviewed observation watermark or a separate new-day
policy-review proof. Consequently rearm remains DENY_NEW_RISK with
DAILY_LOSS_REVIEW_WATERMARK_REQUIRED, even after a later day rollover, until that
contract is implemented. There is no replay/relock of an already reviewed prefix,
no reset of the original day baseline, and no invented approval or still-breached
policy override. The extended repository flag include_resolved defaults to False;
resolved records are never included in the ordinary active permission view.

## Verification

All runs below use fresh disposable ip08_test_* databases on the already
authorized local qualification cluster, with Linux/Python 3.12.16/PostgreSQL 17.11.
They call actual PostgreSQL and runtime/resolver roles, not mocked SQL.
The data and review reports themselves remain explicitly synthetic PAPER fixtures.

Recorded red probes:

- The initial new API was absent: **exit 1, 1 failed**.
  [Output](evidence/domain-daily-loss-2026-10-27/01-feature-red.txt),
  [exit](evidence/domain-daily-loss-2026-10-27/01-feature-red.exit.txt).
- Self-review found report hashing could consume validity after the earlier
  check: **exit 1, 1 failed, 30 passed** before moving a fresh check immediately
  before writes. [Output](evidence/domain-daily-loss-2026-10-27/02-processing-clock-red.txt),
  [exit](evidence/domain-daily-loss-2026-10-27/02-processing-clock-red.exit.txt).
- Self-review found rollover could bypass the missing review-watermark gate after
  source resolution: **exit 1, 1 failed, 47 passed** before the conservative
  rearm correction. [Output](evidence/domain-daily-loss-2026-10-27/03-rollover-rearm-red.txt),
  [exit](evidence/domain-daily-loss-2026-10-27/03-rollover-rearm-red.exit.txt).

On exact implementation commit `472fd90fc6711e63512f05b0aeae24e89413d3d4`:

```powershell
& ./scripts/run_group_daily_probes.ps1 -Label monitor-committed -TestPaths tests/integration/test_daily_loss_monitor.py,tests/unit/test_locked_actions.py,tests/unit/test_risk_applicability.py,tests/unit/test_risk_evaluator.py
# Exit 0: 248 passed, 0 skipped (12.97s); ip08_test_group_d884b41aa2d7
& ./scripts/run_group_daily_probes.ps1 -Label monitor-committed-group -TestPaths tests/integration/test_group_daily_locks.py,tests/unit/test_risk_applicability.py
# Exit 0: 30 passed, 0 skipped (2.55s); ip08_test_group_24d77582fad5
& ./scripts/run_group_daily_probes.ps1 -Label monitor-committed-persistent -TestPaths tests/integration/test_persistent_risk_locks.py
# Exit 0: 36 passed, 0 skipped (2.67s); ip08_test_group_73b71e825a57
```

Raw outputs, exit status, database names, fixed commit and source/migration hashes:

- [248-probe output](evidence/domain-daily-loss-2026-10-27/04-committed-monitor.txt),
  [exit](evidence/domain-daily-loss-2026-10-27/04-committed-monitor.exit.txt),
  [metadata](evidence/domain-daily-loss-2026-10-27/04-committed-monitor.json).
- [Group/isolation output](evidence/domain-daily-loss-2026-10-27/05-committed-group.txt),
  [exit](evidence/domain-daily-loss-2026-10-27/05-committed-group.exit.txt),
  [metadata](evidence/domain-daily-loss-2026-10-27/05-committed-group.json).
- [Persistent/resolver output](evidence/domain-daily-loss-2026-10-27/06-committed-persistent.txt),
  [exit](evidence/domain-daily-loss-2026-10-27/06-committed-persistent.exit.txt),
  [metadata](evidence/domain-daily-loss-2026-10-27/06-committed-persistent.json).

The 248 run includes 48 new real PostgreSQL monitor cases. They cover threshold
equality, exact high-precision fraction arithmetic, cashflow adjustment, no double
fee deduction, IANA/DST 23/25-hour days, concurrent group deduplication, visible
committed event/evidence, actual subprocess restart, rebound/rollover, server-side
policy/archive forgery rejection, all unknown/stale/future/clock boundaries,
actual pg_terminate_backend rollback and lost-commit-ack recovery, independent
causes, separate resolver review and A.3 partial protection/owned reduction.

## Unresolved integration — not full A.4/PAPER acceptance

No production archive reader, protected policy-binding provisioner or shared
console/AI/manual command bridge is wired by this subtask. Those entries must
use this same original lock/monitor result and retain final dispatch rechecks;
they must not create an independent legacy daily-loss authority. A business
rejection must not wrap and roll back a committed latch. The public assessment
returns after its own transaction commits; it is not a queue execution permit.

The complete review watermark, new-day policy review and safe rearm contract
remain unimplemented and fail closed. Prior-day backlog/observation continuity,
production valuation provenance, full combined PAPER crash acceptance and native
qualification remain separate work. No real account, venue API, Testnet or live
write path was used or enabled. No worktree was deleted.
