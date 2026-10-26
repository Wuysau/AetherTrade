"""Fictional contract inputs only; no simulated qualification or trading driver."""
from copy import deepcopy
from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
import types
from typing import get_args, get_origin, get_type_hints, Literal, Union

from trading_system.contracts import types as domain
from trading_system.contracts.wire import ContractCodec

KINDS = ('decision-proposal', 'trade-intent', 'risk-policy', 'execution-command')
NOW = '2026-10-20T06:39:00Z'
HASH = 'a' * 64


def wire_record(paper_fixtures, kind, **changes):
    payload = deepcopy(paper_fixtures[kind])
    payload.update(changes)
    return ContractCodec().decode(kind, payload)


def scope():
    return domain.ScopeKey(environment=domain.Environment.PAPER,
                           venue_account_id='paper-account', product=domain.Product.SPOT,
                           instrument_uid='paper-btc', venue_leg='BOTH')


def condition():
    return domain.RuleCondition(operator='GT', input_ref='closed_bar.close',
                                threshold=Decimal('10.00000001'), children=())


def rule_graph():
    return domain.RuleGraph(rule_id='paper-rule', revision=1, instrument_ref='paper-btc',
                            condition=condition(), trigger='EDGE', window_seconds=60,
                            closed_bar_only=True, cooldown_seconds=60, sizing='BASE_QUANTITY',
                            target_value=Decimal('0.001'), exit_priority=('protection', 'ordinary'),
                            missing_data='HOLD')


def domain_record(cls, paper_fixtures):
    """Populate each declared field with a fictional typed input, not a verdict.

    Used to exercise serialization/structural contracts. Does not execute a
    scenario or claim these inputs form adequate evidence for any service.
    """
    if cls is domain.ScopeKey:
        return scope()
    if cls is domain.RuleCondition:
        return condition()
    if cls is domain.RuleGraph:
        return rule_graph()
    kind = {'DecisionProposal': 'decision-proposal', 'TradeIntent': 'trade-intent',
            'RiskPolicy': 'risk-policy', 'ExecutionCommand': 'execution-command'}.get(cls.__name__)
    if kind:
        return wire_record(paper_fixtures, kind)

    def value(annotation, name):
        origin, args = get_origin(annotation), get_args(annotation)
        if origin in (types.UnionType, Union):
            return None if type(None) in args else value(args[0], name)
        if origin is tuple:
            return ()
        if origin is Literal:
            return args[0]
        if annotation is Decimal:
            return Decimal('9007199254740993.00000001')
        if annotation is str:
            if name.endswith('_at') or name in ('valid_from', 'valid_until', 'as_of', 'utc_now', 'coverage_gap_since'):
                return NOW
            if name in ('hash', 'payload_hash', 'basis_fingerprint'):
                return HASH
            return 'paper-fixture'
        if annotation is int:
            return 1
        if annotation is bool:
            return False
        if isinstance(annotation, type) and issubclass(annotation, Enum):
            return domain.Environment.PAPER if annotation is domain.Environment else next(iter(annotation))
        if is_dataclass(annotation):
            return domain_record(annotation, paper_fixtures)
        raise TypeError(f'No typed fixture for {name}: {annotation}')

    hints = get_type_hints(cls)
    return cls(**{f.name: value(hints[f.name], f.name) for f in fields(cls)})
