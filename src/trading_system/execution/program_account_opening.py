"""Explicit immutable PAPER initial assets, separate from current cash totals.

The separately provisioned initialization role is the authority. An opening
DTO/label does not grant capital, position ownership, order or release permission.
No current book or model proposal is used to infer an initial balance.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload,utc_timestamp
from trading_system.storage.models import decode
from trading_system.storage.uow import UnitOfWork


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramAccountKey:
    environment:t.Environment
    venue_account_id:str
    product:t.Product

    def __post_init__(self):
        require(type(self.environment) is t.Environment and type(self.product) is t.Product
            and type(self.venue_account_id) is str and bool(self.venue_account_id),'ACCOUNT_IDENTITY_REQUIRED')

    @property
    def key(self):return canonical_hash(domain_payload(self))


def account_key(scope):
    return ProgramAccountKey(environment=scope.environment,venue_account_id=scope.venue_account_id,product=scope.product)


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramAccountOpening:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_ACCOUNT_OPENING_V1']
    source_kind:Literal['EXPLICIT_NEW_PAPER_VIRTUAL_CAPITAL']
    account:ProgramAccountKey
    collateral_group_id:str
    balances:tuple[t.Money,...]
    evidence:t.EvidenceRef
    as_of:str

    def __post_init__(self):
        require(self.contract_version=='2026-10-30' and self.record_kind=='PROGRAM_ACCOUNT_OPENING_V1'
            and self.source_kind=='EXPLICIT_NEW_PAPER_VIRTUAL_CAPITAL' and self.account.environment==t.Environment.PAPER,
            'EXPLICIT_PAPER_OPENING_REQUIRED')
        require(type(self.collateral_group_id) is str and bool(self.collateral_group_id),'OPENING_GROUP_REQUIRED')
        require(type(self.balances) is tuple and bool(self.balances) and all(type(m) is t.Money
            and type(m.amount) is Decimal and m.amount.is_finite() and m.amount>=0
            and type(m.currency) is str and bool(m.currency) for m in self.balances),'EXPLICIT_INITIAL_ASSETS_REQUIRED')
        assets=tuple(m.currency for m in self.balances)
        require(assets==tuple(sorted(set(assets))),'EXPLICIT_UNIQUE_SORTED_INITIAL_ASSETS_REQUIRED')
        ref=self.evidence
        require(type(ref) is t.EvidenceRef and ref.environment==self.account.environment and ref.id and ref.source
            and len(ref.hash)==64 and all(c in '0123456789abcdef' for c in ref.hash)
            and ref.visible_at==self.as_of,'OPENING_PROVENANCE_REQUIRED')
        utc_timestamp(self.as_of)

    @property
    def opening_id(self):return canonical_hash(domain_payload(self))
    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


def read_program_account_opening(tx):
    if tx.execute("SELECT to_regclass('ledger.program_account_openings')").fetchone()[0] is None:return None
    account=account_key(tx.scope)
    row=tx.execute('SELECT payload,payload_hash,database_transaction::text,xmin::text,pg_current_xact_id()::text '
        'FROM ledger.program_account_openings WHERE account_key=%s',(account.key,)).fetchone()
    if row is None:return None
    require(row[2]!=row[4] and int(row[2])%(2**32)==int(row[3]),'COMMITTED_ACCOUNT_OPENING_REQUIRED')
    proof=decode(ProgramAccountOpening,row[0])
    require(proof.opening_id==row[1] and proof.account==account and proof.collateral_group_id==tx.group_id,
        'ACCOUNT_OPENING_BINDING_CHANGED')
    return proof


class ProgramAccountOpeningService:
    def __init__(self,uow,scope):
        require(type(uow) is UnitOfWork and type(scope) is t.ScopeKey and scope.environment==t.Environment.PAPER,
            'PAPER_OPENING_UOW_REQUIRED')
        self.uow,self.scope=uow,scope

    def initialize(self,balances,*,evidence):
        require(type(balances) is tuple and all(type(m) is t.Money for m in balances),'EXPLICIT_INITIAL_ASSETS_REQUIRED')
        with self.uow.transaction(self.scope) as tx:
            proof=ProgramAccountOpening(contract_version='2026-10-30',record_kind='PROGRAM_ACCOUNT_OPENING_V1',
                source_kind='EXPLICIT_NEW_PAPER_VIRTUAL_CAPITAL',account=account_key(self.scope),collateral_group_id=tx.group_id,
                balances=tuple(sorted(balances,key=lambda m:m.currency)),evidence=evidence,as_of=evidence.visible_at)
            result=tx.execute('SELECT ledger.provision_program_account_opening(%s,%s::jsonb,%s)',
                (proof.account.key,canonical_json(domain_payload(proof)),tx.transaction_id)).fetchone()
            require(result==(proof.opening_id,),'ACCOUNT_OPENING_COMMITMENT_CHANGED')
        return proof
