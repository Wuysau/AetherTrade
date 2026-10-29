# Native quote quantity interval integration plan

Status: next engineering design for parent review, not implemented qualification.
The current scalar path and all of its historical hashes remain unchanged. This
plan never treats a normalization mark as an execution or fill price.

## Contract and source semantics

Add explicit V1 interval kinds rather than new optional fields to serialized
scalar records: QuoteTargetFactsV1, QuoteTargetAllocationV1,
QuoteAssessmentArchiveV1 and QuoteFinancialIntentV1. Their distinct record_kind
and version dispatch must preserve old ProgramFinancialIntent, old archives and
UNKNOWN readers. Shared ledger identity, reservations, owner, outbox and permits
remain the only infrastructure.

Separate three quantities: the original absolute economic target, remaining
authorized quote usage after actual inventory and existing working/UNKNOWN
obligations, and the immutable request's quoteOrderQty/notional. They must never
be equated merely because all are denominated in the quote asset. A changed
request amount requires a new proposal/compilation, not hidden resizing.

The first binding question is explicit: current QUOTE_NOTIONAL is normalized
through a declared limit or trusted reference by the scalar target contract.
Variable native quote fills cannot inherit an exact base-position goal from that
normalization. A new source revision must carry an explicit admissible absolute
position-value band and valuation basis, or an explicitly authorized cumulative
quote-usage goal. Existing scalar sources cannot silently acquire tolerance or a
new economic meaning. The new source must retain actual/working/UNKNOWN position
and quote obligations, their units, owner and original command identities.

## Enforced price, lot and fee facts

QuoteExecutionBoundsV1 binds physical scope, compilation/node, current rule and
profile hashes, clock/deadline, minimum and maximum legal execution price, native
lot unit/step, maximum gross quote usage including any permitted lot rounding,
and complete per-asset fee bounds. Price facts require a separately authenticated
enforcement proof: an actual PAPER matcher that enforces its frozen visible
depth/range model, or an independently archived native execution-rule guarantee.
A claimed profile range or latest quote is insufficient. Missing guarantee means
unbounded/unknown allocation and denial; it never selects a convenient price.

The unconditional fill interval always includes zero. Full-spend conditional
bounds are separate diagnostics and require explicit spend/lot assumptions. Base
quantity upper bounds use gross quote upper usage / minimum execution price,
converted through explicit native/ledger multipliers and conservative outward
rounding. Never round inward to claim safety. Exact Fraction arithmetic should
establish enclosure before presenting Decimal bounds. Actual fills outside an
approved envelope remain unmodified ledger facts and raise reconciliation locks.

Receipt fees reduce their exact received asset. External-balance fees are held
in their exact debit asset; another currency cannot pay them implicitly. SELL
requires enough owned base for the maximum quantity plus any external base fee;
BUY requires its real wallet/quote debit plus external fees. The request's gross
quote value and net receipt are separate. No hardcoded USDT or implicit FX.

## Product coverage for the first integrated interval cohort

| Product and request | Required debit/receipt handling |
| --- | --- |
| Spot MARKET BUY quoteOrderQty | quote debit ceiling; base receipt interval; base receipt fee and external fees |
| Spot MARKET SELL quoteOrderQty | maximum owned base debit; gross quote receipt versus quote receipt fee; base fee obligations |
| Real equity MARKET BUY notional | selected real cash wallet and quoteAsset; SHARE interval and supported lot rules; tokenize=false; actual per-asset fees |
| Real equity MARKET SELL | quantity request stays scalar; never invent a SELL notional field |
| USD-M ordinary/conditional | no Spot quoteOrderQty projection; retain explicit contract/margin/funding semantics |

## Risk, persistence and continuing duty

Run the same ExposureCalculator and risk limits for no-fill, bounded partial and
full-range endpoints; retain current inventory loss/protection in no-fill. Any
nonmonotone fee/funding/lot behavior requires additional proven extrema or stays
unknown. Cash/capital and group/strategy limits count actual exposure plus full
working/UNKNOWN/parent envelopes once, preserving reservation backing.

Archive interval facts, exact source, allocation, real assessment and complete
per-asset reservation requirements in the same financial transaction. Extend
the existing ledger intent registry by an explicit new kind. Each application
request keeps independent durable write/permit and original-ID recovery; native
children remain observation-only. Use the actual attachment witness and closure.

Fee/dust inventory is never FLAT by tolerance. A separate explicit residual duty
records exact owned quantity/asset, valuation uncertainty, protection or bounded
exit responsibility and review evidence. Accounting does not truncate it. A
position-value goal band does not authorize deleting residual inventory or holds.

## RED and verification sequence

1. A quote4000 request across prices50000..60000 must produce unconditional
   quantity[0,0.08], not a fake point or an unconditional positive lower bound.
2. Explicit full-spend conditional bounds retain lot residual assumptions and
   outward enclosure; tiny prices and recurring rational quotients cannot round
   an unsafe upper bound down. Duplicate/future/stale enforcement facts deny.
3. BUY receipt-base fees and SELL external-base fees use separate extrema; SELL
   owned inventory insufficient at the low-price endpoint denies. Third-asset
   fees never spend the quote wallet automatically.
4. Existing actual/working/UNKNOWN obligations reduce the authorized absolute
   goal or quote envelope before admission. A quote request exceeding it must
   replan; no request mutation or erase-UNKNOWN shortcut.
5. Real equity request remains notional/quoteAsset/wallet/tokenize=false and
   SHARE-based. Unsupported SELL notional remains rejected.
6. Actual SAME financial transaction + durable attachment/outbox/commit rollback,
   independent current dispatch, partial fills/fees, restart, overshoot locks,
   residual duty persistence and original scalar/UNKNOWN hashes all get real PG
   regressions. Component math tests alone do not qualify this cohort or native.

All46 targets/56 cases and OP-D/E/F/G remain required. No LIVE/TESTNET/account,
paid model call or production qualification reader is introduced by this plan.
