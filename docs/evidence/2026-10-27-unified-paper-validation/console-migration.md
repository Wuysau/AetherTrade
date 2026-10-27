# Console guarantees through unified PAPER

The old positive execution fixtures used SQLite and immediate local FILLED rows.
They now use the production provisioner to create three fresh PostgreSQL
databases, the production authority attachment, the real command owner and
PaperEngine, and explicit synthetic-controller events. No production fallback,
mock database, reset of an existing database, or automatic fill was introduced.

`support.unified_console.provision_console` returns a harness with a real Store,
runtime configuration, attached reopen support and explicit `execute`, `fill`,
and `fill_pending` scenario operations. `execute` first asserts the product's
original PENDING receipt, then dispatches and injects a fill, returning both the
unchanged receipt and the separate actual order projection. Authentication,
current quote, policy, strategy and ownership checks use the same production
`attach_console_runtime` function as the app.

Execution-dependent server tests carry the registered `paper_runtime` marker.
The ordinary Python job excludes those tests and the mandatory integration
modules; the required PostgreSQL runner separately executes `tests/server -m
paper_runtime`. Missing PostgreSQL configuration fails those fixtures. Skipped,
absent and failed cases still fail the mandatory gate. Each case provisions fresh
databases and restricted logins; no old `aethertrade_tests` database is dropped.

Preserved assertions include exact entry notional 5000 and fee 5, cash 94995
after one filled order, concurrent duplicate receipts, restart identity, no
shorting, pause and size guards, risk/strategy revision checks, missing-quote and
confirmation exit rejection, exact partial-sale cash 97392.6, final cash 100989
and total fees 11, and win-rate accounting for both entry and exit fees.

Two changed test contracts follow the new architecture explicitly:

- An exit control receipt queues exits. Tests assert positions remain present
  before explicit controller fills and disappear after those actual events.
- A failure persisting a console event can occur after a separate domain
  transaction committed its command. The test keeps cash and positions unchanged,
  checks no legacy console order or console receipt committed, retains the
  recoverable PENDING command, then retries the same identity and verifies one
  final economic fill. It does not invent atomic rollback across two databases.

At fixed source `1f0d76b4499a9b485663ecf1a9b72cc404f440c7`:

| Actual Linux/PostgreSQL scope | Result |
|---|---|
| Fresh console race/restart/idempotency/exit, persistent loss denial/rebound/restart/reduction, pause/no-short | 3 passed, zero skips/errors, exit 0 |
| Migrated ten server execution cases | 7 passed, 3 failed, zero skips/errors, exit 1 |

The three server failures retain original economic/product assertions and expose
production regressions: changing a strategy symbol ignores its attributed unified
holding; selling the remaining 0.06 BTC after a 0.04 BTC partial exit is rejected
with INSUFFICIENT_SPENDABLE:BTC; and an unprofitable round trip after both fees has
win_rate None rather than 0. The raw partial-sale ledger is retained for diagnosis.
No safety assertion was removed to obtain a green result.

The same source's ordinary SQLite subset had 11 passes and one failure: a SELL
without unified execution now correctly returns UNIFIED_PAPER_REQUIRED rather
than entering the domain no-short check. Commit `c18ce07` changes that obsolete
SQLite expectation while retaining the real no-short counterexample in the
mandatory PostgreSQL module; it also adds a legacy FILLED row read-only/no-import
counterexample.

`console-migration-red.zip` and `console-migration-manifest.json` preserve original
commands, exit codes, XML/logs, source/migration information, and the synthetic
partial-sale ledger. This report does not claim the full integrated gate passed.

## Fixed-source followup

At `4ff150f1a8bb63f9f4b694fc3ec685e1a8e93512`, after production fixes
`6ab4d6d`, `2e9e4b1`, and `ff84d45`, independent actual Linux/PostgreSQL runs
passed all **4 console integration cases** and all **10 migrated server execution
cases**, zero skips/errors, exit 0. The ordinary SQLite subset passed **12 cases**,
with 10 explicitly deselected for the mandatory PostgreSQL job. The original
symbol-attribution, exact partial/full cash, total-fee and win-rate assertions all
remain and now pass. Legacy immediate fills remain read-only and are never
imported as unified execution evidence.

The release fix retains the domain's conservative balance-watermark behavior.
Runtime now re-attests already terminal, already released obligations against
the current complete economic balance under the actual collateral/owner guards.
Held and UNKNOWN obligations cannot become released through that attestation.
Additional dedicated safety cases and the fresh whole gate still need to run
at the final integrated commit.

`console-migration-green.zip` and `console-followup-manifest.json` preserve those
raw logs/XML and exact commands, exit codes, migration and fixed-source metadata.
