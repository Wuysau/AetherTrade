# Direct SQL snapshot isolation guard

A second independent review found that migration 0004's mutex did not protect
direct owner SQL at REPEATABLE READ or SERIALIZABLE. A transaction could establish
a snapshot before another session committed an abort and released backing, then
acquire the unchanged mutex row and fail to see that abort. The reverse stale
snapshot could miss a committed send while inserting an abort. The earlier
190-test result did not cover this isolation boundary.

The undeployed additive migration 0004 now requires
`current_setting('transaction_isolation') = 'read committed'` at entry to the abort
function, before any mutex/state read, and in the MAY_HAVE_SENT insert guard,
before any mutex/abort read. Other isolation levels raise a check violation.
The UoW already sets READ COMMITTED before its first data query. The original
command trigger, shared mutex, immutable history and role grants are unchanged.
No caller-controlled custom setting grants an exception.

Four new actual two-session regressions cover REPEATABLE READ and SERIALIZABLE
in both directions. The stale session sets and verifies its isolation level and
`current_user = ledger_owner`, then reads the existing READY command to establish
its snapshot. Another READ COMMITTED owner session commits either abort plus
legitimate reservation release, or the send boundary. The stale direct SQL action
is rejected with the required isolation error; no contradictory abort/send state
or backing change commits. Existing READ COMMITTED race and legitimate-send tests
remain in the same passing run.

Fresh databases on the isolated real Linux PostgreSQL 17.11 cluster:

- `ip03_test_isolation_guard_15abc44d`: 0001 + roles + 0004.
- `ip07_test_isolation_guard_15abc44d`: 0001 + roles + 0004.
- `ip08_test_isolation_guard_15abc44d`: 0001 + roles + 0002 + 0004.
- `ip10_test_isolation_guard_15abc44d`: 0001 + roles + 0004.

Actual Linux Python 3.12 runner command:

```sh
cd /tmp/app
python -m pytest -q tests/integration/test_ledger_transactions.py tests/integration/test_reservations.py tests/integration/test_persistent_risk_locks.py tests/integration/test_durable_dispatch.py --junitxml=/evidence/isolation-guard.xml
```

**194 passed in 24.36s; zero skips; exit 0.** Raw artifacts are retained in ignored
`.runtime/isolation-guard.log`, `.xml`, `.exit`; runtime role/privilege/trigger and
migration evidence in `.runtime/isolation-runtime-proof.json`; synthetic before
and after command/reservation rows in `.runtime/isolation-ledger-snapshot.json`.

The amended migration 0004 SHA-256 is
`19017e040298f0a0f814312252d4c3f3bce25298f3e58512c6d9e8efcf4b73b6`.
The older 190-test evidence and migration hash remain historical records in the
preceding report. Migration 0003 is independent and was not installed by this
subtask. No original migration or deployed database was amended.

This SQL repair does not cover offline emergency-journal permits. That path still
depends on its separately supplied authoritative composition and is not qualified
by these tests. Full coordinated PAPER integration remains **NOT_RUN here**;
no native, TESTNET, or LIVE gate was enabled.
