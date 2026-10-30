"""Read actual consumed source and current barriers; never mint write authority.

Call again after every wait, inside the existing exclusive owner's side-effect
guard. This transaction closes before transport IO. A returned snapshot cannot
prove a later SDK-internal queued send or replace the original one-use permit.
"""
from dataclasses import dataclass,replace
from datetime import timedelta
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.execution.creation_requests import VenueCreationRequest
from trading_system.execution.program_commands import ProgramCommandBoundary,ProgramCommandRepository,ProgramWritePermit,_token_hash
from trading_system.intents.generic_program_intake import current_fence
from trading_system.intents.program_assessment_archive import ProgramFinancialAssessmentArchive
from trading_system.recovery.original_creation import read_original_creation
from trading_system.risk.locked_actions import LockedActionVerifier
from trading_system.risk.program_financial_admission import ProgramFinancialQualification,ProgramFinancialBundle,BUNDLE_PREFIX
from trading_system.risk.program_financial_admission import decode_program_bundle,restore_assessment_archive
from trading_system.storage.models import scope_id,decode


def require(condition,code):
    if not condition:raise ValueError(code)


def digest(value):return canonical_hash(domain_payload(value))


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramConsumedRequest:
    write:ApplicationWriteV3
    request:VenueCreationRequest
    clock:t.ClockReading
    valid_until:str
    deadline_monotonic_ns:int
    consumption_context_hash:str
    source_ref:str

    @property
    def execution_authorized(self)->Literal[False]:return False


def _protected(tx,command_id):
    row=tx.execute('SELECT c.context,m.context,m.write_payload,c.token_hash,m.token_hash,'
        'c.transaction_id,m.transaction_id,c.database_transaction::text,m.database_transaction::text,'
        'c.xmin::text,m.xmin::text,w.xmin::text,w.transaction_id,w.payload,pg_current_xact_id()::text,'
        'floor(extract(epoch FROM m.expires_at-clock_timestamp())*1000000)::bigint '
        'FROM ledger.program_permit_consumptions c JOIN ledger.program_dispatch_marks m USING(scope_id,command_id) '
        'JOIN ledger.order_writes_v2 w ON w.scope_id=m.scope_id AND w.record_id=m.command_id AND w.sequence=2 '
        'WHERE c.scope_id=%s AND c.command_id=%s',(scope_id(tx.scope),command_id)).fetchone()
    require(row is not None,'PROGRAM_ACTUAL_CONSUMPTION_REQUIRED')
    (consumed,marked,marked_write,consumed_token,marked_token,consume_tid,mark_tid,
        consume_xid,mark_xid,consume_xmin,mark_xmin,may_xmin,may_tid,may_write,current_xid,db_remaining_us)=row
    # Logical transaction labels alone are insufficient: bind the protected
    # rows to real PostgreSQL insert transactions and the physical MAY tuple.
    require(consume_xid!=mark_xid and current_xid not in (consume_xid,mark_xid)
        and int(consume_xid)%(2**32)==int(consume_xmin)
        and int(mark_xid)%(2**32)==int(mark_xmin)==int(may_xmin)
        and consume_tid!=mark_tid==may_tid and tx.transaction_id not in (consume_tid,mark_tid),
        'PROGRAM_COMMITTED_DISTINCT_PHYSICAL_TRANSACTIONS_REQUIRED')
    require(type(db_remaining_us) is int and db_remaining_us>0,'PROGRAM_DATABASE_PERMIT_EXPIRED')
    return consumed,marked,marked_write,may_write,consumed_token,marked_token,db_remaining_us


def _original_permit_window(marked,permit):
    """Match committed original labels, independent of today's TTL policy."""
    clock=marked['clock'];lease=marked['lease'];window=marked.get('permit_window')
    require(type(window) is dict and set(window)=={'expires_at','deadline_monotonic_ns'}
        and type(window['expires_at']) is str and type(window['deadline_monotonic_ns']) is int
        and clock['clock_health']=='HEALTHY' and type(clock['monotonic_ns']) is int
        and clock['monotonic_ns']>=0 and type(lease['deadline_monotonic_ns']) is int,
        'PROGRAM_ORIGINAL_PERMIT_WINDOW_REQUIRED')
    marked_at=utc_timestamp(clock['utc_now']);expires=utc_timestamp(window['expires_at'])
    duration_us=(expires-marked_at)//timedelta(microseconds=1)
    require(0<duration_us<=5_000_000 and duration_us%1000==0
        and expires<=min(utc_timestamp(marked['valid_until']),utc_timestamp(lease['expires_at']))
        and window['deadline_monotonic_ns']==clock['monotonic_ns']+duration_us*1000
        and window['deadline_monotonic_ns']<=lease['deadline_monotonic_ns'],
        'PROGRAM_COMMITTED_PERMIT_WINDOW_INVALID')
    require(type(permit.deadline_monotonic_ns) is int
        and permit.deadline_monotonic_ns==window['deadline_monotonic_ns']
        and permit.expires_at==window['expires_at'],'PROGRAM_ORIGINAL_PERMIT_WINDOW_CHANGED')


