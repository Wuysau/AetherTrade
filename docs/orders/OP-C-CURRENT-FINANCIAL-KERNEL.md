# Current full-source program financial admission

The new private program branch composes the existing ReservationService,
UnitOfWork, collateral mutex, active-owner lock, reconciled reservation reader,
capacity formula, target obligations and risk evaluator. It creates no parallel
cash ledger and no legacy order projection. Public legacy paths retain their
original contracts and gates.

`GenericProgramIngress` writes a full typed source through the existing restricted
`ledger.order_program_intakes` table. The execution owner can read, but cannot
author, these rows. Source restoration verifies physical scope, record identity,
sequence and the database transaction identity. Global financial IDs include the
full physical scope; replay retains the original proposal and context bytes.

`ReservationService._reserve_program` receives trusted local readers under the
same transaction. Every attempt requires independently loaded healthy clock
data, current authorization, deployment fence and owner, the actual book and
group reservations, exact pending quantity mappings and complete current risk
facts. The full archive is replayed through the actual compiler, target allocator,
exposure and risk evaluator. A historical ALLOW cannot substitute for these
bindings. Qualification requires PAPER transport, explicit PAPER_VERIFIED claims
and an independently resolved current proof; no production reader is registered.

Cause-lock exemptions use the existing LockedActionVerifier with an additive
program proof. It accepts a separately loaded, transaction-bound bounded atomic
EXIT proof; no fictitious legacy command is made. Current locks and original
obligations cannot disappear through a saved assessment. Report age and evidence
age bound the current deadline. Unknown and recovery responsibilities remain
blocked. Qualified bounded residual exits atomically preserve an EXIT_ONLY lock
and MANAGE_UNTIL_FLAT responsibility; unknown hard loss stays unknown.

The writer appends the financial intent to `ledger.intents`, exact incremental
asset reservations to `ledger.reservations`, and the full archive, basis, actual
decision and bundle to namespaced immutable audit records. It is a PRIVATE
attachment operation. The enclosing transaction still must append the actual
external request commands or a typed internal activation. The component returns
no send permit and no public completed-admission receipt. Attachment failure or
commit failure must roll back all financial rows while retaining durable intake.

Native OCO target allocation preserves both original full-delta legs. A matching
native atomic group proof is required by real risk assessment before using
alternative scenarios in the same exposure calculator. NO-FILL remains a
scenario: a planned conditional FLAT target does not erase current position loss,
stop/holding requirements or protection duties. Local OCO still requires its
separate SUM/race allocation contract; native guarantees never apply to it.

Fixed b6b2dbb new isolated PostgreSQL tests passed8 with zero failures/errors/skips.
They prove private financial storage/restart, restricted intake writes and
rollback; no command, outbox, fill or permit is fabricated. The newer299f9db cohort
adds explicit qualification, final fence, commit-failure and expiry regressions;
its actual PostgreSQL result is pending. Broader contracts/intents/unit passed1468
after OCO/query-age changes. Full fixed-source gate results remain source-specific.

Complete V3 command/outbox/permit integration, local activation and child
admission, native quote target intervals, fee/dust residual handling and all
OP-D/E/F/G work remain active. All46 capabilities and56 acceptance cases retain
their full scope. This component evidence does not qualify Testnet or LIVE.

The full fixedb6b2dbb cohort subsequently completed924 mandatory PostgreSQL tests
across30 suites, zero errors/failures/skips, plus ordinary3231 passed/478 explicit
environmental skips/96 deselected. This source predates the query-age, OCO and
strict qualification followups. Fixed299f9db diagnostic failed1 passed/11 failed
because the newly qualified fixture omitted required qualification evidence.
The fixture was corrected to supply an explicitly synthetic artifact, and the
financial proof must match the snapshot's exact artifact references. The
bdbd9b0 fixed-source diagnostic remains pending; guards were not weakened.

Byte-identical fullb6 and299RED logs, JUnit, manifests and SHA256 maps are retained
under `docs/evidence/binance-orders/op-c-fin-kernel/` (b6 and299-red). These are
engineering evidence with declared synthetic provenance, not native readiness.
