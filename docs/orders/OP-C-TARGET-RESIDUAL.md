# OP-C target precision residual containment

The reviewed case has quantity step0.000001, absolute target1, actual inventory0.0000005, immutable BUY node0.999999 and explicit round-down permission. The shared TargetCompiler correctly preserves those numerical facts, but the first generic adapter returned no blockers even though target != actual + working + remaining. Existing ExposureCalculator already rejects that equation with DELTA_INCONSISTENT; the generic path must not lose this guard.

The adapter now emits TARGET_PRECISION_RESIDUAL_UNRESOLVED and EXPLICIT_RESIDUAL_AND_PROTECTION_ACCOUNTING_REQUIRED when a non-cancel-first result has a nonzero residual. It returns no node/request allocations. Cancel-first cases already remain blocked and are not mislabeled as rounding failures.

`ProgramTargetAllocation.target_residual` reports the exact quantity target − actual − working − remaining. It is a derived property over the preserved DeltaPlan, so persisted dataclass shapes do not change. In the reviewed case it reports0.0000005 BASE. The source proposal, compilation hash, target1, actual inventory and remaining0.999999 are unchanged; no target is rewritten to make the equation pass. An exact-grid control case remains allocatable. The legacy target/risk core is unchanged.

## Remaining contract work

An absolute net-position target does not inherently have to lie on the exchange's executable order grid: base-asset fees and other legitimate dust can place the current position or target between order steps. A future reviewed contract must explicitly distinguish the absolute position goal, executable order quantities, residual inventory and continuing protection/accounting responsibility. The system must not pretend that dust is flat or erase responsibility to admit a trade. Legal dust-plan capability remains a tracked future goal; this patch conservatively blocks until that contract and its proofs exist. It does not widen rounding permission or alter the existing absolute-target normalization rules.

## Verification

- RED: the dust case returned blockers=(); the exact-grid diagnostic property was absent (2 failures), retained in docs/evidence/binance-orders/op-c-target-residual/red.txt.
- GREEN:128 target/shared/legacy target tests passed;437 combined intent/target/risk/valuation/lifecycle/contract regressions passed, zero skips, both exit0.
- The regression directly confirms the unchanged ExposureCalculator still returns DELTA_INCONSISTENT for the preserved dust DeltaPlan.

No public schema, old risk gate, financial ledger, permit, dispatch, native/account API or operational configuration changed. Tests are offline Python checks, not PostgreSQL or venue qualification.
