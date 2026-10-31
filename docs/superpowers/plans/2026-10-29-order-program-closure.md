# Native parent funding closure Implementation Plan

> Execute inline with superpowers:executing-plans. No subagents; parent reviews integration.

**Goal:** Reviewed terminal parent/child reconciliation can reduce linked reservations without granting ordinary runtime resolution authority.
**Architecture:** Existing collateral UoW owns all transitions. A dedicated NOLOGIN closure resolver mints exact, transaction-bound approvals; a database audit insert trigger protects the existing reservation projection path from direct SQL bypass. A trusted local verifier returns structured evidence tied to the complete closure context; defaults deny.
**Spec:** docs/orders/ADR-ORDER-001.md and delegated OP-C closure requirements.
**Constraints:** PAPER/RESEARCH only; no public schema/dispatch/ingress edits; immutable funding links; no new cash totals.

## Review focus

- Owner-role audit forgery and attempts to execute resolver function must fail in PostgreSQL.
- Approval replay after commit or state/book/reservation revision change must fail.
- Parent STOPPED with open/unknown children, missing fill accounting or active risk must deny.
- Residual positions require an explicit independently verified retained protection responsibility.
- Ordinary reservations preserve existing save behavior.

## Steps

- [x] Add failing pure proof/context tests and isolated PostgreSQL fixture/tests.
- [x] Implement risk/order_program_closure.py context validation, typed proof review and atomic release.
- [x] Add migration0009 resolver role, exact transactional approval function and audit trigger.
- [x] Extend only linked reduction guard in ReservationRepository.save to consult approvals.
- [x] Execute isolated PostgreSQL role/forgery/replay/stale/terminal/protection tests; run existing OP-B regression.
- [x] Record actual evidence/limitations, commit own files and report to parent.
