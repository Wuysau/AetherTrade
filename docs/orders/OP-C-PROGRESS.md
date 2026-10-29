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

## Existing binder correction — 2026-10-29

The original IntentCompiler/validate_proposal path now sizes QUOTE_NOTIONAL LIMIT
targets using the declared limit price, while MARKET keeps snapshot ask/bid.
This reuses the existing integer-ratio/grid helper and preserves entry/stop terms.
A red case reproduced0.080 rather than0.049 on a0.001 synthetic step for4000 at
81490; after correction294 target/compiler/wire tests pass. This is not a new
execution path or automatic migration of old accepted/UNKNOWN records. Existing
absolute intents keep their original values and hashes.

## Capability feature derivation — 2026-10-29

intents/order_features.py derives requested capability IDs from typed programs
for Spot, USD-M and real Equity. It distinguishes conditional type aliases,
trailing units, native list/execution algorithms, modifiers, quantity, TIF and
execution price rules. It grants no qualification or order authority. Spot
trailing PERCENT and market post-only were reproduced red then rejected; six
feature tests plus target/compiler regressions total80 passes.

Atomic admission integration is delegated to agent /root/opc_atomic_admission
in TradingHandoff/AutoDispatchWorktrees/binance-opc-admission, branch
codex/binance-opc-admission, starting91ec059. It owns new ingress/admission
sidecar modules and minimal transactional ProposalIngress/dispatcher hooks.
Root owns feature derivation. Do not duplicate that agent's changes; review and
integrate its commit, then run actual isolated PostgreSQL admission tests.
Persistent goal remains active through OP-G.
