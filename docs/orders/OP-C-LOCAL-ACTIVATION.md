# Internal local programme activation

Approved implementation boundary: reuse the actual SAME-transaction financial
bundle and V3 ParentAuthority/ParentBinding/reducers. Local TWAP/VP has no native
mother command/client/venue ID. Its single SUM envelope and existing funding
remain durable; each later external child still needs its own fresh command and
permit. No control grant, second cash ledger or transport is introduced.

First extract a private in-process witness issued only after the real shared
financial writer succeeds, plus one shared attachment claim. An owner-authored
audit row or provider True cannot mint that witness. The same active transaction,
bounded intake, current owner/fence, exact stored bundle and original holds are
mandatory. EXTERNAL_REQUESTS and LOCAL_ACTIVATION are mutually exclusive; the
whole command-ID set comes from the actual admission witness, including the
zero-asset case. The outer service still verifies complete actual attachment.

Then derive local authority/binding/proofs from that verified source and persist
typed activation/state in existing append-only audit_events and order_programs,
linking actual existing funds once. Restart reads restore the original source
without renewing authority. Active, expired and unknown sources retain their
quantity/hold/duty obligations; unproven terminal labels cannot release them.
Exact-scope target and collateral-group UNKNOWN readers must include the neutral
internal source without inventing external command references.

## Shared witness and attachment cohort

The actual financial writer now registers a private live witness only after
current source/auth/risk/quantity/qualification/action/capacity checks and the
whole financial bundle have succeeded. It binds the active transaction, scope,
stored bundle hash, unchanged original book, full preassigned command-ID tuple
and original expiry. No witness is restored from audit JSON after commit.

`program_bundle_command_ids(tx,bundle)` returns that actual whole tuple, including
zero-asset admissions. `claim_program_attachment(tx,bundle,mode,command_ids)`
checks exact stored source/intent/current owner/fence/initial holds. External
request appenders pass the WHOLE tuple; repeated same-transaction claims are
idempotent. Subsets, other IDs or conflicting local/external modes abort the
transaction. This is an attachment plan, never proof that all requests already
exist; the outer service still checks complete actual attachment before commit.

Actual focused tests pass2 unit cases and3 isolated PostgreSQL cases; existing
same-kernel financial PostgreSQL regression passes12. No failures/skips. The
PostgreSQL probes use the real kernel, show historical owner audit cannot reopen
a witness in a different transaction, and prove bad attachment rolls back the
whole financial bundle. Evidence is in
`docs/evidence/binance-orders/op-c-program-attachment/`. There is no new table,
role, control grant, command, permit or transport in this cohort.
