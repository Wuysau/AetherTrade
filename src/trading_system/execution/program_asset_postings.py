"""Immutable per-native economic records in the existing audit ledger.

These contain deltas, never a second running balance. Their data is not an
authority: readers must rederive every value from protected source/native fills.
The owner/book writer is responsible for the accompanying position transaction.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from .program_asset_reader import AuthenticatedProgramAssetEffects


def economic_signature(fact):
    fill=fact.fill
    return canonical_hash(dict(scope=domain_payload(fill.scope),command_id=fact.command_id,
        venue=fill.venue,native_trade_id=fill.native_trade_id,order=domain_payload(fill.order),
        quantity=domain_payload(fill.quantity),price=domain_payload(fill.price),
        fees=[domain_payload(m) for m in fact.effects.fees],
        unit=domain_payload(fact.approval.normalized.economics.unit),
        source_raw_hash=fill.evidence.hash,effects=domain_payload(fact.effects)))


@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramAssetPostingRecord:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['PROGRAM_ASSET_POSTING_V1']
    scope:t.ScopeKey
    command_id:str
    identity_hash:str
    source_approval_id:str
    fill_id:str
    source_economic_hash:str
    asset_deltas:tuple[t.Money,...]
    position_delta:t.Quantity

    @property
    def record_id(self):return 'program-asset-posting:'+self.identity_hash


def make_program_asset_posting(fact):
    if type(fact) is not AuthenticatedProgramAssetEffects:raise TypeError('authenticated native effects required')
    return ProgramAssetPostingRecord(contract_version='2026-10-30',record_kind='PROGRAM_ASSET_POSTING_V1',
        scope=fact.fill.scope,command_id=fact.command_id,identity_hash=fact.approval.identity_hash,
        source_approval_id=fact.approval.approval_id,fill_id=fact.fill.fill_id,
        source_economic_hash=economic_signature(fact),asset_deltas=fact.effects.asset_deltas,
        position_delta=fact.effects.position_delta)
