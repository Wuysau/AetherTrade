"""Explicit band-source compilation through the existing field/routing compiler.

No scalar target, request resizing or financial authority is created. This
versioned compilation still requires interval allocation and actual financial
assessment; its native request amount is independent of the position-value goal.
"""
from dataclasses import dataclass,fields
from decimal import Decimal
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.quote_position_v1 import QuotePositionProposalV1
from trading_system.contracts.program_financial import require
from .program_compilation import ProgramCompilation,compile_order_program


@dataclass(frozen=True,slots=True,kw_only=True)
class QuoteProgramCompilationV1(ProgramCompilation):
    original_proposal:QuotePositionProposalV1
    contract_version:Literal['2026-10-30']
    record_kind:Literal['QUOTE_PROGRAM_COMPILATION_V1']


def compile_quote_program(proposal,rules,capabilities,**kwargs):
    require(type(proposal) is QuotePositionProposalV1,'POSITION_VALUE_SOURCE_REQUIRED')
    require(rules.scope.product in (t.Product.SPOT,t.Product.US_EQUITY),
        'NATIVE_QUOTE_PRODUCT_UNSUPPORTED')
    require(proposal.goal.maximum_quote.currency==rules.quote_asset,'POSITION_VALUE_PRODUCT_CURRENCY_MISMATCH')
    shared=compile_order_program(proposal,rules,capabilities,**kwargs)
    require(shared.original_program.template=='ATOMIC' and len(shared.nodes)==len(shared.requests)==1
        and shared.nodes[0].quantity.kind=='QUOTE_NOTIONAL'
        and shared.nodes[0].original.order.price.kind=='MARKET',
        'NATIVE_QUOTE_ATOMIC_MARKET_REQUIRED')
    require(proposal.goal.valuation_basis.notional_multiplier==Decimal(shared.nodes[0].quantity_multiplier),
        'POSITION_VALUE_PRODUCT_MULTIPLIER_MISMATCH')
    return QuoteProgramCompilationV1(**{f.name:getattr(shared,f.name) for f in fields(ProgramCompilation)},
        contract_version='2026-10-30',record_kind='QUOTE_PROGRAM_COMPILATION_V1')
