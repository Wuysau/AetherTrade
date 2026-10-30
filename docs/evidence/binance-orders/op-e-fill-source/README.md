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

copy-map.json binds 65 original runtime evidence files to their byte copies and
SHA256. Publication also compares the actual committed Git blobs, since a
working-tree comparison alone does not detect newline normalization.
