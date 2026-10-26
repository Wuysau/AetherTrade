"""Gross projection includes each position and every in-flight order separately.

OrderProjection has no side or valuation price. Explicit typed marks supply
these facts; missing marks are reported, never interpreted as zero risk.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Literal

from trading_system.contracts import types as t
from .valuation import Valuation, nonnegative

D = Decimal


@dataclass(frozen=True, kw_only=True)
class ExposureMark:
    scope: t.ScopeKey
    price: t.Price
    multiplier: Decimal
    unit: str
    related_group: str

    def __post_init__(self):
        if nonnegative(self.multiplier) == 0 or not self.related_group:
            raise ValueError('INVALID_EXPOSURE_MARK')


@dataclass(frozen=True, kw_only=True)
class WorkingOrder:
    logical_client_order_id: str
    side: Literal['BUY', 'SELL']


class ExposureCalculator:
    def __init__(self, valuation: Valuation, marks: tuple[ExposureMark, ...],
                 orders: tuple[WorkingOrder, ...] = ()):
        self.valuation, self.marks, self.orders = valuation, marks, orders
        if len({m.scope for m in marks}) != len(marks):
            raise ValueError('DUPLICATE_MARK')
        if len({o.logical_client_order_id for o in orders}) != len(orders):
            raise ValueError('DUPLICATE_ORDER_SIDE')

    def project(self, book: t.BookSnapshot, delta: t.DeltaPlan) -> t.ExposureReport:
        with localcontext() as ctx:
            ctx.prec = 80
            return self._project(book, delta)

    def _project(self, book: t.BookSnapshot, delta: t.DeltaPlan) -> t.ExposureReport:
        groups: dict[str, tuple[Decimal, Decimal]] = {}
        missing: list[str] = []
        if delta.scope != book.scope:
            missing.append('DELTA_SCOPE_MISMATCH')
        if not (book.coverage.query_succeeded and book.coverage.pagination_complete
                and book.coverage.supported and not book.coverage.missing
                and book.coverage.scope == book.scope):
            missing.append('BOOK_INCOMPLETE')
        if (len({q.unit for q in (delta.target, delta.actual, delta.working, delta.remaining)}) != 1
                or delta.target.value != delta.actual.value + delta.working.value + delta.remaining.value):
            missing.append('DELTA_INCONSISTENT')
        if delta.classification == 'REVERSAL_REQUIRES_FLAT':
            missing.append('REVERSAL_REQUIRES_FLAT')
        if delta.actual.value * delta.target.value < 0:
            missing.append('REVERSAL_REQUIRES_FLAT')

        def add(scope: t.ScopeKey, quantity: t.Quantity) -> None:
            mark = next((m for m in self.marks if m.scope == scope), None)
            if mark is None or mark.unit != quantity.unit:
                missing.append('VALUATION_MARK_MISSING:' + scope.instrument_uid)
                return
            if (scope.environment != book.scope.environment
                    or scope.venue_account_id != book.scope.venue_account_id):
                missing.append('POSITION_SCOPE_MISMATCH')
            try:
                # Exposure is a liability bound, not discounted asset equity.
                value = self.valuation.convert(t.Money(
                    amount=quantity.value * mark.multiplier * mark.price.value,
                    currency=mark.price.currency), liability=True).amount
            except ValueError as exc:
                missing.append(str(exc))
                return
            gross, net = groups.get(mark.related_group, (D(0), D(0)))
            groups[mark.related_group] = gross + abs(value), net + value

        actual = D(0)
        matching = [p for p in book.positions if p.scope == delta.scope]
        if len(matching) > 1:
            missing.append('AMBIGUOUS_POSITION')
        for position in book.positions:
            value = position.actual_quantity
            if position.scope == delta.scope:
                actual += value.value
                # Reduce only the verified leg; other legs never net against it.
                value = t.Quantity(value=value.value + delta.remaining.value, unit=value.unit)
            add(position.scope, value)
        if not matching:
            add(delta.scope, delta.remaining)
        if actual != delta.actual.value:
            missing.append('ACTUAL_QUANTITY_MISMATCH')
        working = D(0)
        for order in book.active_orders:
            identity = order.order.identity
            side = next((o.side for o in self.orders
                         if o.logical_client_order_id == identity.logical_client_order_id), None)
            if side not in ('BUY', 'SELL') or order.leaves_quantity.value < 0:
                missing.append('WORKING_ORDER_EVIDENCE_MISSING')
                continue
            signed = order.leaves_quantity.value * (1 if side == 'BUY' else -1)
            if identity.scope == delta.scope:
                working += signed
            add(identity.scope, t.Quantity(value=signed, unit=order.leaves_quantity.unit))
        if working != delta.working.value:
            missing.append('WORKING_QUANTITY_MISMATCH')
        currency = self.valuation.currency
        money = lambda amount: t.Money(amount=amount, currency=currency)
        return t.ExposureReport(
            gross=money(sum((v[0] for v in groups.values()), D(0))),
            net=money(sum((v[1] for v in groups.values()), D(0))),
            groups=tuple(t.ExposureGroup(group_id=k, gross=money(v[0]), net=money(v[1]))
                         for k, v in sorted(groups.items())), missing=tuple(sorted(set(missing))))
