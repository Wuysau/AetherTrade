"""Additive native-algorithm revision; v2 VP participation is never discarded."""
from dataclasses import dataclass
from typing import Literal
from .order_program import OrderProgram,OrderProposal

@dataclass(frozen=True,slots=True,kw_only=True)
class NativeAlgorithmSpec:
    kind:Literal['TWAP','VP']
    duration_seconds:int|None
    urgency:Literal['LOW','MEDIUM','HIGH']|None
    def __post_init__(self):
        if self.duration_seconds is not None and (type(self.duration_seconds) is not int or not 1<=self.duration_seconds<=86400):raise ValueError('Invalid algorithm duration')
        if self.kind=='TWAP' and (self.duration_seconds is None or self.urgency is not None):raise ValueError('TWAP requires duration, no VP urgency')
        if self.kind=='VP' and self.urgency is None:raise ValueError('VP urgency required')

@dataclass(frozen=True,slots=True,kw_only=True)
class OrderProgramV3(OrderProgram):
    contract_version:Literal['2026-10-29']
    algorithm:NativeAlgorithmSpec|None

@dataclass(frozen=True,slots=True,kw_only=True)
class OrderProposalV3(OrderProposal):
    contract_version:Literal['2026-10-29']
    program:OrderProgramV3|None
