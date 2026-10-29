# Shared deterministic risk core — first extraction

This slice reuses the existing RiskEvaluator. It introduces no second evaluator,
money ledger, generic admission, durable command or permit. No operational
configuration, account, native adapter or model is accessed.

`risk/terms.py` provides immutable execution-neutral RiskSource, RiskTerms and
RiskMetrics. The strict legacy adapter validates the exact2026-10-20 TradeIntent
class/codec and retains its canonical source hash, scope/authority, target,
leverage, loss bound, stop/trigger/protection, holding/review/submission deadlines.
It never fabricates a TradeIntent from a program. The existing evaluator uses
these validated terms for its common account/protection policy checks. Invalid
legacy sources cannot receive ALLOW; existing malformed-input DENY diagnostics
retain their previous rejection ordering.

Metric completeness/binding predicates require distinct gross/net/group,
planned-loss/fees/funding/capital, asset, protection, holding horizon and count
bounds, plus current complete financial coverage. None is missing. An empty
ProgramExposure-like object is insufficient. Typed source/facts/allocation/book
hashes, snapshot, owner-side book basis and expiry must match. These predicates
check content and completeness, not authenticity; only trusted local readers
under the existing transaction can establish that. No generic reader or entry
point is installed here, and construction or empty local violations grant no
financial authority.

The common functions in `risk/policy_checks.py` are called at the original
locations in RiskEvaluator:

- Cash/leverage/account-mode/position-leg policy.
- Required protection policy.
- Order notional, per-intent/requested/total planned loss, portfolio gross/net,
  related-group and position/order-count limits, with existing rejection order.
- Asset-specific spendable balances and conservative held/reflected/pending debit.
- Total capital including existing use, fees/funding and buffer.

Scope/auth/basis/evidence/freshness guards, price interpretation, target/delta,
stop direction/ticks, fee/funding and margin derivation remain in the legacy
evaluator. They were neither copied into another core nor removed. This is a
reviewable first extraction, not completed generic semantic risk admission.

## Exact arithmetic correction

A retained RED case proves the old80-digit spendable arithmetic could turn real
available106 into110: free10**80+107, then subtract a hold1 and a hold10**80, with
a required reserve107. The old evaluator returned ALLOW. New256-digit trapped
addition/subtraction rejects INSUFFICIENT_SPENDABLE. Precision beyond the exact
budget denies ARITHMETIC_PRECISION_UNPROVEN instead of producing rounded money.
Existing valuation/exposure helpers inherit traps, so an unproven intermediate
cannot silently round a loss, exposure or asset requirement downward.

Rational derivative margin division is the deliberate exception: its monetary
upper bound rounds toward positive infinity at the existing80-significant-digit
representation. Fraction-based tests prove bound>=exact quotient and that the
immediately preceding representable value is insufficient. Quantity sizing is
unaffected. This prevents both downward money rounding and needless denial of
valid nonterminating quotients.

Twenty complete decisions were captured from unchanged baseline9b28899 before
extraction. Nineteen remain byte-identical, including IDs/codes/coverage. The
leverage6 REPLAN case retains every field and hash except its old downward-rounded
diagnostic reservation, which increases one monetary ULP. The old golden record
is preserved and the test explicitly documents this safety correction; no
historical database row or stored hash is rewritten.

## Remaining boundary

Authentic program risk scenario derivation must still establish signed portfolio
net/group exposure, planned-loss and stop coverage across native/local activation
paths, full holding-horizon funding/exit fees, fee/margin adequacy and order-count
bounds. ProgramExposure cumulative executable gross is not a portfolio report.
Common scope/basis/freshness/price/stop stages can be extracted further without
changing legacy semantics. Versioned generic intent/risk/command persistence,
owner/permit composition and native lifecycle units/group authority remain
separate unfinished work. TESTNET/LIVE remain disabled; this does not promote any
full ORD venue scenario or remove any of the46 targets.
