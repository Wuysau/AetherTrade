# OP-C immutable intake and execution-owner separation

The isolated PostgreSQL admission probe at source `ca3984c` failed all five
cases because the one-phase ingress ran proposal INSERTs as `ledger_owner`.
Migration0006 intentionally forbids that role from authoring proposals. The fix
does not widen its privileges or combine intake and execution roles.

Apply0010 after0001, deploy/db/roles.sql,0006 and0008 under the administrator
migration process. Fresh isolated provisioning also includes0009.0010 adds only
`ledger.order_program_intakes`: immutable original versioned proposal/program,
compilation, context hash and execution policy, keyed by scope and accepted
intent ID. Only bounded `ledger_intake` can insert; owner can read. Old public
schemas, proposal/command hashes, rows and event IDs are unchanged.

The host must supply two separate UnitOfWork connection factories. Intake first
commits the existing AcceptedProposal and original-program source atomically as
`ledger_intake`. The receipt remains VALIDATING. A fresh owner transaction then
re-reads that source and accepted proposal and revalidates current compilation,
rules, qualification, authorization and locked target facts. Existing IP07 writes
Intent/Risk/Reservation/Command plus the complete command sidecar atomically.
No intake receipt is a permit; no send may occur before the owner commit.

If the process stops between phases, only a VALIDATING intake exists: no funds
or command. Retry the exact original proposal/context to finish owner admission.
A rejected or failed owner transaction rolls back all financial writes, retains
the original intake, and never returns an accepted financial receipt on commit
failure. Replay of a committed admission returns the original outcome and does
not renew execution authority. Working/UNKNOWN commands retain their original
IDs and normal query/recovery rules.

Historical owner-authored `order-program-intake:*` audit sidecars remain readable
for diagnostics/recovery and still block the legacy reserve entry point. They
cannot substitute for new bounded intake provenance on a send. Do not backfill
or rewrite them automatically; historical UNKNOWN recovery must query the
original command, never re-admit or resend it. Older databases lacking0010 can
continue ordinary legacy operations; program sends fail closed until migration
and genuine source admission. Rollback disables new program intake/sends and
keeps the additive table/history for recovery rather than dropping evidence.

Qualification proofs in the admission tests are explicitly synthetic local
fixtures. Passing these tests does not qualify a deployed native adapter,
enable TESTNET/LIVE, or prove any full ORD venue capability.
