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
