# PostgreSQL Ledger Repair Implementation Plan

**Goal:** Repair the actual isolated PostgreSQL failures without weakening ledger guards.

**Spec:** `AetherTrade_Next_Engineering_Task_2026-10-27.md`, sections A and C,
and the assigned ledger subtask. Base commit: `3062154`.

**Architecture:** IP-10 audit records remain the authoritative effective command
state; raw command history only allows READY to MAY_HAVE_SENT. Reservations must
read those effective states. Ready commands can be explicitly aborted under the
collateral mutex, while every possibly sent command must reconcile its original ID.

Risk resolution uses a separate trusted local verifier identity. The execution
owner retains no resolution rights. Additive migration `0002` creates a private,
append-only approval table and a fixed-search-path SECURITY DEFINER function,
callable only by `ledger_risk_resolver`. A resolution approval binds scope, lock,
sequence, exact payload, application transaction ID, and PostgreSQL transaction.
The service verifies all existing obligations inside the collateral mutex, then
atomically appends the event, resolved projection, approval, and audit. Missing
verifier or separate authority denies resolution. No runtime login is provisioned
or automatically granted resolver membership by the migration.

**Trust boundary:** A resolver is a separate security principal responsible for
authoritative local evidence verification; the SQL function validates identity,
unchanged lock contents, and sequence, not external facts. Application composition
must provide a separate credentialed resolver UoW and trustworthy verifier. An
execution-owner login must never have resolver membership. This change does not
provide an end-user unlock endpoint or enable automatic lock release.

**Tech stack:** Python 3.12, real PostgreSQL 17, isolated Docker network.

- [x] Read actual baseline failures and existing SQL/domain guard implementations.
- [x] Replace raw terminal fixture writes with durable send/outcome APIs; introduce
  explicit unsent abort and use effective command state during reservation reconcile.
- [x] Preserve stale-owner idempotency coverage by injecting the stale binding gate;
  direct owner mutation remains forbidden pending reviewed transfer CAS.
- [x] Add a separate resolution authority and atomic, additive SQL resolution path.
- [x] Add negative role/forgery, unsent-boundary, and rollback regressions.
- [x] Run targeted Linux tests against fresh ip07/ip08/ip10 databases, preserving logs.
- [x] Record migration/runtime-role evidence and review the implementation diff.
- [x] Prepare reviewable fixes and hand off for coordinated integration review.

**Constraints:** One writer in this worktree; read-only source bind to Linux runner;
no existing database reuse, production credentials, venue calls, disabled triggers,
rewritten original migration, native bridge enablement, or qualification upgrades.

**Baseline:** Parent run: 27 failed, 427 passed, 2 skipped. This subtask owns 20
reservation, three risk resolution, and one owner-fixture failure. Three other
fixture failures are assigned separately. Local mocks do not count as PG evidence.
