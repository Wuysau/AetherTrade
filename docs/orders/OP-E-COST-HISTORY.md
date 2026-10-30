# OP-E: Fresh Spot owner cost/history

Status: implementation checkpoint; PostgreSQL qualification pending infrastructure recovery. No default host activation.

## Interfaces

- `execution.program_cost_history.reconcile_program_cost_history(tx, *, read_opening)` appends the current authorized owner's cost-only revision in the existing UnitOfWork. `read_opening` must be one of the existing trusted account opening readers accepted by account cash. Returns `ProgramCostHistoryRecord`.
- `require_program_cost_history(tx, *, read_opening=read_program_account_opening)` independently rederives protected source, actual FillLedger, original units, committed V2 quantity transitions, genesis, exact cost and cost-only revisions. Returns the current proof or fails closed.
- `program_cost_math.apply_spot_cost(state, *, side, base_delta, quote_delta, external_fee=False)` is pure exact economic arithmetic, not an authority. `SpotCost` uses rational quantity, remaining cost and realized economics.
- `exact_decimal(Fraction)` returns an exact finite decimal or `None`. No rounding and no epsilon flatness.

## Baseline and policy

`PROGRAM_COST_HISTORY_V1` / `SPOT_WAC_RATIONAL_V1` accepts only a fresh epoch whose first committed native owner transition begins at owner sequence 1 with zero owned quantity, a protected consumed assessment binds its actual source book hash/sequence with no owned positions, and every prior book sequence is present and empty, and the consumed command binds the owner/epoch. The original private source reader checks the consumption hash. All current scope source approvals and actual ledger fills must agree; every owner sequence is accounted for by an authentic quantity transition or a rederived cost revision. Nonzero, legacy, transferred, intervened, missing or ambiguous histories are unsupported. Initial account BTC10 is not an owned lot.

Purchases add net base receipts and the negative net quote delta to cost. Sales release weighted-average cost for the entire net base debit, and realize net quote proceeds minus released cost. Fee effects are already present in authentic asset deltas; no additional FEE cashflow is created. Base and quote fees therefore enter once. Third-asset nonzero fees require independent FX evidence and block this version. Equal timestamps permit homogeneous BUY or SELL groups whose aggregate costs commute exactly. Mixed-side equal timestamps require additional causal evidence and remain blocked.

Rational numerator/denominator pairs preserve recurring division exactly. A remaining zero quantity has exactly zero remaining cost. Realized ManagedPosition money is populated only when its rational value is exactly representable as a finite decimal. Otherwise the rational proof remains available and a representation reason is recorded. Unrealized valuation remains unknown without an independent current mark/FX contract. This policy does not change risk.pnl's cashflow-adjusted-equity definition.

## Storage and current reads

No schema migration. Cost revisions use a new static tagged audit record, existing RegistryTx, positions, BookSnapshot, EventStore and transactional outbox in one existing UoW. Old V2 quantity rows, hashes and xmin are unchanged. Every cost proof rechecks same-transaction owner/position/book/event/outbox rows and recomputes its economics from its actual historical source prefix. Later fills invalidate cost through the unchanged owned writer; reconciliation appends a new proof. Replay performs no write.

The existing current cash/book guard revalidates complete cost coverage whenever this scope has authenticated native fills. An owner-written COMPLETE/PnL/book binding cannot substitute for source history. TargetCompiler's POSITION_EVIDENCE requirement is unchanged. Working leaves, commands, permits, reservations, TTL, locks and protection remain independent. Same-target NONE is only core quantity math and does not activate a command.

Recovery requires running the same read/reconcile functions in the current authorized scope. There is no historical rewrite, permissive legacy backfill or zero-genesis flag. Unsupported histories require a future separately privileged genesis contract, before trading, rather than runtime self-certification.

## Evidence and qualification limits

Pure arithmetic: `python -X utf8 -m pytest tests/test_program_cost_math.py -q`: 5 passed. Syntax compile check passed.

First fixed-source PG attempt: `a2dd0a1`, suite `program-cost-history`, requested immutable runner `sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7`, timeout 1200, offline dependencies. Launcher `3ff7b768ae4d` exited 1 during postgres-start with Docker Desktop LinuxEngine /_ping HTTP 500. Zero tests ran. This is infrastructure evidence, not genuine RED or qualification.

Pending: actual native source reconciliation PG RED/GREEN; affected owned, working, source, cash cohorts; authentic SELL/base-fee native cycles; restart/peer nonmutation execution (tests authored). No LIVE, TESTNET, derivative, historical migration, external-FX or protection qualification is claimed.
