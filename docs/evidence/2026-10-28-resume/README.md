# Resume verification — 2026-10-28

The user explicitly resumed the task. Existing worktrees, six unfinished files
and prior evidence were preserved. No testnet, real account or paid model calls.

The most recent pre-pause three-mode/freshness run had 6 passed / 2 failed: both
HYBRID fixtures omitted an explicit model profile and failed authorization setup
with INVALID_MODEL_BINDING. The fixtures now explicitly select the already
configured `default` profile for AI/HYBRID, and no profiles for RULE. Production
model-binding checks and every negative assertion are unchanged.

Actual PostgreSQL reproduction command (Windows application, isolated PostgreSQL
17 container on local port 55433; newly provisioned business/console/venue DBs):

```powershell
.venv/Scripts/python.exe -X utf8 .runtime/run_marked_console.py tests/server/test_automation.py::test_three_modes_share_atomic_risk_owner_and_explicit_fill_path tests/server/test_automation.py::test_three_modes_cannot_override_single_position_limit tests/server/test_automation.py::test_old_model_output_cannot_create_new_domain_command
```

Result: exit 0, **8 passed**. The first attempt ran before database crash-recovery
fsync completed and returned 8 setup errors / exit 1. That environmental failure
is retained separately; it is not a production counterexample or a skipped pass.

Offline targeted intake/accounting/historical-owner checks: exit 0, 14 passed.
These results validate the fixture correction and selected existing regressions,
not the two P1 fixes or final combined acceptance. The complete marked server
suite, Linux failure gate, frontend and real CLI/browser demo remain pending on
the final integrated tree.

`manifest.json` records the base commit, source file hashes and raw artifact
hashes. Compressed logs preserve exact bytes. No connection strings or generated
secret configuration are included.
