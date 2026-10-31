# OP-C remaining integration sequence

This continues the approved ADR and46-feature target. It is implementation
sequencing, not new approval or evidence that execution is complete.

## Current facts

- Generic creation/management compilers preserve typed requests but do not grant
  authority. ProgramExposure calculates bounds and likewise grants none.
- Atomic V2 Spot LIMIT admission now has bounded intake origin, fresh owner
  validation and the existing IP07 atomic bundle. Ten actual isolated PG cases
  pass at3fd6011; this does not cover generic programs or venue matching.
- Parent closure and credit guards use existing reservations and independent
  approval.0011 preserves0009 and refuses unreviewed historical credit.
- V3 program storage already works through OrderProgramCodec delegation. The
  additional storage work makes dispatch/version limits explicit and contains
  V3 reports incompatible with V2 lifecycle authority; it is not a storage
  support claim invented from hardcoded call names.
- Current legacy target/UNKNOWN/queued-reduction readers only see old commands.
  Cross-version obligation integration is being implemented separately.
- Current lifecycle reducers have scalar child allocation and quantity×price
  accounting without explicit native quantity multiplier. They cannot safely
  admit all futures or native exclusive-list graphs. Inflating parent authority
  to admit a second OCO leg is forbidden.

## Ordered implementation boundary

1. Finish explicit version routing and unified command-obligation readers.
   Preserve original table/kind/scope/service identity and hashes. A raw terminal
   state is not authenticated reconciliation. Missing allocation must block
   rather than invent a legacy command or quantity from cash holds.
2. Bind an explicit ProgramTargetAllocation under existing collateral/owner
   locks. Reuse TargetCompiler arithmetic and current source/fence checks, with
   real generic target terms rather than a fabricated MARKET/LIMIT TradeIntent.
   Bind absolute target, actual/working/pending, unit/multiplier, book basis,
   proposal/plan/rule hashes and exact per-request allocation. Mismatching
   immutable node quantities require replanning; no implicit graph scaling or
   BUY-to-SELL rewrite. Partial reduction and flat exits use genuine signed
   delta and owned available quantity. Receipt sizing stays deferred.
3. Extract shared IP07 admission orchestration behind legacy and generic intent
   adapters. Keep one group mutex, owner fence, authorization/revocation checks,
   clocks, risk locks, limits, ledger reservations and atomic commit barrier.
   Generic loss/funding/fee/protection risk must be evaluated explicitly;
   ProgramExposure alone cannot replace risk approval.
4. Add explicitly versioned lifecycle authority/state for native units,
   multipliers and proven exclusive/shared allocations. Retain V2 codecs and
   IDs. Native child observations must prove membership, account/service and
   aggregate envelope; application child writes remain individually durable.
5. Commit the full intent/allocation/risk/reservation/command/source bundle.
   Funding links must exist before a mother can send, not first appear at ACK.
   Bind the actual request/allocation/management hashes as well as program hash.
   Original-ID UNKNOWN recovery and authoritative unsent-abort proof need both
   record kinds. Only then replace the explicit unimplemented send boundary.
6. Integrate amendments and cancel-replace with current owned leaves, overlap
   budgets, distinct outcomes and continuing protection. A cancel/stop ACK never
   releases a child responsibility. Then complete OP-D/E/F and full OP-G tests.

Each step uses failing scenarios first, then executable correction and actual
isolated PostgreSQL checks. Cross-version UNKNOWN, simultaneous targets, partial
reductions, mother/child funding, v3 restart, pre-ACK crash and mixed management
outcomes remain required integration cases. Full ORD statuses are not promoted
by these component checks. No TESTNET/LIVE/account/model-call authorization is
introduced by this sequence.
