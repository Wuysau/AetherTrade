"""Declared local schedule quantity model, not a scheduler or trading grant.

The host must authenticate activation time, cadence, complete commitments and
cumulative native volume since activation. This pure preview cannot establish
those facts or reuse an expired permit. Urgency/placement policy remains separate.
"""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.order_program import amount
from trading_system.contracts.order_lifecycle_v3 import exact_time
from trading_system.intents.program_compilation import ProgramCompilation
from trading_system.risk.policy_checks import exact_risk_arithmetic
from .creation_requests import require


@dataclass(frozen=True,slots=True,kw_only=True)
class LocalSchedulePreview:
    scheduled_quantity:str
    available_quantity:str
    unallocated_quantity:str
    quantity_unit:str
    window_closed:bool

    @property
    def execution_authorized(self)->Literal[False]:return False
    @property
    def funds_release_authorized(self)->Literal[False]:return False


def _elapsed(start,end):
    a,af=exact_time(start);b,bf=exact_time(end)
    delta=utc_timestamp(b+'Z')-utc_timestamp(a+'Z')
    return Fraction(delta.days*86400+delta.seconds)+Fraction(bf)-Fraction(af)


def _text(value):
    with exact_risk_arithmetic():return format(Decimal(value.numerator)/Decimal(value.denominator),'f')


def preview_local_schedule(plan,*,activated_at,now,interval_seconds,committed_quantity,
                           quantity_step,observed_volume=None,volume_unit=None,participation_bps=None):
    require(type(plan) is ProgramCompilation and len(plan.nodes)==1
        and plan.original_program.template in ('TWAP','VP')
        and plan.original_program.execution_mode in ('LOCAL_SYNTHETIC','PAPER_MODEL'),
        'LOCAL_SCHEDULE_PARENT_REQUIRED')
    algorithm=plan.original_program.algorithm;duration=algorithm.duration_seconds
    require(type(duration) is int and duration>0,'LOCAL_SCHEDULE_DURATION_REQUIRED')
    require(type(interval_seconds) is int and 1<=interval_seconds<=duration,'LOCAL_SCHEDULE_CADENCE_REQUIRED')
    elapsed=_elapsed(activated_at,now)
    require(elapsed>=0,'LOCAL_SCHEDULE_CLOCK_BEFORE_ACTIVATION')
    node=plan.nodes[0]
    require(node.quantity.kind=='BASE_QUANTITY','LOCAL_SCHEDULE_BASE_QUANTITY_REQUIRED')
    total=Fraction(amount(node.quantity.value));step=Fraction(amount(quantity_step))
    used=Fraction(amount(committed_quantity,zero=True))
    require(used<=total and (total/step).denominator==1 and (used/step).denominator==1,
        'LOCAL_SCHEDULE_GRID_OR_COMMITMENT_INVALID')
    if algorithm.kind=='TWAP':
        require(observed_volume is None and volume_unit is None,'LOCAL_SCHEDULE_UNEXPECTED_VOLUME')
        slots=(duration+interval_seconds-1)//interval_seconds
        released=min(slots,int(elapsed//interval_seconds)+1)
        upper=total*released/slots
    else:
        require(algorithm.kind=='VP' and observed_volume is not None and volume_unit==node.quantity_unit,
            'LOCAL_SCHEDULE_VOLUME_REQUIRED')
        participation=Fraction(amount(participation_bps if participation_bps is not None
                                     else algorithm.participation_bps))
        require(participation<=10000,'LOCAL_SCHEDULE_PARTICIPATION_INVALID')
        upper=min(total,Fraction(amount(observed_volume,zero=True))*participation/10000)
    scheduled=(upper//step)*step
    closed=elapsed>=duration
    available=Fraction(0) if closed else max(Fraction(0),scheduled-used)
    return LocalSchedulePreview(scheduled_quantity=_text(scheduled),available_quantity=_text(available),
        unallocated_quantity=_text(total-used),quantity_unit=node.quantity_unit,window_closed=closed)
