# OP-E owned-order management and original-ID query slice

Functional commits496fcfd and2f1085c implement unsigned request adaptation and
archived query observations. They do not complete OP-E or qualify native writes.

`execution/management_requests.py` consumes the existing full
`ManagementCompilation` and independent current owned target, rules,
capabilities, product profile, owner epochs and clock. It reruns the existing
compiler and compares complete canonical content before emitting typed
`VenueManagementRequest` records. QUERY has no command; each write retains its
own original command. Ordinary, native lists, USD-M conditional algorithms,
Spot/USD-M execution algorithms and real equities retain their separate routes.
Cancel-replace also replays the original replacement proposal with an independent
instrument binding and explicit rounding permission. No LIMIT becomes MARKET.

`engine/nautilus/order_query.py` provides `query_page_request`,
`archive_query_page` and `resolve_original_query`. It reuses the existing
NativeEventBridge durable raw archive before decoding, including errors. History
uses explicit page/pageSize; open-order collections remain unpaginated. Both
current and historical endpoint sets are required. Every page binds the exact
query, scan, account/product/service, evidence source and archived bytes. Original
client/order/list/algo identities and symbols are matched exactly; price/side
similarity never establishes ownership. Raw quantities, fees and other fields
remain archived and are not posted to the financial ledger by this module.

Duplicate identical page replay is idempotent. Gaps, changing totals and
overlapping page rows remain INCOMPLETE; contradictory identities remain CONFLICT.
Even NOT_FOUND cannot permit resubmission, release funds or establish ownership.
A mother found in a collection is not proof of all children, fills, protection
or terminality. Authenticated source-to-lifecycle proof integration remains due.

The current V3 application-write contract supports CREATE_REQUEST only. This
slice does not disguise management as creation, silently rewrite its version or
use old V2 metadata as dispatch authority. A separately versioned management
command/financial-action binding and permit path must be connected through the
existing owner, ledger and risk services before these unsigned writes can send.

## Verification

Base8555201 management/exact-build tests36 passed. New request cases30 failed
before implementation, then61 new/old management cases passed. Query cases41
failed for the missing module. The first implementation run had70 passes and
one unchanged-namespace fixture mutation; that fixture was corrected to mutate
to the opposite service for both products without removing an assertion.

An additional10**50-total counterexample exposed unbounded page-count allocation.
The owned pytest process was stopped after observing5,514,739,712 bytes working
set. That interrupted run is a resource RED, not a completed test suite. The
fix inspects only actual received page numbers/counts. The same large-total case
now passes. Endpoint-set omission separately failed2 cases before correction.

Final actual regression: **218 passed**, exit0,3.62 seconds; no failures or skips.
It includes new management/query, old management, exact Nautilus candidate guards
and existing native bridge/archive tests. Raw output/JUnit/resource metadata and
SHA256 copy maps are in `docs/evidence/binance-orders/op-e-management/`.

One initial test collection error from reusing sibling fixtures was observed in
the tool transcript before relocating the new test to `tests/intents`. Its file
was superseded before the41-case RED; no original raw-file preservation is
claimed for that collection attempt. No product behavior was relaxed.

## Official interface and build evidence

Current official documentation reread on2026-10-29:

- [Spot trade](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade)
- [USD-M trade](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade)
- [Spot execution algorithms](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/spot-algo)
- [Futures execution algorithms](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/future-algo)
- [Equity trade](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade)

These describe distinct ordinary/list/conditional/execution-algorithm/equity
identities and query routes. The code retains those distinctions. Download-schema
links returned empty bodies; they are not treated as downloaded schema evidence.
Nautilus remains the exact1.231.0 candidate/27a8e54 source pinned in
`qualification/nautilus/order_target/manifest.json`; no latest-doc or installed
version claim is added by these Python-only tests.

## Remaining scope

Actual V3 management durable action admission, financial overlap/protection,
dispatch, qualified SDK transport, query authentication and native lifecycle
ingestion remain unqualified. Equity BUY additionally requires proven CARD/MAIN
funding location. PostgreSQL/native/market-driven acceptance is NOT_RUN for this
slice. The full46-feature catalog and56 acceptance scenarios are unchanged;
component tests do not promote their full execution status. TESTNET/LIVE and paid
product model calls remain disabled. No database migration is needed for this
unsigned adaptation slice and no historical records were rewritten.
