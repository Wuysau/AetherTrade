"""Internal local activation in the financial transaction; never a venue write.

Proof bytes are reconstructed from bounded intake and actual SQL financial
artifacts, including full compiler/allocation/risk replay. Stored proof objects,
owner audit IDs, callbacks and dataclass construction alone grant nothing.
"""
from dataclasses import dataclass
from decimal import Decimal,localcontext,Inexact,Rounded
from typing import Literal
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_lifecycle import AssetBudget
from trading_system.contracts.order_lifecycle_v3 import (ParentAuthorityV3,ParentBindingV3,QuantityTermsV3,
    NodeAllocationV3,AllocationGroupV3,LifecycleProofV3,VERSION)
from trading_system.contracts.order_lifecycle_v3_reducer import prepare,bind_parent,activate,proof_context
from trading_system.contracts.program_financial import require
from trading_system.intents.program_assessment_archive import (financial_intent_from_assessment_archive,
    financial_risk_basis_from_assessment_archive)
from trading_system.intents.generic_program_intake import read_program_source
from trading_system.risk.program_financial_admission import (ProgramFinancialBundle,BUNDLE_PREFIX,
    ProgramFinancialAttachment,ATTACHMENT_PREFIX,current_program_bundle_witness,claim_program_attachment)
from trading_system.storage.local_programs import (ProgrammeActivation,ACTIVATION_PREFIX,STATE_PREFIX,PROOF_PREFIX)
from trading_system.storage.models import scope_id,atomic_write,decode
from trading_system.storage.order_programs import OrderProgramRepository,FundingLink


def digest(value):return canonical_hash(domain_payload(value))
def text(value):return format(value,'f')


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalActivationReceipt:
    activation_id:str
    financial_intent_id:str
    bundle_hash:str
    @property
    def execution_authorized(self)->Literal[False]:return False


