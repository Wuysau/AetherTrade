# Common financial preparation extraction

Approved OP-C integration sequence: use one IP07 financial core, with separate
legacy/program semantic adapters and unchanged ledger/owner/permit boundaries.

1. Extract current collateral projection, independent released-balance proof and
   hold validation into one ReservationService helper returning a typed prepared
   book or exact existing denial codes. Preserve the current verifier, queries,
   locks, source evidence and legacy command terminal checks. No program release
   proof or permission is implied by this extraction.
2. Extract the existing wall/monotonic admission window into a shared helper,
   retaining initial start time, monotonic deadline, backwards-clock denial and
   narrowed deadlines after risk evaluation. Both adapters must use this same
   helper; no clock or deadline may originate from an untrusted proposal.
3. Leave legacy replay, risk semantics, target, capacity, command validation and
   atomic write/commit order unchanged. Generic versioned records/commands remain
   a separate reviewed integration, with current send guards still denying.
4. Verify existing clock, balance/reflection/release, UNKNOWN, role and transaction
   tests; run real isolated PostgreSQL reservations and full integrated safety
   gate. Retain any genuine failure rather than skip or relax assertions.
