# Remaining implementation status (2026-10-31)

Source `64b3a50c20dfa665827ffec1af2e6a17eab93389`, branch
`codex/binance-orders-resume`. This is a code-evidence inventory, not a
completion claim. Full objective remains 46 capabilities / 56 acceptance cases.

| Area | Implemented | Locally verified | Not implemented / not wired | Environment / external block |
|---|---|---|---|---|
| OP-B contracts/storage | Versioned local-child request/command contracts; 0030 commitment storage; 0031 admission storage candidate | Contract/compilation/request/reader unit + static migration cases | 0030/0031 not applied; no runtime writer grant deployed; old-schema compatibility not re-run on PostgreSQL | Shared DEV unavailable; migration applies require approved process |
| OP-C compilation/risk/funding | Child compilation, target slice, funding envelope preview, cumulative quantity, current-facts admission writer, independent command, permit, UNKNOWN retention | 104 focused + 291 broader selected cases | Real activation/owner/risk/target/funding loader composition; actuator send; trusted unused-allocation release; cancel/fill race execution | PostgreSQL trigger/role/concurrency/rollback/crash cases NOT_RUN |
| OP-D matching/lifecycle | Existing PAPER matcher, finite depth, market events, native quote/native parent components, OCO/OPO/OPOCO state code | Historical component suites (preserved, not re-run as current batch) | VP actual execution closeout; all advanced modifier/lifecycle combination scenarios; local OCO race; post-fill protection/reduction combinations | Venue qualification and current integrated PG lifecycle NOT_RUN |
| OP-E management/native | Cancel/amend/cancel-replace candidates, limited KEEP_PRIORITY reduction, native parent/child ingestion components, SDK guard research | Historical component suites only | Full cancel-replace outcomes, USD-M modify, overlapping funds, native/quote unified postings, effective post-amend state, locked Nautilus build proof | No venue, no SDK build/install qualification, no actual native send |
| OP-F model/API/UI | Existing capability/model snapshots, API/UI separation components, local commitment read-only archive view, aggregate scan/archive candidates | Observation/API/frontend component tests; no browser E2E | Real current capability/rule/host binding into model + automation; AI/RULE/HYBRID same-contract chain; aggregate source authentication + VP admission | No model call, no deployment, no browser E2E |
| OP-G acceptance | Historical matrix, current-source inventory, offline audit | 46 capabilities / 56 cases represented; current source 0 PASS, 56 NOT_RUN; 1 historical ORD-47 PASS | Full DB, lifecycle, concurrency, fault, migration, recovery batch; final regression | Unified scheduler executor and fixed DEV service not available |

## Honest status vocabulary

- **PASS** in this document means the named local test command actually exited 0
  on the named source. It never promotes a capability or acceptance case.
- **NOT_RUN** means no current-source execution; skips are not PASS.
- **BLOCKED** means the required environment or authority is absent.
- **Historical PASS** is retained only with its original source and scope
  (ORD-47 local PAPER migration component), never migrated to this integration.

## Immediate next sequence when DEV is available

1. Apply 0030 then 0031 in a disposable `at_dev_*` database under the scheduler;
   run role, FK, deferred-completeness, quantity-pool and permit cohorts.
2. Build the real `LocalChildCurrentFacts` loader from activation, owner,
   target, funding and risk sources; run same-group concurrent admission and
   rollback/crash/UNKNOWN original-ID recovery.
3. Wire an admitted child only through the existing execution owner and PAPER
   adapter; add API/UI command/receipt/fill/duty distinction.
4. Close OP-D/E/F combination scenarios, then run the complete 56-case batch and
   record PASS/FAIL/BLOCKED/NOT_RUN per case.

## Preserved safety boundaries

- PostgreSQL remains the only business ledger; no second cash ledger.
- Every application-originated write requires its own durable command, permit
  and current owner validation.
- Native venue children are observation-only, ingested only after trusted
  report/query and parent-child verification; never resubmitted.
- UNKNOWN/conflict/over-capacity/cancel-race never releases funds or resends.
- PAPER only; TESTNET/LIVE, real accounts and paid model calls remain disabled.
