"""Protected historical source attestations; no cash, positions or send authority.

The trusted host explicitly provisions a separate verifier-role UnitOfWork.
Actual PAPER verification and fsync finish before this transaction opens. Its
commit precedes any future owner posting transaction; no cross-role nested lock
or caller-supplied `source_verified` flag can approve an execution fact.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.storage.models import scope_id,decode
from trading_system.storage.uow import UnitOfWork
from .paper_program_reports import (PaperProgramReportVerifier,PaperProgramVerification,
    NormalizedPaperFill,NativeFillEconomics,normalize_native_economics)
from .paper_program_v2_reports import (PaperProgramV2ReportVerifier,PaperProgramVerificationV2,NormalizedPaperFillV2)


SOURCE_VERSION_ROUTES=((PaperProgramReportVerifier,PaperProgramVerification,NormalizedPaperFill),
    (PaperProgramV2ReportVerifier,PaperProgramVerificationV2,NormalizedPaperFillV2))


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFillSourceApproval:
    approval_id:str
    identity_hash:str
    normalized:NormalizedPaperFill|NormalizedPaperFillV2
    command_id:str
    verification_status:Literal['VERIFIED','DISCREPANCY']
    verification_discrepancies:tuple[str,...]
    retained_duties:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def balance_credit_authorized(self)->Literal[False]:return False


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramFillSourceResult:
    command_id:str
    status:Literal['BLOCKED','NO_FILLS','APPROVED','SOURCE_ONLY']
    verification:PaperProgramVerification|PaperProgramVerificationV2
    approvals:tuple[ProgramFillSourceApproval,...]

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False
    @property
    def balance_credit_authorized(self)->Literal[False]:return False


def _source_context(tx,command_id):
    row=tx.execute('SELECT c.context,m.context,w.payload,b.payload,c.token_hash,m.token_hash '
        'FROM ledger.program_permit_consumptions c JOIN ledger.program_dispatch_marks m USING(scope_id,command_id) '
        'JOIN ledger.order_writes_v2 w ON w.scope_id=c.scope_id AND w.record_id=c.command_id AND w.sequence=1 '
        'JOIN ledger.program_request_bindings b ON b.scope_id=c.scope_id AND b.record_id=c.command_id AND b.sequence=1 '
        'WHERE c.scope_id=%s AND c.command_id=%s',(scope_id(tx.scope),command_id)).fetchone()
    require(row is not None and row[4]==row[5],'PROGRAM_COMMITTED_SOURCE_REQUIRED')
    consumed,marked,write,binding,_,_=row
    require(write['scope']==domain_payload(tx.scope) and binding['request_hash']==write['request_hash']
        and canonical_hash(binding)==write['binding_hash'],'PROGRAM_ORIGINAL_SOURCE_CHANGED')
    return dict(consumption_hash=canonical_hash(consumed),dispatch_mark_hash=canonical_hash(marked),
        original_write_hash=canonical_hash(write),binding_hash=canonical_hash(binding),request_hash=write['request_hash'])


def decode_source_normalized(stored,verification):
    """Static tagged decoder. Untagged original V1 bytes keep their old codec."""
    from trading_system.engine.paper_market import PaperMarketFill
    tags=('contract_version','record_kind','source_v2')
    if any(key in stored for key in tags):
        require(stored.get('contract_version')=='2026-10-30' and stored.get('record_kind')=='NORMALIZED_PAPER_FILL_V2'
            and 'source_v2' in stored and verification.get('contract_version')=='2026-10-30'
            and verification.get('record_kind')=='PAPER_PROGRAM_VERIFICATION_V2','PROGRAM_NORMALIZED_SOURCE_VERSION_REQUIRED')
        normalized=decode(NormalizedPaperFillV2,stored)
        source=normalized.source_v2;raw=normalized.raw
        require(type(raw) is PaperMarketFill and source.node_id==raw.node_id and source.client_id==raw.client_id
            and source.client_id==normalized.fill.order.logical_client_order_id
            and source.causal_graph_hash==verification.get('causal_graph_hash'), 'PROGRAM_V2_SOURCE_PROVENANCE_CHANGED')
        return normalized
    require(not any(key in verification for key in ('contract_version','record_kind')),
        'PROGRAM_NORMALIZED_SOURCE_VERSION_REQUIRED')
    return NormalizedPaperFill(fill=decode(t.Fill,stored['fill']),side=stored['side'],
        economics=decode(NativeFillEconomics,stored['economics']),raw=decode(PaperMarketFill,stored['raw']),
        fees_known=stored['fees_known'])


def save_verification_snapshot(tx,verified):
    """Persist an exact verifier result, including an accepted zero-fill query.

    Verification/archive I/O belongs before this protected source transaction.
    This helper does no cash, ownership, command-outcome, release or permit work.
    """
    route=next((r for r in SOURCE_VERSION_ROUTES if type(verified) is r[1]),None)
    require(route is not None and verified.source_verified is True
        and all(type(n) is route[2] for n in verified.fills),'ACTUAL_PAPER_VERIFICATION_REQUIRED')
    command_id=verified.command_id;context=_source_context(tx,command_id)
    verification_payload=domain_payload(verified)
    normalized_payloads=tuple(domain_payload(n) for n in verified.fills)
    hashes=tuple(canonical_hash(n) for n in normalized_payloads)
    for stored in normalized_payloads:
        require(domain_payload(decode_source_normalized(stored,verification_payload))==stored,
            'PROGRAM_APPROVED_NATIVE_FACTS_CHANGED')
    snapshot=dict(contract_version='2026-10-30',record_kind='PAPER_PROGRAM_FILL_SOURCE_SNAPSHOT_V1',
        scope=domain_payload(tx.scope),command_id=command_id,**context,verification=verification_payload,
        normalized_fills=dict(zip(hashes,normalized_payloads)))
    source_hash=canonical_hash(snapshot)
    saved=tx.execute('SELECT ledger.approve_program_fill_source_snapshot(%s,%s,%s::jsonb,%s)',
        (scope_id(tx.scope),command_id,canonical_json(snapshot),tx.transaction_id)).fetchone()
    require(saved==(source_hash,),'PROGRAM_SOURCE_COMMITMENT_CHANGED')
    return source_hash,hashes


def read_committed_fill_source(tx,approval_id):
    return CommittedProgramFillSources(tx).read(approval_id)


class CommittedProgramFillSources:
    """One transaction's immutable historical proofs; no current risk cache.

    Batch consumers reuse this reader so each shared source snapshot is loaded
    and hashed once. It never caches owner/book/lease/permission or an ALLOW.
    """
    def __init__(self,tx):
        self.tx=tx
        self._snapshots={}

    def _snapshot(self,source_hash,command_id):
        tx=self.tx
        require(tx.active and not tx.failed,'PROGRAM_SOURCE_TRANSACTION_CLOSED')
        if source_hash not in self._snapshots:
            row=tx.execute('SELECT payload,payload_hash,database_transaction::text,xmin::text,pg_current_xact_id()::text '
                'FROM ledger.program_fill_source_snapshots WHERE scope_id=%s AND command_id=%s AND snapshot_id=%s',
                (scope_id(tx.scope),command_id,source_hash)).fetchone()
            require(row is not None and row[2]!=row[4] and int(row[2])%(2**32)==int(row[3]),
                'PROGRAM_COMMITTED_SOURCE_SNAPSHOT_REQUIRED')
            payload=row[0]
            require(source_hash==row[1]==canonical_hash(payload) and payload['scope']==domain_payload(tx.scope)
                and payload['command_id']==command_id and payload['contract_version']=='2026-10-30'
                and payload['record_kind']=='PAPER_PROGRAM_FILL_SOURCE_SNAPSHOT_V1',
                'PROGRAM_SOURCE_SNAPSHOT_CHANGED')
            context=_source_context(tx,command_id)
            require(all(payload[key]==value for key,value in context.items()),'PROGRAM_ORIGINAL_SOURCE_CHANGED')
            self._snapshots[source_hash]=payload
        return self._snapshots[source_hash]

    def read(self,approval_id):
        require(self.tx.active and not self.tx.failed,'PROGRAM_SOURCE_TRANSACTION_CLOSED')
        return _read_committed_fill_source(self.tx,approval_id,self)

    def read_snapshot(self,command_id,source_hash):
        """Read an exact committed query proof, including an actual zero fill.

        Reuses the same original-context/hash/physical-commit checks and live
        transaction cache. A snapshot supplies observations, never permissions.
        """
        return self._snapshot(source_hash,command_id)


def _read_committed_fill_source(tx,approval_id,reader):
    """Read immutable historical facts only, without venue/file IO or new authority.

    An approval inserted by this physical transaction is unusable. Recheck the
    content and original protected context, rather than trusting owner audit
    records or a caller's in-memory approval receipt. Economic postings still
    require the separate fill/book/ownership checks in their owner transaction.
    """
    from trading_system.engine.paper_market import PaperMarketFill
    require(type(approval_id) is str and len(approval_id)==64
        and all(c in '0123456789abcdef' for c in approval_id),'PROGRAM_COMMITTED_SOURCE_APPROVAL_REQUIRED')
    row=tx.execute('SELECT command_id,identity_hash,payload,payload_hash,database_transaction::text,xmin::text,'
        'pg_current_xact_id()::text FROM ledger.program_fill_source_approvals '
        'WHERE scope_id=%s AND approval_id=%s',(scope_id(tx.scope),approval_id)).fetchone()
    require(row is not None and row[4]!=row[6] and int(row[4])%(2**32)==int(row[5]),
        'PROGRAM_COMMITTED_SOURCE_APPROVAL_REQUIRED')
    command_id,identity,payload,payload_hash,*_=row
    require(payload_hash==approval_id==canonical_hash(payload)
        and payload['contract_version']=='2026-10-30'
        and payload['record_kind'] in ('PAPER_PROGRAM_FILL_SOURCE_V1','PAPER_PROGRAM_FILL_SOURCE_V2')
        and payload['scope']==domain_payload(tx.scope) and tx.scope.environment==t.Environment.PAPER
        and payload['command_id']==command_id,'PROGRAM_SOURCE_COMMITMENT_CHANGED')
    if payload['record_kind']=='PAPER_PROGRAM_FILL_SOURCE_V1':
        context=_source_context(tx,command_id)
        require(all(payload[key]==value for key,value in context.items()),'PROGRAM_ORIGINAL_SOURCE_CHANGED')
        stored=payload['normalized']
        verification=payload['verification']
        consumed_hash=context['consumption_hash']
    else:
        source=reader._snapshot(payload['source_snapshot_hash'],command_id)
        stored=source['normalized_fills'].get(payload['normalized_hash'])
        require(stored is not None and canonical_hash(stored)==payload['normalized_hash'],'PROGRAM_APPROVED_NATIVE_FACTS_CHANGED')
        verification=source['verification']
        consumed_hash=source['consumption_hash']
    normalized=decode_source_normalized(stored,verification)
    fill,economics,raw=normalized.fill,normalized.economics,normalized.raw
    if type(normalized) is NormalizedPaperFillV2:
        require(payload['record_kind']=='PAPER_PROGRAM_FILL_SOURCE_V2'
            and normalized.source_v2.consumption_context_hash==consumed_hash,
            'PROGRAM_V2_SOURCE_PROVENANCE_CHANGED')
    rederived=normalize_native_economics(quantity=economics.native_quantity,price=economics.venue_price,
        fees=economics.fees,reported_gross=economics.reported_gross,unit=economics.unit)
    require(domain_payload(normalized)==stored and domain_payload(rederived)==domain_payload(economics)
        and fill.scope==fill.order.scope==tx.scope and fill.quantity==economics.native_quantity
        and fill.price==economics.venue_price and fill.native_trade_id==raw.fill_id
        and fill.quantity.value==raw.quantity and fill.quantity.unit==raw.quantity_unit
        and fill.price.value==raw.price and fill.price.currency==raw.price_asset
        and fill.evidence.hash==raw.evidence.hash and normalized.side in ('BUY','SELL')
        and type(normalized.fees_known) is bool,'PROGRAM_APPROVED_NATIVE_FACTS_CHANGED')
    require(identity==canonical_hash(dict(scope=domain_payload(tx.scope),venue=fill.venue,
        native_trade_id=fill.native_trade_id)),'PROGRAM_APPROVED_NATIVE_IDENTITY_CHANGED')
    require(verification['command_id']==command_id and verification['source_verified'] is True
        and verification['status'] in ('VERIFIED','DISCREPANCY')
        and type(verification['discrepancies']) is list and all(type(c) is str for c in verification['discrepancies'])
        and type(verification['retained_duties']) is list and all(type(c) is str for c in verification['retained_duties']),
        'PROGRAM_APPROVED_SOURCE_REVIEW_CHANGED')
    return ProgramFillSourceApproval(approval_id=approval_id,identity_hash=identity,normalized=normalized,command_id=command_id,
        verification_status=verification['status'],verification_discrepancies=tuple(verification['discrepancies']),
        retained_duties=tuple(verification['retained_duties']))


class ProgramFillSourceService:
    def __init__(self,uow,scope,*,verifier):
        require(type(uow) is UnitOfWork,'PROGRAM_SOURCE_UOW_REQUIRED')
        require(type(scope) is t.ScopeKey and scope.environment==t.Environment.PAPER,'PAPER_SOURCE_ONLY')
        route=next((r for r in SOURCE_VERSION_ROUTES if type(verifier) is r[0]),None)
        require(route is not None and verifier.scope==scope,
            'ACTUAL_SCOPED_PAPER_VERIFIER_REQUIRED')
        self.uow,self.scope,self.verifier=uow,scope,verifier
        self.verification_type=route[1]

    def approve(self,command_id):
        require(type(command_id) is str and 0<len(command_id)<=256,'ORIGINAL_COMMAND_ID_REQUIRED')
        # All venue reads, normalization and durable archive fsync finish first.
        verified=self.verifier.verify(command_id)
        require(type(verified) is self.verification_type and verified.command_id==command_id,
            'ACTUAL_PAPER_VERIFICATION_REQUIRED')
        if not verified.source_verified:
            return ProgramFillSourceResult(command_id=command_id,status='BLOCKED',verification=verified,approvals=())
        if not verified.fills:
            return ProgramFillSourceResult(command_id=command_id,status='NO_FILLS',verification=verified,approvals=())
        approvals=[]
        # Serialize the full proof and each native fact once, outside the owner
        # posting transaction. V2 stores one proof snapshot per observation;
        # per-fill SQL parameters contain only hashes/IDs, never another report.
        with self.uow.transaction(self.scope) as tx:
            source_hash,normalized_hashes=save_verification_snapshot(tx,verified)
            for normalized,normalized_hash in zip(verified.fills,normalized_hashes):
                payload=dict(contract_version='2026-10-30',record_kind='PAPER_PROGRAM_FILL_SOURCE_V2',
                    scope=domain_payload(self.scope),command_id=command_id,
                    source_snapshot_hash=source_hash,normalized_hash=normalized_hash)
                expected=canonical_hash(payload)
                row=tx.execute('SELECT ledger.approve_program_fill_source_v2(%s,%s,%s,%s,%s)',
                    (scope_id(self.scope),command_id,source_hash,normalized_hash,tx.transaction_id)).fetchone()
                require(row==(expected,),'PROGRAM_SOURCE_COMMITMENT_CHANGED')
                identity=canonical_hash(dict(scope=domain_payload(self.scope),venue=normalized.fill.venue,
                    native_trade_id=normalized.fill.native_trade_id))
                approvals.append(ProgramFillSourceApproval(approval_id=expected,identity_hash=identity,normalized=normalized,
                    command_id=command_id,verification_status=verified.status,verification_discrepancies=verified.discrepancies,
                    retained_duties=verified.retained_duties))
        # A receipt is returned only after the protected source transaction commits.
        return ProgramFillSourceResult(command_id=command_id,
            status='SOURCE_ONLY' if verified.discrepancies else 'APPROVED',verification=verified,approvals=tuple(approvals))
