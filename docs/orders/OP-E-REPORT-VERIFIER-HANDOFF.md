# Actual PAPER report verifier continuation checkpoint

Current ownership2026-10-29: reports_fresh is sole writer; root independently
reviews. The checkpoint below is historical. Fixed source1d0106e includes
read6e8d32f and economic fixes e2f2f87. Root reviewed both without blocking
findings. Actual fd28878 had13pass/6fail150.74s; e2f2f87 had18pass/1fixture-reason
failure148.94s. Fixed78055fa19PG passed0failure/error/skip145.75s. New test-only
1d0106e adds actual process restart/exact replay/no posting:20PG passed
0failure/error/skip168.66s. Related169checks at1d0106e passed20.07s. All jobs are
terminal; no report job needs polling or restarting. Final evidence lives under
docs/evidence/binance-orders/op-e-report-verifier with source/SHA maps.
Root final read-only review passed the bounded slice, including restart test
and API contract. The fork proof uses the frozen fixture clock and is not a
native/deployment kill-recovery qualification. Evidence7a485dd/proof1f5e5ef
certify all173 raw source/copy/committed blob bytes; no unfinished job remains.

Read API now preserves actual row.revision, SQL payload::text and ORIGINAL stored
payload_hash for fills/dispositions, with Decimal JSON parsing and exact query
remaining/sum. App-canonical market-input hashes remain a separate domain.
No historical rows/hash or migration0013 changed. Matcher peer applied this
reviewed read patch as1c1b90d and retains all semantic/V2/session ownership.

Current integration rules are in OP-E-REPORT-INTEGRATION.md. Source verification
does not imply permission, financial release, complete account history or valid
aggregate projections. Raw cumulative/leaves/overfill/fees remain uncapped;
discrepant observations require risk/reconciliation before existing projection.

Full parent goal remains ACTIVE through C/D/E/F/G,46 capabilities and56 acceptance
cases. The bounded V1 source slice has the source-specific evidence above;
advanced interfaces and full OP-E remain unqualified. Main/archive stay untouched.

## Historical checkpoint before reports_fresh took ownership

## Ownership and current source

This isolated worktree is binance-ope-report-verifier, branch
codex/binance-ope-report-verifier, source5afa535 before this checkpoint. Parent/root
currently owns it. Root integration_next alone owns binance-orders; activation_fresh
owns binance-opd-matcher; observability_fresh owns binance-opf-observability.
No agent may edit another worktree. Fresh ownership will be delegated explicitly.

Base5866ddc contains reviewed original creation recovery and immutable contract
metadata caching. Own97e5004 is the exact native/ledger bridge. Dependency merge
45dc3ad imports fixed OP-D9748f46 only for isolated verifier development; migration0013
and producer were unmodified. Three conflicts retained ALL cross-family race tests,
both collateral_group_id and market test fixture options, and both required suites.
This does not certify or deploy0013 in the integration worktree.

554e2c4 adds independent bounded market/depth/all-rival liquidity checks.
f6d700b adds4 real PG scenarios and required paper-program-reports suite.
5afa535 adds actual PaperProgramReportVerifier, which takes command_id only and
reads exact original/consumed protected ledger records before venue IO. It archives
full source observations and compares full accepted bytes/envelope/IDs/unit/fees/
clocks; native fills retain raw quantity and venue price with FinancialUnit bridge.
Discrepancies retain actual economics/duties; no posting/release/permit occurs.

## Actual verification and live handles

- Unit bridge7RED→43 related GREEN; committed raw maps at2bc4bbd.
- Liquidity21RED plus one untyped-scope RED→59 related GREEN.
- Host-default2RED and strict JSON3RED→latest64 related GREEN9.55s, zero skips.
  Raw files in `.runtime/paper-program-reports/`; later source proof must publish
  them with exact hashes and committed-byte verification.
- Actual PG RED candidate f6d700b first launch a43e61c037a1 TERMINAL0tests:
  Docker daemon absent. Preserve it as infrastructure failure, not test RED.
- Fresh fixed f6d700b job46700 is CONFIRMED LIVE (last output through
  initialize-marker; still awaiting postgres-suites result). Poll SAME handle,
  never restart on observation timeout. Expected intended RED is missing class;
  inspect actual results rather than assuming. Its source predates the service.
- There is NO actual positive verifier PG result yet. New source5afa535 needs a
  new fixed-source run after dependency API corrections; never count producer17
  tests as verifier qualification.

## Required producer read-interface corrections

These were sent directly to activation_fresh before source verification started:

1. Export actual row.revision as PaperMatchedOrder.lifecycle_revision; don't
   invent it from fills or query attempts. New verifier explicitly requires it.
2. Preserve SQL event payload hash domain.0013 stores SHA256(payload::text),
   whereas current query recomputes app canonical JSON. Verify original stored
   hash; do not rewrite historical rows or equate different hash domains.
3. Fractional disposition observed_best_price is JSON NUMERIC. Psycopg turns it
   into float, and current canonical_json rejects floats. Prefer actual SQL
   payload::text bytes/raw hash (both fills and dispositions), retaining old
   rows. Market-input raw SHA remains separately app-canonical by its contract.
4. Query remaining/sum must be exact under changed ambient Decimal precision;
  1-0.000011 at precision3 currently yields false remaining1.00.
5. Initial producer has no equity session semantics. Don't accept Equity SELL
   LIMIT/GTC outside a proved session model; an explicit interim unsupported
   product guard is required until the planned full V2 session support exists.

Do not weaken verifier/PG assertions to work around missing producer fields. Get
the actual concrete committed API. Keep all46 targets; these interim limitations
are tracked work, not deletion of final capabilities.

## PostgreSQL tests

`tests/integration/test_paper_program_reports_postgres.py` imports the real
matcher fixtures. c.owner is ExecutionOwner; use c.service.uow for the financial
UoW. Actual tests perform real admission/MAY/consume/restricted depth matching,
then verify partial0.4 native fill/gross32596, consumed-but-unobserved original ID,
changed request and excess rival allocations. Corruption readers are explicitly
negative observations after genuine production; no forced fills or fake admit.
Verifier must not post ledger.fills by itself. Root integrator owns later same-
ledger EvidenceIngestor/FillLedger/accounting with native/ledger multipliers.

Reproduction: `python tools/qualify_paper_docker.py --commit <fixed-source> --suite paper-program-reports --timeout 1200`.

## Infrastructure recovery facts

Docker29.7.2 is now actually responding again; no containers/volumes were deleted.
Startup failed on stale0-byte AF_UNIX runtime reparse nodes. Windows Move-Item
couldn't access them. After stopping ONLY this task's newly started Desktop tree,
one WSL/Python operation renamed the0-byte runtime nodes to backups under
Docker/run/aethertrade-ipc-backup-20261029T144020; individual sailor backups also
remain. Nonempty files/directories/data were untouched. Root/peers were notified.
Do not reset Docker/factory data or blindly restart old test handles.

## Next work

Inspect real46700 terminal result; bring in reviewed producer read fixes in this
tree, preserve all source/fixture safety tests, run fixed actual PG and correct
real failures through TDD. Strengthen full source/clock/economic/fee/overshoot
cases, including fractional nonfill source, while retaining raw discrepancies.
Then independent review and publish source-specific logs/images/migration maps
with SHA checks against COMMITTED blobs. Coordinate the exact typed result with
integration_next's dispatcher/accounting. Keep TESTNET/LIVE/account/provider calls
off. Continue the full goal after this slice rather than marking it complete.
