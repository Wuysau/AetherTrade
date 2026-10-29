# Shared risk core — actual verification

Scope: deterministic unit/contract tests and genuine isolated PostgreSQL PAPER
role/transaction/ledger regressions. No account, native venue, model call or
TESTNET/LIVE qualification. No generated execution fill is used as venue proof.

| Run | Source | Actual result |
| --- | --- | --- |
| Small-hold arithmetic RED |9b28899 evaluator plus regression |1 failed: evaluator incorrectly ALLOWed reserve107 when actual available106 |
| Invalid metric RED |79eae93 terms plus regressions |45 failed,1 passed,19 deselected; malformed money/groups wrongly complete or raised |
| Final focused |c71a540 implementation |154 passed, exit0 |
| Final unit/intents/contracts |c71a540 |1087 passed, exit0 (terminal result17.81s) |
| Full mandatory PG implementation |79eae93 |904 passed,0 failures/errors/skips,26 suites, exit0 |
| Full mandatory PG final |c71a540 |904 passed,0 failures/errors/skips,26 suites, exit0 |

The first20 complete legacy decisions were captured before editing the evaluator
from unchanged9b28899. An additional invalid-leverage0 decision was independently
read from that exact pinned evaluator source, without regenerating the original
goldens. The one intentional changed diagnostic reservation is explained in
OP-C-RISK-CORE.md: margin6 rounds upward one ULP while verdict, codes and decision
ID stay unchanged. Other golden fields remain exact.

Commands executed:

```text
python -X utf8 -m pytest tests/unit/test_risk_terms.py tests/unit/test_common_risk_core.py tests/unit/test_risk_evaluator.py -q --tb=short
python -X utf8 -m pytest tests/unit tests/intents tests/contracts -q --tb=short
python -X utf8 tools/qualify_paper_docker.py --commit 79eae93 --timeout 3600
python -X utf8 tools/qualify_paper_docker.py --commit c71a540 --timeout 3600
```

The final PG rerun covers the final source after review, not an earlier source
claim. Each run created its own fresh private cluster/databases, fixed committed
source and sanitized environment. Existing ledger/reservation/dispatch/recovery/
ownership/protection, genuine role and process-fault tests ran with no safety
skips. No operational profile/database/dependency installation was changed.

`pg-implementation/` and `pg-final/` retain raw logs/JUnit, exact suite commands,
migration hashes and aggregate summaries, container image IDs, resolved Linux
dependencies and launcher manifests. Copied artifacts are byte-identical to
original run files and verified against original launcher SHA256 digests. The
per-run `artifact-map.json` binds original paths to short published paths and
content hashes; only the layout was shortened for Windows Git path limits.
Windows focused/broad interpreter was the existing project Python3.14; Linux
runner Python3.12 and PostgreSQL17.11 are recorded by the artifacts.

These results prove local mathematical and transactional regression, not native
order eligibility or full ORD acceptance. Generic financial semantic readers,
versioned persisted intent/risk/command wiring and native lifecycle qualification
remain unfinished. All46 capability targets are retained.
