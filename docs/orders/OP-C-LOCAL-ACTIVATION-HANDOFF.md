# Local activation implementation checkpoint

Owned checkout: `binance-opc-local-activation`, branch
`codex/binance-opc-local-activation`, clean implementation commit071a33c.
Basebdbd9b0 includes the actual SAME-kernel financial writer and V3 lifecycle
contracts/reducers. Actual local activation is implemented at2d0d310 and verified
with real isolated PostgreSQL. No native command0012 or activation0013 schema is
installed in this branch. No existing task, worktree or source data was removed.

## Actual activation delivered

`execution/local_program_activation.py` exports
`activate_local_program(tx,bundle,*,load_clock)` -> `LocalActivationReceipt`
(`activation_id`, `financial_intent_id`, `bundle_hash`) and
`read_local_activation(tx,activation_id)` -> `ProgrammeActivation | None`.
The receipt and persisted record never authorize an external write. The outer
public admission service owns commit and its final current authority barrier.

The writer requires the live same-transaction kernel witness and claims its
empty external command tuple once. It derives real ParentAuthorityV3, an
ORDINARY LOCAL_PROGRAM_ACTIVATION binding without parent command/client/venue
IDs, role-specific FUNDING_AUTHORITY/PARENT_BINDING proofs, and runs the actual
prepare/bind/activate reducers. Original authority/binding/proofs and the three
states live in existing append-only audit_events; programme and funding links
use existing order_programs/order_funding_links. No old V2 state is reinterpreted.

The durable getter authenticates bounded intake, replays complete compiler,
target, exposure and risk source, derives proof bytes, and compares physical
PostgreSQL xmin across bundle, intent, initial reservations, activation, proof,
state and funding links. Copying the original caller UUID cannot establish this
provenance. Final clock and kernel witness are checked after physical attachment.
An old committed orphan bundle cannot obtain a fresh activation.

`storage/local_programs.py` is the neutral obligation source. Local pools block
another exact-scope target, including zero-asset reductions and expired source
authority. Changed/UNKNOWN/raw-terminal states retain the original allocation
and block the collateral group across accounts/products. Locked reductions see
these obligations too. No current state label or proposal expiry discharges
cash, quantities, per-asset fees or MANAGE_UNTIL_FLAT responsibility.

Evidence: `docs/evidence/binance-orders/op-c-local-activation/environment.json`
binds source2d0d310, image IDs, raw RED/GREEN/JUnit/source hashes. Actual local
activation15/15, Python unit/contracts/intents1470/1470, PostgreSQL financial12,
application obligations18, credit8, closure27, legacy programmes32: zero skips.
Regression PG cohorts ran at56f7cbb before the final authority re-poll; the final
activation/Python suites include that fix. Initial RED5 missing-module failures
and the real first-activation clock-drift RED are retained. Two synthetic legacy
reader fixtures now explicitly return no local-source rows; assertions remain.

Public integration writer `/root/integration_next` consumes these exact APIs.
Public admission positive closure must be tested on its integrated commit.
No scheduler, local child admission, external V3 permit/transport qualification,
or production lifecycle reader is installed by this activation slice. Actual
owner-transfer integration is not covered here; durable original owner/epoch
retention and denied direct owner overwrites are covered. All46/56 and OP-D/E/F/G
remain the full parent objective; this is no native/TESTNET/LIVE qualification.

## Completed and actually verified

`risk/program_financial_admission.py` now issues a private live witness only
after successful `_reserve_program` writes. APIs:

- `current_program_bundle_witness(tx,bundle)`: validates active same transaction,
  actual stored bundle/intake/intent, current owner/fence, unchanged original book
  and initial funding projections.
- `program_bundle_command_ids(tx,bundle)`: exact whole preassigned tuple, even
  for zero-asset programmes; never infer identities from holds.
