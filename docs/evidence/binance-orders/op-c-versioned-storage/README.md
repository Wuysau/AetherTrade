# Versioned repository evidence

Scope: local contract/unit checks and genuine isolated PostgreSQL PAPER storage
and lifecycle containment. No network venue, native adapter, model, real account
or TESTNET/LIVE test was run. No simulated execution fill was generated.

| Run | Source | Actual result |
| --- | --- | --- |
| V3 parent safety RED |3fd6011 plus regression only |1 failed: old V2 lifecycle accepted the V3 fixture, contrary to expected containment |
| Focused contracts/repository | implementation474655f |54 passed, exit0 |
| Broad contracts/intents/unit |474655f implementation |890 passed, exit0 (terminal result;17.21s) |
| First real PG run |474655f |31 passed,1 fixture failure,0 skipped |
| Fresh real PG rerun |0e1e2c4 |32 passed,0 failed/errors/skips, exit0 |

The first PG failure was the historical unknown-version fixture passing a dict
to the domain-record encoder, which correctly rejects dicts.0e1e2c4 uses a typed
historical sentinel with its explicit unknown version; no production safety
constraint changed and the unknown-version rejection/history assertions remain.
The raw RED log/JUnit/summary are kept rather than replaced by GREEN evidence.

Commands executed from this worktree:

```text
python -X utf8 -m pytest tests/unit/test_versioned_order_program_repository.py tests/contracts/test_native_program_v3.py tests/contracts/test_order_program_v2.py tests/contracts/test_order_lifecycle.py -q --tb=short
python -X utf8 -m pytest tests/unit tests/contracts tests/intents -q --tb=short
python -X utf8 tools/qualify_paper_docker.py --commit 474655f --suite order-programs --timeout 1800
python -X utf8 tools/qualify_paper_docker.py --commit 0e1e2c4 --suite order-programs --timeout 1800
```

PG runs used independent fresh clusters/databases, Linux Python3.12 and
PostgreSQL17.11, fixed committed source and the existing restricted owner role.
The GREEN launcher manifest, container image IDs, resolved test dependencies,
migration hashes, exact command/exit results, JUnit and raw log are retained.
Windows focused/broad checks used the existing project Python3.14 interpreter.
No operational configuration, database or package installation was modified.

Twelve added PG cases cover exact VP/TWAP restoration after commit/restart,
version identity conflicts, full algorithm hash binding, explicit native
lifecycle denial, no permit/resend, unknown version retention and authenticated
versus unauthenticated imported V3 parent/child reports. Existing V2 migration,
funding, observation, duplicate, UNKNOWN and immutable-history tests still run.
Imported MAY_HAVE_SENT/binding records are explicitly synthetic historical
metadata fixtures; they are not evidence that any venue accepted an order.

The full46-feature/56-scenario project qualification is unchanged by these local
results. V3 native lifecycle execution remains blocked until a reviewed authority
contract includes quantity units, multipliers and complete group allocation.
