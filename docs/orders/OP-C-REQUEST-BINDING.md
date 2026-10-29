# Full creation-request binding boundary

The next durable program write binds a typed materialized request, not merely an
order enum or program hash. Original compilation, product-profile and request
index hashes, account/product/service scope, every normalized field and every
server-assigned client ID remain available. Local algorithm scheduling is an
internal activation, not a fabricated native mother or HTTP submit.

Read-only official API verification on2026-10-29:

- [Stocks trade](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade) defaults quoteAsset toUSDC, uses clientOrderId, and distinguishes CARD/MAIN payment wallets. Compiler now explicitly emits the rule snapshot's quoteAsset. This performs no FX or account-eligibility inference. A typed account/wallet funding binding is still needed; never silently select a different cash pool.
- [Spot trade](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade) uses newClientOrderId for ordinary/SOR requests, listClientOrderId for lists, and separate above/below/working/pending client identifiers. Explicit node/client mapping precedes send; it must not be reconstructed by side/price guesses.
- [Spot algorithms](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/spot-algo) use a32-character clientAlgoId. Native child IDs are observed later, never extra local submissions.
- [USD-M trade](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade) distinguishes ordinary newClientOrderId from conditional clientAlgoId. Native batch members retain separate original identities/outcomes; no fake venue batch-parent ID is created.

An unsigned typed request has no credential, signature, arbitrary URL, timestamp
renewal, permit or execution authority. The same execution owner must revalidate
current facts and bind the complete actual request before a durable MAY boundary.
Runtime signing metadata is controlled separately; it cannot mutate economic
fields or select another endpoint. Historical compiled requests remain readable
and hashed as originally captured; no old/UNKNOWN request is silently rebuilt
with new quote/wallet defaults.

Equity quote propagation TDD: eight real compiler cases failed for missing
quoteAsset, then all eight passed. Full compiler subset68 passed. Public schemas,
old record hashes, native account access and operational settings are unchanged.
This is field propagation evidence, not venue or full ORD qualification.

`execution/creation_requests.py` now materializes full unsigned creation fields
from the immutable compiler output and exact product profile. Assignment and
materialization are separate: saved client IDs survive recovery and are not
reassigned on a timeout. Ordinary/SOR, native lists, conditional algorithms,
TWAP/VP and batch use their documented identity fields; a batch has no fabricated
venue parent. Native children receive no additional local send command.

Equity BUY requires an explicit fresh account/wallet routing selection bound to
scope/profile/quote asset. Verification receives the independently loaded expected
selection; a modified stored request cannot select another wallet by validating
itself. This routing fact still requires financial wallet/balance proof in the
later admission bundle. No eligibility, transfer or available balance is inferred.

Creation binding tests26 passed together with equity quote cases; current broad
contracts/intents/unit1258 passed. Raw scalar values, booleans, IDs, endpoints and
batch items roundtrip without credentials, signing timestamps or HTTP transport.
Independent review is pending; SQL/permit/adapter integration remains unfinished.

Independent review then reproduced two unsigned-binding defects: coherent
request/ID hashes could retain another command's generated IDs, and Equity wire
quoteAsset could disagree with the node/wallet currency. Two actual RED cases
failed before correction. The frozen2026-10-29 assignment is now checked against
the original durable command identity, and Equity wire quote must match the
compiled notional asset plus independently supplied wallet selection. Stored
IDs are never replaced on recovery.28 request/quote cases pass after correction.
These are integrity guards; actual financial/source qualification remains the
execution owner's responsibility before any MAY boundary or transport.
