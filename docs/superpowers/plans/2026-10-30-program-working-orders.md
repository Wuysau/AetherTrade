# Source-backed programme working orders

> Executed inline in the assigned CASH worktree. Parent requested actual working-order projection/new bounded intake after real A/B posting. No subagents or ROOT writes.

**Bounded outcome:** Preserve authenticated working leaves in the existing order/book projections and prove owned+working target inputs without a new command. Actual owned.4+working.6 covers target1, but cost evidence remains incomplete: the existing compiler returns POSITION_EVIDENCE. The originally proposed TARGET_ALREADY_SATISFIED/NO_ACTION result was not claimed or forced.

**Architecture:** Separate PAPER observation service commits actual protected submission observation outside owner TX; owner TX rereads committed source snapshot/original command, original sequence1 financial bundle, exact FinancialUnit/funding link and current owner. Existing OrderProjector, ProjectionRepository, EventStore/outbox and book UoW are reused. Initial projection is an actual query snapshot. The explicit apply_observation interface separates aggregate event revision from projection row sequence while preserving the actual event at the verifier; old apply remains strict. Own book changes only active_orders/sequence/basis; positions/balances/holds/locks stay intact. Binding uses strict existing account reconstruction. No new money ledger, terminal/release/FLAT/protection/history claim or legacy runtime refresh.

**Scope:** Exact ATOMIC Spot PAPER V1/V2 sources only. UNKNOWN and source discrepancies retain prior projections/data and block current working-input authority. Existing generic write barrier stays. Public schemas, historic source bytes and old migrations are unchanged.

- [x] Actual PG REDs and fixes: nofill, partial, UNKNOWN, original bundle rescaling, source/book conflict, and final replay financial-status false completion. Failures retained.
- [x] Expose narrow committed snapshot reader; implement source/order service and readonly working-input mapping through original programme and physical ROOT IDs.
- [x] Prove actual unit/FillLedger bridge, mixed Fill1→Snapshot2(row1)→Fill3→Snapshot4(row2), event/source/row bindings, atomic rollback, replay and process restart.
- [x] Prove same-account peer no-write; historical GTC observations do not renew creation permits. Current intake preserves POSITION_EVIDENCE and creates no new command/hold/permit.
- [x] Confirm and fix higher-priority account-wide private-observed fill completeness with four genuine REDs, compact0018 indices and once-per-snapshot full verification; zero-fill and identical facts remain valid.
- [x] Run final owned11/working17 and related raw9/firstCapital3/knownness8/V2owned2/source16/scoped16/cash9/oldexecution37 PG; zero mandatory skips. Publish fixed-source originals and exact grouped review packet.
- [ ] Parent-assigned independent bounded review, then any review fixes. This checkpoint does not qualify missing cost/history/risk permission or full46/56.
