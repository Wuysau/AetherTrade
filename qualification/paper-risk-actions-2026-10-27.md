# A.3 — verified actions under PAPER risk locks

Base: `3062154beb0dcade0711b93ecca060376e9773be`, branch `codex/paper-risk-gates`.
Scope: next-engineering-task A.3 only. No approved SPEC, wire schema, native
qualification status, credentials or live/testnet path is changed.

The approved architecture requires verified ownership, direction and actual
reducible quantity under NO_NEW_RISK; safety-state permissions are intersected
across all active cause locks. This change implements that policy in the offline
dispatcher and atomic reservation entry without clearing a lock.

## Failure and root cause

Before implementation, running the new two-case regression against the original
dispatcher produced exit 1, **2 failed**. Both `NO_NEW_RISK` and `EXIT_ONLY`
rejected an owned quantity-2 reduction at `dispatcher.py:95` with
`RISK_LOCK_OR_GROUP_COMMAND_UNRESOLVED`. The cause was an unconditional test for
any active lock, rather than a verified action classification.

The original reservation implementation has the same unconditional guard.
Loading `git show 3062154:src/trading_system/risk/reservations.py` into an isolated
Python module and calling its `_reserve` with the same synthetic valid owned
reduction reproduces `accepted=False`, `('ACTIVE_RISK_LOCK',)`, and zero writes.
That assertion probe exited 0. It does not alter the checkout or a database.

## Policy and trust boundary

`LockedActionVerifier` requires a trusted local archive reader, supplied only by
server composition. No production reader is installed by this change. Missing,
malformed, stale or unavailable proof keeps locked writes denied. A boolean,
operation name, source label, or DispatchFacts check name cannot establish the
exception.

The reader must establish complete current positions, working orders, asset
balances and ownership allocations from trusted archived evidence under the
collateral mutex. Its typed `ActionProof` is bound to the current transaction ID
and full immutable command, intent and local book hashes. These include scope,
authorization ID, policy, owner and execution epochs. Hashes bind inputs; the
reader remains responsible for provenance and report endpoint semantics.

The verifier compares the fresh report to the exact local position, balances and
working-order snapshot, checks the owned signed position and quantity units,
requires closing side and finite positive on-step quantity, and counts every
working closing quantity against actual exposure using exact rational arithmetic
(a tiny working remainder cannot disappear through Decimal rounding). Unknown or pending order
operations, missing order-side evidence and other queued commands on this scope
fail closed. The existing collateral-group unresolved-command check remains in
dispatch and now also applies at reservation entry.

- NO_NEW_RISK permits a proved bounded reduction, or a bounded LAST STOP_MARKET
  repair matching the durable native GAP/REPAIRING protection plan.
- EXIT_ONLY permits a proved bounded reduction; it denies protection repair.
- RECOVERY_REQUIRED and unknown reservation/order causes deny every write.
- Lock intersections use the existing RiskLockService action sets. Queries and
  reconciliation retain their separate read/evidence paths.

The dispatcher re-reads this proof before and after the durable marker. It carries
proof/report/evidence expiry into the final pre-I/O clock check. Any denial after
the marker remains UNKNOWN and never automatically resends. Existing owner,
lease, authorization, instrument/risk checks, reservation checks and durable
permit binding remain mandatory.

Conservative limits: single unambiguous owned position; no AMEND/CANCEL exception,
unbounded full-position exit, OCO/coexistence assumption, or MARK/BID_ASK trigger
encoding. Existing first-command reservation semantics support evaluated
SUBMIT/CLOSE reductions; this does not invent a protection reservation ingress.
All existing holdings/capacity checks still apply. The risk evaluator can still
reject a reservation with working orders even when the shared policy proves a
safe dispatcher-side remaining quantity.

## Verification

Commands ran from this worktree on Windows with:

```powershell
$env:PYTHONPATH='D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-risk-gates/src;D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-risk-gates/tests;D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-risk-gates'
$python = 'D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-execution/.venv/Scripts/python.exe'
& $python -m pytest tests/unit/test_locked_actions.py tests/unit/test_risk_evaluator.py tests/unit/test_target_compiler.py tests/unit/test_identity_and_capabilities.py tests/integration/test_persistent_risk_locks.py tests/integration/test_durable_dispatch.py tests/integration/test_reservations.py -q
# Exit 0: 310 passed, 125 skipped (4.06s)
& $python -m pytest tests/qualification/test_nautilus_bridge.py tests/qualification/test_stocks_adapter.py -q
# Exit 0: 171 passed (3.79s)
& $python -m pytest tests/unit/test_locked_actions.py tests/unit/test_risk_evaluator.py tests/unit/test_target_compiler.py tests/unit/test_identity_and_capabilities.py tests/integration/test_persistent_risk_locks.py tests/integration/test_durable_dispatch.py tests/integration/test_reservations.py tests/qualification/test_nautilus_bridge.py tests/qualification/test_stocks_adapter.py -q
# Final combined run, exit 0: 482 passed, 125 skipped (7.66s)
```

