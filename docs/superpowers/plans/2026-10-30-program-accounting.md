# Same-ledger programme accounting implementation plan

> **For agentic workers:** Execute inline with TDD and independent review; preserve exact source-bound evidence and the complete parent goal.

**Goal:** Post authenticated native programme fill facts and justified asset/position effects atomically in the existing PostgreSQL ledger, without inventing financial coverage.

**Architecture:** Reuse EvidenceIngestor, FillLedger, EventStore, existing audit/cashflow/position/book tables and the collateral UoW. Verify/archive outside the business transaction. Inside it, reload original programme, protected consumption, actual owner/book/unit and independent accounting facts. Immutable per-fill asset postings are source evidence, not a second balance total; spendable readers must authenticate them against committed fills/raw sources. No automatic reserve release, FLAT/history/protection claim or fake legacy intent.

**Tech Stack:** Existing Python typed contracts, exact Decimal, real isolated Linux/PostgreSQL and existing mandatory runner.

**Spec:** Approved ADR-ORDER-001 and order architecture; integration_next accounting delegation/ruling recorded in current-task.md. Main/archive untouched, PAPER only, no paid/real venue/account calls.

## Task 1: One actual transaction for existing source/fill records

- Add EvidenceIngestor.prepare(event) to read/check durable bytes before UoW, plus ingest_in_tx(tx, prepared) that rechecks captured byte hash and metadata without file/network IO.
- Extract FillLedger.record_in_tx(tx, fill, correction_of=None) preserving every original validation, duplicate/native identity/unit/correction rule, EventStore/outbox/FillNotice and caught-error transaction abort. Public methods retain behavior and return only after commit.
- Write real PG REDs: same-physical-transaction evidence+fill/event/fee/outbox/notice, caller exception rollback, caught validation rollback and exact duplicate replay. No mock DB or fabricated fill safety proof.
- Run new fixed-source PG and the full existing execution-evidence suite; retain real RED/GREEN/log/XML.

## Task 2: Authenticated per-fill economics and asset postings

Root clarified the mandatory source boundary: reserve additive business0016
`program_fill_source_approval.sql` in THIS tree for serial review. Dedicated
NOLOGIN source-verifier role, no automatic memberships/control/closure grants,
narrow fixed-path SECDEF approval only. Actual verifier runs before a protected
approval transaction; it commits before owner posting transaction. Approval binds
exact original/consumed context, accepted/all-rival/raw hashes, fill/unit/economics/
fees/discrepancies and proof refs, never balances or order authority. Owner cannot
mint approval/credit from audit JSON. Unknown economics can approve raw source
only. Balance readers enumerate and rederive committed exact approvals once.

- New execution/program_accounting.py exposes ProgramPaperAccountingService(...).apply(command_id) only; actual verifier reads/fyncs before business UoW. Trusted current accounting reader defaults deny unresolved economics.
- Recheck original/protected/owner/book/unit and normalized source link in the same collateral transaction. Persist valid native fills even if economic coverage is unresolved, with explicit risk locks/duties. Never cap raw overshoots or turn unknown fees into zero.
- Derive per-asset principal/fee deltas from original native quantity/venue price, FinancialUnit ratio and both multipliers, source fee debit/position effects and actual owned allocation. Preserve asset names/location; no implicit FX.
- Use immutable versioned asset posting rows in audit_events plus existing BookSnapshot balances/ManagedPosition. Cashflow rows retain FEE/FUNDING/etc meanings; trade principal is not EXTERNAL or an invented cross-currency INTERNAL pair. Prevent fee double count.
- USD-M has no Spot principal debit; PnL/cost/margin/funding need actual typed evidence, otherwise raw facts + unresolved economics/locks. Equity CARD/MAIN needs independently bound location evidence.
- Real PG scenarios cover partial/additional fills, replay/restart/concurrency/commit failure, fees in base/quote/third asset, unit/multiplier, reductions, overfills, missing cost/owner facts and raw mismatches.

## Task 3: Shared balance readers and mixed-history safety

- Any spendable credit reader verifies posting against actual committed fill, native bridge, raw evidence and protected original command/consumption. Raw owner audit JSON alone cannot create balance credit.
- Existing legacy _cash joins only legacy commands and hardcodes Spot notional/fee. Add the minimal shared authenticated generic contribution or fail closed preserving the existing book and an explicit reconciliation status; never refresh a legacy-only number over generic effects.
- Keep exact legacy-only behavior where no generic source exists. Real PG tests mix both families and prove source tampering cannot mint credit, no duplicate totals, no stale refresh overwrite.
- Independent review and exact byte-verified evidence before serial integration. Full46/56 and advanced contracts remain active; these tasks cannot certify native qualification.

## Actual foundation evidence

Task1 fixed1faf83c actual4 missing-API PG REDs preceded7d5f4e9 implementation.
The new4 atomic PG tests and original37 execution-evidence PG checks passed with
zero skips. Scope-before-archive-IO unit caught a real extraction ordering error
and passed after correction. No asset posting/cash/position/credit approval is
implemented by this foundation. Real source/role/economic tests remain required.
