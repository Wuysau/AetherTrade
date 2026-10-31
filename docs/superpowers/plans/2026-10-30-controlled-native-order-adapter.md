# Controlled pinned native order adapter implementation plan

> **For agentic workers:** Use superpowers:executing-plans with actual RED/GREEN evidence and independent review. Native/server/financial ownership remains separate.

**Goal:** Implement owner-held, lossless native creation/observation interfaces for all existing46 capability targets through the exact Nautilus1.231.0 source/build boundary, with external qualification and activation disabled.

**Architecture:** Add a controlled extension over the pinned SDK BinanceHttpClient.sign_request. Static typed product/service/operation routes preserve the original VenueCreationRequest and every form parameter/ID. The existing NautilusEngine, ExecutionOwner and actual ProgramCommandBoundary provide exclusive owner, committed MAY/one-use consumption and current checks; no new owner, money ledger or legacy intent. Archive raw bytes and distinguish native call observations from validated order facts or permissions.

**Tech Stack:** Existing Python3.14.2 Windows candidate, frozen Nautilus1.231.0 wheel/source manifest, typed dataclasses, exact JSON/time, existing PostgreSQL ledger and isolated Linux PG safety fixtures.

**Spec:** qualification/binance_orders/reference/CODEX_HANDOFF.md; approved ADR-ORDER-001.md; pinned qualification/nautilus/order_target/manifest.json and COMPATIBILITY.md; parent approved native recon/plan2026-10-30.

## Global constraints

- New isolated worktree binance-ope-native-adapter, branchcodex/binance-ope-native-adapter from exact committed root13bf1e3761ba98ac0b2b6d01fc65ddecdb465459. Root C working files were untouched. Report tree5ef7d9a remains read-only.
- Preserve all46 targets/56 acceptance cases. Interface implementation is separate from source/build/account/venue/performance qualification.
- No LIVE/TESTNET/DEMO activation, credentials/account discovery, actual trading endpoint calls, paid models, main/archive/public push or cleanup.
- Actual SDK probe environment is preserved native-lock/.native-probe-env: CPython3.14.2, Nautilus1.231.0; main operational venv has no SDK. No environment is silently upgraded.
- Pinned source27a8e54e7ac3c57d6cbf8891f0283dfbaee97317; executionSHA642b91645bea94648a674db4c202072a96b7a184a91c5f91d33792587dba0d2b. Preserve original manifest/lock bytes. Windows candidate does not qualify Linux deployment.
- Upstream high-level _submit_order_list rejects linked OCO; never decompose native lists or drop fields to fit it. Use explicit static controlled extensions where pinned types are incomplete.
- Every application write requires real committed command/permit and existing owner/lease checks. Venue-generated children are observation-only. Unsigned typed requests are never permission.
- No business transaction across SDK/venue IO. Signer/client remain owner-held. No arbitrary URL or signer callback available to callers/server/AI.
- SDK HttpClient rate limiting may wait after sign_request starts. A call-entry check does not prove last-send authority. External qualification remains BLOCKED until a reviewed post-wait guard or host-owned preallocated budget path is proven.
- Root integration_next owns shared financial kernels/host accounting, public schemas/migrations/inventory. Coordinate shared changes serially; preserve original hashes and UNKNOWN/original-ID recovery.

## Review focus

Cross-product/service/namespace substitution; unsigned or reused/expired consumption; owner/fence/book revocation after queue wait; loss of list/conditional/execution/equity fields; signer exposure/arbitrary path injection; duplicate/replayed response IDs; raw source/fees/quantity preservation without inferred fills or release.

## Task1: Exact source/build and complete creation-call interface

Create engine/nautilus/native_creation.py and focused tests/qualification/probe artifacts. Build a strict15-route product/namespace/operation/endpoint table from frozen Endpoint values, retaining all46 capability targets. Consume exact VenueCreationRequest.form_parameters and original assigned IDs, reject duplicate/ambiguous fields or service/product switches. Add only separate timestamp/recvWindow transport fields, preserve bool/text/decimal/batch JSON exactly. All output remains unsigned and unauthorized.

- [ ] Write and run RED for all15 routes, actual ordinary/list/batch/conditional/execution/equity materialization, all relevant parameter preservation, wrong routes/IDs/types/auth-field/duplicate substitutions.
- [ ] Implement pure call builder, source/build guard and static service routes; observe GREEN with existing request/native regressions.
- [ ] Probe actual pinned SDK sign_request serialization/signature using only deterministic test credentials and an in-process fake HTTP transport; no endpoint/network IO. Record installed file/artifact hashes and explicit limitations. Test source/version/ABI mutations fail closed.

## Task2: Actual existing owner/one-use command boundary

Compose the extension only through existing NautilusEngine/ExecutionOwner. Reopen actual protected consumption/original records; verify exact original command/request/scope, fence/book/owner/lease and transport window after any test queue wait. Do not issue a second permit or open a business TX through IO. Fake transport is a concrete offline probe implementation; actual external sender stays harddisabled.

- [ ] Write real isolated PG RED for admission→committed MAY→consume→offline native-shaped call; changed request, permit reuse, last-call expiry, owner/fence/book mutation and storage/commit uncertainty leave UNKNOWN/query-original duties and zero unauthorized wire calls.
- [ ] Implement minimal existing-engine composition, bounded current-consumption guard and explicit dispatch observation. Coordinate any shared ExecutionOwner/public boundary addition with integration_next.
- [ ] Prove fake wait then current recheck; inspect pinned Rust HTTP source for a viable post-rate-limit hook. Preserve BLOCKED external last-send qualification if source/build proof is unavailable; do not add enable flags.

## Task3: Namespace response/raw archive/original-ID recovery

Reuse NativeEventBridge and existing original-ID/order_query readers. Bind ordinary/list/conditional/execution/equity responses to the exact original namespace/client IDs and actual raw source; archive even invalid/error bodies before interpreting. Use only pinned actual source schemas or reviewed official schema evidence; unsupported shapes remain UNKNOWN, never guessed fills/ownership or release. Preserve all returned native IDs/quantity/fees and explicit child association; never resubmit venue children.

- [ ] RED source substitutions, duplicate IDs, foreign members, errors/non-JSON, lost ACK/restart and incomplete list/algo coverage.
- [ ] Implement typed read-only call observations and source-specific response mapping. No financial posting or automatic release.
- [ ] Run fixed source PG plus SDK/offline/archive/recovery regressions and retain RED/GREEN/infra evidence with committed-byte SHA checks; independent review before root integration.

## Task4: Controlled management extension and full interface tracking

After creation contracts are concrete, coordinate management command/permit contracts with integration_next and add route-specific cancel/amend/query mapping under the same owner/pinned client/source boundary. Preserve all advanced46 targets and56 cases, noting implementation versus blocked venue qualification individually. This task remains active across creation review; do not declare full OP-E/native qualification from this first bounded slice.

## Current evidence

Baseline at13bf1e3:125 existing native bridge/target-manifest/program-command unit checks passed0fail/skip23.52s. Actual installed probe confirms1.231.0 and exact execution hash above; native order/account/venue qualification NOT_RUN, Linux build BLOCKED. No client/account/trading endpoint was called.
