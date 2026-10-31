# Explicit quote interval financial integration

The existing ReservationService._reserve_program now dispatches the separately
versioned quote source through its SAME current owner/collateral transaction.
It uses the existing reconciled holds, capacity arithmetic, reservation writer,
intent registry, audit history and transient successful-kernel witness. There is
no second cash ledger or public standalone financial success API.

QuoteProgramIntakeV1 and QuoteFinancialSourceV1 use the same access-controlled
order_program_intakes table. The owner cannot author a bounded intake. The exact
2026-10-30 source carries an absolute value band and declared valuation basis;
native quoteOrderQty/notional remains an independent immutable request budget.
The scalar source, FinancialTarget and historical hashes remain unchanged.

QuoteTargetFactsV1/QuoteTargetAllocationV1 replay the real compiler and common
scope reader. Full collateral cash holds stay in the financial book. The kernel
independently derives the physical quantity-reservation subset and compares it
to allocation inputs; foreign cash is never interpreted as local order quantity.
Current UNKNOWN/local/external obligations retain the existing guards.

RiskEvaluator.evaluate_quote_program uses explicit QuoteRiskTermsV1, with no
scalar target. NOFILL and fee-adjusted endpoint scenarios use ExposureCalculator,
the same current bindings, freshness, account/protection/portfolio/spendable and
capital policies. Every priced working leaf must match its independently read
side; unchanged aggregate net quantity cannot hide a BUY/SELL swap. Retained
inventory and partial fills retain loss/protection/management responsibility.

QuoteAssessmentArchiveV1 replays the full source/allocation/risk calculation.
QUOTE_FINANCIAL_INTENT_V1 and QUOTE_FINANCIAL_RISK_BASIS_V1 are distinct kinds in
the existing immutable registry. They retain exact units, original native quote
budget, band/position envelope, fees and source/assessment hashes.

The private financial branch requires a trusted LOCAL load_execution_bounds
reader in addition to current inputs/qualification. It independently compares
the enforced model artifact before assessment and before persistence. The last
read is followed by another clock/fence/book barrier. Missing readers deny.
An archive hash does not establish enforcement. Equity CARD/MAIN funding is
explicitly denied until selected-wallet balance location is independently bound.
Primary band adjustment cannot impersonate the bounded emergency EXIT proof.

Fixed0cfc032 produced6 real PG passes. Fixed04482b1 then demonstrated the real
last-read expiry regression with one stable advancing clock callable:7 pass,
1 DID NOT RAISE. Earlierf0/86 clock fixtures replaced the callable instead of
advancing it; those raw runs remain labelled separately. The book-change RED was
genuine. Fixd6eaed0 passed8 real PG cases, zero failures/errors/skips, including
actual shared-xmin intent/reservation/bundle writes and attachment-failure rollback.
The source proof is synthetic PAPER engineering, never native qualification.

Actual public quote durable-request/outbox/permit integration is still required.
GenericProgramAdmissionService continues to refuse that unsupported attachment
shape. The private writer's mathematical result/witness cannot bypass this gate.
MARKET matcher enforcement, complete quote fee/dust residual persistence, current
wallet sources and full D/E/F/G remain active, with all46 capabilities/56 cases.
