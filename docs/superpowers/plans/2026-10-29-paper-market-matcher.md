# PAPER market matcher Implementation Plan

> Execute inline with superpowers:executing-plans. Parent performs integration review.

**Goal:** Complete OP-D against the full 46-capability / 56-scenario target, retaining the current isolated PaperEngine, commands, owner, business ledger, manual fault injection and synthetic provenance.

**Architecture:** Add a market producer to the existing synthetic venue; market inputs, shared depth consumption and order outcomes commit atomically in its isolated PostgreSQL database. Existing business admission and ProgramCommandBoundary remain the only financial/MAY/permit authority. The root integration writer owns ProgramCommandDispatcher, qualified source archive and normalization into the existing ledger/reducers; this branch owns actual venue execution and replay.

**Fixed base:** aff4aa8; sole writer in binance-opd-matcher. Integrator reserves additive SIM-only migration0013_paper_market_matcher.sql. No old schema, migration or role grants are rewritten.

## Approved API and persistence

- `PaperEngine.submit_creation(write,request,permit,*,boundary,lease,owner)` requires actual ApplicationWriteV3, VenueCreationRequest, ProgramWritePermit, ProgramCommandBoundary and ExecutionOwner. Hold actual owner.guard while consuming after committed MAY; returned request must match the immutable original. Business transaction is closed before the SIM side effect. The host also holds its independent side_effect_guard/revocation fence.
- `PaperEngine.query_creation(command_id)` returns immutable original account/product/namespace/client IDs, current remaining quantities and individual synthetic fills with price, per-asset fees and exact market-event references. Query never invents position/balance/protection coverage or releases cash.
- `PaperMatcher.apply_market_event(PaperMarketEvent)` accepts bounded ordered depth/source events, never requested fills. One transaction locks shared product/instrument liquidity and all affected orders, then atomically writes input, consumption, fills and lifecycle state. Duplicate exact events are no-ops; conflicting IDs and backward sequences deny.
- Reuse `paper_engine_orders`, `paper_engine_commands`, `paper_engine_events`. Add only persisted execution terms / programme metadata and market-input/book/consumption records. No second cash or owner ledger.
- Explicit initial depth model: FIFO among local accepted crossing orders, opposing visible depth, no hidden queue guarantee. Repeated snapshots do not replenish previously consumed levels; only a signed positive depth delta proves new model liquidity. A removed/reintroduced level remains exhausted until explicit replenishment. FOK checks the entire executable book before any deduction; IOC expires only its residual. No bar supplies an inferred path.
- Fees are an explicitly archived PAPER model, segregated per asset; no venue qualification follows from zero-fee fixtures or simulation.

## Execution and acceptance tasks

- [ ] TDD real isolated PostgreSQL external V3 acceptance via actual financial/MAY/consume, original IDs, query, forgery/reuse/owner expiry denial. No caller ALLOW, fake permit or manual-fill shortcut.
- [ ] TDD shared depth, un-crossed LIMIT retention, partial/multilevel fills, MARKET opposing quotes, GTC/IOC/FOK/post-only, duplicate/conflict events, two concurrent consumers and process restart. Evidence includes actual market input and liquidity before/after. ORD-01/03/09/12/38/48/51.
- [ ] TDD persistent LAST/MARK/CONTRACT_PRICE/BID_ASK triggers, priceProtect, trailing activation/extrema, GTD/DAY/session/calendar expiry, dynamic execution range, peg/priceMatch/iceberg and STP. Preserve unsupported/RPI/ambiguous-bar data restrictions. ORD-04/06/08/10/13/14/23/24/26/36/46/50.
- [ ] TDD list OCO/OTO/OTOCO/OPO/OPOCO full-fill and net-receipt activation, per-asset receipt fees/locked remainder, native versus software races, batch individual outcomes and cancellation/amendment/keep-priority. ORD-16..22/27/28/40/44/48.
- [ ] TDD publicly declared native TWAP/VP model and durable local scheduler pools with each application child independently admitted/durable/permitted. Native children are observations only. Stopped parent retains unresolved children. ORD-29..33/39/41/42/43/48.
- [ ] Integrate the root's actual host dispatcher and ledger source replay; verify financial holds, fills, protection duties and UNKNOWN across restart. Run legacy PAPER/role/fault suites and retain all 56 rows truthful. No partial cohort constitutes full OP-D completion.

## Verification

Use fixed-commit isolated Docker/PostgreSQL runs through tools/qualify_paper_docker.py. Every new behavior has an observed RED then GREEN; raw logs/JUnit/source and image digests are retained. Runtime role cannot write fills, matcher role cannot submit commands or edit business money. Defaults deny missing source/current authority. Existing manual injection remains a separate source and fault endpoint.
