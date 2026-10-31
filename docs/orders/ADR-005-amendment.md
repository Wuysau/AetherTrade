# ADR-005 amendment — durable boundary for application writes

Accepted by the user with ADR-ORDER-001. This is an additive interpretation;
the original approved architecture package and previously issued commands remain
unchanged. The historical write-path inventory is archived as v1.

Every AetherTrade-originated external write, including local algorithm child
submit/cancel/amend, obtains its own durable command, current owner validation
and one-action permit. VENUE_GENERATED_CHILD is exclusively an observation:
its native parent was authorized and durably submitted, but the child is not
submitted again locally. Trusted source, scoped parent/child IDs, cumulative
fills/fees, remaining exposure, reservations and protection duties remain
mandatory. Unknown or conflicting observations retain original identities and
raise reconciliation restrictions. This amendment grants no Testnet/LIVE access.
