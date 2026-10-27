# IP-16 owner CAS PostgreSQL evidence

Tested source: `4240e481304cae575252239dcff411b29786f7b7` (base `99c7e7a`).
Linux Python 3.12.16 / PostgreSQL 17.11: **123 passed, 0 failed, 0 skipped;
exit 0**. Exact command, source/migration SHA-256 values, applied migration
order and raw artifact hashes are in `summary.json`. The source hashes were
checked unchanged before and after execution. `0003` is hashed for inventory
only and was not installed by this independent ownership suite.

The Python runner used `python:3.12-slim` in its own container on the existing
explicitly isolated PAPER network. The fresh database was
`ip16_test_owner_cas_65634443a08c` on the sibling `paper-execution` disposable
cluster; no main application database or trading account was used. The fixture
requires Linux, `IP16_ISOLATED_DATABASE=YES` and `IP16_DATABASE_DSN` selecting a
fresh `ip16_test_*` database. DSN/password values are deliberately omitted.

`ownership.log.xz` and `ownership.xml.xz` retain raw pytest output/results.
`crash-ledgers.json.xz` extracts the original raw before/after histories retained
in the XML properties for the two formerly NOT_RUN process-crash scenarios:

- `after_epoch_before_commit`: actual child process exit 72; active owner and
  all durable projections retain epoch 1 / COMMIT_OWNER_EPOCH. Safe replay then
  completes once using the unconsumed authority.
- `after_commit`: actual child process exit 73; active owner and ACTIVE transfer
  projection durably retain epoch 2 / new owner. Terminal replay preserves it.

Both retain system protection responsibility, exact quantity and PortfolioBook,
and the unresolved freeze risk cause. No simulated callback replaces the SQL
CAS in these process tests. The original missing-reviewed-CAS blocked case and
all original security counterexamples are retained.

Additional real PostgreSQL checks exercise unprivileged session-user role
forgery, private approval/function denial, absence of broad UPDATE privileges,
next-epoch/scope/policy/owner binding, exact quantity/unit and coverage proof,
missing/NaN coverage, protection duty and plan acceptance, authorization changes,
revocation and expiry after waiting on the mutex, stale ledger/owner revisions,
controller CAS without atomic projections, concurrent advancement and consumed
authority replay. The verifier inputs are explicitly synthetic trusted local
archive facts; the database/privileges/transactions/contention/process exits are
real. The Decimal test preserves a quantity beyond IEEE-754 precision and the
default Decimal context, while isolating the sibling precision correction.

The initial implementation run recorded 84 passes and two actual crash failures
from a JSONB operator-precedence error in the deferred guard; that SQL expression
was corrected. A later run found three malformed-evidence fixture serialization
errors before SQL; canonical JSON serialization was corrected. The final fixed
source run above has no errors or skips and does not weaken those negatives.

**NOT_RUN:** production authorization/archive verifier; actual venue protection
continuity; native adapter qualification; TESTNET; LIVE; approved T/Q platform
qualification. This is storage/security prerequisite evidence, not LIVE-ready
or a venue qualification claim. Default composition without a reviewed verifier
and controller authority remains blocked.
