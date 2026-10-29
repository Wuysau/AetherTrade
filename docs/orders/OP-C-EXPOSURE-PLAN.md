# OP-C program exposure plan

Approved delegated scope: derive typed group budgets from ProgramCompilation; no ledger, permit or authority changes.

- [x] Write failing tests using real compile_order_program fixtures.
- [x] Implement risk/program_exposure.py with exact per-currency budgets, native proven exclusivity versus local overlap, bounded conditional receipts, explicit margin/fee facts, and existing-obligation allocation proofs.
- [x] Test all templates, missing facts, native/local algorithms, UNKNOWN/positions/reservations, currency mismatch, unit/multiplier binding and Decimal precision.
- [x] Run own tests and compiler/valuation/exposure regressions; report actual boundary and commit.

Missing dynamic input produces explicit blockers and no usable zero requirement. Existing ledger reservation projections remain the money source; the new module only derives requirements. Maximum receipt credits are not spendable assets before full fill and authentic net receipt. Shared allocation proofs bind the complete consumer/hold set and upper envelope; absence never enables deduplication. All outputs retain source hashes and execution_authorized=false.
