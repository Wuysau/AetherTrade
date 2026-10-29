"""Program admission in the SAME owner/collateral transaction and cash ledger.

This trusted composition extension uses ReservationService's reconciled holds,
capacity arithmetic, clocks and retry/commit boundary. A current source reader
must rebuild the entire typed archive. Historical ALLOW is never renewed here.
The private writer creates no command or permit; callers append actual request
writes or a typed internal activation before their enclosing transaction commits.
"""
from dataclasses import dataclass,replace
from decimal import Decimal
from typing import Literal
from uuid import uuid4

from trading_system.accounts.authorization import AuthorizationBinding,AuthorizationService,valid_evidence
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.program_financial import ProgramFinancialIntent,ProgramFinancialRiskBasis,require
from trading_system.intents.generic_program_intake import read_program_source,current_fence
from trading_system.intents.program_assessment_archive import (ProgramFinancialAssessmentArchive,
    ProgramFinancialAssessmentArchiveCodec,financial_intent_from_assessment_archive,
    financial_risk_basis_from_assessment_archive)
from trading_system.risk.program_assessment import ProgramRiskDecision
from trading_system.storage.models import scope_id,atomic_write
from .admission_clock import AdmissionWindow
from .asset_capacity import CapacityInputs,check_capacity
from .locked_actions import LockedActionVerifier
from .reservation_repository import ReservationRepository
from .policy_checks import capital_limit_exceeded
from .reduction import ReductionRiskMetrics
from .target_guard import target_obligations
from trading_system.execution.commands import CommandRepository

ARCHIVE_PREFIX='program-financial-archive:'
BASIS_PREFIX='program-financial-basis:'
DECISION_PREFIX='program-financial-decision:'
BUNDLE_PREFIX='program-financial-bundle:'
ATTACHMENT_PREFIX='program-financial-attachment:'


def digest(value):
    if type(value) is tuple:
        return canonical_hash({'items':[domain_payload(item) for item in value]})
    return canonical_hash(domain_payload(value))


def _scope_facts(archive):
    if type(archive) is ProgramFinancialAssessmentArchive:return archive.target_facts
    from trading_system.intents.quote_assessment_archive import QuoteAssessmentArchiveV1
    require(type(archive) is QuoteAssessmentArchiveV1,'ASSESSMENT_ARCHIVE_TYPE_REQUIRED')
    return archive.risk_facts.target_facts.scope_facts


