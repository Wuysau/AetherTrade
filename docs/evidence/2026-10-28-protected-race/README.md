# Obsolete protected-reduction race: actual PostgreSQL

Fix source `1da8753`; dependency-only cherry-picks `ccc0209`, `366e476` correspond
to root's already integrated `88bf975`, `9db778f`. Integrate only this followup.

Real fresh isolated three-database provisioning, restricted roles, Windows app
against isolated Docker PostgreSQL 55433. No real venue, implicit synthetic fill,
application database 55432, reset, deletion or paid model. Source runner reads
admin credentials internally without printing them. Full final integration and
Linux process qualification remain NOT_RUN here.

Scenario: actual 1 BTC with independently verified healthy stop and initial
protection PENDING reviewed/resolved; authorized reduction .5 creates cancel
UNKNOWN; original stop races .25 fill at95; explicit cancel confirms cumulative
.25. Separate original-ID review resolves UNKNOWN while protection duty remains.
Old reduction must not use its stale actual1 basis. Before the fix the operator
projection wrongly advertised READY_TO_CONTINUE; pending adjustment also stayed
true forever, preventing owner maintenance of residual .75 protection.

Commands, using the existing paper-execution venv Python from this worktree:

```
python .runtime/run_race.py red -k obsolete
python .runtime/run_race.py tests/integration/test_unified_paper_runtime.py tests/integration/test_paper_owner_maintenance.py
PAPER_REDUCTION_EVIDENCE_DIR=<worktree>/.runtime/protected-race-ledger
python .runtime/run_race.py -k obsolete
```

Archived runner is identical to the local runner. Red restores the three product
files from exact dependency `366e476` only for the child invocation, restores bytes
in finally, and requires sole ownership of the worktree. Raw red: 1 failed / 4
deselected, exit1, READY_TO_CONTINUE instead of required review. An earlier local
red (before the added phase assertion) also directly reached pending=True;
that initial raw was superseded by the exact-dependency red above.

Combined original runtime + owner maintenance + protected reduction: **23 passed**,
exit0,113.43s. This preceded small added display fields, acceptance of effective
authoritatively ABORTED_UNSENT as known, and evidence snapshot additions. Final
targeted race on complete code **1 passed / 4 deselected**, exit0,14.27s. Wrapper
process exits independently; the `.exit` files contain actual pytest return codes.

The final test proves unresolved UNKNOWN still suppresses maintenance; separate
review and complete current facts permit immutable adjustment invalidation; owner
maintenance submits exactly .75 protection with no CLOSE or new fill; next pass
does not duplicate it; restart retains invalidation; old continuation permanently
rejects; only an explicit synthetic verification event makes protection verified.
All protection locks persist until independent review. Snapshots include typed
adjustment/invalidation audits, original command/order/fill/lock/plan records and
user projection. Cancellation-to-replacement remains a visible coverage gap.
