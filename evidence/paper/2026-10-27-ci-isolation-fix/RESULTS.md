# PostgreSQL CI isolation follow-up

Fixed source snapshot: `7c645a0` (`codex/unified-paper-execution`). This is a test-runner safety check, not full PAPER acceptance or an external GitHub Actions result.

Independent review found that inherited libpq routing could make the admin connection and console fixture select different servers/ports, that the console fixture DSN was written inside the uploaded evidence tree, and that fresh-database checks missed `pgdata`-style schemas and certain public objects. The fix requires an explicit permitted local host and port, rejects alternate `hostaddr`/service/options supplied in the DSN, clears inherited `PG*` variables while connecting, pins one resolved address for admin and console connections, and verifies the effective connection parameters. The credential-bearing temporary fixture is outside the full upload root, mode-restricted and removed on exit. Initialization rejects nonempty user schemas, relations, routines, types and public-namespace dependencies; existing markers are checked again on every run.

Real test: a new, dedicated PostgreSQL 17.11 cluster/database was created with synthetic credentials. In the Linux Python 3.12 runner the environment intentionally contained `PGPORT=9`, `PGHOSTADDR=192.0.2.123` and an unrelated `PGSERVICE`; those values were not used by the validated connection. On the owned disposable database only:

1. An existing `pgdata` schema was created. `--initialize` returned exit 2, retained the schema and created no marker. The empty test schema was then removed.
2. An existing public enum was created. Initialization again returned exit 2; the test-only enum was removed.
3. Initialization on the empty database returned exit 0. The following diagnostic suites then ran with separate newly created databases:

```text
python tools/ci_paper_postgres.py --suite ledger --evidence-dir /evidence/ci-safe
# gate exit 0: 30 passed, 0 skipped
python tools/ci_paper_postgres.py --suite paper-engine --evidence-dir /evidence/ci-safe
# gate exit 0: 59 passed, 0 skipped
python tools/ci_paper_postgres.py --suite duplicate-target --evidence-dir /evidence/ci-safe
# gate exit 1: 1 failed, 0 skipped — two same-target intents both reserved
python tools/ci_paper_postgres.py --suite console --evidence-dir /evidence/ci-safe
# gate exit 0: 1 passed, 0 skipped
```

The duplicate-target failure is an actual pre-fix security regression, not a skipped test. The saved console evidence tree contained no `postgres-test.env`. A focused test also verifies a temporary directory placed as a sibling of the run directory but still within the broader uploaded root is rejected.

Original per-suite logs, JUnit XML, summary JSON and initialization outputs are saved alongside this report as lossless `.xz` files. They were scanned against the synthetic test passwords, local application database password and any present model API keys before inclusion. Diagnostic `--suite` summaries explicitly mark overall qualification `NOT_RUN`. The full required PostgreSQL matrix, original active-owner CAS, unified execution/exit/protection path and remote GitHub Actions remain separate outstanding work.
