"""Shared IP-07 incremental capacity arithmetic; no execution authority.

The caller already holds the existing collateral mutex and has reconciled the
group projections against the current book. Venue free balances are net of venue
freezes. Remaining caps exclude external actual usage, but have NOT deducted
these local holds yet. Program totals/gross limits are not incremental requests.
Constructing these inputs or receiving a sufficient result grants no authority.
"""
from dataclasses import dataclass
from decimal import Decimal, DecimalException, Inexact, Rounded, localcontext

from trading_system.contracts import types as t


def amounts(values):
    """Historical public IP-07 validation; currencies remain separate units."""
    result = {}
    for value in values:
        if (not value.currency or value.currency in result or not isinstance(value.amount, Decimal)
                or not value.amount.is_finite()
                or value.amount < 0):
            raise ValueError('invalid or ambiguous asset amounts')
        result[value.currency] = value.amount
    return result


@dataclass(frozen=True, kw_only=True)
class CapacityInputs:
    incremental_requirements: tuple[t.Money, ...]
    venue_free_balances: tuple[t.Money, ...]
    remaining_group_capacity: tuple[t.Money, ...]
    remaining_strategy_capacity: tuple[t.Money, ...]
    buffers: tuple[t.Money, ...]
    reconciled_group_holds: tuple[t.ReservationProjection, ...]
    owner_strategy_id: str


@dataclass(frozen=True, kw_only=True)
class CapacityResult:
    sufficient: bool
    rejection_codes: tuple[str, ...]


def check_capacity(inputs: CapacityInputs) -> CapacityResult:
    """Use the existing per-asset debit/held/owned-held formula exactly once.

    This does not validate risk, target allocation, authorization, clocks,
    ownership, native source evidence or closure. Those remain caller duties.
    Arithmetic outside the existing256-digit parent-funding boundary fails
    closed instead of rounding away a small obligation.
    """
    if not isinstance(inputs, CapacityInputs):
        raise ValueError('CAPACITY_INPUTS_REQUIRED')
    if not isinstance(inputs.owner_strategy_id, str) or not inputs.owner_strategy_id:
        raise ValueError('CAPACITY_OWNER_REQUIRED')
    balances, limits, strategy, buffers = map(amounts, (inputs.venue_free_balances,
        inputs.remaining_group_capacity, inputs.remaining_strategy_capacity, inputs.buffers))
    required_assets = amounts(inputs.incremental_requirements)
    identifiers = set()
    for p in inputs.reconciled_group_holds:
        r = p.reservation
        if (not r.reservation_id or r.reservation_id in identifiers or not r.asset
                or not r.owner_strategy_id
                or any(not isinstance(v, Decimal) or not v.is_finite() or v < 0
                    for v in (r.amount, p.held, p.reflected, p.release_pending))
                or p.held > r.amount or p.reflected > p.held or p.release_pending > p.held):
            raise ValueError('INVALID_CAPACITY_HOLD')
        identifiers.add(r.reservation_id)
    try:
        with localcontext() as arithmetic:
            arithmetic.prec = 256
            arithmetic.traps[Inexact] = arithmetic.traps[Rounded] = True
            for asset, required in required_assets.items():
                same = [p for p in inputs.reconciled_group_holds if p.reservation.asset == asset]
                held = sum((p.held for p in same), Decimal(0))
                owned = sum((p.held for p in same
                    if p.reservation.owner_strategy_id == inputs.owner_strategy_id), Decimal(0))
                unreflected = sum((max(p.held-p.reflected, p.release_pending) for p in same), Decimal(0))
                if any(asset not in values for values in (balances, limits, strategy, buffers)) or required > min(
                        balances.get(asset, Decimal(0))-unreflected-buffers.get(asset, Decimal(0)),
                        limits.get(asset, Decimal(0))-held, strategy.get(asset, Decimal(0))-owned):
                    return CapacityResult(sufficient=False,rejection_codes=('INSUFFICIENT_CAPACITY:'+asset,))
    except DecimalException as exc:
        raise ValueError('CAPACITY_ARITHMETIC_NOT_EXACT') from exc
    return CapacityResult(sufficient=True,rejection_codes=())