def _materials(bundle,activation_id,transaction_id,now,expires):
    """Deterministic semantic derivation, called only after SQL source checks."""
    a=bundle.archive;p=a.compilation;allocation=a.allocation;unit=a.target_facts.unit_binding
    original=p.original_program
    ceiling=min(map(utc_timestamp,(bundle.intent.valid_until,a.assessment.expires_at,a.budget_facts.valid_until,
        a.target_facts.valid_until,unit.valid_until,a.risk_terms.submission_deadline,
        *(x.reservation.expires_at for x in bundle.reservations))))
    require(utc_timestamp(now)<utc_timestamp(expires)<=ceiling,'LOCAL_AUTHORITY_WINDOW_UNBOUND')
    require(original.template in ('TWAP','VP') and original.execution_mode in ('LOCAL_SYNTHETIC','PAPER_MODEL'),
        'LOCAL_ALGORITHM_REQUIRED')
    require(len(p.nodes)==len(allocation.nodes)==1 and allocation.nodes[0].phase=='ALGORITHM_ENVELOPE',
        'LOCAL_ALGORITHM_ALLOCATION_REQUIRED')
    n=allocation.nodes[0];bound=a.risk_facts.node_bounds[0]
    require(n.native_quantity is not None and n.ledger_quantity is not None and n.native_quantity.value>0
        and bound.node_id==n.node_id and bound.maximum_price is not None,'LOCAL_ALGORITHM_BOUND_REQUIRED')
    require(n.native_quantity.value*unit.ledger_units_per_native_unit==n.ledger_quantity.value,
        'LOCAL_ALGORITHM_UNIT_MISMATCH')
    quantity=n.native_quantity.value;upper=bound.maximum_price.value
    notional=quantity*upper*unit.native_multiplier
    require(notional>0 and notional<=a.assessment.metrics.maximum_order_notional.amount,'LOCAL_ALGORITHM_NOTIONAL_MISMATCH')
    fees={}
    for facts in a.budget_facts.node_facts:
        require(facts.fees is not None,'LOCAL_ALGORITHM_FEES_REQUIRED')
        for fee in facts.fees:fees[fee.amount.currency]=fees.get(fee.amount.currency,Decimal(0))+fee.amount.amount
    refs=lambda name,hash:t.EvidenceRef(id=activation_id+':'+name,hash=hash,environment=p.scope.environment,
        source='SQL_PROGRAM_SOURCE_'+name.upper(),visible_at=now)
    admission=refs('admission',digest(bundle));compilation=refs('compilation',digest(p));target=refs('allocation',digest(allocation))
    reduction=not bundle.reservations
    if reduction:
        require(p.scope.product==t.Product.USD_M_PERP and allocation.delta.classification=='REDUCE'
            and allocation.delta.actual.value!=0 and n.side==('SELL' if allocation.delta.actual.value>0 else 'BUY')
            and n.ledger_quantity.value<=abs(allocation.delta.actual.value)
            and not any(m.amount for m in bundle.basis.incremental_requirements),'LOCAL_OWNED_REDUCTION_REQUIRED')
        position_hash=digest(a.book.positions[0]) if len(a.book.positions)==1 else None
        require(position_hash is not None,'LOCAL_OWNED_POSITION_SOURCE_REQUIRED')
    else:position_hash=None
    group=activation_id+':pool'
    authority=ParentAuthorityV3(contract_version=VERSION,authority_id=activation_id+':authority',program_id=original.program_id,
        program_hash=p.program_hash,compilation_hash=digest(p),target_allocation_hash=digest(allocation),
        funding_admission_hash=digest(bundle),scope=p.scope,venue='BINANCE',namespace='ORDINARY',template=original.template,
        execution_mode=original.execution_mode,parent_command_id=None,parent_client_id=None,
        owner_strategy_id=bundle.intent.owner_strategy_id,ownership_epoch=bundle.intent.ownership_epoch,
        authorization_id=bundle.intent.authorization_id,funding_intent_id=bundle.intent.intent_id,
        reservation_ids=tuple(x.reservation.reservation_id for x in bundle.reservations),
        funding_requirements=tuple(AssetBudget(asset=m.currency,amount=text(m.amount)) for m in bundle.basis.incremental_requirements if m.amount),
        funding_basis='OWNED_POSITION_REDUCTION' if reduction else 'ASSET_RESERVATIONS',position_allocation_hash=position_hash,
        units=QuantityTermsV3(quantity_unit=unit.native_unit,position_unit=unit.ledger_unit,
            quantity_to_position=text(unit.ledger_units_per_native_unit),notional_multiplier=text(unit.native_multiplier),
            base_asset=a.rules.base_asset,quote_asset=a.rules.quote_asset,
            position_asset_per_unit=None if p.scope.product==t.Product.USD_M_PERP else text(Decimal(1)/unit.ledger_units_per_native_unit)),
        unit_evidence=unit.evidence,rule_revision=a.rules.revision,rule_snapshot_hash=p.rules_hash,adapter_build_digest=p.adapter_build_digest,
        nodes=(NodeAllocationV3(node_id=n.node_id,group_id=group,side=n.side,origin='APPLICATION_GENERATED_CHILD',
            child_mode='ALGORITHM',quantity_mode='FIXED',quantity_limit=text(quantity),price_upper=text(upper),
            activation='IMMEDIATE',source_node_id=None),),
        groups=(AllocationGroupV3(group_id=group,node_ids=(n.node_id,),aggregation='SUM',quantity_limit=text(quantity),notional_limit=text(notional)),),
        starting_position_quantity=text(allocation.delta.actual.value),maximum_notional=text(notional),
        fee_budgets=tuple(AssetBudget(asset=asset,amount=text(amount)) for asset,amount in sorted(fees.items())),
        submission_valid_until=expires,budget_valid_until=expires,management_policy='MANAGE_UNTIL_FLAT',
        protection_required=bool(a.risk_facts.protection) or allocation.delta.actual.value!=0)
    binding=ParentBindingV3(contract_version=VERSION,binding_id=activation_id+':binding',authority_hash=digest(authority),
        scope=p.scope,venue='BINANCE',namespace='ORDINARY',binding_kind='LOCAL_PROGRAM_ACTIVATION',
        parent_command_id=None,parent_client_id=None,parent_venue_id=None,bound_at=now,source_evidence=admission)
    sources=(unit.evidence,compilation,target,admission)+((refs('position',position_hash),) if reduction else ())
    proofs=tuple(LifecycleProofV3(contract_version=VERSION,proof_id=activation_id+':'+role,role=role,scope=p.scope,
        subject_hash=digest(subject),context_hash=proof_context(authority,bound),valid_from=now,valid_until=expires,
        source_refs=sources) for role,subject,bound in (('FUNDING_AUTHORITY',authority,None),('PARENT_BINDING',binding,binding)))
    raw={proof.role:canonical_json(domain_payload(proof)).encode() for proof in proofs}
    proof_refs={proof.role:t.EvidenceRef(id=proof.proof_id,hash=digest(proof),environment=p.scope.environment,
        source='SQL_DERIVED_LOCAL_LIFECYCLE_PROOF',visible_at=now) for proof in proofs}
    def reader(role,ref):
        return raw.get(role) if proof_refs.get(role)==ref else None
    prepared=prepare(authority,proof_refs['FUNDING_AUTHORITY'],reader=reader,now=now)
    require(prepared.state is not None and not prepared.restrictions,'LOCAL_AUTHORITY_REJECTED:'+','.join(prepared.restrictions))
    bound=bind_parent(authority,prepared.state,binding,proof_refs['PARENT_BINDING'],reader=reader,now=now)
    require(not bound.restrictions,'LOCAL_BINDING_REJECTED:'+','.join(bound.restrictions))
    active=activate(authority,bound.state,reader=reader,now=now)
    require(not active.restrictions and active.state.status=='ACTIVE','LOCAL_ACTIVATION_REJECTED:'+','.join(active.restrictions))
    record=ProgrammeActivation(contract_version=VERSION,record_kind='LOCAL_PROGRAM_ACTIVATION',activation_id=activation_id,
        financial_intent_id=bundle.intent.intent_id,bundle_hash=digest(bundle),financial_transaction_id=transaction_id,
        collateral_group_id=bundle.intent.collateral_group_id,activated_at=now,authority=authority,binding=binding,proofs=proofs,state=active.state)
    return record,(prepared.state,bound.state,active.state)


