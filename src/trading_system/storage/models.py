"""Typed persistence encoding. PostgreSQL is the only supported backend."""
from __future__ import annotations

from dataclasses import fields, is_dataclass
from decimal import Decimal
from enum import Enum
from functools import wraps,lru_cache
import json
from typing import get_args, get_origin, get_type_hints, Literal, Union
from types import UnionType

from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload, canonical_json, canonical_hash
from trading_system.contracts.wire import ContractCodec, ExecutionCommand, TradeIntent, DecisionProposal, RiskPolicy


class SequenceConflict(ValueError):
    pass


class ScopeMismatch(ValueError):
    pass


class MissingEvidence(LookupError):
    pass


def atomic_write(method):
    """A caught application validation error must still abort the transaction."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        tx = getattr(self, 'tx', None)
        if tx is None:  # EventStore's first positional argument is the transaction.
            tx = args[0] if args else kwargs['tx']
        try:
            return method(self, *args, **kwargs)
        except BaseException:
            tx.failed = True
            raise
    return wrapped


def encode(record: object) -> str:
    return canonical_json(domain_payload(record))


@lru_cache(maxsize=256)
def _record_metadata(cls):
    """Immutable Python contract metadata; no payload, authority or risk facts."""
    return get_type_hints(cls),frozenset(f.name for f in fields(cls))


def decode(cls, payload):
    """Restore only an explicitly requested contract type; never import stored names."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    from trading_system.contracts.ledger_intents import LedgerIntentRecord, LedgerIntentCodec
    if cls is LedgerIntentRecord:
        return LedgerIntentCodec().decode(payload)
    if cls in (ExecutionCommand, TradeIntent):
        return ContractCodec().decode(
            'execution-command' if cls is ExecutionCommand else 'trade-intent', payload)

    def restore(kind, value):
        if value is None:
            if kind is type(None) or type(None) in get_args(kind):
                return None
            raise ValueError('null for non-null stored field')
        wire_kinds={DecisionProposal:'decision-proposal',TradeIntent:'trade-intent',
                    ExecutionCommand:'execution-command',RiskPolicy:'risk-policy'}
        if kind in wire_kinds:
            return ContractCodec().decode(wire_kinds[kind],value)
        origin = get_origin(kind)
        if origin in (Union, UnionType):
            for option in get_args(kind):
                if option is type(None):
                    continue
                try:
                    return restore(option, value)
                except (TypeError, ValueError, KeyError):
                    pass
            raise ValueError('invalid stored union')
        if origin is tuple:
            return tuple(restore(get_args(kind)[0], item) for item in value)
        if origin is Literal:
            if value not in get_args(kind):
                raise ValueError('invalid stored literal')
            return value
        if kind is Decimal:
            if not isinstance(value, str):
                raise ValueError('stored decimal must be a string')
            result = Decimal(value)
            if not result.is_finite():
                raise ValueError('non-finite decimal')
            return result
        if isinstance(kind, type) and issubclass(kind, Enum):
            return kind(value)
        if is_dataclass(kind):
            hints,names = _record_metadata(kind)
            if set(value) != names:
                raise ValueError('stored record fields differ from contract')
            return kind(**{k: restore(hints[k], v) for k, v in value.items()})
        if kind in (str, bool, int) and type(value) is not kind:
            raise ValueError('stored scalar type differs from contract')
        return value
    return restore(cls, payload)


def scope_id(scope: t.ScopeKey) -> str:
    return canonical_hash(domain_payload(scope))


def account_key(scope: t.ScopeKey) -> tuple[str, str, str]:
    return (str(scope.environment), scope.venue_account_id, str(scope.product))


# SPEC section 12. Domain tables are versioned records; monetary facts also
# have NUMERIC/unit columns. No second cash total is maintained here.
DOMAIN_TABLES = (
    'accounts', 'collateral_groups', 'instruments', 'capabilities', 'policies',
    'authorizations', 'strategy_revisions', 'deployments', 'decision_runs',
    'proposals', 'intents', 'reservations', 'commands', 'venue_events', 'fills',
    'positions', 'protection_plans', 'risk_locks', 'reconciliation_cases',
    'ownership_transfers', 'cashflows', 'control_commands', 'audit_events',
)
