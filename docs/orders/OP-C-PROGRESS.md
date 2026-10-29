# OP-C progress — first atomic target compilation boundary

OP-B verified source d5074c9 remains the contract/storage baseline. The first
OP-C code slice is intents/order_program_compiler.py. It validates exact rule,
capability, scope/build and submission revision references; preserves the entire
versioned proposal/program; uses existing integer-ratio/grid helpers to normalize
an atomic Spot LIMIT target at its stated limit, not a fresh arbitrary market
price. Fixture4000/81490/81330 remains exact for price/stop; quantity rounds down
only with explicit permission. It does not fill to the strategy ceiling.

This is deliberately a non-authorizing compile result. It is not an incremental
order and does not replace TargetCompiler, RiskEvaluator, ReservationService,
owner/permit, protection or native dispatch. Working/UNKNOWN/owned-position target
deltas and funding must be bound through those existing services next. Other
products, linked programs, modifiers and reductions remain explicit compiler
work; all46 targets and remaining55 full ORD scenarios stay tracked.

Next: bind normalized intent/complete program hash to compile_locked + reservation
transaction; distinguish native parent and local child command plans, exact
fees/assets/quantity semantics and group exposure. Then provide reviewed closure
proof for linked parent holds (currently conservative release guard), adapter
capability checks and durable permit dispatch. No runtime entry guard was removed.
