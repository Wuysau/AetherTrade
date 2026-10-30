# Native asset facts and initial account vector

These are component results, not the complete 46/56 acceptance or actual
position/book posting qualification. See docs/current-task.md for the explicit
remaining legacy opening, BookCashBinding and owner transaction work.

| Cohort | Fixed source | Actual result |
| --- | --- | --- |
| asset-reader-red | be27f0a | 5 missing-module failures |
| asset-reader-green | 85ab011 | 5 passed, 36.93s |
| source-metadata-regression | 85ab011 | 15 passed, 80.21s |
| opening-red | e371905 | 7 missing-capability failures |
| opening-initial-green | 9cc980f | 7 passed, 3.69s |
| opening-malformed-red | 526146d | 7 passed, 2 DID NOT RAISE |
| opening-green | 28ebbab | 9 passed, 3.85s |
| cash-red | 5fc320c | 6 missing-module failures |
| cash-fixture-conflicts | 7ca3f09 | 2 passed, 4 precondition market-sequence failures |
| cash-green | fb2de60 | 6 passed, 27.44s |
| cash-distinct-snapshots-green | 2204549 | 6 passed, 26.85s; distinct source snapshots, one economic contribution |

Completed positive suites have zero failures/errors/skips. Source regression
retains the JUnit record_property compatibility warning. The sequence conflicts
were corrected by separating fixture streams; no matcher guard was relaxed.

Every run used a fixed source and fresh isolated Linux/PostgreSQL containers:

```powershell
& 'D:/Projects/Python/AetherTrade/AetherTrade/.venv/Scripts/python.exe' -X utf8 tools/qualify_paper_docker.py --commit <fixed-sha> --suite <program-asset-reader|program-fill-source|program-account-opening|program-account-cash> --timeout 1200 --runner-image sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7 --offline-dependencies
```

The 176 raw files include original stdout, JUnit, launch manifests, migration
hashes and dependency evidence. copy-map.json records each original path, byte
count and SHA256. Private container.env/credentials are excluded. Publication
also checks actual committed blobs; working-copy equality alone is insufficient.

Adversarial owner fill/audit values are negative security fixtures. Positive
economic facts use actual admission, protected one-use consumption, matching,
source approval and the existing FillLedger. Markers in cash tests qualify
reconstruction only: no claim of complete owned-position/cashbook execution,
new order permission, historical FLAT or reservation release follows.
