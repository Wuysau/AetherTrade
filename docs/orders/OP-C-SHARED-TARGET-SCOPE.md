# Shared actual position and quantity obligations for interval targets

TargetCompiler now exposes `read_scope_terms(TargetScopeTerms, book)` without a
scalar target quantity. Its scope/book/clock/session/unit checks and actual
position/working-order/pending-reservation reader are the same methods called
by existing scalar compilation. Old scalar normalization, conflict handling,
minimum/grid rounding and DeltaPlan construction remain in their original order.

The result preserves exact actual inventory, including base-fee dust between
order steps. Individual signed working/pending obligations remain available in
addition to the old net working sum. An UNKNOWN active order retains its complete
possible quantity. PendingQuantity does not carry a certainty classification, so
its distinct obligation is conservatively uncertain. Represented orders remain
counted once; cash holds are never converted into quantity. Scope, ownership,
evidence, clock, unit and reservation mapping checks are retained.

These are pure reader facts, never database authority or a send permit. The new
quote-band allocator still must bind them to authentic current intake/owner/book
under the shared transaction, assess zero/full/partial and fee extrema, and
preserve continuing residual duties. This extraction does not introduce a second
target arithmetic or risk engine, does not interpret a band as a fake base-point
goal and does not unblock interval financial admission by itself.

Five new cases observed missing-reader RED, then133 new/shared/legacy target tests
passed. Subsequent contracts/intents/unit run passed1528 with zero skips, exit0.
The tests cover exact off-grid actual inventory, both working directions, UNKNOWN
and foreign-owner/unhealthy-clock denials. No public schema, historical hash,
ledger/permit model, real account or operational configuration was modified.