def _source_bundle(tx,intent_id,transaction_id,physical_transaction=None):
    row=tx.execute('SELECT payload,transaction_id,xmin::text FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s AND sequence=1',
        (scope_id(tx.scope),BUNDLE_PREFIX+intent_id)).fetchone()
    require(row is not None and row[1]==transaction_id and (physical_transaction is None or row[2]==physical_transaction),
        'LOCAL_FINANCIAL_SOURCE_REQUIRED')
    physical_transaction=row[2]
    bundle=decode(ProgramFinancialBundle,row[0]);a=bundle.archive
    source,intake=read_program_source(tx,bundle.intent.source.intake_record_id)
    require(digest(source)==digest(a.source) and intake.intent_id==intent_id
        and intake.compilation_hash==digest(a.compilation) and a.target_facts.transaction_id==transaction_id,
        'LOCAL_BOUNDED_SOURCE_MISMATCH')
    require(digest(financial_intent_from_assessment_archive(a))==digest(bundle.intent)
        and digest(financial_risk_basis_from_assessment_archive(bundle.intent,a))==digest(bundle.basis)
        and digest(bundle.decision)==digest(a.assessment),'LOCAL_FINANCIAL_REPLAY_MISMATCH')
    intentrow=tx.execute('SELECT payload,transaction_id,xmin::text FROM ledger.intents WHERE scope_id=%s AND record_id=%s AND sequence=1',
        (scope_id(tx.scope),intent_id)).fetchone()
    require(intentrow is not None and intentrow[1]==transaction_id and intentrow[2]==physical_transaction
        and canonical_hash(intentrow[0])==digest(bundle.intent),
        'LOCAL_FINANCIAL_INTENT_MISMATCH')
    rows=tx.execute('SELECT record_id,payload,transaction_id,xmin::text FROM ledger.reservations WHERE scope_id=%s AND sequence=1 '
        "AND payload->>'intent_id'=%s",(scope_id(tx.scope),intent_id)).fetchall()
    require({r[0]:canonical_hash(r[1]) for r in rows}=={p.reservation.reservation_id:digest(p.reservation) for p in bundle.reservations}
        and all(r[2]==transaction_id and r[3]==physical_transaction for r in rows),'LOCAL_FINANCIAL_FUNDING_MISMATCH')
    from trading_system.risk.reservation_repository import ReservationRepository
    require(all(p==ReservationRepository.initial(p.reservation) for p in bundle.reservations),
        'LOCAL_INITIAL_FINANCIAL_PROJECTION_REQUIRED')
    return bundle


