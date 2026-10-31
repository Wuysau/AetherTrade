# Program request persistence and one-use boundary

This implements the approved ADR-ORDER-001 / ADR-005 application-write unit in
`execution/program_commands.py` and additive migration0012. It creates no venue
transport, signer, account connection, second cash ledger or execution owner.
The final 46-capability/56-scenario target remains unchanged.

`build_creation_write(request,bundle,*,execution_epoch)` binds the complete typed
VenueCreationRequest, original scoped client identities, compilation, allocation,
financial intent, risk basis, full assessment archive and real decision. All are
canonically hashed. `ProgramCommandRepository.append_creation(...,bundle=...)`
requires the actual private financial-kernel witness in the same active UoW,
its whole preassigned command set and unexpired qualification/duty window. It
saves the original program, private request binding, service-scoped identities,
READY row in existing `order_writes_v2`, and `PROGRAM_COMMAND_READY` in the
existing outbox. It never commits. The public host must verify whole attachment
closure before its outer financial transaction commits.

Every external hold is also bound through the existing `FundingLink` in that
same physical SQL transaction, so the existing0009/0011 closure and financial
credit guard applies. Current getters/SQL verify its exact intent/program/hold
and physical transaction. A private semantic uniqueness index permits one
command per compiled request index; MAY requires the exact INITIAL index-to-ID
mapping, so a caller cannot reserve once and append the same request twice.

`write`, `binding` and `request` reload the original records without new IDs or
new authority. The obligation reader explicitly registry-decodes V2/V3, validates
the private V3 financial binding and protected mark, retains same-group unknowns,
and rejects READY regression or an unsupported local terminal claim. Scoped
client identities include environment/account/product/service; legacy ordinary
client/command collisions are checked in both insertion directions. Existing
V2 function bodies, schemas and hashes remain unchanged.

Both insertion triggers acquire the same transaction-scoped advisory registry
lock before their cross-table lookup. This closes the raw-SQL write-skew race;
the single lock also avoids cycles between members of one multi-member command.
It serializes identity registration only, without changing scoped uniqueness.

`ProgramCommandBoundary` requires an explicitly provided verifier-role UoW and
trusted LOCAL fact readers. Both mark and consume reload the actual intake,
source, book, owner/deployment fence, independent process lease, authorization,
current full compiler/target/exposure/RiskEvaluator archive, qualification artifact
references, risk duties, exact original holds and remaining group/strategy
capacity. Missing readers deny. Its whole-set physical closure check also works
when there are no reserved assets. Only this exact original program's quantities
and holds are excluded once; an unrelated scope, family, or UNKNOWN remains a
blocker. Old facts are never treated as a new ALLOW.

The complete immutable external obligation snapshot is validated once within
each boundary transaction. Private pure legacy helpers then calculate legacy
obligations without repeatedly decoding the same program archive. Nothing is
cached across transactions or scopes; current risk/qualification/lease/clocks
and final protected SQL are always independently rechecked. The root integration
must additionally read the genuine local-activation obligation registry once:
same-group unresolved local programs and same-scope active local pools block
before calling these legacy helpers. The activation module is owned by the
parallel worktree; this branch does not install a fake empty registry.

The dedicated NOLOGIN `ledger_program_dispatch_verifier` has no memberships and
receives no financial/control write privileges. Only it executes protected
security-definer MAY/consume functions. Ordinary owner/control/decision SQL
cannot create mark or consumption records. SQL rechecks exact immutable hashes,
current owner/deployment and the original mark's exact lease context. Physical
SQL transaction identity is checked in addition to the caller's UoW UUID: binding,
financial intent/bundle/initial holds and first write share xmin; initial READY
also requires the binding's database xid8 to be the current transaction. Later
MAY/UNKNOWN rows retain the original immutable request identity.

The mark and aggregate-head CAS commit synchronously before a short random token
is returned. Its hash is persisted; consumption is unique, after that commit,
before its independent database TTL, and commits before returning the original
unsigned request. A commit exception returns no permit/request even if the
commit actually succeeded. MAY/UNKNOWN cannot mint another permit. UNKNOWN
returns the original request for original-ID reconciliation; it changes no cash
or reservation responsibility and cannot regress to READY.

Database state cannot observe host/process revocation alone. The independently
loaded process lease and current LOCAL facts are mandatory on both server calls;
the raw verifier SQL functions alone are not host or transport qualification.
Future I/O still needs the existing execution owner's process/external fence
across the call. No production reader or transport is registered by this module.

