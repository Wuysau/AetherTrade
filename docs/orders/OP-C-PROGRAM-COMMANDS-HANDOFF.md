# OP-C program commands — fresh-context implementation handoff

Status: parent explicitly approved a fresh-context sole-writer handoff, NOT a goal pause. This worker releases this worktree after committing this file. Do not reset, recreate, fork or clean the checkout. No live PostgreSQL/container/process was started by this cohort.

## Exact workspace / implemented source

- Worktree: D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/binance-opc-program-commands
- Branch: codex/binance-opc-program-commands
- Clean source HEAD before this handoff:93aecdca193fb51555c10cd9669a91af7a8b37ee.
- Own aff7b054a36180dd931c09c85d1571487a4de98d: creation service/node namespace consistency and refusal of disguised local TWAP/VP parent creation. RED2 failed/23 passed; corrected creation+compiler suite85 passed. Root already integrated this as4b2.
- Own93aecdc: contracts/application_write_v3.py, exact V3 application-write public/package schema, explicit version registry, tests/contracts/test_application_write_v3.py and approved plan.31 DTO+old obligation-reader tests passed. This is only a reference DTO/codec, NOT a durable writer or permit.
- Actual dependencies cherry-picked:52c55a6→fd96197 full assessment archive; b6b2dbb→5e697a5 same-kernel financial bundle;52ecf8e→de22494 current reduction query age;299f9db→eb43a14 qualification/final fence; bdbd9b0→9e51155 actual artifact references. A partial qualification cherry-pick was safely aborted/reapplied in dependency order; all DTO work was preserved. No unresolved merge or WIP code remains.

## NOT IMPLEMENTED YET

No execution/program_commands.py, no0012 migration, no private request-binding/identity/mark/consume tables, no new obligation binding validation, no V3 READY database writer, no MAY boundary, no one-use permit consumer, no command PG tests. Do not report or stub these as GREEN. The parent public-admission service is waiting for this real dependency.

## Approved stable composition API

Planned module: src/trading_system/execution/program_commands.py.

- build_creation_write(request, bundle, *, execution_epoch) -> (ApplicationWriteV3, ProgramRequestBinding).
- ProgramCommandRepository(tx, *, load_clock=None, load_lease=None, load_current_facts=None, load_qualification=None, load_action_proof=None).
- append_creation(write, request, *, bundle) -> stored write, SAME active outer financial transaction; it must NOT commit or return an external success receipt.
- write(command_id) -> exact V3 write or None; binding(command_id) -> validated private binding or None; request(command_id) -> original validated VenueCreationRequest or None. Public replay must verify exact durable attachments and retain original IDs, never grant/renew a permit.
- Current trusted load_clock(tx) returns actual typed ClockReading; load_lease(tx) returns current typed OwnerLease. No default clock_health=HEALTHY or inferred epoch. Public service's lease argument must agree with current host facts.
- mark_may_have_sent and server-only consume API still need actual implementation and exact signatures. They must use the same business owner/UoW and commit before returning a short-lived one-use permit; a prior MAY/UNKNOWN never issues another permit.

## Actual financial peer APIs (already in this checkout)

risk/program_financial_admission.py defines ProgramFinancialBundle(intent,basis,archive,decision,reservations) and constants:

- ARCHIVE_PREFIX='program-financial-archive:'
- BASIS_PREFIX='program-financial-basis:'
- DECISION_PREFIX='program-financial-decision:'
- BUNDLE_PREFIX='program-financial-bundle:'

All four are ledger.audit_events keys suffixed by intent_id. Intent uses SAME ledger.intents and reservations use SAME ledger.reservations. `_reserve_program(tx,intake_record_id,command_ids,*,load_inputs,load_qualification,load_action_proof,load_clock)` is a private writer returning an uncommitted bundle. It does not save_program: append_creation or the enclosing host must save/validate the actual versioned program once in the same transaction.

intents/program_assessment_archive.py defines ProgramFinancialAssessmentArchive (PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE, revision1), its strict Codec, financial_intent_from_assessment_archive, financial_risk_basis_from_assessment_archive. It replays actual compilation/target/exposure/RiskEvaluator. Do NOT substitute old ProgramFinancialArchive: its converter intentionally remains PROGRAM_RISK_FACTS_ARCHIVE_REQUIRED.

