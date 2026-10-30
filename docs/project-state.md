# Project state

Accounting branch checkpoint2026-10-30: fresh writer released after product
f0707c5/evidence0c13a96; exact continuation/ownership lives at current-task.md top.
Protected initial proof0019/0020+additive0024, observed-only versus strict cash
readers, and versioned native→owner/position/book transition proof exist in this
branch. Full Root integration, legal2command and actual V2 financial proof remain
unfinished; latest ownedPG10pass/1fixture prerequisite is not a green full gate.
Full Binance46/56 goal remains ACTIVE; no operational trading was resumed.

Latest engineering2026-10-29: ACTIVE full Binance46-target/56-case OP-C→D/E/F→G
continuation. Implementation tree is TradingHandoff/AutoDispatchWorktrees/binance-orders;
sole writer integration_next, parent coordinates/reviews (and may own separate
OP-E tree). Actual shared typed financial/request/local activation integration is
under verification, with native quote interval financial and later work pending.
Authoritative live engineering checkpoint: docs/current-task.md top sections.
Older completed PAPER/operational states below are preserved historical scope;
they do not qualify current full order capability work. No account/Testnet/LIVE
or paid model calls are authorized by this engineering continuation.

Latest 2026-10-28 15:48 +08:00: user authorized real-quote PAPER and paid models.
New API/UI, owner and monitor run; automatic strategy remains paused due to two
INVALID_MODEL_RESPONSE results, no proposal/order. MODEL_PROTOCOL_BLOCKED.
DeepSeek revision4 now uses 4096 output tokens/60 seconds; no recurring paid
loop started. See current-task.md for evidence and resume intent.


Decision evidence update (2026-10-28): structured snapshot/assessment, explicit
evidence review and target-aware unified PAPER intake implemented and verified
on backend d6b07c1 (808 mandatory PG, zero skips/failures), frontend8422f3f
(111 tests, build/typecheck). See DECISION_EVIDENCE_REPORT_2026-10-28.md for
D01–D22 coverage and explicit unsupported UPDATE_EXIT/adapter boundaries.
Existing strategies require explicit revision upgrade; operational testing remains paused.


Latest operational state (2026-10-28 14:08 +08:00): continuous PAPER testing is
PAUSED at the user's request. Strategy scheduling and automatic orders are off;
the diagnostic owner and monitor have stopped. API/UI remain available. Cash is
100000 USDT, with no positions, unfinished orders or active risk locks. Do not
resume without a user request. Current-task.md contains pause evidence.

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
or historical evidence to make a gate green. No real venue orders. The user has
now authorized real DeepSeek calls for the continuing real-market PAPER test;
that authorization does not enable any real-account trading connector.
Keep one writer per worktree; preserve existing worktrees and branches.

The common PostgreSQL PAPER composition now lives in `server/paper_runtime.py`;
explicit three-database provisioning is in `server/paper_provision.py`. Ordinary
runtime, venue submission, synthetic event control and trusted resolution use
separate authority boundaries. The native path remains disabled.

Console orders now create durable PENDING commands; explicit synthetic events
produce fills. SQLite execution fails closed. Existing console fills remain
read-only legacy synthetic records and are never silently imported as unified
evidence. Protection verification is labeled VERIFIED_SYNTHETIC to users.

The local synthetic PAPER engineering gate passed on fixed backend `6cbc870`:
802 mandatory Linux/PostgreSQL tests, no skips/failures; frontend108 passed.
Dispatch-time whole-account risk, protected reductions and reviewed recovery
have regression coverage and no currently unresolved reviewed P0/P1/P2 findings.
Real HTTP/CLI/browser examples preserve explicit synthetic provenance.

This is local engineering qualification only. Console composition remains the
existing Spot shape; final USD-M/Stocks scope is preserved but each real adapter
needs separate qualification. Remote CI, Testnet/Demo, real model calls, actual
venue liquidity/protection and LIVE readiness are not established. Daily unlock
is conservative: next configured risk day + flat complete account + separate
reviewer + explicit review id. No price-based or model-based automatic unlock.
See current-task.md and FINAL_PAPER_REPORT_2026-10-28.md for the final boundary;
HANDOFF-2026-10-28.md remains a historical pause checkpoint.

Current operational update (2026-10-28): the unified implementation is merged
into local public-snapshot. Real Binance quotes plus DeepSeek BTC analysis run
continuously at the user's request, until the user requests pause. Model calls
have now passed; earlier NOT_RUN model statements describe historical gates.
The 300-second strategy, independent PAPER owner and 30-second read-only monitor
are running from the main checkout. See current-task.md for current processes,
repairs, explicit synthetic expiry evidence and the pause procedure.
