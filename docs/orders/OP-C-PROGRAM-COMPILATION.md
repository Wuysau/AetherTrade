# OP-C typed request plans

`intents/program_compilation.py::compile_order_program` consumes the original
versioned proposal, public rule/capability snapshots and a trusted typed product
profile. It returns `ProgramCompilation`, preserving original objects and
proposal/program/rule/capability/profile hashes. `execution_authorized` always
returns false. This is not target-delta admission, a funding reservation, a
durable command, a permit, a native adapter or a market matcher.

The profile binds scope, symbol, exact adapter digest, rule reference/revision,
validity and evidence. It supplies product-specific quantity bounds, allowed
types/STP/sessions, trailing/iceberg/peg/price-match/account-mode limits, execution
price envelope and algorithm bounds absent from the frozen public snapshot.
Dynamic market filters, session calendars, actual positions, fees, whole-account
funds, owner/epoch, permits and deployed adapter qualification remain explicit
`required_checks`. Presence in a capability snapshot never establishes any of
those facts. A profile price envelope must be checked for enforcement at financial
admission and dispatch; the plan does not convert MARKET to a capped LIMIT.

Each compiled node retains the complete original AtomicOrderSpec, an exact
normalized quantity or unresolved receipt, typed `FieldName` parameters and
its derived origin. Each request has a fixed service `Namespace`, `Operation`,
static `Endpoint`, timing, referenced node IDs and typed parameters. All routes
are POST creation routes. No arbitrary URL, HTTP client, signing material or
credentials are accepted. Future transport belongs to the same execution owner.

Native list plans merge shared fields and qualify working/pending/above/below
fields. All legs must agree on shared STP and exclusive quantities. OPO/OPOCO
never emit pendingQuantity; that quantity is staged until full fill and trusted
net-receipt/fees/lot-size evidence. Native lists and execution algorithms create
one durable mother request; venue children are observations. Local list and
algorithm children retain separate staged writes requiring independent command
and permit admission. Software exclusivity races remain an explicit check.

Fixed LIMIT quote sizing reuses the existing exact integer-ratio helper. The
4000 / LIMIT81490 / STOP81330 regression remains 0.049085 base on a 0.000001
step, with the original price and stop strings unchanged. Prices never round.
Quantity rounding requires explicit permission. MARKET quote sizing remains
quoteOrderQty for Spot and notional for equity BUY. USD-M close-all omits both
quantity and reduceOnly, and hedge reductions never emit forbidden reduceOnly.
Base MARKET/PEG/PRICE_MATCH require a trusted execution-price envelope rather
than assuming unbounded buying power. Group exposure is retained as topology;
the compiler does not sum native mutually exclusive legs as independent holds.

## Official documentation rechecked 2026-10-29

- [Spot trading endpoints](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade): ordinary orders, SOR and each list have distinct routes and field prefixes. This plan uses the current orderList routes rather than deprecated OCO.
- [USD-M trading endpoints](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade): conditional requests use fapi/v1/algoOrder with triggerPrice and activatePrice; ordinary order/batch identity remains separate. callbackRate currently reaches20. Actual selected adapter protocol tests remain NOT_RUN (DOC-01/02).
- [Pegged orders](https://developers.binance.com/en/docs/products/spot/faqs/pegged_orders): plain PRIMARY_PEG LIMIT requires GTC; maker requires PRIMARY_PEG; contingent stop-limit pegs are validated at trigger time. SOR does not support peg.
- [Stocks trade](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade): MARKET BUY uses notional and MARKET SELL uses quantity. LIMIT uses price/quantity/session. tokenize=false is retained; unsupported stop/trailing events do not become submit capabilities (DOC-03).
- [Spot algorithms](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/spot-algo) and [futures algorithms](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/future-algo): TWAP takes duration and optional limitPrice. Spot VP takes urgency and optional vpTimeLimit; futures VP has no duration parameter. Public v3 is an explicit additive native revision supplied by root commit87b018b; v2 participation_bps is retained and rejected for native mapping instead of dropped.
- [quoteNotionalLimit FAQ](https://developers.binance.com/en/docs/products/spot/faqs/quoteNotionalLimit) remained inaccessible via the documentation reader. DOC-04 remains UNKNOWN/NOT_RUN; no invented field is emitted.

## Verification and remaining work

Interpreter: original main `.venv/Scripts/python.exe -X utf8`, with this worktree's
src on PYTHONPATH. No venue/account access, operational environment mutation,
model call or operational migration occurred.

Red evidence: initial import failed because the compiler did not exist. Expanded
tests then independently exposed an overbroad PRIMARY_PEG restriction and an
ineffective USD-M STP+FOK combination (2 failed/40 passed). The missing market
price bound test failed with DID NOT RAISE before enforcement was added.

Fresh green command: `python -X utf8 -m pytest tests/intents tests/contracts -q`:
317 passed, exit0. The new compiler file contains48 parameterized cases. This
proves offline compilation and strict codec regressions, not native trading,
database settlement, market matching, restart recovery or full ORD acceptance.
All complex native qualification remains NOT_RUN. The complete46 target catalog
is unchanged. Lifecycle amend/cancel-replace/query require controlled typed
management instruction input and independently admitted commands; those targets
are not erased or claimed implemented by this creation-plan slice.
