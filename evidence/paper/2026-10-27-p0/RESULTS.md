# Baseline failure remediation — 2026-10-27

Tested fixed source: `59413b0` on `codex/unified-paper-execution`. The prior [baseline](../2026-10-27-baseline/BASELINE.md) remains the comparison and records original failures. These checks do not establish a unified PAPER chain, native adapter qualification or TESTNET readiness.

## Production defects

| Defect and affected path | Fix and regression |
|---|---|
| Four rule evaluations could not construct the nested wire proposal: `src/trading_system/rules/evaluator.py` looked up classes that live in `contracts.wire`. | Commit `83f3183` uses the actual wire types; the four original rule cases and existing schema checks pass. |
| `src/trading_system/server/model_client.py` passed SNI as bytes; real httpcore/AnyIO TLS requires a string. Existing mock transport did not exercise TLS. | Commit `59413b0` keeps the original pinned destination, Host and certificate checks, passes a string SNI, and adds real loopback TLS success and wrong-host certificate rejection tests. No paid provider was called. |

An additional incompatibility was identified in generated US-equity commands: the stock adapter rejects derivatives-only flags. Its production correction remains a separate task and is **NOT_RUN** in this report; a fixture fix alone must not be treated as proof of adapter integration.

## Test and fixture defects

Commits `7ceebb7` and `861d12e` correct the following without disabling safety checks:

| Baseline failures | Root cause and preserved check |
|---|---|
| 8 backup/restore tests | The synthetic fixture constructed `Money(value=...)`, while the contract requires `amount`; backup, corruption and identity assertions now reach production code. |
| 3 mixed-decision tests | Authorization evidence was later than the synthetic risk clock; evidence is bound to a valid earlier fixture time and all authorization checks still apply. |
| 1 protocol test | Python typing adds an unannotated implementation dunder to the protocol; inspect declared application methods while continuing to require typed write permits. |
| 1 manual-reduction test | The observation advances the control revision; the test now first proves stale revision is rejected, then checks reconciliation denial at the current revision, with no wire write. |
| 1 equity-protection fixture | The real-security fixture omitted explicit `tokenize=false`; the command hash is recomputed and unsupported equity behavior remains rejected. |
| 1 recovery test | The fixture selector was also named `kind`, colliding with the adversarial `kind` field under test; rename the selector and preserve the binding rejection. |
| 1 point-in-time replay test | A future snapshot reference is rejected earlier by authorization. Retain that `AUTHORIZATION_REQUIRED` counterexample and add a separately bound future quote yielding `FUTURE_EVIDENCE`; both assert no queue or reservation. |
| 4 Windows setup/teardown errors for two cases | Give the huge parameter values short explicit test IDs; the full 262,144-byte and 65,537-byte hostile inputs and original assertions remain. |

These groups account for the original 20 failures and four Windows error phases. Independent review found that an earlier fixture edit had dropped the future-reference case; `861d12e` restored it and the reviewer verified both cases.

## Executed checks

- Windows 11/Python 3.14.2: `AETHERTRADE_RUN_POSTGRES_TESTS=1 python -m pytest -q --tb=short --junitxml=.runtime/paper-after-p0.xml`; **exit 0: 2140 passed, 240 skipped**. The opt-in console PostgreSQL case used the dedicated `aethertrade_tests` database in an isolated cluster. The Linux-only domain database cases remain skipped in this run.
- Linux container/Python 3.12.16 using `git archive 59413b0`: `python -m pip install -e '.[test]'` then `python -m pytest -q --tb=short --junitxml=/evidence/linux-ci-p0.xml`; **exit 0: 2139 passed, 241 skipped**. This reproduces the public workflow's Python command locally without PostgreSQL opt-ins. It is not a new GitHub Actions run; remote CI for this commit is **NOT_RUN**.
- Node frontend: `npm --prefix web ci --ignore-scripts`, `npm --prefix web run typecheck`, `npm --prefix web test`; **exit 0: 98 tests passed**.
- Real isolated Linux/PostgreSQL domain baseline remains the separate **427 passed, 27 failed, 2 skipped** result in the baseline record. Later database fixes require a separate fresh database run and evidence; the green runs above do not supersede it.

Original outputs and JUnit records are retained alongside this report as losslessly compressed `.xz` files. SHA-256 of the decompressed bytes:

| File | SHA-256 |
|---|---|
| `windows-after-p0.log.xz` | `27a5d2521a2bc7a78e621ff5e933cfbd9629cfa7770f71ca1bd8c79c8fd8241f` |
| `windows-after-p0.xml.xz` | `635f6cd213ae3865aae025b0e0c203c906d32ea25fbdd446d306ba60e1a79790` |
| `linux-ci-p0.log.xz` | `6a93c4502e7bacccb0e01cc260cb4b758227bf84d7973383a3a58b4209a77bdc` |
| `linux-ci-p0.xml.xz` | `b15d5a4b100e40466c3afc226cfe0738b49c6bd85e29da72ed80c919deeffa1b` |
| `web-tests.log.xz` | `0a424bbaf0dd3f6b52d932f3a734ebca031e6de261e6cf0a107855c0d0f6c652` |
| `web-typecheck.log.xz` | `bc649c160ed3caffd6fa45ab065a6e7c702472d7665e9a1122a393e0fc821094` |

The original records were scanned for known application/database credentials and present model API keys before inclusion. No real venue, real account, or paid model call was used.
