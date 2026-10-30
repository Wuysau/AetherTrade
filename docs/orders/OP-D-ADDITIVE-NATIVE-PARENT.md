# OP-D additive native execution-parent PAPER slice

Product BASE: `b7f6d85aab22097d05ed4ec80c032af21aa60dc8`. Plan/review BASE: `19a1259`.
Branch: `codex/binance-opd-native-parent-integration`.
Reference-only D: `d75cc14` (functional `6133034`). Its earlier 34 PG cases are not evidence for this candidate.

## Behavior and boundary

`0026_paper_native_execution.sql` applies after the unchanged ROOT `0014`. It adds immutable mother terms, child allocations and input dependencies, mutable finite mother progress, and trusted account/product/instrument/leg position snapshots with fill-tail projection. It updates the existing matcher functions; there is no second ledger, balance engine or matching core.

A funded original V3 TWAP command passes ROOT consume/current-owner/current-funding/window checks before accepting its mother. The adapter repeats the original consumed-permit clock and current-consumption checks after the shared market lock and immediately before SIM commit. Original assigned member inventory remains empty. Actual children are venue-generated observations, never extra application commands or permit exemptions. The original maximum quantity/gross/fees cover cumulative execution and the remaining mother pool. IOC unused quantities are retained and are not recycled automatically.

The explicit TWAP simulation model uses sixty-second slots and finite shared visible depth. A trusted accepted mother can generate observations after its creation permit TTL without obtaining another permit. No expired permit can create a new mother. VP remains parent-only, with no claimed child-generation equivalence or source-verifier support.

## Exported interfaces

- Existing `PaperEngine.submit_creation(...)`, `query_creation(command_id)` and `query_market_evidence(command_id)` remain the producer APIs. Ordinary/list reports retain exact existing types. Native mothers return `PaperNativeExecutionReportV2` (`PAPER_NATIVE_EXECUTION_REPORT_V2`, revision 2), never an empty ordinary V2 acceptance.
- `paper_native_programs.submit_native_program(...)` consumes the already-validated ROOT assessment, original command and consumption. Its owner hook calls `_current_consumed_owner(boundary, original, lease, consumption, assessment, permit)` with current ROOT semantics.
- `advance_native_programs(db, market_key)` calls `paper_advance_native_algorithms_v2(text)` only when installed. Both reference and depth publishers invoke it while holding `pg_advisory_xact_lock(hashtextextended(market_key,0))`. Children use the same `paper_market_levels.available` as BASE/list orders.
- `PaperPositionEventV2` is a trusted event-role `ACCOUNT_POSITION` observation, qualified by account/product/instrument/leg, units, source sequence and through-sequence. Runtime cannot publish it or mutate position/progress/allocation state.
- `PaperNativeParentReportVerifier(uow, scope, engine=..., archive_bridge=...).verify(original_command_id)` returns exact `PaperNativeParentVerification`, tagged `PAPER_NATIVE_PARENT_VERIFICATION_V1`. It binds the original ledger consumption and financial archive to independent restricted SIM reads, authenticates actual parent/member/allocation/input/event/liquidity identities before normalization, retains raw source before DTO construction, and preserves uncapped fill economics. SIM canonical JSON scope keys and ledger scope hashes remain separate identity domains.
- Verification never authorizes execution, release, posting, protection completion or account-history acceptance. `0021` continues to accept only its existing exact nonempty V1/V2 types; native-parent registration is explicitly absent.

## Migration and recovery

Apply `0013`, frozen `0014`, then additive `0026` in a stopped/disposable PAPER venue. `provision_paper_market_v2` performs this sequence. `0026` is a transactional, one-time migration; do not replay it against an already migrated venue. A failed SQL transaction rolls back; restore/recreate a disposable venue only through the existing controlled provisioning workflow. Do not edit migration history or drop evidence to retry.

The runtime gains SELECT on new native/position/dependency tables, INSERT on immutable mother terms, and EXECUTE only on native acceptance. The event role gains SELECT and native advancement EXECUTE. The existing private matcher definer performs child/progress/position changes; PUBLIC function execution is revoked, and no definer role membership is granted. Runtime is denied advancement and arbitrary progress mutation by genuine PG tests.

