# Unified PAPER engineering: final qualification evidence

These artifacts qualify the local unified PAPER engineering workflow only. No
real/paid trading APIs were used by this collection. Venue-native behavior,
TESTNET, and LIVE qualification are **NOT_RUN**. Synthetic venue fault tests and
browser presentation checks do not establish real-venue execution readiness.

| Fixed source / scope | Recorded result |
| --- | --- |
| Backend `c7ce6a420697` (final backend gate) | 20 mandatory Linux/PostgreSQL groups: **801 passed, 0 failed/errors/skipped**; launcher exit **0** |
| Ordinary Python at `c7ce6a420697` | **2373 passed, 362 skipped, 90 deselected**, 1 warning; exit **0** |
| Frontend `6fe6869281f1` (final navigation accessibility change) | **107 passed** across 9 files; build includes `tsc --noEmit && vite build`; root recorded tool session `39194` exit **0**; separate raw exit files **NOT_RECORDED** |
| Pre-final `7136ac5ab815` | 801 mandatory PostgreSQL passed; ordinary Python **2372 passed, 362 skipped, 90 deselected**; launcher exit **0**. Superseded for final CLI packaging behavior. |
| Earlier frontend / reviewer diagnostics | 107 frontend passed with all three `.exit` files 0; supplemental actual PostgreSQL diagnostics 4 + 5 passed with `.exit` files 0. Their short diagnostic logs do not independently record exact source/invocation. |

The final source is `6fe6869281f16e11a55c7f5813f79397eb0a6983`. Independent
`git diff --name-only c7ce6a4 6fe6869` verified the sole changed file is
`web/src/runtime/App.tsx`; its only change adds `aria-label={label}` to navigation
buttons whose text is hidden at medium widths. Every other Git blob, including
backend, migrations, tests and tools, is identical. The patch is preserved in
`final-6fe-frontend-raw.zip`. This is explicitly separate backend and frontend
source verification; the commands were not all executed on one commit.

Each backend ZIP contains exact original bytes from the completed launcher's
`evidence/` directory: command argv and exit codes, logs, JUnit XML, dependency
install/resolution output, container image identifiers, PostgreSQL version,
migration and provisioning source hashes, and nine per-case fault ledger JSON
files. Process cases preserve `before`, `after_crash`, and `after_recovery`;
other cases preserve their actual named before/after phases. These ledgers
already contain the relevant trading facts; no extra database snapshot was
collected. The original launch commands were `python tools/qualify_paper_docker.py
--commit 7136ac5 --python-suite` and `python tools/qualify_paper_docker.py --commit
c7ce6a4 --python-suite`; full child argv are in each `launcher-manifest.json`.
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

Scoped independent review provenance and the explicitly pending packaging review are recorded in [REVIEW_SCOPE.md](REVIEW_SCOPE.md).

