# Local application child admission: remaining implementation boundary

Inspection checkpoint: 2026-10-31, resumed source 0be0c39. This records unfinished
work for ORD-33/44, not a qualified implementation or a scope reduction.

## Current code constraint

Local activation deliberately persists no mother command/client/venue ID
(execution/local_program_activation.py and contracts/order_lifecycle_v3.py).
ApplicationWriteV3 requires a parent_command_id for APPLICATION_GENERATED_CHILD.
The existing program command builder emits APPLICATION_ACTION with no parent;
creation_requests rejects LOCAL_ALGORITHM because activation is not a venue write.
Consequently existing V3 cannot be reused by inventing a mother command ID,
changing origin to APPLICATION_ACTION, or removing the local-pool guards.

## Required additive path

1. Introduce an explicitly versioned child binding referring to the protected
   local activation and its exact authority/state revision. Preserve old V3
   schemas, hashes, SQL validators and UNKNOWN original-ID recovery unchanged.
   Local activation ancestry and a real external parent command are distinct.
2. Derive a typed child request from the admitted node and reviewed schedule
   facts. Preserve LIMIT, trigger, TIF and units. Child size must fit original
   total, per-child bounds, current target and exact remaining allocation.
   Time/volume facts must be source-bound; venue-generated children cannot enter.
3. Under the existing collateral mutex and owner/fence, reserve a child share of
   the already funded parent pool without counting parent money twice. Reuse
   current risk/capacity/owner checks. Persist original child identity, request,
   allocation and independent durable command atomically; consume a fresh
   single-action permit after rechecking current facts. No separate cash ledger.
4. Persist an independently verifiable membership proof referencing the actual
   child command hash. Reconcile fills, fees, cancellations and UNKNOWN through
   original child IDs. Expired proposal/schedule does not erase GTC or protection
   duties. A late cancellation cannot release an uncertain or overlapping share.
5. Route through the existing execution owner and adapter. Never submit native
   generated children, borrow a parent permit, or silently retry uncertain writes.

## Verification sequence under current Harness

First add failing pure contract/compilation tests for ancestry, origins, version
compatibility, exact units and field preservation. No database or Docker calls
at this stage. A narrowly scheduled PostgreSQL cohort is subsequently required
for concurrent slice allocation, no double funding, transaction rollback,
revoked owner/permit, restart and cancel/fill races. Until shared DEV recovery,
isolation and scheduler are ready those cases remain NOT_RUN/BLOCKED. Final
ORD-33/44 and related full matrix acceptance follows all task integrations.

No migration number is reserved here: inspect the authoritative migration
registry before implementing the additive storage boundary. No existing permit
or native-child guard is weakened by this checkpoint.
