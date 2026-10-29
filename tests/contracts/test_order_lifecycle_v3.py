"""Pure lifecycle v3 proofs; no database, accounts, native qualification or sends."""
from dataclasses import replace
from decimal import Decimal as D
from hashlib import sha256
from pathlib import Path
import json
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_lifecycle_v3 import *
from trading_system.contracts.canonical import canonical_hash,domain_payload

NOW='2026-10-29T06:00:00Z'
LATER='2026-10-29T06:30:00Z'
SCOPE=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='v3-fixture',product=t.Product.SPOT,instrument_uid='fixture:BTCUSDT',venue_leg='BOTH')

def ref(name='source'):
    return t.EvidenceRef(id=name,hash='b'*64,environment=t.Environment.PAPER,source='SYNTHETIC_VERIFIER_FIXTURE',visible_at=NOW)

def authority(*,exclusive=False,local=False,algorithm=False,multiplier='1',product=t.Product.SPOT):
    scope=replace(SCOPE,product=product)
    node=NodeAllocationV3(node_id='a',group_id='g',side='BUY',origin='APPLICATION_GENERATED_CHILD' if local else 'VENUE_GENERATED_CHILD',
        child_mode='ALGORITHM' if algorithm else 'FIXED_LEG',quantity_mode='FIXED',quantity_limit='1',price_upper='100',activation='IMMEDIATE',source_node_id=None)
    nodes=(node,replace(node,node_id='b')) if exclusive else (node,)
    group=AllocationGroupV3(group_id='g',node_ids=tuple(n.node_id for n in nodes),aggregation='NATIVE_EXCLUSIVE' if exclusive and not local else 'SUM',
        quantity_limit='2' if exclusive and local else '1',notional_limit='200' if exclusive and local else '100')
    return ParentAuthorityV3(contract_version=VERSION,authority_id='auth',program_id='program',program_hash='1'*64,compilation_hash='2'*64,
        target_allocation_hash='3'*64,funding_admission_hash='4'*64,scope=scope,venue='BINANCE',namespace='ORDINARY' if local else 'ORDER_LIST' if exclusive else 'EXECUTION_ALGO',
        template='OCO' if exclusive else 'TWAP' if algorithm else 'ATOMIC',execution_mode='LOCAL_SYNTHETIC' if local else 'VENUE_NATIVE',
        parent_command_id=None if local else 'original-command',parent_client_id=None if local else 'original-client',owner_strategy_id='owner',ownership_epoch=1,
        authorization_id='grant',funding_intent_id='funding',reservation_ids=('hold',),funding_requirements=(AssetBudget(asset='USDT',amount='210'),),
        units=QuantityTermsV3(quantity_unit='CONTRACT' if product==t.Product.USD_M_PERP else 'BTC',position_unit='CONTRACT' if product==t.Product.USD_M_PERP else 'BASE',
            quantity_to_position='1',notional_multiplier=multiplier,base_asset='BTC',quote_asset='USDT',position_asset_per_unit=None if product==t.Product.USD_M_PERP else '1'),
        unit_evidence=ref('unit'),rule_revision=1,rule_snapshot_hash='5'*64,adapter_build_digest='6'*64,nodes=nodes,groups=(group,),starting_position_quantity='0',
        maximum_notional='210',fee_budgets=(AssetBudget(asset='USDT',amount='10'),AssetBudget(asset='BTC',amount='1')),submission_valid_until=LATER,budget_valid_until=LATER,
        management_policy='MANAGE_UNTIL_FLAT',protection_required=True)


def test_explicit_v3_authority_roundtrip_without_a_venue_id():
    a=authority();codec=VersionedOrderCodec();body=codec.encode('parent-authority',a)
    assert 'parent_venue_id' not in body and body['units']['notional_multiplier']=='1'
    assert codec.decode('parent-authority',body)==a
    bad=dict(body,parent_venue_id='guessed')
    with pytest.raises(Exception):codec.decode('parent-authority',bad)


def test_all_existing_schema_bytes_are_unchanged():
    root=Path(__file__).resolve().parents[2]
    hashes=json.loads((root/'docs/evidence/binance-orders/op-c-lifecycle-v3/schema-baseline.json').read_text(encoding='utf-8'))
    for path,digest in hashes.items():assert sha256((root/path).read_bytes()).hexdigest()==digest,path


def test_every_explicit_new_kind_has_an_exact_packaged_schema():
    from trading_system.contracts.order_codec import KINDS_BY_VERSION
    kinds={'parent-authority','parent-binding','child-membership','native-exclusivity','child-observation',
        'parent-observation','receipt-allocation','lifecycle-proof','program-state'}
    assert kinds<=set(KINDS_BY_VERSION[VERSION])
    root=Path(__file__).resolve().parents[2]
    for kind in kinds:
        name=kind+'.v'+VERSION+'.schema.json'
        assert (root/'contracts'/name).read_bytes()==(root/'src/trading_system/schemas'/name).read_bytes()


def test_v2_records_keep_original_type_and_canonical_hash():
    from test_order_lifecycle import authority as old_authority,event as old_event,state as old_state
    from trading_system.contracts.order_lifecycle import ParentAuthority,ChildObservation,ProgramState
    codec=VersionedOrderCodec()
    for kind,record,cls in [('parent-authority',old_authority(),ParentAuthority),('child-observation',old_event(),ChildObservation),('program-state',old_state(),ProgramState)]:
        raw=codec.encode(kind,record);digest=canonical_hash(raw)
        restored=codec.decode(kind,raw)
        assert type(restored) is cls and codec.hash(kind,restored)==digest
        with pytest.raises(Exception):codec.decode(kind,raw,version=VERSION)


def test_local_activation_cannot_claim_native_parent_or_exclusive_discount():
    a=authority(exclusive=True,local=True)
    with pytest.raises(ValueError,match='Local activation'):
        replace(a,parent_command_id='invented-parent')
    with pytest.raises(ValueError,match='Native list exclusivity'):
        replace(a,groups=(replace(a.groups[0],aggregation='NATIVE_EXCLUSIVE'),))
    native=authority(exclusive=True)
    with pytest.raises(ValueError,match='Exclusive fixed legs'):
        replace(native,nodes=tuple(replace(n,origin='APPLICATION_GENERATED_CHILD') for n in native.nodes))


def test_derivative_position_units_cannot_be_declared_as_collateral_assets():
    a=authority(product=t.Product.USD_M_PERP,multiplier='0.001')
    with pytest.raises(ValueError,match='Derivative collateral'):
        replace(a,units=replace(a.units,position_asset_per_unit='0.001'))


