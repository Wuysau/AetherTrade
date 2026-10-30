# Effective order management implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Assigned worker implements Task 1 only, without subagents.

**Goal:** Execute authorized Spot KEEP_PRIORITY quantity reductions through durable management, shared PAPER matching and an independently authenticated effective-order source.

**Architecture:** Start from ROOT2b94894f815b131c4e5f6be73f15ce5ce918f567, which already combines current capital/permit/quantity guards, quote0025, native0026 and independently reviewed management0022/23. Add an explicit effective quantity/queue overlay while preserving immutable creation terms, existing budgets and shared depth. Use the existing management compiler, boundary, current checks and single-use permit; no second management or matching core.

**Tech Stack:** Existing Python, closed codecs/schemas, PostgreSQL business/SIM stores and isolated Docker qualifiers.

**Spec:** ADR-ORDER-001; reference architecture and ORD-21/38/42/46/47; approved effective-terms handoff in the old management worktree (`../binance-ope-management-v4/docs/orders/OP-E-MANAGEMENT-EFFECTIVE-TERMS-HANDOFF.md`). Root integrates its reviewed current interfaces already.

## Global Constraints

- Every application amendment has its own durable command, fresh current owner/authorization and one-use management permit. Creation/previous/UNKNOWN commands never authorize a retry.
- Preserve original creation/management schemas, hashes, rows, IDs, source events, UNKNOWN recovery, reserves and protection responsibilities. No hidden LIMIT/MARKET conversion or new risk under an amendment.
- All existing funds remain held until a separately qualified release proof. Acknowledged reduction is not fill, ownership, cash credit, FLAT or protection success.
- Keep ROOT0013/14, management0022/23, quote0025 and native0026 migration bytes unchanged. New0027 business and0028 SIM migrations are reserved for this task. Coordinate any additional number with parent.
- Preserve quote/native shared liquidity, source manifests, current venue-position limits and after-wait guards; never replace the matcher with an old0014 implementation.
- Keep all46 targets/56 cases; list/contingent amendment, MODIFY_LIMIT, cancel-replace and protected/exit actions remain required following slices, not silently deleted capabilities.
- Isolated PAPER only. No LIVE/TESTNET/accounts/paid product models, public push, main/archive changes, fake fills, weaker risk, deleted/skipped safety tests or arbitrary transport callbacks.

## Review Focus

- An actual1.0 resting order reduced to0.5 retains its original price and queue priority; only0.5 may fill, while original submitted terms/hash remain recoverable.
- An actual0.2 partial fill followed by reduction to0.5 leaves0.3; no double counting, source fabrication, fee alteration or premature hold release.
- Original creation TTL may expire without ending management duty. Every amendment still needs fresh current authority and the exact original management permit after real market/row waits and before commit.
- A fill, earlier amendment, owner/lease/fence change or unknown result cannot make a stale management target revision executable.
- Source verification distinguishes immutable creation, actual effective terms, management receipt and fills; it rejects forged overlays, target revisions, new quantities, IDs and queue histories while retaining raw facts.

## Task 1: Genuine KEEP_PRIORITY with source-bound effective terms

**Files:** Extend current `execution/program_management_{admission,commands,consumed_boundary,paper,outcomes}.py` only where needed; add focused effective-order source/overlay helpers and `migrations/0027_*`/`0028_*`; add narrowly scoped matcher/query hooks; extend `tests/integration/test_program_management_postgres.py` or a new effective-order suite and register it in `tools/ci_paper_postgres.py`. Add `docs/orders/OP-E-EFFECTIVE-ORDERS.md` with exact interfaces and recovery. Do not edit existing closed schemas merely to permit an enum already present. If new serialized meaning/fields require a new revision, add an explicit codec/schema version while preserving all old bytes and recovery routes.

**Interfaces:** Reuse `ProgramManagementService.admit/current`, current `ManagementBoundary`, `compile_order_management`, `materialize_management` and `PaperManagementTargetReader.capture`; public PAPER submission remains the existing management entry. Define precise overlay/source signatures in the task report before implementing, consistent with existing contracts. Capture source outside business transactions; recheck exact protected consumed authorization under actual SIM locks, retaining reviewed expiration checks. New source/effective history must have a static tag, not impersonate an unchanged ordinary V2 report.

- [ ] **Step 1: Baseline/official semantics and genuine RED.** Inspect current ROOT2b rather than old code locations; verify the frozen file hashes. Recheck current official Spot amend documentation and the existing compiler's restrictions, including partial quantity, client-ID behavior and list limitations. Use real funded admission and a1.0 nonprotective/nonreduce ATOMIC Spot LIMIT, then a fresh management reduction to0.5. Observe the actual unsupported admission/reducer failure before implementing; preserve original51 management cases.
- [ ] **Step 2: Existing financial/management boundary.** Authorize only a proved decrease of the actual current entry quantity, with unchanged price and original risk envelope. Reuse current owner/authorization/target/budget/cause checks. Keep original full funding; no fresh-risk or protected-action exception by fake old TradeIntent. Ensure native request preserves stable original IDs explicitly where the official API would otherwise rename a client ID.
- [ ] **Step 3: Additive effective state and shared matching.** Persist immutable management-linked overlay/receipt with venue/account/product/order identity, effective quantity, prior revision and queue source. Match and query through the same current shared matcher/depth/position mechanisms. Preserve immutable accepted terms and original command hash; retain separate original/effective/cumulative/leaves meanings. No temporary rewrite of frozen terms and no duplicate matching engine.
- [ ] **Step 4: Independent source and actual PG.** Authenticate every overlay against the actual durable management/consumption/receipt source and original order. Prove1→0.5 FIFO against a competing order, partial0.2→total0.5, repeated reductions, stale target/fill races, same-ID recovery after lost ACK, no duplicate write, real advisory/row wait expiry, new owner/fence, rollback and raw source forgery rejection. Use real private PG/finite market data. Below-cumulative/rename/list semantics that lack authoritative support stay explicitly blocked with evidence; do not invent platform behavior. Add affected existing51 management and shared source/matcher regressions once fixed.
- [ ] **Step 5: Commit/review handoff.** Commit code with exact tested source, original RED/final logs/XML/manifests, additive role/migration/recovery notes and a focused BASE-to-HEAD package. All original handles must be terminal. Parent arranges independent review. Static financial-source registration and executable host enabling are separate qualifications; no claim that a source DTO grants trading authority.

Current public documentation: https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade . Checked2026-10-30: amend uses PUT /api/v3/order/amend/keepPriority; newQty is positive and below current order quantity; client ID may be retained explicitly. No signed endpoint/account was accessed. Verify any more detailed semantics from current primary sources before implementing them.
