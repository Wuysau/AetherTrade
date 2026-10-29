# V3 durable program command plan

Approved by root integration lead before code. Existing owner/UoW/intents/reservations/outbox remain the only financial/execution infrastructure. No production transport, fake legacy command or local algorithm mother is introduced.

- [ ] RED and implement explicit ApplicationWriteV3 codec/schema; preserve all V2 bytes and history.
- [ ] Bind full unsigned creation request and actual ProgramFinancialBundle in one outer transaction, same order_writes_v2 and outbox; preserve assigned IDs.
- [ ] Add0012 private immutable bindings/identity/send-mark/permit-consumption records and NOLOGIN dispatch-verifier role. No memberships granted; owner/control cannot mint MAY approval directly.
- [ ] Extend shared application obligation reader without hiding malformed/UNKNOWN/READY-regression histories or namespace collisions.
- [ ] Implement typed fresh source/clock/owner/lease/qualification/risk rechecks; commit MAY before returning one-use permit, then recheck on server-only consume. Default readers deny.
- [ ] Real isolated PostgreSQL tests: shared bundle/rollback/roles, complete request tamper, uncertain commit/replay, original IDs and old V2 regression. No account/network/fill claims.

Actual archive API: ProgramFinancialAssessmentArchiveCodec and converters from intents/program_assessment_archive.py. Actual bundle/constants from risk/program_financial_admission.py. Old content archive stays blocked. Staged/internal activation contracts remain denied until independently admitted; no grants to ledger.control_commands.
