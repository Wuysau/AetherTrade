# Authoritative unsent-abort security follow-up

Independent review found a P1 in `366976f`: audit-only `ABORTED_UNSENT` prevented
the IP-10 dispatcher from sending, but the base `LedgerTx.commands` port still saw
the raw READY command. After a legitimate reservation release it could issue a
send permit; direct owner-role SQL could also append MAY_HAVE_SENT. The earlier
147-test run did not cover these bypasses. This follow-up supersedes the earlier
unsent-abort security conclusion.

## Repair

Additive `0004_command_abort_guard.sql` introduces an immutable authoritative abort
table. Execution-owner rights can read it but cannot insert/update/delete records.
The constrained fixed-search-path SECURITY DEFINER abort function requires the
exact scope, command identity, raw READY command JSON, immutable request JSON,
SHA-256 request hash, receipt command/status, and original durable reference.
The SQL digest is computed over the supplied request bytes and the parsed request
must equal the immutable stored command. Python additionally verifies the approved
canonical command hash when reading the authoritative abort.

The function locks the collateral mutex before reading command state. A new
`command_abort_guard` locks the same mutex before allowing any SQL MAY_HAVE_SENT
insert and rejects commands with an authoritative abort. The original
`command_guard` is unchanged and still validates the immutable READY-to-MAY
transition. No custom setting, trigger disablement, original migration rewrite,
direct abort-table grant, or role escalation is used.

Both the base storage command repository and the IP-10 command repository now
observe authoritative aborts. The IP-10 reader rejects audit-only unsent outcomes.
The authoritative record and audit append share one transaction; an audit failure
rolls both back. Shared command hash/outcome types moved into a storage module so
the base repository can validate them without a circular dependency. Internal
abort audit provenance remains explicit; the outward wire provenance uses the
already approved `LOCAL_POLICY` enum.

Migration 0004 is installed explicitly after roles.sql in every Linux PG test
fixture, after 0002 in the risk fixture. It does not depend on the independently
reserved PAPER migration 0003. An older database missing 0004 fails closed when
the base repository consults the missing authoritative table.

## Fresh actual PostgreSQL verification

Dedicated Linux Python 3.12 container, read-only source bind, real PostgreSQL
17.11 isolated qualification cluster; fresh databases:

- `ip03_test_abortguard_final_9643b7d0`: migrations 0001 and 0004, original roles.
- `ip07_test_abortguard_final_9643b7d0`: migrations 0001 and 0004, original roles.
- `ip08_test_abortguard_final_9643b7d0`: migrations 0001, 0002, 0004, original roles.
- `ip10_test_abortguard_final_9643b7d0`: migrations 0001 and 0004, original roles.

Actual command inside the dedicated runner with synthetic isolated DSNs exported:

```sh
cd /tmp/app
python -m pytest -q tests/integration/test_ledger_transactions.py tests/integration/test_reservations.py tests/integration/test_persistent_risk_locks.py tests/integration/test_durable_dispatch.py --junitxml=/evidence/abortguard-final.xml
```

**190 passed in 23.40s; zero skips; exit 0.** Raw artifacts are retained under this
worktree's ignored `.runtime/abortguard-final.log`, `.xml`, and `.exit`.
The preceding expanded run was 184 passed/6 failed due to the internal provenance
wire-enum bug; `.runtime/abortguard-targeted.log` preserves it. Correcting the
projection to existing `LOCAL_POLICY` resolved that failure without changing a
schema or relaxing assertions.

Regression coverage includes:

- Command + reservation -> authoritative abort -> genuine mapped NOT_SENT proof
  -> held amount zero -> base permit and direct owner SQL both denied.
- Concurrent abort versus base permit, both first-winner orderings; concurrent
  abort versus raw owner SQL, both first-winner orderings. Exactly one boundary
  succeeds; the abort winner may release backing, the send winner retains it.
- Forged scope, changed command, changed immutable request, mismatched hash,
  wrong receipt command, and direct private-table insertion all rejected.
- An audit-only forged unsent outcome cannot release reservation backing.
- A failure after the authoritative write rolls it back with the audit; the
  original ready command remains legitimately sendable afterward.
- Existing pre-abort send, durable commit marker, UNKNOWN query/recovery,
  no-replay, immutable-ledger, resolver-role, and crash regressions all pass.

`.runtime/abortguard-runtime-proof.json` records actual non-superuser
`ledger_owner`, denied private INSERT, constrained abort-function EXECUTE, fixed
`pg_catalog, ledger` search path, and enabled original/new command guards in all
four databases. `.runtime/abortguard-ledger-snapshot.json` retains initial/raw
commands, authoritative abort outcomes and hashes, and reservation creation and
release projections from the tests.

Applied migration SHA-256 values:

```text
0001_business_ledger.sql b3bfbaaec9dbeeb207297a395290dd4083d5b6101c98a11e240d36a9b32b5dc6
0002_risk_resolution_authority.sql f2597dd09832aef2b4fa1a82f0152c10226b613cdbb5ffce24285ffc6b0793a4
0004_command_abort_guard.sql 428439cfccb05811629c8eed4b8866b2d28a4a69fb2a10341300c46ee7165704
```

The separate outage-only emergency journal retains its existing preauthorized,
bounded CLOSE/CANCEL/SET_PROTECTION contract; this change addresses PostgreSQL
command/reservation permits. Full coordinated repository/PAPER integration is
**NOT_RUN here**. No native, TESTNET, or LIVE gate was enabled.
