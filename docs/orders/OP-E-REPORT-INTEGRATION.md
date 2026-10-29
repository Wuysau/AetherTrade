# Actual PAPER report integration contract

The trusted owner host constructs `PaperProgramReportVerifier(uow, scope,
engine=actual_paper_engine, archive_bridge=actual_native_event_bridge)`. The only
external request argument is `verify(command_id)`. It SELECTs original immutable
V3 records and actual protected consumed context in the same existing ledger,
closes that business transaction, then reads the isolated restricted PAPER venue.
No business transaction crosses venue IO. The service archives sources and
returns facts; it posts no fill, cash, position, command outcome or permission.

`PaperProgramVerification` carries `status`, `source_verified`, `fills`, `orders`,
`raw_refs`, `discrepancies`, `retained_duties`, `raw_report` and `market_evidence`.
All results are synthetic and have execution/funds-release authorization false.
`source_verified` establishes bounded source linkage and allocation consistency;
it is separate from authorization, economic consistency and complete coverage.

Each `NormalizedPaperFill` contains `fill: t.Fill`, `side`, `economics`, `raw` and
`fees_known`. `fill.quantity` is the original native quantity, `fill.price` the
unaltered venue price, `native_trade_id` the persisted source event ID and the
order identity includes its actual stored venue order ID. `economics` carries
the native facts, ledger quantity, original `FinancialUnit`, reported/expected
gross, original fee vector and economic discrepancies. The unit includes
`ledger_units_per_native_unit`, `native_multiplier` and `ledger_multiplier`;
native_multiplier = ratio * ledger_multiplier. Never pass these generic fills
through the old Spot quantity*price accounting or create a fake legacy intent.

An explicit empty source fee vector with a verified zero-rate PAPER model means
known zero; its t.Fill uses one zero Money entry so existing FillLedger semantics
remain correct. With a nonzero model, empty fees remain unknown: t.Fill.fees is
empty, fees_known is false and FEE_RECONCILIATION_REQUIRED remains. Original raw
fees and economics stay unchanged, including fee-model/budget discrepancies.

For `DISCREPANCY`, raw linked fills and venue cumulative/leaves are retained
without capping, FX conversion or repricing. Consequently an observed order
projection may have inconsistent or negative quantities. Root must archive these
facts, record risk/reconciliation duties and apply the existing projector's
validation; it must not weaken projector constraints or adopt a conflicting
projection as valid aggregate state. Missing account/history/protection coverage
continues to block automatic ownership, FLAT, protection and release claims.

Fill/disposition raw bytes are the ORIGINAL SQL `payload::text`, whose SHA must
equal the original stored payload_hash. App-canonical market-input bytes/hash
and derived report hashes are distinct domains. No original row or hash is
rewritten. NativeEventBridge durably archives received raw bytes before source
shape/hash/health checks; refs identify exact archived bytes. Only the existing
EvidenceIngestor and FillLedger may ingest/post them through root's reviewed
same-ledger transaction and accounting path, with no second cash ledger.

Current source interface covers one ATOMIC LIMIT/LIMIT_MAKER member from V1.
Unsupported advanced/session/source interfaces return blocked observations and
retained duties. The full46 capability and56 acceptance targets remain active;
this component does not qualify the later V2/0014 producer, native adapters,
TESTNET/LIVE, actual accounts or paid models.