The initial delivered matrix contained **92 passing tests**. It covers positive product/leg
reductions and protection repair, wrong side/oversize, caller assertions, stale or
mismatched proof, working-order overlap, ownership/auth revocation, lock
intersections, capacity preservation, durable recheck and post-transaction proof
expiry, UNKNOWN behavior, and disabled TESTNET/LIVE composition.

Self-review additionally reproduced a Decimal rounding issue in the initial
implementation: quantity 2 plus a working remainder of 1e-91 was rounded to 2
under precision 80. The added remainder probe first failed (exit 1), then passed
after aggregate capacity comparisons changed to exact rational arithmetic.

The broader adjacent run including `test_protection_lifecycle.py` at the earlier
83-probe checkpoint exited 1: **371 passed, 129 skipped, 1 failed**. The failure
is the existing `test_dust_is_disclosed_without_illegal_orders[US_EQUITY-NONTRADABLE_RESIDUAL]`:
`support_ip_15.py:148` omits required `canonical_payload.tokenize` before reaching
execution. The fixture and schema are unchanged here; it is reported to A.1.

PostgreSQL transactional, concurrency, process-crash and restart probes are
**NOT_RUN**: this Windows worktree has no explicitly provisioned isolated Linux
IP07/IP08/IP10/IP15 databases. Skips are not acceptance passes. Memory lifecycle
probes establish Python control flow only. No real account, venue network call,
native write path, or real fence qualification was attempted. Production
composition remains blocked on a concrete trusted reader and the separate
PostgreSQL/native qualification work.

## Independent-review followup on f1ed3d9

Both important review findings were reproduced before the followup fix. Running
the new position-only-lock and slow-reservation-reader probes returned exit 1,
**5 failed, 92 deselected**: NO_NEW_RISK, EXIT_ONLY and RECOVERY_REQUIRED locks
embedded only in a position were ignored by the no-account-lock shortcut, and
expiry during either the proof callback or active-owner database read still
allowed the atomic reservation bundle.

The verifier now loads and validates the current book before that shortcut,
combines repository and position locks, and denies missing, stale or incomplete
book evidence. Even the no-lock validity limit includes book-evidence expiry.
The IP10 synthetic PostgreSQL fixture now explicitly seeds its current book;
absence of a row can no longer stand for empty, unlocked positions.

Reservation entry retains the verifier's validity limit, re-reads the trusted
service clock after the proof callback and immediately before the first bundle
write, and bounds both reads by the earliest proof/evidence, intent, risk,
entry-deadline and quote expiry. A trusted monotonic reader (default:
`time.monotonic_ns`) also bounds elapsed time and rejects backwards readings;
stalled/backwards UTC cannot extend the window. The persisted risk-decision TTL
is narrowed to that same earliest expiry. No time is read from caller payloads.

Partial STOP_MARKET repairs are accepted when both requested coverage and the
durable GAP/REPAIRING plan remain quantity-bounded by actual exposure. This
permission check does not mutate or verify the plan: partial improvement remains
GAP, and NativeVerifier still requires complete coverage before VERIFIED_NATIVE.

The same final combined command shown above now exits 0 with **509 passed,
125 skipped (12.68s)**. The expanded lock-action matrix has **119 passing tests**,
including position-only permission intersections, missing/stale no-lock books,
proof and database-read expiry races for every validity source, monotonic expiry
with stalled UTC, no-bundle-write assertions, and partial repair coverage.
The database-read races use synthetic service time advanced during the SQL read;
they are Python control-flow probes, not an actual PostgreSQL clock test.
PostgreSQL acceptance remains **NOT_RUN** in this Windows worktree.

At the independent-review followup boundary, account/collateral-wide DAILY_LOSS
applicability remained a separate integration requirement. It is now implemented
and verified in the [shared daily-loss followup](paper-group-daily-loss-2026-10-27.md).
The following describes the earlier gap: the ledger repository selected locks by exact instrument
and leg scope; this A.3 work therefore does not make a BTC-only cause lock visible
on ETH automatically. A safe shared policy must bind accounts/products to their
intended risk collateral domain using trusted durable configuration, read the
latest account-wide daily-loss locks under the group mutex, and verify their
applicability while retaining the source lock ID and scope for resolution.
Instrument-specific protection/order locks must not be broadcast globally.
The shared view must also drive RiskLockService.allowed_actions and user-facing
state. The original A.3 commit alone did not implement persistent account-wide
daily-loss enforcement; qualification of the later shared view is recorded in
the followup rather than retroactively attributed to those earlier probes.
