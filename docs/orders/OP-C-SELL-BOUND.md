# OP-C SELL LIMIT gross execution bound correction

A SELL limit is a minimum acceptable execution price. Using it as the maximum gross notional underestimates exposure, including an opening USD-M short. Six failing tests reproduced this: Spot native OCO reported83000 rather than90000;100 futures contracts with multiplier0.001 reported8149 rather than9000; missing and contradictory ceilings were accepted; and an independently supplied higher ceiling was incorrectly rejected.

The compiler now keeps the declared request price and `sizing_price_bound` unchanged while deriving SELL `worst_case_quote_notional` from the explicitly bound product profile execution ceiling. An absent profile ceiling leaves that notional unresolved. An envelope ceiling below the SELL floor is rejected. BUY LIMIT still uses its declared limit as the upper bound. Quote-sized SELL retains the distinct minimum-price bound needed to budget base assets.

The exposure calculator accepts a current trusted `NodeBudgetFacts.price_upper`, bound through the full compilation/facts hash and validity interval. It must have the exact quote currency, cannot be below the SELL floor or shrink an already proven compiled ceiling, and may conservatively expand the gross bound. A deferred receipt SELL requires this explicit upper; it cannot borrow its limit as a ceiling. Missing, stale or conflicting inputs block the budget. Enforcement of the ceiling and fresh fee/margin checks remain explicit obligations: this calculation does not claim that a bare exchange SELL LIMIT itself enforces an upper execution price.

Historical dataclass shapes and all public schemas are unchanged. The compiler adds an internal `SELL_LIMIT_EXECUTION_UPPER_BOUND` required-check marker. An old persisted plan lacking this marker does not gain new ceiling semantics from its old floor-based notional. Its bytes/hash roundtrip unchanged; a fresh, explicitly trusted upper is required for a new budget evaluation and historical-bound revalidation is recorded. No historical plan is rewritten or automatically resubmitted.

Validation:

- RED:6 failed /56 passed, retained in `docs/evidence/binance-orders/op-c-sell-bound/red.txt`.
- GREEN:126 targeted compiler/exposure tests passed;309 related intent/risk/valuation/lifecycle/contract tests passed, zero skips, both exit0.
- New checks cover Spot/nativeOCO, USD-M openingSELL with nonunit multiplier, BUY cap preservation, quoteSELL sizing floor, missing/conflicting/freshness/currency bounds, deferred OPO quantity and historical roundtrip without implicit ceiling upgrade.

Commands:

```text
python -m pytest tests/intents/test_program_exposure.py tests/intents/test_program_compilation.py -q --tb=short
python -m pytest tests/intents tests/unit/test_risk_evaluator.py tests/unit/test_rule_evaluation.py tests/contracts/test_order_lifecycle.py tests/contracts/test_order_program_v2.py -q --tb=short
```

Raw summaries and JUnit files are retained beside the RED output. No native/account/model calls, operational configuration changes or public schema mutations occurred. Authentic bound verification, enforcement, locked financial admission and venue qualification remain separate gates; execution_authorized stays false.
