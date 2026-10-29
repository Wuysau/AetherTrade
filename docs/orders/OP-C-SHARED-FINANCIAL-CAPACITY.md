# Shared incremental per-asset capacity calculation

Extract the existing IP-07 amount validation and capacity block into one typed
pure module, then use that same module at the same location in `_reserve`.
Keep `reservations.amounts` as a public alias. This is a financial sufficiency
calculation, never risk approval, a send permit or a second ledger.

TDD sequence:

1. Probe the existing reserve path for exact-decimal hold aggregation; retain a
   real RED if precision can hide a held debit.
2. Specify incremental required assets, net venue-free balances, remaining
   group/strategy capacities after external actual usage, buffers, reconciled
   group reservation projections and owner identity as distinct typed inputs.
3. Preserve each asset's `max(held-reflected, release_pending)` debit, all group
   held and same-owner held, missing-currency refusal and first denial code.
   Do not pass total program requirements or gross position limits into this
   arithmetic. Hold reconciliation/current-book watermark validation remains
   before this block; do not call it a second time or fabricate credit here.
4. Replace only the inline financial block. Keep RiskEvaluator, owner/clocks,
   target, closure, command checks, financial persistence and commit order.
5. Verify missing currencies, reflection/pending credit, group/strategy and
   cross-account scope, exact decimal behavior and old result parity with unit
   probes and the actual isolated PostgreSQL reservation/regression suites.

No generic financial intent persistence, sidecar, schema or authority is added.

## Implemented result

`risk/asset_capacity.py` provides `CapacityInputs`, `CapacityResult` and
`check_capacity`; `reservations.amounts` is the same imported function, preserving
its validation behavior for existing callers. `_reserve` calls this helper only
where the original capacity block ran: after risk and locked target checks,
before command validation/persistence. No owner, clock, risk, target, closure,
intent, command, reservation or commit step was moved. The new result indicates
financial sufficiency only and cannot replace any of those checks.

Callers must supply incremental assets, not whole-program totals. Venue-free
balances already exclude venue freezes. Group/strategy caps are remaining asset
budgets after external actual usage, with the supplied local holds not yet
deducted. Group held is deducted from the group cap, same-owner held from the
strategy cap, and `max(held-reflected, release_pending)` from net venue-free cash,
along with the buffer. Each currency is separate; missing evidence for a required
currency refuses it. Reconciled projections must come from the existing current-
book reconciliation path, whose watermark and unknown-hold behavior is unchanged.
The helper does not grant reflection, release or allocation credit.

The real old reserve path had a precision counterexample: free10**80+70 and
other-account holds of1 and10**80 could round away the small hold and commit a70
reservation although only69 remained. The regression invokes the actual service
with real PostgreSQL and existing ledger APIs, without replacing RiskEvaluator
or fabricating a permit/fill. Conservative pre-existing hold fixtures are not
claimed as qualified historical trades. The corrected helper uses the existing
256-digit parent-funding arithmetic boundary with rounding traps, rejects this
request with `INSUFFICIENT_CAPACITY:USDT`, and commits no new financial bundle.
Arithmetic outside that exact boundary raises `CAPACITY_ARITHMETIC_NOT_EXACT`
and the existing transaction aborts. RiskEvaluator's separate80-digit metric
arithmetic is unchanged by this extraction and remains a separate audit boundary.

Actual checks (zero failures/skips in final runs):

- Precision reserve-path PostgreSQL regression:1 passed.
- Existing PostgreSQL reservations:67 passed.
- Existing PostgreSQL duplicate-target:29 passed.
- Existing PostgreSQL two-phase admission:10 passed.
- Contracts/intents/unit regression:1021 passed, including26 new capacity probes.

Each PostgreSQL module used its own fresh isolated Linux/PAPER database. Raw RED
(old code incorrectly accepted70), corrected GREEN, regression logs, JUnit XML,
image IDs and source hashes are retained under
`docs/evidence/binance-orders/op-c-shared-financial-capacity/`. The first probe's
fixture omitted a source intent; its binding refusal is retained separately and
was corrected without changing the guard. Unknown/partial reflection, current-
book credit, currency segregation, other-account group holdings, owner limits,
legacy result cases and the public alias have executable unit coverage.

No migration, data rewrite or recovery upgrade is needed for this pure
extraction. Existing Schema/command hashes/UNKNOWN identities are retained.
General program financial adapters and their exact incremental risk metrics are
still to be implemented; no total-program or gross-limit adapter is silently
introduced here. No operational account, venue, model call, send or fill was used.
