# Physical release preparation repair — component evidence

Fixed source: `5ef20a43d00f2d3ffc24361ffa1082f1ec12c9b5`.
Original review base: `4c55778688d59a5053561b8c3a56ac10022722d7`.

`runs.json` identifies all four terminal original handles, source commits, JUnit
counts and exact times. `copy-map.json` binds 64 safe raw originals to published
copies by SHA256. Private container.env files remain outside this evidence.

- Personal RED f6c10c2:16 tests,12 pass/4 fail,0 errors/skips,28.188s. Genuine
  protected abort/reconciliation succeeds before scoped reconstruction rejects
  authenticated current zero-held release. All original11 cases remain green.
- Fixed6707:16 tests,14 pass/2 assertion failures,0 errors/skips,40.865s. Missing
  proof correctly raises MissingEvidence; two tests expected ValueError. Product
  stayed unchanged; exact denial/zero-write assertions corrected in5ef20a4.
- Fixed5ef20a4:16 pass,0 failures/errors/skips,40.697s. Genuine release traverses
  reserve/mark/consume/afterwait; absence of current proof blocks each boundary.
- Affected existing reservation suite6707:67 pass,0 failures/errors/skips,12.024s.
  Every product blob in that run is identical to final5ef20a4.

Separate local command at6707:

```powershell
& D:/Projects/Python/AetherTrade/AetherTrade/.venv/Scripts/python.exe -X utf8 -m pytest -q tests/unit/test_program_release_preparation.py tests/unit/test_program_scoped_target.py tests/unit/test_program_current_financial_inputs.py tests/unit/test_program_locked_action.py
```

Console output: `35 passed in 3.53s`. It includes stale transaction/book/evidence,
duplicate/foreign IDs, missing persisted reconciliation and missing protected
abort negatives. Only unrelated cash opening authentication is isolated in these
pure tests; real PG exercises actual opening/authentication/release/abort paths.
No unit log is represented as an original PG artifact.

An AST comparison confirms the relocated release verifier's executable body is
unchanged after normalizing the local reader and transaction scope; the sole new
statement is `tx.require_scope(book.scope)`. Physical identity validation runs
before shared preparation. Capacity uses the authenticated full financial holds.

No broad gate rerun, venue qualification, owned posting/host enablement, release
permission or full46/56 claim. Independent fix review and Cash A+B are pending.
