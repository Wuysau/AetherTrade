# OP-D V2 component boundary

Checkpoint `1b12ed9` has actual21 V2 PG,19 unchanged V1-on0014 PG,59 legacy PG and
1525 Python checks. See [raw evidence](../evidence/binance-orders/op-d-matcher-v2/README.md).
This is not the final0014/DTO freeze or full46/56 qualification. Frozen0013 and
old DTO/raw hash domains remain unchanged. Defaults do not install a production
qualified matcher or report source.

`PaperMatchingModelV2` selects the same actual owner/MAY/single-use financial
boundary. `PaperCreationReportV2` retains original full request/write/compilation,
rules/model and protected current assessment envelope. Actual row order IDs and
lifecycle revisions, native units, original quantities, per-fill gross/fees,
raw SQL payload bytes and stored SQL SHA remain independently inspectable.

New controller inputs are `PaperReferenceEventV2` (distinct LAST, MARK_PRICE,
CONTRACT_PRICE, BID and ASK), `PaperClockEventV2`, `PaperCalendarEventV2` and
`PaperLiquidityOwnershipEventV2`. They use the same exact market key/stream
sequence and immutable input archive. Clock/reference/calendar/ownership inputs
never replenish depth. Calendar windows retain IANA timezone, actual UTC bounds,
trading date, revision and validity. Ownership binds the exact current depth ID
and hash, reported level quantity, account and observed maker venue order ID.
Runtime cannot publish sources or change native progress/prevention records.

The model currently implements finite-envelope BASE MARKET, fixed limit,
specified-source static/trailing triggers, latched peg/priceMatch pricing,
one-tranche-per-input FIFO iceberg, GTD, calendar-gated equity and DAY, and native
OCO/OTO/OTOCO members. An actual native list has one original mother request and
one command/consume; its persisted parent ID and member IDs are query data.
Members execute in their ordinary namespace. Conditional activation records a
real sibling cancellation event in the same locked SIM transaction; working
partial fills do not activate pending legs. `parent_activation_fill_id` refers
to actual complete working fill source, not an ACK or initial target metric.
This follows the [official list contract](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/trading-endpoints)
under explicit synthetic PAPER assumptions, without native venue qualification.

For a native list, each execution envelope uses the protected current node price
bound and archived compiled gross ceiling, stays inside admitted programme gross,
and separately obeys the protected individual order policy using actual archived
FX valuation. Programme gross never substitutes for individual order policy.
All source/clock/owner checks run after waiting on the actual SIM liquidity lock;
an engine clock behind the persisted source cannot start its first write.

STP observations are distinct from trades. Same-account EXPIRE_TAKER/MAKER/BOTH
and DECREMENT currently have actual PG tests. Full taker expiry records its
entire remaining quantity; DECREMENT keeps original quantity and uses
`original - executed - prevented` for live leaves. `PaperSTPPreventionV2` and
`PaperMarketEvidenceV2.preventions` retain maker/taker quantities and identity;
they do not become fills or fees. See the [official STP definitions](https://developers.binance.com/docs/binance-spot-api-docs/faqs/stp_faq).
Current source model assumes one explicitly identified maker per price level.
Trade-group/TRANSFER, all product/TIF combinations and native list STP remain
unqualified; missing ownership holds the order rather than guessing a fill.

`paper_order_input_dependencies_v2` records actual per-order decision inputs,
including every trailing extrema sample, earlier depth, calendar, source price
and working-fill dependencies. `query_market_evidence` returns those exact
bounded inputs plus ALL competing fills/preventions on those events, under the
same read snapshot. Global streamed limits remain4096 inputs,100000 allocations
or preventions and16MiB; overflow explicitly blocks and never truncates history.

Still required: conditional USD-M native parent/ordinary-child membership and
priceProtect rules, dynamic execution range, trade groups/TRANSFER, richer fees
and OPO/OPOCO receipt/dust locking, batch outcomes, cancel/replace/keep-priority,
halt/extended/DST execution coverage, native TWAP/VP clock/volume pools, separate
local child-stage financial admission and actual V2 host normalization/ledger
integration. The complete [56-row component matrix](OP-D-MATCHER-MATRIX.md)
preserves every requirement and distinguishes this evidence from final results.
