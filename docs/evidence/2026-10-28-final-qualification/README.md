# Unified PAPER engineering: final qualification evidence

These artifacts qualify the local unified PAPER engineering workflow only. No
real/paid trading APIs were used by this collection. Venue-native behavior,
TESTNET, and LIVE qualification are **NOT_RUN**. Synthetic venue fault tests and
browser presentation checks do not establish real-venue execution readiness.

| Fixed source / scope | Recorded result |
| --- | --- |
| Backend `6cbc8707436f` (final backend gate) | 20 mandatory Linux/PostgreSQL groups: **802 passed, 0 failed/errors/skipped**; launcher exit **0** |
| Ordinary Python at `6cbc8707436f` | **2373 passed, 362 skipped, 90 deselected**, 1 warning; exit **0** |
| Frontend `c4520da` (RULE compatibility and final state display) | **108 passed** across 9 files; separate typecheck, tests and build raw `.exit` files are all **0**; build includes `tsc --noEmit && vite build` |
| Pre-reporting backend `c7ce6a420697` | 801 mandatory PostgreSQL passed; ordinary Python **2373 passed, 362 skipped, 90 deselected**; launcher exit **0**. Superseded for executed protective-order fee/history reporting. |
| Pre-final `7136ac5ab815` | 801 mandatory PostgreSQL passed; ordinary Python **2372 passed, 362 skipped, 90 deselected**; launcher exit **0**. Superseded for final CLI packaging behavior. |
| Earlier frontend / reviewer diagnostics | 107 frontend passed with all three `.exit` files 0; supplemental actual PostgreSQL diagnostics 4 + 5 passed with `.exit` files 0. Their short diagnostic logs do not independently record exact source/invocation. |

The final implementation source is `c4520da`; the backend gate remains fixed at
`6cbc870`. All294 selected backend/Python-test/tool/migration/configuration blobs
are independently identical. Final frontend typecheck/tests/build exit0 and108
passed are preserved by `final-c452-frontend-index.json` and its raw ZIP. The last
two display-only changes distinguish request acknowledgement from actual final
execution, and display no current position without a misleading protection gap.
`final-source-scope.json` maps exact backend/frontend scope and Git tree identities.
Historical3cb/6fe artifacts remain; commands were not all executed on one commit.

Actual browser use exposed a RULE detail crash when `counter_evidence` was
absent. The frontend now handles that optional list and labels rule-generated
advice independently from model output. `final-3cb-frontend-raw.zip` preserves
the regression RED (1 failed, 14 passed, 1 unhandled error, exit1), all six final
check logs/exit files (108 passed, all exits0), and the exact frontend source
patch. Earlier 6fe frontend 107-test/build artifacts remain historical evidence;
the 3cb checks supersede them; final c452 display checks supersede3cb. Actual browser screenshots remain in root's
separate D4 collection.

The actual stop-fill demonstration found that research fees/history omitted
executed protective orders even though physical cash was correct. Source
`6cbc870` includes those executed orders in fee and realized history while
keeping target attribution separate. `reporting-regression-red-green-raw.zip`
preserves the original RED (`0.1` fees versus expected `0.195`), an intermediate
public/private field assertion error, final GREEN, exact exit files and source
patch. Historical c7ce and 713 gate artifacts remain preserved with their source
scope; they do not qualify the later reporting change.

Each of the three backend ZIPs contains exact original bytes from the completed launcher's
`evidence/` directory: command argv and exit codes, logs, JUnit XML, dependency
install/resolution output, container image identifiers, PostgreSQL version,
migration and provisioning source hashes, and nine per-case fault ledger JSON
files. Process cases preserve `before`, `after_crash`, and `after_recovery`;
other cases preserve their actual named before/after phases. These ledgers
already contain the relevant trading facts; no extra database snapshot was
collected. The launch command was `python tools/qualify_paper_docker.py --commit <source>
--python-suite` for exact sources `7136ac5`, `c7ce6a4` and `6cbc870`; full child
argv are in each `launcher-manifest.json`.
The ordinary Python run explicitly ignores selected mandatory database modules
and deselects `paper_runtime`; its skips/deselections are retained. The mandatory
PostgreSQL gate separately ran its required suites without skips.

`*-index.json` records archive and every raw member SHA256. Each backend
`*-source-sha256.json` inventories every committed source blob and its SHA256.
Original launcher hashes were checked before compression; archives were reopened
and every member compared after compression. Recheck with:

```
python docs/evidence/2026-10-28-final-qualification/verify_artifacts.py
```

Generated `.env` secret values were compared privately against included bytes;
no matches were found and no values were printed. Environment/config files,
source bundles, authentication/session stores, cookies and encrypted keys were
excluded. Fixture-only strings appearing in test names/assertions are dummy test
data, not evidence of external credentials. Actual root CLI/API/browser demo
artifacts are maintained separately and are not mixed into trading proof here.

Scoped independent review provenance, including the final packaging/reporting followup and its actual 2-passed PostgreSQL probe, is recorded in [REVIEW_SCOPE.md](REVIEW_SCOPE.md).

