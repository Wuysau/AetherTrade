"""Pure v3 lifecycle projections. No ledger, transport or financial release.

Input state must be reconstructed from the owner's immutable ledger, not supplied
by a model/API caller. reader(role, ref) is an independently authenticated LOCAL
verifier archive, never arbitrary caller bytes. No production reader is supplied.
Its records attest native membership/semantics; hashes alone are not provenance.
"""
from dataclasses import dataclass,replace
from decimal import Decimal,localcontext,Inexact,Rounded
from functools import wraps
from hashlib import sha256
import json
from . import types as t
from .canonical import canonical_hash,domain_payload
from .order_codec import VersionedOrderCodec
from .order_lifecycle import AssetBudget
from .order_lifecycle_v3 import *

D=Decimal
TERMINAL=frozenset({'FILLED','CANCELED','EXPIRED','REJECTED'})


def _exact(function):
    @wraps(function)
    def call(*args,**kwargs):
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=True;arithmetic.traps[Rounded]=True
            return function(*args,**kwargs)
    return call


def digest(record):return canonical_hash(domain_payload(record))


def proof_context(authority,binding=None,membership_hash=None):
    return canonical_hash({'authority_hash':digest(authority),'binding_hash':digest(binding) if binding else None,
        'membership_hash':membership_hash,'scope':domain_payload(authority.scope),'venue':authority.venue,
        'namespace':authority.namespace,'template':authority.template,'rule_revision':authority.rule_revision,
        'rule_snapshot_hash':authority.rule_snapshot_hash,'adapter_build_digest':authority.adapter_build_digest})


def membership_digest(state,group_id=None):
    members=sorted((p.membership for p in state.memberships if group_id is None or p.membership.group_id==group_id),key=lambda m:m.membership_id)
    return canonical_hash({'members':[domain_payload(m) for m in members]})


def source_fill_digest(state,node_id):
    ids={p.membership.membership_id for p in state.memberships if p.membership.node_id==node_id}
    children=sorted((c for c in state.children if c.observation.membership_id in ids),key=lambda c:c.observation.child_order_id)
    return canonical_hash({'source_children':[{'membership_id':c.observation.membership_id,'child_order_id':c.observation.child_order_id,
        'quantity':c.observation.quantity,'quantity_unit':c.observation.quantity_unit,'cumulative_quantity':c.observation.cumulative_quantity,
        'status':c.observation.status,'fills':[domain_payload(f) for f in sorted(c.fills,key=lambda f:f.native_trade_id)]} for c in children]})


def _proof(reader,role,ref,subject,authority,binding,now,membership_hash=None):
    if reader is None or ref is None:return False
    try:
        reference(ref)
        if ref.environment!=authority.scope.environment or exact_time(ref.visible_at)>exact_time(now):return False
        raw=reader(role,ref)
        if type(raw) is not bytes or len(raw)>1048576 or sha256(raw).hexdigest()!=ref.hash:return False
        proof=VersionedOrderCodec().decode('lifecycle-proof',json.loads(raw))
        source=getattr(subject,'source_evidence',getattr(subject,'unit_evidence',None))
        required_sources=set()
        if role=='FUNDING_AUTHORITY':
            required_sources={authority.compilation_hash,authority.target_allocation_hash,authority.funding_admission_hash}
            if authority.funding_basis=='OWNED_POSITION_REDUCTION':required_sources.add(authority.position_allocation_hash)
        elif role=='NATIVE_EXCLUSIVITY':required_sources={authority.rule_snapshot_hash,authority.adapter_build_digest}
        elif role=='CHILD_MEMBERSHIP' and subject.origin=='APPLICATION_GENERATED_CHILD':required_sources={subject.command_hash}
        return (proof.proof_id==ref.id and proof.role==role and proof.scope==authority.scope
            and proof.subject_hash==digest(subject) and proof.context_hash==proof_context(authority,binding,membership_hash)
            and exact_time(proof.valid_from)<=exact_time(now)<exact_time(proof.valid_until)
            and (source is None or source in proof.source_refs)
            and required_sources<={r.hash for r in proof.source_refs}
            and all(r.environment==authority.scope.environment and exact_time(r.visible_at)<=exact_time(now) for r in proof.source_refs))
    except Exception:
        return False


def _checked(kind,record):
    VersionedOrderCodec().encode(kind,record)


def _base(authority,state,reader,now):
    return (authority.scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH)
        and state is not None and state.program_id==authority.program_id and state.authority_hash==digest(authority)
        and exact_time(state.prepared_at)<=exact_time(now)
        and _proof(reader,'FUNDING_AUTHORITY',state.authority_proof_ref,authority,authority,None,state.prepared_at))


def _result(state,restrictions=(),retained=None,proof_ref=None,*,duplicate=False,unknown_effect=False):
    reasons=tuple(dict.fromkeys(restrictions))
    if state is None:return LifecycleResultV3(state=None,restrictions=reasons,retained=retained,proof_ref=proof_ref,duplicate=duplicate)
    if duplicate:return LifecycleResultV3(state=state,restrictions=state.restriction_codes if state.status=='RECONCILIATION_REQUIRED' else reasons,
        retained=retained,proof_ref=proof_ref,duplicate=True)
    codes=tuple(dict.fromkeys((*state.restriction_codes,*reasons)))
    restricted=bool(codes) or state.status=='RECONCILIATION_REQUIRED'
    updated=replace(state,sequence=state.sequence+1,status='RECONCILIATION_REQUIRED' if restricted else state.status,
        restriction_codes=codes,protection_status='RECONCILIATION_REQUIRED' if restricted else 'UNVERIFIED_DUTY',
        net_position_quantity=None if unknown_effect else state.net_position_quantity,
        required_protection_quantity=None if unknown_effect else state.required_protection_quantity,
        projections=tuple(replace(p,actual_notional=None,possible_notional=None,effective_remaining_quantity=None) for p in state.projections) if unknown_effect else state.projections,
        unattributed_effects=state.unattributed_effects or unknown_effect)
    return LifecycleResultV3(state=updated,restrictions=codes,retained=retained,proof_ref=proof_ref)


