# Binance Demo Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

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
- [x] Write and run failing tests for endpoint isolation, signing, credentials, time/HTTP failure classification, spot filter validation.
- [x] Implement fixed spot endpoints and disabled USDM namespace, HMAC credentials, bounded read-only APIs and write transport requiring owner permit.
- [x] Run focused tests; commit the contract/transport slice.

### Task 2: Durable command and facts
Files: storage/demo.py, migrations/0033_binance_demo.sql, execution/demo_commands.py, execution/demo_reports.py, runtime/demo_host.py; tests/demo/test_lifecycle.py.
Interfaces: DemoRepository transactions, DemoCommandBoundary, DemoExecutionOwner, DemoReportVerifier, DemoAccountingService, DemoReconciler.
- [x] Write/run failing tests for reservation/idempotency/MAY/UNKNOWN and duplicate/late/conflicting reports.
- [x] Implement scoped SQL persistence, fencing, durable inbox/outbox, immutable intents, order/fill bindings, Decimal postings and recovery cursors.
- [x] Implement conservative risk/admission and unsupported-capability gates; offline testing never grants network execution.
- [x] Run focused tests; document PostgreSQL concurrency acceptance as NOT_RUN; commit.

### Task 3: User-entered Demo credentials
Files: server/demo_api.py, server/app.py, web/src/runtime/demo.tsx, web/src/runtime/{App,types}.tsx/ts; tests/server/test_demo_api.py; web/tests/runtime-demo.test.tsx.
Interfaces: authenticated /api/demo/status, /connect, /disconnect; session-only provider and read-only checks.
- [x] Write/run failing API tests for auth/CSRF, invalid inputs, safe errors, logout and no credential persistence.
- [x] Implement explicit user consent, fixed SPOT/HMAC input, verified Demo balances/rules/open orders and visibly blocked order execution.
- [x] Add bilingual accessible form, repeated submission protection, cancellation/unmount guards and credential field clearing.
- [x] Run related API checks, frontend targeted tests/typecheck/build if installed tooling permits; commit.

### Task 4: Evidence, review and publication
- [x] Independent whole-branch safety review; address important findings with regression tests.
- [x] Record exact commands, PASS/BLOCKED/NOT_RUN, capability matrix and deferred real-venue qualification.
- [x] Commit final evidence; publish feature branch and verify remote commit, no merge.

## Execution rulings
- User already approved the supplied design and explicitly requested execution, so proceed without another planning approval.
- Repository governance overrides generic full-suite instructions: no full pytest, full frontend suite or database provisioning.
- Session-only credentials implement user input without granting ongoing access or saving secrets. D07/D08 actual venue qualification remains blocked without keys/authorization. UI cannot enable trading.

## D06 continuation
- Added independent snapshot/frame adapter, real assessment_v1 ModelService composition and immutable native decision receipts.
- Added target/fee/slippage/daily-loss/active-order gates, scoped protection/management, bounded runtime composition and evidence-bound READY.
- Added additive migration 0034; never applied it here.
- Added read-only independent safety review plus regressions for stale snapshots, concurrent claims, foreign/PAPER projection, cancel UNKNOWN and no replay, bound protection allocation, and slow/failed model supervision.
- Full-design acceptance remains open: PostgreSQL/venue qualification and D08 are NOT_RUN. MARKET/USDM/native conditional/OCO remain disabled or unimplemented, as explicitly itemized in docs/binance-demo/README.md.

## Exit and memory continuation
- Implemented committed plan-head lineage, revision/hash-bound UPDATE_EXIT with all-duty monotonic protection management and no venue order side effect.
- Added separate Demo memory projection and exact native ModelService input-hash validation, leaving PAPER memory scopes unchanged.
- Added migration 0035 without applying it. MARKET extension remains active work.

### Offline continuation, 2026-11-04

- Added a quote-budget MARKET compiler and accounting for base/quote cumulative execution. Malformed or missing MARKET notional filter fields fail closed. MARKET strategy admission still rejects execution, and capability-specific venue qualification remains mandatory. Quote-budget execution has no guaranteed execution-price bound; offline checks do not establish one.
- Added explicit migration 0036 for cumulative quote amounts; it is NOT_RUN on a deployed database. MARKET fees and unmatched exposure are recorded as durable risk breaches; automatic resume and new entries remain blocked pending a separate breach-resolution workflow. Already-qualified cancellations and owned protective exits remain possible.
- Offline credential rebind validates signed UID through the trusted epoch resolver, drains durable observations, retains commands and reservations, and waits for same-process windows and final pause. PostgreSQL rebind is BLOCKED because stream-lifetime ownership is not fenced across processes; venue identity continuity and PostgreSQL fencing remain unqualified. SQLite runtime windows are blocked, including execute=False.
- Hosted protection now requests cancellation of a confirmed resting GTC mitigation after its bounded authority deadline. An acknowledged exit without a status requires original-ID reconciliation first, and a price rebound does not suppress cancellation. Orders with no remaining protection duty are also canceled or reconciled; a definitely-unsent expired cancellation can receive a new lineage, while UNKNOWN remains bound. A replacement requires terminal reconciliation and complete fill accounting. Dust, unavailable cancellation qualification, unresolved breaches, and account or stream uncertainty still block safe progress.
- Offline local Demo tests: 125 passed, with one Starlette/httpx deprecation warning. Python compile and diff whitespace checks pass. No venue calls, credentials, Docker, deployed migrations, PostgreSQL concurrency qualification, full acceptance, or live trading were performed. Capability remains disabled pending qualification.

- Runtime windows with `execute=False` still write reconciliation and stream observations; assessment can invoke model services and stage commands. Neither is an inert preview. No automatic startup or production wiring was added.

- Quote-sized open BUYs have an unknown remaining base quantity, represented as null rather than a fabricated value. This permits protected SELL compilation while MAX_POSITION BUY admission fails closed. Actual Demo open-order semantics are unqualified; no venue calls were made.

- Unmatched negative base exposure is retained in the management snapshot and blocks assessment/new risk, while independently qualified cancellation can still proceed. Protection fees beyond policy and unmatched debits produce durable breaches.
