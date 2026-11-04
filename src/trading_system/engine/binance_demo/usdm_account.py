"""Pure USDⓈ-M Demo account observations, not venue or execution qualification.

The caller supplies the independently observed position mode. REST position-risk
rows and stream updates are observations only: absence never means flat. Stream
updates are partial, and balance change is retained as reported (not PnL or a
complete funding ledger). Account-level totals are deliberately not interpreted.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
import re
from typing import Mapping

from .contracts import DemoError, DemoScope


class PositionMode(Enum):
    ONE_WAY = 'ONE_WAY'
    HEDGE = 'HEDGE'


@dataclass(frozen=True)
class Position:
    scope: DemoScope
    symbol: str
    side: str
    amount: Decimal
    margin_type: str | None = None
    update_time_ms: int | None = None


@dataclass(frozen=True)
class SymbolConfig:
    scope: DemoScope
    symbol: str
    margin_type: str
    leverage: int


@dataclass(frozen=True)
class AssetBalance:
    scope: DemoScope
    asset: str
    wallet_balance: Decimal
    margin_balance: Decimal
    unrealized_profit: Decimal
    available_balance: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    update_time_ms: int


@dataclass(frozen=True)
class BalanceUpdate:
    scope: DemoScope
    asset: str
    wallet_balance: Decimal
    cross_wallet_balance: Decimal
    balance_change: Decimal


@dataclass(frozen=True)
class AccountUpdate:
    scope: DemoScope
    reason: str
    event_time_ms: int
    transaction_time_ms: int
    balances: tuple[BalanceUpdate, ...]
    positions: tuple[Position, ...]
    balances_present: bool
    positions_present: bool


def _invalid():
    raise DemoError('DEMO_USDM_OBSERVATION_INVALID')


def _scope(scope):
    if type(scope) is not DemoScope or scope.product != 'USDM':
        _invalid()


def _mapping(value):
    if not isinstance(value, Mapping):
        _invalid()
    return value


def _rows(value):
    if not isinstance(value, list):
        _invalid()
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z0-9_]{1,40}', value):
        _invalid()
    return value


def _decimal(value):
    # Never round, silently coerce floating point, or accept infinities/NaN.
    if not isinstance(value, (str, Decimal)):
        _invalid()
    if isinstance(value, str) and (len(value) > 100 or not re.fullmatch(
            r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value)):
        _invalid()
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError):
        _invalid()
    if not parsed.is_finite() or len(parsed.as_tuple().digits) > 40 or abs(parsed.as_tuple().exponent) > 24:
        _invalid()
    return parsed


def _timestamp(value):
    if type(value) is not int or not 0 <= value < 2**63:
        _invalid()
    return value


def _mode(mode):
    if not isinstance(mode, PositionMode):
        _invalid()


def position_mode(payload) -> PositionMode:
    """Parse GET positionSide/dual; no config event substitutes for this read."""
    value = _mapping(payload).get('dualSidePosition')
    if type(value) is not bool:
        _invalid()
    return PositionMode.HEDGE if value else PositionMode.ONE_WAY


def _position(scope, mode, symbol, side, amount, margin_type=None, update_time_ms=None):
    symbol = _identifier(symbol)
    if mode == PositionMode.ONE_WAY:
        if side != 'BOTH':
            _invalid()
    elif side not in ('LONG', 'SHORT'):
        _invalid()
    amount = _decimal(amount)
    if (side == 'LONG' and amount < 0) or (side == 'SHORT' and amount > 0):
        _invalid()
    return Position(scope, symbol, side, amount, margin_type, update_time_ms)


def _unique(items, key):
    seen = set()
    for item in items:
        identity = key(item)
        if identity in seen:
            _invalid()
        seen.add(identity)
    return tuple(items)


def position_risk(scope: DemoScope, mode: PositionMode, rows) -> tuple[Position, ...]:
    """Project positionRisk v3 identity, amount and update time, not the full risk record.

    An empty result is not a flat snapshot; update times are not unique IDs.
    """
    _scope(scope)
    _mode(mode)
    result = []
    for raw in _rows(rows):
        row = _mapping(raw)
        result.append(_position(scope, mode, row.get('symbol'), row.get('positionSide'), row.get('positionAmt'),
                                update_time_ms=_timestamp(row.get('updateTime'))))
    return _unique(result, lambda p: (p.symbol, p.side))


def symbol_configs(scope: DemoScope, rows) -> tuple[SymbolConfig, ...]:
    _scope(scope)
    result = []
    for raw in _rows(rows):
        row = _mapping(raw)
        margin, leverage = row.get('marginType'), row.get('leverage')
        if margin not in ('CROSSED', 'ISOLATED') or type(leverage) is not int or leverage <= 0:
            _invalid()
        result.append(SymbolConfig(scope, _identifier(row.get('symbol')), margin, leverage))
    return _unique(result, lambda c: c.symbol)


def asset_balances(scope: DemoScope, payload) -> tuple[AssetBalance, ...]:
    """Extract per-asset account v3 values, ignoring unlabelled account totals."""
    _scope(scope)
    result = []
    for raw in _rows(_mapping(payload).get('assets')):
        row = _mapping(raw)
        result.append(AssetBalance(scope, _identifier(row.get('asset')), *(
            _decimal(row.get(field)) for field in ('walletBalance', 'marginBalance', 'unrealizedProfit',
                                                   'availableBalance', 'initialMargin', 'maintMargin')),
            _timestamp(row.get('updateTime'))))
    return _unique(result, lambda b: b.asset)


def account_update(scope: DemoScope, mode: PositionMode, event) -> AccountUpdate:
    """Normalize partial ACCOUNT_UPDATE; timestamps are not unique IDs or ordering guarantees."""
    _scope(scope)
    _mode(mode)
    event = _mapping(event)
    if event.get('e') != 'ACCOUNT_UPDATE':
        _invalid()
    event_time, transaction_time = _timestamp(event.get('E')), _timestamp(event.get('T'))
    body = _mapping(event.get('a'))
    reason = body.get('m')
    if not isinstance(reason, str) or not re.fullmatch(r'[A-Z_]{1,64}', reason):
        _invalid()
    balances, positions = [], []
    for raw in _rows(body.get('B', [])):
        row = _mapping(raw)
        balances.append(BalanceUpdate(scope, _identifier(row.get('a')), _decimal(row.get('wb')),
                                      _decimal(row.get('cw')), _decimal(row.get('bc'))))
    for raw in _rows(body.get('P', [])):
        row = _mapping(raw)
        raw_margin = row.get('mt')
        if raw_margin not in ('cross', 'isolated'):
            _invalid()
        margin = 'CROSSED' if raw_margin == 'cross' else 'ISOLATED'
        positions.append(_position(scope, mode, row.get('s'), row.get('ps'), row.get('pa'), margin))
    return AccountUpdate(scope, reason, event_time, transaction_time, _unique(balances, lambda b: b.asset),
                         _unique(positions, lambda p: (p.symbol, p.side)), 'B' in body, 'P' in body)
