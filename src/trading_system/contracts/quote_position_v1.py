"""Explicit new position-value-band source; old scalar sources are unchanged.

The desired lower bound makes no fill/completion claim. Authentic allocation and
risk facts must still consider current inventory, working and UNKNOWN quantities,
zero/partial fills, fees, and the full immutable native quote request budget.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from . import types as t
from .canonical import utc_timestamp
from .order_program import OrderProgram,identifier
from .order_program_v3 import OrderProgramV3
from .program_financial import require
from .wire import DecisionProposalExitPlan


def _decimal(value,*,positive=False):
    require(type(value) is Decimal and value.is_finite()
        and (value>0 if positive else value>=0),'POSITION_VALUE_NUMBER_INVALID')


@dataclass(frozen=True,slots=True,kw_only=True)
class PositionValueBasisV1:
    kind:Literal['DECLARED_REFERENCE_PRICE']
    reference_price:t.Price
    notional_multiplier:Decimal

    def __post_init__(self):
        require(self.kind=='DECLARED_REFERENCE_PRICE' and type(self.reference_price) is t.Price,
            'POSITION_VALUE_DECLARED_BASIS_REQUIRED')
        _decimal(self.reference_price.value,positive=True)
        _decimal(self.notional_multiplier,positive=True)
        identifier(self.reference_price.currency)


@dataclass(frozen=True,slots=True,kw_only=True)
class AbsolutePositionValueBandV1:
    kind:Literal['ABSOLUTE_POSITION_VALUE_BAND']
    side:Literal['LONG','SHORT','FLAT']
    minimum_quote:t.Money
    maximum_quote:t.Money
    valuation_basis:PositionValueBasisV1

    def __post_init__(self):
        require(self.kind=='ABSOLUTE_POSITION_VALUE_BAND'
            and self.side in ('LONG','SHORT','FLAT'),'POSITION_VALUE_BAND_REQUIRED')
        require(type(self.minimum_quote) is t.Money and type(self.maximum_quote) is t.Money
            and type(self.valuation_basis) is PositionValueBasisV1,'POSITION_VALUE_TYPED_BAND_REQUIRED')
        _decimal(self.minimum_quote.amount);_decimal(self.maximum_quote.amount)
        require(self.minimum_quote.currency==self.maximum_quote.currency
            ==self.valuation_basis.reference_price.currency,'POSITION_VALUE_CURRENCY_MISMATCH')
        require(self.minimum_quote.amount<=self.maximum_quote.amount
            and ((self.side=='FLAT')==(self.maximum_quote.amount==0)),
            'POSITION_VALUE_BAND_SIDE_OR_RANGE_INVALID')


@dataclass(frozen=True,slots=True,kw_only=True)
class QuotePositionProposalV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['POSITION_VALUE_BAND_PROPOSAL_V1']
    proposal_id:str
    instrument_ref:str
    action:Literal['SET_TARGET']
    goal:AbsolutePositionValueBandV1
    requested_leverage:int
    program:OrderProgram|OrderProgramV3
    protection_policy:Literal['NATIVE_REQUIRED','HOSTED_ALLOWED_CASH_ONLY']
    exit_plan:DecisionProposalExitPlan
    valid_until:str
    evidence_refs:tuple[str,...]
    summary:str

    def __post_init__(self):
        require(self.contract_version=='2026-10-30' and self.record_kind=='POSITION_VALUE_BAND_PROPOSAL_V1'
            and self.action=='SET_TARGET','POSITION_VALUE_SOURCE_VERSION_REQUIRED')
        identifier(self.proposal_id);identifier(self.instrument_ref)
        require(type(self.goal) is AbsolutePositionValueBandV1,'POSITION_VALUE_BAND_REQUIRED')
        require(type(self.requested_leverage) is int and self.requested_leverage>=1
            and type(self.exit_plan) is DecisionProposalExitPlan and self.exit_plan.maximum_holding_seconds>=1,
            'POSITION_VALUE_EXPLICIT_LIMITS_REQUIRED')
        require(type(self.program) in (OrderProgram,OrderProgramV3)
            and self.program.instrument_ref==self.instrument_ref
            and utc_timestamp(self.program.submit_valid_until)<=utc_timestamp(self.valid_until),
            'POSITION_VALUE_PROGRAM_BINDING_REQUIRED')
        require(self.protection_policy in ('NATIVE_REQUIRED','HOSTED_ALLOWED_CASH_ONLY')
            and type(self.evidence_refs) is tuple and bool(self.evidence_refs)
            and all(type(ref) is str and bool(ref) for ref in self.evidence_refs),
            'POSITION_VALUE_PROTECTION_AND_EVIDENCE_REQUIRED')

    @property
    def execution_authorized(self)->Literal[False]:return False
