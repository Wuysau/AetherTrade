# OP-E: Fresh Spot owner cost/history

Status: cost-history PostgreSQL cohort passed at e1e4a8e (11 passed, 0 failed/errors/skipped). Affected cohorts are being verified serially. No default host activation.

## Interfaces

- `execution.program_cost_history.reconcile_program_cost_history(tx, *, read_opening)` appends the current authorized owner's cost-only revision in the existing UnitOfWork. `read_opening` must be one of the existing trusted account opening readers accepted by account cash. Returns `ProgramCostHistoryRecord`.
- `require_program_cost_history(tx, *, read_opening=read_program_account_opening)` independently rederives protected source, actual FillLedger, original units, committed V2 quantity transitions, genesis, exact cost and cost-only revisions. Returns the current proof or fails closed.
- `program_cost_math.apply_spot_cost(state, *, side, base_delta, quote_delta, external_fee=False)` is pure exact economic arithmetic, not an authority. `SpotCost` uses rational quantity, remaining cost and realized economics.
- `exact_decimal(Fraction)` returns an exact finite decimal or `None`. No rounding and no epsilon flatness.

## Baseline and policy

`PROGRAM_COST_HISTORY_V1` / `SPOT_WAC_RATIONAL_V1` accepts only a fresh epoch whose first committed native owner transition begins at owner sequence 1 with zero owned quantity, a protected consumed assessment binds its actual source book hash/sequence with no owned positions, and every prior book sequence is present and empty, and the consumed command binds the owner/epoch. The original private source reader checks the consumption hash. All current scope source approvals and actual ledger fills must agree; every owner sequence is accounted for by an authentic quantity transition or a rederived cost revision. Nonzero, legacy, transferred, intervened, missing or ambiguous histories are unsupported. Initial account BTC10 is not an owned lot.

Purchases add net base receipts and the negative net quote delta to cost. Sales release weighted-average cost for the entire net base debit, and realize net quote proceeds minus released cost. Fee effects are already present in authentic asset deltas; no additional FEE cashflow is created. Base and quote fees therefore enter once. Third-asset nonzero fees require independent FX evidence and block this version. Equal timestamps permit homogeneous BUY or SELL groups whose aggregate costs commute exactly. Mixed-side equal timestamps require the authenticated V2 market stream sequence, with no opposing sides sharing one sequence. Unknown or conflicting native ordering remains blocked; trade IDs never supply economic ordering.

Rational numerator/denominator pairs preserve recurring division exactly. A remaining zero quantity has exactly zero remaining cost. Realized ManagedPosition money is populated only when its rational value is exactly representable as a finite decimal. Otherwise the rational proof remains available and a representation reason is recorded. Unrealized valuation remains unknown without an independent current mark/FX contract. This policy does not change risk.pnl's cashflow-adjusted-equity definition.

## Storage and current reads

No schema migration. Cost revisions use a new static tagged audit record, existing RegistryTx, positions, BookSnapshot, EventStore and transactional outbox in one existing UoW. Old V2 quantity rows, hashes and xmin are unchanged. Every cost proof rechecks same-transaction owner/position/book/event/outbox rows and recomputes its economics from its actual historical source prefix. Later fills invalidate cost through the unchanged owned writer; reconciliation appends a new proof. Replay performs no write.

The existing current cash/book guard revalidates complete cost coverage whenever this scope has authenticated native fills. An owner-written COMPLETE/PnL/book binding cannot substitute for source history. TargetCompiler's POSITION_EVIDENCE requirement is unchanged. Working leaves, commands, permits, reservations, TTL, locks and protection remain independent. Same-target NONE is only core quantity math and does not activate a command.

Recovery requires running the same read/reconcile functions in the current authorized scope. There is no historical rewrite, permissive legacy backfill or zero-genesis flag. Unsupported histories require a future separately privileged genesis contract, before trading, rather than runtime self-certification.

## Evidence and qualification limits

Pure arithmetic: `python -X utf8 -m pytest tests/test_program_cost_math.py -q`: 5 passed. Syntax compile check passed.

First fixed-source PG attempt: `a2dd0a1`, suite `program-cost-history`, requested immutable runner `sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7`, timeout 1200, offline dependencies. Launcher `3ff7b768ae4d` exited 1 during postgres-start with Docker Desktop LinuxEngine /_ping HTTP 500. Zero tests ran. This is infrastructure evidence, not genuine RED or qualification.

Actual RED: test-only a2dd0a1, run 78391355f3d5, 1 failure / 0 errors/skips, 6.591s. The funded V2 source/owned chain succeeded before the expected missing-module failure.

Intermediate bc7ef48, run e7f0ef63672c: 9 passed / 2 failed, 0 errors/skips, 83.690s. Both failures occurred before trading because the newer-book fixture reused risk book_sequence 1. The fixture reader was corrected to bind actual current book evidence; no risk verdict or product guard was bypassed.

GREEN e1e4a8e, run 39cb6ea8e603: 11 passed / 0 failures/errors/skips, 95.440s. Covers actual quote-fee BUY, genuine native OTO BUY .4+.6 then SELL .4+.6 to exact flat, both later and equal timestamps with protected stream order, replay, rollback, forked process restart, same-account peer payload/xmin nonmutation, forged COMPLETE/PnL/cost records, later pretrade book revision and earlier nonzero history rejection, and the existing core's .4 owned + .6 authentic working -> NONE with duties retained.

Pending: affected ownership/working/source/cash cohorts. Authentic base/third-asset fee producer is unavailable (existing native model exposes quote_fee_rate only); these variants have exact arithmetic checks but no native-source PG qualification. No LIVE, TESTNET, derivative, historical migration, external-FX, unknown mixed-order or protection qualification is claimed.


## Explicit host entrypoint (2026-10-31)

`ProgramPaperHost.reconcile_cost_history()` reuses the existing cost-history
service in the owner's UnitOfWork with the local account-opening reader. It is
an explicit maintenance operation and is not automatically called by dispatch
or observe. It creates no permit, command, source truth, fee cashflow or release
approval; domain rederivation and transaction failures propagate unchanged.
No migration or historical rewrite is introduced. Recovery uses the same
idempotent reconciliation after source knownness has been re-established.

Light verification at 1c43307: 7 routing/arithmetic cases PASS. The added two
V1/V2 PostgreSQL host scenarios are NOT_RUN; real host qualification remains
blocked on shared DEV recovery/isolation/scheduling. Existing historical PG
results above do not qualify this newly composed entrypoint.
