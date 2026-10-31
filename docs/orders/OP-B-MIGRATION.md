# OP-B database migration and recovery

Versioned records use `2026-10-28`; the original four public/packaged
`2026-10-20` schemas and all existing ledger payload/hash semantics are unchanged.
The new VersionedOrderCodec dispatches explicitly by version and kind. It never
upgrades UNKNOWN or guesses a revision from payload fields.

Apply `migrations/0008_order_program_records.sql` once as migration administrator
after0001/roles and the existing migrations. New isolated `paper-provision` runs
include it; existing operational profiles are never migrated automatically.
Take a database backup and record schema/source hashes before an authorized
upgrade. Verify the migration on an isolated copy with pending/unknown records.

It adds append-only order programs, parent authorities/states, trusted source
observations, per-parent observation dispositions, funding/child/trade links,
reconciliation cases, and v2 application command records. They share the existing
collateral mutex, owner lookup, active reservation projections and risk locks.
No second cash/position ledger is introduced. Actual Fill/Position settlement
through EventStore remains an OP-C/E integration requirement; OP-B records source
facts and obligations, never manufactures a financial settlement.

Runtime role has SELECT/INSERT only on new tables. Mutations/deletes/truncation,
unknown origin and invalid command transitions are blocked. Runtime cannot run
DDL. Test evidence covers these protections under the actual restricted role.

Recovery:
1. Read original command/program IDs and versions. Load old commands with old
   codec, including MAY_HAVE_SENT/UNKNOWN; query original IDs rather than resend.
2. Read parent binding and trusted source observations. Raw source dedup differs
   from per-parent application disposition; a misrouted source can still apply
   once to the correct parent. Conflicting/unknown data remains quarantined with
   original identifiers, exposure facts and active reconciliation restrictions.
3. Existing funding remains held. `reflected` and `release_pending` are subsets
   of held and are not added as extra capital. Do not lower a reservation linked
   to a native parent using the ordinary-order release path.
4. Review parent AND all possibly live children, fills/fees, ownership, remaining
   generation and protection obligations. OP-C/G must provide reviewed closure
   proof before releasing linked parent funds; until that integration exists,
   the release guard is deliberately fail-closed. No automatic TTL release.
5. Newly generated external child submit/cancel/amend still needs its own command,
   fresh owner/epoch checks and permit. Native observations cannot issue permits.
   OP-B's mark_may_have_sent remains disabled until compiler/risk admission exists.

Rollback is a forward-safe operation: stop new v2 admission first (currently off),
retain new tables and events, and inventory all links/states. An old executable
that lacks the parent funding guard must NOT take over outstanding v2 funding.
If v2 records/holds exist, keep the compatible reader/guard and fix forward until
reviewed closure is implemented. Do not drop tables, rewrite hashes, delete
UNKNOWN rows or recompute old commands under the new compiler. A rollback to
pure legacy code is permissible only with proof that no v2 responsibility exists.

No production migration, LIVE/Testnet enablement, account access or venue order
was performed by this work. Tests used newly provisioned disposable PostgreSQL.
