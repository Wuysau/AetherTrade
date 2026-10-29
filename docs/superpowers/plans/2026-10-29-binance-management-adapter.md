# Binance management request adapter implementation plan

> **For agentic workers:** Use superpowers:executing-plans for this bounded slice; obtain an independent review before integration.

**Goal:** Carry the approved owned-order management compilation to an exact unsigned adapter request and recover original identities without inventing terminal evidence.

**Architecture:** Reuse `ManagementCompilation`, its compiler, trusted product/rule/capability snapshots and the existing execution owner. Recompile against independently supplied current facts before materializing; do not add a signer, transport, financial ledger or write permission. Query collection matching remains read-only and requires complete, scoped, archived pages; no matching row is not proof of NOT_SENT.

**Tech Stack:** Python dataclasses, existing canonical codec and pytest.

**Spec:** `qualification/binance_orders/reference/CODEX_HANDOFF.md`, architecture sections 7/10/14, `docs/orders/ADR-ORDER-001.md`.

## Global constraints

- Every application external write needs its own durable command, permit and owner verification. This unsigned adapter grants none.
- Venue-generated children are observations, never resubmitted.
- Original schemas, command hashes and UNKNOWN identities retain their semantics.
- Nautilus candidate remains exactly 1.231.0 / commit 27a8e54e7ac3c57d6cbf8891f0283dfbaee97317; no latest-document implementation claims.
- PAPER/RESEARCH only; TESTNET/LIVE, account access and paid model calls disabled.
- Only this worktree is written. Root integrator owns shared schemas and migrations.

## Review focus

Product/service endpoint substitution; caller-mutated compilation or bool/integer equality; missing current owned facts; ambiguous original IDs across pages/accounts/services; incomplete or contradictory query collections.

### Task 1: Exact unsigned management requests

Create `src/trading_system/execution/management_requests.py` and `tests/intents/test_management_requests.py`.

Interface: `materialize_management(plan, *, current_target, rules, capabilities, profile, now, scope, expected_owner_strategy_id, expected_ownership_epoch, expected_execution_epoch, expected_policy_revision, expected_build_digest, expected_rule_revision, expected_capabilities_hash)` returns a tuple of typed `VenueManagementRequest`. It recompiles using these independent current facts and requires canonical equality with the complete supplied plan. Each result preserves the original command/target/program/plan hashes, namespace, HTTP method, endpoint and every parameter; `execution_authorized` and `funds_released` are always false.

- [ ] Write RED cases for all seven namespaces, query UNKNOWN original IDs, cancel/keep-priority/modify/cancel-replace field retention, product/route/identity/hash/current-fact substitutions and no authority.
- [ ] Observe failures, implement the adapter, then run new plus existing management regressions.
- [ ] Commit reviewed scope; do not project V2 management writes into V3 creation commands.

### Task 2: Original-identity collection recovery

Add a typed query-page/evidence interface and exact original-ID selection in `src/trading_system/engine/nautilus/order_query.py`; test in `tests/unit/test_order_query.py`. This interface consumes unsigned QUERY requests only. Independently provided scope/source/evidence/request hash and complete page sequence are mandatory. Matching uses original IDs and symbol, not price/side similarity. Contradictory or duplicate identities, missing pages and incomplete scans remain reconciliation-required. Preserve raw evidence references. Results grant neither ownership nor terminal/collateral authority.

- [ ] Write and observe RED for matching exact original mother, unrelated rows, cross-scope/service replies, page gaps, repeated/conflicting rows, absent/not-found and write-response misuse.
- [ ] Implement read-only recovery and run both new suites plus existing native-event/management tests.
- [ ] Record actual commands/results, native and full ORD qualification gaps, then request fresh read-only review and integrate through the root owner.

## Evidence and rulings

Base8555201: existing management and exact Nautilus target guard tests **36 passed**, exit0. Git long-path checkout failed before worktree registration; retry with command-local `core.longpaths=true` succeeded. No global Git settings changed.

Official Binance Spot, USD-M and Equity catalog pages reread on2026-10-29. Older product URL shapes are unavailable; current catalog and `/en/docs/llms.txt` confirm separate ordinary/list/conditional/execution-algorithm/equity services. Download-schema links returned empty bodies and are not implementation evidence. Keep the exact locked Nautilus build boundary unchanged.

Ruling: the user has approved the architecture and explicitly instructed continuing downstream implementation; no routine stage reapproval is required. This plan documents a bounded OP-E slice, not completion of OP-E or promotion of the56 acceptance cases.
