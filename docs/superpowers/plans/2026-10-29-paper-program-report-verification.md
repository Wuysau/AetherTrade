# PAPER programme report verification implementation plan

> **For agentic workers:** Use superpowers:executing-plans; preserve RED/GREEN and request independent review before integration.

**Goal:** Verify actual protected V3 PAPER submission/report sources and normalize exact fills/orders for the existing ledger, without inventing evidence or accounting.

**Architecture:** A trusted owner-host service takes command_id only. Read original immutable V3 identities and protected permit-consumption context from the same ledger; close that transaction before reading OP-D's isolated venue. Independently verify the full accepted write/request/compilation/rules/model/envelope and complete market allocation evidence. Archive original source bodies through the existing durable archive before returning typed SYNTHETIC facts and explicit discrepancies/duties. Existing EvidenceIngestor, FillLedger, reducers and the integrator's accounting remain the only ledger path.

**Tech Stack:** Existing Python dataclasses/canonical codecs, exact Decimal/Fraction arithmetic, real PostgreSQL and restricted PAPER venue.

**Spec:** `qualification/binance_orders/reference/CODEX_HANDOFF.md`, architecture sections10/11/14, `docs/orders/ADR-ORDER-001.md`, root assignment after reviewed original recovery2e30dfc.

## Global constraints

- Every application write still requires its own durable command/permit/owner. Observation never submits an exchange-generated child.
- Input is a command ID through trusted host readers, never caller-supplied reports or arbitrary transport callbacks.
- No fake legacy TradeIntent, second cash ledger, automatic release, FLAT/protection claim or native qualification.
- Native quantity and venue price in t.Fill remain unchanged. Carry FinancialUnit alongside them: ledger_units_per_native_unit, native_multiplier, ledger_multiplier and exact gross; native_multiplier = ratio * ledger_multiplier.
- Do not route generic fills through the old Spot q*price runtime.
- Raw overfills, fees and economic discrepancies remain unchanged and archived; never truncate them to an authorized envelope.
- Root integration_next owns financial/current kernels, host dispatch, shared schemas/migrations and final accounting. Parent owns only this independent verifier module/tests in binance-ope-management.
- PAPER only, no TESTNET/LIVE/account/model calls.

## Review focus

Cross-account/product/service/client/node substitution; missing or forged protected consumption; altered accepted source/envelope or quote currency; duplicate/overlapping liquidity across competing accounts; unit/multiplier, timestamp, cumulative/remaining and fee discrepancies. Each is pinned by a negative test below.

## Actual upstream and readiness

OP-D APIs currently expose PaperCreationReport/PaperAcceptedContext and
PaperMarketEvidence containing full market inputs, pre-event depth and ALL
competing allocations. Peer is correcting proven timestamp and concatenated
fill-ID failures, preserving actual native quantities for4000 quote LIMIT, and
adding the protected current-consumption execution envelope. Required additions
requested before DTO freeze: actual persisted venue_order_id; accepted clock;
full canonical internal fill raw_payload_json with its real SHA. No guessed old
payload location may be used. Wait for that concrete committed API before code.

### Task 1: Trusted source binding and exact normalization

Create `execution/paper_program_reports.py`. Public service constructor takes
existing UoW/scope and server-only creation-report/market-evidence/archive readers;
`verify(command_id)` reloads original creation and protected consumption and returns
a typed result with original raw observations, t.Fill/t.OrderProjection facts,
FinancialUnit bridge, exact gross, discrepancies and retained duties. Missing
readers or unsupported source contracts yield explicit unverified results, never
fabricated success. It issues no write permit or reservation credit.

- [ ] Write failing unit cases against the frozen actual OP-D dataclasses:
  original request/consumption binding, actual venue identity, LIMIT81490 versus
  source prices, exact native/ledger units, quote fees/zero-fee evidence,
  cumulative/leaves, raw source SHA and accepted/market/fill clock ordering.
- [ ] Run RED before implementation; implement independent checks and raw
  archive retention. No new compiler/risk replay for historical identity.
- [ ] Add RED for source-valid over-budget/overfill/raw fee discrepancies;
  preserve actual economics and required reconciliation without capping.
- [ ] Run relevant archive/recovery/native/codec regressions and commit.

### Task 2: Independent full liquidity evidence

Validate every included event's exact hash/product/instrument/stream/sequence and
pre-event finite nonnegative levels. Apply only the declared snapshot/delta
availability model, then sum ALL source allocations by event/side/price. No event
or allocation may be silently omitted, repeated or reused for infinite liquidity.
Each reported fill must match exactly one source allocation and raw event; the
opposite order side and original wire limit must agree. Bounds apply only to
received records; remote totals must not drive unbounded allocations.

- [ ] RED shared-depth overspend across two accounts, duplicate fill/native ID,
  missing competing allocation/source event, wrong price/side and replay.
- [ ] Implement verification with exact arithmetic and explicit source bounds.
- [ ] Real isolated PostgreSQL: actual financial admission → committed MAY →
  actual consumed permit → restricted venue match → query/source archive →
  verifier. Include restart and injected source discrepancies; no forced fills.
- [ ] Register mandatory suite through integrator; retain complete fixed-source
  PG results, manifests and byte-verified logs. Component tests are not native
  or full56-case qualification.

### Task 3: Existing-ledger integration contract

Propose concrete EvidenceIngestor/FillLedger/reducer consumption to root integrator
with persisted identity/raw references and both unit multipliers. No transaction
may remain open across venue I/O; facts, source validity and authorization are
separate. Missing account/history/fee/protection coverage retains duties. Root
implements the actual accounting/command-outcome transaction and public host
wiring; this module cannot declare the execution chain complete by itself.

- [ ] Independent read-only review, correct real findings through RED/GREEN.
- [ ] Publish source/evidence and integration signatures to root. Continue
  full-scope work as downstream contracts become available; do not mark the
  parent goal complete on this slice.
