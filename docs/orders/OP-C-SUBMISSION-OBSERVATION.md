# Source-backed submission observation

Approved continuation of execution/recovery scope; parent reserved0021. This is
submission knownness only. Original write states/hashes, full accepted working
quantity, holds, fills, ownership, protection and release duties remain separate.

`ProgramSubmissionObservationService(uow,scope,verifier=...).observe(command_id)`
accepts only actual scoped V1/V2 PAPER verifiers. Its independent SIM manifest
binds actual command/request, complete expected order IDs/revisions/lifecycles
and stored accepted terms, including genuine accepted-but-unfilled GTC. Actual
verification and fsync run outside every business/source transaction. Exact
`save_verification_snapshot(tx,verified)` persists source-verified zero-fill
snapshots through unchanged0017/0018 private SQL. Unsupported evidence is UNKNOWN.

Add0021 append-only `ledger.program_submission_observations`: only private
functions executable by existing ledger_program_source_verifier can append.
No automatic role membership, owner INSERT, new cash ledger or write-state
mutation exists. Every record binds committed original request/private MAY and
consumption. Known completion additionally binds an exact committed/private
snapshot, actual member IDs and accepted terms. Owner audit flags are ignored.

Each query first commits STARTED/UNKNOWN with a new probe ID, then appends one
COMPLETED/ACCEPTED, REJECTED or UNKNOWN result. Readers choose the newest STARTED
probe and its completion, preserving all earlier acceptance history. An older
delayed ACCEPTED completion cannot replace a newer UNKNOWN probe. Missing query,
archive or source data stays blocking; it never proves NOT_SENT or permits resend.

`read_program_submission_observation(tx,original)` is SELECT-only under the
existing group mutex. It rederives latest state and ever_accepted from protected
history and original context. ApplicationWriteObligation retains every original
quantity/hold/target duty; only its send-uncertainty check can use genuine known
submission. Same-scope target allocation, all cash/current owner/permit checks
remain. Accepted/rejected does not mean FILLED, terminal reconciliation or release.

Implementation steps:

1. Genuine PG RED after actual MAY/consume/venue acceptance: unfilled V1/V2 GTC,
   historical TTL, real post-only rejection, owner audit/role denial, later
   NOT_OBSERVED with ever_accepted preserved and blocking obligation.
2. Implement private0021 probe/completion boundary, exact typed observations,
   independent source manifest and pure reader. Keep shared source persistence
   owned by cash_fresh; select reviewed helper without competing file edits.
3. Couple obligation send uncertainty to the protected reader; prove genuine
   two-ready/two-accepted commands while all original holds and working duties
   remain. Prove source/ID substitution, stale probe completion, direct owner
   forged flags and missing/unknown evidence cannot unblock dispatch.
4. Fresh private PG, source/old-codec/accounting regressions, exact raw evidence,
   independent review. No owned financial writer or executable host promotion.

Full46/56 remains ACTIVE. Frozen producer1b12/0014df9e only; evolving native
parent/execution families and external modes remain unqualified.
