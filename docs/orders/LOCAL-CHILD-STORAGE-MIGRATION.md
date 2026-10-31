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