def recheck_consumed(boundary,permit,lease):
    require(type(boundary) is ProgramCommandBoundary and type(permit) is ProgramWritePermit
        and type(lease) is t.OwnerLease,'PROGRAM_ACTUAL_BOUNDARY_PERMIT_LEASE_REQUIRED')
    require(type(boundary.scope) is t.ScopeKey and boundary.scope.environment==t.Environment.PAPER
        and permit.scope==boundary.scope,'PROGRAM_CONSUMED_SCOPE_CHANGED')
    with boundary.uow.transaction(boundary.scope) as tx:
        consumed,marked,marked_write,may_write,c_token,m_token,_=_protected(tx,permit.command_id)
        require(c_token==m_token==_token_hash(permit.token),'PROGRAM_CONSUMED_TOKEN_CHANGED')
        _original_permit_window(marked,permit)
        original=read_original_creation(tx,permit.command_id)
        require(original is not None and original.current_state=='MAY_HAVE_SENT','PROGRAM_CONSUMED_MAY_REQUIRED')
        write=replace(original.initial,state='MAY_HAVE_SENT');request=original.request
        require(write.scope==boundary.scope and write.command_id==permit.command_id
            and write.request_hash==permit.request_hash==digest(request) and write.binding_hash==permit.binding_hash
            and canonical_hash(marked_write)==canonical_hash(may_write)==digest(write)
            and canonical_hash(consumed['write'])==digest(write)
            and canonical_hash(marked['write'])==digest(original.initial)
            and consumed['request_hash']==marked['request_hash']==write.request_hash,
            'PROGRAM_CONSUMED_ORIGINAL_BINDING_CHANGED')
        source=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s AND sequence=1',
            (scope_id(boundary.scope),BUNDLE_PREFIX+write.intent_id)).fetchone()
        require(source is not None,'PROGRAM_CONSUMED_BUNDLE_REQUIRED')
        bundle=decode_program_bundle(source[0])
        assessment=restore_assessment_archive(consumed['assessment'])
        require(type(bundle.archive) is type(assessment),'PROGRAM_CONSUMED_SOURCE_KIND_CHANGED')
        qualification=decode(ProgramFinancialQualification,consumed['qualification'])
        require(consumed['assessment_hash']==digest(assessment)
            and consumed['source_hash']==digest(bundle.intent.source)==digest(assessment.source)
            and digest(assessment.compilation)==write.compilation_hash
            and assessment.intent_id==bundle.intent.intent_id==write.intent_id
            and consumed['qualification_hash']==digest(qualification)
            and qualification.scope==write.scope and qualification.compilation_hash==write.compilation_hash
            and qualification.capabilities_hash==digest(assessment.capabilities)
            and qualification.rules_hash==digest(assessment.rules) and qualification.profile_hash==digest(assessment.profile)
            and qualification.adapter_build_digest==assessment.compilation.adapter_build_digest
            and qualification.execution_mode==assessment.compilation.original_program.execution_mode,
            'PROGRAM_CONSUMED_SOURCE_HASHES_CHANGED')
        repo=ProgramCommandRepository(tx,**boundary.readers)
        clock,current_lease=repo._clock_lease(write)
        require(digest(current_lease)==digest(lease)
            and canonical_hash(consumed['lease'])==canonical_hash(marked['lease'])==digest(lease),
            'PROGRAM_CONSUMED_LEASE_CHANGED')
        before=utc_timestamp(clock.utc_now)
        for recorded in (marked['clock'],consumed['clock']):
            require(recorded['clock_health']=='HEALTHY' and type(recorded['monotonic_ns']) is int
                and 0<=recorded['monotonic_ns']<=clock.monotonic_ns
                and utc_timestamp(recorded['utc_now'])<=before,'PROGRAM_CONSUMED_CLOCK_CHANGED')
        fence=current_fence(tx,assessment.source.context)
        require(digest(fence)==canonical_hash(consumed['fence']),'PROGRAM_CONSUMED_FENCE_CHANGED')
        book=tx.projections.book(write.scope)
        from trading_system.risk.program_cash_guard import require_funded_program_account_book
        account_cash=require_funded_program_account_book(tx,book)
        require(account_cash.opening_id==consumed.get('account_opening_id')==marked.get('account_opening_id')
            and account_cash.economic_revision==consumed.get('account_cash_revision')==marked.get('account_cash_revision'),
            'PROGRAM_CONSUMED_ACCOUNT_CASH_CHANGED')
        from trading_system.risk.quote_financial_admission import (QuoteProgramFinancialBundleV1,
            current_quote_financial_book,validate_current_quote_bounds)
        quote=type(bundle) is QuoteProgramFinancialBundleV1
        from trading_system.intents.program_target_allocation import ProgramScopedTargetFactsV2
        from trading_system.risk.program_quantity_scope import (
            current_scoped_program_financial_book,validate_current_program_quantity_scope)
        scoped=not quote and type(bundle.archive.target_facts) is ProgramScopedTargetFactsV2
        financial_book=book
        if quote:
            from trading_system.intents.generic_program_intake import read_program_source
            require(digest(book)==consumed.get('source_book_hash'),'PROGRAM_CONSUMED_BOOK_CHANGED')
            financial_book=current_quote_financial_book(tx,bundle,book)
            require(digest(financial_book)==consumed.get('financial_book_hash')==digest(assessment.book),
                'PROGRAM_CONSUMED_FINANCIAL_BOOK_CHANGED')
            original_source,intake=read_program_source(tx,bundle.intent.source.intake_record_id)
            require(digest(original_source)==digest(assessment.source),'PROGRAM_CONSUMED_SOURCE_HASHES_CHANGED')
            validate_current_quote_bounds(tx,original_source,intake,financial_book,clock,assessment,
                boundary.readers.get('load_execution_bounds'))
            require(digest(assessment.risk_facts.target_facts.bounds)==consumed.get('execution_bounds_hash'),
                'PROGRAM_CONSUMED_EXECUTION_BOUND_CHANGED')
            require(digest(tx.projections.book(write.scope))==consumed.get('source_book_hash')
                and digest(current_quote_financial_book(tx,bundle,tx.projections.book(write.scope)))==digest(financial_book)
                and digest(current_fence(tx,assessment.source.context))==canonical_hash(consumed['fence']),
                'PROGRAM_CONSUMED_CURRENT_STATE_CHANGED')
        elif scoped:
            require(digest(book)==consumed.get('source_book_hash'),'PROGRAM_CONSUMED_BOOK_CHANGED')
            financial_book=current_scoped_program_financial_book(tx,bundle,book,
                load_released_balances=boundary.readers.get('load_released_balances'))
            require(digest(financial_book)==consumed.get('financial_book_hash')==digest(assessment.book),
                'PROGRAM_CONSUMED_FINANCIAL_BOOK_CHANGED')
            validate_current_program_quantity_scope(tx,book,financial_book,assessment.target_facts,bundle=bundle,
                load_released_balances=boundary.readers.get('load_released_balances'))
        else:
            require(book is not None and digest(book)==digest(assessment.book),'PROGRAM_CONSUMED_BOOK_CHANGED')
        duty=LockedActionVerifier().verify_program(tx,bundle.intent,assessment,clock.utc_now,
            book=financial_book,read_proof=boundary.readers['load_action_proof'])
        require(duty is not None,'PROGRAM_ACTIVE_RISK_LOCK')
        horizons=(write.valid_until,bundle.intent.valid_until,consumed['valid_until'],marked['valid_until'],
            assessment.assessment.expires_at,assessment.risk_terms.submission_deadline,qualification.valid_until,
            assessment.rules.valid_until,assessment.capabilities.valid_until,assessment.profile.valid_until,
            permit.expires_at,lease.expires_at,duty)
        valid_until=min(map(utc_timestamp,horizons))
        end_clock,end_lease=repo._clock_lease(write)
        require(digest(end_lease)==digest(lease) and end_clock.monotonic_ns>=clock.monotonic_ns
            and before<=utc_timestamp(end_clock.utc_now)<valid_until
            and type(permit.deadline_monotonic_ns) is int and end_clock.monotonic_ns<permit.deadline_monotonic_ns,
            'PROGRAM_CONSUMED_WINDOW_EXPIRED')
        # Re-read physical expiry after source/current checks, not only before
        # them. The caller must still repeat this guard after any later wait.
        final_source=_protected(tx,permit.command_id)
        require(canonical_hash(final_source[0])==canonical_hash(consumed),'PROGRAM_CONSUMED_CONTEXT_CHANGED')
        remaining_us=(valid_until-utc_timestamp(end_clock.utc_now))//timedelta(microseconds=1)
        deadline=min(permit.deadline_monotonic_ns,lease.deadline_monotonic_ns,
            end_clock.monotonic_ns+min(remaining_us,final_source[-1])*1000)
        require(deadline>end_clock.monotonic_ns,'PROGRAM_CONSUMED_WINDOW_EXPIRED')
        result=ProgramConsumedRequest(write=write,request=request,clock=end_clock,valid_until=valid_until.isoformat(),
            deadline_monotonic_ns=deadline,consumption_context_hash=canonical_hash(consumed),
            source_ref='ledger.program_permit_consumptions:'+scope_id(write.scope)+':'+write.command_id)
    return result
