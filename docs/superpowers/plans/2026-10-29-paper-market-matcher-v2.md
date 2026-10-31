# Additive PAPER matcher V2 Implementation Plan

> Execute inline with superpowers:executing-plans. Root reviews serial SIM migration0014; report-verifier owns its read corrections. No new agents or other-tree edits.

**Goal:** Implement the remaining OP-D semantics in the same actual restricted PAPER venue, keeping the complete46/56 target and source-specific evidence.

**Architecture:** Preserve migration0013, V1 source payloads/hashes and legacy manual injection. Add explicit V2 model/terms/event DTOs, an additive0014_paper_market_matcher_v2.sql migration, reference/rule/calendar inputs and durable matching metadata. Upgrade the same venue allocator; never create a second cash/owner ledger, a fake parent, an application-child permit exemption or a caller-controlled fill callback.

## Approved boundaries carried forward

- Actual GenericProgram financial bundle and committed MAY/single-use consumption remain mandatory. Read the protected current assessment, current owner/fence/book and the same consumed deadline at real SIM barriers. Keep native request bytes unchanged and expose full accepted context, raw SQL event bytes/stored SHA, native quantity/unit, real venue ID/revision and exact source evidence.
- V1 remains Spot/USD-M fixed-limit only; unmodelled equities/sessions are explicitly denied until actual V2 calendar execution passes. RPI/counterparty data and ambiguous OHLC paths remain explicit missing-data restrictions, never fabricated execution.
- Sources are explicit tagged V2 records: reference prices (LAST/CONTRACT_PRICE/MARK_PRICE/BID/ASK), rule publication, clock progression and calendar windows. They are persisted by the restricted controller, not the order runtime or a model. Reference/clock events do not replenish depth. Missing/wrong trigger source or missing spread/session data leaves the order unactivated/unverified.
- V2 accepted terms retain the full original AtomicOrderSpec, compiled native quantity and rule/model revision. Existing V1 bytes are restored through their V1 types; V2 records use a distinct revision. Dynamic activation/extrema/effective price/display/session/list/algorithm progress is persisted alongside the existing orders, not substituted into original order fields.

## Sequence and real tests

1. [ ] Actual PG market BASE quantity from the protected finite execution envelope, opposing depth and immediate residual expiry. Exact native quantity remains distinct from quote-MARKET interval support; no money-to-quantity projection.
2. [ ] Actual PG LAST/MARK/CONTRACT/BID_ASK trigger separation; stop-limit activation remains a limit; stop-market uses opposing visible liquidity rather than the trigger price. Publish actual model venue rules for priceProtect; missing rules deny activation. Persist trailing activation/high-low extrema and prove fork/replay recovery.
3. [ ] Peg/priceMatch, iceberg reveal/queue policy, STP prevented quantities, dynamic price-range expiry, GTD and actual equity session/DAY/calendar/DST behavior. Per-asset fee models retain gross/receipt commission/available quantities; enforce every admitted ceiling without clipping raw facts. Test role/source/clock failures.
4. [ ] Native list OCO/OTO/OTOCO/OPO/OPOCO and batch individual outcomes using actual mother requests, full-fill/net-receipt activation and shared depth. Application-generated local actions always need their own actual current command/permit; native generated members are observations only. Full-fill-only protection never masquerades as per-partial protection. Persist cancellation/amendment/keep-priority and software-race outcomes.
5. [ ] Native TWAP/VP declared PAPER assumptions and durable local scheduler pools. Every local external child has a fresh actual financial/stage admission, command and permit. Stopped/expired parent retains unresolved children, budget and manage-until-flat duty.
6. [ ] Integrate actual report verification/normalization and root dispatch/ledger ingestion. Re-run the whole prior17+legacy safety cohorts and new tests on the final fixed source; update56 truthful statuses. No isolated cohort is full OP-D completion.

## Concrete first APIs

- `PaperMatchingModelV2`, `PaperExecutionTermsV2`, `PaperAcceptedContextV2` and `PaperCreationReportV2` are explicit additions; the old DTO shapes/hashes remain readable.
- `PaperReferenceEventV2` contains bounded scoped price points with distinct source tags and event/stream sequence; later rule/calendar/clock variants carry actual typed records, not booleans such as `allowed=True`.
- `provision_paper_market_v2(admin_dsn,roles)` applies only the reserved SIM migration and bounded grants. Runtime cannot publish references/rules/calendar or alter progress/fills. NOLOGIN function ownership, fixed search_path and revoked PUBLIC execute continue.
- The engine dispatches by the actual internal model revision after the same real financial boundary. Source readers explicitly dispatch V1/V2, retain complete raw bytes and block unknown revisions rather than reinterpret them.

## Review/verification focus

One liquidity source is shared across all competing accounts/products by exact market key; V2 trigger activation cannot supply a fresh copy. All quantity/fee math is exact under low caller precision. FOK preflights full depth/gross/fees. Existing accepted GTC/management duties survive proposal expiry; new application writes never reuse expired authority. Native member/exclusivity/receipt proof comes from actual accepted parent and source, not model tags or evidence IDs alone. All data is isolated synthetic PAPER and grants no native/Testnet/LIVE qualification.
