# Actual HTTP / CLI / PostgreSQL / browser PAPER demonstration

Executed on 2026-10-28, Asia/Shanghai. This is the production application and
command service against fresh isolated PostgreSQL databases, with explicit
`CONTROLLED_PAPER_SCENARIO` quotes and operator-injected synthetic venue events.
There were no Playwright network response mocks, venue writes or paid models.
The browser context was closed before this run to remove earlier UI fixtures.

Backend financial projection is qualified at `6cbc870`; final UI is qualified
separately (see manifest). The control API was initially started before the
stop-fee reporting correction and reopened with that correction on the **same**
profile/database. The independent owner process survived this API restart;
its execution/authority logic was unchanged by the reporting-only correction.

## Observed normal-owner sequence

| Observation | Actual outcome |
| --- | --- |
| RULE evaluation with deliberately excessive allocation | Rule rationale displayed; deterministic position limit rejected BUY; no model call |
| Browser's normal order dialog | HTTP 200 PENDING, quantity1, actual0, cash10000; receipt is not a fill |
| Separate `paper-run` process | Original identity submitted; synthetic venue SUBMITTED; no automatic fill |
| Explicit partial-fill controller event | Actual0.4, remainder0.6, cash9959.96, persistent protection duty |
| Protection acknowledgement | Protected quantity stays0/GAP until explicit queried-term verification |
| Explicit protection verification | VERIFIED_SYNTHETIC0.4, never real-native qualification |
| Cancel remainder times out | CANCEL_UNKNOWN; original full100.200USDT reservation retained |
| Original cancellation confirmed and separately reviewed | Remainder0; UNKNOWN resolved only with reviewer credentials and original-ID facts |
| Browser SELL0.2 on healthy protected position | Audited adjustment receipt; actual0.4 unchanged while protection cancellation is pending |
| Confirm stop cancellation and independently review UNKNOWN | Browser exposes bounded continuation; HTTP202 creates PENDING close with deterministic identity |
| Explicit close fill0.2 | Actual0.2, cash9979.94; independent owner creates exact0.2 replacement protection |
| Explicit replacement verification and stop fill0.2@95 | Actual0, protection plan FLAT_VERIFIED, cash/equity9998.921 |
| Explicit final reviewer recovery | No active risk locks; no price-rebound or ACK-only unlock |
| Final API after restart | Fees0.079, three executed orders, no positions; HTTP200 |

Desktop1440x1000 and phone390x844 were inspected. The phone snapshot has no
document horizontal overflow. Screens show rationale/rejection, partial actual
versus remainder, unverified ACK, verified synthetic protection and final flat
state. CLI and API use the same durable ledger and original command identities.

## Artifacts and honest failure retention

`raw.zip` contains exact CLI argv/stdout/stderr/exit records, immutable synthetic
ledger histories at named phases, final API values, API-restart process facts,
and screenshots. `manifest.json` has raw/archive/source hashes. No profiles,
resolver passwords, cookies, CSRF values, console authentication tables or model
secret files are included. The local public demo password is only for the
explicitly disposable virtual account created by the demo helper.

Nonzero CLI attempts are retained, including attempts while the exclusive owner
was busy; exact error codes were not printed by the sanitized CLI and are
NOT_RECORDED. Retries used the same identity/event, not a new trade identity.
An earlier **operator-only** profile (no running owner) demonstrated a rejected
late protected-cancel dispatch (`DispatchDenied`); the exact subreason was not
captured. Its separate snapshots and CLI records are diagnostic, not claimed
as the completed flat run. The normal run uses the independent owner described
in the operation recipe. Databases were not reset or deleted to manufacture a
successful result.

The real demonstration exposed two additional reporting/presentation defects:
executed stop fees were omitted from research history (physical cash was already
correct), and legacy RULE details without model counter-evidence crashed the
page. Both have preserved RED/then GREEN tests and new fixed-source qualification
or frontend checks; earlier screenshots/results are not relabeled as final.

This proves synthetic PAPER application behavior only. Real market liquidity,
slippage, stop execution, Binance Testnet/Demo, USD-M and real-equity connector
qualification, remote GitHub Actions and LIVE readiness are NOT_RUN here.

The temporary demo control APIs and independent virtual owner were explicitly
stopped after capture. `helper-stop.json` records only the owned process IDs and
stop scope. All database volumes, generated private profiles and worktrees were
retained; no application database or real account was reset.
