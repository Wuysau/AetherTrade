# Shared financial preparation and clock: actual isolated evidence

All runs used the fixed-commit Docker launcher, fresh isolated task-only
PostgreSQL/Linux containers and the existing main `.venv/Scripts/python.exe`
controller. No venue/account/model call or native qualification occurred.

| Source | Actual command suffix | PostgreSQL | Ordinary Python | Exit |
| --- | --- | --- | --- | --- |
|58327f6|`--commit 58327f6 --python-suite --timeout 5400`|905 passed,27 suites,0 failed/errors/skips|2842 passed,459 env skips,96 deselected|0|
|bcccc75|`--commit bcccc75 --suite capacity-precision --timeout 1800`|1 failed,0 skipped|not selected|1|
|08718fc|`--commit 08718fc --python-suite --timeout 5400`|905 passed,27 suites,0 failed/errors/skips|2942 passed,459 env skips,96 deselected|0|

Command prefix: `python -X utf8 tools/qualify_paper_docker.py`.
Each ordinary run reported the same Starlette/httpx deprecation warning;
ordinary environment skips are not acceptance passes. Mandatory PG suites
reject any skip. Source08718fc includes shared collateral preparation and
nonrenewable wall/monotonic admission window, plus the latest risk/core changes.

The integrated RED was a strict rejection-code mismatch. Both financial guards
now calculate exactly: the risk evaluator rejects the70-USDT request with only69
available BEFORE the capacity stage. The test now also directly verifies the
capacity stage against the actual committed cross-account holds, then asserts
the exact earlier risk rejection and zero intent/reservation/command bundle.
Neither assertion was removed or broadened, and no evaluator stub was added.

Artifacts retain exact original bytes. Each run's artifact-map.json maps original
relative paths to Windows-safe published paths and SHA256. The final source also
retains all synthetic final-state JSON artifacts. Manifests record exact source,
migration/role hashes, images, dependencies, per-suite commands and counts.

Scope remains ledger/financial/owner/recovery/runtime safety in PAPER, not full
ORD venue matching or all46 native capabilities. Generic financial records,
program semantic risk and lifecycle-v3 work following this source require their
own integration checks. Full original work directories remain in `.runtime`.
