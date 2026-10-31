# Historical fill source approval boundary

Business migration 0016_program_fill_source_approval.sql is additive. It must be
applied by the isolated migration administrator after 0012; it changes no old
schema, command hash, event or UNKNOWN history. SIM migrations 0013/0014 and
quote context migration 0015 are independent serial integration work.

Apply0017_program_fill_source_snapshots.sql after0016 for new writers. It adds
one protected source-snapshot table, a snapshot approval function and compact
V2 per-fill approvals referencing the exact snapshot/normalized hash. It does
not edit0016, its original function or historical V1 records. New readers handle
both explicit record kinds. No migration silently rewrites old approvals.

The original writer repeated the complete report/all-rival proof in every
per-fill approval. A modest real-source RED proved this amplification; V2 shares
one immutable proof instead. CommittedProgramFillSources is scoped to one live
owner transaction and loads/hashes that immutable shared snapshot once for batch
reads. It caches no current book, owner, permission or risk decision and cannot
be reused after its transaction closes. See the real thirteen-case PG cohort.

Apply0018_program_fill_source_leaves.sql next. Compact parameters alone did not
prevent the old per-fill SQL function from repeatedly loading/decompressing
the full JSONB snapshot. A genuine nine-fill probe measured nine extra heap
hits and1071TOAST block reads/hits.0018 materializes immutable protected header
and normalized leaf indices once; per-fill approval reads only one compact
header/leaf. No current owner, book or risk decision is cached in those indices.

0018 renames the previous snapshot validator and explicitly revokes its
inherited PUBLIC/source runtime execute ACL, then installs a protected wrapper
at the original signature to validate once and materialize indices atomically.
The per-fill function keeps its original payload/hash/result semantics. Existing
snapshots are backfilled into the new index tables without editing any old
payload, hash or physical tuple. A real0017→0018 test verifies unchanged old
xmin/content, old read/replay and subsequent genuine fills. New runtime roles
receive no direct INSERT/UPDATE/DELETE on either index table.

The new NOLOGIN ledger_program_source_verifier has no automatic role members.
It receives the existing collateral transaction locks, minimal source SELECTs,
transaction observation INSERT and EXECUTE on the single fixed-search-path
SECURITY DEFINER approval function. It cannot directly INSERT/UPDATE/DELETE
approvals or post fills/cash/books. Owner, control, decision, dispatch verifier
and closure resolver cannot invoke approval or insert its table. Provisioning a
trusted verifier process requires a separately reviewed deployment identity;
this migration does not enable an ordinary server request or external adapter.

ProgramFillSourceService accepts an original command ID and an actual scoped
PaperProgramReportVerifier dependency. The verifier queries the original PAPER
execution and fsyncs its raw evidence before the approval transaction begins.
The approval function rechecks the committed original request/binding and
protected mark/consumption, including exact unit/source commitments. Immutable
approvals include the native fill/economics/fees, accepted context, archived
sources, all-rival market allocations, discrepancies and retained duties. They
contain no balance total and issue no execution or release authorization.

read_committed_fill_source requires another physical transaction's committed
row, validates its hash/native identity, rederives the native-to-ledger unit and
gross bridge, and rechecks original protected history without file/network IO.
Old execution TTL does not erase a historical fill; it also cannot authorize a
new send. A new owner posting transaction must independently check actual
FillLedger evidence, current book/allocation and justified economics.

On archive or verification failure, approval is absent and original IDs remain
available for query. Source commit failure rolls back the approval; no receipt
is returned. A committed approval followed by owner posting failure is harmless
historical evidence: retry the original ID, re-read the approval and deduplicate
by the native fill identity. Never resend an order, mint a permit, release a
reservation or repair history to recover accounting.

Repeated observations may attest the same native identity with distinct report
snapshots. Future posting/balance readers must enumerate each native identity
once and reject conflicting economics; summing approval rows is forbidden.
Economic discrepancies or unknown fees preserve raw facts and reconciliation
duties. USD-M principal is not Spot cash; Equity CARD/MAIN binding is unresolved.
ProgramPaperAccountingService now consumes those approvals and captures actual
source evidence/native fills, bridge, notices and unresolved recovery locks in
one existing ledger transaction. On owner commit failure all those owner writes
roll back while the separate source attestation remains committed and original
IDs stay available for retry. Duplicate native IDs retain the original receipt;
approvals are never counted as additional executions. No send, cash credit,
position ownership, protection or funds release follows from raw ingestion.

Asset postings, positions, mixed legacy refresh and full financial accounting
remain required next. The raw service reports its economics blocker explicitly.

Until the authenticated shared contribution reader is installed, existing
UnifiedPaperRuntime._cash/_book refresh fails closed if this collateral group
contains a protected generic source approval. The existing book is preserved
and the public error is PROGRAM_ACCOUNTING_RECONCILIATION_REQUIRED. Legacy
databases without these tables and pure legacy histories retain their original
path. This guard is tested with actual source-approved fills and the full56
mandatory console contract regression; it is not a substitute for final asset
postings/position accounting.

Rollback means disabling the new host composition while retaining the additive
tables/approvals. Do not drop or rewrite committed source/history. Missing 0016
or missing verifier identity must fail closed; neither permits a legacy cash
refresh to overwrite new programme effects.
