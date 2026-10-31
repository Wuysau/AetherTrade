# AetherTrade Agent instructions

Read docs/harness/HARNESS_OVERVIEW.md and its environment, testing, worktree and lifecycle policies before acting. These are the user-approved 2026-10-31 engineering governance requirements.

- DEV = public-snapshot; TEST = dev. All automated tests share DEV. Branch updates do not deploy TEST; user approval is required for a specific TEST deployment.
- New task worktrees start from latest dev. One writer per task; only Integration Manager merges dev, serially. Agents submit commits and task-scoped light evidence.
- Default to relevant unit/static/compile/contract checks. No Agent Docker startup, container/database provisioning, full integration suite or automatic heavy retry. Heavy checks require the unified scheduler; full acceptance follows all task integrations.
- Preserve user edits, ignored files and historical evidence. Auto cleanup requires merged ancestry, light/quick PASS, archived evidence and no unsaved files or active processes. Non-force worktree remove and branch -d only; verify physical removal. Never bulk-delete existing worktrees.
- No global Docker cleanup, VHDX operations, cross-drive migration or unknown-resource deletion. No TEST data access, real trading accounts, TESTNET/LIVE enablement or automatic paid model calls.
- Docker recovery and isolation are not yet qualified. Until ready, only code edits and safe tests that need no Docker; old container-creating qualification launchers must not run.
- Record NOT_RUN, BLOCKED, FAIL and PASS honestly. Light success is not full acceptance or venue qualification. Preserve approved trading contracts, safety gates, original command hashes and UNKNOWN recovery.

Infrastructure enforcement remains pending as of 2026-10-31; policy text is not evidence that automation or OS isolation exists. Update this implementation status only with actual evidence.
