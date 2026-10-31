# OP-C native parent funding closure — 2026-10-29

This change implements a reviewed reservation reduction path. It does not submit orders, grant permits, resolve risk locks, or qualify exchange behavior.

## Implemented boundary

`OrderProgramClosureService.close(scope, program_id, remaining_holds)` uses the existing ledger UnitOfWork and collateral-group mutex. Defaults deny: both a separately provisioned resolver UoW and a trusted local evidence verifier are required. The verifier returns a typed `ClosureReview` bound to the hash of the full current context, with evidence for every obligation; a boolean, owner audit row, or arbitrary authorization string cannot substitute.

The context includes the immutable parent authority, latest parent/child state, owner/epoch/policy, exact current book, ledger fills/fees, retained protection plans and all shared reservation before/after projections. Parent STOPPED/COMPLETED alone is insufficient: no possible future child quantity, every child terminal, owned native child linkage, exact ledger fill/fee match, no unresolved fill corrections, complete post-terminal balances, reconciled positions and no applicable active risk locks are required. Independent review must additionally prove venue enumeration completeness and external evidence authenticity; local absence of a child is not proof that no child exists.

A nonflat position remains blocked unless its current owned protection plan is verified and the independent review explicitly records `RESPONSIBILITY_RETAINED`. Closure does not delete that plan or position, transfer ownership, or discharge management duties. Flat cases require `FLAT_RECONCILED`.

Migration 0009 creates NOLOGIN `ledger_order_closure_resolver`, with no automatic role membership. Its definer function creates append-only approvals bound to the exact projection, PostgreSQL transaction ID, application transaction ID, latest state/book/owner/protection/fill facts and a review validity of at most 60 seconds. The existing reservation audit table has an INSERT guard: even direct ordinary-owner SQL cannot lower a linked hold without that approval. Approval reuse after commit, changed context, changed reservation revision, or expiry fails. Funding links and reservation creation rows remain immutable. Full and partial reductions are supported; further independent reviews may release remaining holds.

`ReservationRepository.save` gates linked financial credit as well as nominal held reductions. The SQL INSERT guard enforces the same boundary against direct ordinary-owner statements. Effective debit is `max(held-reflected,release_pending)`; lowering it, increasing reflected credit or refreshing a credit-bearing book watermark requires independent same-transaction approval. A public snapshot hash proves identity, never authority. Unlinked reservations retain previous behavior. Released balance credit is tied to the reviewed book watermark; a new book revision conservatively restores the hold until independently reconciled.

The ordinary IP-07 reconciliation/attestation callback cannot mint an independent
database approval for linked native funding. Active native-parent reflection and
renewed released credit therefore remain default-denied until a separately
trusted approval path is implemented. The existing closure resolver authorizes
reviewed terminal reductions only; this correction does not pretend that an
ordinary callback or a matching watermark supplies that authority.

The final guard correction is additive migration0011;0009 keeps its original
bytes. The upgrade refuses existing unapproved linked credit without rewriting
it and accepts exact archived reviewed credit. Apply with writers quiesced and
follow [the migration/recovery notes](OP-C-FINANCIAL-CREDIT-CORRECTION.md) on
refusal. This does not qualify native reflection or enable an execution adapter.

## Verification performed

Actual disposable Linux/PostgreSQL checks, with the worktree mounted read-only and writable test evidence mounted separately:

- **28 passed**, zero failed/skipped: `tests/unit/test_order_closure_review.py` and `tests/integration/test_order_program_closure.py`.
- **57 passed**, zero failed/skipped: `tests/integration/test_order_programs.py`, `tests/contracts/test_order_lifecycle.py`, `tests/contracts/test_order_program_v2.py` in a separate fresh database.
- Scope includes full/partial/repeated reductions, residual protection positive and negative paths, missing ledger fills, owner audit forgery, denied role escalation/function access, cross-transaction replay, state/book/owner/reservation staleness, same-transaction changed context, expiry before save, unknown/open child rejection, active risk rejection, exact snapshot balance credit, and ordinary reservation compatibility.
- The first unit collection failed because the new module did not yet exist. The first DB run exposed a JSONB operator precedence error (2 failures / 15 passes); it was fixed. Initial failure output and final JUnit/raw summaries are retained under `docs/evidence/binance-orders/op-c-closure/`.

Commands executed inside `python:3.12-slim`, connecting only to the task's disposable `postgres:17-alpine` container:

```text
python -m pytest -p no:cacheprovider tests/unit/test_order_closure_review.py tests/integration/test_order_program_closure.py -q --tb=short --junitxml=/evidence/closure-final.xml
python -m pytest -p no:cacheprovider tests/integration/test_order_programs.py tests/contracts/test_order_lifecycle.py tests/contracts/test_order_program_v2.py -q --tb=short --junitxml=/evidence/closure-regression.xml
```

Both exited 0. The closure suite uses `IP03_ISOLATED_DATABASE=YES` and a fresh `ip03_test_*` database, installs existing 0001/roles/0004/0008 fixtures followed by 0009, and requires Linux. Exact runtime versions/image identities are in `environment.txt`. Test administrator authority is used only for synthetic fixture provisioning and adversarial mutation tests. The independent test archive is inaccessible to the ordinary runtime role.

## Remaining qualification boundary

Production evidence archive verification, real account balances/fills/protection, platform closure semantics, crash injection and the full combined repository gate are **NOT_RUN here**. A deployment must deliberately provision the resolver identity and a real local verifier that substantiates every required obligation from authentic archived records. No runtime role receives resolver membership from this migration; no production verifier is registered automatically.

TESTNET and LIVE remain disabled; PAPER/RESEARCH scope checks remain. No native client, account API, paid model, operational configuration or public order schema was modified. The parent integration task will register this suite in the full fixed-commit PostgreSQL gate.
