# PAPER native quote budget execution

Status: isolated PAPER implementation qualified for the tested slice; independent review and integration pending. This is a PAPER producer/source slice, not exchange, registry, cash-posting or whole-product qualification.

## Exported interfaces

`engine.paper_market_quote` exports:

- `provision_paper_quote(admin_dsn, roles)` — administrator-only additive SIM schema setup, after existing PAPER market provisioning.
- `submit_quote_creation(engine, write, request, permit, *, boundary, lease, owner)` — delegates to `PaperEngine.submit_creation`; creates no alternate authority and consumes no second permit.
- `query_quote_creation(engine, command_id) -> PaperQuoteReportV1 | None` — original-ID read, including after process recreation.
- Immutable `PaperQuoteTermsV1`: record kind, original scope/request/write/QuoteAssessmentArchiveV1, protected consumption hash, client ID, requested quote, native lot step, side, price bounds, quote asset, explicit fee rate/model and acceptance clock.
- Immutable `PaperQuoteFillV1`: fill identity, actual native base, actual venue price, gross quote, external quote fee and debit kind, units/assets and immutable market source event/hash.
- Immutable `PaperQuoteReportV1`: original command/request/terms, terminal status/reason, actual base, gross quote, unused quote, fills and evidence. `execution_authorized` is always false.

`execution.paper_quote_reports` exports `verify_quote_report(engine, report, *, boundary) -> VerifiedPaperQuoteReportV1`. The result contains the unchanged authentic report, original consumption hash and discrepancies; it is not a financial approval. This function reads restricted source state and the protected business consumption independently. A resealed DTO cannot substitute for either source.

## Execution and economics

Only Spot atomic native MARKET quoteOrderQty with standard routing, no trigger/iceberg/STP and explicit finite price/lot/fee evidence is implemented. BUY spends gross quote; SELL seeks gross quote receipt. Both consume actual opposing finite levels in price order, sharing `paper_market_levels` and the exact existing market advisory mutex with BASE execution. No original base quantity is invented from the budget interval. Existing 0013/0014 bytes and BASE order quantity constraints are unchanged.

Each fill is rounded down to the native lot step with exact PostgreSQL numeric `div`, then actual price × base determines gross quote. Neither fees nor net receipts become requested quote. Supported fees are explicit external-balance fees in the quote asset. Receipt fees, base fees and third-asset fees are blocked rather than treated as zero.

The bounded model makes one immediate attempt at acceptance. Exact gross-budget completion yields FILLED; remaining budget yields EXPIRED, including partial execution, out-of-band/no opposing liquidity and lot dust. Unused budget remains reported. No later event restarts a terminal quote attempt. These PAPER lifecycle semantics do not assert that a native exchange uses identical terminal statuses or that the economic target band was reached.

## Authority and recovery

The existing adapter holds the actual ExecutionOwner guard, consumes the real ProgramWritePermit once, reads the committed MAY_HAVE_SENT/original immutable request and protected consumption, and explicitly decodes QuoteAssessmentArchiveV1. It rechecks the current owner/fence and original permit deadline after the actual SIM lock wait and before commit. Missing evidence or a revoked/expired owner path rolls back SIM effects while preserving the original business consumption and recovery identity. A retry cannot consume the permit twice. Recovery queries the original command; it never creates a resized request or submits exchange-generated children.

Migration 0025 only adds quote terms, terminal runtime, allocations and a restricted reducer. Allocations reference existing immutable market inputs; the reducer updates existing shared depth. Runtime cannot update/delete terminal state or directly append allocations. The event role cannot execute quote acceptance. There is no new balance/reservation core. Provisioning is explicit and historical BASE-only deployments continue without 0025. Deploy additive schema before enabling quote production; retain schema and original records for restart/UNKNOWN recovery.

## Independent verification and boundaries

The verifier compares the supplied report against actual SIM source, binds the immutable request/write/archive to the protected consumption, replays the quote assessment, rebuilds exact terms, validates all quantities/prices/fee debit/currency fields, and checks combined BASE/quote allocations against immutable event capacity. Source-backed out-of-envelope facts produce discrepancies without resizing actual fills. Financial source registration and owned cash/inventory posting remain subsequent work; an exported verified-report DTO grants no authority by itself.

Final quote source `9e0650f5747390fc4d81cf534d04cd74ae2f7870`: **30 passed, zero failures/errors/skips** in a private PostgreSQL qualifier. Genuine source-identity hardening RED at `f1565ec` was 2 failed / 28 passed; original unsupported adapter RED is preserved at `a771049`. Existing BASE matcher 23/23, V2 compatibility 23/23 and V2 trusted source 51/51 passed at `fbcc961da616685f078cb9bd644fdde18f19add6`; subsequent production changes only bind quote source client/market rows. Final packaging removes terminal blank lines only, with no semantic source change.

[Committed evidence and launcher manifests](evidence/paper-quote-budget/) preserve exact source identities. Full implementer report: `.superpowers/sdd/2026-10-30-paper-quote-budget/task-1-report.md`. The qualifier used pinned offline runner image `sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7`, one private PostgreSQL instance per invocation and no host ports.

Remaining qualifications: static registry and owned financial posting; receipt/base/third-asset fee models; OS kill/restart campaign; ROOT physical-scope host integration; independent spec/quality review. Durable original-ID queries and actual lock-wait expiry/fence rollback are covered, but do not establish those broader qualifications or all 46/56 product acceptance rows.

## Review fix round 1: shared V2 source interoperability

Review BASE `f31784c`; fixed source `13c52d319eb572c12da7f81049db14ff6b267dbd`. The independent restricted V2 manifest includes both BASE and quote allocations, preserving the complete Counter and source bounds. Both verifiers now reuse exact V2 input-depth reconstruction. Non-depth events retain their archived prior capacity; they do not replenish liquidity. Quote allocations retain the current event identity/hash and authenticate the latest immutable depth dependency. Maker prevention consumption is included in conservation.

Personally reproduced the three independent counterexamples before fixing: genuine mixed snapshot, mixed carried empty delta and reference-before-quote all failed at the reported product stages. All three pass on fixed source. Original quote 30 and affected V2 source 51 final results are recorded in the implementer fix report and committed qualifier artifacts. No schema or frozen migration changes were needed. Native-parent revision `18fdef06`/0026 combined with this quote slice remains explicitly unqualified; ROOT owner changes and financial integration remain separate.

Fixed-source qualification completed: **mixed counterexamples 3/3, original quote 30/30, affected V2 trusted source 51/51**, all with zero failures/errors/skips at `13c52d319eb572c12da7f81049db14ff6b267dbd`. Committed `fix1-*` evidence directories retain original RED and GREEN launcher manifests/logs/XML. All original execution handles reached terminal status; no observation timeout was treated as a restart request. Independent scoped re-review remains pending.