@_exact
def prepare(authority,proof_ref=None,*,reader=None,now):
    _checked('parent-authority',authority)
    reasons=[]
    if authority.scope.environment not in (t.Environment.PAPER,t.Environment.RESEARCH):reasons.append('ENVIRONMENT_DISABLED')
    if exact_time(now)>=exact_time(authority.submission_valid_until):reasons.append('SUBMISSION_AUTHORITY_EXPIRED')
    if exact_time(now)>=exact_time(authority.budget_valid_until):reasons.append('DYNAMIC_BOUND_EXPIRED')
    if authority.fee_budgets is None:reasons.append('FEE_BUDGET_UNKNOWN')
    if authority.scope.product!=t.Product.USD_M_PERP and D(authority.starting_position_quantity)<0:reasons.append('CASH_NEGATIVE_POSITION')
    if authority.funding_basis=='OWNED_POSITION_REDUCTION':
        actual=D(authority.starting_position_quantity)
        if not actual or any(n.side!=('SELL' if actual>0 else 'BUY') for n in authority.nodes):reasons.append('OWNED_REDUCTION_REQUIRED')
        if sum((D(g.quantity_limit) for g in authority.groups),D(0))*D(authority.units.quantity_to_position)>abs(actual):reasons.append('OWNED_REDUCTION_OVERALLOCATED')
        if authority.fee_budgets is not None and any(D(f.amount)>0 for f in authority.fee_budgets):reasons.append('ZERO_ASSET_REDUCTION_HAS_FEES')
    if not _proof(reader,'FUNDING_AUTHORITY',proof_ref,authority,authority,None,now):reasons.append('AUTHORITY_PROOF_UNVERIFIED')
    if reasons:return _result(None,reasons,proof_ref=proof_ref)
    pools=tuple(GroupPoolV3(group_id=g.group_id,unallocated_quantity=g.quantity_limit) for g in authority.groups
        if any(n.child_mode=='ALGORITHM' and n.group_id==g.group_id for n in authority.nodes))
    state=ProgramStateV3(contract_version=VERSION,program_id=authority.program_id,authority_hash=digest(authority),authority_proof_ref=proof_ref,
        prepared_at=now,sequence=1,status='PREPARED',binding=None,binding_proof_ref=None,memberships=(),exclusivities=(),receipts=(),pools=pools,
        children=(),events=(),parent_observation=None,projections=(),fee_totals=(),net_position_quantity=authority.starting_position_quantity,
        required_protection_quantity=format(abs(D(authority.starting_position_quantity)),'f'),protection_status='UNVERIFIED_DUTY',restriction_codes=())
    return LifecycleResultV3(state=state,restrictions=(),retained=None,proof_ref=proof_ref)


@_exact
def bind_parent(authority,state,binding,proof_ref=None,*,reader=None,now):
    _checked('parent-binding',binding)
    if not _base(authority,state,reader,now):return _result(state,('AUTHORITY_STATE_UNVERIFIED',),binding,proof_ref)
    native=authority.execution_mode=='VENUE_NATIVE'
    if (binding.authority_hash!=digest(authority) or binding.scope!=authority.scope or binding.venue!=authority.venue
        or binding.namespace!=authority.namespace or binding.parent_command_id!=authority.parent_command_id
        or binding.parent_client_id!=authority.parent_client_id
        or (binding.binding_kind=='VENUE_ACCEPTANCE')!=native
        or not exact_time(state.prepared_at)<=exact_time(binding.bound_at)<=exact_time(now)):
        return _result(state,('ORIGINAL_PARENT_BINDING_MISMATCH',),binding,proof_ref)
    if not _proof(reader,'PARENT_BINDING',proof_ref,binding,authority,binding,now):return _result(state,('PARENT_BINDING_PROOF_UNVERIFIED',),binding,proof_ref)
    if state.binding is not None:
        if state.binding==binding:return _result(state,retained=binding,proof_ref=proof_ref,duplicate=True)
        return _result(state,('IMMUTABLE_PARENT_IDENTITY',),binding,proof_ref)
    if state.status not in ('PREPARED','RECONCILIATION_REQUIRED'):return _result(state,('PARENT_RECONCILIATION_REQUIRED',),binding,proof_ref)
    status='RECONCILIATION_REQUIRED' if state.status=='RECONCILIATION_REQUIRED' else 'BOUND'
    return _result(replace(state,binding=binding,binding_proof_ref=proof_ref,status=status),retained=binding,proof_ref=proof_ref)


def _binding_verified(authority,state,reader,now):
    return (state.binding is not None and state.binding_proof_ref is not None
        and exact_time(state.binding_proof_ref.visible_at)<=exact_time(now)
        and _proof(reader,'PARENT_BINDING',state.binding_proof_ref,state.binding,authority,state.binding,state.binding_proof_ref.visible_at))


def _member_verified(authority,state,proven,reader,now):
    return (exact_time(proven.proof_ref.visible_at)<=exact_time(now)
        and _proof(reader,'CHILD_MEMBERSHIP',proven.proof_ref,proven.membership,authority,state.binding,proven.proof_ref.visible_at))


