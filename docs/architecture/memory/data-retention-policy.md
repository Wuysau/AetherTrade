# Strategy Memory Data Retention Policy

Version: 1.0 · 2026-11-04
Owner: AetherTrade operations / data governance
Status: proposed; no automatic deletion is implemented.

## Principles

1. Events are the audit source of truth; projections are rebuildable.
2. Do not delete an event while any decision checkpoint, plan or knowledge item
   references it.
3. Never store API keys, OAuth tokens, signatures or account credentials in
   memory events, checkpoints, prompts or knowledge.
4. Retention changes require explicit approval and a backup/restore drill.
5. This policy does not authorize Docker, VHDX or cross-drive operations.

## Default retention

| Data | Default retention | Notes |
| --- | --- | --- |
| `console_strategy_memory_events` | Indefinite / 7 years | Append-only audit; deletion requires legal/operational approval and verified backup |
| `console_strategy_memory_state` | Current + 7 years of snapshots if archived | Rebuildable from events; current row always retained |
| `console_market_theses` | 7 years | Includes INVALIDATED/EXPIRED/SUPERSEDED history |
| `console_strategy_plans` | 7 years | Includes PROPOSED/REJECTED/REVISED history |
| `console_decision_checkpoints` | 2 years minimum | Keep longer if referenced by a plan/decision audit; contains actual model input references and hashes |
| `console_memory_knowledge` | Expiry + 2 years | VALIDATED/REJECTED/EXPIRED history retained |
| `console_prompt_templates` | Indefinite | Old versions must remain readable for replay |
| `console_model_runs` | 1 year minimum | Existing model journal; may contain exact request bodies; no secrets |

## Sensitive data

- `console_model_runs.requests[].body` and `console_decision_checkpoints.data`
  must never contain provider credentials, cookies, OAuth tokens, signatures or
  account secrets. Model service checks this before persistence.
- Market/account snapshots are PAPER-only in this repository. Do not enable
  TESTNET/LIVE or real accounts under this policy.
- Access is limited to the authenticated local console user and approved
  operators.

## Deletion procedure (future)

1. Confirm no legal hold and no referenced checkpoint/plan/knowledge.
2. Export the affected scope to approved encrypted storage.
3. Take and verify a full backup.
4. Delete only through an approved, reviewed script; never ad-hoc SQL.
5. Rebuild projections and verify hashes.
6. Record the deletion in the acceptance/audit log.

## Current implementation status

- No automatic retention job is implemented.
- No automatic event/projection deletion is implemented.
- No cleanup is performed by Agents.
- PostgreSQL backup/restore evidence is **BLOCKED** in this environment.
