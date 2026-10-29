# Application-write obligations across existing safety readers

This slice is read-side integration into the existing ledger and risk boundaries.
ApplicationWrite records retain their original IDs, namespace and version. They
are not converted to legacy commands or inferred quantities. No new cash ledger,
send path, permit or venue-child submission is introduced.

Implementation sequence (TDD):

1. Reproduce existing readers ignoring v2 READY/MAY_HAVE_SENT and ID collisions.
2. Add one typed, fail-closed reader for original and latest v2 history, exact
   physical/payload scope, immutable identity, program hash and current collateral
   binding. Do not use an inner join or state filter that hides malformed rows.
   Missing v2 tables mean an older database; malformed present history is blocked.
3. Feed exact-scope READY obligations into target and locked-reduction guards.
   Without an approved quantity allocation, reject with
   `PROGRAM_TARGET_ALLOCATION_REQUIRED`. Any sent/terminal label remains a
   reconciliation obligation until an independently qualified resolution exists.
4. Feed uncertain v2 writes into existing collateral-group dispatch blocking,
   including other accounts/products. Excluding a legacy command never excludes
   a v2 record with the same text ID. Preserve namespaces in diagnostics.
5. Run unit negatives and fresh isolated PostgreSQL tests for history/scope,
   group bindings, version/ID collisions, terminal claims and older database
   compatibility, then relevant existing safety regression suites.

Native child observation tables do not create application-write obligations.
Actual native qualification and a production resolution/allocation authority
remain outside this slice and must stay explicitly blocked.

## Implemented boundary and remaining restrictions

`storage/application_writes.py` now returns typed original/current obligations
from the existing `order_writes_v2` table. It verifies contiguous immutable
history, first-row physical/payload scope and ID, versioned codecs, program hash,
application-child ancestry and the current collateral account binding. It does
not filter on a latest payload's state/account before validating the original.
Valid other groups are ignored. An unassignable malformed first row or missing
binding fails closed because its collateral scope cannot be trusted; it is not
silently assumed unrelated. Older owner epochs continue to count as obligations.

The existing target reader rejects exact-scope READY writes with
`PROGRAM_TARGET_ALLOCATION_REQUIRED`, and sent/claimed-terminal writes with
`PROGRAM_TARGET_RECONCILIATION_REQUIRED`. No money is converted into quantity.
The existing group command scanner retains every non-READY program write,
including raw ACK, terminal and abort claims, using diagnostics containing the
original namespace, scope and command ID. A legacy exclusion cannot suppress the
program namespace. The locked-action verifier rejects queued program obligations
before requesting a proof that could spend the same reducible position.

Missing additive tables preserve old-database behavior. Present malformed
history is a block. Raw terminal state, a legacy outcome audit record or a
completed native parent cannot substitute for a genuine resolution protocol.
Native observation tables create no additional local commands in this reader.

No schema migration or historical rewrite is required by this change. There is
still no production allocation/resolution authority for general program writes;
new targets and lock exceptions remain restricted while those obligations are
unresolved. This is a prerequisite for later program admission and recovery,
not native execution qualification. The initial implementation reads first
identities across the table before grouping so malformed UNKNOWN cannot vanish
through a join. An indexed trusted scope directory may be needed for large
histories; any optimization must preserve the same fail-closed behavior.

## Actual verification

Evidence is retained in
`docs/evidence/binance-orders/op-c-application-write-obligations/`:

- Real isolated Linux/PostgreSQL plus unit baseline-hook RED:20 failed,
  17 passed. The candidate reader and tests were unchanged; exactly the three
  existing scanner modules were restored to commitddf944c in a read-only test
  snapshot. This proves omission in the old hooks rather than import failures.
- Candidate run:37 passed, zero failures/skips (14 integration and23 unit).
- Separate pre0008 PostgreSQL database:1 passed, zero failures/skips; legacy
  uncertain state, original client ID and legacy exclusion behavior preserved.
- Contracts/intents/unit regression:902 passed. A historical fake database now
  explicitly reports absence of the new table; its existing ownership/fill
  assertions are retained.

The probes cover READY target blocking, same-text cross-version IDs, same-group
other accounts/products, unrelated groups, raw terminal/audit claims, malformed
first/latest rows, physical-scope mismatch, missing binding and queued reduction
before proof. Corruption probes run as ordinary-owner reads against actual
PostgreSQL rows injected only by the disposable test administrator, then roll
back. Initial combined collection exposed a duplicate pytest module basename;
the unit file was renamed with all assertions retained and that error is saved.
No engine, venue account, outbound order, paid model or fabricated fill was used.

## Corrupted/imported state regression defense

Review found that READY→MAY_HAVE_SENT/UNKNOWN→READY could otherwise hide an
uncertain write by changing only its latest state. Migration0008 already rejects
that ordinary SQL transition; this additional reader defense handles corrupted
or imported histories. Once a non-READY state appears, a later READY raises
`PROGRAM_WRITE_STATE_REGRESSION`, regardless of a changed durable_record_ref.
The original READY positive case remains valid, while changing a reference to
`claimed-not-sent` never resolves UNKNOWN. No new schema or resolution authority
is introduced.

Real PostgreSQL fault injections retain every original client ID and roll back
after ordinary-owner reads. The new regression reproduced8 failures/2 passes;
focused correction10 passed. The complete obligation integration/unit suite then
passed47 cases (18 integration and29 unit), zero failures/skips; related existing
unit safety regressions also passed156. Logs, JUnit XML, source hashes and image
IDs are retained in
`docs/evidence/binance-orders/op-c-application-write-state-regression/`.
