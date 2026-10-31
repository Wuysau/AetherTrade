# ROOT quote producer integration

Exact merged source: `46bf284dbb2145287068d7f92761511ada3202c3`. ROOT base:
`b5d7357407db6d412619016aa57ecc53ceaba1fb`. Reviewed candidate:
`03646b5705a5cabbefafe608dee472a2cf0ba103`, tested peer13c52d3. Independent
[review](D:/Projects/Python/AetherTrade/TradingHandoff/QuoteBudgetReview-f31784c/review.md)
cleared both allocation-completeness and non-depth-lineage P1 fixes.

[Exact dependency manifest](ROOT-QUOTE-DEPENDENCY-46bf284.json) binds nine copied
files and eight preserved source/guard/migration files. Only two suite entries
were added; original62 remain. No current cash/source registry or host was changed.

The existing public adapter consumes the original permit once, recognizes the
strict QuoteAssessmentArchiveV1, and executes only Spot atomic MARKET quoteOrderQty
with standard routing and supported explicit bounds. `provision_paper_quote` is
an administrator-only additive0025 SIM step, never automatic runtime provisioning.
The existing market mutex/depth and immutable inputs remain the shared allocator.
BUY spends gross quote; SELL seeks gross quote receipt. Exact lot floors derive
actual base. Requested quote, gross quote, remaining quote and external quote fees
are separate fields. Budget dust/partial/no liquidity terminate this bounded PAPER
attempt with EXPIRED/unused quote; no later event renews the attempt. Unsupported
receipt/base/third-asset fee models remain denied. These PAPER lifecycle semantics
do not qualify native exchange behavior or target-band completion.

`query_quote_creation(engine,command_id)` reads original IDs after restart and
returns tagged `PaperQuoteReportV1`. `verify_quote_report(engine,report,*,boundary)`
returns `VerifiedPaperQuoteReportV1`, independently comparing actual terms/runtime,
business consumption, every fill and shared event capacity. It resolves real
depth lineage across references/deltas and counts BASE/quote/prevention usage.
The affected V2 manifest retains exact complete-set Counter equality and bounds.

Actual ROOT post-merge suites on the same fixed source:

| Suite | Passed | Fail/error/skip | Seconds | Original handle/run |
|---|---:|---|---:|---|
| paper-quote-budget |30|0/0/0|190.063|81556 /00bf6e19f413|
| quote-review-mixed |3|0/0/0|33.843|12742 /670e9670aab3|
| program-scoped-financial |16|0/0/0|42.175|43484 /d0b39644c50e|
| paper-program-v2-reports |51|0/0/0|254.984|98892 /75478dae9853|

All original handles terminal0. Focused62 unit passed3.03s. Evidence is under
`docs/evidence/binance-orders/op-d-root-quote-integration`; every invocation uses
the exact committed source, fresh private PostgreSQL/no host ports and the pinned
offline image. No identical old broad suite was repeated after documentation edits.

For the later host integration, the static `SOURCE_VERSION_ROUTES` still admits
only exact V1/V2 program verifier/result/normalized triples. Neither quote result
is one of those triples. `save_verification_snapshot` cannot accept it. A future
quote source route needs its own exact tagged result/normalization and genuine
native raw-fill/economic bridge; it cannot alias NormalizedPaperFillV2, invent
original base quantity from an interval, or treat this source check as cash,
ownership, release, current protection, history or send authority. Actual source
approval/raw recording/owned posting remain separate transactions and qualifications.

CASH's stricter newer financial modules must be merged separately after its real
A+B/order projection/new-intake proof. New native0026 integration must preserve the
quote source UNION/shared capacity and prove actual combined source behavior.
Current UI/host/default qualification remains unchanged. Full46/56 is active;
these component counts do not promote the formal56 acceptance rows.
