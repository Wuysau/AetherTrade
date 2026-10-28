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

## Follow-up reviewer findings and corrected validation

- `b13eaae`: expired maintenance READY was retained forever with no projection.
  After complete original-ID order/fill absence on the synthetic source, the
  worker now calls the existing SQL-guarded `abort_unsent` under both the control
  fence and collateral mutex. It never retires MAY_HAVE_SENT/UNKNOWN. Aborted
  immutable READY snapshots no longer count as queued reducible exposure; a
  deterministic replacement identity incorporates retired IDs. The active holding
  duty and current actual quantity still bound the replacement grant.
- Expiry regression against original `f81347e` production source (new tests
  retained): `pytest -q --tb=short tests/integration/test_paper_owner_maintenance.py
  -k expired_ready_protection`: exit 1, one failed; original remains DURABLE_READY.
  Raw output: expired-maintenance-red.log.
- `b13eaae` fixed-source command adds `tests/unit/test_locked_actions.py` to the
  prior two suites: exit 0, **141 passed in 90.37s**. See b13eaae-green.log.
- Validation correction: the earlier stale-entry test rejected error_type but
  did not require a confirmed stop receipt, so UNKNOWN could satisfy that older
  assertion. That result alone did not prove accepted maintenance. The corrected
  test explicitly requires effective `VENUE_CONFIRMED`, unchanged old quote, fresh
  maintenance grant and exact actual quantity. It advances runtime and UoW wall/
  monotonic issuance clocks together. PostgreSQL's OS clock remains unchanged.
  These controlled clocks are held constant during each action; they do not prove
  physical clock/fencing/deadline qualification. The corrected test is included
  in the 141-pass fixed-source result.
- `0c9fcf4`: flat-after-trading daily review previously checked raw command
  snapshots, which remain MAY_HAVE_SENT after confirmed receipt append. A scoped
  read-only wrapper now uses `CommandRepository.get` for every symbol under the
  SAME collateral-locked resolver transaction. Only confirmed/rejected or
  authoritatively aborted commands qualify; all held reservations must be zero.
- Traded daily-review regression against `b13eaae`: `pytest -q --tb=short
  tests/integration/test_paper_owner_maintenance.py -k daily_review_after_actual`:
  exit 1, two failed / one passed. Both real flat cycles failed review; a foreign
  ETH READY command correctly remained blocked. See traded-daily-review-red.log.
  New fixed tests close a real synthetic 1-unit fill/verified stop/stop fill cycle,
  assert cash **9994.805** including both fees, and prove review can also proceed
  after a genuinely retired unsent stop has no remaining financial obligation.

Final fixed-source follow-up: `0c9fcf4`.
`pytest -q --tb=short tests/integration/test_paper_owner_maintenance.py
 tests/integration/test_unified_paper_runtime.py tests/unit/test_locked_actions.py`
returned exit 0, **144 passed in 118.42s**. Raw stdout: 0c9fcf4-green.log.
This includes fourteen focused maintenance/review cases, eleven existing unified
runtime PostgreSQL cases, and 119 locked-actions unit cases. Scope remains the
isolated local PAPER runtime; full integration, CI, process-supervision and browser
qualification are separate. `git diff --check` also passed.
