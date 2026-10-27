# Unified PAPER PostgreSQL validation, 2026-10-27

This is actual Linux/Python 3.12 and isolated PostgreSQL 17.11 evidence, not a
remote GitHub Actions run, exchange execution, Testnet qualification, or LIVE
readiness. The unified runtime integration still requires its own fixed-commit
validation. The foundational full gate below remains **INCOMPLETE** at its exact
recorded commit because one child-planning test fixture failed; its focused
correction passed separately. These two runs must not be presented as one green
full run.

## Fixed source and results

| Source | Command/scope | Actual result |
|---|---|---|
| `2920641c5326690969d1075d9e2f17eb466b194a` | Historical ownership module, real PostgreSQL | 133 passed, 1 failed, zero skipped; late deferred trigger expiry counterexample |
| `66bd023449dc` (full SHA in historical manifest) | Historical integrated PostgreSQL attempt | Ownership 134 passed; child fixture 30 errors, group fixture 2 failures; console not run because existing test DB was refused |
| `97c4f9d` | Fresh launcher initialization | Exit 2 before tests: default port information omitted by libpq; no marker or application objects created |
| `463792581b2da7416563ebf0303c8762dafa8ab5` | Whole 13-suite PostgreSQL runner after harness correction | 686 passed, 1 failed, zero errors/skips; child-planning fixture failure only; runner exit 1 |
| Same `4637925` | Ordinary full Python CI command, mandatory PaperEngine DB module separately covered | 2402 passed, 363 skipped, exit 0; skips remain explicit, not database evidence |
| `bda743e13466ca24801a9431142ce9ed4ef87a9a` | Fresh isolated child-cancel diagnostic database after fixture correction | 49 passed, zero errors/failures/skips, exit 0; diagnostic qualification field remains NOT_RUN |

At `4637925`, individual PostgreSQL suite passes were ledger 30, reservations 67,
duplicate-target 29, risk-locks 36, group-daily-loss 11, dispatch 61,
child-cancel 48 (plus one failure), execution-evidence 37, recovery 94,
protection 74, ownership 139, PaperEngine 59, console 1. The console case really
exercises concurrent transactions, restart, daily-loss rejection whose operation
rolls back, denial after rebound/restart, and permitted reduction.

The child regression was a fixture error exposed after the parent fixture changed
its initial book sequence from 1 to 2. The test's hardcoded replacement with 2
therefore stopped changing the book. Commit `24c7973` replaces that constant with
`current_sequence + 1`; the unchanged rejection assertion then passes. Production
planner and safety logic were untouched.

The default-port harness regression was reproduced by initial initialization:
`get_parameters().get('port')` was absent although `connection.info.port` was
5432. Commit `4637925` compares the actual effective numeric port. Other validated
endpoint identity checks remain intact.

## Fresh environment and reusable launcher

`tools/qualify_paper_docker.py --commit <full-sha> --python-suite` creates a new
PostgreSQL container and a new Linux runner, with no published host port. It
never discovers an existing application DSN, resets an existing database, or
deletes existing containers. Each mandatory module receives a new database.
Synthetic credentials and the Git bundle live only under ignored `.runtime/`;
the `evidence/` subdirectory is the publishable result. The new runner is stopped
at completion/deadline, and containers/databases remain available for inspection.
The whole-run deadline and individual command deadlines are bounded. Dependency
versions, actual image hashes, source commit, per-suite commands/exit codes,
migration hashes, and original outputs are recorded. Ordinary Python matches the
CI exclusion of the separately mandatory PaperEngine PostgreSQL module.

The foundational attempt created `aethertrade-qualification-pg-6f1db19c6a28` and
`aethertrade-qualification-runner-6f1db19c6a28`. Initialization first failed before
creating any objects. The corrected source then initialized that same newly
created, still-empty dedicated cluster. The child-only retry used another newly
created database in it. No older cluster or pre-existing console DB was reset.

## Owner CAS migration 0007 review and limits

Migration 0007 revokes PUBLIC temporary-object creation in the dedicated business
ledger database. Its SECURITY DEFINER wrapper uses a fixed search path, rejects
current callers with database TEMP/CREATE, schema CREATE, superuser, CREATEDB,
CREATEROLE, or BYPASSRLS authority, then invokes the original owner/group guard.
Direct function execution remains revoked from business roles. Migration 0005
requires safe NOLOGIN, NOINHERIT roles without role memberships at provisioning.

The retained baseline demonstrates why blocking late caller deferred work matters:
after real CAS and both projections, a caller-created deferred trigger could
sleep past authority expiry after the business commit check. The corrected suite
requires the attack to reach those genuine writes and rejects temporary-table
creation; ledger snapshots must remain unchanged. Existing attacks also cover
early SET CONSTRAINTS, named/DO/multiple-statement variants, final expiry,
reconciliation mutation, manual risk locks, and asynchronous commit.

Five added PostgreSQL cases use a real restricted LOGIN `session_user`, rather
than only administrator-assigned SET ROLE. They prove a successful CAS with both
projections, and rollback after attempted temporary DDL, SET ROLE administrator,
SET ROLE verifier, and RESET ROLE followed by attempted administrator escalation.
All five passed inside the 139-case ownership suite.

