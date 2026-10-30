# Protected PAPER fill source: actual evidence

These are source-specific PostgreSQL component results, not native execution,
balance posting, whole-branch qualification or the complete 46/56 acceptance.

| Cohort | Fixed source | Actual result |
| --- | --- | --- |
| source-red | 71ecb3f | 0 passed, 8 failed, 0 errors/skips; missing migration/service |
| source-green | 6e75d05 | 8 passed, 0 failures/errors/skips; 26.51 seconds |
| committed-reader-red | 6a2eef4 | 9 passed, 2 failed, 0 errors/skips; missing committed reader |
| committed-reader-green | 56ddb04 | 11 passed, 0 failures/errors/skips; 41.25 seconds |
| interrupted-before-tests | 6e75d05 | NOT_RUN: only bundle.txt; environment interrupted before containers/tests |
| resource-red | 42067ee | 11 passed, 1 failed, 0 errors/skips; 9 source-linked fills repeated the full proof |
| snapshot-green | 08c05ae | 13 passed, 0 failures/errors/skips; 52.19 seconds |
| accounting-fixture-errors | 362320c | 5 errors; imported fixture dependency missing; no test body executed |
| accounting-red | c9ca04c | 5 failed, 0 errors/skips; accounting service missing after fixture correction |
| accounting-green | 08cc9b1 | 5 passed, 0 failures/errors/skips; 35.62 seconds |

The resource regression uses nine genuine matched partial fills, a 101706-byte
verified source and an actual verifier-role database connection measuring SQL
parameters. The old implementation sent 964772 bytes and failed the linear
growth bound. The new writer stores one protected source snapshot with compact
per-fill references. A batch reader loads that shared proof once, refuses use
after transaction closure and still reads original V1 approvals unchanged.

The accounting cohort additionally proves actual same-transaction evidence,
native fills, bridge/notices and unresolved recovery duties; replay with later
archive receipt, further partial fills, unchanged funds/reservations, atomic
rollback and raw economic discrepancies. It deliberately reports
RAW_RECORDED_ECONOMICS_BLOCKED. No cash/position financial posting is implemented
by that result. It is not the requested complete capability qualification.

The interrupted attempt had no qualifier process and no matching container after
Docker recovery. It is not a failing product test or a qualification pass. The
subsequent fixed-source run used fresh containers and retained separate evidence.

All completed commands used:

```powershell
& 'D:/Projects/Python/AetherTrade/AetherTrade/.venv/Scripts/python.exe' -X utf8 tools/qualify_paper_docker.py --commit <full-fixed-sha> --suite program-fill-source --timeout 1200 --runner-image sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7 --offline-dependencies
```

Each completed run records the exact source, PostgreSQL version, migration hashes,
runner image, dependency resolution, raw pytest log, JUnit and mandatory-gate
summary. Missing results or any skip fails the gate. The private container.env
and test database credentials are not published.

The actual path is financial admission → committed MAY/one-use consumption →
restricted durable matcher → actual report verifier/archive → separate source
role transaction → subsequent owner read of committed approval. Tests prove
source-role separation, no implicit role membership, owner write denial,
immutability, exact native/unit/source commitments, replay/additional fills,
original-ID-only requests, historical facts after execution TTL, economic
discrepancies, unknown/not-observed commands, cross-scope denial, uncommitted
approval rejection and rollback on source commit failure. No fill, position,
cash credit or reservation release is produced by the source service.

Related local tests at 56ddb04: 35 passed in 0.38 seconds:

```powershell
& 'D:/Projects/Python/AetherTrade/AetherTrade/.venv/Scripts/python.exe' -X utf8 -m pytest -q tests/unit/test_evidence_preparation.py tests/unit/test_paper_program_reports.py tests/unit/test_paper_program_liquidity.py
```

An earlier local command named a nonexistent test_paper_program_economics.py and
ran zero tests; it was corrected using the actual test file inventory. It is
not counted as a pass.

Expanded local regression at08cc9b1 added locked-action and original-ID recovery
modules:167 passed in9.80seconds, with no skips/failures.

copy-map.json binds 145 original runtime evidence files to their byte copies and
SHA256. Publication also compares the actual committed Git blobs, since a
working-tree comparison alone does not detect newline normalization.
