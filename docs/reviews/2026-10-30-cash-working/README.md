# CASH A/B and source/order bounded review

Runtime product: `805aa53f4b8698b2e79524971974cc93c86a0013`.
Cash review base: `85a919d` (full SHA in DIFF-MANIFEST.json).
Sole writer: `cash_chain_fresh`; ownership retained while idle for independent review.
PAPER component proof only. Full46/56 acceptance remains ACTIVE; no ROOT integration or operational enablement is claimed.

## Review boundaries

The first six patches partition every changed source/test/tool path in BASE85a919d..805aa53. Patches07–08 additionally isolate two confirmed conservative-boundary fixes. Patches contain exact Git diff bytes; DIFF-MANIFEST.json records full endpoints, path lists and SHA256. Documentation/raw evidence changes are outside those product patches.

1. `01-root-physical-verbatim.patch`: exact nine-path reviewed ROOT5ef20a4 physical reservation/release slice. All nine paths still match the frozen manifest and ROOT Git blobs byte for byte; three typed-facts paths already matched CASH base and therefore have no patch hunk. DEPENDENCY-EXACTNESS.json records each. There are NO local differences in these nine files. The inherited ROOTb7 manifest remains the prerequisite; no quote/native/management WIP is included.
2. `02-working-order-product.patch`: new execution/program_order_projection.py, explicit projector interface and narrow source snapshot accessor. No other existing source provider functions changed. Read original issued sequence1 bundle, not later audit revision; use private current snapshots/original command/funding link/actual FillLedger/physical ROOT reservations. Only own book order facts/sequence/basis change. Marker/EventStore/outbox bind actual aggregate revision separately from projection row sequence. Old apply keeps its contiguous revision guard.
3. `03-observed-source-completeness.patch`: new execution/program_observed_cash.py and strict cash integration. Enumerate actual0018compact headers/leaves across exact environment/account/product, all instruments. Require actual approved/ledger/owned coverage, immutable native economics and verified global source status. Load/hash full proof once per snapshot with existing cache, never once per leaf; derive no new cash credit. Zero-fill and identical native data under a new observedClock remain valid.
4. `04-cash-integration-and-safety-tests.patch`: genuine A/B and working/replay/source PG plus explicit projector unit cases and additive mandatory suite registration. Original finite market events, actual scoped fixture loader and current protected knownness are used. Both A/B accepted-unfilled before either fill; no prepared-facts mutation, duplicate SQL reader or permit bypass. V2 fixture import uses the correctly scoped cash_database module fixture.
5. `05-root-optional-validation-verbatim.patch`: exact optional ROOT5ef20a4 physical PG/release unit files; both verified byte equal to their source Git blobs. No old tests deleted or weakened.
6. `06-legacy-negative-fixture-only.patch`: test setup after0020 now asserts ordinary owner seed DENY. Only isolated marked testadmin creates an explicitly UNQUALIFIED negative audit shape. It creates no protected legacy seed/attestation and relaxes no production permission.
7. `07-counterexample-completeness-fix.patch`: exact b39740e..e61f5e9 product fix following four real DIDNOTRAISE failures.
8. `08-counterexample-replay-status-fix.patch`: exact3bd8c57..805aa53 product fix for false financial completion on semantic replay. Replay validates current book and strict current cash READONLY; it cannot silently reconcile/renew a stale binding.

Of eleven preserved CASH modules at dependency import, nine still equal baseline bytes. Only program_account_cash.py (completeness gate) and program_fill_source.py (narrow existing snapshot accessor) differ intentionally and are in patches03/02. No migrations, old source payloads/hashes, owned posting, asset ledger, opening readers or FillLedger were changed in this slice.

## Evidence and genuine counterexamples

RUNS.json indexes ALL25 new fixed-source runs, including failures and fixture preconditions; all are terminal with JUnit present and zero mandatory skips. The evidence COPY-MAP covers these plus the previous20 terminal runs. Raw bytes are copied under docs/evidence/binance-orders/op-e-cash-chain with -text attributes and force-added logs. COMMITTED-BYTES.json checks original/copy/actual committed Git blob SHA256, not a worktree-only digest. Do not publish container.env or source.bundle.

