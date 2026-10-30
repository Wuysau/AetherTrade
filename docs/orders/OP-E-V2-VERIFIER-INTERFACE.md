# PAPER V2 trusted observation interface

This component reads the frozen actual producer1b12ed9/0014 contract. It does
not register a production source. Full46/56 C–G remains ACTIVE. Root owns the
protected static source registry, approval decoder, economic accounting and
ownership reconciliation. A source observation grants no new command/permit,
cash, position, protection, FLAT determination or reservation release.

`PaperProgramV2ReportVerifier.verify(command_id)` is the sole entry point. It
requires the actual UoW, exact PaperEngine and NativeEventBridge in PAPER scope,
loads original protected request/MAY/consumption/assessment, and closes that
business transaction before reading the separate synthetic venue database.
No caller DTO, hash, actor ID or `source_verified` flag can supply authority.

The result is the distinct `PaperProgramVerificationV2` dataclass, kind
`PAPER_PROGRAM_VERIFICATION_V2`, version `2026-10-30`. Its serialized fields
include `command_id`, `status`, `source_verified`, `fills`, `orders`, `raw_refs`,
`discrepancies`, `retained_duties`, `causal_dependencies`, `causal_graph_hash`,
`raw_report`, `market_evidence`, `synthetic`, `execution_authorized` and
`funds_release_authorized`. Both authorization fields are always false.
`raw_report.request.command_id` remains the original producer request path.

Each fill is `NormalizedPaperFillV2`, kind `NORMALIZED_PAPER_FILL_V2`, same
version. Common fields are `fill`, `side`, `economics`, `raw`, `fees_known` and
`source_v2`. Crucially, `raw` retains the genuine unchanged producer
`PaperMarketFill`; it is not wrapped in a caller-created replacement or
masqueraded as a V1 normalized result. `source_v2` is `PaperFillSourceV2`, kind
`PAPER_FILL_SOURCE_V2`, with original node/client/parent identities, accepted
context and consumption hashes, global causal graph hash, and compact immediate
input IDs. The complete typed `PaperCausalDependencyV2` graph occurs once in the
result. Native quantity, venue price, unit ratio, both economic multipliers,
reported/expected gross and fees retain exact values without caps or FX repricing.

The host fsyncs raw fill/disposition/input bytes and full snapshots before
classification. It independently archives a restricted SQL manifest containing
actual parent/members/order IDs/revisions/lifecycle, accepted terms/runtime,
every causal input including pre-depth and clocks, all events and ALL competing
allocations/preventions. Bounds reject overflow rather than truncate history.
Event-set completeness and duplicate checks precede normalization. Known
economic/cumulative/remaining conflicts preserve observations with explicit
discrepancies and reconciliation duties. Source or unsupported-shape conflicts
retain raw evidence and block approval.

The frozen roles do not grant SELECT on the mutable native exclusivity table.
This verifier adds no grant. Native exclusivity instead requires the protected
group and actual member inventory, a unique immutable cancellation winner,
exactly one actual cancellation witness for each peer, actual CANCELED peer
lifecycle with zero actual fill and zero prevention, and independently verified
winner trigger/fill inputs and clocks. Missing or contradictory evidence blocks;
membership or one arbitrary cancellation alone does not prove exclusivity.
Working/pending lineage requires the complete actual working fill sum and exact
persisted full-fill witness. Historical accepted GTC/native children are never
re-gated by expired creation TTL or given another application permit.

Supported frozen-source checks cover market/fixed/static/trailing, peg and
priceMatch placement depth, iceberg display, published calendar/DAY/GTD clocks,
four STP modes with explicit maker identity, and OCO/OTO/OTOCO native lineage.
Reference/calendar/clock/ownership inputs never replenish depth. Preventions
never become fills. Spot BID_ASK trigger sources are rejected by the actual
compiler (Spot requires LAST); USD-M conditional native parent/execution and
other evolving producer families stay explicitly unqualified until their actual
source contract is adapted and tested. All46/56 targets remain in scope.

The V1 report module,0013 bytes, producer files and frozen0014 hash remain
unchanged. Final source-specific PG results and every intermediate RED/fixture
failure are recorded in `docs/evidence/binance-orders/op-e-v2-report-progress`.
Review and root integration are separate required steps; this document supplies
no registry-ready or complete-goal claim.
