# OP-F scoped order observation

Status: implemented read-only slice at `d51523f`; full OP-F remains active.
This extends the existing console without admitting or dispatching rich programs.
It uses the frozen V2/V3/2026-10-30 codec map, the existing application writes,
financial bundle, GenericProgramAdmissionReceipt, and local activation records.
No schema, migration, qualification registry, lock file or execution path changed.

## API and trusted scope

Existing cookie authentication protects all three GET routes:

- `/api/order-programs?limit=10&scope=<server-scope-key>&cursor=<opaque>`:
  keyset pages of 10/20/50, stable `(scope_id, program_id)` ordering, archived
  programs plus retained intake-only proposals. The host chooses the allowlist.
- `/api/order-programs/{program_id}?scope=<server-scope-key>`: original plan,
  intakes, current archived command states, full request bindings, financial
  receipts, reservations, mother/child state and residual management duties.
  Unknown account scope returns 404; caller account/product cannot expand it.
- `/api/order-capabilities`: all 46 fixed reference targets, expressible codec
  versions, separate unknown compilation/test status and NOT_RUN venue status.
  This reference does not represent a current CapabilityRegistry decision.

`create_app(..., order_observer=...)` is a trusted composition dependency.
The default only observes the existing UnifiedPaperRuntime's provisioned console
scopes. It does not discover arbitrary accounts. Each read starts a PostgreSQL
REPEATABLE READ READ ONLY transaction and rolls back/closes it, without invoking
a business UoW or appending its transaction markers.

The same service is used by CLI `paper-programs` (scope/limit/cursor) and
`paper-program-detail <program_id> --scope <key>` with explicit isolated PAPER
configuration. `order-capabilities` reads the reference without loading runtime
configuration. The new page reuses the existing decision pagination component;
10/20/50 controls and history behavior remain covered.

## Meaning and compatibility

- A committed receipt is not a dispatch permit, venue ACK or fill. DURABLE_READY,
  MAY_HAVE_SENT/UNKNOWN and venue observations remain separate records.
- A local TWAP/VP ACTIVE state is labelled LOCAL_ACTIVATION. No venue mother is
  invented. Venue-generated children are observation only.
- Empty/missing child inventory cannot establish flatness or end responsibility.
  Stored protection remains unverified unless its real record says otherwise.
- The original program fields, IDs, hashes and raw JSON are retained. The new
  `canonical_payload_hash` is explicitly a digest of archived JSONB content,
  never a replacement for stored proposal/command hashes.
- Unknown version/kind and invalid known-version records remain visible as raw
  archives, without inferred nodes/defaults or recompilation. Legacy analyses
  and decision-history rendering continue unchanged.
- Detail reads cap each related family at 500 latest records and report a
  truncation flag. Missing optional newer tables do not erase old observations.
- Archived capability claims and their evidence are displayed as historical.
  Current qualification is UNKNOWN and host/current-qualification blockers stay
  visible because no genuine production rich-admission reader is registered.

## Evidence and remaining work

See `docs/evidence/binance-orders/op-f-observation/README.md` for exact source,
raw RED/GREEN logs, PostgreSQL diagnostics, actual HTTP and browser evidence.
At d51523f: 7 real isolated PostgreSQL cases (3 basic + 4 actual public admission),
25 focused backend cases and 118 frontend cases passed; build/typecheck passed.
This proves the observation component, not the complete 46/56 gate.

Parent integration owner must register BOTH required suites in
`tools/ci_paper_postgres.py` before the final full gate:
`order-observation` and `order-observation-admission`, each with IP03 isolation.
The worker deliberately did not edit that serial-owned inventory.

Remaining mandatory OP-F: bind the actual frozen snapshot to AI input through the
same future qualified host/Registry reader, report current replan reasons, and
expose authorized control actions only through the same durable command/permit/
owner boundaries after those host interfaces are qualified. The read-only UI
provides no execution buttons. ORD-45/56 remain component coverage rather than
full integrated acceptance. No paid model, actual account, Testnet or LIVE call
was made. No production account status, fill or venue qualification was invented.