def _bundle_type_valid(bundle):
    from .quote_financial_admission import QuoteProgramFinancialBundleV1
    return type(bundle) in (ProgramFinancialBundle,QuoteProgramFinancialBundleV1)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFinancialInputs:
    archive:ProgramFinancialAssessmentArchive
    authorization:AuthorizationBinding
    group_capacity:tuple[t.Money,...]
    strategy_capacity:tuple[t.Money,...]
    buffer:tuple[t.Money,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFinancialQualification:
    scope:t.ScopeKey
    compilation_hash:str
    capabilities_hash:str
    rules_hash:str
    profile_hash:str
    adapter_build_digest:str
    execution_mode:Literal['VENUE_NATIVE','LOCAL_SYNTHETIC','PAPER_MODEL']
    evidence:tuple[t.EvidenceRef,...]
    valid_until:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFinancialBundle:
    intent:ProgramFinancialIntent
    basis:ProgramFinancialRiskBasis
    archive:ProgramFinancialAssessmentArchive
    decision:ProgramRiskDecision
    reservations:tuple[t.ReservationProjection,...]

    @property
    def execution_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class _ProgramFinancialWitness:
    """Live private kernel provenance, never restored from owner audit JSON."""
    transaction_id:str
    scope:t.ScopeKey
    bundle_hash:str
    original_book_hash:str
    command_ids:tuple[str,...]
    expires_at:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFinancialAttachment:
    contract_version:Literal['2026-10-29']
    record_kind:Literal['PROGRAM_FINANCIAL_ATTACHMENT']
    intent_id:str
    bundle_hash:str
    admission_transaction_id:str
    mode:Literal['EXTERNAL_REQUESTS','LOCAL_ACTIVATION']
    command_ids:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False


def current_program_bundle_witness(tx,bundle):
    """Read only the actual successful SAME-transaction kernel invocation."""
    witnesses=getattr(tx,'_program_financial_witnesses',None)
    require(tx.active and _bundle_type_valid(bundle) and type(witnesses) is dict,
        'PROGRAM_FINANCIAL_WITNESS_REQUIRED')
    witness=witnesses.get(bundle.intent.intent_id)
    require(type(witness) is _ProgramFinancialWitness and witness.transaction_id==tx.transaction_id
        and witness.scope==tx.scope and witness.bundle_hash==digest(bundle),
        'PROGRAM_FINANCIAL_WITNESS_REQUIRED')
    row=tx.execute('SELECT payload,transaction_id FROM ledger.audit_events '
        'WHERE scope_id=%s AND record_id=%s AND sequence=1',
        (scope_id(tx.scope),BUNDLE_PREFIX+bundle.intent.intent_id)).fetchone()
    require(row is not None and row[1]==tx.transaction_id and canonical_hash(row[0])==witness.bundle_hash,
        'PROGRAM_FINANCIAL_BUNDLE_SOURCE_MISMATCH')
    require(digest(tx.intents.get(bundle.intent.intent_id))==digest(bundle.intent)
        and _scope_facts(bundle.archive).transaction_id==tx.transaction_id
        and bundle.intent.scope==tx.scope and bundle.intent.collateral_group_id==tx.group_id,
        'PROGRAM_FINANCIAL_BUNDLE_SOURCE_MISMATCH')
    source,record=read_program_source(tx,bundle.intent.source.intake_record_id)
    require(digest(source)==digest(bundle.archive.source) and record.intent_id==bundle.intent.intent_id,
        'PROGRAM_FINANCIAL_BUNDLE_SOURCE_MISMATCH')
    fence=current_fence(tx,source.context)
    require((fence.owner_strategy_id,fence.ownership_epoch,fence.risk_policy_revision)==(
        bundle.intent.owner_strategy_id,bundle.intent.ownership_epoch,bundle.intent.risk_policy_revision),
        'STALE_OWNER')
    require(digest(tx.projections.book(tx.scope))==witness.original_book_hash,'PROGRAM_CURRENT_BOOK_CHANGED')
    stored={r.reservation_id:digest(r) for r in tx.reservations.for_intent(bundle.intent.intent_id)}
    expected={p.reservation.reservation_id:digest(p.reservation) for p in bundle.reservations}
    require(stored==expected,'PROGRAM_FINANCIAL_FUNDING_CHANGED')
    repo=ReservationRepository(tx)
    require(all(digest(repo.get(p.reservation.reservation_id))==digest(p) for p in bundle.reservations),
        'PROGRAM_FINANCIAL_FUNDING_CHANGED')
    return witness


def program_bundle_command_ids(tx,bundle):
    """Whole preassigned set, including zero-assets; never infer fake IDs."""
    return current_program_bundle_witness(tx,bundle).command_ids


class _AttachmentWriter:
    def __init__(self,tx):self.tx=tx

    @atomic_write
    def claim(self,bundle,*,mode,command_ids):
        tx=self.tx;witness=current_program_bundle_witness(tx,bundle)
        require(mode in ('EXTERNAL_REQUESTS','LOCAL_ACTIVATION'),'PROGRAM_ATTACHMENT_MODE_INVALID')
        require(type(command_ids) is tuple and command_ids==witness.command_ids,
            'PROGRAM_ATTACHMENT_IDENTITIES_CHANGED')
        local=bundle.archive.compilation.original_program.template in ('TWAP','VP') and (
            bundle.archive.compilation.original_program.execution_mode!='VENUE_NATIVE')
        require((mode=='LOCAL_ACTIVATION')==local,'PROGRAM_ATTACHMENT_MODE_CONFLICT')
        require(not command_ids if local else bool(command_ids),'PROGRAM_ATTACHMENT_IDENTITIES_CHANGED')
        require(all(p.reservation.command_refs==command_ids for p in bundle.reservations),
            'PROGRAM_ATTACHMENT_FUNDING_IDENTITIES_CHANGED')
        record=ProgramFinancialAttachment(contract_version='2026-10-29',record_kind='PROGRAM_FINANCIAL_ATTACHMENT',
            intent_id=bundle.intent.intent_id,bundle_hash=witness.bundle_hash,
            admission_transaction_id=tx.transaction_id,mode=mode,command_ids=command_ids)
        key=ATTACHMENT_PREFIX+record.intent_id
        previous=tx._latest('audit_events',key,ProgramFinancialAttachment)
        require(previous is None or digest(previous)==digest(record),'PROGRAM_ATTACHMENT_ALREADY_CONSUMED')
        if previous is None:tx._append('audit_events',key,record,0)
        return previous or record


def claim_program_attachment(tx,bundle,*,mode,command_ids):
    return _AttachmentWriter(tx).claim(bundle,mode=mode,command_ids=command_ids)


def validate_current_inputs(tx,source,intake,book,inputs,clock,load_execution_bounds=None):
    from .quote_financial_admission import QuoteFinancialInputsV1,validate_current_quote_inputs
    if type(inputs) is QuoteFinancialInputsV1:
        return validate_current_quote_inputs(tx,source,intake,book,inputs,clock,load_execution_bounds)
    require(type(inputs) is ProgramFinancialInputs,'PROGRAM_FINANCIAL_INPUTS_REQUIRED')
    a=inputs.archive
    require(type(a) is ProgramFinancialAssessmentArchive,'ASSESSMENT_ARCHIVE_TYPE_REQUIRED')
    require(digest(source)==digest(a.source) and intake.intent_id==a.intent_id
        and digest(intake.proposal)==digest(a.compilation.original_proposal)
        and digest(intake.context)==digest(source.context)
        and intake.compilation_hash==digest(a.compilation),'PROGRAM_CURRENT_SOURCE_MISMATCH')
    require(intake.book_sequence==book.sequence==a.book.sequence
        and intake.basis_fingerprint==book.basis_fingerprint and digest(book)==digest(a.book),
        'PROGRAM_CURRENT_BOOK_CHANGED')
    require(a.compilation.scope==book.scope==tx.scope and tx.group_id==a.target_facts.collateral_group_id,
        'PROGRAM_FINANCIAL_SCOPE_MISMATCH')
    require(a.target_facts.transaction_id==a.allocation.transaction_id==tx.transaction_id,
        'PROGRAM_CURRENT_TRANSACTION_REQUIRED')
    require(clock.clock_health=='HEALTHY' and type(clock.monotonic_ns) is int and clock.monotonic_ns>=0
        and digest(a.risk_facts.inputs.clock)==digest(clock)
        and digest(a.target_facts.inputs.clock)==digest(clock),'PROGRAM_CURRENT_CLOCK_REQUIRED')
    now=utc_timestamp(clock.utc_now)
    require(type(inputs.authorization) is AuthorizationBinding,'AUTHORIZATION_REQUIRED')
    auth=AuthorizationService(lambda _:inputs.authorization,clock=lambda:now).validate(source.context)
    grant=inputs.authorization.authorization
    require(auth.allowed is True and not auth.rejection_codes
        and digest(auth)==digest(a.risk_facts.authorization)
        and grant.owner_strategy_id==a.risk_terms.owner_strategy_id,'AUTHORIZATION_REQUIRED')
    require(a.risk_terms.protection_policy in grant.protection_options,'PROTECTION_NOT_AUTHORIZED')
    require(now<utc_timestamp(a.risk_terms.valid_until)<=utc_timestamp(grant.expires_at),
        'PROGRAM_AUTHORIZATION_WINDOW_UNBOUND')
    # Real compiler/target/exposure/evaluator replay; no supplied boolean/callback.
    ProgramFinancialAssessmentArchiveCodec().hash(a)
    intent=financial_intent_from_assessment_archive(a)
    basis=financial_risk_basis_from_assessment_archive(intent,a)
    if a.allocation.delta.classification!='REDUCE':
        m=a.assessment.metrics
        require(grant.maximum_capital.currency==m.capital.currency==a.risk_facts.inputs.capital_in_use.currency,
            'PROGRAM_AUTHORIZATION_CAPITAL_CURRENCY_UNBOUND')
        require(not capital_limit_exceeded(capital=m.capital.amount,fees=m.fees.amount,funding=m.funding.amount,
            capital_in_use=a.risk_facts.inputs.capital_in_use.amount,
            policy=replace(a.risk_policy,maximum_capital=format(grant.maximum_capital.amount,'f'))),
            'PROGRAM_AUTHORIZATION_CAPITAL_EXCEEDED')
    return intent,basis


def _reserve_program(service,tx,intake_record_id,command_ids,load_inputs,load_qualification,load_action_proof,load_clock,
                     load_execution_bounds=None):
    """Private SAME-UoW writer. No success receipt or transport authority.

    command_ids are already assigned immutable request identities; the enclosing
    host transaction must materialize every external request or internal local
    activation. A failed attachment/commit rolls back this entire bundle.
    """
    require(tx.scope==service.scope and tx.active,'PROGRAM_FINANCIAL_TRANSACTION_REQUIRED')
    clock=load_clock(tx) if load_clock else None
    require(type(clock) is t.ClockReading and clock.clock_health=='HEALTHY','PROGRAM_CURRENT_CLOCK_REQUIRED')
    reading=clock.utc_now;started_ns=clock.monotonic_ns;now=utc_timestamp(reading)
    require(type(started_ns) is int and started_ns>=0,'EXPIRED_OR_UNBOUND_CLOCK')
    source,intake=read_program_source(tx,intake_record_id)
    fence=current_fence(tx,source.context)
    require(tx.intents.get(intake.intent_id) is None,'PROGRAM_FINANCIAL_ALREADY_ADMITTED')
    book=tx.projections.book(tx.scope)
    original_book_hash=digest(book)
    collateral=service._prepare_collateral(tx,book)
    require(not collateral.rejection_codes,','.join(collateral.rejection_codes))
    book=collateral.book
    inputs=load_inputs(tx,source,intake,book,clock) if load_inputs else None
    intent,basis=validate_current_inputs(tx,source,intake,book,inputs,clock,load_execution_bounds)
    a=inputs.archive;sf=_scope_facts(a)
    from .quote_financial_admission import (QuoteFinancialInputsV1,QuoteProgramFinancialBundleV1,
        validate_current_quote_bounds)
    quote=type(inputs) is QuoteFinancialInputsV1
    require(a.capabilities.transport=='PAPER' and a.capabilities.qualification=='PAPER_VERIFIED',
        'PROGRAM_QUALIFICATION_REQUIRED')
    require((intent.owner_strategy_id,intent.ownership_epoch,intent.risk_policy_revision)==(
        fence.owner_strategy_id,fence.ownership_epoch,fence.risk_policy_revision),'STALE_OWNER')
    require(all(valid_evidence(e,tx.scope.environment,now) for e in book.evidence),'BOOK_EVIDENCE_INVALID')
    require(not CommandRepository(tx).unresolved_in_group(''),'ACTIVE_RISK_LOCK')
    repo=ReservationRepository(tx)
    local_ids={r[0] for r in tx.execute('SELECT record_id FROM ledger.reservations WHERE scope_id=%s',
        (scope_id(tx.scope),)).fetchall()}
    # Snapshot-only external holds still need a quantity mapping; cash is never
    # proof of zero working quantity. Match the legacy IP-07 inclusion rule.
    persisted_ids={p.reservation.reservation_id for p in repo.group()}
    external_ids={p.reservation.reservation_id for p in book.reservations
        if p.reservation.reservation_id not in persisted_ids}
    if quote:
        # Group cash holds remain in the financial book. Quantity belongs only
        # to this physical scope or its original same-account external snapshot.
        original=tx.projections.book(tx.scope)
        external_ids={p.reservation.reservation_id for p in original.reservations
            if p.reservation.reservation_id not in persisted_ids
                and p.reservation.venue_account_id==tx.scope.venue_account_id}
        expected_ids=tuple(p.reservation.reservation_id for p in book.reservations
            if p.reservation.reservation_id in local_ids|external_ids)
        require(a.risk_facts.target_facts.quantity_reservation_ids==expected_ids,
            'QUOTE_CURRENT_QUANTITY_RESERVATION_MAP_CHANGED')
    pending=target_obligations(tx,intent,book,tuple(p for p in collateral.holds
        if p.reservation.reservation_id in local_ids|external_ids),now)
    require(digest(pending)==digest(sf.inputs.pending),'PROGRAM_CURRENT_QUANTITY_OBLIGATIONS_CHANGED')
    archived_holds=inputs.reconciled_holds if quote else a.budget_facts.reservations
    require(digest(archived_holds)==digest(collateral.holds),
        'PROGRAM_CURRENT_GROUP_HOLDS_CHANGED')
    proof=load_qualification(tx,a,clock) if load_qualification else None
    require(type(proof) is ProgramFinancialQualification and proof.scope==tx.scope
        and proof.compilation_hash==intent.hashes.compilation and proof.capabilities_hash==intent.hashes.capabilities
        and proof.rules_hash==intent.hashes.rules and proof.profile_hash==intent.hashes.profile
        and proof.adapter_build_digest==a.compilation.adapter_build_digest
        and proof.execution_mode==a.compilation.original_program.execution_mode and proof.evidence
        and digest(proof.evidence)==digest(a.capabilities.evidence)
        and all(valid_evidence(e,tx.scope.environment,now) for e in proof.evidence)
        and now<utc_timestamp(proof.valid_until),'PROGRAM_QUALIFICATION_REQUIRED')
    action_until=LockedActionVerifier().verify_program(tx,intent,a,reading,book=book,
        read_proof=load_action_proof)
    require(action_until is not None,'ACTIVE_RISK_LOCK')
    capacity=check_capacity(CapacityInputs(incremental_requirements=basis.incremental_requirements,
        venue_free_balances=book.balances,remaining_group_capacity=inputs.group_capacity,
        remaining_strategy_capacity=inputs.strategy_capacity,buffers=inputs.buffer,
        reconciled_group_holds=collateral.holds,owner_strategy_id=intent.owner_strategy_id))
    require(capacity.sufficient,','.join(capacity.rejection_codes))
    require(type(command_ids) is tuple and len(set(command_ids))==len(command_ids)
        and all(type(cid) is str and bool(cid) for cid in command_ids),'PROGRAM_REQUEST_IDENTITIES_REQUIRED')
    # No pretend external mother is needed for local scheduling. Only a local
    # activation may leave command_refs empty; it remains a financial obligation.
    local_algo=a.compilation.original_program.template in ('TWAP','VP') and (
        a.compilation.original_program.execution_mode!='VENUE_NATIVE')
    require(bool(command_ids) or local_algo,'PROGRAM_REQUEST_IDENTITIES_REQUIRED')
    expires=min(map(utc_timestamp,(intent.valid_until,a.assessment.expires_at,proof.valid_until,action_until,
        a.risk_terms.submission_deadline,inputs.authorization.authorization.expires_at)))
    window=AdmissionWindow(started_at=now,started_ns=started_ns,wall_clock=service.clock,
        monotonic_clock=service.monotonic_clock)
    require(window.poll(expires) is not None,'EXPIRED_OR_UNBOUND_CLOCK')
    require(digest(current_fence(tx,source.context))==digest(fence),'STALE_REVISION')
    require(digest(tx.projections.book(tx.scope))==original_book_hash,'PROGRAM_CURRENT_BOOK_CHANGED')
    if quote:
        validate_current_quote_bounds(tx,source,intake,book,clock,a,load_execution_bounds)
        require(window.poll(expires) is not None,'EXPIRED_OR_UNBOUND_CLOCK')
        require(digest(current_fence(tx,source.context))==digest(fence),'STALE_REVISION')
        require(digest(tx.projections.book(tx.scope))==original_book_hash,'PROGRAM_CURRENT_BOOK_CHANGED')
    tx.intents.append(intent)
    tx._append('audit_events',ARCHIVE_PREFIX+intent.intent_id,a,0)
    tx._append('audit_events',BASIS_PREFIX+intent.intent_id,basis,0)
    tx._append('audit_events',DECISION_PREFIX+intent.intent_id,a.assessment,0)
    if collateral.released_proof is not None:
        tx._append('audit_events','ip07:released-balances:'+intent.intent_id,collateral.released_proof,0)
    projections=[]
    for money in basis.incremental_requirements:
        if not money.amount:continue
        reservation=t.CapitalReservation(reservation_id=str(uuid4()),environment=tx.scope.environment,
            collateral_group_id=tx.group_id,venue_account_id=tx.scope.venue_account_id,
            owner_strategy_id=intent.owner_strategy_id,intent_id=intent.intent_id,asset=money.currency,
            amount=money.amount,reflected_amount=Decimal(0),state=t.ReservationState.HELD,
            created_at=reading,expires_at=expires.isoformat(),command_refs=command_ids,evidence_watermark='')
        tx.reservations.append(reservation,0);projections.append(repo.initial(reservation))
    if type(a.assessment.metrics) is ReductionRiskMetrics:
        residual=a.assessment.metrics.residual
        lock=t.RiskLock(lock_id='program-residual:'+intent.intent_id,scope=tx.scope,cause='PROTECTION_GAP',
            state='ACTIVE',permission=t.RiskPermission.EXIT_ONLY,required_evidence=('PROTECTION_OR_EXIT_CONFIRMED',),
            raised_at=reading)
        tx.risk_locks.append(lock,0)
        tx._append('audit_events','program-residual-duty:'+intent.intent_id,residual,0)
    bundle_type=QuoteProgramFinancialBundleV1 if quote else ProgramFinancialBundle
    bundle=bundle_type(intent=intent,basis=basis,archive=a,decision=a.assessment,
        reservations=tuple(projections))
    tx._append('audit_events',BUNDLE_PREFIX+intent.intent_id,bundle,0)
    witnesses=getattr(tx,'_program_financial_witnesses',None)
    if witnesses is None:
        witnesses={};tx._program_financial_witnesses=witnesses
    require(type(witnesses) is dict and intent.intent_id not in witnesses,'PROGRAM_FINANCIAL_ALREADY_ADMITTED')
    witnesses[intent.intent_id]=_ProgramFinancialWitness(transaction_id=tx.transaction_id,scope=tx.scope,
        bundle_hash=digest(bundle),original_book_hash=original_book_hash,command_ids=command_ids,
        expires_at=expires.isoformat())
    return bundle


class _ProgramBundleWriter:
    def __init__(self,service,tx):self.service,self.tx=service,tx

    @atomic_write
    def reserve(self,*args,**kwargs):return _reserve_program(self.service,self.tx,*args,**kwargs)


def reserve_program(service,tx,*args,**kwargs):
    return _ProgramBundleWriter(service,tx).reserve(*args,**kwargs)
