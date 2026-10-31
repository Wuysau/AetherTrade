# OP-C isolated PostgreSQL program admission evidence

Actual runs of the fixed-commit launcher, using newly created task-only
postgres:17-alpine and python:3.12-slim containers with no published account port:

```text
python -X utf8 tools/qualify_paper_docker.py --commit ca3984c --suite order-admission --timeout 1800
python -X utf8 tools/qualify_paper_docker.py --commit 66ab9ed --suite order-admission --timeout 1800
python -X utf8 tools/qualify_paper_docker.py --commit 3fd6011 --suite order-admission --timeout 1800
```

The Windows controller used the original repository `.venv/Scripts/python.exe`.
Tests ran from bundled exact committed source in Linux, against fresh isolated
databases. See provenance.json for exact commits, launcher digests, timestamps
and actual suite counts. Any password-like output is redacted; no credentials or
runtime environment files are included.

- Initial role-policy RED:5 failed, zero skips. Owner was forbidden to insert
  proposals, as required by0006. The fix separated intake and owner commits;
  it did not widen role privileges.
- First two-phase run:9 passed/1 failed, zero skips. The remaining failure counted
  two appended versions of the same UNKNOWN command as two distinct commands.
  The corrected assertion checks both exact version count and one original ID.
- GREEN at3fd6011:10 passed, zero failures/errors/skips, launcher exit0.

Coverage includes exact original limit/stop/TIF, original replay, working and
UNKNOWN duplicate target denial, insufficient funds, full financial rollback,
bounded role writes, fresh owner rules after intake commit, owner commit failure,
owner-authored sidecar rebinding denial and source-only legacy dispatch denial.
Independent unit regressions additionally reproduced sidecar intent mutation
and both new/historical ancestry fallback failures before correction.

The proof/market/rule facts in these tests are explicitly synthetic fixtures.
These results prove local ledger orchestration, not deployed adapter eligibility,
market matching, full ORD end-to-end scenarios or Binance account behavior.
Full integrated regression is still required after subsequent source changes.
