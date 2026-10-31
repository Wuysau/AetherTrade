# ROOT quote integration evidence

Exact source46bf284dbb2145287068d7f92761511ada3202c3 selects reviewed03646b57
producer/source/test files while retaining ROOT5ef guards. `runs.json` records
all four terminal0 original handles and actual JUnit counts/times. `copy-map.json`
binds64safe raw originals/copies. Private container.env files are not published.

Each command used the shared Python executable from ROOT:

```powershell
python -X utf8 tools/qualify_paper_docker.py --commit 46bf284dbb2145287068d7f92761511ada3202c3 --suite SUITE --timeout 1200 --runner-image sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7 --offline-dependencies
```

Suites: paper-quote-budget30, quote-review-mixed3, program-scoped-financial16,
paper-program-v2-reports51; all0fail/error/skip. No job was restarted. The shared
source/guard tests run against this merged revision, not inferred from peer counts.

Separate local focused command:

```powershell
python -X utf8 -m pytest -q tests/unit/test_program_release_preparation.py tests/unit/test_program_scoped_target.py tests/unit/test_paper_program_liquidity.py tests/intents/test_quote_risk_assessment.py
```

Output62passed3.03s. The earlier two invalid test-path invocations ran no tests;
neither is a verification claim. No unit output is represented as original PG raw.

Peer genuine unsupported/source-identity/mixed RED evidence and independent fix
review remain in the quote worktree/review package with their original source
identities. This directory records only actual ROOT post-merge qualification.
It confers no quote source registration, owned financial posting, unsupported fee,
native0026 combination, host/default qualification or full46/56 acceptance.