def test_node_groups_cannot_double_assign_or_hide_a_member():
    a=authority(exclusive=True)
    with pytest.raises(ValueError):replace(a,groups=(a.groups[0],replace(a.groups[0],group_id='other')))
    with pytest.raises(ValueError):replace(a,groups=(replace(a.groups[0],node_ids=('a',)),))


def test_zero_asset_reduction_contract_needs_no_dummy_zero_reservation():
    a=authority(product=t.Product.USD_M_PERP)
    reduced=replace(a,reservation_ids=(),funding_requirements=(),fee_budgets=(),
        funding_basis='OWNED_POSITION_REDUCTION',position_allocation_hash='8'*64,starting_position_quantity='1',
        nodes=(replace(a.nodes[0],side='SELL'),))
    body=VersionedOrderCodec().encode('parent-authority',reduced)
    assert body['reservation_ids']==[] and body['funding_requirements']==[]


class ProofArchive:
    """Synthetic independently controlled role archive, not an account reader."""
    def __init__(self):self.records={};self.counter=0
    def read(self,role,proof_ref):return self.records.get((role,proof_ref.id))
    def prove(self,role,subject,a,binding=None,membership_hash=None,until=LATER):
        from trading_system.contracts.order_lifecycle_v3_reducer import proof_context
        self.counter+=1
        source=getattr(subject,'source_evidence',a.unit_evidence)
        materials=set()
        if role=='FUNDING_AUTHORITY':
            materials={a.compilation_hash,a.target_allocation_hash,a.funding_admission_hash}
            if a.funding_basis=='OWNED_POSITION_REDUCTION':materials.add(a.position_allocation_hash)
        elif role=='NATIVE_EXCLUSIVITY':materials={a.rule_snapshot_hash,a.adapter_build_digest}
        elif role=='CHILD_MEMBERSHIP' and subject.origin=='APPLICATION_GENERATED_CHILD':materials={subject.command_hash}
        sources=(source,*(replace(ref('material-'+h[:12]),hash=h) for h in sorted(materials)))
        p=LifecycleProofV3(contract_version=VERSION,proof_id='proof-'+str(self.counter),role=role,scope=a.scope,
            subject_hash=canonical_hash(domain_payload(subject)),context_hash=proof_context(a,binding,membership_hash),
            valid_from=NOW,valid_until=until,source_refs=sources)
        raw=json.dumps(VersionedOrderCodec().encode('lifecycle-proof',p),sort_keys=True).encode()
        proof_ref=t.EvidenceRef(id=p.proof_id,hash=sha256(raw).hexdigest(),environment=a.scope.environment,source='ISOLATED_ROLE_ARCHIVE',visible_at=NOW)
        self.records[(role,proof_ref.id)]=raw
        return proof_ref


def ready(a,*,native_proof=False):
    from trading_system.contracts.order_lifecycle_v3_reducer import (
        prepare,bind_parent,register_membership,register_exclusivity,activate,membership_digest)
    archive=ProofArchive()
    prepared=prepare(a,archive.prove('FUNDING_AUTHORITY',a,a),reader=archive.read,now=NOW)
    assert not prepared.restrictions
    local=a.execution_mode!='VENUE_NATIVE'
    binding=ParentBindingV3(contract_version=VERSION,binding_id='bound',authority_hash=canonical_hash(domain_payload(a)),scope=a.scope,venue=a.venue,namespace=a.namespace,
        binding_kind='LOCAL_PROGRAM_ACTIVATION' if local else 'VENUE_ACCEPTANCE',parent_command_id=a.parent_command_id,parent_client_id=a.parent_client_id,
        parent_venue_id=None if local else 'native-parent',bound_at=NOW,source_evidence=ref('ack'))
    result=bind_parent(a,prepared.state,binding,archive.prove('PARENT_BINDING',binding,a,binding),reader=archive.read,now=NOW)
    assert not result.restrictions
    for node in a.nodes:
        if node.child_mode=='ALGORITHM':continue
        app=node.origin=='APPLICATION_GENERATED_CHILD'
        root=app and not local and a.template=='ATOMIC' and a.namespace=='ORDINARY'
        m=ChildMembershipV3(contract_version=VERSION,membership_id='member-'+node.node_id,authority_hash=canonical_hash(domain_payload(a)),binding_hash=canonical_hash(domain_payload(binding)),
            scope=a.scope,venue=a.venue,parent_namespace=a.namespace,child_namespace='ORDINARY',node_id=node.node_id,group_id=node.group_id,child_order_id=binding.parent_venue_id if root else 'child-'+node.node_id,
            child_client_id=a.parent_client_id if root else 'client-'+node.node_id,origin=node.origin,mapping_basis='APPLICATION_COMMAND_ID' if app else 'NATIVE_PARENT_CHILD_QUERY',command_id=a.parent_command_id if root else 'local-'+node.node_id if app else None,command_hash='7'*64 if app else None,
            durable_record_ref='committed:'+node.node_id if app else None,source_evidence=ref('membership'))
        result=register_membership(a,result.state,m,archive.prove('CHILD_MEMBERSHIP',m,a,binding),reader=archive.read,now=NOW)
        assert not result.restrictions
    if native_proof:
        for g in a.groups:
            if g.aggregation!='NATIVE_EXCLUSIVE':continue
            guarantee=native_guarantee(a,result.state,g.group_id)
            result=register_exclusivity(a,result.state,guarantee,archive.prove('NATIVE_EXCLUSIVITY',guarantee,a,binding,guarantee.membership_hash),reader=archive.read,now=NOW)
            assert not result.restrictions
    result=activate(a,result.state,reader=archive.read,now=NOW)
    return result,archive


def child_event(a,state,node_id='a',*,quantity='1',cumulative='0',status='SUBMITTED',fills=(),sequence=1,at=NOW):
    m=next(m.membership for m in state.memberships if m.membership.node_id==node_id)
    return ChildObservationV3(contract_version=VERSION,observation_id='event-'+node_id+'-'+str(sequence),membership_id=m.membership_id,scope=a.scope,venue=a.venue,
        parent_namespace=a.namespace,child_namespace='ORDINARY',parent_client_id=state.binding.parent_client_id,parent_venue_id=state.binding.parent_venue_id,
        child_order_id=m.child_order_id,child_client_id=m.child_client_id,side=next(n.side for n in a.nodes if n.node_id==node_id),status=status,
        quantity_unit=a.units.quantity_unit,quantity=quantity,cumulative_quantity=cumulative,leaves_quantity=None if quantity is None else str(amount(quantity)-amount(cumulative,zero=True)),
        fills=fills,report_sequence=sequence,occurred_at=at,source_evidence=replace(ref('event-source'),visible_at=at))


