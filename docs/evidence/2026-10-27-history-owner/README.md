# Complete terminal history after an owner handoff

Base: `ddbd9ab3a91954f78c00572a81649868c3316751`.
The accompanying manifest records the exact tested source SHA-256 values.

Root cause: target reconciliation compared every historical terminal command's
immutable intent owner/epoch with the current active owner. After a legitimate
handoff, this rejected all later target reconciliation, including reductions.

The fix keeps every historical root, its original order ownership, all archived
fills, and the full earliest-baseline-plus-fills equation. Only terminal history
uses the scope/group-bound original intent. Current positions still require the
current active owner, and READY obligations still require current intent ownership.
There is no history filtering, checkpoint, new authority, or reservation/dispatch
authorization change.

The deterministic test exercises production `target_obligations`,
`validate_report`, and `TargetCompiler`. It supplies an immutable old-owner
terminal command/fill and a current-owner position, checks a legal reduction,
then rejects forged historical order owner/epoch, altered fills/current quantity,
wrong current position ownership, cross-scope historical intents, and old-owner
READY commands. SQL reads and durable root enumeration are test doubles.
It does **not** execute PostgreSQL CAS, persistence, contention or a platform.

Commands used from this worktree (PowerShell, existing shared Python virtualenv):

```powershell
& ../paper-execution/.venv/Scripts/python.exe -m pytest -q tests/unit/test_target_history_owner.py --tb=short --junitxml=.runtime/history-owner-review/red.xml
# Exit 1: 7 failed, 1 passed, before the production fix.

& ../paper-execution/.venv/Scripts/python.exe -m pytest -q tests/unit/test_target_history_owner.py tests/unit/test_target_compiler.py tests/unit/test_locked_actions.py --tb=short --junitxml=.runtime/history-owner-review/green.xml
# Exit 0: 197 passed, after the production fix.

git diff --check
# Exit 0.
```

The original redirected stdout/stderr is retained in `red.log` and `green.log`.
Actual PostgreSQL CAS plus target/reservation integration checks on the combined
commit remain **NOT_RUN by this isolated change**. Existing separate CAS evidence
does not turn this deterministic handoff fixture into database proof. Unified
runtime acceptance, Testnet and LIVE are outside this change.
