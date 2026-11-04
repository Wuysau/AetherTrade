# Binance Demo Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an isolated Binance Demo foundation and user-entered, session-only Demo credentials without changing PAPER or enabling unqualified trading.
**Architecture:** Dedicated Demo scope, endpoint registry, signed transport, durable SQL records, execution gate, report reducer and account journal. The authenticated console exposes only read-only connection checks; order execution stays closed pending PostgreSQL and actual Demo qualification.
**Tech Stack:** Python, HTTPX, SQLAlchemy/PostgreSQL, FastAPI, React/TypeScript.
**Spec:** docs/binance-demo/design-2026-11-04.txt

## Global Constraints
- Base: public-snapshot 4134fd1ba6ab9152be064fc91ad93c59f61b8eb7, confirmed through GitHub on 2026-11-04.
- BINANCE_DEMO is independent of PAPER/LIVE; fixed official hosts, TLS, no redirects/proxies/fallback.
- No credentials in logs, database, model context, frontend responses or persistent browser storage.
- No actual account connection, orders, paid models, Docker, PostgreSQL provisioning or full integration tests in this task.
- One writer; focused TASK_LIGHT tests only. No merge/deployment/cleanup. Commit and publish task branch.

## Review Focus
- Host confusion, redirects, clock drift and credential-bearing transport exceptions must fail closed.
- Expired sessions and out-of-order connection responses must not retain/reveal credentials.
- Ambiguous sends, owner fencing and restarts must preserve original IDs/reservations and never auto-resend.
- Duplicated/conflicting WS/REST trades, third-asset fees and late order reports must not corrupt account facts.
- Snapshot freshness, reset epochs, private-stream gaps and missing qualification must block risk.

### Task 1: Contracts and transport
Files: engine/binance_demo/{contracts,credentials,transport,orders}.py; tests/demo/test_transport.py.
Interfaces: DemoScope, DemoEndpointRegistry, DemoCredentialProvider, BinanceDemoTransport; independent of legacy ScopeKey.
- [ ] Write and run failing tests for endpoint isolation, signing, credentials, time/HTTP failure classification, spot filter validation.
- [ ] Implement fixed spot endpoints and disabled USDM namespace, HMAC credentials, bounded read-only APIs and write transport requiring owner permit.
- [ ] Run focused tests; commit the contract/transport slice.

### Task 2: Durable command and facts
Files: storage/demo.py, migrations/0033_binance_demo.sql, execution/demo_commands.py, execution/demo_reports.py, runtime/demo_host.py; tests/demo/test_lifecycle.py.
Interfaces: DemoRepository transactions, DemoCommandBoundary, DemoExecutionOwner, DemoReportVerifier, DemoAccountingService, DemoReconciler.
- [ ] Write/run failing tests for reservation/idempotency/MAY/UNKNOWN and duplicate/late/conflicting reports.
- [ ] Implement scoped SQL persistence, fencing, durable inbox/outbox, immutable intents, order/fill bindings, Decimal postings and recovery cursors.
- [ ] Implement conservative risk/admission and unsupported-capability gates; offline testing never grants network execution.
- [ ] Run focused tests; document PostgreSQL concurrency acceptance as NOT_RUN; commit.

### Task 3: User-entered Demo credentials
Files: server/demo_api.py, server/app.py, web/src/runtime/demo.tsx, web/src/runtime/{App,types}.tsx/ts; tests/server/test_demo_api.py; web/tests/runtime-demo.test.tsx.
Interfaces: authenticated /api/demo/status, /connect, /disconnect; session-only provider and read-only checks.
- [ ] Write/run failing API tests for auth/CSRF, invalid inputs, safe errors, logout and no credential persistence.
- [ ] Implement explicit user consent, fixed SPOT/HMAC input, verified Demo balances/rules/open orders and visibly blocked order execution.
- [ ] Add bilingual accessible form, repeated submission protection, cancellation/unmount guards and credential field clearing.
- [ ] Run related API checks, frontend targeted tests/typecheck/build if installed tooling permits; commit.

### Task 4: Evidence, review and publication
- [ ] Independent whole-branch safety review; address important findings with regression tests.
- [ ] Record exact commands, PASS/BLOCKED/NOT_RUN, capability matrix and deferred real-venue qualification.
- [ ] Commit final evidence; publish feature branch and verify remote commit, no merge.

## Execution rulings
- User already approved the supplied design and explicitly requested execution, so proceed without another planning approval.
- Repository governance overrides generic full-suite instructions: no full pytest, full frontend suite or database provisioning.
- Session-only credentials implement user input without granting ongoing access or saving secrets. D07/D08 actual venue qualification remains blocked without keys/authorization. UI cannot enable trading.
