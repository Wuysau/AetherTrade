# Explicit program storage versions — OP-C

Repository program reads, validation and canonical hash binding now use the
explicit VersionedOrderCodec registry. Both v2 (`2026-10-28`) and v3
(`2026-10-29`) records retain their exact typed program body. VP urgency and
optional duration are never dropped; TWAP duration remains mandatory. Existing
v2 canonical bytes/hashes and its128KiB program limit remain unchanged. Unknown
versions are rejected rather than inferred as v2. A program ID cannot be reused
with another version or changed content.

Baseline clarification: OrderProgramCodec already delegated v3 records to the
versioned codec. V3 persistence was therefore readable before this change. The
functional safety correction is the lifecycle boundary: an otherwise matching
imported V3 parent previously entered the old V2 ParentAuthority reducer. This
was reproduced in a failing fixture with original durable identity and explicit
trusted test funding. New parent binding rejects it with
`PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED`.

Already imported V3 programs with old V2 authority/state are contained too.
Incoming parent/child reports first pass the existing source-authentication and
identity checks. Authentic exact reports are archived, then the existing
reconciliation/risk-lock path retains original parent/event IDs without running
the V2 reducer, claiming a child, ingesting a fill or changing held funding.
Unauthenticated reports remain SOURCE_UNVERIFIED and do not become trusted source
history. Existing V2 observation behavior is covered by the PG regression suite.

This preserves the ApplicationWrite v2 envelope, which can durably reference a
complete known-version program hash. Persistence is not execution authority;
`mark_may_have_sent` still rejects sends with
`ORDER_COMPILER_RISK_ADMISSION_NOT_IMPLEMENTED`. A MAY_HAVE_SENT record retains
its original ID and must be queried/reconciled, never resubmitted. No new permit,
account transport, fill, model call or TESTNET/LIVE qualification is provided.

No database migration is needed:0008's append-only JSONB program envelope already
stores explicit versions. Old rows are never rewritten or backfilled. Roll back
new writes before changing readers; do not downgrade a v3 record to v2, and keep
the original versioned reader available for recovery. A future native lifecycle
revision must bind quantity units, contract multipliers, complete parent/child
allocation and protection/funding obligations before v3 native child admission.

Validation evidence is kept under
`docs/evidence/binance-orders/op-c-versioned-storage/`. Focused tests and actual
isolated PostgreSQL results are reported separately; neither implies native
exchange qualification or full ORD acceptance.

Actual verification: focused54 passed; broad contracts/intents/unit890 passed.
Committed source0e1e2c4 passed32 isolated PostgreSQL cases with no failures,
errors or skips. The initial PG result31 passed/1 failed is retained: a typed
historical sentinel corrected the unknown-version test fixture; no guard was
relaxed. See the evidence README for exact commands and remaining qualification
boundaries.
