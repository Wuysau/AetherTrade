# Unified PAPER execution and real acceptance plan

User task: `TradingHandoff/AetherTrade_Next_Engineering_Task_2026-10-27.md`.
Base: `3062154` on `public-snapshot`, one commit newer than task review baseline `acbdcb2` (default-market DNS only).
Approved architecture: `TradingHandoff/AI_Trading_System_Implementation_Package_2026-10-21/docs/spec/approved/trading-architecture/` with approval record `docs/spec/APPROVAL.md`. Preserve original SPEC, IP/T/INV/Q identities, all product and decision modes, and all native/live qualification gates.

## Constraints

- Exactly one writer per worktree; independent changes in separate branches, then reviewed and integrated to this branch. Do not delete worktrees/branches or alter real accounts.
- Record red tests and distinguish implementation defects, fixture errors, environmental failures and genuine unrun checks. No weakened negative/security assertions.
- Every entry, including manual/exit, uses one authorized, risk-checked, durable-before-side-effect execution owner; no new real exchange writing, no paid model call.
- No TESTNET/LIVE qualification claim until all specified PAPER and real PostgreSQL failure-injection gates pass. Mark missing evidence NOT_RUN.
- No invented state or conversion of existing console FILLED records into authoritative exchange evidence. Design an auditable migration with explicit legacy provenance and fail-closed consent.

## Independently reviewable tasks

1. [x] Capture `3062154` baseline: full Python failures, Windows long IDs, CI configuration/status, Linux + isolated PostgreSQL pre-fix evidence with commands, exit codes, server/version and migration checksum.
2. [x] Fix P0 deterministic rule type binding, model TLS hostname typing, and generated product-specific stock/spot order payloads; independent regression tests and commits.
3. [x] Correct defective tests/fixtures while retaining every negative case: typed money, valid authorization time, stale handoff revision, equity tokenize setting, recovery helper argument and future-evidence context, protocol introspection, oversized parameter IDs. Commit independently with root-cause evidence.
4. [x] Derive verified allowed operations under persistent `NO_NEW_RISK`/`EXIT_ONLY` from fresh server-owned evidence; never classify solely by a requested verb. Keep quantity/direction/owner/auth/UNKNOWN and both dispatch checks; fail closed if proof is missing. Integrate reservation and dispatcher gates, tests and commit.
5. [x] Implement a durable console daily-loss cause lock with verified risk-day evidence and explicit evidence-backed resolution; persist denial despite transaction rollback and keep reductions/protection correctly authorized. Test price rebound and restart with SQLite and genuine isolated PostgreSQL.
6. [x] Design and implement the common PAPER command service: AI/RULE/HYBRID/manual and exit handling; compile current target vs actual vs in-flight under the same collateral transaction, authorize/freshness/ownership, risk decision, intent/reservation/command/outbox before effect, one owner and explicit PAPER engine composition. Retain native bridge gate.
7. [x] Implement eventful PAPER execution and projection: SUBMITTED/PARTIALLY_FILLED/FILLED/CANCEL_UNKNOWN/EXPIRED/REJECTED, original client-order-ID query and no blind resend; partial-fill protection, unresolved reservations, persistent protection gaps and restart recovery. Migrate/label legacy console records safely.
8. [x] Connect UI/CLI to shared projection and all three decision modes. Distinguish target, in-flight, fills, verified protection and locks; API receipt is not a fill. Include real decision/risk/exit details; protect existing model and bStocks behaviors.
9. [x] Execute all user C acceptance scenarios on actual isolated PostgreSQL plus forced process failures; record migration digest, fixed commit, reproducible scripts, ledger before/after, outputs and exit codes. Run full Python + web CI and explicit capability matrices; record skipped/NOT_RUN honestly.
10. [x] Deliver `TRADE_LIFECYCLE.md`, `FAILURE_PLAYBOOK.md`, `DESIGN_DECISIONS.md`, minimal terminal/web demonstration and final scoped reviews. Keep Testnet/real funds gated. No push/merge unless requested.

## Integration scan

| Areas | Shared boundary | Ruling |
|---|---|---|
| P0 production / P0 fixtures | Existing tests consume contracts | Different worktrees; preserve safety assertions; integrate both before full retest |
| Lock gate / PAPER command service | Verified action and ledger facts | Shared fail-closed evidence contract; neither command verb nor UI checkbox grants authority |
| Console loss lock / domain RiskLock | Transactions, day scope, authorized resolution | Record a committed cause before returning denial; no implicit price-rebound/rollover unlock |
| User intents / allocation | Target, working and actual under collateral lock | Compile and reserve atomically; do not expose ReservationService as a shortcut |
| Paper engine / owner / recoverer | IDs, events, unknown and side effects | One owner, persist MAY_HAVE_SENT before send, query same ID after uncertainty; no venue qualification claim |
| Legacy console / shared ledger | balances, orders, risk | Explicit provenance and reconciled transfer; no silent creation of verified external events |
| Model/rule/manual UX / API | proposal, permission and visible state | Modes share one service; response state and risk/protection evidence remain distinct |
| Linux / PostgreSQL tests | dedicated per-suite DB | Separate named DBs and isolated cluster, require actual Linux; never point at application data |

## Final local engineering result (2026-10-28)

Implementation and local qualification completed. Exact fixed-source results and
limits: `docs/FINAL_PAPER_REPORT_2026-10-28.md`. Backend802 mandatory gate passed;
ordinary Python2373 passed (optional362 skipped/90deselected retained); frontend108
passed. Real HTTP/CLI/PG desktop/mobile demonstration archived. Remote CI and all
venue/Testnet/LIVE qualification remain NOT_RUN. Branch/worktrees retained, no
push/public merge; do not start the next connector task without explicit request.
