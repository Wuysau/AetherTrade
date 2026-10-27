# Offline ownership CAS authority

Migration `0005_owner_cas_authority.sql` adds the narrow database prerequisite
for IP-16 `COMMIT_OWNER_EPOCH`. Install it as the isolated migration administrator
after `0001`, `deploy/db/roles.sql` and `0004`; it also coexists with `0002`.
It does not assign runtime logins, memberships, passwords or account access.
It does not enable native trading, TESTNET or LIVE.

The ordinary `OwnershipRegistry` remains blocked with
`REVIEWED_OWNER_CAS_REQUIRED`. An explicitly composed `PostgresOwnerCAS` uses
the existing transaction connection; it never changes role or opens an elevated
connection. The separately assigned `ledger_owner_controller` may consume one
authority using `commit_owner_transfer`. It cannot issue one. The separately
assigned `ledger_owner_verifier` may attest or revoke one and cannot commit an
ownership change. PUBLIC and the ordinary ledger roles receive neither power.

The host's trusted local archive verifier must verify explicit transfer
authorization, both deployments, current reconciliation/position/quantity and
protection coverage, and the new owner's acceptance of the preserved exit plan
or a reviewed replacement. It supplies `TransferFacts`, canonical JSON text for
`ControlContext` and `TransferEvidence`, the exact durable `TransferRecord`, and
the exact old/final `OwnerRecord` to `attest_owner_transfer`. This is a trusted
composition interface, never a client DTO endpoint. A reference/authority ID is
not a secret or bearer permission. No production archive verifier is installed
by this change. Missing verifier composition must remain denied.

The verifier attestation locks the collateral group and active owner and binds
the exact scope, old owner, epoch, policy, transfer and projection sequences,
authorization context, evidence digest, quantities/units, freeze, reconciliation
case, system protection responsibility and accepted exit plan. Its lifetime is
at most 60 seconds and cannot exceed the facts' expiry. Current authorization,
deployment and policy history, as well as relevant scope ledger histories, are
snapshotted. An intervening revision invalidates the authority conservatively.
The verifier must revoke its authority through `revoke_owner_transfer` when an
external authorization or archive fact changes; that function uses the same
collateral/owner guard. Archive revocations must obey this contract. Raw writes
by a database administrator remain outside the runtime trust boundary.

The CAS rechecks that snapshot, authority expiry, revocation and every binding
after acquiring the guard with READ COMMITTED isolation. It changes exactly one
matching five-part active-owner row to the requested strategy and next epoch,
preserving policy and PortfolioBook. No runtime role gains broad UPDATE. The
named `owner_immutable` statement trigger still rejects ordinary UPDATE,
including zero-row statements, and DELETE/TRUNCATE. Its definer path requires a
private current-database-transaction approval, and a row guard requires the exact
approved old/new row. No trigger is disabled. Settings and forged audit payloads
cannot create an approval.

A deferred constraint requires the exact new owner and ACTIVE transfer
projections, with the next durable step and the same transaction identity,
before COMMIT can succeed. A raw controller CAS without those projections rolls
back. The approval is consumed once; precommit process loss rolls it back with
the active row and projections. Postcommit process loss leaves all three durable.
Transfer completion preserves the freeze cause and system protection duty; it
does not automatically restore permission to add risk.

The Linux PostgreSQL tests use a fresh `ip16_test_*` database and synthetic
trusted archive facts, with real SQL privileges, transactions, contention and
process termination. They verify the storage/security boundary only. Production
authorization archives, actual venue protection continuity, native adapters,
TESTNET/LIVE and the approved T/Q platform qualifications remain **NOT_RUN**.
The large-Decimal CAS test uses sufficient Decimal context to isolate this SQL
boundary from the separately reviewed transfer-coverage precision correction.