@_exact
def register_membership(authority,state,member,proof_ref=None,*,reader=None,now):
    _checked('child-membership',member)
    if not _base(authority,state,reader,now) or not _binding_verified(authority,state,reader,now):
        return _result(state,('PARENT_BINDING_UNVERIFIED',),member,proof_ref)
    node=next((n for n in authority.nodes if n.node_id==member.node_id),None)
    if (node is None or member.authority_hash!=digest(authority) or member.binding_hash!=digest(state.binding)
        or member.scope!=authority.scope or member.venue!=authority.venue or member.parent_namespace!=authority.namespace
        or member.group_id!=node.group_id or member.origin!=node.origin):
        return _result(state,('CHILD_MEMBERSHIP_MISMATCH',),member,proof_ref)
    if not _proof(reader,'CHILD_MEMBERSHIP',proof_ref,member,authority,state.binding,now):
        return _result(state,('CHILD_MEMBERSHIP_PROOF_UNVERIFIED',),member,proof_ref)
    if member.mapping_basis=='OUTBOUND_CLIENT_ID' and (node.expected_client_id is None or member.child_client_id!=node.expected_client_id):
        return _result(state,('OUTBOUND_CLIENT_MAPPING_MISMATCH',),member,proof_ref)
    if member.mapping_basis=='NATIVE_PARENT_CHILD_QUERY' and state.binding.binding_kind!='VENUE_ACCEPTANCE':
        return _result(state,('NATIVE_PARENT_QUERY_MAPPING_REQUIRED',),member,proof_ref)
    if authority.template=='ATOMIC' and authority.namespace=='ORDINARY' and state.binding.binding_kind=='VENUE_ACCEPTANCE':
        if (member.origin!='APPLICATION_GENERATED_CHILD' or member.command_id!=authority.parent_command_id
            or member.child_order_id!=state.binding.parent_venue_id or member.child_client_id!=authority.parent_client_id):
            return _result(state,('ORDINARY_ROOT_COMMAND_MAPPING_REQUIRED',),member,proof_ref)
    for old in state.memberships:
        m=old.membership
        if m==member:return _result(state,retained=member,proof_ref=proof_ref,duplicate=True)
        if (m.membership_id==member.membership_id or m.child_order_id==member.child_order_id
            or member.child_client_id is not None and m.child_client_id==member.child_client_id
            or member.command_id is not None and m.command_id==member.command_id):
            return _result(state,('CHILD_IDENTITY_REUSED',),member,proof_ref)
        if node.child_mode=='FIXED_LEG' and m.node_id==node.node_id:return _result(state,('FIXED_NODE_ALREADY_BOUND',),member,proof_ref)
    updated=replace(state,memberships=(*state.memberships,ProvenMembershipV3(membership=member,proof_ref=proof_ref)))
    late=('LATE_MEMBERSHIP_RECONCILIATION_REQUIRED',) if state.status in ('STOPPED','COMPLETED') else ()
    return _result(updated,late,member,proof_ref)


def _exclusive_valid(authority,state,entry,reader,now):
    x=entry.exclusivity
    group=next((g for g in authority.groups if g.group_id==x.group_id),None)
    members=[p for p in state.memberships if p.membership.group_id==x.group_id]
    return (group is not None and group.aggregation=='NATIVE_EXCLUSIVE' and authority.execution_mode=='VENUE_NATIVE'
        and state.binding is not None and state.binding.binding_kind=='VENUE_ACCEPTANCE'
        and x.authority_hash==digest(authority) and x.binding_hash==digest(state.binding) and x.scope==authority.scope
        and x.venue==authority.venue and x.namespace==authority.namespace and x.template==authority.template
        and set(x.node_ids)==set(group.node_ids)=={p.membership.node_id for p in members}
        and len(members)==len(group.node_ids) and all(_member_verified(authority,state,p,reader,now) for p in members)
        and x.membership_hash==membership_digest(state,x.group_id) and x.rule_revision==authority.rule_revision
        and x.rule_snapshot_hash==authority.rule_snapshot_hash and x.adapter_build_digest==authority.adapter_build_digest
        and _proof(reader,'NATIVE_EXCLUSIVITY',entry.proof_ref,x,authority,state.binding,now,x.membership_hash))


@_exact
def register_exclusivity(authority,state,guarantee,proof_ref=None,*,reader=None,now):
    _checked('native-exclusivity',guarantee)
    if not _base(authority,state,reader,now):return _result(state,('AUTHORITY_STATE_UNVERIFIED',),guarantee,proof_ref)
    entry=ProvenExclusivityV3(exclusivity=guarantee,proof_ref=proof_ref) if proof_ref is not None else None
    if entry is None or not _exclusive_valid(authority,state,entry,reader,now):
        return _result(state,('NATIVE_EXCLUSIVITY_PROOF_UNVERIFIED',),guarantee,proof_ref)
    old=next((e for e in state.exclusivities if e.exclusivity.group_id==guarantee.group_id),None)
    if old==entry:return _result(state,retained=guarantee,proof_ref=proof_ref,duplicate=True)
    if old is not None and old.exclusivity!=guarantee:return _result(state,('EXCLUSIVE_MEMBERSHIP_CHANGED',),guarantee,proof_ref)
    updated=replace(state,exclusivities=tuple(e for e in state.exclusivities if e.exclusivity.group_id!=guarantee.group_id)+(entry,))
    return _result(updated,retained=guarantee,proof_ref=proof_ref)


def _node_states(state,node_id):
    ids={p.membership.membership_id for p in state.memberships if p.membership.node_id==node_id}
    return tuple(c for c in state.children if c.observation.membership_id in ids)


def _receipt_for(state,node_id):
    return next((p.receipt for p in state.receipts if p.receipt.source_node_id==node_id),None)


