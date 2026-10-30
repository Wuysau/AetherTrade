# Isolated PAPER host composition

Approved continuation: compose the existing ExecutionOwner, durable V3 boundary,
protected V1/V2 source, Raw FillLedger, owned posting, working-order projector and
strict book binding. No console database, HTTP route, scheduler or default mode
is activated. Existing financial/admission guards remain authoritative.

Use one thin runtime class, `ProgramPaperHost`, scoped to PAPER Spot atomic BASE
creation with exact V1/V2 source types. It receives the existing owner, boundary,
owner UoW and private source service. It constructs the already reviewed owned
accounting and order-projection services with the exact registered local opening
reader; no alternate balances, matchers or caller supplied approval callbacks.

`dispatch(original_command_id, lease)` reads the immutable original binding,
checks supported shape and all neutral management duties, then uses the actual
owner session and existing mark/consume/submit path exactly once. Existing MAY/
UNKNOWN requires original-ID observation, never a new token/retry. `observe(id)`
records source/raw/owned facts before projecting working order and binding the
own book. Missing financial readiness remains explicit. `reconcile_book()` is
an explicit own-scope owner transaction, never silent replay repair. Current
working inputs remain observations; cost/history POSITION_EVIDENCE and generic
new-write structural guards are unchanged. Quote/native/managed sources require
their separate qualified bridges and cannot masquerade as V1/V2.

TDD: real funded original commands precede missing-host RED. Genuine PG tests
cover V1/V2 dispatch→unfilled observation→finite .4 fill/.001 fee→raw/owned/order/
book, replay, original TTL recovery, missing owner, UNKNOWN and family denials.
Then run affected current cash/working/source/old execution regressions. Preserve
every original handle, source-bound raw evidence and explicit remaining blocks.
Independent review precedes any operational or full-catalog promotion.
