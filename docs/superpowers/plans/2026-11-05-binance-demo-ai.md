# Binance Demo AI execution completion plan

> **For agentic workers:** Use superpowers:executing-plans in this session. Steps use checkboxes for tracking.

**Goal:** Implement the approved offline-verifiable SPOT BTCUSDT LIMIT execution design without enabling unqualified trading.
**Architecture:** Explicit signed admission evidence, conservative serialized account reconciliation, durable recovery/takeover, and an opt-in runtime composition. Reuse existing AI, durable commands, permits, report reducers and native Demo transport.
**Tech Stack:** Python, cryptography Ed25519, SQLAlchemy, FastAPI, React.
**Spec:** docs/superpowers/specs/2026-11-05-binance-demo-ai-design.md (approved 2026-11-05).

## Global Constraints
- No Docker, provisioning, migrations, venue accounts/orders, paid model calls, integration merge or deployment.
- Preserve UNKNOWN/original IDs, legacy gates and evidence. Only qualified new policy versions may replace old unconditional rejection.
- TASK_LIGHT only; offline credentials/signers never constitute production qualification.
- One writer in codex/binance-demo-ai-20261105. Evidence retained, no cleanup.

## Review Focus
- Signed but wrong-purpose/account/build/generation/revoked evidence must fail closed.
- Unrelated or late account events must not be silently consumed as reconciled.
- Same decision/order must not consume new budget or be resent after UNKNOWN.
- Credential expiry/stop must not claim a flat account or complete protection.
- Trust configuration and model output must not become self-issued trading authority.

### Task 1: Evidence verification and qualification workflow
Files: new engine/binance_demo/attestation.py, qualification/demo_evidence.py; tests/demo/test_attestation.py.
Interfaces: TrustStore.verify(envelope, purpose, bindings, now) -> payload; measure_build(root) -> digest; QualificationRun records bounded capability observations and produces unsigned evidence for independent signing.
- [ ] Add tests for signature, exact purpose/binding, expiry/revocation, content measurement, capability-specific FAIL/NOT_RUN and command budgets; run to observe failure.
- [ ] Implement canonical bounded signed payloads, domain separation, read-only verification keys and source/dependency/migration digest. No runtime signing key.
- [ ] Run targeted tests, record output and commit.

### Task 2: Durable evidence, recovery and takeover storage
Files: new storage/demo_authority.py, additive migration; tests/demo/test_authority.py.
Interfaces: DemoAuthority.install/require/revoke, consume_recovery; takeover snapshot/acknowledge bound to account and activity hash. Serialized transactions use existing account/sender guards.
- [ ] Tests reject replay, stale state, old credentials, wrong generation and incomplete takeover; preserve UNKNOWN, reservations, baseline and history.
- [ ] Implement immutable proof-consumption/revocation rows and audited takeover with strategy authorization revoked before acknowledgement.
- [ ] Run focused SQLite tests; record genuine PG checks NOT_RUN; commit.

### Task 3: Conservative reconciliation policy
Files: new execution/demo_consistency.py; modify demo_reconcile.py and demo_commands.py; tests/demo/test_consistency.py.
Interfaces: SerializedAccountPolicy.prepare(scope, stream, command_id=None) -> receipt; validate(receipt, scope) checks revision/generation/time/ledger and signed authority.
- [ ] Tests: valid dedicated account, external trades/balances, incomplete fees, stale stream, duplicate entry, racing revision, UNKNOWN and pending positions.
- [ ] Reuse original-ID backfill; enforce one risk-increasing command in flight and original budget ceiling. Consume only captured matching position frames under locks; never claim an atomic REST/WS barrier.
- [ ] Wire explicit policy only; retain legacy default rejection. Run focused regressions and commit.

### Task 4: Qualified deployment composition and recovery
Files: server/demo_deployment.py, runtime/demo_service.py, storage/demo_physical.py; tests/demo/test_qualified_deployment.py.
Interfaces: explicitly configured verifier/manifest plus DemoAuthority and SerializedAccountPolicy; refresh qualification at bind/start/send. Authenticated offline recovery proof authorizes new physical generation only after prior ownership fence and preserved recovery state.
- [ ] Tests reject mismatched build, missing trust roots, unverified evidence and stale recovery; verify lazy construction and new opt-in composition.
- [ ] Bind signed source measurement and evidence, retain old configuration behavior, connect policy to owner/reconciler; ensure no credential/model/venue IO on load.
- [ ] Run deployment/runtime regression selections and commit.

### Task 5: Exit budget and operator takeover
Files: runtime/demo_service.py, runtime/demo_control.py, server/demo_control_api.py, web/src/runtime/demo-controls.tsx (locate actual component before edit); tests/demo/test_takeover.py, relevant API/UI tests.
Interfaces: bounded entry cutoff; durable NEEDS_OPERATOR and acknowledgement API taking expected responsibility digest. Acknowledgement never resumes execution.
- [ ] Tests: entry denied after cutoff, protection/known-order cancel retain their own authorization, expiry/unknown/open exposure cannot be called flat, stale acknowledgements rejected.
- [ ] Add runtime and UI state, explicit acknowledgement and preserved audit facts.
- [ ] Run focused checks and commit.

### Task 6: Offline chain, review and evidence
Files: tests/demo/test_qualified_chain.py, docs/evidence/binance-demo-ai/, docs/current-task.md, task registration.
- [ ] Exercise real AI assessment adapter with deterministic model/transport substitutes through qualified admission, durable command, observed fill and takeover; do not remove PG-only owner guard.
- [ ] Run relevant TASK_LIGHT suites, compile/typecheck as applicable; archive command/time/exit/count/source hashes.
- [ ] Fresh independent whole-branch review, fix material findings with regressions.
- [ ] Report code scope accurately; PG/venue/paid-model/full acceptance remain NOT_RUN/BLOCKED. Commit, no integration.

## Progress / rulings
- Design approved; native execution selected by agent under existing task authorization, avoiding redundant approval prompts.
- Existing root venv may execute tests with this worktree's pythonpath. No dependency installation or shared service startup.
- Skill scratch cleanup/full-suite instructions yield to project evidence retention and TASK_LIGHT limits.
