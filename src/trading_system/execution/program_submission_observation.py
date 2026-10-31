"""Protected submission queries; known acceptance is neither a fill nor credit."""
from dataclasses import dataclass,replace
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.application_write_v3 import ApplicationWriteV3
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.engine.paper_market import SOURCE
from trading_system.storage.models import MissingEvidence,decode,scope_id,account_key
from trading_system.storage.uow import UnitOfWork
from .program_fill_source import SOURCE_VERSION_ROUTES,_source_context,save_verification_snapshot


def need(value,code):
    if not value:raise MissingEvidence(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class SubmissionMember:
    node_id:str
    client_id:str
    venue_order_id:str
    namespace:str
    lifecycle:str
    revision:int


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramSubmissionObservationV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_SUBMISSION_OBSERVATION_V1']
    scope:t.ScopeKey
    command_id:str
    probe_id:str
    phase:Literal['STARTED','COMPLETED']
    state:Literal['ACCEPTED','REJECTED','UNKNOWN']
    consumption_hash:str
    dispatch_mark_hash:str
    original_write_hash:str
    binding_hash:str
    request_hash:str
    source_snapshot_hash:str|None
    manifest_ref:t.EvidenceRef|None
    raw_refs:tuple[t.EvidenceRef,...]
    members:tuple[SubmissionMember,...]
    observed_clock:t.ClockReading|None
    reasons:tuple[str,...]


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramSubmissionObservationView:
    observation_id:str
    record:ProgramSubmissionObservationV1
    ever_accepted:bool
    ever_rejected:bool

    @property
    def state(self):return self.record.state
    @property
    def source_snapshot_hash(self):return self.record.source_snapshot_hash
    @property
    def submission_known(self):return self.record.phase=='COMPLETED' and self.state in ('ACCEPTED','REJECTED')
    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


def _hash(value):return canonical_hash(domain_payload(value))


def _record(tx,command_id,probe_id,**values):
    return ProgramSubmissionObservationV1(contract_version='2026-10-30',record_kind='PROGRAM_SUBMISSION_OBSERVATION_V1',
        scope=tx.scope,command_id=command_id,probe_id=probe_id,**_source_context(tx,command_id),**values)


def read_program_submission_observation(tx,original):
    """SELECT-only current query state. No live venue I/O or permission cache."""
    need(type(original) is ApplicationWriteV3 and original.state=='DURABLE_READY','ORIGINAL_PROGRAM_WRITE_REQUIRED')
    if tx.execute("SELECT to_regclass('ledger.program_submission_observations')").fetchone()[0] is None:return None
    sid=scope_id(original.scope)
    bound=tx.execute('SELECT collateral_group_id FROM ledger.account_bindings WHERE environment=%s AND account_id=%s AND product=%s',
        account_key(original.scope)).fetchone()
    need(bound==(tx.group_id,),'SUBMISSION_OBSERVATION_GROUP_CHANGED')
    initial=tx.execute('SELECT payload FROM ledger.order_writes_v2 WHERE scope_id=%s AND record_id=%s AND sequence=1',
        (sid,original.command_id)).fetchone()
    need(initial is not None and canonical_hash(initial[0])==_hash(original),'SUBMISSION_OBSERVATION_ORIGINAL_CHANGED')
    fields='payload,payload_hash,database_transaction::text,xmin::text,pg_current_xact_id()::text'
    started=tx.execute('SELECT '+fields+' FROM ledger.program_submission_observations WHERE scope_id=%s AND command_id=%s '
        "AND phase='STARTED' ORDER BY sequence DESC LIMIT 1",(sid,original.command_id)).fetchone()
    # A newly marked MAY has not consumed yet. With no protected query probe it
    # remains unknown; it must not require a consumption merely to read duties.
    if started is None:return None
    view=SimpleNamespace(scope=original.scope,execute=tx.execute)
    context=_source_context(view,original.command_id)
    def checked(row):
        payload,digest,database_transaction,xmin,current=row
        need(database_transaction!=current and int(database_transaction)%(2**32)==int(xmin),
            'COMMITTED_SUBMISSION_OBSERVATION_REQUIRED')
        record=decode(ProgramSubmissionObservationV1,payload)
        need(_hash(record)==digest and record.scope==original.scope and record.command_id==original.command_id
            and all(getattr(record,k)==v for k,v in context.items()),'SUBMISSION_OBSERVATION_CONTEXT_CHANGED')
        if record.phase=='COMPLETED' and record.state!='UNKNOWN':
            source=tx.execute('SELECT payload_hash FROM ledger.program_fill_source_snapshots '
                'WHERE scope_id=%s AND command_id=%s AND snapshot_id=%s',
                (sid,original.command_id,record.source_snapshot_hash)).fetchone()
            need(source==(record.source_snapshot_hash,) and record.manifest_ref is not None
                and record.manifest_ref.environment==t.Environment.PAPER and record.manifest_ref.source==SOURCE
                and bool(record.members),'SUBMISSION_OBSERVATION_SOURCE_REQUIRED')
        return record
    start=checked(started)
    completed=tx.execute('SELECT '+fields+' FROM ledger.program_submission_observations WHERE scope_id=%s AND command_id=%s '
        "AND probe_id=%s AND phase='COMPLETED'",(sid,original.command_id,start.probe_id)).fetchone()
    current=checked(completed) if completed is not None else start
    history=tx.execute("SELECT coalesce(bool_or(state='ACCEPTED'),false),coalesce(bool_or(state='REJECTED'),false) "
        "FROM ledger.program_submission_observations WHERE scope_id=%s AND command_id=%s AND phase='COMPLETED'",
        (sid,original.command_id)).fetchone()
    return ProgramSubmissionObservationView(observation_id=(completed or started)[1],record=current,
        ever_accepted=history[0],ever_rejected=history[1])


class ProgramSubmissionObservationService:
    def __init__(self,uow,scope,*,verifier,raise_query_errors=False):
        need(type(uow) is UnitOfWork and type(scope) is t.ScopeKey and scope.environment==t.Environment.PAPER,
            'PAPER_SUBMISSION_SOURCE_UOW_REQUIRED')
        route=next((r for r in SOURCE_VERSION_ROUTES if type(verifier) is r[0]),None)
        need(route is not None and verifier.scope==scope,'ACTUAL_SCOPED_PAPER_VERIFIER_REQUIRED')
        need(type(raise_query_errors) is bool,'SUBMISSION_ERROR_POLICY_REQUIRED')
        self.uow,self.scope,self.verifier,self.result_type=uow,scope,verifier,route[1]
        self.raise_query_errors=raise_query_errors

    def _clock(self):
        try:
            reading=self.verifier.engine._clock()
            need(type(reading) is t.ClockReading and type(reading.monotonic_ns) is int and reading.monotonic_ns>=0,
                'SUBMISSION_SOURCE_CLOCK_REQUIRED')
            utc_timestamp(reading.utc_now)
            return reading
        except (ValueError,TypeError,AttributeError,MissingEvidence):return None

    def _archive(self,payload,clock):
        need(clock is not None,'SUBMISSION_SOURCE_CLOCK_REQUIRED')
        raw=canonical_json(payload).encode('utf-8')
        return self.verifier.archive_bridge.ingest(raw,source=SOURCE,received_at=clock.utc_now,
            processed_at=clock.utc_now).raw.raw_payload_ref

    def _actual_manifest(self,verified,clock,refs):
        """Bind V1 no-fill IDs too; an in-memory reseal is not venue presence."""
        request=verified.raw_report.request;clients=[m.client_id for m in request.assigned_ids.members]
        with self.verifier.engine._connect() as db:
            db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            command=db.execute('SELECT request,request_hash FROM public.paper_engine_commands WHERE scope_key=%s AND command_id=%s '
                "AND operation='CREATE_REQUEST'",(self.verifier.engine._scope_key,verified.command_id)).fetchone()
            rows=db.execute('SELECT o.client_id,o.order_id,o.revision,o.lifecycle,o.quantity_unit,o.price_currency,t.terms '
                'FROM public.paper_engine_orders o JOIN public.paper_order_terms t USING(scope_key,client_id) '
                'WHERE o.scope_key=%s AND o.client_id=ANY(%s) ORDER BY o.client_id LIMIT 33',
                (self.verifier.engine._scope_key,clients)).fetchall()
        payload=dict(record_kind='PROGRAM_SUBMISSION_ACTUAL_VENUE_MANIFEST',command=None if command is None else dict(command),
            orders=[dict(r) for r in rows],observed_clock=domain_payload(clock))
        ref=self._archive(payload,clock)
        refs.append(ref)
        need(command is not None and command['request']==domain_payload(request) and command['request_hash']==_hash(request)
            and len(rows)==len(clients)==len(set(clients)) and 0<len(rows)<=32,'ACTUAL_SUBMISSION_COMMAND_REQUIRED')
        orders={o.client_id:o for o in verified.raw_report.orders}
        contexts={a.terms.client_id:a for a in verified.raw_report.accepted_contexts}
        need(set(orders)==set(contexts)==set(clients),'COMPLETE_SUBMISSION_MEMBERS_REQUIRED')
        members=[]
        for row in rows:
            order=orders[row['client_id']];accepted=contexts[row['client_id']]
            durable={**domain_payload(accepted.terms),'write':domain_payload(accepted.original_write),
                'request':domain_payload(accepted.request),'compilation':domain_payload(accepted.compilation),
                'rules':domain_payload(accepted.rules),'model':domain_payload(accepted.model),
                'execution_envelope':domain_payload(accepted.execution_envelope)}
            need(row['terms']==durable and row['order_id']==order.venue_order_id and row['order_id']
                and row['revision']==order.lifecycle_revision and row['lifecycle']==order.status
                and row['quantity_unit']==order.quantity_unit and row['terms']['namespace']==order.namespace
                and row['terms']['node_id']==order.node_id,'ACTUAL_SUBMISSION_MEMBERS_CHANGED')
            need(order.status in ('NEW','SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED'),
                'SUBMISSION_LIFECYCLE_UNSUPPORTED')
            members.append(SubmissionMember(node_id=order.node_id,client_id=order.client_id,venue_order_id=order.venue_order_id,
                namespace=order.namespace,lifecycle=order.status,revision=order.lifecycle_revision))
        return tuple(members),ref

    def begin_query(self,command_id):
        """Commit conservative UNKNOWN before any caller's source I/O.

        A start alone establishes no source/financial fact. A later independent
        observer must complete its own current probe to restore knownness.
        """
        need(type(command_id) is str and 0<len(command_id)<=256,'ORIGINAL_COMMAND_ID_REQUIRED')
        probe=uuid4().hex;clock=self._clock()
        with self.uow.transaction(self.scope) as tx:
            initial=tx.execute('SELECT payload FROM ledger.order_writes_v2 WHERE scope_id=%s AND record_id=%s AND sequence=1',
                (scope_id(self.scope),command_id)).fetchone()
            need(initial is not None,'ORIGINAL_PROGRAM_WRITE_REQUIRED')
            original=VersionedOrderCodec().decode('application-write',initial[0])
            need(type(original) is ApplicationWriteV3,'ORIGINAL_PROGRAM_WRITE_REQUIRED')
            start=_record(tx,command_id,probe,phase='STARTED',state='UNKNOWN',source_snapshot_hash=None,manifest_ref=None,
                raw_refs=(),members=(),observed_clock=clock,reasons=('QUERY_IN_PROGRESS',))
            row=tx.execute('SELECT ledger.begin_program_submission_probe(%s,%s,%s::jsonb,%s)',
                (scope_id(self.scope),command_id,canonical_json(domain_payload(start)),tx.transaction_id)).fetchone()
            need(row==(_hash(start),),'SUBMISSION_PROBE_COMMITMENT_CHANGED')
        return original,probe,clock

    def observe(self,command_id):
        original,probe,clock=self.begin_query(command_id)
        # No ledger/source transaction spans venue read or archive fsync.
        verified=None;members=();manifest=None;refs=[];state='UNKNOWN';reasons=('SUBMISSION_QUERY_FAILED',)
        query_error=None
        try:
            verified=self.verifier.verify(command_id);clock=self._clock()
            summary=dict(record_kind='PROGRAM_SUBMISSION_QUERY_SUMMARY',command_id=command_id,
                status=getattr(verified,'status',None),source_verified=getattr(verified,'source_verified',False),
                raw_refs=[domain_payload(r) for r in getattr(verified,'raw_refs',())])
            refs.append(self._archive(summary,clock))
            need(type(verified) is self.result_type and verified.command_id==command_id
                and verified.source_verified is True and clock is not None and clock.clock_health=='HEALTHY',
                'SUBMISSION_SOURCE_UNVERIFIED')
            members,manifest=self._actual_manifest(verified,clock,refs)
            state='REJECTED' if all(m.lifecycle=='REJECTED' for m in members) else 'ACCEPTED'
            reasons=()
        except (ValueError,TypeError,AttributeError,KeyError,ArithmeticError,MissingEvidence):
            members=();state='UNKNOWN';reasons=('SUBMISSION_SOURCE_UNVERIFIED',)
        except Exception as error:
            # Error text can contain driver connection details. Preserve UNKNOWN,
            # never NOT_SENT; the committed start already blocks if persistence fails.
            members=();state='UNKNOWN';reasons=('SUBMISSION_QUERY_FAILED',)
            query_error=error
        with self.uow.transaction(self.scope) as tx:
            source_hash=None
            if state!='UNKNOWN':source_hash,_=save_verification_snapshot(tx,verified)
            complete=_record(tx,command_id,probe,phase='COMPLETED',state=state,source_snapshot_hash=source_hash,
                manifest_ref=manifest,raw_refs=tuple(refs),members=members,observed_clock=clock,reasons=reasons)
            row=tx.execute('SELECT ledger.record_program_submission_observation(%s,%s,%s::jsonb,%s)',
                (scope_id(self.scope),command_id,canonical_json(domain_payload(complete)),tx.transaction_id)).fetchone()
            need(row==(_hash(complete),),'SUBMISSION_OBSERVATION_COMMITMENT_CHANGED')
        with self.uow.transaction(self.scope) as tx:result=read_program_submission_observation(tx,original)
        if query_error is not None and self.raise_query_errors:raise query_error
        return result
