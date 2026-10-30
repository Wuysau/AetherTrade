"""PAPER private source -> existing order/event/book projections, never sends.

The source observer commits before this owner transaction. Every current read
rederives observations, original funding/IDs, unit bridge and recorded fills.
An owner-written book/marker cannot supply working quantity. UNKNOWN retains
the previous facts and blocks a current working-input claim. Atomic Spot only.
"""
from dataclasses import dataclass,replace
from decimal import Decimal,localcontext,Inexact,Rounded
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.intents.target_compiler import PendingQuantity
from trading_system.ownership.registry import RegistryTx
from trading_system.recovery.original_creation import read_original_creation
from trading_system.risk.exposure import WorkingOrder
from trading_system.risk.program_financial_admission import read_program_bundle
from trading_system.risk.program_quantity_scope import current_program_quantity_reservation_ids
from trading_system.risk.reservations import prepare_collateral
from trading_system.storage.events import EventStore
from trading_system.storage.models import MissingEvidence,decode,encode,scope_id
from trading_system.storage.uow import UnitOfWork
from .fills import FillLedger
from .projections import OrderProjector,TERMINAL
from .program_book_cash import bind_program_book_cash
from .program_fill_source import CommittedProgramFillSources,decode_source_normalized
from .program_legacy_opening import read_program_local_account_opening
from .program_submission_observation import ProgramSubmissionObservationService,read_program_submission_observation


def need(condition,code):
    if not condition:raise MissingEvidence(code)


def digest(value):return canonical_hash(domain_payload(value))
def marker_id(logical):return 'program-order-source:'+logical


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramOrderSource:
    command_id:str
    source_snapshot_hash:str
    projection_hash:str
    event_id:str


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramOrderProjectionResult:
    status:Literal['SOURCE_BLOCKED','ORDER_BOOK_PROJECTED_DUTIES_RETAINED','ORDER_PROJECTED_FINANCIAL_RECONCILIATION_REQUIRED']
    reasons:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramWorkingInputs:
    order_sides:tuple[WorkingOrder,...]
    pending:tuple[PendingQuantity,...]


def _source(tx,command_id,reader):
    original=read_original_creation(tx,command_id)
    need(original is not None,'PROGRAM_ORDER_ORIGINAL_REQUIRED')
    view=read_program_submission_observation(tx,original.initial)
    need(view is not None and view.submission_known,'PROGRAM_ORDER_QUERY_UNKNOWN')
    bundle=read_program_bundle(tx,original.initial.intent_id)
    need(bundle is not None and bundle.archive.compilation.original_program.template=='ATOMIC'
        and tx.scope.product==t.Product.SPOT,'PROGRAM_ORDER_EXECUTION_FAMILY_UNQUALIFIED')
    need(tx.execute('SELECT ledger.program_binding_current(%s,%s,%s::jsonb)',
        (scope_id(tx.scope),command_id,encode(original.initial))).fetchone()==(True,),
        'PROGRAM_ORDER_ORIGINAL_FUNDING_CHANGED')
    need(RegistryTx(tx).active_owner()==(original.initial.owner_strategy_id,
        original.initial.ownership_epoch,original.initial.policy_revision),'PROGRAM_ORDER_OWNER_CHANGED')
    snapshot=reader.read_snapshot(command_id,view.source_snapshot_hash);verification=snapshot['verification']
    tags=(verification.get('contract_version'),verification.get('record_kind'))
    need(tags in ((None,None),('2026-10-30','PAPER_PROGRAM_VERIFICATION_V2'))
        and verification['source_verified'] is True and verification['status']=='VERIFIED'
        and verification['discrepancies']==[],'PROGRAM_ORDER_SOURCE_RECONCILIATION_REQUIRED')
    projections=tuple(decode(t.OrderProjection,p) for p in verification['orders'])
    ids=original.request.assigned_ids.members
    need(len(projections)==len(ids)==1,'PROGRAM_ORDER_MEMBERS_REQUIRED')
    normalized=tuple(decode_source_normalized(p,verification) for p in snapshot['normalized_fills'].values())
    fills=FillLedger(None,tx.scope,trade_id_scope='INSTRUMENT')
    for n in normalized:fills.require_recorded_in_tx(tx,n.fill)
    projected=projections[0];logical=projected.order.identity.logical_client_order_id
    need(projected.order.identity.scope==tx.scope and logical==ids[0].client_id
        and projected.order.identity.venue_client_order_id==ids[0].client_id
        and projected.order.identity.venue_order_id==view.record.members[0].venue_order_id
        and projected.order.owner_strategy_id==original.initial.owner_strategy_id
        and projected.order.ownership_epoch==original.initial.ownership_epoch
        and projected.command_certainty=='CONFIRMED' and projected.pending_operation=='NONE'
        and projected.outcome_provenance=='VENUE_QUERY','PROGRAM_ORDER_SOURCE_IDENTITY_CHANGED')
    rows=tx.execute('SELECT payload FROM ledger.fills WHERE scope_id=%s '
        "AND payload#>>'{order,logical_client_order_id}'=%s",(scope_id(tx.scope),logical)).fetchall()
    recorded=tuple(decode(t.Fill,r[0]) for r in rows)
    need({f.native_trade_id for f in recorded}=={n.fill.native_trade_id for n in normalized}
        and all(f.correction_of is None for f in recorded),'PROGRAM_ORDER_FILL_HISTORY_REQUIRED')
    unit=bundle.intent.unit
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=arithmetic.traps[Rounded]=True
        total=sum((n.fill.quantity.value for n in normalized),Decimal(0))
        need(projected.filled_quantity.unit==projected.leaves_quantity.unit==unit.native_unit
            and projected.filled_quantity.value==total,'PROGRAM_ORDER_FILL_HISTORY_REQUIRED')
        projected=replace(projected,
            filled_quantity=t.Quantity(unit=unit.ledger_unit,value=projected.filled_quantity.value*unit.ledger_units_per_native_unit),
            leaves_quantity=t.Quantity(unit=unit.ledger_unit,value=projected.leaves_quantity.value*unit.ledger_units_per_native_unit))
    return view,bundle,projected,tuple(decode(t.EvidenceRef,r) for r in verification['raw_refs'])


