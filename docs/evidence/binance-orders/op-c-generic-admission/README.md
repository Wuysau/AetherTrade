# Public generic admission negative PostgreSQL diagnostic

Fixed source: b7e0dd5b0e052e86f1cfd55f9a0f06d25e345534.

Command: `.venv/Scripts/python.exe -X utf8 tools/qualify_paper_docker.py --commit HEAD --suite program-financial-admission --timeout 1800`

Actual fresh isolated PostgreSQL result:16 passed,0 failed,0 errors,0 skipped,
exit0. The launcher provisioned its own postgres:17-alpine and python:3.12-slim
containers and stopped only its own runner. Job41678 is TERMINAL. Full raw logs,
JUnit, source/migration/environment manifests and source-copy SHA256 map are in
`b7e/`; copied files were compared byte-for-byte by SHA256 against original files.

The12 existing private financial cases and4 public negative composition cases
verify missing/unhealthy clock, missing lease and injected lease-reader failure
leave no partial financial intent/reservations/bundle/admission receipt, while
the restricted intake remains durable. These are actual SQL transactions. The
ordinary unit transaction probes separately verify return-after-commit and final
attachment-boundary guards; they do not replace PostgreSQL qualification.

Positive public request attachment/outbox/dispatch and internal activation remain
NOT_RUN pending real peer implementation integration. No venue/native matcher,
Testnet, LIVE, account access or paid model calls were exercised or qualified.