def ingest(a,state,archive,event,now=NOW):
    from trading_system.contracts.order_lifecycle_v3_reducer import apply_child
    m=next((m.membership for m in state.memberships if m.membership.membership_id==event.membership_id),None)
    proof=archive.prove('LIFECYCLE_EVENT',event,a,state.binding,canonical_hash(domain_payload(m)) if m else None)
    return apply_child(a,state,event,proof,reader=archive.read,now=now)


def native_guarantee(a,state,group_id='g'):
    from trading_system.contracts.order_lifecycle_v3_reducer import membership_digest
    group=next(g for g in a.groups if g.group_id==group_id)
    return NativeExclusivityV3(contract_version=VERSION,group_id=group_id,authority_hash=canonical_hash(domain_payload(a)),binding_hash=canonical_hash(domain_payload(state.binding)),
        membership_hash=membership_digest(state,group_id),scope=a.scope,venue=a.venue,namespace='ORDER_LIST',template=a.template,node_ids=group.node_ids,
        guarantee='ONE_EXECUTING_MEMBER',rule_revision=a.rule_revision,rule_snapshot_hash=a.rule_snapshot_hash,adapter_build_digest=a.adapter_build_digest,source_evidence=ref('matching-engine'))


def fill(a,trade='trade',quantity='1',price='100',fees=(),at=NOW):
    return FillV3(native_trade_id=trade,quantity=quantity,quantity_unit=a.units.quantity_unit,price=price,price_asset=a.units.quote_asset,fees=fees,occurred_at=at,correction_of=None)


def parent_event(a,state,*,status='ACTIVE',sequence=1,pools=None,at=NOW):
    pools=pools or {}
    reports=tuple(GroupReportV3(group_id=g.group_id,cumulative_quantity=next((p.cumulative_quantity for p in state.projections if p.group_id==g.group_id),'0'),
        unallocated_quantity=pools.get(g.group_id,next((p.unallocated_quantity for p in state.pools if p.group_id==g.group_id),None))) for g in a.groups)
    return ParentObservationV3(contract_version=VERSION,observation_id='parent-'+str(sequence),scope=a.scope,venue=a.venue,namespace=a.namespace,
        parent_client_id=a.parent_client_id,parent_venue_id=state.binding.parent_venue_id if state.binding else None,status=status,groups=reports,
        child_inventory='COMPLETE',child_order_ids=tuple(p.membership.child_order_id for p in state.memberships),report_sequence=sequence,occurred_at=at,source_evidence=replace(ref('parent-source'),visible_at=at))


def ingest_parent(a,state,archive,event,now=NOW):
    from trading_system.contracts.order_lifecycle_v3_reducer import apply_parent,membership_digest
    proof=archive.prove('LIFECYCLE_EVENT',event,a,state.binding,membership_digest(state))
    return apply_parent(a,state,event,proof,reader=archive.read,now=now)


def add_algorithm_child(a,state,archive,child_id='slice'):
    from trading_system.contracts.order_lifecycle_v3_reducer import register_membership
    node=a.nodes[0];app=node.origin=='APPLICATION_GENERATED_CHILD'
    m=ChildMembershipV3(contract_version=VERSION,membership_id='member-'+child_id,authority_hash=canonical_hash(domain_payload(a)),binding_hash=canonical_hash(domain_payload(state.binding)),
        scope=a.scope,venue=a.venue,parent_namespace=a.namespace,child_namespace='ORDINARY',node_id=node.node_id,group_id=node.group_id,child_order_id=child_id,
        child_client_id='client-'+child_id,origin=node.origin,mapping_basis='APPLICATION_COMMAND_ID' if app else 'NATIVE_PARENT_CHILD_QUERY',command_id='command-'+child_id if app else None,
        command_hash='7'*64 if app else None,durable_record_ref='committed-'+child_id if app else None,source_evidence=ref('child-query'))
    proof=archive.prove('CHILD_MEMBERSHIP',m,a,state.binding)
    result=register_membership(a,state,m,proof,reader=archive.read,now=NOW)
    return result,m


def test_v3_nonunit_multiplier_uses_exact_native_quantity_times_price_times_multiplier():
    a=authority(product=t.Product.USD_M_PERP,multiplier='0.001')
    a=replace(a,nodes=(replace(a.nodes[0],quantity_limit='100'),),groups=(replace(a.groups[0],quantity_limit='100',notional_limit='20'),),maximum_notional='20',fee_budgets=())
    result,archive=ready(a);assert not result.restrictions
    fill=FillV3(native_trade_id='trade',quantity='100',quantity_unit='CONTRACT',price='100',price_asset='USDT',fees=(),occurred_at=NOW,correction_of=None)
    result=ingest(a,result.state,archive,child_event(a,result.state,quantity='100',cumulative='100',status='FILLED',fills=(fill,)))
    assert not result.restrictions and amount(result.state.projections[0].actual_notional)==10
    assert result.state.required_protection_quantity=='100' and not result.execution_authorized and not result.funds_released


def test_v3_native_oco_two_quantity_one_legs_share_proven_one_quantity_capacity():
    a=authority(exclusive=True);result,archive=ready(a,native_proof=True);assert not result.restrictions
    first=ingest(a,result.state,archive,child_event(a,result.state,'a'))
    second=ingest(a,first.state,archive,child_event(a,first.state,'b'))
    assert not first.restrictions and not second.restrictions
    projection=second.state.projections[0]
    assert projection.aggregation=='VERIFIED_NATIVE_EXCLUSIVE'
    assert amount(projection.reported_remaining_quantity)==2 and amount(projection.effective_remaining_quantity)==1
    assert tuple(c.observation.status for c in second.state.children)==('SUBMITTED','SUBMITTED')


def test_constructed_authority_or_boolean_reader_is_not_funding_proof():
    from trading_system.contracts.order_lifecycle_v3_reducer import prepare
    a=authority();archive=ProofArchive();p=archive.prove('FUNDING_AUTHORITY',a,a)
    assert prepare(a,p,now=NOW).state is None
    assert prepare(a,p,reader=lambda *args:True,now=NOW).state is None
    typed=VersionedOrderCodec().decode('lifecycle-proof',json.loads(archive.records[('FUNDING_AUTHORITY',p.id)]))
    assert prepare(a,p,reader=lambda *args:typed,now=NOW).state is None
    assert 'AUTHORITY_PROOF_UNVERIFIED' in prepare(a,replace(p,hash='0'*64),reader=archive.read,now=NOW).restrictions


