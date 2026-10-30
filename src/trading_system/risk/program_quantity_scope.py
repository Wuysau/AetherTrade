"""Physical quantity scope is distinct from the complete financial hold set."""
from dataclasses import replace
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.wire import ExecutionCommand,TradeIntent
from trading_system.intents.program_target_allocation import ProgramScopedTargetFactsV2
from trading_system.storage.models import MissingEvidence,decode,scope_id,account_key
from .reservation_repository import ReservationRepository,merge_holds


def need(value,code):
    if not value:raise MissingEvidence(code)


def digest(value):return canonical_hash(domain_payload(value))


def _actual_group(tx):
    rows=tx.execute('SELECT r.scope_id,r.record_id,r.payload,p.sequence,p.payload FROM ledger.reservations r '
        "LEFT JOIN LATERAL (SELECT sequence,payload FROM ledger.audit_events a WHERE a.scope_id=r.scope_id "
        "AND a.record_id='ip07:reservation:'||r.record_id ORDER BY sequence DESC LIMIT 1) p ON true "
        "WHERE r.payload->>'environment'=%s AND r.payload->>'collateral_group_id'=%s",
        (str(tx.scope.environment),tx.group_id)).fetchall()
    by_id={};group=[]
    for physical,identifier,payload,projection_sequence,projection in rows:
        r=decode(t.CapitalReservation,payload)
        need(identifier==r.reservation_id and identifier not in by_id,'PROGRAM_RESERVATION_IDENTITY_AMBIGUOUS')
        need(r.environment==tx.scope.environment and r.collateral_group_id==tx.group_id,'PROGRAM_RESERVATION_SCOPE_CHANGED')
        p=ReservationRepository.initial(r) if projection is None else decode(t.ReservationProjection,projection)
        immutable=lambda item:replace(item,state=t.ReservationState.HELD,reflected_amount=Decimal(0),evidence_watermark='')
        need(immutable(p.reservation)==immutable(r),'PROGRAM_RESERVATION_PROJECTION_CHANGED')
        need(all(type(v) is Decimal and v.is_finite() and v>=0 for v in
            (r.amount,p.held,p.reflected,p.release_pending,p.reservation.reflected_amount))
            and p.reflected<=p.held<=r.amount and p.release_pending<=p.held
            and p.reservation.reflected_amount==p.reflected,'PROGRAM_RESERVATION_PROJECTION_CHANGED')
        if projection is not None:
            head=tx.execute('SELECT count(*),min(sequence),max(sequence) FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s',
                (physical,'ip07:reservation:'+identifier)).fetchone()
            need(type(p.sequence) is int and p.sequence==projection_sequence==head[2]
                and head[1]==1 and head[0]==head[2],'PROGRAM_RESERVATION_PROJECTION_HISTORY_CHANGED')
        if p.reservation.state==t.ReservationState.RELEASED:
            need(p.held==p.reflected==p.release_pending==0,'PROGRAM_RESERVATION_PROJECTION_CHANGED')
        if physical==scope_id(tx.scope):need(r.venue_account_id==tx.scope.venue_account_id,'PROGRAM_RESERVATION_SCOPE_CHANGED')
        by_id[identifier]=(physical,p);group.append(p)
    return by_id,tuple(group)


def _external_scope(tx,projection):
    """Only existing authenticated legacy references qualify snapshot-only IDs.

    Truly external venue IDs without an independently registered source mapping
    stay blocked. A raw snapshot label, asset or money value cannot prove scope.
    """
    r=projection.reservation
    need(bool(r.command_refs),'PROGRAM_EXTERNAL_QUANTITY_SCOPE_EVIDENCE_REQUIRED')
    physical_scopes=set()
    for identifier in r.command_refs:
        rows=tx.execute('SELECT scope_id,payload FROM ledger.commands WHERE record_id=%s AND sequence=1',(identifier,)).fetchall()
        matched=[]
        for physical,payload in rows:
            command=decode(ExecutionCommand,payload)
            if (str(command.environment),command.venue_account_id)!=(str(r.environment),r.venue_account_id):continue
            need(command.intent_id==r.intent_id,'PROGRAM_EXTERNAL_QUANTITY_SCOPE_CHANGED')
            position_side=command.canonical_payload.position_side
            need(position_side in ('BOTH','LONG','SHORT'),'PROGRAM_EXTERNAL_QUANTITY_SCOPE_EVIDENCE_REQUIRED')
            actual_scope=t.ScopeKey(environment=t.Environment(command.environment),venue_account_id=command.venue_account_id,
                product=t.Product(command.product),instrument_uid=command.instrument_uid,venue_leg=position_side)
            need(physical==scope_id(actual_scope),'PROGRAM_EXTERNAL_QUANTITY_SCOPE_CHANGED')
            bound=tx.execute('SELECT collateral_group_id FROM ledger.account_bindings WHERE environment=%s AND account_id=%s AND product=%s',
                account_key(actual_scope)).fetchone()
            need(bound==(tx.group_id,),'PROGRAM_EXTERNAL_QUANTITY_SCOPE_CHANGED')
            intent=tx.execute('SELECT payload FROM ledger.intents WHERE scope_id=%s AND record_id=%s AND sequence=1',
                (physical,command.intent_id)).fetchone()
            need(intent is not None,'PROGRAM_EXTERNAL_QUANTITY_SCOPE_EVIDENCE_REQUIRED')
            original=decode(TradeIntent,intent[0])
            need(original.owner_strategy_id==r.owner_strategy_id and original.ownership_epoch==command.ownership_epoch,
                'PROGRAM_EXTERNAL_QUANTITY_SCOPE_CHANGED')
            matched.append(physical)
        need(len(matched)==1,'PROGRAM_EXTERNAL_QUANTITY_SCOPE_EVIDENCE_REQUIRED')
        physical_scopes.add(matched[0])
    need(len(physical_scopes)==1,'PROGRAM_EXTERNAL_QUANTITY_SCOPE_CHANGED')
    return next(iter(physical_scopes))


