"""Gross projection includes each position and every in-flight order separately.

OrderProjection has no side or valuation price. Explicit typed marks supply
these facts; missing marks are reported, never interpreted as zero risk.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext,Inexact,Rounded
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


@dataclass(frozen=True,kw_only=True)
class PendingExposure:
    """Reader-bound quantity absent from active orders; never cash or authority.

Represented orders require zero extra quantity, matching TargetCompiler's
pending semantics. Host readers must authenticate exact command/quantity links.
"""
    obligation_id:str
    scope:t.ScopeKey
    signed_quantity:t.Quantity
    command_refs:tuple[str,...]
    represented_order_ids:tuple[str,...]=()


@dataclass(frozen=True,kw_only=True)
class ProposedExposure:
    """A proposed node's fill bound, not a durable order or owned position."""
    node_id:str
    scope:t.ScopeKey
    signed_quantity:t.Quantity
    mark:ExposureMark


class ExposureCalculator:
    def __init__(self, valuation: Valuation, marks: tuple[ExposureMark, ...],
                 orders: tuple[WorkingOrder, ...] = (),*,pending:tuple[PendingExposure,...]=(),
                 proposed:tuple[ProposedExposure,...]=()):
        self.valuation, self.marks, self.orders = valuation, marks, orders
        self.pending=pending
        self.proposed=proposed
        if len({m.scope for m in marks}) != len(marks):
            raise ValueError('DUPLICATE_MARK')
        if len({o.logical_client_order_id for o in orders}) != len(orders):
            raise ValueError('DUPLICATE_ORDER_SIDE')

    def project(self, book: t.BookSnapshot, delta: t.DeltaPlan) -> t.ExposureReport:
        with localcontext() as ctx:
            ctx.prec = 256
            ctx.traps[Inexact]=ctx.traps[Rounded]=True
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

        def add(scope: t.ScopeKey, quantity: t.Quantity,override=None) -> None:
            original_mark = next((m for m in self.marks if m.scope == scope), None)
            mark=original_mark if override is None else override
            if mark is None or mark.unit != quantity.unit:
                missing.append('VALUATION_MARK_MISSING:' + scope.instrument_uid)
                return
            if (mark.scope!=scope or (original_mark is not None and mark.related_group!=original_mark.related_group)):
                missing.append('EXPOSURE_MARK_BINDING_MISMATCH');return
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

        proposed_valid=True
        if self.proposed:
            seen=set();proposed_quantity=D(0)
            for p in self.proposed:
                if (type(p) is not ProposedExposure or not p.node_id or p.node_id in seen or p.scope!=delta.scope
                    or p.mark.scope!=p.scope or p.signed_quantity.unit!=delta.remaining.unit
                    or not p.signed_quantity.value.is_finite() or p.signed_quantity.value*delta.remaining.value<=0):
                    missing.append('PROPOSED_EXPOSURE_BINDING_MISMATCH');proposed_valid=False;continue
                seen.add(p.node_id);proposed_quantity+=p.signed_quantity.value
            if proposed_quantity!=delta.remaining.value:
                missing.append('PROPOSED_EXPOSURE_DELTA_MISMATCH');proposed_valid=False
        split_new_inventory=bool(self.proposed) and delta.classification!='REDUCE'
        actual = D(0)
        matching = [p for p in book.positions if p.scope == delta.scope]
        if len(matching) > 1:
            missing.append('AMBIGUOUS_POSITION')
        for position in book.positions:
            value = position.actual_quantity
            if position.scope == delta.scope:
                actual += value.value
                # Reduce only the verified leg; other legs never net against it.
                if not split_new_inventory:
                    value = t.Quantity(value=value.value + delta.remaining.value, unit=value.unit)
            add(position.scope, value)
        if not matching and not split_new_inventory:
            add(delta.scope, delta.remaining)
        if split_new_inventory and proposed_valid:
            for p in self.proposed:add(p.scope,p.signed_quantity,p.mark)
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
        seen_pending=set();seen_commands=set();represented=set()
        orders={o.order.identity.logical_client_order_id:o for o in book.active_orders}
        for pending in self.pending:
            if (type(pending) is not PendingExposure or not pending.obligation_id
                or pending.obligation_id in seen_pending or not pending.command_refs
                or len(set(pending.command_refs))!=len(pending.command_refs)
                or seen_commands.intersection(pending.command_refs)):
                missing.append('PENDING_EXPOSURE_IDENTITY_MISSING_OR_DUPLICATED');continue
            seen_pending.add(pending.obligation_id);seen_commands.update(pending.command_refs)
            value=pending.signed_quantity
            mark=next((m for m in self.marks if m.scope==pending.scope),None)
            if (pending.scope.environment!=book.scope.environment or pending.scope.venue_account_id!=book.scope.venue_account_id
                or type(value) is not t.Quantity or not value.value.is_finite()
                or mark is None or value.unit!=mark.unit):
                missing.append('PENDING_EXPOSURE_SCOPE_OR_UNIT_MISMATCH');continue
            if pending.represented_order_ids:
                ids=set(pending.represented_order_ids)
                if (len(ids)!=len(pending.represented_order_ids) or ids.intersection(represented)
                    or value.value!=0 or any(i not in orders or orders[i].order.identity.scope!=pending.scope for i in ids)):
                    missing.append('PENDING_EXPOSURE_DOUBLE_COUNT_OR_MAPPING_INVALID')
                represented.update(ids)
                continue
            if value.value==0:
                missing.append('PENDING_EXPOSURE_QUANTITY_MISSING');continue
            if pending.scope==delta.scope:working+=value.value
            add(pending.scope,value)
        if working != delta.working.value:
            missing.append('WORKING_QUANTITY_MISMATCH')
        currency = self.valuation.currency
        money = lambda amount: t.Money(amount=amount, currency=currency)
        return t.ExposureReport(
            gross=money(sum((v[0] for v in groups.values()), D(0))),
            net=money(sum((v[1] for v in groups.values()), D(0))),
            groups=tuple(t.ExposureGroup(group_id=k, gross=money(v[0]), net=money(v[1]))
                         for k, v in sorted(groups.items())), missing=tuple(sorted(set(missing))))
