"""Versioned order semantics. This module never creates authority or sends orders.

Old 2026-10-20 wire shapes remain untouched. A program is a proposal to compile;
venue/account/product identity is supplied separately by trusted composition.
"""
from __future__ import annotations
from dataclasses import dataclass, fields, is_dataclass
from typing import Literal, get_type_hints, get_origin, get_args, Union
from types import UnionType
from enum import Enum
from decimal import Decimal
import json
from jsonschema import Draft202012Validator, FormatChecker
from .canonical import canonical_hash, canonical_json, domain_payload, decimal_string, utc_timestamp
from .resources import contract_path
from .wire import DecisionProposalExitPlan

VERSION='2026-10-28'

def identifier(value):
    if not isinstance(value,str) or not 1<=len(value)<=128 or any(ord(c)<33 for c in value):raise ValueError('Invalid identity')

def amount(value, *, zero=False):
    if not isinstance(value,str) or len(value)>64:raise ValueError('Decimal string required')
    result=decimal_string(value)
    if result<0 or (not zero and result==0):raise ValueError('Invalid amount')
    return result

def variant(kind, options, values):
    if kind not in options:raise ValueError('Unknown variant')
    required,optional=options[kind]
    if any(values[k] is None for k in required) or any(v is not None and k not in required|optional for k,v in values.items()):
        raise ValueError('Variant fields conflict')

@dataclass(frozen=True,slots=True,kw_only=True)
class QuantitySpec:
    kind:Literal['BASE_QUANTITY','QUOTE_NOTIONAL','CLOSE_ALL','DERIVED_RECEIPT']
    value:str|None
    source_node:str|None
    def __post_init__(self):
        variant(self.kind,{'BASE_QUANTITY':({'value'},set()),'QUOTE_NOTIONAL':({'value'},set()),'CLOSE_ALL':(set(),set()),'DERIVED_RECEIPT':({'source_node'},set())},dict(value=self.value,source_node=self.source_node))
        if self.value is not None:amount(self.value)
        if self.source_node is not None:identifier(self.source_node)

@dataclass(frozen=True,slots=True,kw_only=True)
class PriceSpec:
    kind:Literal['MARKET','FIXED_LIMIT','PEG','PRICE_MATCH']
    value:str|None
    peg_type:Literal['PRIMARY_PEG','MARKET_PEG']|None
    offset_levels:int|None
    price_match:Literal['OPPONENT','OPPONENT_5','OPPONENT_10','OPPONENT_20','QUEUE','QUEUE_5','QUEUE_10','QUEUE_20']|None
    def __post_init__(self):
        variant(self.kind,{'MARKET':(set(),set()),'FIXED_LIMIT':({'value'},set()),'PEG':({'peg_type','offset_levels'},set()),'PRICE_MATCH':({'price_match'},set())},dict(value=self.value,peg_type=self.peg_type,offset_levels=self.offset_levels,price_match=self.price_match))
        if self.value is not None:amount(self.value)
        if self.offset_levels is not None and (type(self.offset_levels) is not int or not 0<=self.offset_levels<=100):raise ValueError('Invalid peg offset')

@dataclass(frozen=True,slots=True,kw_only=True)
class TriggerSpec:
    kind:Literal['NONE','STATIC','TRAILING']
    purpose:Literal['STOP_LOSS','TAKE_PROFIT']|None
    source:Literal['LAST','CONTRACT_PRICE','MARK_PRICE','BID_ASK']|None
    price:str|None
    activation_price:str|None
    offset:str|None
    offset_unit:Literal['BIPS','PERCENT','PRICE']|None
    price_protect:bool|None
    def __post_init__(self):
        values={f.name:getattr(self,f.name) for f in fields(self) if f.name!='kind'}
        variant(self.kind,{'NONE':(set(),set()),'STATIC':({'purpose','source','price','price_protect'},set()),'TRAILING':({'purpose','source','offset','offset_unit','price_protect'},{'activation_price'})},values)
        for k in ('price','activation_price','offset'):
            if values[k] is not None:amount(values[k])

