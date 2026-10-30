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


def _original_permit_window(tx,marked,permit):
    """Restore the exact existing mark/boundary token labels, never renew them.

    This is the same TTL formula used by mark_may_have_sent, using its protected
    original clock/lease/horizon and the trusted boundary's unchanged TTL policy.
    A changed policy fails closed; a new process must query original IDs rather
    than recreate a send token. This performs no historical financial replay.
    """
    clock=marked['clock'];lease=marked['lease']
    require(type(tx.permit_ttl_ms) is int and 1<=tx.permit_ttl_ms<=5000
        and clock['clock_health']=='HEALTHY' and type(clock['monotonic_ns']) is int
        and clock['monotonic_ns']>=0 and type(lease['deadline_monotonic_ns']) is int,
        'PROGRAM_ORIGINAL_PERMIT_WINDOW_REQUIRED')
    marked_at=utc_timestamp(clock['utc_now'])
    # Preserve the frozen constructor's millisecond truncation exactly.
    ttl=min(tx.permit_ttl_ms,int((utc_timestamp(marked['valid_until'])-marked_at).total_seconds()*1000),
        (lease['deadline_monotonic_ns']-clock['monotonic_ns'])//1_000_000)
    require(ttl>=1 and type(permit.deadline_monotonic_ns) is int
        and permit.deadline_monotonic_ns==clock['monotonic_ns']+ttl*1_000_000
        and permit.expires_at==(marked_at+timedelta(milliseconds=ttl)).isoformat(),
        'PROGRAM_ORIGINAL_PERMIT_WINDOW_CHANGED')


def recheck_consumed(boundary,permit,lease):
    require(type(boundary) is ProgramCommandBoundary and type(permit) is ProgramWritePermit
        and type(lease) is t.OwnerLease,'PROGRAM_ACTUAL_BOUNDARY_PERMIT_LEASE_REQUIRED')
    require(type(boundary.scope) is t.ScopeKey and boundary.scope.environment==t.Environment.PAPER
        and permit.scope==boundary.scope,'PROGRAM_CONSUMED_SCOPE_CHANGED')
    with boundary.uow.transaction(boundary.scope) as tx:
        consumed,marked,marked_write,may_write,c_token,m_token,_=_protected(tx,permit.command_id)
        require(c_token==m_token==_token_hash(permit.token),'PROGRAM_CONSUMED_TOKEN_CHANGED')
        _original_permit_window(tx,marked,permit)
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
        bundle=decode(ProgramFinancialBundle,source[0])
        assessment=decode(ProgramFinancialAssessmentArchive,consumed['assessment'])
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
        require(book is not None and digest(book)==digest(assessment.book),'PROGRAM_CONSUMED_BOOK_CHANGED')
        duty=LockedActionVerifier().verify_program(tx,bundle.intent,assessment,clock.utc_now,
            book=book,read_proof=boundary.readers['load_action_proof'])
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
