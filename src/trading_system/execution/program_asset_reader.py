"""Authenticated native execution effects, not a spendable balance or permit.

Every read uses protected committed source and original command, the existing
FillLedger's native ID scope/dedup/evidence rules and the original typed unit.
Caller audit JSON or a normalized decoder output cannot mint an asset credit.
"""
from dataclasses import dataclass
from typing import Literal

from trading_system.contracts import types as t
from trading_system.recovery.original_creation import read_original_creation
from trading_system.risk.program_financial_admission import ProgramFinancialBundle,BUNDLE_PREFIX
from trading_system.storage.models import scope_id,decode
from .fills import FillLedger
from .program_fill_source import ProgramFillSourceApproval,CommittedProgramFillSources
from .program_asset_effects import SpotAssetEffects,derive_spot_asset_effects


def require(condition,code):
    if not condition:raise ValueError(code)


@dataclass(frozen=True,slots=True,kw_only=True)
class AuthenticatedProgramAssetEffects:
    command_id:str
    approval:ProgramFillSourceApproval
    fill:t.Fill
    effects:SpotAssetEffects
    original_owner_strategy_id:str
    original_ownership_epoch:int

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def balance_credit_authorized(self)->Literal[False]:return False


class ProgramAssetPostingReader:
    def __init__(self,tx,*,fill_ledger):
        require(type(fill_ledger) is FillLedger,'ACTUAL_SCOPED_FILL_LEDGER_REQUIRED')
        tx.require_scope(fill_ledger.scope)
        self.tx,self.fills=tx,fill_ledger
        self.sources=CommittedProgramFillSources(tx)

    def read(self,approval_id):
        tx=self.tx;approved=self.sources.read(approval_id)
        # Order/global contradictions cannot disappear merely because this one
        # fill's quantity*price and fee values happen to be self-consistent.
        require(approved.verification_status=='VERIFIED' and not approved.verification_discrepancies,
            'SOURCE_ECONOMICS_RECONCILIATION_REQUIRED')
        actual=self.fills.require_recorded_in_tx(tx,approved.normalized.fill)
        original=read_original_creation(tx,approved.command_id)
        require(original is not None and original.current_state in ('MAY_HAVE_SENT','UNKNOWN'),
            'PROGRAM_ORIGINAL_CONSUMED_CREATION_REQUIRED')
        row=tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s AND sequence=1',
            (scope_id(tx.scope),BUNDLE_PREFIX+original.initial.intent_id)).fetchone()
        require(row is not None,'PROGRAM_ORIGINAL_FINANCIAL_BUNDLE_REQUIRED')
        bundle=decode(ProgramFinancialBundle,row[0])
        require(bundle.intent.scope==tx.scope and bundle.intent.unit==approved.normalized.economics.unit,
            'PROGRAM_APPROVED_ACCOUNTING_UNIT_CHANGED')
        effects=derive_spot_asset_effects(approved.normalized,base_asset=bundle.archive.rules.base_asset,
            quote_asset=bundle.archive.rules.quote_asset)
        return AuthenticatedProgramAssetEffects(command_id=approved.command_id,approval=approved,
            fill=actual,effects=effects,original_owner_strategy_id=original.initial.owner_strategy_id,
            original_ownership_epoch=original.initial.ownership_epoch)
