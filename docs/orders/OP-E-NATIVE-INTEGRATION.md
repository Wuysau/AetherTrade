# Controlled native interface integration

The native adapter is an implementation and offline probe cohort. All external
activation remains disabled; no native/account/venue/last-send qualification is
established. Full46 capabilities and56 acceptance cases remain active.

## Owned interfaces

`native_creation.creation_call(VenueCreationRequest, clock=..., recv_window_ms=...)`
preserves every original form field/client ID through fifteen fixed product/
namespace/operation/endpoint routes. It returns an unauthorized call shape.
`native_management.management_call(VenueManagementRequest, clock=...,
page_number=..., page_size=...)` does the same for nineteen existing management/
query route-method pairs, including exact collection pagination. It contains no
sender or permission. Management requires its own future versioned durable
binding and independent permit; a creation token must never authorize it.

The existing `NautilusEngine` accepts an optional exact concrete
`OfflineNativeCreationProbe` or `PinnedSdkOfflineCreationProbe`. DefaultNone
rejects creation dispatch. `submit_creation(write, request, permit,
boundary=..., lease=..., owner=...)` requires the actual existing ExecutionOwner,
consumes through the actual ProgramCommandBoundary once, waits only on the
bounded concrete probe queue, then reloads `recheck_consumed` under the owner.
The original request is rebuilt into a call only from that returned source.
No business transaction crosses queue/client/archive IO. Root's dispatcher
retains its mandatory revocation side_effect_guard around this composition.

The result is `NativeCreationObservation`: command_id, call, raw_ref,
actual_sdk_shape, statusUNKNOWN and retained original-query/reconciliation
duties. It emits no ACK, fill, cash, ownership, position or release claim.
The SDK variant holds a public fixture HMAC signer only during the owner call
and has a fixed in-process fake HTTP implementation; it cannot select an actual
URL/client/key. This is separate from Linux PostgreSQL framework qualification.
The original QUERY reader/strict source archive remains the recovery path.

## Original consumed window

`ProgramConsumedRequest` and `recheck_consumed(boundary, permit, lease)` read
actual original/consumed protected records and close their transaction before
IO. Actual physical XIDs/tuple xmin/app-transaction IDs/MAY, token/request/context
hashes, server DB expiry, current lease/owner/fence/book/cause locks, source
assessment/qualification horizons and end clocks remain necessary checks.
The snapshot itself has execution_authorizedFalse and mints/renews nothing.

The mutable-label audit found that token identity alone did not authenticate
the logical expiry/deadline fields. Reconstructing with today's TTL policy was
also wrong under a reconfigured boundary. The final fix atomically stores
`context.permit_window = {expires_at, deadline_monotonic_ns}` in the protected
mark before SQL creates MAY. Recheck matches these immutable original labels
and validates their coherence with original clock/lease/source horizons and
the existing≤5000ms protocol, independently of current TTL policy. All real DB
expiry checks remain. No migration, old row/hash rewrite or new token exists.
Historical marks missing this metadata fail closed for new sends/rechecks;
read_original_creation and original-ID queries remain unchanged.

Root's quote assessment/raw/prepared-book/static-kind registry changes must be
preserved. Do not replace the root guard or command repository wholesale.

## Serial extraction

New own files: engine/nautilus/native_creation.py, sdk_pins.py,
native_program_adapter.py, native_management.py; the new focused tests and
qualification/nautilus/order_adapter/probe_signed_shape.py.
Shared bridge changebf4017b: optional concrete probe and submit_creation only.
Shared mark change2d6eda8: three lines that add immutable permit_window before
the existing protected SQL call. Shared guard change2d6eda8: the complete
_original_permit_window helper and one call after actual token validation.
Preserve all root quote/kernel branches and original0012 bytes. Root owns public
boundary delegation, global inventory and schema/migration integration.

Required new suites are program-consumed-boundary and native-creation-owner;
append their entries while preserving every existing required suite and special
financial fixture option. Evidence and exact source results are recorded in
docs/evidence/binance-orders/op-e-native-adapter.

## Explicit remaining qualification

Actual pinned Windows CPython3.14.2/Nautilus1.231.0 source files and core pyd were
hashed. The execution/config hashes match the existing pinned manifest. Exact
SDK sign_request/HMAC/URL encoding was exercised through fake HTTP for fifteen
creation plus nineteen management/query shapes. These do not prove semantics
of an account, endpoint, queue, venue response, performance or native deployment.

Pinned Rust source27a8e54e `crates/network/src/http/client.rs` SHA
ee606aeb333f639398dd97c060566b29e0f5b029b32024760da4a9d55420eb41
awaits rate limits before send_request and exposes no final guard hook through
the inspected Python HttpClient.request API. A caller-entry check or host
prebudget alone is not last-send proof. Linux native/build, post-SDK-queue guard
and all external activation remain BLOCKED/NOT_RUN. Native response mapping and
management durable integration continue under the approved task; no capability
target is deleted or declared venue-qualified by these call-shape tests.