def test_qualified_zero_asset_owned_reduction_vs_unfunded_opening_and_unknown_fees():
    from trading_system.contracts.order_lifecycle_v3_reducer import prepare
    base=authority(product=t.Product.USD_M_PERP)
    a=replace(base,reservation_ids=(),funding_requirements=(),fee_budgets=(),funding_basis='OWNED_POSITION_REDUCTION',
        position_allocation_hash='8'*64,starting_position_quantity='1',nodes=(replace(base.nodes[0],side='SELL'),))
    archive=ProofArchive();p=archive.prove('FUNDING_AUTHORITY',a,a)
    assert prepare(a,p,reader=archive.read,now=NOW).state is not None
    assert prepare(a,p,now=NOW).state is None
    for changed,reason in [(replace(a,starting_position_quantity='0'),'OWNED_REDUCTION_REQUIRED'),
        (replace(a,fee_budgets=None),'FEE_BUDGET_UNKNOWN'),
        (replace(a,fee_budgets=(AssetBudget(asset='USDT',amount='0.1'),)),'ZERO_ASSET_REDUCTION_HAS_FEES')]:
        result=prepare(changed,archive.prove('FUNDING_AUTHORITY',changed,changed),reader=archive.read,now=NOW)
        assert result.state is None and reason in result.restrictions


def test_native_pair_has_no_circular_bootstrap_and_late_proof_does_not_resolve_risk():
    from trading_system.contracts.order_lifecycle_v3_reducer import register_exclusivity,review_projection
    a=authority(exclusive=True);unproven,archive=ready(a)
    assert len(unproven.state.memberships)==2 and 'GROUP_QUANTITY_EXCEEDED' in unproven.restrictions
    assert unproven.state.projections[0].aggregation=='SUM'
    assert amount(unproven.state.projections[0].effective_remaining_quantity)==2
    guarantee=native_guarantee(a,unproven.state)
    p=archive.prove('NATIVE_EXCLUSIVITY',guarantee,a,unproven.state.binding,guarantee.membership_hash)
    late=register_exclusivity(a,unproven.state,guarantee,p,reader=archive.read,now=NOW)
    review=review_projection(a,late.state,reader=archive.read,now=NOW)
    assert not review.current_restrictions
    assert review.result.state.status=='RECONCILIATION_REQUIRED' and review.result.restrictions
    assert amount(review.result.state.projections[0].effective_remaining_quantity)==1
    assert not review.result.execution_authorized and not review.result.funds_released


def test_local_oco_counts_both_independent_children_and_no_native_parent_is_invented():
    a=authority(exclusive=True,local=True);result,archive=ready(a)
    assert not result.restrictions and result.state.binding.parent_venue_id is None
    assert result.state.projections[0].aggregation=='SUM' and amount(result.state.projections[0].effective_remaining_quantity)==2
    assert all(p.membership.command_id for p in result.state.memberships)


def test_ordinary_atomic_member_uses_the_actual_single_command_and_order():
    a=authority();a=replace(a,namespace='ORDINARY',nodes=(replace(a.nodes[0],origin='APPLICATION_GENERATED_CHILD'),))
    result,archive=ready(a);assert not result.restrictions
    member=result.state.memberships[0].membership
    assert member.command_id==a.parent_command_id and member.child_order_id==result.state.binding.parent_venue_id
    assert member.child_client_id==a.parent_client_id and member.mapping_basis=='APPLICATION_COMMAND_ID'


def test_no_native_discount_for_wrong_rule_build_scope_or_member_set():
    from trading_system.contracts.order_lifecycle_v3_reducer import register_exclusivity
    a=authority(exclusive=True);result,archive=ready(a)
    original=native_guarantee(a,result.state)
    for g in (replace(original,adapter_build_digest='0'*64),replace(original,membership_hash='0'*64),replace(original,rule_revision=2),replace(original,scope=replace(a.scope,venue_account_id='other'))):
        p=archive.prove('NATIVE_EXCLUSIVITY',g,a,result.state.binding,g.membership_hash)
        bad=register_exclusivity(a,result.state,g,p,reader=archive.read,now=NOW)
        assert 'NATIVE_EXCLUSIVITY_PROOF_UNVERIFIED' in bad.restrictions
        assert bad.state.projections[0].aggregation=='SUM'


def test_native_proof_never_clips_an_oversized_raw_leaves_claim_to_capacity():
    a=authority(exclusive=True);result,archive=ready(a,native_proof=True)
    event=child_event(a,result.state,'a',quantity='2')
    result=ingest(a,result.state,archive,event)
    assert 'GROUP_QUANTITY_EXCEEDED' in result.restrictions
    assert amount(result.state.projections[0].effective_remaining_quantity)==2
    assert result.retained==event and result.state.children[0].observation.quantity=='2'


def test_native_winner_does_not_fabricate_sibling_cancel_and_dual_execution_restricts():
    a=authority(exclusive=True);result,archive=ready(a,native_proof=True)
    for node in ('a','b'):result=ingest(a,result.state,archive,child_event(a,result.state,node))
    first=child_event(a,result.state,'a',cumulative='0.4',status='PARTIALLY_FILLED',fills=(fill(a,'ta','0.4'),),sequence=2)
    result=ingest(a,result.state,archive,first)
    assert not result.restrictions and amount(result.state.projections[0].effective_remaining_quantity)==D('0.6')
    assert next(c.observation.status for c in result.state.children if c.observation.child_order_id=='child-b')=='SUBMITTED'
    second=child_event(a,result.state,'b',cumulative='0.1',status='PARTIALLY_FILLED',fills=(fill(a,'tb','0.1'),),sequence=2)
    result=ingest(a,result.state,archive,second)
    assert 'NATIVE_EXCLUSIVITY_VIOLATION' in result.restrictions
    assert result.state.projections[0].aggregation=='SUM'
    assert amount(result.state.required_protection_quantity)==D('0.5') and result.retained==second


