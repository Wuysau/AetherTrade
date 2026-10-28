# PAPER whole-account dispatch risk regression

Scope: Windows Python application connected to the existing, explicitly isolated Docker PostgreSQL cluster on port 55433. Each console test provisions three new databases and restricted roles. No databases were reset/deleted. No application database on 55432, real venue, or model API was used. This is not Linux process/failure qualification or final integrated acceptance.

Base production source: `dd4fda4`. Fixed source: `7de8727`. Final source and migration byte hashes are in `source-sha256.txt`. RED production source is exactly the base commit; the two-test RED file was not separately hashed at execution time (NOT_RECORDED). It contained the first two focused tests before the later boundary/exit tests were added.

All commands ran from `D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-dispatch-account-risk` using `D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-execution/.venv/Scripts/python.exe`. The exact environment loader is archived as `run_probe.py.txt`; it reads credentials internally from the main worktree's ignored runtime files without printing them. These credential files are intentionally not archived. Every `.exit` records the child pytest exit code.

| Log suffix | Arguments after `.runtime/run_probe.py` | Result |
| --- | --- | --- |
| `30e809bc` | `tests/integration/test_paper_dispatch_account_risk.py` | RED: 2 failed, exit 1, both DID NOT RAISE: the original cross-symbol daily loss and the independent current-equity position cap |
| `e81c9172` | `tests/integration/test_paper_dispatch_account_risk.py tests/integration/test_unified_console_contract.py` | Initial fix: 6 passed, exit 0 |
| `829d1d51` | `tests/integration/test_paper_dispatch_account_risk.py tests/integration/test_unified_paper_runtime.py tests/integration/test_durable_dispatch.py tests/unit/test_locked_actions.py` | 135 passed, 61 skipped, exit 0. 16 actual PostgreSQL tests + 119 unit tests. All 61 IP10 Linux dispatcher/process tests NOT_RUN on this Windows run |
| `9b213292` | `tests/integration/test_paper_dispatch_account_risk.py::test_daily_lock_keeps_bounded_exit_available_with_unrelated_stale_quote` | Test fixture failure: `.05` is not an accepted decimal spelling. Corrected to `0.05` and `0.005`; no production validation changed |
| `76965f1f` | `tests/integration/test_paper_dispatch_account_risk.py` | Final 8 focused PostgreSQL tests passed, exit 0 |

The original scenario fills BTC `0.04 @ 50000` from 10000, leaving cash 7998; ETH BUY `1.2 @ 2000` is accepted while BTC is still 50000. A fresh BTC price of 1 must deny ETH and persist DAILY_LOSS with observed equity 7998.04 in both console and business ledgers before returning. Rebound and reopening the runtime cannot unlock it. A BTC price of 25000 independently denies the current-equity position cap without a daily-loss breach.

The five late-boundary cases introduce the loss at: the post-durable dispatcher check, the initial engine authority check, the pre-order-insert check, the pre-command-insert check, and the final precommit check. They assert UNKNOWN remains durable, staged venue writes roll back, the loss cause remains committed outside that rollback, and a restarted runtime never resends. The final case allows a bounded BTC SELL with stale unrelated ETH quotes while rejecting an oversell.

Implementation: a trusted optional dispatcher callback runs under the existing owner guard outside business UoWs. The production console composition also invokes it at each existing PaperEngine authority boundary. Whole-account marks use the current authenticated quote reader; the immutable request, reservations, permission/quantity/ownership checks, native gate, and configured risk caps remain in force. Only opening BUY commands require whole-account valuation; bounded maintenance retains its original checks and does not gain an unrelated quote dependency.

Remaining scope: direct standalone runtime test compositions without `attach_console_runtime` have no current whole-account quote provider and retain their fixture behavior. This evidence does not qualify them as a production entrypoint. DAILY_LOSS recovery metadata/review and protected reduction are separate agent changes; full final integrated Linux qualification, broader server suites, and UI demonstration are NOT_RUN here.
