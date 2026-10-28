# Current task

## ACTIVE — resumed 2026-10-28

用户已明确要求“继续执行任务”，撤销先前暂停。已核对 Git：实施分支 HEAD
`dd4fda4`，六份 WIP 原位保留。两项 P1 分别在独立的
`paper-dispatch-account-risk` / `paper-protected-reduction` worktree 处理；
`paper-runtime-resume` 继续完成独立 owner 与证据解锁。root 负责原 WIP 测试、
集成与最终验证。先复现失败，再最小修复；TESTNET/LIVE 门禁不变。

暂停交接仍是恢复基线，以下暂停文字仅作历史，不再阻止本轮已授权工作。

### Historical pause checkpoint

用户要求暂停所有任务并记录。**不得自动继续实施或测试。** 当前实施工作区是
`D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/paper-execution`，
分支 `codex/unified-paper-execution`，暂停前代码 HEAD `fcdc5ce`。

最新、优先采用的完整记录是 [HANDOFF-2026-10-28.md](HANDOFF-2026-10-28.md)，
包括六份未提交修改、待集成 `9f0b1cb` / `1b5e3bd` / `96bb6fa`、原始证据位置、
最近 6 passed / 2 failed 的 HYBRID 测试结果，以及两个尚未修复的 P1：
发送前跨标的全账户风险重估缺失、健康保护单阻断合法减仓。

最终完整集成门禁、真实终端/网页组合演示及远端 CI 均 NOT_RUN。
TESTNET/LIVE 继续禁止，不能把局部测试通过称为完整任务已完成。

收尾核对：无活动子代理或 Python 测试进程；未找到本次 Vite preview；Docker
引擎停止，未启动或清理任何资源。只保存文档及忽略目录内的 WIP/日志副本。

**以下为暂停前阶段性历史，含旧的“下一步”表述；不能据此自行恢复。**

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

## Progress after recovery

- Atomic target branch integrated as `4aa1323`/`40572be`; owner-CAS branch
  integrated through `3302354`. Deferred-expiry counterexample retained in
  `2920641`: actual PostgreSQL 133 passed / 1 failed, then `66bd023` restricts
  caller DDL/TEMP and the same PostgreSQL suite passes 134 / 0 skipped.
- Integrated PostgreSQL run at `66bd023` proved ledger 30, reservations 67,
  duplicate targets 29, risk locks 36, dispatch 61, execution evidence 37,
  recovery 94, protection 74, ownership 134, PaperEngine 59 passed. It is NOT a
  green full gate: ETH revision/child-book fixture conflicts fixed in `b50a6c4`
  require rerun; existing console test DB caused the runner to refuse reuse.
- `97c4f9d` persists console DAILY_LOSS after rollback, rebound, restart and
  risk-day rollover, using shared cause policies. SQLite 24 passed / 1 skipped;
  PostgreSQL regression awaits the fresh-cluster run.
- User supplied prior chat `01a167ee-3f84-7483-8767-3a0dd532c8d4`. Its validated
  console/multimodel/live-market work was merged to public-snapshot, old
  runtime-console worktree removed, ending at DNS fix `3062154`. All included
  in our original baseline; reuse those records rather than rebuild features.
- Web distinguishes queued/submitted/unknown receipts from fills and shows
  target/in-flight/actual/protection/locks. Typecheck/build pass; 103 tests pass.
  Desktop 1440 and mobile 390 rendered with explicitly labeled synthetic UI
  fixture, no document horizontal overflow. This is UI evidence only.
- Separate worktrees now implement remaining backend wiring, fresh validation,
  and learning docs (`paper-runtime-resume`, `paper-validation-resume`,
  `paper-learning-resume`), each with one writer. Integrate reviewed commits
  before final combined qualification; no real account or venue accessed.
- Foundation followup: integrated restricted LOGIN owner probes and safe Docker
  launcher (`14f9ca4`, `c1cfc41`, `4f22d25`); full independent Linux run at
  `4637925` had 2402 passed / 363 skipped (unconfigured optional suites, NOT DB
  acceptance). Mandatory PostgreSQL gate had 686 passed / 1 failed, no skips;
  only child replan fixture retained hardcoded sequence 2 after its initial
  book moved to 2. Minimal fix `afcd882` preserves the safety assertion, fresh
  targeted child suite 49 passed / 0 skipped. Final integrated gate still due.
- Actual PostgreSQL console loss latch and 139 owner cases (including five
  distinct restricted LOGIN probes) passed. Raw logs, images/dependencies,
  migration hashes and 13 synthetic final SQL snapshots are retained by
  `8cbb358` under `docs/evidence/2026-10-27-unified-paper-validation/`.
- Learning documents and explicit NOT_RUN acceptance matrix integrated at
  `4f04f5e`. Web all-mode configuration/projection: 105 tests pass, typecheck
  and build pass; `c472c46` retains raw red/green and responsive fixture images.
- Runtime agent reports first actual PostgreSQL common-service cycle passes:
  PENDING -> SUBMITTED -> 40% partial -> durable protection duty -> restart
  original-ID query -> final fill. This remains **not yet integrated or final
  acceptance**. Remaining cancellation, protection/exit, full three-mode,
  process-kill and final gate evidence must still be completed.
- Early independent foundation review active, read-only. Known integration
  concern: automation catches DAILY_LOSS inside a savepoint; runtime work must
  persist that cause outside the failed savepoint before claiming shared locks.

Next smallest step: integrate the common runtime service, connect all modes and
complete actual PostgreSQL lifecycle/fault evidence, then final review.

Ruling: continue the already authorized plan without new design approval.
After initial integration, use dispatching-parallel-agents for independent
runtime, validation and documentation work in separate worktrees. Record fresh
test outputs and update this file after each meaningful implementation step.