def _project(authority,state,reader,now):
    errors=[];stats={};fees={};fees_known=True;position_known=True
    units=authority.units;net=D(authority.starting_position_quantity);seen_trades=set()
    if state.unattributed_effects:
        errors.append('UNATTRIBUTED_EFFECTS_REMAIN');position_known=False;fees_known=False
    if state.parent_observation is not None and state.parent_observation.status in ('SUBMIT_UNKNOWN','CANCEL_UNKNOWN'):
        errors.append('PARENT_STATE_UNKNOWN')
    for node in authority.nodes:
        children=_node_states(state,node.node_id);filled=D(0);reported=D(0);remaining=D(0);spent=D(0)
        raw_known=True;remaining_known=True;spent_known=True;unsettled=[]
        if not children and node.child_mode=='FIXED_LEG':
            allocation=next((p.receipt for p in state.receipts if p.receipt.target_group_id==node.group_id),None)
            remaining=D(allocation.pending_quantity if allocation and node.quantity_mode=='NET_RECEIPT' else node.quantity_limit)
            if (authority.execution_mode!='VENUE_NATIVE' and not any(p.membership.node_id==node.node_id for p in state.memberships)
                and state.parent_observation is not None and state.accepted_parent_status in ('STOPPED','COMPLETED')
                and state.parent_observation.child_inventory=='COMPLETE'):
                remaining=D(0)  # Proven local schedule stopped before this child existed.
            raw_known=False
            unsettled.extend(p.membership.child_order_id for p in state.memberships if p.membership.node_id==node.node_id)
        for child in children:
            event=child.observation;cum=D(event.cumulative_quantity);filled+=cum
            if event.status in ('UNKNOWN','CANCEL_UNKNOWN'):errors.append('CHILD_STATE_UNKNOWN')
            if event.quantity is None and not (node.quantity_mode=='NET_RECEIPT' and cum==0
                and event.status in ('PENDING_NEW','CANCELED','EXPIRED','REJECTED')):
                errors.append('CHILD_QUANTITY_UNKNOWN')
            net+=cum*D(units.quantity_to_position)*(1 if node.side=='BUY' else -1)
            if event.quantity_unit!=units.quantity_unit:errors.append('CHILD_QUANTITY_UNIT_MISMATCH');position_known=False
            if event.leaves_quantity is None:raw_known=False
            else:reported+=D(event.leaves_quantity)
            if event.status not in TERMINAL:
                unsettled.append(event.child_order_id)
                if event.status=='PENDING_NEW' and node.quantity_mode=='NET_RECEIPT' and event.quantity is None:
                    allocation=next((p.receipt for p in state.receipts if p.receipt.target_group_id==node.group_id),None)
                    remaining+=D(allocation.pending_quantity if allocation else node.quantity_limit)
                elif event.quantity is None or event.leaves_quantity is None:
                    remaining_known=False;errors.append('CHILD_QUANTITY_UNKNOWN')
                else:
                    computed=max(D(event.quantity)-cum,D(0));raw=D(event.leaves_quantity)
                    if raw!=computed:errors.append('REPORTED_LEAVES_CONFLICT')
                    # Retain the larger unresolved claim, never clip to a cap.
                    remaining+=max(computed,raw)
            total=sum((D(f.quantity) for f in child.fills),D(0))
            if total!=cum:spent_known=False;fees_known=False;position_known=False;errors.append('FILL_DETAILS_MISSING')
            for fill in child.fills:
                if fill.native_trade_id in seen_trades:errors.append('TRADE_IDENTITY_CONFLICT');spent_known=False;position_known=False
                seen_trades.add(fill.native_trade_id)
                if fill.quantity_unit!=units.quantity_unit or fill.price_asset!=units.quote_asset:
                    spent_known=False;position_known=False;errors.append('FILL_UNIT_OR_ASSET_MISMATCH')
                else:
                    spent+=D(fill.quantity)*D(fill.price)*D(units.notional_multiplier)
                if D(fill.price)>D(node.price_upper):errors.append('EXECUTION_PRICE_BOUND_VIOLATION')
                if fill.fees is None:
                    fees_known=False;errors.append('FEE_DATA_UNKNOWN')
                    if authority.scope.product!=t.Product.USD_M_PERP:position_known=False
                    continue
                for fee in fill.fees:
                    fees[fee.asset]=fees.get(fee.asset,D(0))+D(fee.amount)
                    if fee.receipt_effect=='DEDUCT_RECEIVED_ASSET':
                        expected=units.base_asset if node.side=='BUY' else units.quote_asset
                        if authority.scope.product==t.Product.USD_M_PERP or fee.asset!=expected:
                            errors.append('FEE_RECEIPT_ATTRIBUTION_CONFLICT');position_known=False
                        if node.side=='BUY' and fee.position_effect!='DEDUCT_OWNED_BASE':
                            errors.append('RECEIPT_POSITION_ATTRIBUTION_CONFLICT');position_known=False
                    if fee.position_effect=='DEDUCT_OWNED_BASE':
                        if authority.scope.product==t.Product.USD_M_PERP or fee.asset!=units.base_asset or units.position_asset_per_unit is None:
                            errors.append('FEE_POSITION_UNIT_CONFLICT');position_known=False
                        else:net-=D(fee.amount)/D(units.position_asset_per_unit)
        if node.child_mode=='ALGORITHM':
            pool=next((p for p in state.pools if p.group_id==node.group_id),None)
            if pool is None or pool.unallocated_quantity is None:remaining_known=False;raw_known=False;errors.append('ALGORITHM_POOL_UNKNOWN')
            else:remaining+=D(pool.unallocated_quantity);reported+=D(pool.unallocated_quantity)
        if remaining_known and filled+remaining>D(node.quantity_limit):errors.append('NODE_QUANTITY_EXCEEDED')
        stats[node.node_id]=(filled,reported if raw_known else None,remaining if remaining_known else None,
                             spent if spent_known and not state.unattributed_effects else None,tuple(unsettled))
    projections=[];aggregate=D(0);aggregate_known=True
    for group in authority.groups:
        entries=[stats[n] for n in group.node_ids];filled=sum((x[0] for x in entries),D(0))
        raw=None if any(x[1] is None for x in entries) else sum((x[1] for x in entries),D(0))
        actual=None if any(x[3] is None for x in entries) else sum((x[3] for x in entries),D(0))
        entry=next((x for x in state.exclusivities if x.exclusivity.group_id==group.group_id),None)
        exclusive=entry is not None and _exclusive_valid(authority,state,entry,reader,now)
        if entry is not None and not exclusive:errors.append('NATIVE_EXCLUSIVITY_PROOF_UNVERIFIED')
        executing=[n for n in group.node_ids if stats[n][0]>0]
        if exclusive and len(executing)>1:errors.append('NATIVE_EXCLUSIVITY_VIOLATION');exclusive=False
        selected=executing[0] if exclusive and len(executing)==1 else None
        considered=(selected,) if selected else group.node_ids
        if any(stats[n][2] is None for n in considered):future=None;future_notional=None
        else:
            quantities=[stats[n][2] for n in considered]
            notionals=[stats[n][2]*D(next(x.price_upper for x in authority.nodes if x.node_id==n))*D(units.notional_multiplier) for n in considered]
            future=max(quantities) if exclusive else sum(quantities,D(0))
            future_notional=max(notionals) if exclusive else sum(notionals,D(0))
        if future is not None and filled+future>D(group.quantity_limit):errors.append('GROUP_QUANTITY_EXCEEDED')
        if future and exact_time(now)>=exact_time(authority.budget_valid_until):future_notional=None;errors.append('DYNAMIC_BOUND_EXPIRED')
        if actual is None or future_notional is None:
            aggregate_known=False;errors.append('GROUP_NOTIONAL_UNKNOWN')
        else:
            if actual+future_notional>D(group.notional_limit):errors.append('GROUP_NOTIONAL_EXCEEDED')
            aggregate+=actual+future_notional
        projections.append(GroupProjectionV3(group_id=group.group_id,aggregation='VERIFIED_NATIVE_EXCLUSIVE' if exclusive else 'SUM',
            cumulative_quantity=format(filled,'f'),reported_remaining_quantity=None if raw is None else format(raw,'f'),
            effective_remaining_quantity=None if future is None else format(future,'f'),actual_notional=None if actual is None else format(actual,'f'),
            possible_notional=None if future_notional is None else format(future_notional,'f'),executing_node_id=selected,
            unsettled_child_ids=tuple(cid for n in group.node_ids for cid in stats[n][4])))
    if authority.fee_budgets is None:fees_known=False;errors.append('FEE_BUDGET_UNKNOWN')
    else:
        limits={f.asset:D(f.amount) for f in authority.fee_budgets}
        if any(value>limits.get(asset,D(0)) for asset,value in fees.items()):errors.append('FEE_BUDGET_EXCEEDED')
    if aggregate_known and fees_known and aggregate+fees.get(units.quote_asset,D(0))>D(authority.maximum_notional):errors.append('PARENT_NOTIONAL_EXCEEDED')
    if position_known and authority.scope.product!=t.Product.USD_M_PERP and net<0:errors.append('CASH_NEGATIVE_POSITION')
    updated=replace(state,projections=tuple(projections),fee_totals=tuple(AssetBudget(asset=a,amount=format(v,'f')) for a,v in sorted(fees.items())) if fees_known else None,
        net_position_quantity=format(net,'f') if position_known else None,required_protection_quantity=format(abs(net),'f') if position_known else None)
    return updated,tuple(dict.fromkeys(errors))


