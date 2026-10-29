# OP-C lifecycle v3 pure reducer report

The additive2026-10-29 lifecycle now models native quantity units/multipliers and disjoint allocation groups. Frozen V2 lifecycle code, records and all36 pre-existing public/package schema hashes remain unchanged. Existing production V3 lifecycle containment and send denial remain intact.

## Contract and proof boundary

`ParentAuthorityV3` is immutable pre-ACK economic authority, without a venue ID. It binds program/compilation/target-allocation/funding-admission hashes, original native mother command/client IDs, ownership, units, budgets and funding references. `ParentBindingV3` subsequently proves the original identity's venue receipt; local activation has no invented native mother. Ordinary ATOMIC membership can refer to its actual single command/order.

Zero-asset owned reductions use explicit `OWNED_POSITION_REDUCTION` and a position-allocation hash, with empty reservation/requirement tuples. No dummy zero-value reservation is created. Preparation still requires an authenticated funding proof, a nonzero owned position, closing direction and a bounded allocation; positive or unknown fee requirements reject this basis. Opening claims cannot acquire authority from empty tuples.

`reader(role, ref)` must be a separately authenticated local verifier archive. No implementation is installed. The reducer requires archive bytes matching the reference hash, strict proof codec, exact subject/context, role, scope, validity and source material. Objects, booleans, model hints and source labels alone fail. Funding proofs cite exact compilation, allocation and admission materials plus owned-position allocation for zero-asset reductions. Native exclusivity cites matching rules/build; application membership cites its actual command hash.

Child membership is independent of child events. It uses an explicit outbound-client map, authenticated native-parent child query, or actual application command. Events cannot choose arbitrary node/group assignments. Native groups require exact authority/binding/membership/service/template/rule/build proof. Local software groups remain summed.

## Reducer behavior

- `prepare`, `bind_parent`, `register_membership`, `register_exclusivity`, `activate` establish data bindings without sends or releases. Both fixed memberships can register while BOUND before pair-proof validation, avoiding circular OCO bootstrap.
- `apply_child` preserves authenticated reports and cumulative trade details. Exact notional is quantity × price × multiplier. A mother envelope is not charged again as a child.
- Without a verified native guarantee, alternatives sum. With an exact ONE_EXECUTING_MEMBER guarantee, independent alternative remaining amounts use max; after one actual member executes, only that member's future alternative is eligible under the proven guarantee. Raw sibling status/leaves remain unchanged. There is no min(raw_remaining, cap) clipping. Actual executions in both members invalidate exclusivity and trigger summed reconciliation.
- Algorithm children debit an explicit future pool once. Canceling a child does not recycle its allocation automatically. An authenticated complete parent query may revise the pool only with cumulative-plus-remaining quantity/notional/fee checks. Missing pool quantity produces unknown risk, not zero.
- Parent STOPPED does not settle children. COMPLETED needs full inventory and terminal, economically complete children. Separate accepted-parent status preserves stop/cancel fences even while risk status is RECONCILIATION_REQUIRED. Exact submicrosecond timestamps and report sequences reject stale reports.
- Unknown, late, contradictory, oversized or unbound reports retain their original IDs and source proof reference in `LifecycleResultV3.retained`. Attributable over-budget facts remain in the projection; unattributed effects explicitly make economic/protection bounds unknown. No fictitious sibling cancellation or fill is produced.
- `review_projection` can assess a subsequently verified pair proof and show a currently consistent projection while historical reconciliation restrictions remain sticky. It never resolves a risk lock, releases funds, resubmits an order or grants permission.

## Fees, receipts and protection

Fee asset amounts, receipt debit and owned-position debit are independent. A Spot fee paid from pre-existing base inventory may leave the OPO locked receipt unchanged while reducing owned position quantity. Derivative collateral fees never subtract contracts. Unknown fee data is None and forces reconciliation; normalized fee debits are nonnegative, so source signed fee/rebate conventions must be proven before normalization, never blindly abs().

OPO/OPOCO receipt allocation requires full working fill, complete fee information, exact source-fill digest, actual net/locked amount, authenticated rule/lot evidence and explicit unused/dust disposition. PENDING_NEW and ACK do not activate a pending leg or prove protection. The reducer does not invent rounding, unlock funds or erase dust. Required protection tracks the declared-unit observed position; its status remains UNVERIFIED_DUTY or RECONCILIATION_REQUIRED, never a claim that a pending order already protects it.

## Verification

Stage1 commit1443f6c registered the explicit contracts and schemas. RED evidence includes the frozen-V2 nonunit and OCO capacity gaps, initially missing v3 reducers/registry, required zero-asset variant, missing original quantity, delayed identity-proof timing, reconciliation overlay losing the stopped-parent fence, and an owned-reduction certificate lacking position-allocation material. These are covered by current tests; V2 was not weakened.

Final verification: **50 focused tests passed;1095 contracts/intents/unit tests passed**, zero skips and both exit0. Public/package schemas match exactly; V2 typed roundtrip/hash and all36 pre-existing schema bytes are checked. A production repository probe still rejects V3 parent binding before any write.

```text
python -m pytest tests/contracts/test_order_lifecycle_v3.py -q --tb=short
python -m pytest tests/contracts tests/intents tests/unit -q --tb=short
```

Raw summaries/JUnit evidence and the intentional frozen-V2 RED probe are under `docs/evidence/binance-orders/op-c-lifecycle-v3/`. Official source findings are in `official-sources.md`. Tests use synthetic protected-role archive fixtures and actual pure implementation, not live/native execution evidence.

## Remaining integration and qualification

No production verifier, native source normalizer, database binding/migration, transport, permission issuer or ledger writer was added. TESTNET/LIVE remain disabled by these operations. Native/account/model calls were not made. Existing V3 containment must remain until common financial admission and native qualification are complete.

Raw algo aggregates are not individual fills; a qualified normalizer must obtain genuine trade identities and fee attribution. Signed fees/rebates and fill corrections without reviewed reconciliation remain restricted. Full reconciliation resolution, authority amendments, production archived-state verification and high-volume/paged state qualification are subsequent integration gates. The codec keeps its1MiB record budget; these tests do not certify unbounded algorithm histories or crash/venue behavior. This report does not mark ORD/native qualification passed.
