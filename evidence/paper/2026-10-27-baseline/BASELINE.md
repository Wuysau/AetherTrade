# Unified PAPER baseline — 2026-10-27

This is pre-fix evidence, not qualification or a claim that the new execution chain exists.

## Scope and original authority

- User task: `TradingHandoff/AetherTrade_Next_Engineering_Task_2026-10-27.md`.
- Source snapshot tested: `3062154beb0dcade0711b93ecca060376e9773be`. It differs from the task's public review baseline `acbdcb2b2966d46fb004a01ce3e32034b8484d2f` by the default public-market Fake-IP DNS fix only.
- User confirmed the complete architecture SPEC on 2026-10-20. All 23 files listed in `TradingHandoff/AI_Trading_System_Implementation_Package_2026-10-21/docs/spec/spec-manifest.json` match their approved SHA-256. Approval is not native, Testnet, account or LIVE authorization.
- Original 66 acceptance scenarios, 14 invariants, 10 quality gates, existing task IDs, all three decision modes, and the approved Spot, USD-M and real-security scope are retained. No approved files were edited.

## Public CI

[GitHub Actions Tests #12](https://github.com/Wuysau/AetherTrade/actions/runs/40314658504) ran on `acbdcb2` and failed: `pytest` exit 1 with **2095 passed, 240 skipped, 20 failed**; the `web` job passed. The workflow installs Python 3.12 and runs `python -m pytest -q`, but does not provision or require the isolated PostgreSQL checks. The later local source `3062154` has no confirmed public CI run; remote job logs and job status were read from GitHub, with no workflow rerun or remote mutation.

## Windows source baseline

Windows 11, Python 3.14.2, source `3062154`:

```powershell
$env:PYTHONDONTWRITEBYTECODE = '1'
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --junitxml=.runtime/paper-baseline-20261027.xml --tb=short
# exit 1: 2115 passed, 241 skipped, 20 failed, 4 errors
```

The four reported errors are setup/teardown phases of two cases whose default pytest parameter IDs contain the complete 262,144-byte and 65,537-byte adversarial values; Windows cannot set that oversized test environment variable. The test inputs themselves must remain complete. The 20 failure identities and full errors are in the compressed original output and JUnit XML below. The opt-in PostgreSQL domain probes were skipped here and are **NOT_RUN** in this Windows run.

## Actual isolated Linux/PostgreSQL baseline

Dedicated container/network, no production database: PostgreSQL **17.11** (`postgres:17-alpine` image digest `sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73`), Python **3.12.16** (`python:3.12-slim` digest `sha256:12316debbfb86890f6b2d825820612741ba527ad5a44e3902759141d572bfe8f`). Eight separately named fresh `ipXX_test_20261027` databases were created on a dedicated cluster. No application account or existing database was used.

Migration `migrations/0001_business_ledger.sql`, SHA-256 `b3bfbaaec9dbeeb207297a395290dd4083d5b6101c98a11e240d36a9b32b5dc6`, and `deploy/db/roles.sql` were applied by the existing individual module fixtures. The following files actually ran inside the isolated Linux container with the corresponding `IPXX_ISOLATED_DATABASE=YES` and per-module `IPXX_DATABASE_DSN` values:

```sh
python -m pip install -e '.[test]'
python -m pytest -q --tb=short --junitxml=/evidence/linux-pg-baseline.xml \
  tests/integration/test_ledger_transactions.py tests/integration/test_reservations.py \
  tests/integration/test_persistent_risk_locks.py tests/integration/test_durable_dispatch.py \
  tests/integration/test_execution_evidence.py tests/integration/test_recovery_gate.py \
  tests/integration/test_protection_lifecycle.py tests/integration/test_ownership_transfer.py
# exit 1: 427 passed, 27 failed, 2 skipped
```

The additional failures expose stale reservation state fixtures, a database-rejected risk-lock resolution path, and an owner-change test attempting a protected direct mutation. Each requires diagnosis; none may be made green by disabling immutable triggers or skipping opted-in database checks. The two skipped cases and all unexecuted integration combinations remain **NOT_RUN**. These isolated-domain tests are not a full end-to-end console execution-chain acceptance.

## Original output integrity

The four `.xz` files alongside this report decompress to the exact unmodified test output and JUnit XML; they were checked against the synthetic PostgreSQL password, current local database password, and any present model API keys before inclusion. SHA-256 below is of the **decompressed** original:

| File | SHA-256 |
|---|---|
| `windows-baseline.log.xz` | `965492a06c74ff17482fa242e8030cf20ec3eeebc7089a81beae8c3fb42667fb` |
| `windows-baseline.xml.xz` | `5c60a5b4f68ea4215da9e7cd96c1c0dd8b9e25753d2a24eae7a88d4cb0c0e129` |
| `linux-pg-baseline.log.xz` | `0d34a1e417c6b2a6615e2f6f1fea02835d874488b187ef8424530404fce5c863` |
| `linux-pg-baseline.xml.xz` | `2f0939719de34e8465c1a6de9b8dc152c70b90f53e525328194ff3a0245da1d3` |

For example: `python -c "import lzma,sys; sys.stdout.buffer.write(lzma.open(sys.argv[1],'rb').read())" linux-pg-baseline.log.xz`.
