# Integrated PAPER baseline, before subsequent OP-C changes

Fixed source4b6b941, actual command:

```text
python -X utf8 tools/qualify_paper_docker.py --commit 4b6b941 --python-suite --timeout 5400
```

Fresh task-only Docker/PostgreSQL run, launcher exit0.24 mandatory PG suites:
873 passed, zero failures/errors/skips. Includes existing safety regressions,
new two-phase admission, closure and financial-credit upgrade checks. Ordinary
Python run:2650 passed,427 environment skips,96 deselected, one dependency
deprecation warning. Ordinary skips are not safety acceptance passes; mandatory
PG suites reject skips. No native/account/Testnet/LIVE qualification occurred.

This source precedes generic exposure, version containment, cross-version
obligations and shared TargetTerms integration. It is a baseline, not proof for
later commits. Those changes require further integrated verification. Raw
controller logs, exact source/migration/role/image digests, test commands and
per-suite counts are retained here. Full original JUnit/raw suite logs remain
in the task's ignored paper-qualification-4e4e76a7dad6 evidence directory.
