# OP-D sole-writer release handoff

Parent explicitly accepted this batch ending so a fresh cash implementer can use
the concurrency slot. After this handoff commit, activation_fresh RELEASES write
ownership and performs no further code/root/cash-tree writes. The full OP-D and
46-capability/56-scenario objective remains ACTIVE and incomplete; this is neither
goal completion nor a user pause. A fresh root lead must explicitly assign the
next writer. Preserve this SAME worktree/branch and all committed WIP; do not reset,
clean, recreate or cherry-pick earlier C dependencies blindly.

## Tree and exact checkpoints

- Tree: `D:/Projects/Python/AetherTrade/TradingHandoff/AutoDispatchWorktrees/binance-opd-matcher`.
- Branch: `codex/binance-opd-matcher`; original base `aff4aa8`.
- Current code: `ca57f791d0978caffbec3cadf5accd0f90281b02`. All code committed.
- Last whole advanced cohort: `89469c81136be6fb63cc509c4aae1181c4d4aa4d`, actual30 V2 PG passed, zero failed/error/skipped,140.94s.
- Earlier `e45a452494f0a4b22f62d972cebfb4c4d660d176`: actual27 V2 PG passed,126.39s; actual19 unchanged V1-on0014 cases passed,86.73s.
- Frozen verifier baseline: `1b12ed9329b08f7adc505f762689b7e633c09754`, evidence/docs `6d3e765`: actual21 V2 +19 V1-on0014 +59 legacy PG, all zero failure/error/skip;1525 Python unit/contracts/intents passed.178 raw artifacts independently verified against committed Git blobs.
- Baseline0014 SHA256: `df9e0709b7ebb12006549c464251b29f1937aed2efd20182319e9d42a13dd565`.

Migration0013 remains unchanged. **0014 is still UNFROZEN**; integration_next owns
serial review/integration. No business-ledger migration was changed. Do not treat
the newer native-parent/position family as inheriting baseline qualification.
reports_fresh received the exact1b12 API/hash and owns its separate V2 verifier.

## Current RED, without a passing claim

The current position-snapshot increment is not ready for qualification. Its actual
test is
`test_actual_position_snapshot_and_fill_projection_constrain_native_reduce_only_without_lending_another_position`.

1. `b7d7aaa`: genuine missing-DTO RED.
2. `96de153`: actual PG RED, private definer lacked SELECT on existing immutable
   `paper_market_consumptions` while reconstructing the position fill tail.
3. `ca57f79`: the necessary SELECT-only grant was added. The actual short PG test
   now reaches a real0.2 fill but still fails: venue-position projection is1,
   expected0.8. **This remains RED, not fixed.**

A concrete root-cause candidate from code inspection is PostgreSQL composite NULL
semantics: valid `paper_venue_positions_v2` rows contain nullable `discrepancy`.
The `position_state IS NOT NULL` predicates at current0014 lines431,609,894 can
therefore skip dependency copying and quantity update despite a real row. Use a
nonnullable key/FIND result and prove it with the retained RED before claiming a
fix. This candidate has not been executed/fixed in this batch. The sibling stale/
conflicting-watermark test was added but is **NOT_RUN** after this increment.

Parent explicitly requested no new long test or feature expansion at release.
No full current-source or post-child legacy/Python gate was started. Keep the
working30-case checkpoint and frozen verifier baseline separate from current RED.

## Functional code and APIs

`engine/paper_market.py` retains actual `ProgramCommandBoundary.consume` after
committed MAY, closed business transaction before SIM effects, owner/fence/book
and same-permit clock checks after the actual market lock and before commit.
`e45a452` adds exact256 trapped arithmetic around public `submit_creation` after
actual prec3 RED copied protected0.1119 as0.112; both ordinary/native cases passed
afterward. This one-line fix is independently applicable to root V1.