@dataclass(frozen=True,slots=True,kw_only=True)
class TimeInForceSpec:
    kind:Literal['NONE','GTC','IOC','FOK','GTX','GTD','DAY','RPI']
    expires_at:str|None
    def __post_init__(self):
        if self.kind=='GTD' and self.expires_at is None:raise ValueError('GTD expiry required')
        if self.kind!='GTD' and self.expires_at is not None:raise ValueError('Unexpected venue expiry')
        if self.expires_at is not None:utc_timestamp(self.expires_at)

@dataclass(frozen=True,slots=True,kw_only=True)
class AtomicOrderSpec:
    side:Literal['BUY','SELL']
    quantity:QuantitySpec
    price:PriceSpec
    trigger:TriggerSpec
    tif:TimeInForceSpec
    post_only:bool
    iceberg_quantity:str|None
    stp:Literal['NONE','EXPIRE_TAKER','EXPIRE_MAKER','EXPIRE_BOTH','DECREMENT','TRANSFER']
    routing:Literal['STANDARD','SOR']
    position_side:Literal['BOTH','LONG','SHORT']
    reduce_only:bool
    trading_session:Literal['RTH','EXTENDED','24H']|None
    tokenize:Literal[False]|None
    def __post_init__(self):
        if self.iceberg_quantity is not None:amount(self.iceberg_quantity)

@dataclass(frozen=True,slots=True,kw_only=True)
class OrderNode:
    node_id:str
    role:Literal['ENTRY','WORKING','PENDING','LEG','ALGORITHM_PARENT']
    child_origin:Literal['APPLICATION_GENERATED_CHILD','VENUE_GENERATED_CHILD']
    order:AtomicOrderSpec
    def __post_init__(self):identifier(self.node_id)

@dataclass(frozen=True,slots=True,kw_only=True)
class ActivationLink:
    source:str
    target:str
    condition:Literal['FULL_FILL','NET_RECEIPT']

@dataclass(frozen=True,slots=True,kw_only=True)
class AlgorithmSpec:
    kind:Literal['TWAP','VP']
    duration_seconds:int
    participation_bps:str|None
    urgency:Literal['LOW','MEDIUM','HIGH']|None
    def __post_init__(self):
        if type(self.duration_seconds) is not int or not 1<=self.duration_seconds<=86400:raise ValueError('Invalid algorithm duration')
        if self.kind=='TWAP' and (self.participation_bps is not None or self.urgency is not None):raise ValueError('TWAP fields conflict')
        if self.kind=='VP' and self.participation_bps is None:raise ValueError('VP participation required')
        if self.participation_bps is not None and amount(self.participation_bps)>10000:raise ValueError('Invalid participation')

