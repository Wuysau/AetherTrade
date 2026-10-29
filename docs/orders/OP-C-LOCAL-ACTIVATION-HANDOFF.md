# Local activation implementation checkpoint

Owned checkout: `binance-opc-local-activation`, branch
`codex/binance-opc-local-activation`, clean implementation commit071a33c.
Basebdbd9b0 includes the actual SAME-kernel financial writer and V3 lifecycle
contracts/reducers. No native command0012 or activation0013 schema is installed.
The witness/attachment dependency is delivered; actual local activation is NOT
implemented yet. No existing task, worktree or source data was removed.

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

## Approved next implementation (not another design approval)

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
