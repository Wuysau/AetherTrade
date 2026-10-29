"""Typed order capability/rule snapshots; evidence claims never grant permission."""
from dataclasses import dataclass
from typing import Literal
from . import types as t
from .canonical import utc_timestamp,canonical_hash,domain_payload
from .order_program import identifier,amount

@dataclass(frozen=True,slots=True,kw_only=True)
class OrderCapabilitySnapshot:
    contract_version:Literal['2026-10-28']
    snapshot_id:str
    scope:t.ScopeKey
    venue:Literal['BINANCE']
    adapter_build_digest:str
    transport:Literal['REST','WS','FIX','PAPER']
    rule_revision:int
    capability_ids:tuple[str,...]
    qualification:Literal['NOT_RUN','PAPER_VERIFIED','TESTNET_VERIFIED','LIVE_VERIFIED']
    evidence:tuple[t.EvidenceRef,...]
    as_of:str
    valid_until:str
    def __post_init__(self):
        identifier(self.snapshot_id)
        if self.rule_revision<1 or len(self.adapter_build_digest)!=64:raise ValueError('Build/revision required')
        if utc_timestamp(self.as_of)>=utc_timestamp(self.valid_until):raise ValueError('Invalid snapshot validity')
        if len(set(self.capability_ids))!=len(self.capability_ids):raise ValueError('Duplicate capability')
        if self.qualification!='NOT_RUN' and not self.evidence:raise ValueError('Qualification evidence missing')
        if any(e.environment!=self.scope.environment for e in self.evidence):raise ValueError('Evidence environment mismatch')

@dataclass(frozen=True,slots=True,kw_only=True)
class OrderRuleSnapshot:
    contract_version:Literal['2026-10-28']
    snapshot_id:str
    scope:t.ScopeKey
    venue:Literal['BINANCE']
    revision:int
    tick_size:str
    quantity_step:str
    minimum_quantity:str
    minimum_notional:str
    quote_asset:str
    base_asset:str
    allowed_tifs:tuple[Literal['GTC','IOC','FOK','GTX','GTD','DAY','RPI'],...]
    maximum_callback_percent:str|None
    evidence:tuple[t.EvidenceRef,...]
    as_of:str
    valid_until:str
    def __post_init__(self):
        for x in (self.snapshot_id,self.quote_asset,self.base_asset):identifier(x)
        for x in (self.tick_size,self.quantity_step):amount(x)
        for x in (self.minimum_quantity,self.minimum_notional):amount(x,zero=True)
        if self.maximum_callback_percent is not None:amount(self.maximum_callback_percent)
        if self.revision<1 or not self.evidence:raise ValueError('Rule provenance required')
        if utc_timestamp(self.as_of)>=utc_timestamp(self.valid_until):raise ValueError('Invalid rule validity')
        if any(e.environment!=self.scope.environment for e in self.evidence):raise ValueError('Evidence environment mismatch')
