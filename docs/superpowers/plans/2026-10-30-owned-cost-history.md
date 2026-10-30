# Source-backed owned cost/history implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Assigned worker implements Task 1, no subagents.

**Goal:** Derive authentic cost/history for fresh Spot ownership epochs from complete native executions, allowing current target arithmetic to use known positions without invented coverage or PnL.

**Architecture:** ROOTa799f19a2b963d33f9d638ffdd08d68d8b183ee9 already has the reviewed source/FillLedger/owned asset and quantity posting, working-order projection, private observed-fill completeness and thin isolated host. Owned posting deliberately leaves cost/history incomplete; TargetCompiler correctly rejects POSITION_EVIDENCE. Add a separately versioned source-derived cost/history proof using those existing facts and ownership/event/UoW stores. Keep all old posting facts/hashes immutable and all current risk/permission barriers.

**Tech Stack:** Existing Python exact arithmetic, PostgreSQL, private source snapshots and isolated Docker PAPER qualifier.

**Spec:** Approved trading architecture ManagedPosition/cost-evidence contracts and SAFETY_STATE_MACHINES reconciliation step8; Binance architecture exposure/accounting/recovery; ADR-ORDER-001 and ORD-11/40/43/47/49. Complete46/56 remains required.

## Global Constraints

- No coverage flag or owner audit JSON is evidence by itself. Require complete authenticated native source, actual FillLedger, original issued command/unit, exact owned transition history and an independently justified opening ownership/cost baseline.
- Initial account assets, especially unowned BTC10, never become strategy inventory or a zero-cost lot. A nonzero/ambiguous old epoch or legacy history stays explicitly blocked; no retrospective invented prices/fees/history.
- Reuse existing asset ledger/cashflows, RegistryTx, ManagedPosition, EventStore/projections/UoW, exact arithmetic and risk/TargetCompiler. No second cash balance, duplicate fee debit, fabricated INTERNAL/EXTERNAL flow, nested UoW or other-scope write.
- Preserve source approval schemas/hash/event IDs, old native/owned posting records and UNKNOWN/original commands/permits. Cost reconciliation mints no permission, renews no old token, releases no reserve, declares no FLAT/protection success, and never deletes risk locks.
- Existing POSITION_EVIDENCE and generic new-write structural guards remain. Only genuine verified new cost evidence can satisfy the former; new-risk permission is a later typed-boundary qualification.
- PAPER only; no LIVE/TESTNET/accounts/paid product models/public push/main/archive edits/cleanup/deleted or skipped safety cases/risk relaxation.
- No frozen migration rewrites. Optional new0030 BUSINESS is reserved if a separate protected zero-epoch/cost attestation is genuinely required; coordinate any other number. Existing0027/28/29 belong to management elsewhere.

## Review Focus

- Source-backed zero-owned genesis is distinct from a caller-created zero owner. Missing/late/ambiguous genesis cannot manufacture complete cost history.
- BUY/SELL/partial fills, receipt versus external fees and exact native-to-ledger units preserve actual net inventory, remaining cost and realized effects. Unknown fee/FX/source ordering stays unknown; no silent rounding or implicit conversion.
- Duplicate native identities/fresh query clocks do not duplicate costs or fees; same-ID economic conflict, unrecorded observed leaves and missing earlier source reject.
- Old quantity posting proofs remain verifiable after a separate cost-only revision. A forged COMPLETE coverage/PnL/owner row cannot bypass source-derived current evidence checks.
- Actual owned.4 + authentic working.6 at target1 can reach genuine NONE/NO_ACTION only after required evidence is complete; no new financial/command/permit record or old-permit renewal follows.

## Task 1: Verified fresh-epoch Spot cost and history projection

**Files:** New `execution/program_cost_history.py` and focused cost math helper if needed; narrow ownership/book/current-evidence integration; additive0030 only if required; `tests/integration/test_program_cost_history_postgres.py`, pure arithmetic cases and mandatory qualifier entry; `docs/orders/OP-E-COST-HISTORY.md`. Do not modify Root/old Cash trees or lower TargetCompiler requirements. Preserve schemas; any new proof/state uses an explicit static version/tag.

**Interfaces:** Reuse ProgramAssetPostingReader/CommittedProgramFillSources/FillLedger, original sequence1 bundle and private consumption, require_owned_program_posting, RegistryTx and current working inputs. Define actual exported signatures in task report before implementation. Reconciliation updates only the caller's authorized current cost/position/book revision in one existing UoW. Every current proof must be independently rederived; a cached ALLOW or public marker is never authority.

- [ ] **Step 1: Baseline and genuine RED.** Inspect current owned writer and target reader: program_owned_posting.py deliberately sets PROGRAM_COST_HISTORY_RECONCILIATION_REQUIRED, realized/unrealized None; target_compiler.py requires complete cost_evidence. Preserve existing working tests' correct POSITION_EVIDENCE result. Add a fresh zero-owned, genuinely provisioned/authorized Spot case with actual funded order, finite native fill, source/ledger/owned/FEE/order/book chain and an attempted cost reconciliation. Observe the actual missing implementation before changing coverage.
- [ ] **Step 2: Baseline and complete-history proof.** Establish a true source-backed zero-owned epoch baseline (prefer existing protected first-financial/ownership/source facts; otherwise explicit independent zero-genesis authority before trading, not runtime owner self-certification). Bind every current-epoch fill to original source/unit/owner and exact committed owned effects. Reject initial unowned assets, earlier gaps, transfers/interventions or nonzero starting history without cost evidence. Do not call incomplete legacy history complete.
- [ ] **Step 3: Exact economics.** Use an explicit versioned Spot weighted-average economic-cost policy unless approved project semantics already prescribe another method. Derive buys, partial sells and remaining cost from actual gross/asset deltas/fees; preserve raw amounts and do not charge fees twice. Use exact rational arithmetic where division recurs and declare any representation/rounding residual; no tolerance-based FLAT. Realized/unrealized output requires sufficient price/FX/ordering evidence; leave unsupported valuation unknown and explain it instead of assigning zero. Current quantity/history evidence must remain distinct from valuation readiness.
- [ ] **Step 4: Existing core projection and actual PG.** Append separate cost proof/revision with same-TX event/outbox/current owner/book linkage; never mutate old V2 quantity-posting rows. Revalidate it on current reads so a forged COMPLETE/PnL/owner marker fails. Test actual BUY/SELL partial cycles, base/quote fee economics supported by authentic sources, duplicates/new clocks, source/economic conflict, missing genesis/history/FX, wrong owner/unit/epoch, intervening fill, rollback/restart/replay and same-account peer nonmutation. Prove existing target reader can return actual NONE for owned.4+working.6/target1 when fully evidenced, without new command/reservation/permit or safe-action bypass. Missing real protection/other risk evidence remains a separate block.
- [ ] **Step 5: Fixed-source regressions and review.** Run new mandatory PG plus affected owned/working/cash/source/current-target cohorts after a stable implementation; no blanket old broad reruns for documentation. Commit real RED/final log/XML/manifests, exact exported interfaces, method/baseline/rounding policy and additive migration/recovery notes. Deliver focused BASE-to-HEAD package and precise remaining non-Spot/legacy/valuation/protection/new-write qualification. Parent arranges independent review; no host/default activation.
