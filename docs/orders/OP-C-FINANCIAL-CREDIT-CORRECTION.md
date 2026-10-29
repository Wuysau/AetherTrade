# Native-parent financial credit guard correction

Read-only review found a direct ordinary-owner bypass of migration0009. The
original trigger checked held reductions, but a same-held projection could set
reflected=held, release_pending=0 and a forged public balance_watermark. The risk
capacity formula then deducted max(held-reflected,release_pending)=0. A previously
legitimately released hold could similarly regain credit against a newer book
by changing only its watermark, without a new independent review.

Both the repository guard and PostgreSQL trigger now require the existing exact,
same-transaction independent approval for nominal held reductions, effective
debit reductions, increased reflected credit and changed credit-bearing
watermarks. The database additionally rejects malformed reflected/release-pending
subsets and inconsistent reservation reflected metadata. No new release or
reflection authority is granted. Ordinary unlinked reservation behavior stays
unchanged. Native still-held reflection and renewed released credit remain
default-denied absent a separately trusted approval path; ordinary callback
labels/public hashes cannot establish that authority.

New real PostgreSQL regressions use both the repository and genuine ordinary
session authorization for raw SQL. Each first proves the proposed projection
would reduce effective debit to zero, then requires rejection. A positive
unlinked-reflection case preserves the existing IP-07 behavior. Existing full,
partial and repeated independently reviewed closure cases remain valid.

Evidence is retained under docs/evidence/binance-orders/op-c-financial-credit/.
The original main Windows venv launched fresh task-only PostgreSQL17/Python3.12
containers. Source was mounted read-only; tests wrote only their isolated
databases and separate evidence directory. No operational container/database,
venue/account API, credentials source or paid model was accessed. The red run
reproduced4 missing rejections (4 failed/1 passed). The corrected focused run
passed6 cases (5 new integration+1 existing unit). Existing closure regressions
passed28, with zero failed/skipped, separately in a fresh database because their module fixtures intentionally
refuse any existing ledger. A first verification attempt exposed a SQL
parenthesis error and incompatible shared database fixture setup; its raw output
is retained alongside the corrected runs. Actual venue qualification remains
NOT_RUN.

## Additive migration and recovery

The final correction is migration0011, applied after0009 (and0010 in the
integrated installation sequence). Migration0009 is restored byte for byte to
its original committed form, SHA256
`d716937224421b366a1f930a7209784609c6d1609d2d40c789aa87a8635b8106`.
The earlier candidate edit and its evidence remain in Git history. Existing
databases receive an explicit new migration rather than silently rerunning a
changed historical file. No command, reservation, event, hash or ID is rewritten.

Quiesce application writers before upgrade. Migration0011 also locks the
reservation audit and funding-link tables while checking existing projections
and replacing the guard, preventing a concurrent INSERT from escaping between
those steps. An existing linked credit must have an exact archived independent
approval for its scope, reservation, sequence, payload and transaction identity.
Malformed existing linked projections cause refusal. Unapproved existing credit
raises `unreviewed linked financial credit requires reconciliation` with SQLSTATE
23514; the migration transaction rolls back and the original records stay intact.
That refusal is a blocked upgrade, not successful qualification. Keep writers
quiesced until independent reconciliation establishes the native parent/child,
fill, fee, balance and protection facts. Any necessary conservative projection
must be an explicit reviewed append preserving the original history, then the
migration can be retried. Do not stamp a new watermark or manufacture a
retroactive approval. Migration0011 performs no automatic credit reversal.

An exact previously approved closure remains valid history and is preserved.
The new guard requires a fresh independent approval for each later credit
change; archived approvals do not renew authority. Do not roll back to the
vulnerable guard while native funding is active. Disable new writes and retain
the new ledger, original-ID recovery and outstanding obligations for recovery.
Active native-parent reflection and refreshed released credit still need a
separately trusted approval path and remain blocked; this migration adds none.

Additional evidence is in
`docs/evidence/binance-orders/op-c-financial-credit-upgrade/`:

- Upgrade preflight RED:1 failed/11 passed. A genuinely committed old-version
  forged credit was incorrectly accepted by the initial additive migration.
- Final focused Linux/PAPER PostgreSQL plus unit run:12 passed, zero failed or
  skipped (8 integration cases and4 unit cases).
- Existing closure regression in a separate fresh isolated database:28 passed,
  zero failed or skipped. Its module fixtures intentionally refuse existing
  ledgers, so it is not combined with the financial-credit module database.
- Exact-decimal RED:2 failed/1 passed using the committed old helper. The final
  helper uses the existing256-digit parent-funding arithmetic boundary with
  rounding traps; caller precision cannot conceal a small debit reduction.

The upgrade tests compare commands, reservation creation rows, audit history,
funding links, parent authority and parent state before/after migration. They
exercise old guard→0011 with existing native funding, refusal without rewriting
committed unapproved credit, and acceptance of previously reviewed credit. The
explicit conservative append in the refusal test is test cleanup, not migration
behavior. Source hashes, image IDs, real stdout and JUnit XML are retained. No
operational database, venue qualification or real account was used.
