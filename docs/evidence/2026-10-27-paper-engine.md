# Controlled PAPER engine: scoped verification

Date: 2026-10-27. Source base: `3062154beb0dcade0711b93ecca060376e9773be`.
Implementation is the focused commit containing this evidence, not a LIVE or Testnet qualification.

The adapter implements EnginePort.submit/cancel/amend/query. Its simulated venue
state lives in additive PostgreSQL tables from `migrations/0003_paper_engine.sql`;
every table rejects an environment other than PAPER. The dispatcher still owns
the separate business-ledger durable-before-side-effect boundary. Its files were
not changed by this subtask. No business-ledger transaction is opened by this
adapter; its own transaction is the independent simulated venue operation.

`NautilusEngine.live_enabled` remains False and `enable_native_path` still raises
`NATIVE_PATH_NOT_QUALIFIED`. The owner retains its exact NautilusEngine check and
adds only exact PaperEngine in PAPER with a matching shard; subclasses and
arbitrary EnginePort objects cannot enter the owner composition.

## Behavior and integration contract

- Provision a truly isolated synthetic-only PostgreSQL database and apply SQL0003.
  The adapter never creates a database or discovers account credentials.
- Before committing an ExecutionCommand, compute its client ID with
  `paper_client_order_id(scope, logical_client_order_id, operation)`.
- Supply server-held `authority_guard(command_id)` yielding BridgeAuthority. It
  must reload the committed command/permit, recheck current authorization and
  ownership, and hold the exclusive owner/revocation guard. Close all business
  ledger transactions before yielding. This is a trusted composition boundary,
  not a callback or authority object accepted from an API request.
- Submission acknowledges reception with a SUBMITTED order and zero fill.
  SET_PROTECTION reception does not verify protection. CANCEL/AMEND retain
  UNKNOWN and a pending operation until an explicit scenario event resolves it.
- SyntheticOrderEvent injection is disabled unless a separate trusted event
  controller guard authorizes the exact PAPER scope. AMENDED must reference the
  exact current pending amend command. Events retain exact decimal quantities
  and prices, and duplicate identical event IDs have no second economic effect.
- The simulated side effect commits before the optional lost-ACK timeout.
  Replayed command/client IDs are rejected across engine instances and process
  restarts. ReconciliationCase.command_ids resolves the persisted original
  client ID for both submissions and maintenance commands.
- Query returns synthetic orders and explicit fill evidence. Balances,
  positions, ownership allocations, economic history, liquidity, fees, slippage,
  and protection verification remain outside this component. Coverage explicitly
  includes `PAPER_BALANCES_POSITIONS_PROTECTION_NOT_COVERED`; the report alone
  cannot unlock recovery or release reservations.

## Real test environment and command

Dedicated PostgreSQL container: `aethertrade-paper-qualification-5fbf2cf3`,
network: `aethertrade-paper-qualification-5fbf2cf3-net`.
Fresh synthetic database: `paper_engine_0c7e68f94c0e`.
Migration: SQL0003, SHA-256
`a6fd2181cf441bb5f48f2c0e536749688a5c9f9565fbf56187f73b4f1d81e0e8`.
No production database or real exchange account was accessed.

Runtime: Docker `python:3.12-slim`, PostgreSQL `postgres:17-alpine`; pytest runs
under Linux UID/GID 10001. The source mount is read-only and copied to
`/tmp/paper-engine`, excluding `.git`, `.venv`, `.runtime`, caches and node_modules.
Credentials are injected only through an ignored env file. The runner starts as
root only to install the four verification packages, then drops groups/UID/GID
before running pytest. No credentials appear in evidence or command arguments.

```text
python -m pytest tests/integration/test_paper_engine_postgres.py \
  tests/qualification/test_nautilus_bridge.py \
  tests/faults/test_owner_and_storage_failures.py \
  -k "not backup and not restore" -q -p no:cacheprovider \
  --junitxml=/tmp/paper-engine-results.xml
```

The mandatory PostgreSQL fixtures require `PAPER_ENGINE_ISOLATED_DATABASE=YES`
and `PAPER_ENGINE_DATABASE_DSN`; absence is a failing NOT_RUN result, not a skip.
The fresh database was explicitly created within the dedicated synthetic
container using `CREATE DATABASE paper_engine_0c7e68f94c0e`.

Result: **198 passed, 8 deselected, exit 0; zero skipped.** This includes 27
new real-PostgreSQL adapter tests, concurrent duplicate delivery, a separate
process killed with `os._exit(23)` after the venue commit and before ACK delivery,
same-ID query/replay refusal, partial fill/restart/full fill, unknown cancel with
late fills, explicit cancellation/expiry/rejection, duplicate events, stale
amendment association, scoped query failure, forged/stale permit and authority,
owner concrete-type checks, native gates and the existing Linux flock probe.

Raw pytest output and JUnit report are saved alongside this document's referenced
qualification evidence. Runner/env/database metadata remain under ignored
`.runtime/`; credentials are excluded from the commit.

## Failures recorded and limitations

The initial new test collection failed because PaperEngine did not yet exist.
The first 15 engine tests then passed against real PostgreSQL. An expanded run
had 23 passes and one fixture failure because the child process lacked
PYTHONPATH; setting its explicit source path fixed the fixture and the forced
post-commit crash test passed.

An unfiltered Windows native/owner run returned 170 passed, one Linux-only skip,
and eight existing backup fixture failures due to `Money(value=...)`. Those
eight were explicitly deselected for this independent adapter run; their P0
repair belongs to the separate owner of that work. They were neither modified
nor counted as passing here.

Full console composition, business reservation conversion/release, managed
positions and protection responsibility, command-resolution persistence, and
end-to-end approval are integration work outside this subtask. Exchange
Testnet/LIVE, native adapter, real liquidity/slippage, and production deployment
verification: **NOT_RUN**. All existing gates remain closed.
