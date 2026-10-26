"""Explicit IANA risk days and immutable, once-per-day ledger settlements.

Pure pnl requires the opening midnight and an end point within that risk day.
settle requires the closing midnight, and persists the chained adjusted HWM.
No day rollover changes a lock or authorizes trading.
"""
from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.risk.pnl import adjusted_equities, exact_sum


def day_bounds(day, zone):
    tz = ZoneInfo(zone)
    date = datetime.fromisoformat(day).date()
    start = datetime.combine(date, time.min, tzinfo=tz)
    end = datetime.combine(date + timedelta(days=1), time.min, tzinfo=tz)
    # A skipped local date (e.g. Pacific/Apia) is not a risk day.
    if end.astimezone(timezone.utc) <= start.astimezone(timezone.utc):
        raise ValueError('nonexistent local risk day')
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


@dataclass(frozen=True)
class Settlement:
    result: t.RiskDayResult
    digest: str
    start_at: str
    end_at: str
    closing_equity: t.Money
    adjusted_close: t.Money


@dataclass(frozen=True)
class RiskDayHead:
    settlement: Settlement
    sequence: int


class RiskDayService:
    def __init__(self, *, missing_costs=('COST_COVERAGE_UNVERIFIED',)):
        # Only a trusted caller with reconciled coverage may pass (). Missing
        # costs do not prohibit management of independently verified current risk.
        self.missing_costs = tuple(missing_costs)

    def pnl(self, series: list[t.EquityPoint], cashflows: list[t.Cashflow], zone: str) -> t.RiskDayResult:
        adjusted, missing = adjusted_equities(series, cashflows)
        start = utc_timestamp(series[0].at)
        day = start.astimezone(ZoneInfo(zone)).date().isoformat()
        opening, closing = day_bounds(day, zone)
        if start != opening or not opening < utc_timestamp(series[-1].at) <= closing:
            raise ValueError('series requires exact opening midnight and one risk day')
        missing = tuple(sorted(set(missing + self.missing_costs)))
        currency = series[0].equity.currency
        return t.RiskDayResult(risk_day=day, timezone=zone,
            adjusted_pnl=None if missing else t.Money(amount=exact_sum((adjusted[-1], adjusted[0].copy_negate())), currency=currency),
            high_water_mark=None if missing else t.Money(amount=max(adjusted), currency=currency), missing=missing)

    def settle(self, uow, scope, series, cashflows, zone):
        result = self.pnl(series, cashflows, zone)
        if result.missing:
            raise ValueError('incomplete history cannot advance durable HWM')
        start, end = day_bounds(result.risk_day, zone)
        if utc_timestamp(series[-1].at) != end:
            raise ValueError('settlement requires closing midnight')
        if any(item.evidence.environment != scope.environment for item in (*series, *cashflows)):
            raise ValueError('settlement scope environment mismatch')
        digest = canonical_hash({'series': [domain_payload(p) for p in series],
                                 'cashflows': [domain_payload(f) for f in cashflows], 'zone': zone})
        key = 'risk-day:' + zone + ':' + result.risk_day
        with uow.transaction(scope) as tx:
            previous = tx._latest('audit_events', key, Settlement)
            if previous:
                if previous.digest != digest:
                    raise ValueError('settled risk day cannot be silently revised')
                return previous.result
            head = tx._latest('audit_events', 'risk-day-head', RiskDayHead)
            adjusted, _ = adjusted_equities(series, cashflows)
            offset = Decimal(0)
            if head:
                prior = head.settlement
                if (prior.result.timezone != zone or utc_timestamp(prior.end_at) != start
                        or prior.closing_equity != series[0].equity):
                    raise ValueError('risk-day baseline/zone discontinuity requires review')
                offset = exact_sum((prior.adjusted_close.amount, adjusted[0].copy_negate()))
            adjusted = tuple(exact_sum((value, offset)) for value in adjusted)
            high = max(adjusted + (() if head is None else (head.settlement.result.high_water_mark.amount,)))
            result = t.RiskDayResult(risk_day=result.risk_day, timezone=zone,
                adjusted_pnl=result.adjusted_pnl,
                high_water_mark=t.Money(amount=high, currency=series[0].equity.currency), missing=())
            record = Settlement(result, digest, start.isoformat(), end.isoformat(), series[-1].equity,
                                t.Money(amount=adjusted[-1], currency=series[0].equity.currency))
            tx._append('audit_events', key, record, 0)
            sequence = 0 if head is None else head.sequence
            tx._append('audit_events', 'risk-day-head', RiskDayHead(record, sequence + 1), sequence)
            return result
