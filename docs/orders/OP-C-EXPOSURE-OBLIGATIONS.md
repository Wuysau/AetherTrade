# Shared exposure obligations — generic risk prerequisite

ExposureCalculator remains the single portfolio arithmetic engine. Empty pending
and proposed inputs retain legacy semantics. No order, TradeIntent, command,
position or fill is fabricated by these projections.

PendingExposure carries an explicit signed ledger quantity, physical scope,
original command references, source family and service namespace. Deduplication
uses the full physical/source identity; equal strings in different products,
instruments, legs, families or namespaces do not collide. Already-represented
orders require zero extra quantity and an exact physical order mapping. The
trusted host reader must establish these links; the type itself grants nothing.

ProposedExposure carries original node ID, exact quantity and bounded valuation
mark. Its quantities must sum to delta.remaining, have the same direction and
scope, and preserve the established currency/unit/multiplier/valuation group.
Distinct entry fill bounds remain distinct; no weighted-average rounding or
current-ask substitution is introduced. Retained inventory keeps its current
mark. Reductions value physical remaining inventory once: a full flat exit has
zero gross/net exposure even when sale price differs from the position mark.
That difference is P&L, not a new short position or residual exposure.

These are conservative portfolio projections under explicit source assumptions,
not accounting updates, cash budgets or proof that an order can fill. Generic
assessment must separately validate no-fill/partial/full/contingent scenarios,
complete current marks, executable bounds, protection and funding. Missing or
ambiguous obligations produce incomplete reports; they cannot become risk zero.
Arithmetic uses256-digit exactness traps. Original legacy records/hashes and
public schemas are unchanged; no native/TESTNET/LIVE qualification is provided.