def test_duplicate_and_conflicting_events_keep_original_facts_and_no_flat_claim():
    a=authority();result,archive=ready(a)
    event=child_event(a,result.state,cumulative='1',status='FILLED',fills=(fill(a),))
    result=ingest(a,result.state,archive,event);state=result.state
    duplicate=ingest(a,state,archive,event)
    assert duplicate.duplicate and duplicate.state==state
    conflict=replace(event,quantity='2',leaves_quantity='1')
    result=ingest(a,state,archive,conflict)
    assert 'EVENT_CONTENT_CONFLICT' in result.restrictions and result.retained==conflict
    assert result.state.children==state.children and result.state.required_protection_quantity is None
    assert result.state.unattributed_effects and not result.funds_released


def test_actual_price_above_upper_and_unknown_fee_are_not_clipped_or_zeroed():
    a=authority();result,archive=ready(a)
    event=child_event(a,result.state,cumulative='1',status='FILLED',fills=(fill(a,price='120'),))
    actual=ingest(a,result.state,archive,event)
    assert 'EXECUTION_PRICE_BOUND_VIOLATION' in actual.restrictions
    assert amount(actual.state.projections[0].actual_notional)==120
    unknown=ingest(a,result.state,archive,replace(event,fills=(fill(a,fees=None),)))
    assert 'FEE_DATA_UNKNOWN' in unknown.restrictions and unknown.state.fee_totals is None
    assert unknown.state.required_protection_quantity is None


def test_unknown_fee_refinement_can_update_facts_but_cannot_clear_risk():
    a=authority();result,archive=ready(a)
    event=child_event(a,result.state,cumulative='1',status='FILLED',fills=(fill(a,fees=None),))
    result=ingest(a,result.state,archive,event)
    refined=replace(event,observation_id='fee-known',report_sequence=2,fills=(fill(a,fees=()),))
    result=ingest(a,result.state,archive,refined)
    assert result.state.status=='RECONCILIATION_REQUIRED' and amount(result.state.required_protection_quantity)==1
    assert result.state.fee_totals==() and not result.funds_released


def test_parent_stop_does_not_imply_child_terminal_and_real_late_fill_retains_duty():
    a=authority();result,archive=ready(a)
    result=ingest(a,result.state,archive,child_event(a,result.state))
    stopped=ingest_parent(a,result.state,archive,parent_event(a,result.state,status='STOPPED'))
    assert not stopped.restrictions and stopped.state.status=='STOPPED'
    assert amount(stopped.state.projections[0].effective_remaining_quantity)==1 and not stopped.funds_released
    completed=ingest_parent(a,stopped.state,archive,parent_event(a,stopped.state,status='COMPLETED',sequence=2))
    assert 'CHILDREN_STILL_UNSETTLED' in completed.restrictions
    event=child_event(a,stopped.state,cumulative='1',status='FILLED',fills=(fill(a),),sequence=2)
    late=ingest(a,stopped.state,archive,event)
    assert amount(late.state.required_protection_quantity)==1 and late.state.protection_status=='UNVERIFIED_DUTY'


def test_cancel_race_records_actual_fill_and_rejects_parent_active_regression():
    a=authority();result,archive=ready(a)
    result=ingest(a,result.state,archive,child_event(a,result.state))
    cancel=ingest_parent(a,result.state,archive,parent_event(a,result.state,status='CANCEL_PENDING'))
    event=child_event(a,cancel.state,cumulative='1',status='FILLED',fills=(fill(a),),sequence=2)
    raced=ingest(a,cancel.state,archive,event)
    assert 'PARENT_CANCEL_RACE' in raced.restrictions and amount(raced.state.required_protection_quantity)==1
    regressed=ingest_parent(a,cancel.state,archive,parent_event(a,cancel.state,status='ACTIVE',sequence=2))
    assert 'PARENT_STATE_REGRESSION' in regressed.restrictions


def test_parent_and_child_submicrosecond_watermarks_do_not_truncate():
    a=authority();result,archive=ready(a)
    high='2026-10-29T06:00:00.0000002Z';low='2026-10-29T06:00:00.0000001Z';now='2026-10-29T06:00:00.0000003Z'
    first=ingest(a,result.state,archive,child_event(a,result.state,at=high),now)
    child=ingest(a,first.state,archive,child_event(a,first.state,sequence=2,at=low),now)
    assert 'OUT_OF_ORDER_CHILD_OBSERVATION' in child.restrictions
    first=ingest_parent(a,result.state,archive,parent_event(a,result.state,at=high),now)
    parent=ingest_parent(a,first.state,archive,parent_event(a,first.state,sequence=2,at=low),now)
    assert 'OUT_OF_ORDER_PARENT_OBSERVATION' in parent.restrictions


def test_algorithm_children_consume_pool_once_and_cancel_never_recycles_implicitly():
    a=authority(algorithm=True);result,archive=ready(a)
    result,member=add_algorithm_child(a,result.state,archive)
    event=child_event(a,result.state,quantity='0.4')
    result=ingest(a,result.state,archive,event)
    assert amount(result.state.pools[0].unallocated_quantity)==D('0.6')
    assert amount(result.state.projections[0].effective_remaining_quantity)==1
    canceled=replace(event,observation_id='canceled',status='CANCELED',report_sequence=2)
    result=ingest(a,result.state,archive,canceled)
    assert amount(result.state.pools[0].unallocated_quantity)==D('0.6')
    updated=ingest_parent(a,result.state,archive,parent_event(a,result.state,pools={'g':'1'}))
    assert not updated.restrictions and amount(updated.state.pools[0].unallocated_quantity)==1
    unknown=ingest_parent(a,result.state,archive,parent_event(a,result.state,pools={'g':None}))
    assert 'ALGORITHM_POOL_UNKNOWN' in unknown.restrictions and unknown.state.pools[0].unallocated_quantity is None


def test_parent_reallocation_rechecks_cumulative_plus_remaining_notional_and_quantity():
    a=authority(algorithm=True);result,archive=ready(a);result,_=add_algorithm_child(a,result.state,archive)
    event=child_event(a,result.state,quantity='1',cumulative='0.5',status='CANCELED',fills=(fill(a,quantity='0.5'),))
    result=ingest(a,result.state,archive,event)
    over=ingest_parent(a,result.state,archive,parent_event(a,result.state,pools={'g':'1'}))
    assert 'GROUP_QUANTITY_EXCEEDED' in over.restrictions
    assert over.state.pools[0].unallocated_quantity=='1'  # raw claim retained, not clipped


