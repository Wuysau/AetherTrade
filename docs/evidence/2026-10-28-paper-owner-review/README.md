# 2026-10-28 PAPER owner/review verification

Environment: Windows application/Python; real PostgreSQL in the already provisioned
isolated cluster on localhost:55433. No application DB on 55432, drops, exchange
calls, model calls, native enablement, or Testnet writes. Fresh per-test PAPER
business/engine/console databases and roles are retained.

Source base: 9f0b1cb. Implementation: 88bf975. Stale-maintenance follow-up:
9db778f1c8edd672da08e8f72e3f569a40327141.

Commands executed from paper-runtime-resume using sibling paper-execution's
.venv/Scripts/python.exe. The attached runner reads existing cluster config
internally and never prints connection strings; no credentials are archived.

- Baseline red: runtime source temporarily restored verbatim from 9f0b1cb
  (new module/tests retained); `pytest -q --tb=short
  tests/integration/test_paper_owner_maintenance.py -k "not survives_storage"`.
  Exit 1, three failed: unchanged proof churn, no automatic protection duty,
  DAILY_LOSS missing cause archive. See initial-baseline-red.log.
- Initial implementation 88bf975: `pytest -q --tb=short
  tests/integration/test_paper_owner_maintenance.py
  tests/integration/test_unified_paper_runtime.py`. Exit 0, 17 passed.
- Requested stale-authority regression against 88bf975 with its new test:
  `pytest -q --tb=short tests/integration/test_paper_owner_maintenance.py -k outlives`.
  Exit 1: RISK_LOCK_OR_GROUP_COMMAND_UNRESOLVED. Genuine new source queries had
  not renewed evidence after the entry quote/grant expired. See stale-maintenance-red.log.

Tests include separate reviewer denial to the normal owner, explicit operator ID,
next configured day, nonflat refusal, all-scope flat review, durable console sync,
unchanged query/balance basis, subsequent-cycle lock re-arm, partial-fill repair
without fabricated verification, and loop retry with sanitized storage fault reports.
The stale test advances the injected application wall/monotonic clock five minutes;
PostgreSQL's OS clock is unchanged. It proves local PAPER maintenance grant and
query freshness behavior, not hardware clock qualification. API/control reader
is made unavailable; the worker continues querying and repairing actual quantity.

Limitations: conservative DAILY review only supports completely flat accounts on a
later configured risk day. Historical causes lacking archived risk-day metadata
remain locked. Operator supervision must restart a terminated worker; this suite
checks durable restart behavior already covered by existing runtime tests and loop
retry in process, not a Linux service manager/fencing qualification. Integration
with protected-reduction's pending-adjustment hook, full repository/CI gate, browser
and separate OS-process API shutdown demonstration are root integration work.

Final fixed-source verification at 9db778f1c8edd672da08e8f72e3f569a40327141:
`pytest -q --tb=short tests/integration/test_paper_owner_maintenance.py
 tests/integration/test_unified_paper_runtime.py`.
Exit 0, **18 passed in 61.36s**. Raw stdout: 9db778f-green.log.
Seven focused owner/review regressions plus eleven existing real-PG runtime tests;
this is not a full repository qualification claim. `git diff --check` also passed.