@_exact
def activate(authority,state,*,reader=None,now):
    if not _base(authority,state,reader,now) or not _binding_verified(authority,state,reader,now):return _result(state,('PARENT_BINDING_UNVERIFIED',))
    if state.status not in ('BOUND','ACTIVE'):return _result(state,('PARENT_RECONCILIATION_REQUIRED',))
    if authority.execution_mode=='VENUE_NATIVE' and any(n.child_mode=='FIXED_LEG' and not any(p.membership.node_id==n.node_id for p in state.memberships) for n in authority.nodes):
        return _result(state,('FIXED_MEMBERSHIP_INCOMPLETE',))
    if any(not _member_verified(authority,state,p,reader,now) for p in state.memberships):return _result(state,('MEMBERSHIP_PROOF_UNVERIFIED',))
    updated,errors=_project(authority,replace(state,status='ACTIVE'),reader,now)
    return _result(updated,errors)


@_exact
def apply_child(authority,state,event,proof_ref=None,*,reader=None,now):
    _checked('child-observation',event)
    if not _base(authority,state,reader,now) or not _binding_verified(authority,state,reader,now):
        return _result(state,('PARENT_BINDING_UNVERIFIED',),event,proof_ref,unknown_effect=True)
    member=next((p for p in state.memberships if p.membership.membership_id==event.membership_id),None)
    mh=digest(member.membership) if member else None
    if not _proof(reader,'LIFECYCLE_EVENT',proof_ref,event,authority,state.binding,now,mh):
        return _result(state,('EVENT_SOURCE_UNVERIFIED',),event,proof_ref,unknown_effect=True)
    if not exact_time(event.occurred_at)<=exact_time(event.source_evidence.visible_at)<=exact_time(now):
        return _result(state,('EVENT_TIME_UNVERIFIED',),event,proof_ref,unknown_effect=True)
    if (member is None or not _member_verified(authority,state,member,reader,now)
        or event.scope!=authority.scope or event.venue!=authority.venue or event.parent_namespace!=authority.namespace
        or event.parent_client_id!=state.binding.parent_client_id or event.parent_venue_id!=state.binding.parent_venue_id
        or event.child_order_id!=member.membership.child_order_id or event.child_client_id!=member.membership.child_client_id):
        return _result(state,('CHILD_MEMBERSHIP_UNVERIFIED',),event,proof_ref,unknown_effect=True)
    prior=next((e for e in state.events if e.kind=='CHILD' and e.observation_id==event.observation_id),None)
    if prior:
        if prior.payload_hash==digest(event):return _result(state,retained=event,proof_ref=proof_ref,duplicate=True)
        return _result(state,('EVENT_CONTENT_CONFLICT',),event,proof_ref,unknown_effect=True)
    node=next(n for n in authority.nodes if n.node_id==member.membership.node_id)
    if event.side!=node.side or event.quantity_unit!=authority.units.quantity_unit:
        return _result(state,('CHILD_SIDE_OR_UNIT_CONFLICT',),event,proof_ref,unknown_effect=True)
    old=next((c for c in state.children if c.observation.child_order_id==event.child_order_id),None)
    if old:
        o=old.observation
        if event.report_sequence<=o.report_sequence or exact_time(event.occurred_at)<exact_time(o.occurred_at):
            return _result(state,('OUT_OF_ORDER_CHILD_OBSERVATION',),event,proof_ref,unknown_effect=True)
        if D(event.cumulative_quantity)<D(o.cumulative_quantity):return _result(state,('CUMULATIVE_REGRESSION',),event,proof_ref,unknown_effect=True)
        if o.quantity is not None and (event.quantity is None or D(event.quantity)!=D(o.quantity)):
            return _result(state,('CHILD_QUANTITY_CHANGED',),event,proof_ref,unknown_effect=True)
        if o.status in TERMINAL and (event.status!=o.status or D(event.cumulative_quantity)!=D(o.cumulative_quantity)):
            return _result(state,('CANCEL_FILL_RACE' if o.status in ('CANCELED','EXPIRED') else 'TERMINAL_STATE_CONFLICT',),event,proof_ref,unknown_effect=True)
        if (o.status=='PARTIALLY_FILLED' and event.status in ('SUBMITTED','PENDING_NEW')) or (o.status=='CANCEL_PENDING' and event.status in ('SUBMITTED','PARTIALLY_FILLED','PENDING_NEW')):
            return _result(state,('CHILD_STATE_REGRESSION',),event,proof_ref,unknown_effect=True)
    reasons=[]
    if state.status not in ('ACTIVE','STOPPED','COMPLETED','RECONCILIATION_REQUIRED'):reasons.append('PARENT_STATE_UNSETTLED')
    if state.status=='CANCEL_PENDING':reasons.append('PARENT_CANCEL_RACE')
    if old is None and state.status in ('STOPPED','COMPLETED'):reasons.append('CHILD_AFTER_PARENT_STOP')
    if event.status in ('UNKNOWN','CANCEL_UNKNOWN'):reasons.append('CHILD_STATE_UNKNOWN')
    if event.quantity is not None:
        q=D(event.quantity);cum=D(event.cumulative_quantity)
        if q>D(node.quantity_limit) or cum>q:reasons.append('CHILD_OVERFILL')
        if event.status=='FILLED' and q!=cum or event.status in ('SUBMITTED','PENDING_NEW','REJECTED') and cum!=0:
            reasons.append('CHILD_STATUS_QUANTITY_CONFLICT')
        if event.status=='PARTIALLY_FILLED' and not 0<cum<q:reasons.append('CHILD_STATUS_QUANTITY_CONFLICT')
    if node.activation!='IMMEDIATE' and event.status not in ('PENDING_NEW','CANCELED','EXPIRED','REJECTED'):
        source=_node_states(state,node.source_node_id)
        if len(source)!=1 or source[0].observation.status!='FILLED':reasons.append('FULL_FILL_ACTIVATION_UNPROVEN')
        if node.quantity_mode=='NET_RECEIPT':
            receipt=next((p for p in state.receipts if p.receipt.target_group_id==node.group_id),None)
            if receipt is None or event.quantity is None or D(event.quantity)!=D(receipt.receipt.pending_quantity):reasons.append('NET_RECEIPT_ALLOCATION_UNPROVEN')
    fills={f.native_trade_id:f for f in old.fills} if old else {}
    other={f.native_trade_id for c in state.children if c.observation.child_order_id!=event.child_order_id for f in c.fills}
    for fill in event.fills:
        if fill.native_trade_id in other:return _result(state,('TRADE_IDENTITY_CONFLICT',),event,proof_ref,unknown_effect=True)
        if fill.correction_of is not None:return _result(state,('FILL_CORRECTION_REVIEW_REQUIRED',),event,proof_ref,unknown_effect=True)
        previous=fills.get(fill.native_trade_id)
        if previous is not None and previous!=fill:
            # Authenticated refinement of explicitly unknown fees is not a rewrite
            # of known fee history. The reconciliation state remains sticky.
            if previous.fees is not None or replace(previous,fees=fill.fees)!=fill:
                return _result(state,('TRADE_CONTENT_CONFLICT',),event,proof_ref,unknown_effect=True)
        if exact_time(fill.occurred_at)>exact_time(event.occurred_at):return _result(state,('FUTURE_FILL',),event,proof_ref,unknown_effect=True)
        fills[fill.native_trade_id]=fill
    pools=state.pools
    if node.child_mode=='ALGORITHM' and event.quantity is not None and (old is None or old.observation.quantity is None):
        pool=next((p for p in pools if p.group_id==node.group_id),None)
        if pool is None or pool.unallocated_quantity is None:reasons.append('ALGORITHM_POOL_UNKNOWN')
        else:
            left=D(pool.unallocated_quantity)-D(event.quantity)
            if left<0:reasons.append('ALGORITHM_POOL_OVERALLOCATED')
            else:pools=tuple(replace(p,unallocated_quantity=format(left,'f')) if p.group_id==node.group_id else p for p in pools)
    child=ChildStateV3(observation=event,event_hash=digest(event),fills=tuple(fills.values()))
    candidate=replace(state,pools=pools,children=tuple(c for c in state.children if c.observation.child_order_id!=event.child_order_id)+(child,),
        events=(*state.events,EventDigestV3(observation_id=event.observation_id,payload_hash=digest(event),kind='CHILD')))
    updated,errors=_project(authority,candidate,reader,now)
    return _result(updated,(*reasons,*errors),event,proof_ref)


