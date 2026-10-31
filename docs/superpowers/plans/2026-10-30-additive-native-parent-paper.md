# Additive PAPER native parent and observation source implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Assigned worker executes Task 1 without spawning agents.

**Goal:** Integrate the evolved isolated PAPER native execution-parent and position model through additive migrations, then authenticate its venue-generated children without resubmission.

**Architecture:** ROOT b7f6d85 stays the product baseline. Reference-only OP-D d75cc14 contains tested native TWAP/position work but an incompatible changed 0014; port the specific behavior as a new migration and small adapter changes, preserving all current ROOT consumed-window/capital guards. A distinct native execution-parent report/source type preserves real mother/child identity and economics; it never aliases an empty ordinary V2 order report to acceptance.

**Tech Stack:** Existing Python/PAPER/SQL matcher and isolated Docker PostgreSQL qualifier; no account client.

**Spec:** `docs/orders/ADR-ORDER-001.md`, reference architecture report and acceptance ORD-25/29/31/38/43/47/48. All46 targets/56 cases remain required.

## Global Constraints

- All application external writes require independent durable command, one-use permit and current owner. Native children are trusted venue observations, never extra local sends.
- Parent authorization and reserves cover cumulative fills plus every remaining possible exposure. No automatic release/FLAT/protection success from mother ACK/stop.
- Keep PostgreSQL ledger, old schemas/hashes/events, original IDs/UNKNOWN recovery, actual funds and source-role boundaries. No second balance or trading core.
- PAPER only; no LIVE/TESTNET/accounts/paid product model calls/public push/main/archive cleanup or relaxed/deleted/skipped safety cases.
- Frozen ROOT0013 and0014 file bytes remain unchanged. New0026 SIM migration is reserved. Quote worker owns0025 in its own tree; management0022/23 and CASH0024 stay untouched. Any additional business migration number requires parent coordination.
- Never copy whole old OP-D adapter/SDK files over current ROOT. This task does not enable any external Nautilus connector or claim proprietary VP behavior equivalent to a simulator.

## Review Focus

- Healthy nullable position rows must actually update; multiple depth levels cannot reuse the original position capacity.
- Mother command identity/account/product/namespace and every generated child/fill/fee are authenticated; same integer IDs across namespaces never auto-associate.
- Restart/duplicate clock/depth does not generate children twice, consume depth twice or create a child application command.
- Missing/unknown position, conflicting watermark, parent budget tampering and overshoot retain raw original facts and reconciliation duties.
- Creation TTL/permit are enforced for mother submission and after waits, while genuine already-accepted mother/child observations survive TTL without a new permit.

## Task 1: Additive native execution-parent model and trusted child observations

**Files:**

- Create `migrations/0026_paper_native_execution.sql` by porting only evolved D behavior to additive state/functions/constraints; never replace frozen0014.
- Create/port `src/trading_system/engine/paper_native_programs.py`; narrowly update current `paper_market.py`/`paper_market_v2.py` adapter/query points.
- Create `src/trading_system/execution/paper_native_parent_reports.py` with explicit tagged report/verification; reuse existing source/economics/evidence utilities.
- Add genuine integration cases and one mandatory qualifier suite in `tools/ci_paper_postgres.py`. Preserve BASE/V2 tests and their source contracts.
- Commit `docs/orders/OP-D-ADDITIVE-NATIVE-PARENT.md` with exact exported interfaces, migration/recovery notes, verified scope and remaining integration blockers.

**Interfaces:**

- Consume existing `PaperEngine.submit_creation(...)`, original V3 command/financial archive/current consumed guard, market inputs/depth mutex and trusted source-role provisioning.
- Reference-only evolved implementation is Git d75cc14 (functional6133034) in `../binance-opd-matcher`; its genuine34PG result is prior component proof, never this candidate's pass.
- Preserve existing producer/query interfaces for ordinary/list BASE requests. Expose an independently tagged native execution-parent report/source carrying actual parent identity, generation history, complete children/status/fills/fees, original authorization/reserve envelope and trusted provenance.
- ROOT0021 currently accepts exact V1/V2 reports with nonempty original members. Parent/source-knownness for empty-original-member execution algos requires an exact new static extension. Do not turn empty ordinary rows into known acceptance. Either implement the narrow source-backed extension with parent coordination or explicitly expose the source result and leave that integration blocked; never fabricate completion.

- [ ] **Step 1: Baseline and genuine RED.** Read ROOT contracts and the exact D differences, verify frozen0013/14 hashes, then run meaningful baseline checks. Write an actual native TWAP parent test for Spot and USD-M using current funded financial admission, independent mother MAY/consume and real market/clock sources. Observe missing native-parent runtime/source support before implementing; preserve genuine failure evidence.
- [ ] **Step 2: Port additive model.** Add current trusted venue-position snapshots/watermarks and actual fill-tail projection, native parent budget/progress and venue-generated child state. Keep current ROOT owner/cash/window/final-wait checks and per-order policy bounds. No receipt is a fill; no child is an application send command. Apply nullable-key presence and qualified-column fixes from D, and debit actual position capacity between levels.
- [ ] **Step 3: Authenticate observations.** Independent verifier binds actual original mother/request/consumption, namespace/account/product, protected budget and every actual child/source event, fee and market allocation. Validate children before normalization, retain original uncapped native fills and discrepancies, and reject invented/resealed parent/child/position relationships. Empty child inventory can prove accepted mother only through actual authenticated parent evidence, never through absence of ordinary orders.
- [ ] **Step 4: Actual PG and regressions.** Cover duplicate/restart generation; partial/full children; actual finite shared liquidity; full mother plus child remaining exposure; missing/stale/conflicting position; reduce-only both legs/accounts; tampered budget; and original permit wait expiry. Run this fixed-source suite plus affected existing BASE/list/V2-source tests. No real account/testnet or synthetic fill shortcut; ordinary environment skips are not PG passes. Coordinate shared SQL hook signatures with quote worker so0025/26 compose.
- [ ] **Step 5: Reviewable handoff.** Commit focused code with original RED/final log/XML/manifest, source hashes, additive role grants and rollback/recovery instructions. Return exact BASE/HEAD, tested source, exported interfaces, focused diff and remaining registry/CASH/management-stop/VP/external qualifications. Parent dispatches independent review before ROOT integration.
