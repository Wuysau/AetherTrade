# Protected reduction actual PostgreSQL evidence

Code commit: `8bc1080` (dependency `9f0b1cb`, locally `a707baa`).
Execution: Windows Python application, real isolated Docker PostgreSQL on 55433;
not Linux process/fsync qualification or full final integration qualification.
Every regression uses production `paper_provision.provision()` to create three
fresh databases and restricted runtime/event/reviewer profiles. No application
database on 55432, exchange, paid model, reset or deletion was used.

Runner `run_reduction.py` reads existing isolated admin config internally and
never prints DSNs/passwords. Run from this sole-writer worktree with the existing
paper-execution venv Python. The red option temporarily restores only two owned
production files from the current HEAD and restores their bytes in `finally`;
the recorded red was executed while HEAD was dependency `a707baa`. On a later
HEAD use a separate dependency checkout for the red. Wrapper exit status is not
pytest status: its `.exit` artifact records the child pytest return code.

Recorded commands (all use `.runtime/run_reduction.py`, archived identically):

```
python .runtime/run_reduction.py red -k "explicit_cancel or existing_store"
python .runtime/run_reduction.py tests/integration/test_unified_paper_runtime.py
PAPER_REDUCTION_EVIDENCE_DIR=<worktree>/.runtime/protected-reduction-ledger
python .runtime/run_reduction.py
python -m pytest -q tests/unit/test_locked_actions.py
```

Red: **2 failed / 2 deselected**, pytest exit 1; both real direct intake and
Store SELL encounter `PAPER_RISK_REJECTED`, subcodes
`ACTIVE_ORDERS_REQUIRE_RECONCILIATION`, `WORKING_QUANTITY_MISMATCH`, with healthy
verified stop and initial protection PENDING explicitly resolved by reviewer.
Opening-only ordinary stop cancel rejection is also asserted by the happy-path
test. This is the behavior regression, separate from an earlier missing-new-API
red used during development.

Green: **4 passed**, exit 0 (35.21 seconds), after final invalid-command failclosed
guard. Combination: **15 passed**, exit 0 (72.17 seconds), four new plus eleven
existing unified runtime tests, before that small guard and evidence-only test
snapshot additions. Locked actions: **119 passed**, exit 0 on committed source.
The final integration tree, all acceptance suites, browser flow and Linux fault
qualification are **NOT_RUN** here. The UI/API continuation action is composed
by the root integration task; CLI continuation is implemented in this commit.

Five original JSON snapshots capture healthy stop, cancellation UNKNOWN, confirmed
and reviewed cancellation with retained GAP, explicitly filled bounded reduction,
and residual stop verified. They include original commands, orders, fills, locks,
plans and user projection, without credentials. No ACK is treated as a fill or
protection verification. Tests additionally cover over-reduction, racing fills,
idempotent Store/runtime requests, and unchanged existing EXIT_ONLY fault locks.