@dataclass(frozen=True,slots=True,kw_only=True)
class OrderProgram:
    contract_version:Literal['2026-10-28']
    program_id:str
    instrument_ref:str
    template:Literal['ATOMIC','OCO','OTO','OTOCO','OPO','OPOCO','TWAP','VP','BATCH']
    execution_mode:Literal['VENUE_NATIVE','LOCAL_SYNTHETIC','PAPER_MODEL']
    nodes:tuple[OrderNode,...]
    links:tuple[ActivationLink,...]
    exclusive_groups:tuple[tuple[str,...],...]
    algorithm:AlgorithmSpec|None
    submit_valid_until:str
    management_policy:Literal['MANAGE_UNTIL_FLAT']
    capability_snapshot_ref:str
    rule_snapshot_ref:str
    def __post_init__(self):
        for value in (self.program_id,self.instrument_ref,self.capability_snapshot_ref,self.rule_snapshot_ref):identifier(value)
        utc_timestamp(self.submit_valid_until)
        ids={n.node_id for n in self.nodes}
        if not 1<=len(self.nodes)<=32 or len(ids)!=len(self.nodes):raise ValueError('Invalid program nodes')
        if len(self.links)>64 or len(self.exclusive_groups)>16:raise ValueError('Program too large')
        if self.template=='ATOMIC' and (len(ids)!=1 or self.links or self.exclusive_groups):raise ValueError('Atomic template mismatch')
        if (self.template in ('TWAP','VP')) != (self.algorithm is not None):raise ValueError('Algorithm/template mismatch')
        if self.algorithm is not None and self.algorithm.kind!=self.template:raise ValueError('Algorithm mismatch')
        if len(set((e.source,e.target,e.condition) for e in self.links))!=len(self.links):raise ValueError('Duplicate activation link')
        if self.template=='OCO':
            if len(ids)!=2 or self.links or len(self.exclusive_groups)!=1 or set(self.exclusive_groups[0])!=ids:raise ValueError('OCO requires exactly two exclusive legs')
        if self.template in ('OTO','OTOCO','OPO','OPOCO'):
            expected=3 if self.template.endswith('CO') else 2
            working=[n for n in self.nodes if n.role=='WORKING']
            pending=[n for n in self.nodes if n.role=='PENDING']
            if len(ids)!=expected or len(working)!=1 or len(pending)!=expected-1:raise ValueError('Working/pending topology mismatch')
            w=working[0];pending_ids={n.node_id for n in pending}
            condition='NET_RECEIPT' if self.template.startswith('OPO') else 'FULL_FILL'
            if set((e.source,e.target,e.condition) for e in self.links)!={(w.node_id,n,condition) for n in pending_ids}:raise ValueError('Activation topology mismatch')
            expected_groups={frozenset(pending_ids)} if expected==3 else set()
            if {frozenset(g) for g in self.exclusive_groups}!=expected_groups or len(self.exclusive_groups)!=len(expected_groups):raise ValueError('Pending exclusivity mismatch')
            if condition=='NET_RECEIPT' and (w.order.side!='BUY' or any(n.order.side!='SELL' or n.order.quantity.kind!='DERIVED_RECEIPT' or n.order.quantity.source_node!=w.node_id for n in pending)):
                raise ValueError('OPO requires net-receipt SELL quantities')
        if self.template in ('TWAP','VP') and (len(ids)!=1 or self.nodes[0].role!='ALGORITHM_PARENT' or self.links or self.exclusive_groups):raise ValueError('Algorithm parent topology mismatch')
        if self.template=='BATCH' and (self.links or self.exclusive_groups):raise ValueError('Batch is not an atomic linked list')
        # Origin here is a compiler hint only. It never creates a trusted child identity.
        for node in self.nodes:
            if node.child_origin=='VENUE_GENERATED_CHILD' and (self.execution_mode!='VENUE_NATIVE' or self.template in ('ATOMIC','BATCH')):
                raise ValueError('Venue children require a native parent program')
            q=node.order.quantity
            if q.kind=='DERIVED_RECEIPT' and (q.source_node not in ids or not any(e.source==q.source_node and e.target==node.node_id and e.condition=='NET_RECEIPT' for e in self.links)):
                raise ValueError('Derived receipt linkage missing')
        edges={i:[] for i in ids}
        for link in self.links:
            if link.source not in ids or link.target not in ids:raise ValueError('Unknown activation node')
            edges[link.source].append(link.target)
        def visit(node,active,done):
            if node in active:raise ValueError('Cyclic activation graph')
            if node in done:return
            for child in edges[node]:visit(child,active|{node},done)
            done.add(node)
        done=set()
        for node in ids:visit(node,set(),done)
        for group in self.exclusive_groups:
            if len(group)<2 or len(set(group))!=len(group) or not set(group)<=ids:raise ValueError('Invalid mutual exclusion group')