`engine/paper_market_v2.py` contains baseline V2 DTOs/reference/clock/calendar/
ownership inputs, runtime state, source decoder and provisioning. Baseline report
includes real persisted venue order IDs/revisions, complete original request/write/
compilation/rules/model/current protected envelope, raw SQL event bytes/stored SHA,
native OCO/OTO/OTOCO parent/member IDs, explicit prevented quantities and actual
working-full-fill source. Native lists use one real mother command, ordinary child
observations and real atomic cancellation events; no extra application commands.

`query_market_evidence` returns exact per-order causal input dependencies, previous
depth, trailing extrema samples, calendar and working-fill dependencies, plus ALL
competing consumptions/preventions for those IDs. App-canonical market input hash
and original SQL JSONB-text fill/disposition hash are distinct domains. Bounds
apply while streaming before append:4096 inputs,100000 allocations/preventions,
16MiB; overflow blocks explicitly, never truncates. Shared member reads were
extracted to `_read_creation_members`, also streamed and globally bounded.

`engine/paper_native_programs.py` is the newer, separately evolving family:

- Genuine native parent has a NULL command target, original nonempty parent ID,
  empty original assigned members, immutable parent terms and finite pool in the
  same SIM commit. A deferred constraint requires complete backing. Ordinary and
  legacy targets retain NOTNULL-equivalent constraints. No fictitious Mother or
  ordinary order is created at acceptance.
- Parent-only `PAPER_NATIVE_PARENT_TERMS_V2`/`PAPER_NATIVE_PROGRAM_REPORT_V2` are
  preserved. Explicit `PAPER_NATIVE_EXECUTION_TERMS_V2` revision2 has a declared
 60s/IOC/no-recycle TWAP model. Older parent-only records never acquire generation
  semantics silently. New output is `PAPER_NATIVE_EXECUTION_REPORT_V2` revision2.
- `PAPER_NATIVE_CHILD_TERMS_V2` is backed by actual immutable native allocation and
  member rows, unchanged original mother request, actual source event/ordinal,
  real ordinary child IDs/quantities and protected parent envelope. Children are
  generated once by restricted controller-driven SQL, not by application commands
  or a caller-provided fill callback.
- Actual Spot and USD-M tests: native original1 -> allocated0.6, executed0.4,
  known unused0.2, unallocated future0.4 after three inputs; exactly one mother
  command. Runtime cannot generate; repeated match/event cannot allocate twice.
- `ca57f79` adds the still-failing position observation/projection increment.
  Its new `ACCOUNT_POSITION` source is scoped to account/product/instrument/leg/
  native unit/multiplier/covered market watermark. It is not cash/owner/risk
  authority. Source missing/mismatched/conflicting remains held. No fee-to-contract
  or cash-to-quantity conversion is allowed. Independent verification and the
  current projection bug must be completed before qualification.

Current APIs, including the new native family, are not a production qualified
reader/transport registration. V1-configured readers explicitly reject unsupported
parent shapes and retain duties rather than treating zero ordinary rows as an
empty resolved programme. Never reject accepted GTC/working native children just
because the original creation permit/proposal is now old.

## Integration constraints still required

1. Root has the newer `boundary.recheck_consumed(original_permit,lease)` and
   immutable `mark.context.permit_window`. Adapt **four** private guard calls
   (two scalar, two native) with the original permit at serial merge. Do not
   reconstruct original labels from current TTL configuration or consume again.
2. Own financial source reader still restores scalar
   `ProgramFinancialAssessmentArchive`; root's current quote source/context union
   and shared consumed-boundary module are not merged here. Native MARKET quote
   budgets must remain distinct from actual BASE fills/origQty. Never invent
   origQty from interval.upper or force FILLED from quote-budget depletion.
3. Local TWAP/VP/list children still need a real child-stage financial source,
   current owner/facts, existing parent-pool coverage counted once, same-TX claim,
   independent durable external command and one-use permit. Existing compiler
   `LOCAL_ALGORITHM` has no external endpoint and current boundary gates initial
   requests. Do not bypass this with parent permits, fake native membership or
   callback success. Integration_next was notified; root owns the shared cash/
   allocation/stage interface.
