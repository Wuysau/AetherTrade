"""Trusted compiler inputs, resolved by composition rather than model payloads.

The public RuleSnapshot deliberately stays thin. These internal records carry
the additional symbol/account limits needed to compile a product honestly.
Presence of a profile is neither proof of venue qualification nor permission.
"""
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.order_program import amount, identifier


class PositionMode(StrEnum):
    CASH = 'CASH'
    ONE_WAY = 'ONE_WAY'
    HEDGE = 'HEDGE'


@dataclass(frozen=True, slots=True, kw_only=True)
class DecimalRange:
    minimum: str
    maximum: str

    def __post_init__(self):
        if amount(self.minimum, zero=True) > amount(self.maximum):
            raise ValueError('Invalid rule range')

    def contains(self, value: Decimal) -> bool:
        return Decimal(self.minimum) <= value <= Decimal(self.maximum)


@dataclass(frozen=True, slots=True, kw_only=True)
class SpotRules:
    allowed_stp: tuple[str, ...] = ('NONE',)
    trailing_above_bips: DecimalRange | None = None
    trailing_below_bips: DecimalRange | None = None
    maximum_iceberg_parts: int | None = None
    maximum_peg_levels: int = 100
    quote_market_quantity: bool = False
    sor_enabled: bool = False


@dataclass(frozen=True, slots=True, kw_only=True)
class UsdMRules:
    position_mode: PositionMode
    quantity_unit: str | None = None
    contract_multiplier: str | None = None
    allowed_stp: tuple[str, ...] = ('NONE',)
    callback_percent: DecimalRange | None = None
    allowed_price_match: tuple[str, ...] = ()
    rpi_eligible: bool = False
    maximum_batch_orders: int = 5


@dataclass(frozen=True, slots=True, kw_only=True)
class EquityRules:
    allowed_sessions: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ExecutionAlgorithmRules:
    minimum_duration_seconds: int
    maximum_duration_seconds: int
    notional: DecimalRange

    def __post_init__(self):
        if not 1 <= self.minimum_duration_seconds <= self.maximum_duration_seconds <= 86400:
            raise ValueError('Invalid algorithm duration rules')


@dataclass(frozen=True, slots=True, kw_only=True)
class TrustedProductRules:
    profile_id: str
    scope: t.ScopeKey
    rule_snapshot_ref: str
    rule_revision: int
    adapter_build_digest: str
    venue_symbol: str
    allowed_order_types: tuple[str, ...]
    quantity: DecimalRange
    price: DecimalRange
    notional: DecimalRange
    market_quantity_step: str
    market_quantity: DecimalRange
    evidence: tuple[t.EvidenceRef, ...]
    as_of: str
    valid_until: str
    execution_price_envelope: DecimalRange | None = None
    spot: SpotRules | None = None
    usd_m: UsdMRules | None = None
    equity: EquityRules | None = None
    algorithm: ExecutionAlgorithmRules | None = None

    def __post_init__(self):
        for value in (self.profile_id, self.rule_snapshot_ref, self.venue_symbol):
            identifier(value)
        if not self.venue_symbol.isalnum() or len(self.venue_symbol) > 32:
            raise ValueError('Venue symbol must be a symbol, not a path')
        amount(self.market_quantity_step)
        if self.rule_revision < 1 or len(self.adapter_build_digest) != 64 or not self.evidence:
            raise ValueError('Trusted rule provenance required')
        if any(e.environment != self.scope.environment for e in self.evidence):
            raise ValueError('Profile evidence environment mismatch')
        if utc_timestamp(self.as_of) >= utc_timestamp(self.valid_until):
            raise ValueError('Invalid profile validity')
        selected = (self.spot, self.usd_m, self.equity)
        expected = {t.Product.SPOT: 0, t.Product.USD_M_PERP: 1, t.Product.US_EQUITY: 2}[self.scope.product]
        if sum(value is not None for value in selected) != 1 or selected[expected] is None:
            raise ValueError('Product profile mismatch')
        if not self.allowed_order_types or len(set(self.allowed_order_types)) != len(self.allowed_order_types):
            raise ValueError('Explicit order types required')
        if self.spot is not None and (not 0 <= self.spot.maximum_peg_levels <= 100
                or self.spot.maximum_iceberg_parts is not None and self.spot.maximum_iceberg_parts < 1):
            raise ValueError('Invalid Spot limits')
        if self.usd_m is not None and (self.usd_m.position_mode == PositionMode.CASH
                or not 1 <= self.usd_m.maximum_batch_orders <= 5):
            raise ValueError('Invalid USD-M profile')
        if self.usd_m is not None:
            if self.usd_m.quantity_unit is not None:
                identifier(self.usd_m.quantity_unit)
            if self.usd_m.contract_multiplier is not None:
                amount(self.usd_m.contract_multiplier)
        if self.equity is not None and (not self.equity.allowed_sessions
                or not set(self.equity.allowed_sessions) <= {'RTH', 'EXTENDED', '24H'}):
            raise ValueError('Invalid Equity sessions')
        if self.execution_price_envelope is not None and Decimal(self.execution_price_envelope.minimum) <= 0:
            raise ValueError('Execution price envelope must be positive')