class _ActivationWriter:
    def __init__(self,tx):self.tx=tx

    @atomic_write
    def activate(self,bundle,*,load_clock):
        tx=self.tx;witness=current_program_bundle_witness(tx,bundle)
        require(tx.scope.environment in (t.Environment.PAPER,t.Environment.RESEARCH),'ENVIRONMENT_DISABLED')
        clock=load_clock(tx) if load_clock else None
        require(clock is not None and clock.clock_health=='HEALTHY' and type(clock.monotonic_ns) is int
            and clock.monotonic_ns>=bundle.archive.risk_facts.inputs.clock.monotonic_ns
            and utc_timestamp(bundle.archive.risk_facts.inputs.clock.utc_now)<=utc_timestamp(clock.utc_now)
            <utc_timestamp(witness.expires_at),'LOCAL_CURRENT_CLOCK_REQUIRED')
        require(not witness.command_ids,'LOCAL_ACTIVATION_HAS_EXTERNAL_ROOTS')
        activation_id='local-program:'+bundle.intent.intent_id
        prior=read_local_activation(tx,activation_id)
        if prior is not None:
            require(prior.bundle_hash==digest(bundle),'LOCAL_ACTIVATION_CONFLICT')
            return LocalActivationReceipt(activation_id=activation_id,financial_intent_id=prior.financial_intent_id,bundle_hash=prior.bundle_hash)
        source=_source_bundle(tx,bundle.intent.intent_id,tx.transaction_id)
        with localcontext() as arithmetic:
            arithmetic.prec=256;arithmetic.traps[Inexact]=True;arithmetic.traps[Rounded]=True
            record,states=_materials(source,activation_id,tx.transaction_id,clock.utc_now,witness.expires_at)
        repo=OrderProgramRepository(tx)
        require(repo.program(record.authority.program_id) is None,'LOCAL_PROGRAM_ALREADY_BOUND')
        claim_program_attachment(tx,bundle,mode='LOCAL_ACTIVATION',command_ids=())
        repo.save_program(bundle.archive.compilation.original_program)
        for reservation_id in record.authority.reservation_ids:
            require(tx._latest('order_funding_links',reservation_id,FundingLink) is None,'LOCAL_FUNDING_ALREADY_LINKED')
            tx._append('order_funding_links',reservation_id,FundingLink(program_id=record.authority.program_id,
                reservation_id=reservation_id,intent_id=bundle.intent.intent_id),0)
        for proof in record.proofs:tx._append('audit_events',PROOF_PREFIX+proof.proof_id,proof,0)
        for index,state in enumerate(states):tx._append('audit_events',STATE_PREFIX+activation_id,state,index)
        tx._append('audit_events',ACTIVATION_PREFIX+activation_id,record,0)
        require(read_local_activation(tx,activation_id)==record,'LOCAL_ACTIVATION_ATTACHMENT_FAILED')
        # Source replay is bounded work but still consumes time. Poll again
        # after physical attachment and before returning to the outer commit
        # barrier; an expired activation must roll back its financial writes.
        current=current_program_bundle_witness(tx,bundle)
        final_clock=load_clock(tx)
        require(final_clock is not None and final_clock.clock_health=='HEALTHY'
            and type(final_clock.monotonic_ns) is int and final_clock.monotonic_ns>=clock.monotonic_ns
            and utc_timestamp(clock.utc_now)<=utc_timestamp(final_clock.utc_now)<utc_timestamp(current.expires_at),
            'LOCAL_CURRENT_CLOCK_REQUIRED')
        return LocalActivationReceipt(activation_id=activation_id,financial_intent_id=record.financial_intent_id,bundle_hash=record.bundle_hash)


