Historical checkpoint — user resumed on 2026-10-29; see current-task.md.

# OP-B pause checkpoint

Status: PAUSED_BY_USER. The user explicitly requested saving state then pausing.
Do not continue implementation, tests, OP-C, integration or native qualification
until the user resumes. No operational services/accounts were changed by OP-B.

Worktree: D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/binance-orders
Branch: codex/binance-order-capabilities
HEAD: e8cb1d10ea3e81b2aea08b8ad2d5119c90fefe68

## Authorization and decisions

ADR-ORDER-001 has been approved by the user. Every application-originated write
requires its own durable command, permit and owner check. VENUE_GENERATED_CHILD
is verified observation only; never submit it again. The original inventory has
been archived under qualification/nautilus/history; v2 retains the old child-write
alias and adds explicit application-child writes / venue-child observations.
The original four 2026-10-20 schemas must remain byte-identical.

## Completed and verified

- OP-A commit aa4ca85: reference package, complete46-capability/56-case inventory,
  audit tool and prior406 tests. Not execution qualification.
- Native-lock agent commit c2d20b7 was cherry-picked as e8cb1d1: stable Nautilus
  1.231.0, Windows cp314/AMD64, source27a8e54e7ac3c57d6cbf8891f0283dfbaee97317,
  45 hashed dependency artifacts. Isolated install/import/LIMIT construction and
  five guard tests passed. Linux/container still BLOCKED; native venue tests
  NOT_RUN and TESTNET/DEMO/LIVE disabled. Original native lock preserved.
- New OrderProgram/AtomicOrderSpec codec and two new public/packaged schemas:
  8 contract tests passed at first implementation checkpoint.
- Pure child lifecycle first tests: cancel-race red reproduced then corrected.
- Independent focused review identified six issues: decimal rounding, execution
  epoch mismatch, incomplete exposure/protection calculation, missing template
  topology checks, status regression, restored state budget mismatch.
- Three of those red regressions were reproduced; after precision/state changes
  lifecycle14 tests passed at that checkpoint. Subsequent edits are NOT_RUN.

## Uncommitted implementation (incomplete)

- ADR acceptance, ADR-005 amendment, history/v2 write inventory, OP-B plan.
- contracts/order_program.py: immutable typed variants, program DAG/codec; public
  and packaged order-program/atomic-order schemas plus isolated regression fixture.
- contracts/order_lifecycle.py: parent authority, venue observations/fills/fees,
  pure reducer, application write and permit guard. Added explicit funding
  requirements/base asset/starting position/allowed sides since last green run.
- storage/order_programs.py: NEW, untested. Existing UoW/group mutex and
  reservations/owner reads, parent binding, trusted ingress callbacks, idempotent
  source facts, child/trade/funding links, reconciliation cases and risk locks.
  mark_may_have_sent intentionally refuses OP-C compiler/risk admission; no new
  outgoing path is enabled. Original durable parent is required before binding.
- migrations/0008_order_program_records.sql: NEW, untested and never applied.
  Additive append-only ledger records/links and command transition trigger.
- Existing frozen Schema bytes and production console were not modified.

## Required work when resumed

1. Finish the six review findings and add regressions for all, especially OCO/
   OTO/OTOCO/OPO/OPOCO topology and child-origin constraints (not yet fixed),
   lease.execution_epoch and net/protection/base-fee/short cases (changes need tests).
2. Review new repository carefully: trusted parent/source checks, exact durable
   association, cumulative/remaining budget, cross-parent funding uniqueness,
   observation idempotency and conflict retention, risk locks without rollback.
   Do not treat callback booleans or imported metadata as execution qualification.
3. Add explicit capability/rule snapshot types, lifecycle schemas/version-dispatch
   compatibility as needed; test old hashes and UNKNOWN restoration.
4. Add real isolated PostgreSQL migration/restart/dedup/concurrency/role tests;
   wire migration into only explicit isolated provisioning and mandatory test
   runner. Neither PG tests nor migration have run in this task yet.
5. Keep parent risk/funding computation and actual outgoing compilation for OP-C;
   no second balance ledger and no resubmission of native children.
6. Native target review and final integrated verification remain due. Full56 ORD
   scenarios still NOT_RUN; do not promote them based on codec/unit tests.
7. Commit reviewed OP-B functional groups plus migration/recovery notes, then
   proceed to OP-C only after OP-B acceptance. No merge/push performed.

## Git status at pause

```text
 M docs/orders/ADR-ORDER-001.md
 M qualification/nautilus/write-path-inventory.json
?? contracts/atomic-order.v2026-10-28.schema.json
?? contracts/order-program.v2026-10-28.schema.json
?? docs/orders/ADR-005-amendment.md
?? docs/orders/OP-B-PLAN.md
?? migrations/0008_order_program_records.sql
?? qualification/nautilus/history/
?? src/trading_system/contracts/order_lifecycle.py
?? src/trading_system/contracts/order_program.py
?? src/trading_system/schemas/atomic-order.v2026-10-28.schema.json
?? src/trading_system/schemas/order-program.v2026-10-28.schema.json
?? src/trading_system/storage/order_programs.py
?? tests/contracts/test_order_lifecycle.py
?? tests/contracts/test_order_program_v2.py
?? tests/fixtures/orders-v2/
```