PARENT_TRANSITIONS={
    'ACTIVE':{'ACTIVE','CANCEL_PENDING','STOPPED','COMPLETED'},
    'CANCEL_PENDING':{'CANCEL_PENDING','STOPPED','COMPLETED'},
    'STOPPED':{'STOPPED','COMPLETED'},'COMPLETED':{'COMPLETED'},
}


@_exact
def apply_parent(authority,state,event,proof_ref=None,*,reader=None,now):
    _checked('parent-observation',event)
    if not _base(authority,state,reader,now):return _result(state,('AUTHORITY_STATE_UNVERIFIED',),event,proof_ref,unknown_effect=True)
    mh=membership_digest(state)
    if not _proof(reader,'LIFECYCLE_EVENT',proof_ref,event,authority,state.binding,now,mh):
        return _result(state,('EVENT_SOURCE_UNVERIFIED',),event,proof_ref,unknown_effect=True)
    if not exact_time(event.occurred_at)<=exact_time(event.source_evidence.visible_at)<=exact_time(now):
        return _result(state,('EVENT_TIME_UNVERIFIED',),event,proof_ref,unknown_effect=True)
    expected_venue=state.binding.parent_venue_id if state.binding else None
    if (event.scope!=authority.scope or event.venue!=authority.venue or event.namespace!=authority.namespace
        or event.parent_client_id!=authority.parent_client_id or event.parent_venue_id!=expected_venue):
        return _result(state,('ORIGINAL_PARENT_BINDING_MISMATCH',),event,proof_ref,unknown_effect=True)
    prior=next((e for e in state.events if e.kind=='PARENT' and e.observation_id==event.observation_id),None)
    if prior:
        if prior.payload_hash==digest(event):return _result(state,retained=event,proof_ref=proof_ref,duplicate=True)
        return _result(state,('EVENT_CONTENT_CONFLICT',),event,proof_ref,unknown_effect=True)
    old=state.parent_observation
    if old and (event.report_sequence<=old.report_sequence or exact_time(event.occurred_at)<exact_time(old.occurred_at)):
        return _result(state,('OUT_OF_ORDER_PARENT_OBSERVATION',),event,proof_ref,unknown_effect=True)
    history=(*state.events,EventDigestV3(observation_id=event.observation_id,payload_hash=digest(event),kind='PARENT'))
    candidate=replace(state,parent_observation=event,events=history)
    if event.status in ('SUBMIT_UNKNOWN','CANCEL_UNKNOWN'):
        return _result(candidate,('PARENT_STATE_UNKNOWN',),event,proof_ref,unknown_effect=True)
    if state.binding is None:return _result(candidate,('PARENT_BINDING_REQUIRED',),event,proof_ref,unknown_effect=True)
    if not _binding_verified(authority,state,reader,now):return _result(candidate,('PARENT_BINDING_UNVERIFIED',),event,proof_ref,unknown_effect=True)
    previous_phase=state.accepted_parent_status or (state.status if state.status in PARENT_TRANSITIONS else 'ACTIVE')
    if event.status not in PARENT_TRANSITIONS.get(previous_phase,set()):
        return _result(candidate,('PARENT_STATE_REGRESSION',),event,proof_ref,unknown_effect=True)
    if {g.group_id for g in event.groups}!={g.group_id for g in authority.groups}:
        return _result(candidate,('PARENT_GROUP_COVERAGE_INCOMPLETE',),event,proof_ref,unknown_effect=True)
    known={m.membership.child_order_id for m in state.memberships}
    if event.child_inventory!='COMPLETE' or set(event.child_order_ids)!=known:
        return _result(candidate,('CHILD_INVENTORY_INCOMPLETE',),event,proof_ref,unknown_effect=True)
    reasons=[];pools=[]
    for group in authority.groups:
        report=next(g for g in event.groups if g.group_id==group.group_id)
        ids={m.membership.membership_id for m in state.memberships if m.membership.group_id==group.group_id}
        filled=sum((D(c.observation.cumulative_quantity) for c in state.children if c.observation.membership_id in ids),D(0))
        if filled!=D(report.cumulative_quantity):
            return _result(candidate,('CHILD_EVIDENCE_INCOMPLETE',),event,proof_ref,unknown_effect=True)
        algorithm=any(n.child_mode=='ALGORITHM' and n.group_id==group.group_id for n in authority.nodes)
        if algorithm:
            pools.append(GroupPoolV3(group_id=group.group_id,unallocated_quantity=report.unallocated_quantity))
            if report.unallocated_quantity is None:reasons.append('ALGORITHM_POOL_UNKNOWN')
            elif event.status in ('STOPPED','COMPLETED') and D(report.unallocated_quantity)!=0:reasons.append('PARENT_STOP_UNVERIFIED')
        elif report.unallocated_quantity is not None:
            reasons.append('FIXED_GROUP_UNALLOCATED_NOT_APPLICABLE')
    # Confirmed parent stop never fabricates child terminal reports.
    if event.status=='COMPLETED':
        observed={c.observation.child_order_id:c for c in state.children}
        if set(observed)!=known or any(c.observation.status not in TERMINAL for c in state.children):
            reasons.append('CHILDREN_STILL_UNSETTLED')
    candidate=replace(candidate,pools=tuple(pools),
        status=state.status if state.status=='RECONCILIATION_REQUIRED' or reasons else event.status,
        accepted_parent_status=state.accepted_parent_status if reasons else event.status)
    updated,errors=_project(authority,candidate,reader,now)
    return _result(updated,(*reasons,*errors),event,proof_ref)