4. Keep root's newer C request uniqueness/funding/obligation/per-node-policy and
   metadata-cache changes during integration; do not overwrite current root CI
   suites. Own branch already contains selected older C dependency fixes; they
   are not new root work. Root must do the combined final qualification.
5. Baseline1b12 native per-node exit cap is current price/compiled gross bounded
   by programme gross **and separately the actual individual policy limit**.
   Root found/was fixing the earlier shared C pending-node policy gap; total
   programme gross never substitutes for individual order policy.

## Remaining complete scope

Full46/56 remains required. Continue genuine position/unit/hedge/source conflict
and raw dependency proof; fix current RED; complete USD-M conditional native
parent/ordinary-child identity, priceProtect rules, native VP volume/urgency,
native stop/UNKNOWN/pool/fork races, local stage admission/scheduling, dynamic
price-range rules, full peg/priceMatch/iceberg/STP combinations/trade groups/
TRANSFER, per-asset/base-receipt/third-asset/equity fees, OPO/OPOCO actual locked
net receipt/LOT_SIZE/dust, native batch partial outcomes, cancel/replace/keep-
priority, actual equity EXTENDED/24H/halts/DST behavior, and V2 host source
verification/normalization/atomic ledger/protection ingestion. Only then finish
the combined56 matrix and original safety gates. SOR/adapter/native qualification
remains separately required; synthetic PAPER is never promoted to native/LIVE.

`OP-D-MATCHER-MATRIX.md` deliberately describes only frozen1b12 component evidence,
not final whole-scenario PASS. The newer native TWAP component is partial evidence
for ORD-29/31/48; new position projection is still RED. No catalogue item was
deleted or silently downgraded to MARKET.

## Evidence, processes and runner recovery

- Old baseline: `docs/evidence/binance-orders/op-d-matcher-v2/` (178 verified raw
  artifacts,42 source-specific runs). Original interrupted63725 is8pass/1 lost PG
  connection, not green; later fresh27e3add9-case run passed.
- New: `docs/evidence/binance-orders/op-d-after-1b12/` (87 raw artifacts,22 separate
  source-bound runs,6 checkpoint hash maps), including current permission and
  position-arithmetic RED. Do not sum different source versions as one full gate.
- All test handles owned by this agent are terminal. Latest short test invocation
  returned exit1 with the exact quantity1 versus0.8 failure. No test was restarted
  from an observation timeout.
- Own runner `aethertrade-qualification-runner-c533ccc29867` was verified to have
  only `sleep14400` (no tests), then intentionally stopped at release. Exit137
  is that idle runner stop, not a new pytest failure. Stop command handle18235
  terminated exit0. Own private PG `aethertrade-qualification-pg-c533ccc29867`
  remains running with failed rows available; no host ports. Other agents' jobs/
  resources were untouched.
- Runtime directory: `.runtime/paper-qualification-c533ccc29867`. Fixed clone is
  `/tmp/source`; source bundles and `.runtime/run_matcher_fixed.py` invocation
  helper remain ignored. That helper starts ONLY the owned runner, checks out an
  exact committed source and uses a fresh per-module database/marker. Never
  change its source while a job is live. No env/DSN file is committed or printed.
- Python: `D:/Projects/Python/AetherTrade/AetherTrade/.venv/Scripts/python.exe -X utf8`.
  Runner image is pinned sanitized offline
  `sha256:768aad7887ee385c55b82ac30b6d7b1d403d25f62aca8d0bc8cae0939fb9a9b7`;
  PG image `sha256:18cfe3ef5e6815560c98237d6216d1e5119702fb0f3894c8785dd58b8bbe5d73`.
  No TLS/TTL weakening, live accounts, TESTNET, paid models or operational envs.

No root/cash/other-agent tree was edited. The old local-activation tree is also
read-only; its completed implementation/evidence remains independently pinned.
