# Owner CAS final-commit security review evidence

Fixed source: `4cd769c6dd8061d2a67ddf7e25aaaac0f29d2b66`.
Linux Python 3.12.16 / PostgreSQL 17.11: **133 passed, 0 failed, 0 skipped;
exit 0**. See `summary.json` for the exact test command, fresh isolated database,
source/migration hashes, applied migrations, artifact hashes and explicit limits.
Source hashes were checked unchanged before and after the complete run.

Independent review found two real bypasses in the original CAS implementation.
`review-red.log.xz` and `review-red.xml.xz` retain six actual failing PostgreSQL
reproductions against the original controller role, before the fix: an early
`SET CONSTRAINTS ALL IMMEDIATE` followed by a conflicting owner/transfer append,
an authority-expiry sleep with and without an early check, a later ACTIVE manual
intervention lock, and a later reconciliation amendment. Every case originally
committed instead of rejecting. These were new tests added over the original
`ace73ae` source; the original migration SHA-256 was
`42f0d0623e8006901fa2286ed85126d4a2df8c80b661e1fbb6a486c2cc0ba586`.
The red working-tree tests were exploratory; their raw output is retained and
is not represented as the final fixed-source run.

The fix introduces non-deferrable fences on every attested snapshot source and
on owner/transfer inserts. After a CAS approval, only the exact expected final
owner and ACTIVE transfer revisions may be appended; a later risk/source change
or contradictory projection rolls back. The deferred guard rejects execution
outside a single top-level synchronous COMMIT/END query, preventing an early
constraint check from consuming the final check. It then rechecks current
expiry/revocation, all snapshot sources and the exact active owner under the
still-held collateral/owner guard. No role receives broader mutation authority.

The final suite retains all original scenarios and adds ten post-CAS attack
cases, including named constraints, DO-block and multi-statement early checks,
and a later `synchronous_commit=off` downgrade. Each asserts rollback of the
active row and raw owner/transfer/freeze histories. The two original process
crashes still genuinely exit with 72/73 and prove precommit rollback versus
durable new owner plus projections. Their complete before/after histories are
in `crash-ledgers.json.xz` and in the raw XML properties.

`ownership.log.xz`/`ownership.xml.xz` are the raw final output/results. The test
runner used its own Python 3.12 Docker container on the sibling PAPER disposable
cluster, a fresh `ip16_test_*` database, explicit isolation opt-in, synthetic
trusted local archive facts and real PostgreSQL privileges/transactions/locks.
No main application data, trading account, exchange adapter or real credential
was involved. DSN/password values are omitted.

**Limit:** the enforceable boundary is the final SQL precommit phase. A pure SQL
trigger cannot check a wall-clock deadline after the later physical WAL flush.
That stronger post-WAL guarantee is **NOT_RUN / unavailable**, not a claimed
passing result. If required, the opt-in capability must stay disabled pending
a separately reviewed database commit mechanism. No production composition is
enabled here; default missing-reviewed-CAS and missing-verifier paths still deny.

Production authorization/archive verifier, real venue protection continuity,
native qualification, TESTNET/LIVE and approved T/Q platform qualification also
remain **NOT_RUN**. The older 123-case evidence is explicitly superseded.
