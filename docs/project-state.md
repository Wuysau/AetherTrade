# Project state

AetherTrade is a Python/FastAPI and TypeScript web trading workbench. Approved
architecture lives in `TradingHandoff/AI_Trading_System_Implementation_Package_2026-10-21/docs/spec/approved/trading-architecture/`.
The 2026-10-27 engineering task preserves AI/RULE/HYBRID and Spot/USD-M/Stocks.

- `src/trading_system/server`: authenticated console, models, market data.
- `intents`, `risk`, `execution`, `storage`, `protection`, `ownership`, `recovery`:
  typed domain services and append-only PostgreSQL business ledger.
- `engine/paper.py`: separate durable synthetic venue, explicit event injection.
- `migrations`: additive ledger and authority restrictions; never apply tests to
  the user's console or real account database.
- `tools/ci_paper_postgres.py`: required isolated Linux/PostgreSQL safety gate;
  skipped safety cases fail qualification.

Persist commands before side effects. Query original IDs after uncertainty.
Cause locks require evidence-backed resolution. Native paths remain disabled;
PAPER, TESTNET and LIVE qualifications are distinct. Do not change approved SPEC
or historical evidence to make a gate green. No real orders or paid model calls.
Keep one writer per worktree; preserve existing worktrees and branches.

The common PostgreSQL PAPER composition now lives in `server/paper_runtime.py`;
explicit three-database provisioning is in `server/paper_provision.py`. Ordinary
runtime, venue submission, synthetic event control and trusted resolution use
separate authority boundaries. The native path remains disabled.

Console orders now create durable PENDING commands; explicit synthetic events
produce fills. SQLite execution fails closed. Existing console fills remain
read-only legacy synthetic records and are never silently imported as unified
evidence. Protection verification is labeled VERIFIED_SYNTHETIC to users.

The integrated lifecycle is not fully qualified: dispatch-time whole-account
risk revaluation and reductions with active protection have outstanding P1
findings, with full integration gates still pending. The user resumed the task
on 2026-10-28; see current-task.md for active work and HANDOFF-2026-10-28.md for
the preserved pause checkpoint.