- `claim_program_attachment(tx,bundle,mode=...,command_ids=whole_tuple)`:
  EXTERNAL_REQUESTS versus LOCAL_ACTIVATION, immutable and mutually exclusive.
  Identical repeated external claims support several request appenders. This is
  not completion/permission; the outer service checks all actual attachment.

Actual unit2, PostgreSQL attachment3 and financial regression12 pass with zero
failures/skips. Raw logs/XML/source/image hashes are committed under
`docs/evidence/binance-orders/op-c-program-attachment/`. No pending process.
Command writer `/root/command_fresh` and root writer `/root/integration_next`
have the commit/API. Parent/root only performs coordination/read/QA.

## Approved implementation requirements (implemented above)

1. Internal module `execution/local_program_activation.py` APIs:
   `activate_local_program(tx,bundle,load_clock=...)` returning a typed receipt
   with stable `.activation_id`, and `read_local_activation(tx,activation_id)`.
   Outer transaction is owned by the actual public admission service. Receipt
   and stored source remain non-authorizing. Caller/provider True is no proof.
2. Require the actual current same-TX financial witness plus bounded intake,
   exact SQL bundle/initial holds, current source/fence/owner and healthy current
   clocks/expiry. Never reserve twice or attach an old orphan bundle after its
   transaction committed. Existing activation replay restores original metadata
   without renewing authority. Reject reused/conflicting programmes or funding.
3. Derive existing ParentAuthorityV3 from real local TWAP/VP ALGORITHM_ENVELOPE:
   namespaceORDINARY, local APPLICATION_GENERATED_CHILD, single SUM pool, exact
   native remaining quantity, unit/ledger bridge, current actual position,
   source price/notional/fee bounds, exact admission/compilation/allocation hashes.
   All native parent command/client/venue IDs are None. Zero-asset derivative
   reduction uses OWNED_POSITION_REDUCTION plus actual position allocation proof.
4. Use actual existing prepare/bind_parent/activate reducers with
   ParentBindingV3(binding_kindLOCAL_PROGRAM_ACTIVATION). Role-specific
   LifecycleProofV3 material must be independently derived from the bounded
   intake and live actual kernel source, not owner audit existence or a generic
   validation callback. No role/control grant or signer is added.
5. Store typed activation, original authority/binding/proof/state in existing
   append-only audit_events, programme in existing order_programs. Do not insert
   V3 state into old V2 state records. Link positive holds once with existing
   order_funding_links and preserve credit/closure restrictions. No second ledger.
6. Add a neutral typed read-side obligation source (avoid writer/risk import
   cycles). Active/expired/terminal-unproven local envelopes block another exact-
   scope target, even with no assets. UNKNOWN/recovery sources also block their
   collateral group across accounts/products. No activation ID may masquerade
   as an external command_ref. Expiry or raw terminal labels never erase pools,
   funds or MANAGE_UNTIL_FLAT responsibility.
7. Actual scheduling and external child submission are later stages: every child
   still needs its own fresh admission, durable command, owner and one-action
   permit. This slice creates a real internal activation, not a fake mother.

## Test construction pointers

Existing `tests/integration/test_program_financial_admission.py::real_case` uses
the real kernel, bounded intake and synthetic qualification archive. Extend its
test-only default-preserving parameters for local TWAP/VP. For non-atomic plans,
also patch `test_program_exposure.inputs` with the same scoped fixture used for
`test_program_risk_assessment.inputs`/`test_program_target_allocation.inputs`;
otherwise make_plan creates another scope and correctly fails hash binding.
Do not weaken production source/risk/qualification checks to fix fixture scope.

TDD real PostgreSQL: local TWAP/VP create/restart/replay, no fake native IDs or
external rows/permits, exact funding/SUM pools, zero-asset quantity obligation,
scope/owner/source/clock/budget/identity conflicts, atomic failure rollback and
UNKNOWN/group blocking. Synthetic qualification remains engineering evidence,
not native/TESTNET/LIVE qualification. No paid product model calls/accounts.
