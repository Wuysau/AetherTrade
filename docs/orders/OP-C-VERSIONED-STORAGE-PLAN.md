# OP-C explicit program storage versions

Approved scope: store/read/hash existing v2 and additive v3 programs through the
explicit version registry. Do not upgrade historical data or grant execution.

1. Add failing repository tests for v3 VP urgency and TWAP duration restoration,
   v2 hash identity, unknown version rejection and same-ID cross-version conflict.
2. Replace repository hard-coded v2 codec uses with version-dispatched program
   reads, validation and hash checks. Preserve the v2 ApplicationWrite schema.
3. Keep native parent lifecycle binding explicitly v2-only: existing authority
   lacks complete quantity/multiplier and group allocation semantics for v3.
   Durable write persistence must still produce no permit or outbound action.
4. Add genuine isolated PostgreSQL storage/rollback/history tests to the existing
   order-programs suite; run focused and relevant regression tests and retain
   evidence. No migration is necessary for the existing JSONB program envelope.

Old schemas/codec hashes/rows and UNKNOWN identities remain unchanged. New program
versions do not imply new lifecycle authority versions. TESTNET/LIVE remain off.