@dataclass(frozen=True,slots=True,kw_only=True)
class OrderProposal:
    contract_version:Literal['2026-10-28']
    proposal_id:str
    instrument_ref:str
    action:Literal['SET_TARGET','UPDATE_EXIT','HOLD']
    target_side:Literal['LONG','SHORT','FLAT']
    target_sizing:Literal['BASE_QUANTITY','QUOTE_NOTIONAL']
    target_value:str
    requested_leverage:int
    program:OrderProgram|None
    protection_policy:Literal['NATIVE_REQUIRED','HOSTED_ALLOWED_CASH_ONLY']
    exit_plan:DecisionProposalExitPlan
    valid_until:str
    evidence_refs:tuple[str,...]
    summary:str
    def __post_init__(self):
        identifier(self.proposal_id);identifier(self.instrument_ref)
        quantity=amount(self.target_value,zero=True)
        if (self.target_side=='FLAT')!=(quantity==0):raise ValueError('Target side/value mismatch')
        if self.requested_leverage<1 or self.exit_plan.maximum_holding_seconds<1:raise ValueError('Explicit positive limits required')
        utc_timestamp(self.valid_until)
        if not self.evidence_refs:raise ValueError('Evidence references required')
        if self.program is not None and (self.program.instrument_ref!=self.instrument_ref or utc_timestamp(self.program.submit_valid_until)>utc_timestamp(self.valid_until)):raise ValueError('Program/proposal binding mismatch')
        if self.action!='HOLD' and self.program is None:raise ValueError('Executable program required')


def validate_program_transition(previous:OrderProposal,candidate:OrderProposal):
    if previous.instrument_ref!=candidate.instrument_ref:raise ValueError('Instrument mismatch')
    protected=('target_side','target_sizing','target_value')
    if candidate.action=='HOLD':protected+=('program','protection_policy','exit_plan','requested_leverage','valid_until')
    elif candidate.action=='UPDATE_EXIT':protected+=('program','requested_leverage')
    else:return
    if any(getattr(previous,k)!=getattr(candidate,k) for k in protected):raise ValueError('HOLD/UPDATE_EXIT changes protected responsibility')


def schema_for(cls, *, allow_decimal=False):
    """Deterministic public schema generator, with no arbitrary runtime class loading."""
    from dataclasses import is_dataclass
    from trading_system.contracts.types import ScopeKey
    def shape(kind):
        origin=get_origin(kind)
        if origin is Literal:return {'enum':list(get_args(kind))}
        if origin in (Union,UnionType):return {'anyOf':[shape(k) for k in get_args(kind)]}
        if origin is tuple:return {'type':'array','items':shape(get_args(kind)[0])}
        if is_dataclass(kind):
            props={k:shape(t) for k,t in get_type_hints(kind).items()}
            return {'type':'object','properties':props,'required':list(props),'additionalProperties':False}
        if isinstance(kind,type) and issubclass(kind,Enum):return {'enum':[v.value for v in kind]}
        if kind is str:return {'type':'string','minLength':1,'maxLength':256}
        if kind is int:return {'type':'integer','minimum':0}
        if kind is bool:return {'type':'boolean'}
        if allow_decimal and kind is Decimal:return {'type':'string','pattern':r'^-?(0|[1-9][0-9]*)(\.[0-9]+)?$'}
        if kind is type(None):return {'type':'null'}
        raise TypeError('Unregistered schema type')
    return {'$schema':'https://json-schema.org/draft/2020-12/schema',**shape(cls)}

class OrderProgramCodec:
    def decode(self,payload):
        if isinstance(payload,dict) and payload.get('contract_version')=='2026-10-29':
            from .order_codec import VersionedOrderCodec
            return VersionedOrderCodec().decode('order-program',payload)
        from trading_system.storage.models import decode
        canonical_json(payload)
        schema=json.loads(contract_path('order-program.v2026-10-28.schema.json').read_text(encoding='utf-8'))
        Draft202012Validator(schema,format_checker=FormatChecker()).validate(payload)
        if len(canonical_json(payload).encode())>131072:raise ValueError('Program budget exceeded')
        return decode(OrderProgram,payload)
    def encode(self,record):
        if getattr(record,'contract_version',None)=='2026-10-29':
            from .order_codec import VersionedOrderCodec
            return VersionedOrderCodec().encode('order-program',record)
        if type(record) is not OrderProgram:raise TypeError('OrderProgram required')
        payload=domain_payload(record);self.decode(payload);return payload
    def hash(self,record):return canonical_hash(self.encode(record))