def _persisted(tx,command_id,derived,reader):
    logical=derived.order.identity.logical_client_order_id
    row=tx.execute('SELECT payload,xmin::text,transaction_id FROM ledger.order_projections '
        'WHERE scope_id=%s AND record_id=%s ORDER BY sequence DESC LIMIT 1',(scope_id(tx.scope),logical)).fetchone()
    need(row is not None,'PROGRAM_ORDER_PROJECTION_REQUIRED')
    projection=decode(t.OrderProjection,row[0])
    need(replace(derived,sequence=projection.sequence)==projection,'PROGRAM_ORDER_SOURCE_CONFLICT')
    proof=tx.execute('SELECT payload,xmin::text,transaction_id FROM ledger.audit_events '
        'WHERE scope_id=%s AND record_id=%s AND sequence=%s',
        (scope_id(tx.scope),marker_id(logical),projection.sequence)).fetchone()
    need(proof is not None and proof[1:]==row[1:],'PROGRAM_ORDER_PROJECTION_PROOF_REQUIRED')
    binding=decode(ProgramOrderSource,proof[0])
    need(binding.command_id==command_id and binding.projection_hash==digest(projection),
        'PROGRAM_ORDER_PROJECTION_PROOF_REQUIRED')
    # Historical proof remains valid on an identical fresh query, but cannot be
    # replaced by a flag or a marker referring to another command's snapshot.
    source=reader.read_snapshot(command_id,binding.source_snapshot_hash)
    need(bool(source['verification']['orders']),'PROGRAM_ORDER_PROJECTION_PROOF_REQUIRED')
    event=tx.execute('SELECT payload,payload_hash,xmin::text,transaction_id FROM ledger.domain_events WHERE event_id=%s',
        (binding.event_id,)).fetchone()
    need(event is not None and event[1]==canonical_hash(event[0]) and event[2:]==row[1:]
        and event[0]['payload']==domain_payload(projection),'PROGRAM_ORDER_PROJECTION_PROOF_REQUIRED')
    need(tx.execute("SELECT xmin::text,transaction_id FROM ledger.outbox WHERE event_id=%s AND kind='DOMAIN_EVENT'",
        (binding.event_id,)).fetchall()==[row[1:]],'PROGRAM_ORDER_PROJECTION_PROOF_REQUIRED')
    return projection


