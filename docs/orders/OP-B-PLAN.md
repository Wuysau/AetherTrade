# OP-B implementation plan

Spec: approved ADR-ORDER-001 and user OP-B requirements. One writer per checkout.

1. Approve/archive ADR and inventory, retain all historical write gates.
2. RED/GREEN versioned AtomicOrderSpec/OrderProgram codec: strict variants,
   DAG/exclusive groups, explicit origin/namespaces and four distinct times;
   do not change four existing public schemas or their hashes.
3. RED/GREEN parent authority and child observation state reducer: proof-bound
   identity, immutable native IDs, fees, cumulative and remaining exposure,
   conflict/unknown/overfill/cancel-race => reconciliation, no resend/release.
4. Add additive PostgreSQL records and repositories using existing UoW/group
   lock, reservations, owner and risk-lock machinery. No parallel balance ledger.
   New-write disabled pending OP-C; old codecs remain original-version readers.
5. Real isolated PG migration/idempotency/restart/restricted-role tests; preserve
   previous safety suites. Record ORD-47 separately from other unrun ORD cases.
6. Pin independent native target artifacts (separate worktree), integrate reviewed
   manifest. No installed/latest/native readiness conflation.
7. Commit implementation, migration/recovery notes, actual results; OP-C starts
   only after OP-B contracts and evidence are complete.
