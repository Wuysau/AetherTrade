# DEV runner implementation checkpoint — 2026-11-02

Task: dev-runner-enforcement. Owner: current Codex chat, one writer.
Base: ba737e0c5b3179a74e325c082757d6acdf037226, local public-snapshot.
Remote checked through GitHub connector: e68ff486b357690ae01a4eff4cb7d586e20f9362,
an ancestor of local base. Direct git network request failed; no remote ref changed.
Worktree: D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/dev-runner-enforcement.
Budget: fourth registered Git checkout, no additional worktrees; zero heavy runs,
zero database/container creation, zero automatic heavy retries.

## Evidence

- User reports runner identity, work write, app write denial, control read denial,
  and Docker engine named-pipe access denial. These are user-provided observations.
- User states TEST is completely undeployed; no TEST credential inventory or
  credential access probe has been performed. Do not equate this with proven absence.
- Agent inspected DEV admin secret metadata and ACL, never secret contents.
  File exists. Inherited Authenticated Users and Users allow entries include read.
  This is an unsafe ACL finding, not a runner-token execution result.
- Current Agent identity remains wuysa, distinct from runner. Agent isolation
  is not qualified by runner checks; do not widen runner directory permissions.
- Baseline scheduler unit checks: 24 passed. No live scheduler/executor exists.

## Next actions in order

1. Administrator reviews/runs scripts/dev/protect-dev-admin-secret.ps1. It backs
   up the single file ACL under protected control, adds explicit runner deny,
   and verifies the rule. It does not repair Agent identity isolation or remove
   broad inherited permissions for all other users. No password is read.
2. Runner runs scripts/dev/Test-DevSecretIsolation.ps1. File-not-found, timeout
   and other errors remain BLOCKED; only UnauthorizedAccess is PASS. This result
   must be paired with the administrator's existing-file verification.
3. Implement a trusted process executor with fixed case catalog and source SHA,
   cross-process queue lock, clean environment and enforced Windows process-tree
   CPU/memory/process/time limits. Keep mutable test output separate from trusted
   queue/lease/evidence archive. Never accept arbitrary commands.
4. Implement privileged fixed-DEV resource broker and persistent instance/OID/
   role/lease ownership checks; never expose cluster admin credentials to tests.
   Role-sensitive tests must use registered exclusive roles and run serially.
5. Deploy under independent identities and prove denial/budget/crash behavior.
   Only then schedule 0030/0031 disposable-DB cohorts and subsequent integration.

Overall BLOCKED; scripts prepared, administrator deployment and runner probe
NOT_RUN by Agent. Executor, resource broker, live budget enforcement, migration
and OP-G all NOT_RUN / incomplete. No integrated acceptance claim.

Validation: both PowerShell files parsed without errors; actual wrong-runner and non-admin invocations each exited 1 at their identity guards before file access. git diff --check passed.