class ProgramPaperOrderProjectionService:
    def __init__(self,uow,scope,*,observer):
        need(type(uow) is UnitOfWork and type(scope) is t.ScopeKey and scope.environment==t.Environment.PAPER
            and type(observer) is ProgramSubmissionObservationService and observer.scope==scope,
            'PROGRAM_ORDER_ACTUAL_PAPER_COMPOSITION_REQUIRED')
        self.uow,self.scope,self.observer=uow,scope,observer

    def apply(self,command_id):
        observed=self.observer.observe(command_id)
        if not observed.submission_known:return ProgramOrderProjectionResult(status='SOURCE_BLOCKED',reasons=observed.record.reasons)
        with self.uow.transaction(self.scope) as tx:
            reader=CommittedProgramFillSources(tx)
            view,bundle,derived,refs=_source(tx,command_id,reader)
            need(view.observation_id==observed.observation_id,'PROGRAM_ORDER_QUERY_CHANGED')
            book=tx.projections.book(self.scope);logical=derived.order.identity.logical_client_order_id
            previous=tx._latest('order_projections',logical,t.OrderProjection)
            if previous is not None and replace(derived,sequence=previous.sequence)==previous:
                _persisted(tx,command_id,derived,reader)
                return ProgramOrderProjectionResult(status='ORDER_BOOK_PROJECTED_DUTIES_RETAINED',reasons=('ORDER_HISTORY_AND_PROTECTION_DUTIES_RETAINED',))
            expected=0 if previous is None else previous.sequence
            projection=replace(derived,sequence=expected+1)
            event=t.DomainEvent(event_id='program-order:'+canonical_hash(dict(source=view.source_snapshot_hash,
                logical=logical,sequence=projection.sequence)),scope=self.scope,aggregate_id='order:'+logical,
                revision=projection.sequence,occurred_at=view.record.observed_clock.utc_now,evidence=refs,payload=projection)
            if previous is not None:
                projection=OrderProjector(require_evidence=lambda e:need(e==event,'PROGRAM_ORDER_SOURCE_CONFLICT')).apply(event,previous).projection
                need(projection==replace(derived,sequence=projection.sequence),'PROGRAM_ORDER_SOURCE_CONFLICT')
            event=replace(event,payload=projection)
            EventStore(received_at=view.record.observed_clock.utc_now).append(tx,event,expected)
            tx.projections.append(t.ProjectionUpdate(projection=projection,events=()),expected)
            tx._append('audit_events',marker_id(logical),ProgramOrderSource(command_id=command_id,
                source_snapshot_hash=view.source_snapshot_hash,projection_hash=digest(projection),event_id=event.event_id),expected)
            orders={p.order.identity.logical_client_order_id:p for p in book.active_orders}
            if projection.lifecycle_state in TERMINAL:orders.pop(logical,None)
            else:orders[logical]=projection
            updated=replace(book,active_orders=tuple(orders[k] for k in sorted(orders)),sequence=book.sequence+1,
                basis_fingerprint=canonical_hash(dict(prior=digest(book),order=domain_payload(projection))))
            tx.projections.save_book(updated,book.sequence)
            try:cash=bind_program_book_cash(tx,updated,read_opening=read_program_local_account_opening)
            except (MissingEvidence,ValueError) as error:
                if tx.failed:raise
                return ProgramOrderProjectionResult(status='ORDER_PROJECTED_FINANCIAL_RECONCILIATION_REQUIRED',reasons=(str(error),))
            return ProgramOrderProjectionResult(status='ORDER_BOOK_PROJECTED_DUTIES_RETAINED' if cash is not None
                else 'ORDER_PROJECTED_FINANCIAL_RECONCILIATION_REQUIRED',reasons=('ORDER_HISTORY_AND_PROTECTION_DUTIES_RETAINED',))


def read_program_working_inputs(tx,book):
    """Observation inputs for target math, not financial approval or a permit."""
    from trading_system.storage.application_writes import application_write_obligations
    tx.require_scope(book.scope)
    need(tx.projections.book(tx.scope)==book,'PROGRAM_ORDER_CURRENT_BOOK_REQUIRED')
    prepared=prepare_collateral(tx,book)
    need(not prepared.rejection_codes,','.join(prepared.rejection_codes))
    ids=set(current_program_quantity_reservation_ids(tx,book,prepared.book))
    positive={p.reservation.reservation_id:p for p in prepared.holds if p.reservation.reservation_id in ids
        and (p.held or p.reflected or p.release_pending)}
    reader=CommittedProgramFillSources(tx);sides=[];pending=[];mapped=set();active={}
    for obligation in application_write_obligations(tx,scope_only=True):
        view,bundle,derived,_=_source(tx,obligation.original.command_id,reader)
        projection=_persisted(tx,obligation.original.command_id,derived,reader)
        logical=projection.order.identity.logical_client_order_id
        holdings=tuple(sorted(identifier for identifier,p in positive.items() if p.reservation.intent_id==bundle.intent.intent_id))
        if projection.lifecycle_state in TERMINAL:
            need(not holdings,'PROGRAM_ORDER_TERMINAL_RESERVATION_UNRESOLVED');continue
        active[logical]=projection
        side=bundle.archive.compilation.nodes[0].original.order.side
        sides.append(WorkingOrder(logical_client_order_id=logical,side=side))
        need(all(obligation.original.command_id in positive[i].reservation.command_refs
            and any(p.reservation.reservation_id==i for p in bundle.reservations) for i in holdings),
            'PROGRAM_ORDER_RESERVATION_MAPPING_REQUIRED')
        mapped.update(holdings)
        pending.append(PendingQuantity(intent_id=bundle.intent.intent_id,
            signed_quantity=t.Quantity(unit=bundle.intent.unit.ledger_unit,value=Decimal(0)),reservation_ids=holdings,
            represented_order_ids=(logical,),command_refs=(obligation.original.command_id,)))
    need(mapped==set(positive),'PROGRAM_ORDER_RESERVATION_MAPPING_REQUIRED')
    need({p.order.identity.logical_client_order_id:p for p in book.active_orders}==active,'PROGRAM_ORDER_BOOK_MISMATCH')
    return ProgramWorkingInputs(order_sides=tuple(sides),pending=tuple(pending))