def opo_authority(*,pair=False,starting='0'):
    a=authority();working=replace(a.nodes[0],node_id='working',group_id='entry')
    pending=replace(a.nodes[0],node_id='pending',group_id='exit',side='SELL',quantity_mode='NET_RECEIPT',activation='NET_RECEIPT',source_node_id='working')
    pending_nodes=(pending,replace(pending,node_id='pending-b')) if pair else (pending,)
    return replace(a,template='OPOCO' if pair else 'OPO',namespace='ORDER_LIST',nodes=(working,*pending_nodes),
        groups=(AllocationGroupV3(group_id='entry',node_ids=('working',),aggregation='SUM',quantity_limit='1',notional_limit='100'),
            AllocationGroupV3(group_id='exit',node_ids=tuple(n.node_id for n in pending_nodes),aggregation='NATIVE_EXCLUSIVE' if pair else 'SUM',quantity_limit='1',notional_limit='100')),
        starting_position_quantity=starting,reservation_ids=('hold','base-fee-hold'),funding_requirements=(AssetBudget(asset='USDT',amount='210'),AssetBudget(asset='BTC',amount='1')))


def receipt_claim(a,state,*,commission='0.01',locked='0.99',pending='0.9',unused='0.09',disposition='STILL_LOCKED'):
    from trading_system.contracts.order_lifecycle_v3_reducer import source_fill_digest
    return ReceiptAllocationV3(contract_version=VERSION,receipt_id='receipt',authority_hash=canonical_hash(domain_payload(a)),binding_hash=canonical_hash(domain_payload(state.binding)),
        scope=a.scope,source_node_id='working',target_group_id='exit',source_fill_hash=source_fill_digest(state,'working'),asset='BTC',quantity_unit='BTC',
        gross_received_quantity='1',receipt_commission_quantity=commission,locked_quantity=locked,pending_quantity=pending,unused_quantity=unused,
        unused_disposition=disposition,lot_step='0.1',rule_snapshot_hash=a.rule_snapshot_hash,occurred_at=NOW,source_evidence=ref('actual-receipt-and-lot-query'))


def ingest_receipt(a,state,archive,receipt):
    from trading_system.contracts.order_lifecycle_v3_reducer import apply_receipt,membership_digest
    proof=archive.prove('RECEIPT_ALLOCATION',receipt,a,state.binding,membership_digest(state,receipt.target_group_id))
    return apply_receipt(a,state,receipt,proof,reader=archive.read,now=NOW)


@pytest.mark.parametrize('pair',[False,True])
def test_opo_full_fill_net_receipt_and_lot_dust_are_explicit_without_fake_protection(pair):
    a=opo_authority(pair=pair);result,archive=ready(a,native_proof=pair);assert not result.restrictions
    fee=FeeV3(asset='BTC',amount='0.01',receipt_effect='DEDUCT_RECEIVED_ASSET',position_effect='DEDUCT_OWNED_BASE')
    result=ingest(a,result.state,archive,child_event(a,result.state,'working',cumulative='1',status='FILLED',fills=(fill(a,fees=(fee,)),)))
    assert not result.restrictions and amount(result.state.required_protection_quantity)==D('0.99')
    receipt=receipt_claim(a,result.state)
    result=ingest_receipt(a,result.state,archive,receipt)
    assert not result.restrictions and result.state.receipts[0].receipt.unused_quantity=='0.09'
    assert result.state.receipts[0].receipt.unused_disposition=='STILL_LOCKED' and not result.funds_released
    result=ingest(a,result.state,archive,child_event(a,result.state,'pending',quantity='0.9'))
    assert not result.restrictions
    assert amount(result.state.required_protection_quantity)==D('0.99') and result.state.protection_status=='UNVERIFIED_DUTY'


def test_external_base_fee_does_not_reduce_locked_receipt_but_debits_existing_owned_position():
    a=opo_authority(starting='1');result,archive=ready(a)
    fee=FeeV3(asset='BTC',amount='0.01',receipt_effect='NO_RECEIPT_DEBIT',position_effect='DEDUCT_OWNED_BASE')
    result=ingest(a,result.state,archive,child_event(a,result.state,'working',cumulative='1',status='FILLED',fills=(fill(a,fees=(fee,)),)))
    assert amount(result.state.net_position_quantity)==D('1.99')
    receipt=receipt_claim(a,result.state,commission='0',locked='1',pending='1',unused='0')
    result=ingest_receipt(a,result.state,archive,receipt)
    assert not result.restrictions and result.state.receipts[0].receipt.locked_quantity=='1'
    assert amount(result.state.required_protection_quantity)==D('1.99')


def test_derivative_collateral_fee_never_subtracts_position_contracts():
    a=authority(product=t.Product.USD_M_PERP,multiplier='0.001');result,archive=ready(a)
    fee=FeeV3(asset='BTC',amount='0.01',receipt_effect='NO_RECEIPT_DEBIT',position_effect='NO_POSITION_DEBIT')
    event=child_event(a,result.state,cumulative='1',status='FILLED',fills=(fill(a,fees=(fee,)),))
    actual=ingest(a,result.state,archive,event)
    assert not actual.restrictions and amount(actual.state.required_protection_quantity)==1
    bad=replace(event,fills=(fill(a,fees=(replace(fee,position_effect='DEDUCT_OWNED_BASE'),)),))
    rejected=ingest(a,result.state,archive,bad)
    assert 'FEE_POSITION_UNIT_CONFLICT' in rejected.restrictions and rejected.state.required_protection_quantity is None


def test_pending_new_ack_is_not_activation_and_early_execution_requires_reconciliation():
    a=opo_authority();result,archive=ready(a)
    pending=child_event(a,result.state,'pending',quantity=None,status='PENDING_NEW')
    seen=ingest(a,result.state,archive,pending)
    assert not seen.restrictions and seen.state.protection_status=='UNVERIFIED_DUTY'
    early=child_event(a,seen.state,'pending',quantity='1',sequence=2)
    rejected=ingest(a,seen.state,archive,early)
    assert 'FULL_FILL_ACTIVATION_UNPROVEN' in rejected.restrictions and 'NET_RECEIPT_ALLOCATION_UNPROVEN' in rejected.restrictions


