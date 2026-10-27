# PostgreSQL CI gate proof — 2026-10-27

Source under test: `44ec2dcc264e`, with `tools/ci_paper_postgres.py` and its mandatory PostgreSQL workflow job. This records the gate behavior, not a complete unified PAPER or venue qualification.

An isolated PostgreSQL 17.11 cluster was started separately from the application database. A fresh `paper_ci_admin` database and explicit isolation marker were supplied; the runner created a separate new database for each test module. The repository was cloned from a Git bundle into a Linux Python 3.12 container, so the recorded tests correspond to the fixed source commit. Credentials were synthetic and were not included in the saved artifacts.

Commands and actual results:

```text
python tools/ci_paper_postgres.py --initialize
# exit 0: disposable database marker verified
python tools/ci_paper_postgres.py --suite ledger --evidence-dir /evidence/ci-proof
# pytest exit 0: 30 passed, 0 skipped; gate exit 0
python tools/ci_paper_postgres.py --suite ownership --evidence-dir /evidence/ci-proof
# pytest exit 0: 84 passed, 2 skipped; gate exit 1
python tools/ci_paper_postgres.py --suite console --evidence-dir /evidence/ci-proof
# pytest exit 0: 1 passed, 0 skipped; gate exit 0
```

The two ownership cases explicitly state **NOT_RUN** because reviewed owner-CAS crash behavior is missing. The gate correctly rejects this result even though pytest itself returns zero. A diagnostic `--suite` run is marked `qualification: NOT_RUN` in its summary. The default gate requires all declared suites, an explicit isolated Linux/PG marker, fresh per-suite databases, a clean fixed commit and zero skipped cases. It does not delete databases or accept a production database path. Actual remote GitHub Actions for the new workflow are **NOT_RUN** until the user authorizes a remote update.

The original pytest logs, JUnit XML and runner summaries for all three runs are the adjacent losslessly compressed `.xz` files. Their decompressed SHA-256 values:

| Suite | Log | JUnit | Summary |
|---|---|---|---|
| ledger | `e2943dfdd874a824e46adffb460ee0e4db7aa6ffb485caad02be80669439f582` | `ae189ebc7d24023fd570219551b40d4472d1614f3da74e2fdd7d6367b2a60b82` | `70cfb0f0a1bee97d34acb098bf18874dfb3e0d994e40869905b49d3fd9c5a2ac` |
| ownership | `6639e7b3e745f90646d9e48850779f23519a0267af8a80e1e1666202a99b8c15` | `2945ca4f4b06e103b3f933bd495644aba3449a47b11e2bfa9b076408c38f2fde` | `0bd89f5b10507303050ec2938a97531c1c8f8fb4d9dcacb1034c79d799e2ec13` |
| console | `85a7532b40196fb821e4b3dfb1231181e8e280a2da477abec65674576186c6c1` | `c017ebde5193819231650bd57234081c2037bc85234b360f5250ac818751649b` | `841734f7c7682d1a31db1624d3f33c5c5b92e9c5065fc0ef69d444082a803571` |

Artifacts were checked for the isolated database passwords, the existing application database password, and present model keys before inclusion. This proof does not run or imply any external exchange, real account or paid model call.