@_exact
def apply_receipt(authority,state,receipt,proof_ref=None,*,reader=None,now):
    _checked('receipt-allocation',receipt)
    if not _base(authority,state,reader,now) or not _binding_verified(authority,state,reader,now):
        return _result(state,('PARENT_BINDING_UNVERIFIED',),receipt,proof_ref,unknown_effect=True)
    mh=membership_digest(state,receipt.target_group_id)
    if not _proof(reader,'RECEIPT_ALLOCATION',proof_ref,receipt,authority,state.binding,now,mh):
        return _result(state,('RECEIPT_PROOF_UNVERIFIED',),receipt,proof_ref,unknown_effect=True)
    if not exact_time(receipt.occurred_at)<=exact_time(receipt.source_evidence.visible_at)<=exact_time(now):
        return _result(state,('RECEIPT_TIME_UNVERIFIED',),receipt,proof_ref,unknown_effect=True)
    targets=[n for n in authority.nodes if n.group_id==receipt.target_group_id]
    source=next((n for n in authority.nodes if n.node_id==receipt.source_node_id),None)
    if (authority.template not in ('OPO','OPOCO') or authority.scope.product!=t.Product.SPOT
        or receipt.authority_hash!=digest(authority) or receipt.binding_hash!=digest(state.binding) or receipt.scope!=authority.scope
        or source is None or source.side!='BUY' or not targets
        or any(n.source_node_id!=source.node_id or n.quantity_mode!='NET_RECEIPT' or n.side!='SELL' for n in targets)
        or receipt.asset!=authority.units.base_asset or receipt.quantity_unit!=authority.units.quantity_unit
        or receipt.rule_snapshot_hash!=authority.rule_snapshot_hash):
        return _result(state,('RECEIPT_BINDING_MISMATCH',),receipt,proof_ref,unknown_effect=True)
    old=next((p for p in state.receipts if p.receipt.target_group_id==receipt.target_group_id),None)
    if old:
        if old.receipt==receipt:return _result(state,retained=receipt,proof_ref=proof_ref,duplicate=True)
        return _result(state,('RECEIPT_ALLOCATION_CONFLICT',),receipt,proof_ref,unknown_effect=True)
    children=_node_states(state,source.node_id)
    if len(children)!=1 or children[0].observation.status!='FILLED' or children[0].observation.quantity is None:
        return _result(state,('RECEIPT_REQUIRES_FULL_FILL',),receipt,proof_ref,unknown_effect=True)
    child=children[0];gross=sum((D(f.quantity) for f in child.fills),D(0))
    if (gross!=D(child.observation.quantity) or gross!=D(child.observation.cumulative_quantity)
        or receipt.source_fill_hash!=source_fill_digest(state,source.node_id)):
        return _result(state,('RECEIPT_FILL_EVIDENCE_MISMATCH',),receipt,proof_ref,unknown_effect=True)
    if any(f.fees is None for f in child.fills):return _result(state,('RECEIPT_FEES_UNKNOWN',),receipt,proof_ref,unknown_effect=True)
    deducted=sum((D(fee.amount) for f in child.fills for fee in f.fees if fee.receipt_effect=='DEDUCT_RECEIVED_ASSET' and fee.asset==receipt.asset),D(0))
    locked=D(receipt.locked_quantity);pending=D(receipt.pending_quantity);unused=D(receipt.unused_quantity);step=D(receipt.lot_step)
    if (D(receipt.gross_received_quantity)!=gross or D(receipt.receipt_commission_quantity)!=deducted
        or locked!=gross-deducted or pending+unused!=locked or pending%step!=0 or not 0<=unused<step
        or any(pending>D(n.quantity_limit) for n in targets)):
        return _result(state,('NET_RECEIPT_OR_LOT_PROOF_CONFLICT',),receipt,proof_ref,unknown_effect=True)
    if state.net_position_quantity is None or pending*D(authority.units.quantity_to_position)>D(state.net_position_quantity):
        return _result(state,('RECEIPT_EXCEEDS_OWNED_POSITION',),receipt,proof_ref,unknown_effect=True)
    candidate=replace(state,receipts=(*state.receipts,ProvenReceiptV3(receipt=receipt,proof_ref=proof_ref)))
    updated,errors=_project(authority,candidate,reader,now)
    return _result(updated,errors,receipt,proof_ref)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProjectionReviewV3:
    result:LifecycleResultV3
    current_restrictions:tuple[str,...]
    basis_hash:str


@_exact
def review_projection(authority,state,*,reader=None,now):
    """Reassess late proof without clearing historical risk/protection duty."""
    if not _base(authority,state,reader,now) or not _binding_verified(authority,state,reader,now):
        errors=('PARENT_BINDING_UNVERIFIED',)
        return ProjectionReviewV3(result=_result(state,errors),current_restrictions=errors,basis_hash=digest(state))
    updated,errors=_project(authority,state,reader,now)
    if any(not _member_verified(authority,state,p,reader,now) for p in state.memberships):errors=(*errors,'MEMBERSHIP_PROOF_UNVERIFIED')
    return ProjectionReviewV3(result=_result(updated,errors),current_restrictions=tuple(dict.fromkeys(errors)),basis_hash=digest(state))