@pytest.mark.parametrize('case',['early','unknown_fees','over_net','wrong_lot','wrong_fill_hash'])
def test_receipt_claim_needs_full_economic_proof_and_cannot_invent_rounded_quantity(case):
    a=opo_authority();result,archive=ready(a)
    fee=FeeV3(asset='BTC',amount='0.01',receipt_effect='DEDUCT_RECEIVED_ASSET',position_effect='DEDUCT_OWNED_BASE')
    if case!='early':
        result=ingest(a,result.state,archive,child_event(a,result.state,'working',cumulative='1',status='FILLED',fills=(fill(a,fees=None if case=='unknown_fees' else (fee,)),)))
    receipt=receipt_claim(a,result.state)
    if case=='over_net':receipt=replace(receipt,locked_quantity='1',pending_quantity='1',unused_quantity='0')
    if case=='wrong_lot':receipt=replace(receipt,pending_quantity='0.99',unused_quantity='0')
    if case=='wrong_fill_hash':receipt=replace(receipt,source_fill_hash='0'*64)
    rejected=ingest_receipt(a,result.state,archive,receipt)
    assert rejected.restrictions and rejected.retained==receipt and not rejected.state.receipts
    assert not rejected.funds_released and rejected.state.status=='RECONCILIATION_REQUIRED'


def test_event_cannot_self_assign_membership_and_outbound_map_cannot_guess_client_id():
    from trading_system.contracts.order_lifecycle_v3_reducer import register_membership
    a=authority();result,archive=ready(a)
    event=replace(child_event(a,result.state),membership_id='self-declared-node')
    rejected=ingest(a,result.state,archive,event)
    assert 'CHILD_MEMBERSHIP_UNVERIFIED' in rejected.restrictions and not rejected.state.children
    # A new parent with an authoritative outbound client map rejects a different
    # reported client even when a misconfigured synthetic verifier signs it.
    a=authority(algorithm=True);a=replace(a,nodes=(replace(a.nodes[0],expected_client_id='expected-client'),))
    state,archive=ready(a);registered,member=add_algorithm_child(a,state.state,archive)
    wrong=replace(member,membership_id='outbound',child_order_id='different-child',child_client_id='guessed',mapping_basis='OUTBOUND_CLIENT_ID')
    p=archive.prove('CHILD_MEMBERSHIP',wrong,a,state.state.binding)
    rejected=register_membership(a,state.state,wrong,p,reader=archive.read,now=NOW)
    assert 'OUTBOUND_CLIENT_MAPPING_MISMATCH' in rejected.restrictions


def test_dynamic_price_or_native_proof_expiry_never_becomes_zero_risk():
    from trading_system.contracts.order_lifecycle_v3_reducer import review_projection
    a=authority(exclusive=True);result,archive=ready(a,native_proof=True)
    review=review_projection(a,result.state,reader=archive.read,now=LATER)
    assert 'NATIVE_EXCLUSIVITY_PROOF_UNVERIFIED' in review.current_restrictions
    assert 'DYNAMIC_BOUND_EXPIRED' in review.current_restrictions
    assert review.result.state.projections[0].possible_notional is None
    assert review.result.state.projections[0].aggregation=='SUM'


def test_missing_fill_details_are_retained_as_unknown_economics_not_zero_notional():
    a=authority();result,archive=ready(a)
    event=child_event(a,result.state,cumulative='1',status='FILLED',fills=())
    rejected=ingest(a,result.state,archive,event)
    assert 'FILL_DETAILS_MISSING' in rejected.restrictions and rejected.retained==event
    assert rejected.state.children[0].observation.cumulative_quantity=='1'
    assert rejected.state.projections[0].actual_notional is None and rejected.state.required_protection_quantity is None


def test_preack_unknown_keeps_original_ids_and_late_binding_never_resends_or_clears_risk():
    from trading_system.contracts.order_lifecycle_v3_reducer import prepare,bind_parent
    a=authority();archive=ProofArchive();state=prepare(a,archive.prove('FUNDING_AUTHORITY',a,a),reader=archive.read,now=NOW).state
    unknown=ingest_parent(a,state,archive,parent_event(a,state,status='SUBMIT_UNKNOWN'))
    assert unknown.state.binding is None and 'PARENT_STATE_UNKNOWN' in unknown.restrictions
    binding=ParentBindingV3(contract_version=VERSION,binding_id='late-binding',authority_hash=canonical_hash(domain_payload(a)),scope=a.scope,venue=a.venue,namespace=a.namespace,
        binding_kind='VENUE_ACCEPTANCE',parent_command_id=a.parent_command_id,parent_client_id=a.parent_client_id,parent_venue_id='queried-original-parent',bound_at=NOW,source_evidence=ref('original-id-query'))
    proof=archive.prove('PARENT_BINDING',binding,a,binding)
    late=bind_parent(a,unknown.state,binding,proof,reader=archive.read,now=NOW)
    assert late.state.binding==binding and late.state.status=='RECONCILIATION_REQUIRED'
    assert late.state.binding.parent_client_id==a.parent_client_id and not late.execution_authorized and not late.funds_released


def test_terminal_fill_without_original_quantity_remains_explicitly_unsettled():
    a=authority();result,archive=ready(a)
    event=child_event(a,result.state,quantity=None,cumulative='1',status='FILLED',fills=(fill(a),))
    result=ingest(a,result.state,archive,event)
    assert 'CHILD_QUANTITY_UNKNOWN' in result.restrictions and result.retained==event


def test_identity_proof_issued_after_receipt_binding_time_can_be_reused_as_historical_identity():
    from trading_system.contracts.order_lifecycle_v3_reducer import prepare,bind_parent,activate
    a=authority(algorithm=True);archive=ProofArchive();prepared=prepare(a,archive.prove('FUNDING_AUTHORITY',a,a),reader=archive.read,now=NOW)
    binding=ParentBindingV3(contract_version=VERSION,binding_id='delayed-proof',authority_hash=canonical_hash(domain_payload(a)),scope=a.scope,venue=a.venue,namespace=a.namespace,
        binding_kind='VENUE_ACCEPTANCE',parent_command_id=a.parent_command_id,parent_client_id=a.parent_client_id,parent_venue_id='original-order',bound_at=NOW,source_evidence=ref('ack'))
    p=archive.prove('PARENT_BINDING',binding,a,binding)
    issued='2026-10-29T06:00:00.0000001Z';now='2026-10-29T06:00:00.0000002Z'
    body=json.loads(archive.records[('PARENT_BINDING',p.id)]);body['valid_from']=issued
    raw=json.dumps(body,sort_keys=True).encode();p=replace(p,hash=sha256(raw).hexdigest(),visible_at=issued)
    archive.records[('PARENT_BINDING',p.id)]=raw
    bound=bind_parent(a,prepared.state,binding,p,reader=archive.read,now=now)
    assert not bound.restrictions
    activated=activate(a,bound.state,reader=archive.read,now=now)
    assert not activated.restrictions and not activated.execution_authorized


