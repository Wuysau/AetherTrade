# OP-D first market execution interface

This is an actual first execution slice, not completion of OP-D or qualification
of all46 capabilities/56 scenarios. Full scope remains in
`docs/superpowers/plans/2026-10-29-paper-market-matcher.md`. The functional API
checkpoint is11ff5df; its final fixed-commit verification is running. Prior
whole17-case matcher and legacy/Python cohorts passed at4d84ece, before the
nonfill disposition export was added. No native, TESTNET or LIVE reader/transport
is registered by this branch.

## Actual composition and boundaries

`PaperEngine(...,matching_model=PaperMatchingModel(model_id,quote_fee_rate))`
retains the original constructor/manual injection behavior. Its new methods:

- `submit_creation(write,request,permit,*,boundary,lease,owner)` requires actual
  ApplicationWriteV3, VenueCreationRequest, ProgramWritePermit,
  ProgramCommandBoundary and ExecutionOwner. Genuine MAY and independent
  single-use consumption commit before the SIM transaction. After actual SIM
  advisory waits and before SIM commit, the same permit's wall/monotonic deadline,
  actual owner/lease, source fence and book are rechecked. No permit is renewed.
  The root host additionally holds its side-effect/revocation guard. Failure
  rolls back SIM writes and preserves the original MAY/consumption/query duty.
- `query_creation(command_id)` is scope/account/product bound and returns
  PaperCreationReport or None. It never infers funds release or complete account,
  position or protection coverage.
- `query_market_evidence(command_id)` is a server-only typed archive. It covers
  exactly the command's causal fill and disposition market IDs, and ALL competing
  allocations for those IDs. It is not a public cross-account query endpoint.
- `PaperMatcher(engine).apply_market_event(PaperMarketEvent)` consumes actual
  ordered depth inputs in one isolated SIM transaction with all affected orders.
  It takes no caller fill quantities/prices or authorization booleans.

Migration0013 is SIM-only. Existing paper_engine_orders/commands/events remain
the venue records. New tables hold immutable market inputs, accepted execution
terms and liquidity consumption, plus current depth. paper_matcher_definer is
NOLOGIN/NOINHERIT with no memberships; its narrowly exposed SQL function has a
fixed search_path and no PUBLIC execute grant. Runtime cannot write market
sources/fills or alter accepted terms. The function accepts only scope/client,
derives execution from persisted terms and controller depth, and atomically
writes consumption, fills and order projections. No business cash/owner ledger
is created. The additive provisioner is `paper_market.provision_paper_market`;
the old provisioner and old migration are unchanged.

## Report/source contracts

`PaperCreationReport` includes the full unchanged request, orders, fills,
accepted_contexts, dispositions and a SYNTHETIC evidence reference.

- Each `PaperMatchedOrder` contains actual persisted venue_order_id, original
  client/node/namespace, quantity/unit, cumulative filled and executable remaining.
- Each `PaperMarketFill` contains stable structured IDs, native unit/price asset,
  gross notional, complete actual per-asset FeeV3 vector, exact market ID/SHA,
  execution occurred_at, raw_payload_json and evidence. The raw string is
  canonical_json of the actual SQL JSONB payload, with every producer field;
  SHA256(UTF8(raw_payload_json)) equals evidence.hash. It is synthetic internal
  venue data, not fabricated Binance wire data.
- Nonfill `PaperMarketDisposition` includes status/reason, causal market ID/SHA,
  raw canonical payload and evidence. EXPIRED/REJECTED without fills still retain
  their original venue identity and source event.
- `PaperAcceptedContext` includes the actual original write, request, compilation,
  rules, explicit model, normalized PaperExecutionTerms and PaperExecutionEnvelope.
  Terms use the actual compiled native quantity cross-checked against unchanged
  request.quantity, preserving the original QUOTE_NOTIONAL proposal. They include
  the actual accepted_clock.
- The envelope is derived ONLY from the protected, committed
  ledger.program_permit_consumptions.context.assessment. It binds scope, command,
  request, complete consumption_context_hash, assessment_hash, exact source_ref,
  node, finite min/max when known, gross ceiling and full asset fee ceilings.
  Current assessment economics replace no original wire fields. SELL limit is
  never treated as an upper execution price; fee bounding uses the proven upper
  bound. Missing bounds cannot authorize a quote-MARKET interpretation.
- `PaperMarketEvidence.inputs` contains typed raw event, exact raw_payload_json,
  event_hash, actual before_levels and observed_clock. Raw market occurred_at
  remains unchanged. Fill execution time is at least causal acceptance, source
  observation and raw market time. `consumptions` contains market/event, original
  scope/client/fill IDs, book side, exact price and native quantity for all rivals.
  No per-scope fresh copy supplies duplicate liquidity.

Root normalization independently compares these bytes/context against the real
protected business consumption and full immutable original request. Hashes or
DTO construction alone are not authority. Root owns normalization and existing
ledger/reducer ingestion; this engine owns venue facts. An unbacked filled
projection fails source validation. Unknown or oversized evidence blocks rather
than releasing any financial/management obligation.

## Model and bounded archive

Initial actual execution supports fixed LIMIT/LIMIT_MAKER, GTC/IOC/FOK/GTX,
shared visible depth, partial/multiple-level fills, FIFO local acceptance,
exact replay/conflict rejection and process restart. FOK preflights full depth,
gross and fees before any consumption. The original4000/81490/81330 fixture
executes its compiled0.049085 native quantity, retaining the quote source.

Snapshots never replenish an already consumed/removed level; only explicit
positive depth delta supplies new model liquidity. A delta must be contiguous;
snapshots may recover sequence gaps. No LAST/bar value supplies unlimited depth.
The explicit first fee model charges quote fees from external balance; base
receipt/third-asset/equity fee models remain planned, not falsely qualified.
All actual events retain their per-asset vector and fee bounds.

Named PostgreSQL cursors fetch evidence one record at a time. Counters apply
before append:4096 causal events,100000 competing allocations TOTAL,16MiB total
market evidence,1MiB individual raw event,4096 order events/16MiB serialized fill
and disposition payload. Distinct current depth levels are bounded at8192.
Exceeding bounds raises a specific error; history is never silently truncated,
deleted or represented as complete. Pagination beyond these bounds is not yet
implemented.

Remaining full OP-D work includes market/quote-interval integration, trigger
sources and priceProtect, trailing/extrema, peg/priceMatch/iceberg, dynamic
execution/session/RPI rules, STP, lists/net receipts/native versus local races,
amend/cancel variants, native algorithm models and independently authorized
local scheduling. Host end-to-end ledger/protection integration is a separate
actual gate. Each remains tracked; no first cohort substitutes for the full goal.