This slice accepts INITIAL_SUBMISSION external requests only. Staged and local
algorithm child writes require a genuine activation and fresh stage financial
contract; they remain denied. Venue-generated children remain observations.
The existing Native V3 parent/lifecycle qualification guard remains in place.
There is no V3 terminal/unsent-abort authority or automatic reservation release;
the approved V3 write codec deliberately exposes READY/MAY/UNKNOWN only.

Equity BUY current checks additionally require the additive trusted callback
`load_wallet_selection(tx,current_archive,clock)` to return a fresh exact typed
EquityWalletSelection, independently of the stored request. Missing, changed,
stale, foreign scope/quote/profile selections deny. Even an exact selection is
currently denied with `PROGRAM_EQUITY_WALLET_FUNDING_LOCATION_UNBOUND`: the
assessed asset-scoped PortfolioBook does not establish CARD/MAIN cash location.
OP-E must implement and qualify that location binding to the actual assessed
balances; this remains a required engineering item in the complete46-capability
target. No wallet ledger or fallback wallet is introduced. The additive loader
defaults to None; existing ordinary requests and historical request hashes do
not change. Wallet TDD: missing API RED7 then exact independent checks GREEN7.

## Verification record

- Unit creation binding: original missing-module RED9, then GREEN9 in this
  session; original RED is preserved in the session transcript.
- Real OTO staged-write regression: guard removed produced `DID NOT RAISE`
  RED1; restored guard GREEN1, raw XML retained.
- Exact c3bcf28 isolated PostgreSQL candidate:9 passed,1 test setup error before
  its permission assertion (`encode({})` was not a domain record); corrected.
- Exact9e36c8f isolated PostgreSQL candidate:37 passed,1 failed,0 skipped. The
  genuine UNKNOWN failure identified missing aggregate-head advancement in the
  SQL MAY writer; fixed with same-transaction CAS in2991b69.
- 68 unit/codec/reader/request/attachment cases passed before2991b69.
- Exact2991b69 isolated PostgreSQL:48 passed,0 failed,0 errors,0 skipped. This
  source predates the later wallet and funding-link fixes and must not be
  described as complete financial-chain qualification.
- Exactfcf2e12:300-second timeout after47 progress dots; result exit124, accepted
  false. No missing XML/progress output was counted as a pass. The growing
  program suite now has a600-second diagnostic budget (48 tests took281 seconds).
- Exact7005aed semantic duplicate regression: real PostgreSQL RED1, `DID NOT
  RAISE UniqueViolation`; fixed0abd0ba with the semantic index and exact mapping.
- Exact2a3767e with0009+0011 installed: real PostgreSQL RED2, both repository and
  raw owner SQL accepted forged reflection credit. Fixed5a75aae by connecting
  external holds to the existing financial guard through FundingLink.
- 75 unit/codec/reader/request/attachment/wallet cases passed on5a75aae and6e8f250
  source,0 failures. Wallet location remains explicitly blocked.
- Exact0abd0ba,5a75aae and47f06ca runs respectively49/1,51/1,51/1. Their positive
  consumption case exhausted the five-second token while redundantly replaying
  immutable archives. Moving diagnostic assertions alone did not solve it.
  Source6e8f250 removes repeated immutable validation within one transaction;
  the short TTL and all current-fact checks remain unchanged.
- Exacte965c33: real two-connection raw-SQL RED4 reproduced cross-family client
  and command collisions, with either table inserted first. Both transactions
  committed. Source6e8f250 adds the common transaction advisory guard.
- Final exact6e8f250 disposable Docker PostgreSQL:56 passed,0 failed,0 errors,
  0 skipped,exit0 in196.73 seconds. It includes real positive committed MAY/consume,
  four protected concurrency cases, financial credit forgery, genuine process
  exits, failed/uncertain commits, UNKNOWN and V2 compatibility. Evidence lives
  in `docs/evidence/binance-orders/op-c-program-commands/final-green` with source,
  migration, image and resolved-dependency hashes plus raw log/XML.
- Synthetic PAPER artifact readers qualify financial/database engineering only;
  native venue behavior,
  Testnet/Demo/LIVE, fills and actual transport remain NOT_RUN here.

Migration rollback requires stopping new V3 writes and inventorying unresolved
V3 obligations first. Do not drop0012/private records or let a legacy worker
take over V3 UNKNOWN. Old V2 obligations retain the original codec and IDs.

Independent review also identified a future host read-interface constraint:
`program_binding_current` currently treats reservations/funding links as initial
facts. A future legitimate reservation version must not prevent original-ID
query recovery; OP-E must separate initial immutable identity proof from current
financial send eligibility. No current terminal/release authority is inferred.
