# Scoped programme target implementation plan

> **For agentic workers:** Execute inline in the already assigned sole-writer CASH worktree; ROOT owns runtime physical-scope authentication. Preserve full46/56 ACTIVE scope.

**Goal:** Explicitly separate scalar quantity reservations from the full financial book without changing historical facts or money holds.

**Architecture:** Parent/ROOT approved a NEW tagged `ProgramScopedTargetFactsV2(ProgramTargetFacts)` in `intents/program_scoped_target.py`, with the existing fields plus version/kind and explicit quantity reservation IDs. The full financial book is validated and hashed unchanged; only the existing TargetCompiler receives a subset projection. Pure subset declarations grant no authority. ROOT separately authenticates the exact IDs using physical scope SQL and independently bound external snapshot IDs at reserve/current boundaries.

**Tech Stack:** Existing dataclasses/static decoder, same TargetCompiler/risk/archive, exact Decimal and isolated PostgreSQL.

**Spec:** Approved ADR-ORDER-001/005, parent and ROOT explicit scoped-facts ruling in this task, and 2026-10-30-program-accounting.md.

## Constraints and review

- Original ProgramTargetFacts fields/defaults/serialized bytes/replay stay unchanged; unknown/partial tags deny. No public20 schema changes.
- New exact V2 declares unique existing reservation IDs; amounts/assets never infer quantity. Missing local quantity and forged subsets require runtime SQL rejection, not guessed pure authority.
- Full balances/group holds/book hash and same target equation remain; no dropped financial reservation or second calculator.
- No cash/book/owned module overwrite, new funds, native release/FLAT/protection/history claims or operational trading.

## One reviewable typed/compiler slice

Files: new `intents/program_scoped_target.py`; minimal `program_target_allocation.py` compiler call; annotation unions in `program_financial_records.py` and `risk/program_assessment.py`; focused tests in `tests/unit/test_program_scoped_target.py`.

- [ ] Write and run RED: exact scoped type absent; full financial hash/foreign money hold retained with explicit empty quantity set, actual mapped local pending uses the same math, omitted mapped/duplicate/unknown IDs deny, unknown/partial tags fail decode, V1 byte hashes/replay unchanged and full assessment restores V2.
- [ ] Implement exact tagged dataclass and `program_target_quantity_book(book,facts)` helper; V1 returns original object, exactV2 projects only reservations. No runtime authority inferred.
- [ ] Run focused new/old target and archive/risk regressions; inspect complete results, commit slice and send ROOT exact API before serial integration.
- [ ] ROOT provides authenticated runtime/accepted-unfilled path; import coordinated dependencies and execute genuine A+B source/financial posting and V2 financial chain in fresh fixed-source PostgreSQL. Preserve all prerequisite failures and duties.
