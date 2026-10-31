# Generic program assessment evidence

Only pure synthetic PAPER risk facts and isolated PostgreSQL regression are in
scope. No account, model, native venue, TESTNET/LIVE or generated-fill qualification.
Risk ALLOW is mathematical and always execution_authorized=False.

Actual commands:

```text
python -X utf8 -m pytest tests/intents/test_program_risk_assessment.py tests/intents/test_program_reduction_risk.py tests/unit/test_risk_evaluator.py tests/unit/test_risk_terms.py tests/unit/test_common_risk_core.py tests/unit/test_pending_exposure.py -q --tb=short
python -X utf8 -m pytest tests/unit tests/intents tests/contracts -q --tb=short
python -X utf8 tools/qualify_paper_docker.py --commit a055f2c --timeout 3600
python -X utf8 tools/qualify_paper_docker.py --commit d92dfda --timeout 3600
```

Final d92dfda results: **264 targeted passed**, **1273 unit/intents/contracts
passed**, and **905 mandatory PostgreSQL passed across27 suites**, all exits0.
The PG gate has zero failed/error/skipped safety cases. The selected PG console
suite ran56 cases and deselected497 cases outside that gate; the complete server
and browser/frontend suites were not independently rerun by this task.

Both initial a055f2c and final reviewed d92dfda PG runs passed905/27, each with its
own fresh private cluster/databases and committed source. Their raw logs/JUnit,
runtime-state snapshots, migration hashes, suite commands, dependency/image IDs
and launcher manifests are retained in pg-initial/ and pg-final/. Artifact-map
files bind original paths to short Windows-safe paths and exact SHA256; content
is byte-identical to the original runs. No operational profile/database/package
installation was changed. The initial pass is historical regression evidence,
not a substitute for the later semantic RED probes or final-source validation.

Independent source probes found the first generic implementation a055f2c could
report zero planned residual loss for partial exits and admit invalid/off-tick
or unsupported native Spot stop triggers. Six RED cases are retained.3aa2ad9
fixes them while preserving valid Spot LAST, futures unified MARK/native
MARK_PRICE and full-flat controls. A legacy PostgreSQL regression pass alone
was not proof that those new semantics were correct.

The bounded emergency extension uses a separate explicit metric subtype. It
retains unprotected/unknown nonzero residual quantity and EXIT_ONLY management
duties; no false normal completeness, placeholder loss0 or protectionTrue. Its
proof tests exercise exact current ownership/data/locks/emergency price/type/age,
reduce-only, report/duty bindings and unknown/recovery denials. No SQL lock,
reservation, command or permit is created by these tests or records.

Projection tests include same source strings across physical scopes/families/
namespaces, distinct BATCH fill bounds, full-flat/partial inventory valuation,
real pending/working quantities, receipt fee sources, native/local algorithms,
staged protection gaps and owned-quantity constraints. The same calculator and
policy core are exercised; no fake TradeIntent, order or caller ALLOW replaces
the actual calculation.

No full ORD venue case is promoted by these results; all46 goals and existing
OCO/quote/residual contract blockers remain. Authentic production fact/proof
readers, current LockedActionVerifier and the durable financial/permit bundle
remain required and are not registered here.
