# OP-E management V4 component — ACTIVE

This additive work preserves all46 Binance capabilities and56 scenarios. It is
not full management qualification and installs no executable host or account
client. PAPER only; native, TESTNET and LIVE remain disabled.

The closed `2026-10-30.management-v4` contract uses a dedicated codec/schema,
separate history, full plan/current target/owner/risk/funding hashes and a fresh
management validity window. Creation V3 permits cannot enter this boundary.
Migration0022 extends the existing business ledger, without creating a cash total,
changing old schemas/hashes or granting ordinary owner writes. Admission, mark,
consume and post-wait checks use the existing collateral-mutex UnitOfWork,
current owner/deployment fence, AuthorizationService, cause permission core,
actual ReservationRepository group holds, capacity core and protected book cash
reader. Unconfigured LOCAL readers deny. Full actual current source capture
finishes outside business transactions. No open business TX crosses venue IO.

Migration0023 manages existing isolated PAPER orders under their existing market
advisory mutex. Immutable accepted terms, market inputs, fill production and
frozen0013/0014 remain untouched. Ordinary runtime cannot insert a private bridge
authorization. Trusted composition can bridge one committed consumed V4 action
with its original physical database expiry. The venue function rechecks actual
owner/IDs/quantity/cumulative fills; duplicate command insertion prevents retry.
Original-ID observation reads the protected management action source, retaining
raw payload and SHA256. Cancel success grants no FILLED, credit, cash release or
all-child terminality. Lost ACK remains UNKNOWN until queried using that same ID.

The first implemented action is a conservative cancellation of a nonprotective,
nonreduce ATOMIC order whose actual original financial allocation is INCREASE.
An expired original creation proposal does not itself remove management duty;
each new write still needs fresh current management authorization and permit.
Any target change after admission requires reconciliation and a new approved
plan; it cannot mutate or retry the existing command. Source-confirmed outcomes
do not remove the neutral management duty or release original funds.

## Required continuation

- Serial ROOT hooks must consume `program_management_obligations` throughout
  neutral group readiness/admission. Until installed, no management host may be
  enabled. The standalone reader keeps even READY and observed-cancel duties.
- Protection/exit cancellation needs the shared LockedActionVerifier's typed
  management action proof; no legacy synthetic ExecutionCommand/TradeIntent is
  constructed to pass that verifier. Lock exceptions currently deny.
- KEEP_PRIORITY/MODIFY_LIMIT need an additive effective-terms/quantity/queue
  overlay read by matcher and query while preserving immutable accepted terms,
  existing execution envelope, market events and original source. Frozen0014
  explicitly checks original quantity and spec equality, so simply changing an
  order quantity would corrupt its source semantics. These writes currently deny.
- Cancel-replace needs fresh actual ReservationService financial admission in
  the same business transaction, retaining old holds and overlap/late-fill
  duties. ROOT source-knownness must provide an exact original identity witness
  for current allocation, never a bare-ID exclusion or a creation permit. Its
  independent cancel/replacement outcomes, HTTP400/409/PARTIAL/UNKNOWN and
  insufficient overlap-funds cases remain required. No transport is enabled.
- Native parent stop, independent application-generated child commands and
  observation-only venue-generated children remain required full-scope work.

## Actual verification ledger

| Fixed source | Actual restricted PostgreSQL result | Meaning |
| --- | --- | --- |
| 8621118 | setup ERROR, missing0022 | Initial fixture RED, not behavioral proof |
| c9884c9 | 1pass/1fail | Management tuple packing RED |
| 306260d | 8pass/11fail | Collection hashing RED |
| 9cb24d3 | 15pass/4fail | Actual permit consumption SQL operator RED |
| f0785ae | 19pass/0fail/error/skip,69.42s | First narrow cancel boundary component |
| 02a76d6 | 26pass/2fail,106.29s | Creation-TTL fixture replay and short initial permit RED |

Every run uses the fixed immutable offline runner image, fresh private Linux PG,
no host ports, complete JUnit and original evidence. Original handles are held
to terminal and never restarted on observation timeout. Later results must retain
their own source identity; the original ROOT full gate does not cover this code.
Raw evidence publication and independent review are required before integration.

No migration has run on the user's application database. Rollback is to disable
new management admission/dispatch, inventory all V4 duties and retain these
additive tables for original-ID reconciliation. Old workers must never take over
V4 UNKNOWN or reinterpret it as a creation command.
