# Native response schema evidence and interpretation

Pinned Nautilus1.231.0 source/build evidence remains independent of current API
documentation. These schema facts do not enable an account or qualify native
execution, deployment, last-send guarding or venue behavior.

The installed pinned futures schema BinanceFuturesAlgoOrder identifies the parent
by algoId/clientAlgoId. Its optional actualOrderId is a triggered child reference,
not a new application-send instruction. The pinned list response contains parent
identity and optional orders/orderReports; all returned IDs/raw fields must remain
available without assuming parent acknowledgement proves child completion.

Equity placement returns status S/F as acknowledgement, plus orderId and
clientOrderId. Cancellation S/F is also acknowledgement; lifecycle requires a
separate original order query. Never convert these codes into FILLED/CANCELED
or a release claim. [Official equity trade response schema](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade).

Execution-algo placement may return only clientAlgoId, success, code and msg,
without algoId. Preserve this original client identity and query the complete
original open/history collections; never fabricate a venue parent ID or fill.
[Official Spot algo schema](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/spot-algo),
[official futures algo schema](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/future-algo).

Reviewed2026-10-30 via official rendered pages. The advertised schema.yaml
downloads returned empty bodies and are recorded as EMPTY_RESPONSE, not usable
schema or implementation evidence. Raw response parsing must archive original
bytes first, retain unknown/fractional economics and failed/partial outcomes,
and keep source authentication separate from structural identity matching.

Spot cancel-replace has independent cancelResult/newOrderResult, including
partial HTTP error envelopes. Cancellation can fail while placement succeeds;
placement can fail or remain NOT_ATTEMPTED after cancellation. Keep both raw
outcomes and old/new IDs separately; neither HTTP status nor one success proves
the other result or releases any reservation. origClientOrderId identifies the
old target; a cancellation's new clientOrderId is not a replacement for that
original identity. [Official Spot cancel-replace schema](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade).
