# Decision evidence refactor — 2026-10-28

Implementation baseline: cfe6b0f. Verification source: d6b07c1. PAPER only.
The user's running test remains PAUSED. No real account, paid inference, Testnet,
LIVE, operational database migration, or automatic runtime restart was performed.

## Implemented flow

Manual and explicitly upgraded automatic strategies use the same AnalysisFeed /
build_analysis_context path. Public candle IO completes before account reads.
Closed candles, source generation, time windows, required and optional sections,
deterministic return/SMA/ATR/relative volume/high-low evidence, decimal values,
strict nested allowlists and byte budgets produce a hash-bound snapshot.

Single: readiness -> one primary -> strict assessment/approved DecisionProposal
codec -> at most one semantic correction -> DECIDED or explicit non-trading error.
HOLD with no executable plan retains no fabricated entry/stop. Its reason is
NO_EDGE, WAIT_CONDITION, or MAINTAIN_POSITION. Failed inference is MODEL_ERROR.

Cross: explicitly selected unanimous keeps agreement on executable plan terms;
evidence_review shares one frozen snapshot, primary -> required reviewers ->
server-verifiable predicates -> optional single adjudication. Verified defects
block; unverified critical facts and failed reviewers require review. Soft
preferences can retain/reduce the candidate or observe, never force HOLD by vote.
Repair and adjudication share one extra call slot. Single has at most 2 calls;
cross has at most participant_count+1. Exact sanitized request bodies, versions,
results, usage and hashes are persisted in the existing JSON run store.

Execution: fresh strategy/model/lease/snapshot binding, quote drift and expiry ->
existing whole-account valuation/daily-loss/position checks -> target delta ->
existing ownership, intent, capital reservation, durable command and synthetic
venue chain. A target is not an instruction to fill the strategy ceiling. Unknown
orders retain risk; already-achieved targets create no order. Protected reductions
use the original cancel-confirm-review-continue flow and retain the proposal's exit
terms. Independent protection/recovery continues through model failure.

Schema and policy changes require explicit strategy revision and active-strategy
cost acknowledgement. Existing five-field receipts remain verbatim and readable;
legacy model advice cannot automatically execute. The UI exposes explicit format,
research interval and cross-policy selection, assessment reasons, exact target,
exit times, watch conditions and review/participant details.

## Acceptance map

All fixtures are synthetic and provider calls mocked. Program protocol tests do
not demonstrate predictive accuracy or economic benefit.

| ID | Executable evidence |
|---|---|
| D01 | test_d01_required_history_blocks; test_d01_http_missing_candles_does_not_call_provider; test_required_readiness_no_paid_call |
| D02 | test_d02_payload_uses_assessment_schema_and_preserves_snapshot; test_d02_d03_http_uses_closed_snapshot_and_replay_artifact |
| D03 | Shared builder HTTP tests plus migrated test_real_model_transport_and_notification_api and real automatic target tests |
| D04 | test_d04_partial_never_enters_features; parser / websocket closure metadata tests |
| D05 | test_d05_mixed_generation_blocks; product mismatch and existing late REST/source replacement tests |
| D06 | test_d06_hourly_freshness_independent_of_book; test_d06_stale_book_cannot_borrow_hourly_expiry |
| D07 | test_d07_nested_untrusted_data_rejected; oversized/forged quality tests; existing provider URL/secret guards |
| D08 | test_d08_optional_news_does_not_block; test_optional_missing_news_is_a_soft_review_preference |
| D09 | test_d09_error_not_hold; test_d09_d20_malformed_output_accounted_without_retry |
| D10 | test_d10_single_and_replay; test_d10_d11_real_unified_proposal_creates_only_target_and_preserves_exit |
| D11 | repeated target and exact target tests; migrated fee-bound/no-accumulation PG regression |
| D12 | test_d12_real_partial_target_preserves_protected_cancel_and_original_plan; unknown cancel tests |
| D13 | test_d13_soft_review_bounded_adjudication; test_d13_soft_review_cannot_increase_target |
| D14 | semantic errors / verified and unknown review predicate tests |
| D15 | test_d15_required_review_failure |
| D16 | test_d16_evidence_review_guard_does_not_revote_soft_disagreement; migrated real consensus PG tests |
| D17 | test_d17_legacy_output_cannot_be_upgraded; verbatim historical receipt and explicit schema/config tests |
| D18 | test_d18_watch_only_on_crossing_or_new_closed_bar; scheduler implements per-interval closed-bar and price crossings |
| D19 | expiry/config-change tests, real stale-output PG cases, existing mandatory ownership-epoch suite |
| D20 | test_d20_invalid_repair_once; test_d20_repair_and_adjudication_share_extra_call_budget |
| D21 | test_d21_owner_protection_continues_without_model_budget; mandatory owner-maintenance/recovery PG suites |
| D22 | test_d22_hold_without_invented_wire_plan; test_d22_single_observation |