The command target constraint permits a null ordinary target only with an exact native mother request. A deferred constraint requires the matching genuine parent/pool before commit. Fixed native list membership keeps a partial unique node index; algorithm children use unique ordinals. Existing rows remain fixed legs.

Recovery queries original mother IDs, immutable allocations, actual child statuses and raw events; it does not resubmit children or infer FLAT/released funds from ACK/stop. Do not downgrade code to readers unaware of native reports after accepting a mother. Quiesce sends and preserve the database for forward repair/reconciliation. There is no destructive down migration.

Frozen SHA256:

- 0013: `efd0a3a2edc1cca12de47baa60be5e7ec90abae155f7a014404a3aadc14f80d2`
- 0014: `df9e0709b7ebb12006549c464251b29f1937aed2efd20182319e9d42a13dd565`

## Evidence

Evidence is committed under `docs/evidence/binance-orders/op-d-additive-native-parent/`, including exact-source launcher manifests, PG logs/XML, summaries and copied-file SHA256 manifest. Every run used immutable runner image `sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7` with offline dependencies. No ordinary environment skip is a PG qualification.

- Funded RED `b4f662d`: 1 passed, 12 failed, 0 skipped; real Spot/USD-M creation rejected `PAPER_PROGRAM_MATCHER_STAGE_REQUIRED`.
- Runtime `91d5e29`: 13 actual PG passed, 0 skipped.
- Independent source RED `af2b3e8`: 13 passed, 6 failed because the source verifier did not exist.
- Further authentic REDs cover wrong SIM/ledger scope domain, forged remaining quantity/time/hash/allocation, and retention when producer projection fails. Original failures are preserved.
- BASE `9bfa0d9`: 23 actual PG passed; V2 source at the same source: 51 actual PG passed.
- V2 compatibility `d775486`: 23 actual PG passed. The old reduce-only collision test now supplies a genuine position snapshot only when the upgraded schema exists; original quantity/collision assertions and old-schema behavior are retained.
- Final native qualification `fdde86014e17`: 24 actual PG passed, 0 failed/errors/skipped (110.51 seconds); original launcher completed exit 0. Includes source retention after producer denial, eight tamper variants and real mother lock-wait expiry.

## Explicit remaining integration boundaries

This is an independently reviewable PAPER component slice, not completion of all 46 targets / 56 acceptance cases. Those requirements remain unchanged.

ROOT must register the distinct typed native-parent acceptance/source extension, reconcile CASH/source-knownness and original reserves/ownership/protection duties, and provide the final host composition. Management stop/child ambiguity, account history and external venue qualifications remain separate. VP execution, live/testnet/accounts and paid product models are not enabled. Exhaustive hedge LONG/SHORT multi-account qualification is not claimed by this cohort.

Quote worker owns additive `0025`. There are no overlapping new table/function names and both components use the same market lock/depth. Combined source evidence is NOT qualified: native/BASE source reads currently cover `paper_market_consumptions`; the integrated host must authenticate quote allocations and native/BASE consumptions together and run actual combined PG cases. No cross-component equivalence or integration pass is inferred from separate green runs.

## Independent review fix round 1

Review finding P2: native disposition projection did not bind market event ID, market hash or occurred-at to the authenticated raw event/source. Fixed in `203596d`: exact raw/stored event time equality, source identity/hash binding, and accepted/source/current observation clock bounds are checked before VERIFIED. Original raw facts remain archived on conflict, without granting any execution or release authority.

Genuine funded partial-IOC regression, `test_native_disposition_projection_requires_authenticated_source`, verifies the authentic `.1` fill and `IMMEDIATE_RESIDUAL_EXPIRED` baseline, then independently forges each of the three fields. Personal actual-PG RED `defbdc3`: 24 passed, 3 failed, zero skipped (123.85 seconds). Fixed actual-PG `203596d`: 27 passed, zero failures/errors/skips (122.26 seconds). Original qualifier handles terminated. Full logs/XML/manifests and hashes are in `fix-round-1/`. Only the native verifier and its tests changed; no unrelated BASE/V2 reruns, migration edits or integration expansion. Scoped independent re-review remains the parent coordinator's next step.
