# OP-C lifecycle v3 approved implementation plan

Root approved the additive contract/pure reducer scope on2026-10-29. Existing V2 lifecycle bytes, schema hashes and storage containment remain unchanged. No production proof reader, qualification, command, permit, ledger release or transport is added.

- [x] Reverify official OCO/OTO/OPO and algo query semantics read-only; preserve frozen-V2 capability RED probes.
- [x] Define standalone2026-10-29 authority/binding/membership/unit/group/fee/observation/proof/state records, explicit registry kinds, exact public/package schemas and history checks.
- [x] Implement pure preparation, parent binding, membership and exclusivity-proof registration. Role-specific authenticated archive bytes are required; objects, booleans and model hints do not suffice.
- [x] Implement child/parent reducers: exact unit/multiplier, default summed exposure, independently proven native alternatives, observed fills/fees, watermarks and conservative reconciliation.
- [x] Implement full-fill receipt allocation with explicit fee source and owned-position debit, verified lot/dust facts, no automatic free-funds/protection claims.
- [x] Test nonunit/OCO RED-to-GREEN, proof forgery/missing/expiry, local overlap, actual dual fills, algorithms, unknown/races/time order, fees/receipts/protection and V2 history. Keep production containment/send denial.

The native-exclusive projection must never use min(raw_remaining, cap) to make an unproven claim fit. It may exclude other future alternatives only under an independently verified ONE_EXECUTING_MEMBER guarantee over exact bound native memberships. All raw reported leaves/status remain retained; sibling cancellation is not fabricated. Executions in more than one member invalidate the exclusive interpretation and require reconciliation using actual data.

Parent economic authority is immutable before ACK and has no venue ID. Later binding proves original command/client IDs and actual parent receipt. Local program activation has no invented native mother. Child membership is a separately verified mapping; child events never select arbitrary node/group identities.

Receipt and owned-position fee effects are distinct: an external base fee can leave an OPO receipt intact while reducing pre-existing owned inventory. Derivative collateral fees never reduce contracts. Unknown or source-sign-unresolved fee attribution is not zero.
