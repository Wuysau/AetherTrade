# Versioned financial records in the existing ledger

Root approved the concrete contract/codec/persistence plan before edits. The
dependency is the actual frozen RiskTerms/RiskMetrics commit88d3c90, cherry-picked
here as3bce1a8. Public contracts import no intent/risk implementation modules.

This slice adds execution-neutral ProgramFinancialIntent and risk-basis records,
an explicit old/new ledger codec, static typed content archives and
registry-aware access to the same intents/reservations tables. Original V2/V3
proposals, units, signed target residuals, source/intake/context and all current
content bindings remain explicit. No fake TradeIntent or execution preference,
new ledger, financial ALLOW, command, permit or send is introduced.

The content archive retains full compilation, target/budget facts and outputs, snapshots,
profile, book, context, policy and actual shared RiskTerms/RiskMetrics. Hashes
establish integrity only. Future admission must independently read the bounded
ledger_intake source and authenticate complete archive facts under current
owner/collateral locks. Record existence, labels and provider booleans grant no
authority. Existing generic send marks remain denied.

## Explicit proof boundary

Independent ProgramRiskFacts is still being implemented separately: its complete
funding, holding, protection, count and execution-price inputs are not contained
in ProgramBudgetFacts. The public intent and risk-basis records have an explicit
independent risk-facts hash, but a hash declaration cannot supply those facts.
The content codec can retain the presently known static typed data for review;
production archive-to-financial-intent/basis conversion explicitly fails with
`PROGRAM_RISK_FACTS_ARCHIVE_REQUIRED`. No budget digest is relabeled as risk proof,
no dummy risk-facts structure is introduced, and no generic financial ALLOW is
implemented. Storage-only test DTOs deliberately carry unverified declarations
and prove that they cannot obtain a legacy permit. They are not qualified trades.

The full source responsibility is checked within known content: leverage,
action, submission deadline and validity must match the original proposal and
allocation. Three real negative probes reproduced acceptance of changed terms
before correction; those RED logs are retained. Three further RED cases used
self-consistent rewritten hashes to expose contradictory source side, unit
multiplier and signed delta. The validator now checks those algebraic bindings
with the same public FinancialUnit/FinancialTarget types. Portfolio metric fixtures now
come from the existing ExposureCalculator, including its related groups and net
exposure; completeness validation was not relaxed.

## Persistence and compatibility

IntentRepository.get/append and reservation creation/existing views use one
explicit registry over the same ledger tables. Unknown kinds/versions and
physical identity/scope conflicts fail closed. Existing TradeIntent encoding and
hashing use the original ContractCodec. Generic metadata and new conservative
holds require the current ledger owner/epoch/policy, including after handoff.
Legacy append/mark/binding explicitly require a real TradeIntent and cannot infer
MARKET/LIMIT fields for the new kind. Generic record/hold storage is not risk
admission, source-intake authentication or permission to send.

No database migration is needed: existing append-only JSON intent and
CapitalReservation tables/indexes are reused. Cross-kind reuse of an immutable
intent ID is rejected, and rollback leaves no partial intent/hold. Original old
UNKNOWN IDs and hashes remain unchanged. The38 existing public/packaged files
are byte-identical to the recorded baseline; only the two new financial kinds
use opt-in Decimal schema support. Their public and packaged copies are added.

Final real isolated PostgreSQL checks cover10 new financial-ledger cases,30 old
ledger cases and67 old reservation cases, with zero failures/skips. The complete
contracts/intents/unit run passes1123 cases. Raw current-owner and post-handoff
hold-owner RED, source-term RED, final GREEN logs, JUnit XML and source/image
hashes are retained in `docs/evidence/binance-orders/op-c-financial-records/`.
Full generic bundle admission and complete typed risk-facts archiving remain
blocked as described above. No operational account, paid model, engine send,
fabricated fill or LIVE/TESTNET activation occurred.

TDD covers V2/V3 strict restoration, unchanged existing schemas/legacy hashes,
unknown versions/kinds, unit/scope/hash conflicts, immutable cross-kind IDs and
same CapitalReservation/restart/rollback/roles on isolated PostgreSQL. Old
commands require an actual legacy TradeIntent before their durable send boundary.