| Actual PG suite | Source | Result | Run |
| --- | --- | --- | --- |
| execution-evidence | `74aa479` | 37 PASS, 3.65s, 0 fail/error/skip | `8f57ce55f393` |
| program-account-cash | `4749d95` | 9 PASS, 45.44s, 0 fail/error/skip | `72a1e80a295e` |
| program-accounting | `d349ec9` | 9 PASS, 53.10s, 0 fail/error/skip | `fe1c6a34bdb8` |
| program-fill-source | `4749d95` | 16 PASS, 88.14s, 0 fail/error/skip | `10745de19e55` |
| program-first-capital | `d349ec9` | 3 PASS, 7.32s, 0 fail/error/skip | `a685aa371692` |
| program-owned-posting | `805aa53` | 11 PASS, 91.91s, 0 fail/error/skip | `90da05c1bd21` |
| program-scoped-financial | `4749d95` | 16 PASS, 44.98s, 0 fail/error/skip | `1c65d5ff4f7b` |
| program-submission-observation | `d349ec9` | 8 PASS, 54.99s, 0 fail/error/skip | `c6bd83df26c0` |
| program-v2-owned-posting | `d349ec9` | 2 PASS, 18.20s, 0 fail/error/skip | `89bf406fae47` |
| program-working-orders | `805aa53` | 17 PASS, 137.52s, 0 fail/error/skip | `d0a9ed6ebd38` |

Focused unit log: working-final-unit.log,23PASS1.55s. The earlier host working-sequence-unit.log honestly contains2 explicit PG-environment skips; the corresponding original projector safety cases then actually ran in the mandatory37PG cohort with zero skips. No broad ordinary/119 cohort rerun in this slice.

- `2753198`: genuine missing working API RED (4fail); initial `a5d9ede` aggregate stream attempt was1pass/3fail. These failures are preserved, not qualification.
- `1ea9665`: original issued quantity1 was incorrectly rescaled to2 by a later owner audit bundle. Fixed8f3cd16 reads and verifies ORIGINAL recorded financial bundle sequence1. The old POSITION_EVIDENCE expectation failure is separate and retained; tests now assert the real existing block.
- `b39740e` /run3c2124335d70:12pass/4DIDNOTRAISE. Actual Afill→private observe only, with0 approvals/0 ledger, allowed Bfunding; sameNative unit, fee and globalstatus conflicts also escaped existing older facts. Fixede61f5e9:16GREEN113.64s; final working17 also covers all four. Earliera407217 failed OPENING_IMMUTABLE fixture setup and is explicitly not the proof.
- Real event stream: Fill revision1 → Snapshot revision2 /projection row1 → Fill revision3 → Snapshot revision4 /row2. Actual event reaches verifier unchanged. Initial full snapshot uses authentic source/full recorded fills without inventing prior venue state. Marker binds applied_event_revision independently from rowseq; old apply gap remains rejected. Commit failure rolls everything back; fork restart/replay add no duplicate rows.
- `3bd8c57` /runf01012b64536:10pass/1false financial-completion failure while B native economics remained unposted. Fixed805aa53 validates current book and readonly strict binding on replay; both calls retain FINANCIAL_RECONCILIATION_REQUIRED and a forged book also denies. Final owned11GREEN91.91s.
- First affected raw9 run74aa479 was5pass/4 owner-seed privilege fixture failures after0020; test-only negative seed preparation fixd349ec9 retains production DENY and all safety assertions. Fixed9GREEN53.10s.

## Result and remaining boundary

Actual A/B finite fills are.4 each, fee.001. Both raw facts precede financial posting. A own posts while B unposted without cash binding; B completes without A financial row/xmin writes; A explicitly reconciles in OWN transaction. GlobalBTC10.8 /USDT934742.808, each owner.4, initialBTC10 unowned. Full163180 holds, .6 remaining per native order and fee32.596 each remain exact. Working service positively proves same-account peer no-write, replay/restart/rollback, V1/V2 nofill/partial, historical creationTTL GTC, UNKNOWN and current source/book negatives.

New bounded data intake uses the current book with owned.4+working.6 and original reservation-to-order IDs. Core cost evidence remains incomplete: POSITION_EVIDENCE explicitly blocks target compilation. No forced NO_ACTION, ALLOW, new financial admission/command/reserve/permit, release, terminality, FLAT, protection/history completion or old permit TTL renewal. Existing generic new-write structural guard stays. Cost/PnL/history and typed risk/current new-write proof are future separate tasks; this slice must not broaden them during review.

The earlier historical opening/scoped pure helper/V2 checkpoint and its failures remain in docs/reviews/2026-10-30-cash-chain. Review this bounded diff against that existing history rather than reclassifying old component proof as full capability acceptance.
