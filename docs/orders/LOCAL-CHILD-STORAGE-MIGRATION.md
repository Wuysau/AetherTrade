# Local child commitment storage candidate (0030)

Status: IMPLEMENTED_NOT_APPLIED / PostgreSQL NOT_RUN. Shared DEV recovery,
isolation and unified scheduler are absent, confirmed by the user on 2026-10-31.
No migration or database test was executed. Python test syntax and whitespace
inspection are not PostgreSQL proof; no database RED/GREEN is claimed.

0030 adds ledger.local_child_commitments_v1 within the existing business ledger.
It stores immutable original child commitment bytes, local activation/command
identity, content hash and application/database transaction identifiers. It does
not reuse or rewrite old V3 command rows. The initial constraints check shape,
PAPER/RESEARCH scope and hashes; they do not authenticate financial admission.
No runtime INSERT grant, writer function, permit, transport or automatic startup
registration is supplied. The table is deliberately dormant until the full
protected admission boundary is implemented and verified.

The five authored PostgreSQL cases verify runtime role write denial, malformed
record denial and append-only behavior. They are NOT_RUN, not skipped PASS.
Future validation must additionally cover default privileges and role inheritance,
actual source ancestry, command namespace collisions, complete-set locks,
concurrent allocations, commit failures, restart and original UNKNOWN queries.
Those remain required before installing an application writer.

Apply only through the approved shared DEV migration process, after prior ledger
migrations (including 0012 digest functions) and with writers quiesced. TEST
migration/deployment requires user approval bound to the exact candidate. Do not
apply this candidate to restore or recreate the lost Docker database.

Recovery/rollback: stop future new child writes first; inventory all commitment
records and corresponding command/funding duties. Preserve table, IDs, payloads
and original codec during downgrade; no destructive down migration is provided.
An older worker cannot take over these records or manufacture a V3 parent ID.
No migration rollback may release funds or retry an uncertain child.


The unapplied candidate now includes SELECT for ledger_owner and
ledger_program_dispatch_verifier, solely to retain child duties in the existing
local-program obligation registry. INSERT/UPDATE/DELETE/TRUNCATE remain revoked.
This is a change to an unexecuted candidate, not a claim about deployed privileges.


The unapplied candidate additionally binds each scope/activation to the exact
sequence-one local-activation audit record with a generated-key foreign key.
This prevents orphan commitments from being hidden by parent-first obligation
scans. Six PostgreSQL cases now collect; all six remain NOT_RUN. Inspection and
collection do not establish actual FK enforcement or migration compatibility.

## Additive admission candidate (0031)

Status: CODE_IMPLEMENTED / MIGRATION_NOT_APPLIED / PostgreSQL NOT_RUN. Source
`64b3a50`. 0031 is a separate additive candidate; 0030 remains byte-unchanged
except for no edits in this round. It adds:

- `local_child_writes` state history (DURABLE_READY -> MAY_HAVE_SENT -> UNKNOWN)
  for the child's own command, never a fabricated V3 parent command.
- `local_child_dispatch_marks` and `local_child_permit_consumptions`, bound to
  the commitment row and single-use token hash.
- A deferred completeness trigger proving command and commitment both exist at
  commit, a quantity/unit pool guard against the activation authority node, and
  an activation-source guard for every write.
- `LOCAL_CHILD_COMMAND_READY` outbox kind; INSERT only to `ledger_owner`, with
  no PUBLIC grant and no transport.

`execution/local_child_admission.py` implements the matching Python boundary:
mutex/scope/activation/request/quantity checks, a trusted current-facts port
(clock, lease, owner, target, funding, authorization, risk), atomic persistence,
replay without re-allocation, permit issuance/consumption and UNKNOWN retention.
It is not imported by runtime composition. A weak or unauthenticated facts
loader remains a real risk; the SQL candidate must pass actual role, concurrency,
rollback, crash and recovery cases before any grant is deployed.

Unit/contract evidence: 104 focused cases and 291 broader local-child/ops cases
passed (10 dormant PostgreSQL skips) at source `64b3a50`; evidence is under
`docs/evidence/binance-orders/local-child-admission/`. No database, Docker,
venue, TESTNET/LIVE or TEST action occurred.
