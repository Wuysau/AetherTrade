# PAPER fresh provisioning and schema isolation

Source base: `c1cf7704130212fbf429e5dd424a4b3eb005cc8c`. The previous
`paper_sim`-only setup guard did not cover the public relations actually created
by SQL0003. This followup supersedes that provisioning claim; existing engine
state is never reused or regranted by fresh setup.

Before migration, grants or role creation, provisioning checks the actual
database prefix and rejects the existing `paper_sim` schema or **any relation
kind** named `public.paper_engine_orders`, `public.paper_engine_commands` or
`public.paper_engine_events`. SQL0003 is fresh-only: explicit public-qualified
CREATE TABLE statements have no IF NOT EXISTS fallback. A concurrent relation
collision aborts its transaction, including all staged DDL, before role setup.

Provisioning and engine connections override client/role search_path with
`pg_catalog`. Every engine table, foreign key, grant and table/column privilege
lookup is explicitly qualified with `public`; runtime also verifies that the
three expected public relations are ordinary tables. An untrusted schema cannot
redirect those queries or privilege checks, even when deliberately placed before
public/pg_catalog in the supplied DSN. Fresh provisioning remains usable.

Real PostgreSQL negative tests cover each reserved name as a preexisting TABLE,
VIEW and SEQUENCE. They compare relation OIDs/kinds/owners/ACLs, columns/types/null
constraints, public schema ownership/ACL, database ACL and stored records/sequence
state before and after refused setup. Each case also verifies zero proposed
roles and zero partial DDL. Separate tests cover the retained paper_sim guard,
malicious administrator search_path during fresh setup and malicious runtime
search_path with a shadow command table whose marker must remain unchanged.

Before the fix, all nine new existing-relation contracts failed against the old
implementation; the raw pytest short-summary output is recorded in
`2026-10-27-paper-engine-provisioning-red-summary.txt` (exit 1). Test database DSNs
are redacted from fixture repr and no credentials are committed.

Final real environment: dedicated container
`aethertrade-paper-qualification-5fbf2cf3`, network
`aethertrade-paper-qualification-5fbf2cf3-net`, fresh primary database
`paper_engine_3dd9871703b4`. Each provisioning negative test allocates its own
fresh `paper_engine_guard_*` database in that same synthetic-only container.
No existing database/schema/role is deleted. SQL0003 SHA-256:
`4028e5495f2f6fa8973d8e1d3f3ae9cdfada83b1c3032db08303a5a41e725692`.

The runner uses Docker `python:3.12-slim` and `postgres:17-alpine`, copies a
read-only source mount into `/tmp`, then runs pytest as Linux UID/GID 10001.
Administrator sessions only provision/observe the synthetic databases; actual
engine and event-controller operations use their restricted separate roles.

```text
python -m pytest tests/integration/test_paper_engine_postgres.py \
  tests/qualification/test_nautilus_bridge.py \
  tests/faults/test_owner_and_storage_failures.py \
  -k "not backup and not restore" -q -p no:cacheprovider \
  --junitxml=/tmp/paper-engine-results.xml
```

Result: **230 passed, 8 deselected, zero skipped, exit 0**. This includes 59 real
PostgreSQL adapter/security cases plus existing native bridge/owner/Linux flock
regressions. The unchanged eight backup fixture failures remain assigned to the
separate P0 repair and are not counted passing here. Raw green output and JUnit
are `2026-10-27-paper-engine-provisioning-pytest.txt` and
`2026-10-27-paper-engine-provisioning-junit.xml`.

No native gate was changed. Full-console integration and independent re-review
remain separate; Testnet/LIVE and real account execution remain NOT_RUN/closed.
