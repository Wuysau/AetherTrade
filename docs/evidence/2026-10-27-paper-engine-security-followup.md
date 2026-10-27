# PAPER engine security followup

Source base: `df76e25cb0921a6e1cc584836e050f5208211f99`. This evidence supersedes
the original adapter run for the lock-wait and database-role findings. Native,
Testnet and LIVE qualification remain NOT_RUN and all existing gates stay closed.

The review found that CANCEL/AMEND checked an authority snapshot before a
blocking `SELECT FOR UPDATE`, then committed a maintenance operation after that
snapshot or permit could expire. Eight real-PostgreSQL regressions reproduced
the defect on the original implementation: both operations continued after
permit expiry, lease expiry, revocation and ownership-epoch change. Raw red
output is `2026-10-27-paper-engine-security-red.txt` (8 failed, exit 1).

The adapter now reloads current authority through the trusted reentrant owner
guard after the target lock, immediately before every mutating statement and
immediately before committing. A denial after a statement waited on a table,
index or foreign-key lock rolls back every staged order/command change. The
outer owner guard still serializes revocation through commit. Synthetic event
control is likewise re-entered after its row lock, before writes and before
commit; stale controller authority rolls back staged amendments and events.
These checks do not open or hold a business-ledger transaction.

`scripts/provision_paper_engine_roles.py` provisions two distinct LOGIN roles
in an explicitly isolated `paper_engine_*` database. It checks the actual
`current_database()` and rejects a preexisting `paper_sim` application schema
before applying SQL0003. Missing isolation opt-in/DSN fails NOT_RUN; it never
silently skips. The real guard negative test creates `paper_sim` only inside a
rolled-back transaction, and proves that setup is refused before migration.

- Runtime role: SELECT synthetic state, append immutable commands and initial
  orders, update only pending operation/command, certainty and revision. Initial
  orders use the database SUBMITTED default, with zero filled quantity.
- Separate synthetic-controller role: SELECT synthetic state, append synthetic
  events, and update lifecycle/quantity/fill/pending state. It cannot insert
  commands or initial orders. Inject requires explicit `event_dsn` plus the
  trusted event guard; the runtime DSN alone cannot fabricate fills.
- Both roles: NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOINHERIT, NOREPLICATION,
  NOBYPASSRLS, no memberships, no table/database ownership, no schema CREATE or
  temporary tables, no DELETE/TRUNCATE, and no access to an administrator-owned
  synthetic authority sentinel. Public schema CREATE/database privileges are
  revoked. Neither role receives broad DDL or arbitrary table privileges.
- Every engine connection checks these restrictions, the actual database
  prefix, and separate command/event privileges. Admin credentials, swapped
  controller/runtime credentials and excessive economic write permissions are
  rejected. Administrator credentials belong solely to provisioning/test
  orchestration; the adapter's actual sessions all use restricted roles.

Fresh dedicated database: `paper_engine_e6890752ff32` in
`aethertrade-paper-qualification-5fbf2cf3`, network
`aethertrade-paper-qualification-5fbf2cf3-net`.
SQL0003 SHA-256:
`b3628dfac08132b617cfad98422166857f9b6f36849efa113146a4a6a43fb0ee`.
No production database or real exchange account was accessed. Credentials are
generated independently, not printed or committed; role names are unique per
test session. No preexisting database/schema/role is deleted.

The runner uses Docker `python:3.12-slim`, PostgreSQL `postgres:17-alpine`, a
read-only source mount copied into `/tmp`, and Linux UID/GID 10001 for pytest.
The administrator connection creates roles and observes lock waits only;
EnginePort and injected-event operations execute with their restricted roles.
The lock tests wait until `pg_stat_activity.wait_event_type='Lock'` for the
actual engine backend, then invalidate authority and release the held lock.
They assert no maintenance command row, order-state change, fill, synthetic
event or staged amendment survives the rollback.

```text
python -m pytest tests/integration/test_paper_engine_postgres.py \
  tests/qualification/test_nautilus_bridge.py \
  tests/faults/test_owner_and_storage_failures.py \
  -k "not backup and not restore" -q -p no:cacheprovider \
  --junitxml=/tmp/paper-engine-results.xml
```

Final result: **219 passed, 8 deselected, zero skipped; exit 0**. This includes
48 real-PostgreSQL adapter/security tests and the existing bridge/owner/Linux
flock regressions. The eight explicitly deselected backup tests are unchanged
baseline `Money(value=...)` fixtures handled by the separate P0 repair.
Raw green output/JUnit are `2026-10-27-paper-engine-security-pytest.txt` and
`2026-10-27-paper-engine-security-junit.xml`.

The first expanded run also exposed a test-instrumentation issue: querying
`pg_backend_pid()` opened a transaction on an instrumented connection used again
by a later read-only query. Restoring the original connector before assertions
fixed the fixture; production read connections check roles in autocommit mode
before opening their transaction. This fixture failure was not counted green.

Full console composition, reservation/protection integration and independent
re-review remain separate requirements. These results qualify only the bounded
PAPER adapter and the tested database authority boundary.
