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
