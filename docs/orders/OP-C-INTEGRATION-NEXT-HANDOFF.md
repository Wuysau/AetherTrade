# Root integration continuation handoff

Prepared2026-10-30. SOLE writer is still integration_next until parent explicitly
reassigns it; this document alone does not release ownership or pause the goal.
Main/archive untouched. Full46 capabilities/56 cases and OP-C→D/E/F→G ACTIVE.

## Current source and jobs

Root branch codex/binance-order-capabilities. Product source1aab295 plus docs5685efb
contains actual quote financial kernel and PUBLIC request/outbox attachment,
actual owner-held program dispatcher, matcher0013 + source/write precision fixes,
trusted report verifier and scoped OP-F API/CLI/UI/model research. No rich-order
production admission reader/native transport qualification is installed.

Only live root test job: exec57061, fixed0a55692 full42 mandatory PostgreSQL suites
plus ordinary Python, timeout5400, fresh private Docker, explicit immutable runner
sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7.
Artifacts .runtime/paper-qualification-f5be1013bf70/evidence. Do not restart.
Program-commands56 passed290.10s under300s budget; preserve timing/no widening.
Later current code is outside that fixed source; a pass cannot qualify it.

All other root handles mentioned below are TERMINAL; never restart them:

- fixed5866ddc exec45760:35 PG suites1020 pass/0fail/error/skip; ordinary3411
  pass/574 environmental skips/96deselected.95 raw artifacts committed+SHA checked.
- fixed4843bff exec83773 matcher17PG pass;499c757fc475 evidence committed.
- fixed0cfc032 exec73040 quote kernel6PG pass;1dfb53045810 evidence committed.
- f0beacf13653:6pass/2fail (genuine book RED; callable-replacement clock fixture).
- 86efee760841:7pass/1fail (same clock fixture issue, separately labelled).
- corrected04482b122059:7pass/1 genuine advancing-clock DID NOT RAISE.
- fixd6eaed069334:8PG pass,5031c1cda0f1 evidence committed.
- dispatcher4905fea39464:5 missing-module RED; fixeb7c8f371042:5PG pass,
  d92c3aa1a337/1347a34f5b40 evidence committed.
- shared consumed guard2dde8f672303:14PG pass,86c479091259 evidence NOT COPIED yet.
- public quote649dc2459262:3 missing-port RED,0f6e89ba6562 NOT COPIED yet.
- public quote1aab29518864:3PG pass,953db907bcd0 NOT COPIED yet.
-1713 contracts/intents/unit at0cfc032; later quote attachment44 focused pass.
- OP-F restored reviewedb0 source:532 server pass/56 mandatory-environment cases
  deselected; web119 pass/typecheck/build exit0. npm ci installed local ignored
  web/node_modules. Source-specific peer evidence retained; no paid calls.

## Actual quote financial/public path

Version2026-10-30 source QuotePositionProposalV1 carries ABSOLUTE_POSITION_VALUE_BAND
and declared reference basis. QuoteProgramCompilationV1 preserves immutable native
quoteOrderQty/notional; no scalar target/quantity fiction. Fraction outward fill
interval includes0/no-fill/partial. Fees retain exact assets and receipt/external
semantics; primary SET_TARGET reductions cannot undershoot desired lower band.
Independently authorized protective/triggered exits use separate trusted duties.

QuoteTargetAllocationV1 replays compiler and same _validate_program_scope/TargetCompiler
reader, preserving full financial book while selecting actual quantity holds.
RiskEvaluator.evaluate_quote_program shares binding/freshness/ExposureCalculator/
account/protection/portfolio/spendable/capital core, evaluates no-fill/endpoints,
and retains loss/protection across current inventory. Real side-swap RED fixed
13bf1e3 in both old/new adapters using per-order current side, not aggregate net.

QuoteAssessmentArchiveV1 full replay; distinct QuoteFinancialIntentV1/RiskBasisV1 in
same LedgerIntentCodec, old types/hash domains unchanged. Bounded QuoteProgramIntakeV1
uses SAME restricted intake table. ReservationService._reserve_program chooses
typed adapter but shares locks/capacity/holds/reservation/audit/witness writer.
QuoteFinancialInputsV1 requires independent trusted LOCAL load_execution_bounds;
missing/defaultNone deniesquote. It is compared before validation and persistence,
then clock/fence/book are repolled. Hash/dataclass construction is not enforcement.
Equity CARD/MAIN assessed balance location is still explicitly unbound/denied.

Public GenericProgramAdmissionService now forwards optional load_execution_bounds,
accepts exact QuoteProgramCompilationV1 and requires actual same-TX request/funding/
ID/outbox/physical-xmin attachment before commit. Request remains V3 CREATE_REQUEST:
its source-neutral fields/meaning unchanged, new exact bundle/archive registry
selects quote financial kinds. Unknown kinds reject. OriginalCreation reader uses
the same exact static dispatch without historical risk replay. Public3PG verifies
actual4000 quote body/no quantity/price field, original-ID replay and after-real-
append rollback. PUBLIC quote MAY/consume and actual MARKET producer NOT DONE.

## Next critical quote work (parent reviewed direction)

Reuse source-neutral V3 shape; do not add fake TradeIntent/scalar target or rewrite
0012/history. Reserve0015 business migration for exact quote archive/version
context predicate. Keep every old private binding/owner/fence/token/SQL expiry
condition, old V2 handler and role permissions.0014 is separately OP-D SIM-only.

