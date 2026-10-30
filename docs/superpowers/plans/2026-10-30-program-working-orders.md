# Source-backed programme working orders

> Execute inline in the assigned CASH worktree. Parent requested actual working-order projection/new bounded intake after real A/B posting. No subagents or ROOT writes.

**Goal:** Preserve authenticated working leaves in the existing order/book projections, then demonstrate owned+working TARGET_ALREADY_SATISFIED without a new command.

**Architecture:** Separate PAPER observation service runs actual protected submission observation outside owner TX; owner TX rereads committed source snapshot/original command, exact FinancialUnit and current owner. Use existing OrderProjector, ProjectionRepository, EventStore/outbox and book UoW. Initial projection is an actual query snapshot, never a fabricated initial state. Filled cumulative native facts require actual FillLedger. Own book changes only active_orders/sequence/basis; positions/balances/holds/locks stay intact. Strict existing cash binding is optional only when full account reconstruction is valid; otherwise no spendable grant. No new money ledger, terminal/release/FLAT/protection/history claim or legacy runtime refresh.

**Scope:** Exact ATOMIC Spot PAPER V1/V2 sources only. UNKNOWN and source discrepancies keep prior projections/data and block current working-input authority. Other execution families remain explicitly blocked. Public schemas and historic source bytes remain unchanged.

## Steps

- [ ] Add real PG REDs: accepted-unfilled yields working1 with no fill/position; actual .4 owned+.6 working and new bounded intake yields same target NO_ACTION; replay preserves sequences; unknown query retains .6 and denies current input; forged book projection/owner/source selection denies.
- [ ] Expose a narrow existing committed snapshot reader, preserving original verifier/cache/context checks. Implement new observation service and readonly working-input mapping through actual original programme/physical ROOT IDs.
- [ ] Verify actual source/FillLedger/unit bridge and use existing reducer/event/projection writes atomically. Existing protected source is authority, owner marker/DTO alone never is.
- [ ] Verify no new financial/send/release row in NO_ACTION, old permits remain single-use and source UNKNOWN/cost/protection/history duties remain. Run affected source/raw/owned/first-capital/knownness/V2 PG once stable and publish exact source-bound failures/greens.
