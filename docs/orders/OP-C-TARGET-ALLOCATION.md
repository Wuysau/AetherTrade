# OP-C shared target terms and program target allocation

This slice adds economic target terms and a non-authorizing program allocation. It does not add generic financial admission, reservations, durable commands, permits, account access or transport.

## One target arithmetic engine

`TargetCompiler.compile_terms(TargetTerms, BookSnapshot)` and the legacy `compile(TradeIntent, BookSnapshot)` enter the same existing `_compile` implementation. Positions, working leaves, UNKNOWN bounds, pending reservation quantities, cancellation conflicts, reversal checks, grid rules and partial reductions have one implementation.

`legacy_target_terms` validates the unchanged wire codec first. Legacy `compile_locked` still performs its existing owner, context and deployment fence checks. Legacy HOLD/UPDATE_EXIT retain the complete `validate_transition` responsibility validation. Generic terms currently accept SET_TARGET only; management is an explicit separate unsupported adapter, not a relaxed legacy projection.

The ledger unit restrictions remain BASE/CONTRACT/SHARE. Generic code cannot turn BTC into an implicitly accepted legacy unit. A native-to-ledger mapping must be supplied explicitly and consistently with the compiled node, rules, InstrumentSpec and source evidence.

## Program adapter

`compile_program_target(plan, book, facts)` accepts `ProgramCompilation`, `BookSnapshot` and `ProgramTargetFacts`. It creates no TradeIntent, MARKET/LIMIT stand-in or fake execution deadline. The host must load the facts from authentic local records while holding the existing collateral/owner transaction. This pure function checks their bindings; an arbitrary transaction ID or evidence label does not establish database authority.

The facts bind the complete compilation digest, exact versioned original proposal/program hashes, rule snapshot, actual book hash/sequence/basis, owner epoch/policy, DecisionContext, source evidence, current clock/monotonic deadline and validity windows. `NativeUnitBinding` explicitly supplies native and fixed ledger units, quantity ratio and both economic multipliers. Their relation must be exact; identical units cannot hide a scale change. Cash/share mappings remain unit scale. Contract lot mappings are accepted only with explicit consistent facts.

For quote targets, `TargetNormalizationPrice` is mandatory unless the target is exactly flat/zero. A declared fixed limit must remain the exact normalization price; a fresh market quote cannot silently replace81490 with50000. Other targets require an explicitly trusted mark. Quantity normalization uses the existing exact integer-ratio/grid helpers and explicit round-down permission. The original compiled requests and quantities are unchanged.

The result binds all input hashes, typed `TargetTerms`, the shared `DeltaPlan`, node allocations, original request plans, deferred obligations and blockers. `execution_authorized` is always false.

| Case | Allocation behavior |
| --- | --- |
| ATOMIC increase | Immutable compiled quantity must equal the actual remaining target delta. |
| Partial reduction / flat exit | Original side must already be the correct reducing side and the immutable quantity must match; no silent flip or resizing. |
| Working and pending commands | The common compiler subtracts their real quantity obligations once; currency holds are never converted into quantity. |
| UNKNOWN / cancellation conflict / satisfied target | Preserve the computed facts but return blockers and no request allocations. |
| Native/local TWAP or VP | One parent/schedule envelope; native mother versus independently admitted local children remain distinct. |
| Same-direction BATCH | Fixed node quantities must sum exactly to the shared remaining delta. |
| OTO/OTOCO | Working leg participates in the initial delta; protective pending legs remain AFTER_FULL_FILL obligations. |
| OPO/OPOCO | Pending quantity stays unresolved until full fill, actual net receipt and lot/fee recheck. It is never inserted into the initial delta. |
| CLOSE_ALL | Requires a flat target and the full unencumbered actual position. Record an internal quantity bound; do not insert a quantity field into the native close-position request. |

Every node must occur exactly once in the request set. Duplicated requests cannot spend one allocation twice. Native mothers containing deferred legs additionally require all contingent bounds to be resolved before the mother is sent; a deferred label is not permission to send an unvalidated native list. Existing protection responsibility and financial admission remain mandatory.

## Explicit unresolved semantics

- Native/local standalone OCO quantity envelopes are blocked: the existing scalar working-order reader cannot yet collapse proven native mutually-exclusive leaves. This implementation does not create a competing grouped target arithmetic core.
- Native quoteOrderQty/notional requests remain blocked for exact quantity-target allocation; a trusted normalization mark alone cannot guarantee their actual fill quantity.
- Nonprotective staged legs, contingent legs on a flat goal and fixed contingent quantities greater than the post-entry target are blocked. Reviewed multi-stage target goals are needed instead of inventing future targets.
- HOLD/UPDATE_EXIT and management-compilation admission are not routed through this SET_TARGET adapter.
- These blockers preserve all catalog goals; they do not delete capabilities or claim the entire OP-C financial path is complete.

## Verification and remaining gates

The shared-terms extraction is commit `5abfff5`, with177 target/legacy ingress/risk tests passing. The allocation follow-up ran125 target-specific tests and434 combined intent/target/risk/valuation/lifecycle/contract tests, all passing with zero skips. Evidence is under `docs/evidence/binance-orders/op-c-target-terms/` and `op-c-target-allocation/`.

Observed RED cases: missing typed terms/module; nonzero FLAT accepted by the first extracted terms; hidden scaling under an identical unit label; future context snapshot; duplicated requests consuming one allocation; nonprotective or oversized pending legs incorrectly appearing allocatable. These now reject or produce explicit blockers. Tests include all legacy products, partial reducers, native/local mothers, v3 VP, exact81490 quote normalization, real clocks, pending/UNKNOWN, unit/multiplier binding and no legacy-wire invocation from the generic path.

```text
python -m pytest tests/intents/test_program_target_allocation.py tests/unit/test_target_terms.py tests/unit/test_target_compiler.py -q --tb=short
python -m pytest tests/intents tests/unit/test_target_terms.py tests/unit/test_target_compiler.py tests/unit/test_risk_evaluator.py tests/unit/test_rule_evaluation.py tests/contracts/test_order_lifecycle.py tests/contracts/test_order_program_v2.py -q --tb=short
```

Both final commands exited0. Tests are offline Python tests, including existing in-memory orchestration probes; they are not PostgreSQL/crash/venue qualification. New locked financial wiring, authoritative fact readers, shared generic command-obligation scans and actual dispatch remain NOT_RUN here. No public schema, operational configuration, ledger money totals, native/account API or paid model was changed. TESTNET/LIVE are not enabled by this adapter.
