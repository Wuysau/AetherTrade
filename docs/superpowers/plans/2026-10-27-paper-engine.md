# Controlled PAPER Engine Implementation Plan

> **For agentic workers:** Execute inline with verification before completion. This is the independently assigned PAPER engine subtask of the approved engineering task.

**Goal:** Provide durable, controlled simulated venue behavior through EnginePort without enabling any native exchange path.

**Architecture:** The dispatcher remains responsible for committing MAY_HAVE_SENT before calling EnginePort. PaperEngine independently validates the immutable command, permit and server-held authority, and stores its own synthetic venue state in PostgreSQL. Lifecycle changes require explicit synthetic events; query preserves exact scope and original client identity.

**Tech Stack:** Python, psycopg, PostgreSQL, pytest; Docker Linux for database tests.

**Spec:** `D:/Projects/Python/AetherTrade/TradingHandoff/AetherTrade_Next_Engineering_Task_2026-10-27.md`, approved trading architecture INV-01/02/04/05/06/08/11/14.

## Global Constraints

- One writer in this worktree; no server/store, dispatcher/risk, or migration 0001/0002 edits.
- PAPER only; no account access, testnet, native startup, invented fills/protection, or automatic replay.
- Keep exact concrete Nautilus composition checks and native gates.

## Review Focus

- Forged or stale permit, authority, scope, policy and owner epoch must fail before side effect.
- Lost acknowledgement and process restart must query the original client ID without replay.
- Partial fills and cancel unknown retain the actual filled and remaining quantities.
- Duplicate event IDs are idempotent only for the identical event; invalid transitions fail.
- Missing database or unsupported balances/positions/protection coverage cannot unlock recovery.

## Task 1: Engine, durable simulation and composition

- [x] Add failing real-PostgreSQL engine tests and establish the preimplementation failure.
- [x] Extract unchanged shared authority validation from Nautilus bridge.
- [x] Add PAPER-only additive migration 0003 and engine with acknowledgement-only submission, cancel/amend request tracking, synthetic lifecycle events and scoped query.
- [x] Permit only exact PaperEngine in PAPER owner composition; retain existing concrete Nautilus path.
- [x] Run Linux PostgreSQL lifecycle, replay, concurrency and authority tests plus existing native/owner regressions.
- [x] Record commands, database migration, test output and limitations, then commit the focused change.