def _own_ids(tx,bundle,physical):
    if bundle is None:return set()
    from .program_financial_admission import ProgramFinancialBundle,BUNDLE_PREFIX
    need(type(bundle) is ProgramFinancialBundle and bundle.intent.scope==tx.scope,'PROGRAM_FINANCIAL_BUNDLE_REQUIRED')
    row=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s AND sequence=1',
        (scope_id(tx.scope),BUNDLE_PREFIX+bundle.intent.intent_id)).fetchone()
    need(row is not None and canonical_hash(row[0])==digest(bundle),'PROGRAM_ORIGINAL_FINANCIAL_BUNDLE_REQUIRED')
    result=set()
    for p in bundle.reservations:
        actual=physical.get(p.reservation.reservation_id)
        need(actual is not None and actual[0]==scope_id(tx.scope) and actual[1]==p,'PROGRAM_RESERVATION_CHANGED')
        result.add(p.reservation.reservation_id)
    return result


def current_program_quantity_reservation_ids(tx,raw_source_book,financial_book,*,bundle=None):
    tx.require_scope(raw_source_book.scope)
    need(type(raw_source_book) is t.BookSnapshot and type(financial_book) is t.BookSnapshot
        and financial_book.scope==tx.scope and tx.projections.book(tx.scope)==raw_source_book,
        'PROGRAM_CURRENT_SOURCE_BOOK_REQUIRED')
    need(replace(raw_source_book,reservations=())==replace(financial_book,reservations=()),
        'PROGRAM_FINANCIAL_BOOK_SOURCE_CHANGED')
    physical,group=_actual_group(tx);own=_own_ids(tx,bundle,physical)
    expected=tuple(p for p in merge_holds(group,raw_source_book) if p.reservation.reservation_id not in own)
    expected_by_id={p.reservation.reservation_id:p for p in expected}
    supplied={p.reservation.reservation_id:p for p in financial_book.reservations}
    need(len(supplied)==len(financial_book.reservations) and supplied==expected_by_id,
        'PROGRAM_CURRENT_FINANCIAL_HOLDS_CHANGED')
    local={identifier for identifier,(physical_scope,_) in physical.items() if physical_scope==scope_id(tx.scope)}
    external=set()
    for p in raw_source_book.reservations:
        if p.reservation.reservation_id in physical:continue
        if _external_scope(tx,p)==scope_id(tx.scope):external.add(p.reservation.reservation_id)
    return tuple(sorted(identifier for identifier in supplied if identifier in local|external))


def validate_current_program_quantity_scope(tx,raw_source_book,financial_book,target_facts,*,bundle=None):
    need(type(target_facts) is ProgramScopedTargetFactsV2,'PROGRAM_SCOPED_TARGET_FACTS_REQUIRED')
    target_facts.__post_init__()
    expected=current_program_quantity_reservation_ids(tx,raw_source_book,financial_book,bundle=bundle)
    need(target_facts.quantity_scope==tx.scope and target_facts.quantity_reservation_ids==expected,
        'PROGRAM_CURRENT_QUANTITY_RESERVATION_MAP_CHANGED')
    return expected


def current_scoped_program_financial_book(tx,bundle,raw_source_book):
    need(type(bundle.archive.target_facts) is ProgramScopedTargetFactsV2,'PROGRAM_SCOPED_TARGET_FACTS_REQUIRED')
    physical,group=_actual_group(tx);own=_own_ids(tx,bundle,physical)
    return replace(raw_source_book,reservations=tuple(p for p in merge_holds(group,raw_source_book)
        if p.reservation.reservation_id not in own))