def test_reconciliation_overlay_does_not_erase_stopped_parent_transition_fence():
    a=authority();result,archive=ready(a)
    result=ingest(a,result.state,archive,child_event(a,result.state))
    stopped=ingest_parent(a,result.state,archive,parent_event(a,result.state,status='STOPPED'))
    unknown=ingest(a,stopped.state,archive,child_event(a,stopped.state,status='UNKNOWN',sequence=2))
    assert unknown.state.status=='RECONCILIATION_REQUIRED'
    reopened=ingest_parent(a,unknown.state,archive,parent_event(a,unknown.state,status='ACTIVE',sequence=2))
    assert 'PARENT_STATE_REGRESSION' in reopened.restrictions


def test_projection_review_does_not_call_unknown_child_economics_reconciled():
    from trading_system.contracts.order_lifecycle_v3_reducer import review_projection
    a=authority();result,archive=ready(a)
    unknown=ingest(a,result.state,archive,child_event(a,result.state,status='UNKNOWN'))
    review=review_projection(a,unknown.state,reader=archive.read,now=NOW)
    assert 'CHILD_STATE_UNKNOWN' in review.current_restrictions


def test_complete_and_restricted_v3_states_roundtrip_without_migrating_v2():
    a=authority(exclusive=True);result,archive=ready(a,native_proof=True)
    result=ingest(a,result.state,archive,child_event(a,result.state,'a'))
    codec=VersionedOrderCodec();body=codec.encode('program-state',result.state)
    assert codec.decode('program-state',body)==result.state
    assert codec.hash('program-state',codec.decode('program-state',body))==canonical_hash(body)
    restricted=ingest(a,result.state,archive,replace(child_event(a,result.state,'b'),membership_id='unmapped'))
    assert codec.decode('program-state',codec.encode('program-state',restricted.state))==restricted.state
    assert restricted.state.unattributed_effects


def test_current_storage_does_not_gain_v3_binding_from_codec_registration():
    from trading_system.storage.order_programs import OrderProgramRepository
    from trading_system.storage.models import MissingEvidence
    root=Path(__file__).resolve().parents[2]
    body=json.loads((root/'tests/fixtures/orders-v2/program.paper.json').read_text(encoding='utf-8'))
    body.update(contract_version=VERSION,execution_mode='VENUE_NATIVE',program_id='program',instrument_ref=SCOPE.instrument_uid)
    program=VersionedOrderCodec().decode('order-program',body)
    a=replace(authority(),program_hash=VersionedOrderCodec().hash('order-program',program))
    class Tx:
        scope=a.scope
        failed=False
        def require_scope(self,scope):assert scope==self.scope
        def _append(self,*args):raise AssertionError('production binding unexpectedly wrote')
    tx=Tx();repository=OrderProgramRepository(tx)
    repository.program=lambda _:program
    with pytest.raises(MissingEvidence,match='PARENT_LIFECYCLE_VERSION_NOT_IMPLEMENTED'):
        repository.bind_parent(a)
    assert tx.failed


def test_lifecycle_never_uses_a_wrong_currency_fill_or_future_event_as_economic_truth():
    a=authority();result,archive=ready(a)
    event=child_event(a,result.state,cumulative='1',status='FILLED',fills=(replace(fill(a),price_asset='USD'),))
    wrong=ingest(a,result.state,archive,event)
    assert 'FILL_UNIT_OR_ASSET_MISMATCH' in wrong.restrictions and wrong.state.projections[0].actual_notional is None
    future=replace(event,observation_id='future',occurred_at=LATER,fills=(fill(a),))
    rejected=ingest(a,result.state,archive,future)
    assert 'EVENT_TIME_UNVERIFIED' in rejected.restrictions and rejected.retained==future


def test_fee_cap_violation_keeps_actual_fee_and_protection_obligation():
    a=authority();result,archive=ready(a)
    fee=FeeV3(asset='USDT',amount='11',receipt_effect='NO_RECEIPT_DEBIT',position_effect='NO_POSITION_DEBIT')
    event=child_event(a,result.state,cumulative='1',status='FILLED',fills=(fill(a,fees=(fee,)),))
    rejected=ingest(a,result.state,archive,event)
    assert 'FEE_BUDGET_EXCEEDED' in rejected.restrictions
    assert rejected.state.fee_totals==(AssetBudget(asset='USDT',amount='11'),)
    assert amount(rejected.state.required_protection_quantity)==1 and rejected.retained==event


def test_parent_cumulative_disagreement_and_incomplete_inventory_are_not_assumed_zero():
    a=authority();result,archive=ready(a)
    result=ingest(a,result.state,archive,child_event(a,result.state,cumulative='1',status='FILLED',fills=(fill(a),)))
    event=parent_event(a,result.state,status='COMPLETED')
    missing=ingest_parent(a,result.state,archive,replace(event,child_inventory='INCOMPLETE'))
    assert 'CHILD_INVENTORY_INCOMPLETE' in missing.restrictions and missing.state.required_protection_quantity is None
    mismatch=ingest_parent(a,result.state,archive,replace(event,groups=(replace(event.groups[0],cumulative_quantity='0'),)))
    assert 'CHILD_EVIDENCE_INCOMPLETE' in mismatch.restrictions and mismatch.retained.groups[0].cumulative_quantity=='0'


def test_zero_asset_proof_must_cite_owned_position_allocation_material_not_only_the_claim():
    from trading_system.contracts.order_lifecycle_v3_reducer import prepare
    a=authority(product=t.Product.USD_M_PERP)
    a=replace(a,reservation_ids=(),funding_requirements=(),fee_budgets=(),funding_basis='OWNED_POSITION_REDUCTION',
        position_allocation_hash='8'*64,starting_position_quantity='1',nodes=(replace(a.nodes[0],side='SELL'),))
    archive=ProofArchive();p=archive.prove('FUNDING_AUTHORITY',a,a)
    body=json.loads(archive.records[('FUNDING_AUTHORITY',p.id)])
    body['source_refs']=[r for r in body['source_refs'] if r['hash']!=a.position_allocation_hash]
    raw=json.dumps(body,sort_keys=True).encode();p=replace(p,hash=sha256(raw).hexdigest())
    archive.records[('FUNDING_AUTHORITY',p.id)]=raw
    rejected=prepare(a,p,reader=archive.read,now=NOW)
    assert rejected.state is None and 'AUTHORITY_PROOF_UNVERIFIED' in rejected.restrictions
