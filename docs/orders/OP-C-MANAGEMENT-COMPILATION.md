# OP-C owned management plans

`order_management_compilation.py::compile_order_management` compiles a typed
ManagementInstruction against an OwnedTargetSnapshot and current trusted product,
rule and capability snapshots. The target binds original account/product/service,
program/hash, order/client IDs, owner/epochs, quantity/cumulative facts and evidence.
All writes also bind the existing ApplicationWrite metadata and target ID. The
instruction and target receive independent content hashes. The returned plan
always reports execution_authorized=false and funds_released=false.

It maps queries/cancels for Spot ordinary/list/execution algo, USD-M ordinary/
conditional algo/execution algo and real Equity. A query never requests a write
permit. UNKNOWN queries retain the original client ID where the endpoint supports
it. Execution algo mother queries require paginated current/history collection
reads filtered locally by exact original identity; the plan does not invent a
single-algo query endpoint or send undocumented algoId filters to collections.

Spot keep-priority is an explicit PUT amendment with a smaller total quantity.
It does not change the price, increase size or erase already-filled quantity.
Spot cancel-replace requires explicit STOP_ON_FAILURE/ALLOW_FAILURE and
DO_NOTHING/CANCEL_ONLY choices. It retains cancel ID and distinct new client ID,
complete fresh replacement plan/hash and independent cancel/new outcome duties.
USD-M ordinary LIMIT modification uses PUT/fapi/v1/order with side, quantity,
price and original ID; it explicitly records queue reordering. Untriggered
conditional algos cannot use this path. Linked-list collateral and sibling effects
are an explicit subsequent check when an individual child is addressed.

Existing GTC management outlives the original proposal TTL. Replacement submission
still requires a fresh proposal/program deadline and matching rule/capability/
profile hashes. The tests reproduced missing replacement-expiry enforcement with
a DID NOT RAISE red case, then verified that normal management continues while a
stale new replacement is rejected.

These are independently reviewable request plans, not integrated mutation workers.
Before any send, the owner must bind the **full management plan hash** durably and
perform locked action/financial verification, fresh ownership/execution/policy
epoch checks and a single-use write permit. Existing ApplicationWrite metadata
alone does not bind amendment quantities or cancel-replace modes. Cancel ACKs and
algorithm stops never release funds or prove children terminal. Collection query
pagination, result ingestion and actual adapter protocol tests remain NOT_RUN.

The same current official [Spot trade documentation](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade),
[USD-M trade documentation](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade),
[Spot algo documentation](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/spot-algo),
[futures algo documentation](https://developers.binance.com/en/docs/catalog/advanced-trading-algo-trading/api/rest-api/future-algo)
and [Equity trade documentation](https://developers.binance.com/en/docs/catalog/advanced-trading-stocks-trading/api/rest-api/trade)
were rechecked read-only on2026-10-29. DOC-04 remains unresolved. There was no
venue/account access, operational environment change or model call.

Fresh verification using the original main venv and this worktree src:
`python -X utf8 -m pytest tests/intents tests/contracts -q` →347 passed, exit0.
The management suite contains30 parameterized cases. Creation tests now explicitly
prove APPLICATION_GENERATED_CHILD proposal hints cannot cause duplicate local
sends for native OPO/TWAP, while local OTO pending writes remain independently
staged. Full complex native qualification and end-to-end ORD scenarios remain
NOT_RUN; this offline evidence does not supersede those acceptance requirements.
