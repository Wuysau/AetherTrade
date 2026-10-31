"""Explicit provisioner attestation of the original console initialization.

Historical audit JSON alone is not capital. This opt-in adapter accepts only
the known single USDT console account and a protected exact-seed commitment.
It never derives opening capital from a current book or invents base inventory.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.storage.models import MissingEvidence,decode,scope_id
from .program_account_opening import (ProgramAccountKey,account_key,read_program_account_opening,
    require_unambiguous_program_account_opening)


def need(condition,code):
    if not condition:raise MissingEvidence(code)


def console_seed_scope():
    return t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='paper-local',product=t.Product.SPOT,
        instrument_uid='console-paper:BTCUSDT',venue_leg='BOTH')


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramLegacyAccountOpening:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_LEGACY_ACCOUNT_OPENING_V1']
    source_kind:Literal['EXPLICIT_LEGACY_CONSOLE_INITIALIZATION_ATTESTATION']
    account:ProgramAccountKey
    collateral_group_id:str
    balances:tuple[t.Money,...]
    evidence:t.EvidenceRef
    as_of:str
    seed_scope_id:str
    seed_hash:str

    @property
    def opening_id(self):return canonical_hash(domain_payload(self))


def _seed(tx):
    rows=tx.execute("SELECT scope_id,sequence,payload FROM ledger.audit_events WHERE record_id='console:seed'").fetchall()
    need(len(rows)==1,'AMBIGUOUS_LEGACY_SEED')
    sid,sequence,payload=rows[0]
    need(sid==scope_id(console_seed_scope()) and sequence==1 and set(payload)=={'initial_cash','source'}
        and payload['source']=='EXPLICIT_NEW_VIRTUAL_CAPITAL' and type(payload['initial_cash']) is str,
        'LEGACY_SEED_BINDING_REQUIRED')
    value=Decimal(payload['initial_cash'])
    need(value.is_finite() and value>=0,'LEGACY_SEED_INITIAL_CASH_REQUIRED')
    return payload,value


def read_program_legacy_opening(tx):
    account=require_unambiguous_program_account_opening(tx)
    if account!=account_key(console_seed_scope()):return None
    if tx.execute("SELECT to_regclass('ledger.program_legacy_account_openings')").fetchone()[0] is None:return None
    row=tx.execute('SELECT payload,payload_hash,database_transaction::text,xmin::text,pg_current_xact_id()::text '
        'FROM ledger.program_legacy_account_openings WHERE account_key=%s',(account.key,)).fetchone()
    if row is None:return None
    need(row[2]!=row[4] and int(row[2])%(2**32)==int(row[3]),'COMMITTED_LEGACY_OPENING_REQUIRED')
    proof=decode(ProgramLegacyAccountOpening,row[0]);seed,value=_seed(tx)
    need(proof.opening_id==row[1] and proof.account==account and proof.collateral_group_id==tx.group_id
        and proof.collateral_group_id=='console-paper-collateral' and proof.seed_hash==canonical_hash(seed)
        and proof.seed_scope_id==scope_id(console_seed_scope())
        and proof.balances==(t.Money(currency='USDT',amount=value),),'LEGACY_SEED_BINDING_CHANGED')
    return proof


def read_program_local_account_opening(tx):
    explicit=read_program_account_opening(tx);legacy=read_program_legacy_opening(tx)
    need(explicit is None or legacy is None,'AMBIGUOUS_ACCOUNT_OPENING')
    return explicit or legacy


class ProgramLegacyOpeningService:
    def __init__(self,uow,scope):
        from trading_system.storage.uow import UnitOfWork
        need(type(uow) is UnitOfWork and account_key(scope)==account_key(console_seed_scope()),'LEGACY_SEED_ACCOUNT_REQUIRED')
        self.uow,self.scope=uow,scope

    def attest(self,seed_hash,*,evidence):
        with self.uow.transaction(self.scope) as tx:
            seed,value=_seed(tx)
            need(seed_hash==canonical_hash(seed),'LEGACY_SEED_HASH_CHANGED')
            proof=ProgramLegacyAccountOpening(contract_version='2026-10-30',record_kind='PROGRAM_LEGACY_ACCOUNT_OPENING_V1',
                source_kind='EXPLICIT_LEGACY_CONSOLE_INITIALIZATION_ATTESTATION',account=account_key(self.scope),
                collateral_group_id=tx.group_id,balances=(t.Money(currency='USDT',amount=value),),
                evidence=evidence,as_of=evidence.visible_at,seed_scope_id=scope_id(console_seed_scope()),seed_hash=seed_hash)
            result=tx.execute('SELECT ledger.attest_program_legacy_opening(%s,%s::jsonb,%s)',
                (proof.account.key,canonical_json(domain_payload(proof)),tx.transaction_id)).fetchone()
            need(result==(proof.opening_id,),'LEGACY_SEED_ATTESTATION_CHANGED')
        return proof
