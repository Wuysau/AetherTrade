# PAPER native quote-budget execution implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans task by task. The assigned worker implements Task 1 only and never spawns agents.

**Goal:** Execute admitted Spot MARKET BUY/SELL quoteOrderQty programs through the existing durable PAPER boundary, with genuine finite-depth fills and independently verifiable quote/base accounting facts.

**Architecture:** Extend the current PAPER execution adapter and shared market/depth consumption system with explicit immutable quote terms and budget progress. Preserve existing BASE execution/report contracts. A quote request remains a quote request; an interval bound is never its original base quantity. The existing command, owner, single-use permit, financial archive, consumed guard and source verification infrastructure remain authoritative.

**Tech Stack:** Python, existing PostgreSQL business/SIM databases, existing isolated Linux Docker qualifier and pinned offline image.

**Spec:** `qualification/binance_orders/reference/AetherTrade_Binance_Order_Architecture_2026-10-28.md`, ORD-11/12/43/46/47 in the reference acceptance matrix, `docs/orders/ADR-ORDER-001.md`, and the already approved `docs/orders/OP-C-QUOTE-INTERVAL-PLAN.md` plus quote financial/current-dispatch documents.

## Global Constraints

- Every AetherTrade-initiated external write keeps an independent durable command, one-use permit and current execution-owner validation. Exchange-generated children remain observations and are never resubmitted.
- Preserve PostgreSQL ledger, reservations, original IDs, UNKNOWN recovery, old schemas, command hashes and old source bytes. No alternate execution or balance core.
- Remain isolated PAPER. TESTNET/LIVE and real accounts stay disabled; no paid product-model calls, public push, main checkout edits or branch cleanup.
- No unauthorized LIMIT-to-MARKET conversion, hidden request resizing, synthetic original base quantity, fake fills, weakened risk, deleted safety cases or skipped mandatory cases.
- Existing migrations 0013 and 0014 are frozen byte for byte. Additive SIM migration 0025 is reserved for this task; coordinate any further migration number with the parent. Do not modify 0021/ROOT physical-scope work, CASH owned posting, management 0022/0023 or initial proof 0024.
- Keep all 46 capability targets and all 56 acceptance cases. A bounded PAPER slice does not establish native/whole-product qualification.

## Review Focus

- A legal quote request reaches an actual bounded matcher without treating amount/price upper enclosure as origQty; both BUY and SELL use real opposing depth.
- Fee debits, gross quote usage, received asset and lot dust remain distinct, exact and bounded under a low ambient Decimal precision.
- All commands/accounts sharing a market consume each visible unit once, including mixed BASE and quote requests and duplicate events.
- An original permit expiring or an owner/fence changing during a SIM lock wait prevents side effects; no permit renewal or second consume occurs.
- A resealed report cannot forge quote budget, base fills, cumulative usage, currency/unit, source event or terminal state; old BASE source results and hashes remain unchanged.

## Task 1: Quote-budget producer and trusted report source

**Files:**

- Create `src/trading_system/engine/paper_market_quote.py` for explicit quote terms/progress/report types and bounded adapter/query support.
- Create `migrations/0025_paper_quote_budget.sql` for additive quote runtime state and shared-depth execution routing.
- Modify only necessary adapter dispatch points in `engine/paper.py` and `engine/paper_market.py`; preserve the single existing authority path and frozen V1/V2 BASE semantics.
- Create `src/trading_system/execution/paper_quote_reports.py` for independent source verification. Reuse existing source and evidence helpers rather than inventing a second approval system.
- Create `tests/integration/test_paper_quote_budget_postgres.py` and focused unit cases as needed; register the mandatory isolated qualifier suite in `tools/ci_paper_postgres.py`.
- Add a concise task report with tested source, real failures/results, additive migration/recovery semantics and explicit remaining registry/financial qualifications.

**Interfaces:**

- Consume the current immutable QuoteAssessmentArchiveV1/QuoteFinancialIntentV1 and actual `ProgramCommandBoundary`/`ProgramWritePermit`/`ExecutionOwner` composition. Existing `PaperEngine.submit_creation(...)` is the public execution entry and must keep one consume.
- A quote adapter helper, if necessary, uses `submit_quote_creation(engine, write, request, permit, *, boundary, lease, owner)`. It grants no alternate authority.
- Produce explicitly tagged immutable quote terms, actual runtime budget and source-backed quote report/verification. Choose the smallest versioned types consistent with existing static codecs; document their precise exported signatures before implementing them.
- Root/CASH integration will separately register the trusted quote verifier and post actual verified native fills. This task must expose authentic uncapped fills and source economics without claiming that downstream accounting or initial inventory ownership is already qualified.

- [ ] **Step 1: Confirm baseline and write the failing executable tests.** Start from fixed b7f6d85aab22097d05ed4ec80c032af21aa60dc8, verify clean worktree and current adapter/SQL/source contracts. Read the named spec/current quote documents, and record the narrow versioned state/report design in the task report. Existing guards must remain the baseline. Write real PG cases for BUY and SELL quote amount 4000 through genuine funded financial admission/MAY/consume, with prices 50000 and 60000 and finite depth. Assert the immutable request still contains quoteOrderQty=4000 and lacks a fabricated quantity; exact actual base fills and gross quote derive from consumed levels. Run to observe the genuine unsupported-path RED before implementation.
- [ ] **Step 2: Implement explicit quote state through the existing adapter and depth allocator.** Keep requested quote, cumulative gross quote, remaining quote, actual base and fees distinct. Reuse the same market mutex, original consumption proof, shared liquidity sources, fill identities and original-ID recovery. Model terminal/unspent lot dust explicitly; spending a budget is not by itself an order lifecycle proof. Missing price/fee/lot/model evidence denies and keeps durable recovery duties. Do not silently change old positive BASE-only table/order semantics to manufacture an original quantity.
- [ ] **Step 3: Add independent source verification and adversarial cases.** Bind actual immutable quote terms and original request/consumption to actual stored source events and market allocations; verify every quote/base/fee/currency/unit field. Assert genuine BUY/SELL partial/no-fill, actual insufficient cash/base denial, quote fee and any newly implemented asset-fee model, lot dust, upper/lower execution band, mixed BASE/quote shared liquidity and replay/restart. Preserve actual overshoot facts with discrepancies instead of capping them. Unsupported fee models remain explicitly blocked until implemented, never assumed zero.
- [ ] **Step 4: Exercise authority and compatibility.** Actual advisory wait expiry/current fence/owner negative must leave no order/fill side effect, retaining original MAY/consumed recovery identity. Cover forged quote report/request/terms/runtime and private-role denial. Run the new mandatory PG suite plus the existing relevant frozen BASE matcher and V2-source compatibility suites on the fixed changed source. No blanket full-tree reruns after documentation-only commits.
- [ ] **Step 5: Commit the reviewable slice.** Preserve original RED and final logs/XML/launcher manifest with source identity; publish only sanitized evidence. Summarize code, exact exported interfaces, migration privileges/backward compatibility, restart/UNKNOWN recovery and remaining unsupported execution/fee/financial routes. Return a focused BASE-to-HEAD review package. Parent arranges independent spec/quality review before integration.

## Current official interface check

Checked publicly on 2026-10-30: [Binance Spot trading endpoints](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/trade). MARKET quoteOrderQty denotes quote spend for BUY and desired gross quote receipt for SELL; base quantity follows liquidity and LOT_SIZE. SOR does not support quoteOrderQty. No signed endpoint or account was accessed. PAPER terminal/liquidity assumptions must be documented separately from native exchange observations.