Add optional independent load_execution_bounds to ProgramCommandRepository and
ProgramCommandBoundary current readers. validate_current_inputs already dispatches
quote if loader provided. Current _current still assumes old budget_facts and
target_facts shapes; adapt exact typed kind only, preserving old path and root
local-program exclusion fix97b7710.

Important foreign-hold detail: initial quote assessment uses prepared collateral
book (all group cash holds), while persisted source BookSnapshot is raw. At
current mark/consume exclude the exact independently verified own bundle holds,
construct risk book with actual remaining group holds, and derive physical
quantity subset from scope IDs/original scoped external snapshot. Never turn
foreign cash into quantity or silently ignore it. Use actual reconciled current
holds before/after the trusted reader; do not cache current facts/ALLOW.

Protected quote context likely needs distinct source_book_hash (actual raw book)
versus assessment.book (prepared financial book); exact full hashes both bound.
Do NOT drop current-book check to fit the old consumed guard. Extend static
quote context validation/guard explicitly with physical current otherholds.
First add real positive mark/consume and expiry/forged-context/foreign-group-hold
tests. Quote current fixture qload must explicitly exclude actual own admitted
holds from other-hold inputs; never supplied-zero shortcut. Price execution
artifact still independently reloaded and original wire budget unchanged.

Original SOURCE/snapshot/schema hashes and UNKNOWN history remain immutable.
Do not infer an execution guarantee from profile range/latest quote. Actual
native/current MARKET enforcement remains a qualified producer duty. Preserve
raw overshoots with risk/reconciliation duties; no cap/round-to-flat or release.

## Peers / shared boundaries

Parent root owns new isolated binance-ope-program-accounting based986c6d0.
Reviewed foundation7d5f4e9 (+tests1faf83c): source preparation captures bytes before
TX, EvidenceIngestor.ingest_in_tx rehashes without IO, FillLedger.record_in_tx
retains all existing validators/EventStore/notices/outbox; actual4new+37oldPG
green. Source/evidence1583322,50 committed artifacts verified. NOT integrated root.
Do not take its docs/current-task over this one.

Accounting ruling: principal uses same-ledger immutable per-fill asset posting
deltas + existing BookSnapshot, not cross-asset Cashflow.INTERNAL/EXTERNAL or a
second running cash total. Real FEE/FUNDING keep their existing meaning and no
double count. Raw owner audit/fill/OBSERVED JSON cannot grant cashcredit. Parent
reserved0016_program_fill_source_approval.sql: independent NOLOGIN source-verifier
role, no memberships/control/closure grants, narrow protected approval linked to
actual verifier/source/consumption/native-fill/unit/economics hashes. Committed
source approval may precede owner posting TX; no nested role TX under owner mutex.
Owner TX posts once against exact approval; readers enumerate approvals/rederive
once. Missing approval/discrepancy/unknown fee keeps economics locked and legacy
refresh preserves current book. Futures principal is not Spot gross debit; actual
PnL/margin/funding/cost evidence required. No flat/protection/release inference.

reports_fresh owns binance-ope-native-adapter, old report tree5ef7d9a preserved.
Guardde747e8 independent review14PG green integrated2dde8f6 with public boundary
recheck_consumed delegation and required suite. Root14PG GREEN above. It binds
actual physical committed MAY/consume+tokens, SQL expiry entry/exit, original
bytes, owner/lease/fence/book/locks/horizons; no new permit/risk replay. It still
explicitly rejects quote archive kinds until root extends their semantics.
PAPER final guard has NOT yet been changed to reuse this new method. Do so only
with original permit and exact consumption hash; recheck after every wait.

Native adapter candidatebf4017b8PG pending at peer: default-disabled concrete
OFFLINE probe on actual NautilusEngine, real owner/consume/boundedwait/recheck,
actual pinnedSDK fake HTTP shape; UNKNOWN raw archive/no invented ACK/fills.
Actual Rust queued final-send hook remains BLOCKED. Peer may continue unsigned
management/query shape only. Management requires new distinct write/binding/action
risk/overlap/target proof, independent permits and cancelReplace outcomes; never
reuse creation permit or oldV2 history. Global inventory/schema still root serial.

activation_fresh owns binance-opd-matcher.0013 frozen; newer reader6e8 and WRITE
precision7178 integrated root. Peer initial17+legacy159PG/Python1525 raw e2114e4
and precision raw pending selected copy. Reserved0014_paper_market_matcher_v2.sql
SIM-only; candidate13c61a53PG base-MARKET/Spot stoplimit/trailing green, advanced
mixedV1/V2/STP/peg/iceberg/session/list/algo still active. No second cash ledger.

Parent OP-F sourceb0d5445 reviewed/integrated0a;3required suites in root inventory.
Full host/cross/RULE/HYBRID/HTTP and V30 model support remain unfinished; default
unqualified model path makes zero provider calls. Old analysis and financial
snapshot hashes are separate named domains, never forced equal.

Local TWAP/VP activation and SUM pool are real, but children still need a distinct
typed parent_activation_ref/new write contract/current per-child risk/permit;
V3 parent_command_id cannot be a fake mother. Staged local OTO/OPO/list activation
also remains blocked by initial-only boundary. Keep all46/56 scope, do actual
work after each cohort, and never promote unit/synthetic evidence to native.