Use the real integration fixture tests/integration/test_program_financial_admission.py::real_case and its qualified synthetic capability setup. Pure risk-math ALLOW/caps NOT_RUN is not sufficient: current admission requires transportPAPER, PAPER_VERIFIED, independently loaded matching artifact references, current clocks/fence/authorization and actual current risk duties.

## Attachment and coordination

Parent /root is read-only coordinator/QA. /root/integration_next is delegated SOLE root binance-orders writer, wiring public service. /root/admission_review owns binance-opc-local-activation and shared transient financial attachment witness.

Activation peer is adding risk.program_financial_admission.claim_program_attachment(tx,bundle,*,mode,command_ids), with EXTERNAL_REQUESTS/LOCAL_ACTIVATION mutually exclusive, exact bundle/hash/current transaction and exact whole preassigned command ID set. It records the witness only after actual `_reserve_program` succeeds. Requested getter/inference for zero-asset bundles is pending: do not infer whole command IDs from reservations because reservations can be empty. Ask peer for tested commit/API, do NOT implement a parallel helper.

Local ALGO activation is internal, without a fake external mother or grants to ledger.control_commands. Initial external requests only in this slice; staged/local-generated children stay denied until a genuine parent/activation and fresh stage financial contract exists. Public parent_command_id must mean an actual external parent write, never a local activation row in another family. Venue-generated children are observation-only.

## Approved0012 / permissions / safety design

Planned filename: migrations/0012_program_command_boundary.sql.

- SAME order_writes_v2 JSON table, explicit V2/V3 decode. Preserve exact V2 first READY / immutable READY→MAY behavior and old mark denial. Add only V3 guarded transitions and exact immutable body references; no raw terminal/READY-regression escape.
- Private immutable full request-binding and client-identity records, namespaced by environment/account/product/service/client ID and command family. Include cross-legacy collision defenses without hiding historical same-text IDs.
- Existing outbox.kind CHECK currently permits only COMMAND_READY and DOMAIN_EVENT. Extend SAME CHECK to PROGRAM_COMMAND_READY; do not misuse DOMAIN_EVENT or create another outbox. Legacy outbox consumer explicitly filters COMMAND_READY.
- Root/integration lead APPROVED dedicated NOLOGIN ledger_program_dispatch_verifier, no memberships or grants to ledger_owner/control/decision. Security-definer MAY/consume functions and protected exact mark/consumption rows prevent ordinary-owner SQL/audit minting. Same business executor/lease, no second keyholder or cash ledger. Metadata append remains ledger_owner.
- MAY/consume require current independently sourced typed facts, exact complete archive/request/basis/ALLOW binding, current owner/deployment fence/lease/risk/reservations/qualification and both clocks. Default missing readers deny; no caller ALLOW boolean or audit row substitutes. Qualification is not inferred from construction or metadata.
- Must COMMIT MAY before returning permit; uncertain commit returns no permit. Retain original request/client IDs and uncertainty. Post-commit server consumer rechecks current owner/lease/qualification/risk and consumes once before any future controlled I/O. No actual transport is added.
- Shared storage/application_writes.py must recognize V3 and verify private binding/history while preserving conservative non-READY unresolved behavior, false-terminal rejection and READY regression defense. Exact program self-exclusion may be needed for postcommit recheck; do not reuse a legacy bare-ID exclusion that hides another family/scope.

## Next concrete work / tests

1. Implement private binding + actual same-transaction append/readback, using actual bundle and peer attachment witness.
2. Implement0012 and guarded MAY/consume; default-deny proof ports.
3. Add isolated PG fixture using existing IP03 helpers. Test owner/control forgery, request/route/type/IDs/wallet tamper, SAME ledger bundle+append rollback, commit failure/commit-then-error, MAY once/UNKNOWN replay, consumed permit, old V2 history/schema/hash and scoped collisions. No fabricated fills or account APIs.
4. Send narrow genuine source/PG commits to /root/integration_next and /root; they run combined root gate. No PG process handles exist in this worktree to restart.