This is a boundary for freshly provisioned roles with no DDL/object ownership.
It is **not universal DDL immunity**: privileged session identities, migration
administrators, privileged role reassignment, concurrent administrator grants,
or pre-existing caller-owned relations/trigger functions are outside the proven
boundary. A table owner could have DDL authority without schema CREATE; 0007 does
not inventory existing object ownership. Host composition must keep verifier,
controller, and administrator identities separate and provision clean roles.
Deliberately pre-owned-object and concurrent privileged-grant bypass scenarios
were NOT_RUN. The final deadline sample is a pre-WAL check; these results do not
prove authorization validity at physical WAL flush or at an external venue.

## Original artifacts and provenance

- `owner-red.zip` and `partial-integrated.zip`: untouched historical raw logs/XML
  and the red summary where available. `historical-manifest.json` records exact
  hashes, migration hashes, parsed counts, and recovered parent tool command/exit
  metadata. The partial run never wrote its summary; recovered metadata is
  explicitly distinguished from original stdout.
- `fresh-foundation.zip`: initial gate failure, corrected whole-run logs/XML and
  summary, ordinary Python XML/log, dependency/image records, and child correction
  logs/XML/summary. `fresh-manifest.json` records checksums and supplemental exact
  commands. `run-corrected-foundation.py` preserves the executed followup driver.
- `synthetic-ledgers.json.gz` inside the fresh archive: read-only final SQL
  snapshots from 13 synthetic databases, 10,594 table rows. This is final-state
  evidence, not a fabricated before/after event transcript. Console authentication
  tables were excluded. `capture-ledgers.py` preserves the executed exporter.

Publication checks confirmed that the generated password and isolation marker
are absent from all exported files and decompressed synthetic snapshots. The
ordinary pytest XML contains only checked-in dummy `test`/`synthetic` DSNs in
parameter names. Credential files are excluded from every archive.

## Independent unified-runtime counterexamples

`runtime-counterexamples.zip` and its manifest retain four fixed-source, actual
Linux/PostgreSQL diagnostic runs. The latest red source,
`5ac12f05e52018bbdd82185d7ab2fa0bffc04de4`, has 7 passes, 3 failures, and zero
skips/errors. It includes runtime implementation `00d3955`, before its followup
fixes. The three remaining failures are:

1. A resumed READY command calls the real PaperEngine submit before querying its
   original identity. Instrumentation records actual delegated method calls;
   neither SQL nor the PaperEngine result is mocked.
2. A real Store pause transaction in a distinct console database completes while
   the business-ledger execution owner advisory lock is held. Equal advisory-lock
   strings in separate PostgreSQL databases provide no shared guard.
3. An old automatic target following a filled manual reduction is rejected by the
   protection-gap risk lock but lacks the independently persistent manual-override
   rejection `MANUAL_REDUCTION_REAUTH_REQUIRED`. This result does not demonstrate
   a successful refill; existing protection-gap safety still rejected the order.

The seven passing cases cover real spawned-child SIGKILL at READY durable,
MAY_HAVE_SENT before venue I/O, after venue commit, after ACK before projection,
and after staging the protection plan within a transaction. They verify the
original identity, no duplicate side effects, UNKNOWN retention when a query
cannot prove submission, rollback of partial fill/protection projection, and
exactly one fill after recovery. Two competing strategies cannot reserve the same
collateral, and a stale quote cannot create a command or reservation.

Per-case synthetic ledger and simulated venue JSON records preserve actual
before/crash/recovery states, including commands, reservations, projections,
fills, protection plans, positions, locks, and later audit/domain event histories.
Each case provisions fresh runtime and venue databases, with an additional fresh
console database for the authority-race probe. Database names and applied source
hashes are captured by the runner; credentials are not exported.

Two test-fixture mistakes are retained and distinguished in the manifest: an
existing zero-quantity position was initially mistaken for a partial transaction
write (corrected to compare against its actual before snapshot); `.25` was an
invalid wire decimal spelling (corrected to `0.25`). No production safety
assertion was removed.

`runtime-followup.zip` and its manifest preserve the subsequent red/green steps.
With runtime followup `7e26648`, the actual separate-database guard and persistent
manual override both passed; only the query-order regression remained red (9
passes, 1 failure). With followup `a5fa514`, the query-order regression also passed.
The added ACK boundary hook initially inspected the conservative raw command row,
which intentionally remains MAY_HAVE_SENT; reading the effective append-only
outcome through CommandRepository corrected that test instrumentation.

At fixed source `36322b8ffcbd4b6dd6ccd5662a8343e2d27681d3`, all **10 independent
runtime fault cases passed, with zero skips/errors, exit 0**. At source
`e5d895911af4e6bfd34ba961df40a8de3cf194db` (identical production runtime, before
the instrumentation-only correction), all **3 primary runtime cases passed**, zero
skips/errors, exit 0, including 40% partial fill plus CANCEL_UNKNOWN with full
reservation retention and immediate protection-gap responsibility. Both are
diagnostic runs; neither changes the full-gate qualification field to passed.

Full fixed-commit integration, ordinary CI fixture migration, and a fresh whole
PostgreSQL run remain required. Runtime protection repair/verification and
failed-protection-plus-failed-exit evidence are not demonstrated by these
diagnostics; protection remains explicitly GAP with protected quantity zero.
