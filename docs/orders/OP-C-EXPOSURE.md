# OP-C typed program exposure and budgets — 2026-10-29

`risk/program_exposure.py::derive_program_exposure` consumes a real `ProgramCompilation` plus `ProgramBudgetFacts` supplied by trusted ledger/product/fee readers under the existing collateral and ownership locks. It is a pure calculation. It never creates a cash total, reservation, fill, command, permit, authorization, or execution path. `execution_authorized` is always false.

## Inputs and output boundary

Facts bind the complete compilation hash, exact scope/collateral pool, base and quote assets, provenance and validity interval. The node facts carry explicit fee upper amounts/debit sources, derivative margin bounds and unresolved quantity/price bounds. The compiled `quantity_unit`, `quantity_multiplier` and `notional_asset` are preserved and checked; USD-M multipliers are never assumed to be 1 or applied twice.

`None` means missing and blocks evaluation. An empty tuple is a trusted assertion of no such consumers/fees, not a fallback. The caller must prove completeness/authenticity of all supplied dynamic facts; this module does not turn an evidence label into authority. It must not be called with model-authored financial assertions.

The result retains compilation/facts hashes and supplies typed per-asset `program_requirements`, `additional_requirements`, `existing_requirements`, `total_requirements`, gross notional bounds, conditional receipt obligations and blockers. Missing/invalid facts yield `None` requirements, not usable zeroes. Capacity shortfalls retain calculated amounts for review but include blockers.

`asset_limits` are total gross budget capacities BEFORE the supplied positions, working/UNKNOWN orders and ledger holds. They must not be venue free balances already reduced by those same holds. Existing group/strategy/authorization/balance admission remains authoritative. Gross reporting is a conservative cumulative executable-notional envelope, not a netted position forecast, P&L or replacement for `ExposureCalculator`/target-delta admission. Existing `Money`, `Quantity`, reservation projections, `Valuation`, `nonnegative` and reservation `amounts` helpers are reused.

## Allocation rules

| Pattern | Calculation |
| --- | --- |
| ATOMIC / BATCH | Per-node principal/margin and fee bounds; independent nodes sum. |
| Native OCO | Per-asset maximum and maximum gross only with an exact-node atomicity proof. Without proof, use overlapping sum. |
| Local OCO | Both legs sum; native atomicity claims are rejected. A funded overlap is not permission to execute both legs. |
| OTO / OTOCO | Full-fill activation remains explicit; working and pending requirements remain funded. No future receipt credit is inferred. Native proven pending exclusivity uses max; local pending legs sum. |
| OPO / OPOCO | A single locked receipt source is allocated once to derived pending quantities; net interval includes base fees deducted from the working receipt. Native proven pending exclusivity shares it; local overlap needs additional base capacity. |
| TWAP / VP | The compiled mother envelope counts once. A native mother or a local staged child schedule is not an extra duplicate principal. Existing actual children require complete allocation-envelope evidence before deduplication. |

OPO receipt maxima are risk bounds, **not authorized sell quantities**. Pending sizing stays unresolved in the original compiled plan. Full fill, authentic actual net receipt, locked funds, actual fees and lot-size checks remain mandatory at activation. No partial-fill protection is implied; incompatible per-fill protection policies block these full-fill-only plans. Conditional receipt credit never enters current spendable balances.

`FeeBound` distinguishes `EXTERNAL_BALANCE` from `FROM_RECEIPT`. Quote/third-asset fees paid externally stay in their own currencies. Buy base fees deducted from acquired assets reduce the receipt interval; sell quote fees deducted from proceeds do not invent an unrelated quote funding requirement. Impossible receipt fee bounds are rejected. Actual receipt fee checks remain obligations. Market quote-sized sells need a caller-proven base upper quantity consistent with the compiled minimum-price envelope; no quantity is silently rounded up. CLOSE_ALL requires trusted actual quantity and price bounds.

Positions, working orders and UNKNOWN consumers require explicit asset and gross-exposure upper bounds. Missing UNKNOWN bounds cannot become zero. Ledger reservations count `held` once: reflected/release-pending are subsets, not extra money or automatic release credit. Same-pool other-account holdings are counted conservatively; foreign pools are rejected.

`AllocationEnvelope` is a trusted complete cumulative-plus-remaining proof over exact consumer/hold IDs. It must cover the parent and the SUM of covered children/positions; cannot reuse a member or hold across proofs; and cannot combine parent/child scopes. Its capital consumption is the larger of that complete bound and backing ledger holds. A matching already-funded program additionally binds exact scope/hash/native-or-local origin before reducing additional requirements, and emits an original-ID/no-resubmission obligation. Without proof, consumers and holds sum conservatively.

FX conversion only reports liability-valued gross exposure. It never makes USD into spendable USDT or pays a BNB fee from USDT. Decimal operations use explicit precision with inexact/rounding traps; precision loss in the reused 80-digit valuation helper blocks instead of silently rounding.

## Verification

Executed with the existing Python 3.14.2 test interpreter; no dependencies or operational configuration were changed:

```text
python -m pytest tests/intents/test_program_exposure.py -q --tb=short
python -m pytest tests/intents tests/unit/test_risk_evaluator.py tests/unit/test_rule_evaluation.py tests/contracts/test_order_lifecycle.py tests/contracts/test_order_program_v2.py -q --tb=short
```

Results: **56 passed** targeted; **299 passed** combined regression, both exit 0 with no skips. Raw summaries and JUnit results are in `docs/evidence/binance-orders/op-c-exposure/`.

Observed RED cases included the missing module; unsupported Spot BATCH fixture (moved to supported USD-M without weakening the compiler); cross-account identical-program dedup and mixed-scope parent/child allocation (both initially incorrectly accepted); and a base receipt fee larger than its source quantity (initially accepted). These now fail closed with explicit blockers. Tests cover every allocation pattern, native/local algorithms, receipt fees, margin/units/multiplier, stale inputs, currencies/FX, UNKNOWN/positions/holds, no double allocation and exact large/subdecimal arithmetic.

The 46-target catalog and public schemas remain unchanged. This is not a claim that every target is financially admissible or natively qualified. Locked integration into existing target-delta/risk/reservation/dispatch, authentic dynamic-fact readers, database/crash integration and exchange qualification are **NOT_RUN here**. TESTNET/LIVE authorization is not added; no account/native/model calls were made.
