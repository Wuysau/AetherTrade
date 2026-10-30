"""Original-ID source observation; an ACK never closes cash or child duties."""
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal
import json
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json,domain_payload
from trading_system.storage.models import scope_id
from .program_management_commands import ManagementRepository,digest,require

@dataclass(frozen=True,slots=True,kw_only=True)
class ManagementOutcome:
    command_id:str
    binding_hash:str
    scope:t.ScopeKey
    original_client_id:str
    target_venue_id:str
    cancel_result:Literal['CANCELED','NOT_CANCELED','UNKNOWN']
    replacement_result:Literal['NOT_ATTEMPTED','ACCEPTED','REJECTED','UNKNOWN']
    source_hash:str
    raw_payload_json:str
    funds_released:Literal[False]=False
    children_terminal:Literal[False]=False

def observe_paper_management(service,command_id):
    from .program_management_paper import PaperManagementTargetReader
    reader=service.target_reader
    require(type(reader) is PaperManagementTargetReader,'MANAGEMENT_TRUSTED_TARGET_READER_REQUIRED')
    with service.verifier_uow.transaction(service.scope) as tx:
        binding=ManagementRepository(tx).read(command_id)
        require(binding is not None and binding.write.state in ('MAY_HAVE_SENT','UNKNOWN'),
            'MANAGEMENT_DURABLE_MAY_REQUIRED')
    # Read-only actual isolated venue source, after the business TX closes.
    engine=reader.engine;w=binding.write
    with engine._connect() as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        row=db.execute('SELECT binding_hash,payload::text AS raw FROM public.paper_management_actions_v4 '
            'WHERE scope_key=%s AND command_id=%s',(engine._scope_key,command_id)).fetchone()
    raw='{}' if row is None else row['raw'];p=json.loads(raw)
    if row is not None:
        require(row['binding_hash']==w.binding_hash and p.get('command_id')==command_id
            and p.get('binding_hash')==w.binding_hash and p.get('original_client_id')==w.original_client_id
            and p.get('target_venue_id')==w.target_venue_id and p.get('funds_released') is False
            and p.get('children_terminal') is False,'MANAGEMENT_SOURCE_BINDING_CHANGED')
    outcome=ManagementOutcome(command_id=command_id,binding_hash=w.binding_hash,scope=w.scope,
        original_client_id=w.original_client_id,target_venue_id=w.target_venue_id,
        cancel_result='UNKNOWN' if row is None else p['cancel_result'],
        replacement_result='UNKNOWN' if row is None else p['replacement_result'],
        source_hash=sha256(raw.encode('utf-8')).hexdigest(),raw_payload_json=raw)
    with service.verifier_uow.transaction(service.scope) as tx:
        current=ManagementRepository(tx).read(command_id)
        require(current.write.binding_hash==w.binding_hash,'MANAGEMENT_SOURCE_BINDING_CHANGED')
        tx.execute('SELECT ledger.record_management_outcome(%s,%s,%s::jsonb,%s)',
            (scope_id(w.scope),command_id,canonical_json(domain_payload(outcome)),tx.transaction_id))
    return outcome
