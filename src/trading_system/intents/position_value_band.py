"""Primary SET_TARGET band containment, separate from risk/authority approval.

Every existing leaf is independently possible; opposite directions cannot fund
one another. A bounded reduction may retain pre-existing above-goal inventory in
NO-FILL/partial scenarios. That does not authorize any new increasing request.
Independent protective/triggered/emergency exits use their separately authorized
contracts; callers cannot label a primary request as protection to skip its goal.
"""
from dataclasses import dataclass
from decimal import Decimal,DecimalException
from typing import Literal

from trading_system.contracts import types as t
from trading_system.contracts.quote_position_v1 import AbsolutePositionValueBandV1
from trading_system.contracts.program_financial import require
from trading_system.risk.policy_checks import exact_risk_arithmetic
from .target_compiler import TargetScopeState


@dataclass(frozen=True,slots=True,kw_only=True)
class PositionValueBandAssessmentV1:
    contract_version:Literal['2026-10-30']
    record_kind:Literal['POSITION_VALUE_BAND_ASSESSMENT_V1']
    scope:t.ScopeKey
    goal:AbsolutePositionValueBandV1
    scope_state:TargetScopeState
    request_side:Literal['BUY','SELL']
    delta_minimum:t.Quantity
    delta_maximum:t.Quantity
    baseline_minimum:t.Quantity
    baseline_maximum:t.Quantity
    projected_minimum:t.Quantity
    projected_maximum:t.Quantity
    current_value:t.Money
    maximum_projected_value:t.Money
    classification:Literal['NONE','INCREASE','REDUCE']
    unmet_goal_possible:bool
    blockers:tuple[str,...]

    @property
    def execution_authorized(self)->Literal[False]:return False


def assess_position_value_band(goal,state,*,scope,request_side,delta_minimum,delta_maximum):
    require(type(goal) is AbsolutePositionValueBandV1 and type(state) is TargetScopeState,
        'POSITION_BAND_AND_SCOPE_REQUIRED')
    require(request_side in ('BUY','SELL'),'POSITION_BAND_REQUEST_SIDE_REQUIRED')
    unit={t.Product.SPOT:'BASE',t.Product.USD_M_PERP:'CONTRACT',t.Product.US_EQUITY:'SHARE'}[scope.product]
    values=(state.actual,state.working,delta_minimum,delta_maximum)
    require(all(type(q) is t.Quantity and q.unit==unit and type(q.value) is Decimal and q.value.is_finite()
        for q in values),'POSITION_BAND_QUANTITY_UNIT_MISMATCH')
    require(delta_minimum.value<=0<=delta_maximum.value,'POSITION_BAND_UNCONDITIONAL_ZERO_REQUIRED')
    require(request_side!='SELL' or delta_maximum.value==0,'POSITION_BAND_SELL_DELTA_INVALID')
    require(scope.product!=t.Product.USD_M_PERP or request_side!='BUY' or delta_minimum.value==0,
        'POSITION_BAND_CONTRACT_BUY_DELTA_INVALID')
    require(type(state.obligations) is tuple and len({(o.source_kind,o.source_id) for o in state.obligations})
        ==len(state.obligations),'POSITION_BAND_OBLIGATION_DUPLICATED')
    require(all(o.signed_quantity.unit==unit and type(o.signed_quantity.value) is Decimal
        and o.signed_quantity.value.is_finite() for o in state.obligations),
        'POSITION_BAND_OBLIGATION_UNIT_MISMATCH')
    require(tuple(o.signed_quantity.value for o in state.obligations)==state.signed_orders,
        'POSITION_BAND_OBLIGATION_COVERAGE_REQUIRED')
    try:
        with exact_risk_arithmetic():
            require(sum(state.signed_orders,Decimal(0))==state.working.value,
                'POSITION_BAND_OBLIGATION_COVERAGE_REQUIRED')
            return _assess(goal,state,scope,request_side,delta_minimum,delta_maximum,unit)
    except DecimalException as error:
        raise ValueError('POSITION_BAND_ARITHMETIC_UNPROVEN') from error


def _assess(goal,state,scope,side,low,high,unit):
    actual=state.actual.value
    negative=sum((min(q.value,Decimal(0)) for q in (o.signed_quantity for o in state.obligations)),Decimal(0))
    positive=sum((max(q.value,Decimal(0)) for q in (o.signed_quantity for o in state.obligations)),Decimal(0))
    baseline_low,baseline_high=actual+negative,actual+positive
    minimum,maximum=baseline_low+low.value,baseline_high+high.value
    reducing=actual>0 and side=='SELL' and high.value==0 or actual<0 and side=='BUY' and low.value==0
    classification='NONE' if low.value==high.value==0 else 'REDUCE' if reducing else 'INCREASE'
    blockers=[]
    if (scope.product!=t.Product.USD_M_PERP and minimum<0 or actual>0 and minimum<0
            or actual<0 and maximum>0 or actual>0 and goal.side=='SHORT' or actual<0 and goal.side=='LONG'):
        blockers.append('REPLAN:POSITION_REVERSAL_OR_OVERSELL')
    reference=goal.valuation_basis.reference_price
    factor=reference.value*goal.valuation_basis.notional_multiplier
    value=lambda quantity:t.Money(currency=reference.currency,amount=abs(quantity)*factor)
    upper=max(abs(minimum),abs(maximum));baseline_upper=max(abs(baseline_low),abs(baseline_high))
    magnitude_lower=Decimal(0) if minimum<=0<=maximum else min(abs(minimum),abs(maximum))
    if classification=='REDUCE':
        if upper>baseline_upper:blockers.append('REPLAN:REDUCTION_EXPOSURE_INCREASED')
        if value(magnitude_lower).amount<goal.minimum_quote.amount:
            blockers.append('REPLAN:PRIMARY_REDUCTION_BELOW_BAND')
    elif value(upper).amount>goal.maximum_quote.amount:
        blockers.append('REPLAN:POSITION_BAND_UPPER')
    unmet=(value(magnitude_lower).amount<goal.minimum_quote.amount or value(upper).amount>goal.maximum_quote.amount)
    quantity=lambda v:t.Quantity(unit=unit,value=v)
    return PositionValueBandAssessmentV1(contract_version='2026-10-30',record_kind='POSITION_VALUE_BAND_ASSESSMENT_V1',
        scope=scope,goal=goal,scope_state=state,request_side=side,delta_minimum=low,delta_maximum=high,
        baseline_minimum=quantity(baseline_low),baseline_maximum=quantity(baseline_high),
        projected_minimum=quantity(minimum),projected_maximum=quantity(maximum),current_value=value(actual),
        maximum_projected_value=value(upper),classification=classification,unmet_goal_possible=unmet,blockers=tuple(blockers))
