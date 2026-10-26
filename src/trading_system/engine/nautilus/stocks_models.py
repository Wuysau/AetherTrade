"""Stocks application models. Request previews are never executable authority."""
from dataclasses import dataclass

from trading_system.contracts import types as t
from trading_system.contracts.canonical import decimal_string


@dataclass(frozen=True, slots=True, kw_only=True)
class StocksOrderPreview:
    symbol: str
    side: str
    order_type: str
    quantity: str | None
    notional: str | None
    price: str | None
    time_in_force: str
    trading_session: str | None
    tokenize: bool = False

    def __post_init__(self):
        if self.tokenize is not False:
            raise ValueError('EXPLICIT_TOKENIZE_FALSE_REQUIRED')
        if not self.symbol or self.side not in ('BUY', 'SELL'):
            raise ValueError('INVALID_EQUITY_ORDER')
        if self.order_type not in ('MARKET', 'LIMIT'):
            raise ValueError('NATIVE_STOP_UNVERIFIED')
        for value in (self.quantity, self.notional, self.price):
            if value is not None and decimal_string(value) <= 0:
                raise ValueError('POSITIVE_AMOUNT_REQUIRED')
        if self.order_type == 'LIMIT':
            if (self.quantity is None or self.price is None or self.notional is not None
                    or self.trading_session not in ('RTH', 'EXTENDED', '24H')
                    or self.time_in_force not in ('DAY', 'GTC')):
                raise ValueError('INVALID_LIMIT_FIELDS_OR_SESSION')
            if decimal_string(self.price).as_tuple().exponent < -2:
                raise ValueError('PRICE_PRECISION_EXCEEDS_TWO')
            quantity = decimal_string(self.quantity)
            if (self.time_in_force == 'GTC' and quantity != quantity.to_integral_value()
                    and self.trading_session == 'RTH'):
                raise ValueError('FRACTIONAL_GTC_SESSION_REQUIRED')
        else:
            if self.price is not None or self.trading_session is not None or self.time_in_force != 'DAY':
                raise ValueError('INVALID_MARKET_FIELDS')
            if self.side == 'BUY':
                if self.notional is None or self.quantity is not None:
                    raise ValueError('MARKET_BUY_REQUIRES_NOTIONAL_ONLY')
            elif self.quantity is None or self.notional is not None:
                raise ValueError('MARKET_SELL_REQUIRES_QUANTITY_ONLY')


@dataclass(frozen=True, slots=True, kw_only=True)
class StocksRequestObservation:
    event: t.VenueEvent
    acknowledgement: str
    actual_shares: str
    command_receipt_only: bool = True
    lifecycle: str = 'UNKNOWN'

    def __post_init__(self):
        if self.acknowledgement not in ('S', 'F') or decimal_string(self.actual_shares) < 0:
            raise ValueError('INVALID_REQUEST_OBSERVATION')
        if self.command_receipt_only is not True or self.lifecycle != 'UNKNOWN':
            raise ValueError('ACK_IS_NOT_LIFECYCLE_EVIDENCE')
