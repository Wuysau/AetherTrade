# Explicit position-value band source and lossless native quote compilation

New contract revision2026-10-30 introduces POSITION_VALUE_BAND_PROPOSAL_V1.
Its goal is an explicit ABSOLUTE_POSITION_VALUE_BAND with side, minimum/maximum
quote amounts and a declared reference-price valuation basis/multiplier. It
retains SET_TARGET. The old scalar QUOTE_NOTIONAL proposal and original codec
bytes remain unchanged. No turnover goal or tolerance is inferred from them.

A desired lower band is not a fill/completion claim. Current inventory, working
and UNKNOWN quantities, zero and partial fills and continuing management remain
requirements of the pending interval allocation and financial assessment.

`compile_quote_program` reuses the actual shared product/rule/field compiler and
returns an explicitly versioned QuoteProgramCompilationV1. Spot MARKET BUY/SELL
retain the original quoteOrderQty and real equity MARKET BUY retains notional,
quoteAsset and selected wallet, tokenize=false. The declared valuation reference
never resizes these requests into a base quantity. Real equity SELL notional
continues to fail its existing quantity-only product rule. Currency/multiplier
bindings remain explicit. The first compiler cohort covers atomic native quote
requests; advanced composition and remaining full catalog work remain active.

Observed RED:7 source and3 compiler tests failed because their modules did not
exist. Current focused59 source/compiler/math/request/admission tests pass,
including new Spot BUY/SELL and equity BUY/invalid SELL cases. New source
schema resources are generated for the explicit new type in both public and
packaged contract directories; they do not modify old schemas.

These are expression/request components only. Authentic enforced interval facts,
actual position-band/working/UNKNOWN allocation, same risk calculator scenarios,
wallet financial location, per-asset fee/dust responsibilities, new interval
financial kind/archive, shared financial admission and fresh PostgreSQL/restart
proof are still required. No source object/compilation grants execution or
native qualification. The full approved interval plan and46/56 scope remain in
OP-C-QUOTE-INTERVAL-PLAN.md.
