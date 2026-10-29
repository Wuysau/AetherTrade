# Original creation identity recovery

Source2e30dfc adds `recovery/original_creation.py` on the existing ledger.
`read_original_creation(tx, command_id)` reads the exact scoped first V3 write,
first request binding/financial bundle, continuous immutable state history,
protected MAY record and registered original client identities. It validates
complete original canonical hashes, account/product/service/intent/owner bindings
without rerunning a newer compiler or RiskEvaluator. No record is upgraded.

The returned metadata grants no send authority. `original_query_targets` restores
previously assigned mother/member IDs, including the absence of a batch mother;
it never calls the ID generator. `original_creation_case` returns the existing
ReconciliationCase shape for CaseScheduler, with an explicit programme identity
and query budget. It does not persist a case, change command state, resolve a
cause lock, establish ownership or release money. READY cannot become an UNKNOWN
case merely because this helper is called. Current send gates remain unchanged.

The independent integration review found no blocking issue in this explicit
read-only V3 scope. Native algorithm query targets retain the saved parent ID;
their node_ids tuple is empty when the original assigned_ids had no members.
This does not invent child IDs or establish later parent/child attribution.

Submission expiry does not erase a query identity. Current CapitalReservation
creation rows are immutable; actual later financial versions live in reservation
projection audit records. The first PG fixture mistakenly tried to update a
creation row and correctly failed its existing guard. That result is retained.
The corrected test uses ReservationRepository.save with an unchanged economic
hold and a new source watermark; no financial credit or guard bypass is used.
This does not establish that the former creation-row reader had a version bug.

Actual test evidence:

- Sourcefc6f32f:0 passed/4 failed/0 errors/skips. Two missing-module failures and
  two correctly rejected fixture transitions; not treated as the intended RED.
- Corrected source3a1509b:0 passed/4 missing-module failures/0 errors/skips in real
  isolated PostgreSQL before implementation.
- Source2e30dfc:4 passed/0 failures/errors/skips,16.99 seconds, PostgreSQL17.11 in
  new isolated Linux containers. READY read, real protected MAY/UNKNOWN after a
  later projection, and cross-scope/missing-ID isolation all ran.
- Unit/source-hash/history cases9 RED before implementation; combined225
  management/query/recovery/native-bridge regressions passed11.36 seconds. A local
  test variable shadowing its fixture helper was corrected without changing the
  assertions; its initial8-pass/1-error output remains archived.

Reproduction:

```powershell
python -X utf8 -m pytest tests/unit/test_original_creation_recovery.py -q
python -X utf8 tools/qualify_paper_docker.py --commit 2e30dfc --suite original-creation-recovery --timeout 900
```

The fixed-source PG gate uses this worktree's base8555201 command kernel; it
does not replace a final regression against newer integrated command/UNKNOWN
guards. The new mandatory suite is registered in ci_paper_postgres.py. Raw logs,
JUnit, exact migration/image/dependency manifests and SHA256 copies are under
`docs/evidence/binance-orders/op-e-recovery/`. No migration or historical rewrite
is introduced. Authenticated transport, persisted recovery workflow, native and
full ORD qualification remain unestablished by these read tests. All46/56 targets
remain in the parent goal and TESTNET/LIVE stays disabled.
