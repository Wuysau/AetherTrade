# Current task

## Objective

Resume `AetherTrade_Next_Engineering_Task_2026-10-27.md`: unified PAPER execution
and genuine PostgreSQL acceptance, preserving native/Testnet/live gates.

## Resume checkpoint (2026-10-27)

- Active implementation checkout: `TradingHandoff/AutoDispatchWorktrees/paper-execution`,
  branch `codex/unified-paper-execution`, resume HEAD `be59e40`.
- User's open checkout `AetherTrade` remains at `3062154`, clean, and is not the
  implementation checkout. No applicable AGENTS.md found in these checkouts.
- Existing plan: `docs/superpowers/plans/2026-10-27-unified-paper.md`.
- P0 failures reproduced and corrected; historical raw evidence is committed.
- Integrated: lock action verifier, group daily-loss applicability, ledger abort
  guard, isolated CI runner, restricted durable PaperEngine, stock precision fixes.
- Not integrated: `codex/paper-atomic-intake` (HEAD `1393ec7`) and
  `codex/paper-owner-cas` (HEAD `808ba9a`). The latter has an uncommitted
  late-deferred-expiry regression; leave its files untouched and investigate in
  this checkout before integration.
- Preserve untracked `evidence/paper/2026-10-27-stock-precision/` from prior work.
- Existing plan checkboxes/ignored ledger are stale relative to Git commits.

## Remaining work

1. Integrate atomic targets and verify ownership CAS final-commit safety.
2. Persist console daily-loss locks and connect manual/AI/RULE/HYBRID through
   a single authorized, risk-checked PostgreSQL command service and owner.
3. Project synthetic events, fills, protections, unknown reservations and exits;
   distinguish and explicitly migrate legacy console state.
4. Web/CLI projections and minimal end-to-end demonstration.
5. Real isolated PostgreSQL concurrency/process-fault acceptance, full Python/web
   checks, lifecycle/playbook/design documents and final review.

## Validation and boundaries

New post-resume verification: NOT_RUN. Prior reports are historical evidence,
not proof for a newly integrated tree. Remote CI for this branch: NOT_RUN.
Use fresh dedicated test databases in the existing isolated test clusters or
a newly provisioned disposable cluster. Never read/alter real credentials.
No push, public merge, venue Testnet or LIVE writing is authorized by this task.

Next smallest step: integrate the reviewed atomic-target commits and run their
PostgreSQL regressions; reproduce the preserved owner-CAS counterexample.

Ruling: continue the already authorized plan inline; do not repeat design
approval or task dispatch. Record fresh test outputs and update this file after
each meaningful implementation step.
