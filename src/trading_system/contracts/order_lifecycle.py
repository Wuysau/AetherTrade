"""Order-program lifecycle records; origin is never a substitute for authority.

Reducers produce observations/reconciliation requirements, not cash movements,
permissions, fill fabrication, or outbound commands. Binding and ingress evidence
are verified by the existing owner/UoW composition in storage.order_programs.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext, Inexact, Rounded
from typing import Literal
from . import types as t
from .canonical import canonical_hash, domain_payload, utc_timestamp
from .order_program import amount, identifier

Namespace=Literal['ORDINARY','ORDER_LIST','CONDITIONAL_ALGO','EXECUTION_ALGO']

@dataclass(frozen=True,slots=True,kw_only=True)
class AssetBudget:
    asset:str
    amount:str
    def __post_init__(self):identifier(self.asset);amount(self.amount,zero=True)

@dataclass(frozen=True,slots=True,kw_only=True)
class ParentAuthority:
    contract_version:Literal['2026-10-28']
    program_id:str
    program_hash:str
    scope:t.ScopeKey
    namespace:Namespace
    venue:str
    parent_command_id:str
    parent_client_id:str
    parent_venue_id:str
    owner_strategy_id:str
    ownership_epoch:int
    authorization_id:str
    funding_intent_id:str
    reservation_ids:tuple[str,...]
    funding_requirements:tuple[AssetBudget,...]
    base_asset:str
    starting_position_quantity:str
    allowed_child_sides:tuple[Literal['BUY','SELL'],...]
    maximum_quantity:str
    maximum_price:str
    maximum_notional:str
    quote_asset:str
    fee_budgets:tuple[AssetBudget,...]
    management_policy:Literal['MANAGE_UNTIL_FLAT']
    protection_required:bool
    evidence:t.EvidenceRef
    def __post_init__(self):
        for value in (self.program_id,self.parent_command_id,self.parent_client_id,self.parent_venue_id,self.owner_strategy_id,self.authorization_id,self.funding_intent_id,self.quote_asset):identifier(value)
        identifier(self.base_asset)
        from .canonical import decimal_string
        decimal_string(self.starting_position_quantity)
        for value in (self.maximum_quantity,self.maximum_price,self.maximum_notional):amount(value)
        if not self.reservation_ids or len(set(self.reservation_ids))!=len(self.reservation_ids):raise ValueError('Funding references required')
        if not self.funding_requirements or len({b.asset for b in self.funding_requirements})!=len(self.funding_requirements):raise ValueError('Explicit funding requirements required')
        if len({b.asset for b in self.fee_budgets})!=len(self.fee_budgets):raise ValueError('Duplicate fee budget')
        if not self.allowed_child_sides or not set(self.allowed_child_sides)<={'BUY','SELL'}:raise ValueError('Allowed child sides required')
        if len(self.program_hash)!=64 or self.ownership_epoch<1:raise ValueError('Invalid authority binding')

@dataclass(frozen=True,slots=True,kw_only=True)
class ChildFill:
    native_trade_id:str
    quantity:str
    price:str
    fees:tuple[AssetBudget,...]
    occurred_at:str
    def __post_init__(self):
        identifier(self.native_trade_id);amount(self.quantity);amount(self.price);utc_timestamp(self.occurred_at)
        if len({f.asset for f in self.fees})!=len(self.fees):raise ValueError('Duplicate fee asset')

@dataclass(frozen=True,slots=True,kw_only=True)
class ChildObservation:
    child_namespace:Literal['ORDINARY']='ORDINARY'
    contract_version:Literal['2026-10-28']
    origin:Literal['VENUE_GENERATED_CHILD']
    observation_id:str
    scope:t.ScopeKey
    venue:str
    namespace:Namespace
    parent_client_id:str
    parent_venue_id:str
    child_order_id:str
    child_client_id:str|None
    side:Literal['BUY','SELL']
    status:Literal['SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED','UNKNOWN','CANCEL_PENDING','CANCEL_UNKNOWN']
    quantity:str
    cumulative_quantity:str
    fills:tuple[ChildFill,...]
    occurred_at:str
    def __post_init__(self):
        if self.origin!='VENUE_GENERATED_CHILD':raise ValueError('Observation origin mismatch')
        for value in (self.observation_id,self.parent_client_id,self.parent_venue_id,self.child_order_id):identifier(value)
        if self.child_client_id is not None:identifier(self.child_client_id)
        amount(self.quantity);amount(self.cumulative_quantity,zero=True);utc_timestamp(self.occurred_at)

@dataclass(frozen=True,slots=True,kw_only=True)
class ChildState:
    child_order_id:str
    child_client_id:str|None
    side:str
    status:str
    quantity:str
    cumulative_quantity:str
    fills:tuple[ChildFill,...]
    occurred_at:str
    def __post_init__(self):
        identifier(self.child_order_id);utc_timestamp(self.occurred_at)
        if self.side not in ('BUY','SELL') or self.status not in ('SUBMITTED','PARTIALLY_FILLED','FILLED','CANCELED','EXPIRED','REJECTED','UNKNOWN','CANCEL_PENDING','CANCEL_UNKNOWN'):raise ValueError('Invalid stored child state')
        if amount(self.cumulative_quantity,zero=True)>amount(self.quantity):raise ValueError('Invalid stored child quantity')
        if len({f.native_trade_id for f in self.fills})!=len(self.fills):raise ValueError('Duplicate stored fills')

@dataclass(frozen=True,slots=True,kw_only=True)
class ProgramState:
    parent_observed_at:str|None=None
    contract_version:Literal['2026-10-28']='2026-10-28'
    program_id:str
    sequence:int
    status:Literal['ACTIVE','SUBMIT_UNKNOWN','CANCEL_PENDING','CANCEL_UNKNOWN','STOPPED','COMPLETED','RECONCILIATION_REQUIRED']
    unallocated_quantity:str
    children:tuple[ChildState,...]
    observation_ids:tuple[str,...]
    restriction_codes:tuple[str,...]
    protection_quantity:str
    def __post_init__(self):
        identifier(self.program_id);amount(self.unallocated_quantity,zero=True);amount(self.protection_quantity,zero=True)
        if len({c.child_order_id for c in self.children})!=len(self.children) or len(set(self.observation_ids))!=len(self.observation_ids):raise ValueError('Duplicate stored child/event identity')
        if type(self.sequence) is not int or self.sequence<0:raise ValueError('Invalid state sequence')

@dataclass(frozen=True,slots=True,kw_only=True)
class ObservationEvidence:
    observation:ChildObservation
    evidence:t.EvidenceRef
    verified_at:str

@dataclass(frozen=True,slots=True,kw_only=True)
class IngestionResult:
    state:ProgramState|None
    restriction:str|None
    duplicate:bool=False

TERMINAL=frozenset({'FILLED','CANCELED','EXPIRED','REJECTED'})

def restrict(state,code):
    return IngestionResult(state=replace(state,sequence=state.sequence+1,status='RECONCILIATION_REQUIRED',
        restriction_codes=tuple(dict.fromkeys((*state.restriction_codes,code)))),restriction=code)


def apply_child_observation(authority:ParentAuthority,state:ProgramState,event:ChildObservation):
    with localcontext() as arithmetic:
        arithmetic.prec=256
        arithmetic.traps[Inexact]=True
        arithmetic.traps[Rounded]=True
        return _apply_child_observation(authority,state,event)

def _apply_child_observation(authority:ParentAuthority,state:ProgramState,event:ChildObservation):
    """Pure deterministic transition AFTER ingress verification. Never releases funds."""
    if event.origin!='VENUE_GENERATED_CHILD':return restrict(state,'CHILD_ORIGIN_MISMATCH')
    if (event.scope!=authority.scope or event.venue!=authority.venue or event.namespace!=authority.namespace
        or event.parent_client_id!=authority.parent_client_id or event.parent_venue_id!=authority.parent_venue_id
        or state.program_id!=authority.program_id):return restrict(state,'PARENT_LINK_UNVERIFIED')
    if event.observation_id in state.observation_ids:
        # Exact-content dedup is the repository's responsibility; no identity-only acceptance.
        return restrict(state,'EVENT_ID_REUSED')
    if state.status in ('SUBMIT_UNKNOWN','CANCEL_UNKNOWN','RECONCILIATION_REQUIRED'):
        return restrict(state,'PARENT_STATE_UNKNOWN')
    if state.status=='CANCEL_PENDING':return restrict(state,'PARENT_CANCEL_RACE')
    if event.side not in authority.allowed_child_sides:return restrict(state,'CHILD_SIDE_NOT_AUTHORIZED')
    if Decimal(state.unallocated_quantity)+sum((Decimal(c.cumulative_quantity)+(Decimal(c.quantity)-Decimal(c.cumulative_quantity) if c.status not in TERMINAL else 0) for c in state.children),Decimal(0))>Decimal(authority.maximum_quantity):return restrict(state,'PARENT_STATE_BUDGET_MISMATCH')
    old=next((c for c in state.children if c.child_order_id==event.child_order_id),None)
    quantity=amount(event.quantity);cumulative=amount(event.cumulative_quantity,zero=True)
    if cumulative>quantity:return restrict(state,'CHILD_OVERFILL')
    if old:
        if (old.side!=event.side or old.child_client_id!=event.child_client_id or old.quantity!=event.quantity):return restrict(state,'CHILD_IDENTITY_CONFLICT')
        if (old.status=='PARTIALLY_FILLED' and event.status=='SUBMITTED') or (old.status=='CANCEL_PENDING' and event.status in ('SUBMITTED','PARTIALLY_FILLED')):return restrict(state,'ORDER_STATE_REGRESSION')
        if utc_timestamp(event.occurred_at)<utc_timestamp(old.occurred_at):return restrict(state,'OUT_OF_ORDER_OBSERVATION')
        if cumulative<Decimal(old.cumulative_quantity):return restrict(state,'CUMULATIVE_REGRESSION')
        if old.status in TERMINAL and (event.status!=old.status or cumulative!=Decimal(old.cumulative_quantity)):
            return restrict(state,'CANCEL_FILL_RACE' if old.status in ('CANCELED','EXPIRED') else 'TERMINAL_STATE_CONFLICT')
    elif state.status in ('STOPPED','COMPLETED'):
        return restrict(state,'CHILD_AFTER_PARENT_STOP')
    # Only parent authority authorizes total possible native generation. A cancel ACK
    # never recycles this allocation and never releases the shared reservation.
    unallocated=Decimal(state.unallocated_quantity)-(quantity if old is None else Decimal(0))
    if unallocated<0:return restrict(state,'PARENT_QUANTITY_EXCEEDED')
    fills={f.native_trade_id:f for f in old.fills} if old else {}
    other_trades={f.native_trade_id for c in state.children if c.child_order_id!=event.child_order_id for f in c.fills}
    for fill in event.fills:
        if fill.native_trade_id in other_trades:return restrict(state,'TRADE_IDENTITY_CONFLICT')
        if fill.native_trade_id in fills and fills[fill.native_trade_id]!=fill:return restrict(state,'TRADE_CONTENT_CONFLICT')
        if utc_timestamp(fill.occurred_at)>utc_timestamp(event.occurred_at):return restrict(state,'FUTURE_FILL')
        fills[fill.native_trade_id]=fill
    if sum((Decimal(f.quantity) for f in fills.values()),Decimal(0))!=cumulative:return restrict(state,'FILL_DETAILS_MISSING')
    if event.status=='SUBMITTED' and cumulative!=0:return restrict(state,'ORDER_STATE_REGRESSION')
    if event.status=='PARTIALLY_FILLED' and not 0<cumulative<quantity:return restrict(state,'TERMINAL_QUANTITY_MISMATCH')
    if event.status=='FILLED' and cumulative!=quantity:return restrict(state,'TERMINAL_QUANTITY_MISMATCH')
    child=ChildState(child_order_id=event.child_order_id,child_client_id=event.child_client_id,side=event.side,status=event.status,quantity=event.quantity,cumulative_quantity=event.cumulative_quantity,fills=tuple(fills.values()),occurred_at=event.occurred_at)
    children=tuple(c for c in state.children if c.child_order_id!=event.child_order_id)+(child,)
    filled_notional=sum((Decimal(f.quantity)*Decimal(f.price) for c in children for f in c.fills),Decimal(0))
    possible=unallocated+sum((Decimal(c.quantity)-Decimal(c.cumulative_quantity) for c in children if c.status not in TERMINAL),Decimal(0))
    fees={}
    for c in children:
        for f in c.fills:
            for fee in f.fees:fees[fee.asset]=fees.get(fee.asset,Decimal(0))+Decimal(fee.amount)
    budgets={b.asset:Decimal(b.amount) for b in authority.fee_budgets}
    if any(asset not in budgets or value>budgets[asset] for asset,value in fees.items()):return restrict(state,'FEE_BUDGET_EXCEEDED')
    if filled_notional+possible*Decimal(authority.maximum_price)+fees.get(authority.quote_asset,Decimal(0))>Decimal(authority.maximum_notional):
        return restrict(state,'PARENT_NOTIONAL_EXCEEDED')
    net=Decimal(authority.starting_position_quantity)+sum(((1 if c.side=='BUY' else -1)*Decimal(c.cumulative_quantity) for c in children),Decimal(0))-fees.get(authority.base_asset,Decimal(0))
    if authority.scope.product in (t.Product.SPOT,t.Product.US_EQUITY) and net<0:return restrict(state,'CASH_NEGATIVE_EXPOSURE')
    protected=abs(net)
    result=replace(state,sequence=state.sequence+1,unallocated_quantity=format(unallocated,'f'),children=children,
        observation_ids=(*state.observation_ids,event.observation_id),protection_quantity=format(protected,'f'))
    if event.status in ('UNKNOWN','CANCEL_UNKNOWN'):
        return IngestionResult(state=replace(result,status='RECONCILIATION_REQUIRED',restriction_codes=(*result.restriction_codes,'CHILD_STATE_UNKNOWN')),restriction='CHILD_STATE_UNKNOWN')
    return IngestionResult(state=result,restriction=None)

@dataclass(frozen=True,slots=True,kw_only=True)
class ApplicationWrite:
    venue_client_id:str
    service_namespace:Namespace
    owner_strategy_id:str
    contract_version:Literal['2026-10-28']
    command_id:str
    program_id:str
    scope:t.ScopeKey
    origin:Literal['APPLICATION_ACTION','APPLICATION_GENERATED_CHILD']
    operation:Literal['SUBMIT','CANCEL','AMEND','SUBMIT_PROGRAM','CANCEL_PROGRAM','AMEND_PROGRAM','SET_PROTECTION']
    parent_command_id:str|None
    target_venue_id:str|None
    canonical_program_hash:str
    ownership_epoch:int
    execution_epoch:int
    policy_revision:int
    durable_record_ref:str
    state:Literal['DURABLE_READY','MAY_HAVE_SENT','UNKNOWN','VENUE_CONFIRMED','VENUE_REJECTED','ABORTED_UNSENT']
    created_at:str
    def __post_init__(self):
        if self.origin not in ('APPLICATION_ACTION','APPLICATION_GENERATED_CHILD'):raise ValueError('Venue observations cannot become local writes')
        if self.origin=='APPLICATION_GENERATED_CHILD' and not self.parent_command_id:raise ValueError('Application child parent required')
        if self.operation in ('CANCEL','AMEND','CANCEL_PROGRAM','AMEND_PROGRAM') and not self.target_venue_id:raise ValueError('Owned target required')
        for value in (self.owner_strategy_id,self.command_id,self.program_id,self.durable_record_ref):identifier(value)
        utc_timestamp(self.created_at)


def require_write_authority(command:ApplicationWrite,permit:t.DurablePermit,lease:t.OwnerLease,*,now,deadline_monotonic_ns):
    """Necessary guard only. OP-C dispatcher must reload these trusted records."""
    if command.scope.environment not in (t.Environment.PAPER,t.Environment.RESEARCH):raise ValueError('ENVIRONMENT_DISABLED')
    if (command.state!='MAY_HAVE_SENT' or permit.command_id!=command.command_id or permit.scope!=command.scope
        or permit.payload_hash!=canonical_hash(domain_payload(command)) or permit.durable_record_ref!=command.durable_record_ref
        or permit.ownership_epoch!=command.ownership_epoch or permit.execution_epoch!=command.execution_epoch
        or permit.policy_revision!=command.policy_revision or utc_timestamp(now)>=utc_timestamp(permit.expires_at)
        or deadline_monotonic_ns>=permit.deadline_monotonic_ns):raise ValueError('INDEPENDENT_DURABLE_PERMIT_REQUIRED')
    shard=lease.shard
    if (shard.environment!=command.scope.environment or shard.venue_account_id!=command.scope.venue_account_id
        or shard.product!=command.scope.product or shard.execution_epoch!=command.execution_epoch or utc_timestamp(now)>=utc_timestamp(lease.expires_at)
        or deadline_monotonic_ns>=lease.deadline_monotonic_ns):raise ValueError('EXECUTION_OWNER_REQUIRED')


@dataclass(frozen=True,slots=True,kw_only=True)
class ParentObservation:
    contract_version:Literal['2026-10-28']
    observation_id:str
    scope:t.ScopeKey
    venue:str
    namespace:Namespace
    parent_client_id:str
    parent_venue_id:str
    status:Literal['ACTIVE','SUBMIT_UNKNOWN','CANCEL_PENDING','CANCEL_UNKNOWN','STOPPED','COMPLETED']
    unallocated_quantity:str
    cumulative_quantity:str
    occurred_at:str
    def __post_init__(self):
        for x in (self.observation_id,self.parent_client_id,self.parent_venue_id):identifier(x)
        amount(self.unallocated_quantity,zero=True);amount(self.cumulative_quantity,zero=True);utc_timestamp(self.occurred_at)


PARENT_TRANSITIONS={
 'ACTIVE':{'ACTIVE','CANCEL_PENDING','STOPPED','COMPLETED'},
 'CANCEL_PENDING':{'CANCEL_PENDING','STOPPED','COMPLETED'},
 'STOPPED':{'STOPPED','COMPLETED'},'COMPLETED':{'COMPLETED'},
}

def apply_parent_observation(authority,state,event):
    """An original-ID query updates facts, never releases a reservation or risk lock."""
    if (event.scope!=authority.scope or event.namespace!=authority.namespace or event.venue!=authority.venue
        or event.parent_client_id!=authority.parent_client_id or event.parent_venue_id!=authority.parent_venue_id):return restrict(state,'PARENT_LINK_UNVERIFIED')
    if state.status=='RECONCILIATION_REQUIRED':return restrict(state,'PARENT_REVIEW_REQUIRED')
    if event.status in ('SUBMIT_UNKNOWN','CANCEL_UNKNOWN'):return restrict(state,'PARENT_STATE_UNKNOWN')
    if event.status not in PARENT_TRANSITIONS.get(state.status,set()):return restrict(state,'PARENT_STATE_REGRESSION')
    if state.parent_observed_at is not None and utc_timestamp(event.occurred_at)<utc_timestamp(state.parent_observed_at):return restrict(state,'OUT_OF_ORDER_PARENT_OBSERVATION')
    with localcontext() as arithmetic:
        arithmetic.prec=256;arithmetic.traps[Inexact]=True;arithmetic.traps[Rounded]=True
        filled=sum((Decimal(c.cumulative_quantity) for c in state.children),Decimal(0))
        remaining=sum((Decimal(c.quantity)-Decimal(c.cumulative_quantity) for c in state.children if c.status not in TERMINAL),Decimal(0))
        unallocated=Decimal(event.unallocated_quantity)
        if filled!=Decimal(event.cumulative_quantity):return restrict(state,'CHILD_EVIDENCE_INCOMPLETE')
        if filled+remaining+unallocated>Decimal(authority.maximum_quantity):return restrict(state,'PARENT_QUANTITY_EXCEEDED')
        fees={}
        for c in state.children:
            for f in c.fills:
                for fee in f.fees:fees[fee.asset]=fees.get(fee.asset,Decimal(0))+Decimal(fee.amount)
        caps={b.asset:Decimal(b.amount) for b in authority.fee_budgets}
        if any(asset not in caps or value>caps[asset] for asset,value in fees.items()):return restrict(state,'FEE_BUDGET_EXCEEDED')
        spent=sum((Decimal(f.price)*Decimal(f.quantity) for c in state.children for f in c.fills),Decimal(0))
        if spent+(remaining+unallocated)*Decimal(authority.maximum_price)+fees.get(authority.quote_asset,Decimal(0))>Decimal(authority.maximum_notional):return restrict(state,'PARENT_NOTIONAL_EXCEEDED')
        if event.status in ('STOPPED','COMPLETED') and unallocated!=0:return restrict(state,'PARENT_STOP_UNVERIFIED')
        if event.status=='COMPLETED' and any(c.status not in TERMINAL for c in state.children):return restrict(state,'CHILDREN_STILL_OPEN')
    return IngestionResult(state=replace(state,sequence=state.sequence+1,status=event.status,
        unallocated_quantity=event.unallocated_quantity,parent_observed_at=event.occurred_at),restriction=None)