## Execution limitations and remaining work

- UPDATE_EXIT is rejected as EXIT_UPDATE_UNSUPPORTED by the current console
  adapter. Snapshot previous_proposal is unavailable; validators do not fabricate
  an existing plan. New SET_TARGET proposals do carry and persist exit plans.
- The qualified adapter supports Spot MARKET, partial fills allowed, LAST stop,
  maximum 20 bps requested slippage. Unsupported LIMIT/FOK/other triggers fail
  explicitly. No native adapter qualification is claimed.
- Protected reduction intentionally pauses at unknown cancellation and requires
  existing independent recovery review plus explicit continue. It never invents
  cancellation, protection verification or fills.
- News and trades are not newly sourced; required news blocks, optional news is
  disclosed. Previous assessment narrative is not fed back as verified fact.
- Model budgeting is bounded calls/time/output tokens with actual usage when
  returned; monetary provider pricing/daily spend accounting was not introduced.
- D18 validates deterministic triggers; a long-running live scheduler experiment
  and real paid-model quality experiments were not run. The operational pause is
  retained. Full UPDATE_EXIT and the above unsupported adapters remain unfinished.
- Review found and fixed shared-feed masking, interval-edit permission expansion,
  audit serialization, whole-account intake risk, and own-cancel misclassification.
  Review agents became unavailable; final integration was reviewed and tested by
  the primary agent, not independently re-approved by another reviewer.

## Verification

Final fixed-commit Linux/PostgreSQL gate: **808 passed, 0 failures/errors/skips,
20 suites, exit 0**, backend d6b07c1. Ordinary Python: **2432 passed, 362 skipped,
96 deselected, exit 0**. The ordinary job does not configure per-suite PG opt-ins;
those cases are exercised by the separate mandatory PG gate. Remaining skipped
qualifications include nonroot/native-rootfs/container probes and registered
50/5/10 workload observations, not claimed as passed. Historical red runs are
retained under the evidence directory. No unresolved failing final tests.
Final frontend 8422f3f (backend identical to d6b07c1). Local fast-forward
integration into public-snapshot is authorized; no push or runtime restart.

Commands actually executed (Windows host Python is the original checkout's .venv):

```powershell
python tools/qualify_paper_docker.py --commit d6b07c1 --python-suite --timeout 5400
python -m pytest tests/server -m "not paper_runtime" -q --tb=short
npm --prefix web test
npm --prefix web run typecheck
npm --prefix web run build
```

The targeted Windows server run returned 477 passed / 56 deselected; the final
Linux run above is the authoritative full result. See
[evidence](evidence/2026-10-28-decision-evidence/README.md) for raw logs/JUnit,
resolved dependencies, migration hashes, source revisions and prior failures.
Frontend: 111 tests pass; typecheck and production build exit 0. Isolated fixture
render checked at 390x844 and 1440x900, no horizontal overflow seen. No operational
API or model was used for visual checks.

Earlier runs were red and retained: 4c3911c had 19 control-plane failures; 396276a
had 32 (including wrong audit record type and unmigrated routing fixtures).
Diagnostics after fixes: 45 automation/proposal tests and 37 routing tests pass
against real isolated PostgreSQL. No tests were removed/skipped to clear failures.

## Changed files

- `docs/superpowers/plans/2026-10-28-decision-evidence.md`
- `src/trading_system/server/analysis_snapshot.py`
- `src/trading_system/server/app.py`
- `src/trading_system/server/automation.py`
- `src/trading_system/server/decision_assessment.py`
- `src/trading_system/server/decision_execution.py`
- `src/trading_system/server/market_live.py`
- `src/trading_system/server/market_parse.py`
- `src/trading_system/server/model_api.py`
- `src/trading_system/server/model_client.py`
- `src/trading_system/server/model_repository.py`
- `src/trading_system/server/model_service.py`
- `src/trading_system/server/paper_runtime.py`
- `src/trading_system/server/store.py`
- `src/trading_system/server/validation.py`
- `tests/server/test_analysis_snapshot.py`
- `tests/server/test_assessment_api.py`
- `tests/server/test_automation.py`
- `tests/server/test_decision_assessment.py`
- `tests/server/test_decision_configuration.py`
- `tests/server/test_decision_execution.py`
- `tests/server/test_multi_routing.py`
- `tests/support/decision_fixture.py`
- `tools/qualify_paper_docker.py`
- `web/src/runtime/assessment.tsx`
- `web/src/runtime/automation.tsx`
- `web/src/runtime/forms.tsx`
- `web/src/runtime/modelprofiles.tsx`
- `web/src/runtime/modelsettings.tsx`
- `web/src/runtime/types.ts`
- `web/tests/assessment.test.tsx`

Additional final UI contract file: `web/src/runtime/pages.tsx`.