def activate_local_program(tx,bundle,*,load_clock=None):
    return _ActivationWriter(tx).activate(bundle,load_clock=load_clock)


def read_local_activation(tx,activation_id):
    return _read_local_activation_source(tx,activation_id)[0]


def read_local_activation_source(tx,activation_id):
    """Return the activation and the exact replayed financial bundle."""
    return _read_local_activation_source(tx,activation_id)


def _read_local_activation_source(tx,activation_id):
    row=tx.execute('SELECT payload,transaction_id,xmin::text FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s AND sequence=1',
        (scope_id(tx.scope),ACTIVATION_PREFIX+activation_id)).fetchone()
    if row is None:return None
    record=decode(ProgrammeActivation,row[0])
    require(record.activation_id==activation_id and record.financial_transaction_id==row[1], 'LOCAL_ACTIVATION_IDENTITY_MISMATCH')
    bundle=_source_bundle(tx,record.financial_intent_id,row[1],row[2])
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=True;arithmetic.traps[Rounded]=True
        derived,states=_materials(bundle,activation_id,row[1],record.activated_at,record.authority.submission_valid_until)
    require(digest(record)==digest(derived),'LOCAL_ACTIVATION_SOURCE_MISMATCH')
    attachment=tx._latest('audit_events',ATTACHMENT_PREFIX+record.financial_intent_id,ProgramFinancialAttachment)
    require(attachment is not None and attachment.bundle_hash==record.bundle_hash and attachment.mode=='LOCAL_ACTIVATION'
        and attachment.command_ids==() and attachment.admission_transaction_id==row[1],'LOCAL_ACTIVATION_ATTACHMENT_REQUIRED')
    program=OrderProgramRepository(tx).program(record.authority.program_id)
    require(program==bundle.archive.compilation.original_program,'LOCAL_ACTIVATION_PROGRAM_MISMATCH')
    def same_physical(table,key):
        physical=tx.execute('SELECT xmin::text,transaction_id FROM ledger.'+table+' WHERE scope_id=%s AND record_id=%s '
            'ORDER BY sequence',(scope_id(tx.scope),key)).fetchall()
        require(len(physical)==1 and physical[0]==(row[2],row[1]),'LOCAL_PHYSICAL_ATTACHMENT_MISMATCH')
    same_physical('audit_events',ATTACHMENT_PREFIX+record.financial_intent_id)
    same_physical('order_programs',program.program_id)
    for proof in derived.proofs:
        require(tx._latest('audit_events',PROOF_PREFIX+proof.proof_id,LifecycleProofV3)==proof,'LOCAL_PROOF_ARCHIVE_MISMATCH')
        same_physical('audit_events',PROOF_PREFIX+proof.proof_id)
    for reservation_id in record.authority.reservation_ids:
        require(tx._latest('order_funding_links',reservation_id,FundingLink)==FundingLink(program_id=program.program_id,
            reservation_id=reservation_id,intent_id=record.financial_intent_id),'LOCAL_FUNDING_LINK_MISMATCH')
        same_physical('order_funding_links',reservation_id)
    stored=tx.execute('SELECT payload,xmin::text,transaction_id FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s ORDER BY sequence',
        (scope_id(tx.scope),STATE_PREFIX+activation_id)).fetchall()
    require(len(stored)>=3 and all(canonical_hash(r[0])==digest(s) and r[1:]==(row[2],row[1]) for r,s in zip(stored,states)),
        'LOCAL_LIFECYCLE_ARCHIVE_MISMATCH')
    return record,bundle
