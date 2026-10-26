"""Decimal-only offline metrics with explicit coverage and missing values.

EventRange does not contain a net equity curve, cash flows, contract
multipliers, or complete cost/initial-order coverage. MetricInputs supplies
these host-bound observations without changing the shared wire contracts.
Returns use initial capital; tail loss is the worst observed net-equity step,
not a statistical VaR estimate. No implicit FX or annualization is performed.
"""
from dataclasses import dataclass
from decimal import Decimal, localcontext

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp

from .experiments import check_offline, check_ref, fingerprint, require

D = Decimal


@dataclass(frozen=True, slots=True, kw_only=True)
class EquityObservation:
    at: str
    gross_equity: t.Money
    cumulative_external_flow: t.Money
    cumulative_cost: t.Money | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ProtectionInterval:
    start: str
    end: str
    covered: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class EventSequence:
    """Host ledger sequence binding; DomainEvent has only aggregate revision."""
    event_id: str
    sequence: int


@dataclass(frozen=True, slots=True, kw_only=True)
class MetricInputs:
    event_range_hash: str
    assumptions_hash: str
    start: str
    end: str
    evidence: tuple[t.EvidenceRef, ...]
    event_sequences: tuple[EventSequence, ...] = ()
    equity: tuple[EquityObservation, ...] = ()
    fills_complete: bool = False
    fees_complete: bool = False
    funding_complete: bool = False
    model_costs_complete: bool = False
    orders_complete: bool = False
    initial_orders: tuple[t.OrderProjection, ...] = ()
    protection: tuple[ProtectionInterval, ...] = ()
    contract_multiplier: Decimal | None = None


def _seconds(start, end) -> Decimal:
    delta = end - start
    return D(delta.days * 86400 + delta.seconds) + D(delta.microseconds) / D(1000000)


class MetricService:
    def __init__(self, inputs: MetricInputs | None = None):
        self.inputs = inputs

    def evaluate(self, events: t.EventRange, assumptions: t.MetricAssumptions) -> t.MetricReport:
        # Isolate arithmetic from a caller's Decimal context; preserve widely
        # separated magnitudes for sums/products as well as ordinary ratios.
        from trading_system.contracts.canonical import domain_payload
        values = []

        def collect(value):
            if isinstance(value, str):
                try:
                    candidate = D(value)
                    if candidate.is_finite():
                        values.append(candidate)
                except Exception:
                    pass
            elif isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)

        for record in (events, assumptions, self.inputs):
            if record is not None:
                collect(domain_payload(record))
        with localcontext() as ctx:
            ctx.prec = max(80, 2 * (max((v.adjusted() for v in values), default=0)
                - min((v.as_tuple().exponent for v in values), default=0)) + len(str(len(values))) + 16)
            return self._evaluate(events, assumptions)

    def _evaluate(self, events, assumptions):
        check_offline(events.scope.environment)
        require(bool(assumptions.valuation_currency), 'VALUATION_CURRENCY_REQUIRED')
        require(type(events.first_sequence) is int and type(events.last_sequence) is int
                and 0 <= events.first_sequence <= events.last_sequence, 'INVALID_EVENT_RANGE')
        require(isinstance(events.events, tuple), 'IMMUTABLE_EVENTS_REQUIRED')
        missing = set()
        amounts = {}
        currency = assumptions.valuation_currency
        seen_events, seen_fills = {}, {}
        fills, order_events = [], []
        for event in events.events:
            require(isinstance(event, t.DomainEvent) and event.scope == events.scope
                    and event.event_id and event.aggregate_id
                    and type(event.revision) is int and event.revision >= 1, 'INVALID_EVENT_BINDING')
            utc_timestamp(event.occurred_at)
            for ref in event.evidence:
                check_ref(ref, events.scope.environment)
            if not event.evidence:
                missing.add('EVENT_EVIDENCE_MISSING')
            prior = seen_events.get(event.event_id)
            require(prior is None or prior == event, 'CONFLICTING_EVENT_ID')
            if prior is not None:
                continue
            seen_events[event.event_id] = event
            if isinstance(event.payload, t.Fill):
                fill = event.payload
                require(fill.scope == events.scope and fill.order.scope == events.scope,
                        'FILL_SCOPE_MISMATCH')
                check_ref(fill.evidence, events.scope.environment)
                require(fill.evidence in event.evidence and fill.occurred_at == event.occurred_at,
                        'FILL_EVENT_EVIDENCE_MISMATCH')
                require(fill.quantity.value > 0 or (fill.correction_of is not None
                        and fill.quantity.value != 0), 'INVALID_FILL_QUANTITY')
                prior = seen_fills.get(fill.fill_id)
                require(prior is None or prior == fill, 'CONFLICTING_FILL_ID')
                if prior is None:
                    seen_fills[fill.fill_id] = fill
                    fills.append(fill)
            elif isinstance(event.payload, t.OrderProjection):
                require(event.payload.order.identity.scope == events.scope, 'ORDER_SCOPE_MISMATCH')
                order_events.append(event)
            else:
                payload_scope = getattr(event.payload, 'scope', None)
                if payload_scope is not None:
                    require(payload_scope == events.scope, 'PAYLOAD_SCOPE_MISMATCH')
        inputs = self.inputs
        for ref in assumptions.evidence:
            check_ref(ref, events.scope.environment)
        # Counts alone cannot establish continuity: aggregate revision is not
        # a global ledger sequence. Require explicit host-bound sequence data.
        contiguous = False
        if inputs is not None and inputs.event_sequences:
            require(isinstance(inputs.event_sequences, tuple), 'IMMUTABLE_SEQUENCES_REQUIRED')
            bindings = {item.event_id: item.sequence for item in inputs.event_sequences}
            require(len(bindings) == len(inputs.event_sequences)
                    and all(type(value) is int and value > 0 for value in bindings.values()),
                    'INVALID_EVENT_SEQUENCE_BINDING')
            ordered = sorted(bindings.values())
            contiguous = (set(bindings) == set(seen_events)
                and ordered[0] == events.first_sequence and ordered[-1] == events.last_sequence
                and all(b == a + 1 for a, b in zip(ordered, ordered[1:])))
        if not events.events and events.first_sequence == events.last_sequence == 0:
            contiguous = True
        if not contiguous:
            missing.add('EVENT_RANGE_GAP')
            if inputs is None or not inputs.event_sequences:
                missing.add('EVENT_SEQUENCE_BINDING_MISSING')
        if inputs is not None:
            require(inputs.event_range_hash == fingerprint(events)
                    and inputs.assumptions_hash == fingerprint(assumptions), 'METRIC_INPUT_BINDING_MISMATCH')
            start, end = utc_timestamp(inputs.start), utc_timestamp(inputs.end)
            require(start < end and bool(inputs.evidence), 'METRIC_COVERAGE_REQUIRED')
            for ref in inputs.evidence:
                check_ref(ref, events.scope.environment)
            for flag in (inputs.fills_complete, inputs.fees_complete, inputs.funding_complete,
                         inputs.model_costs_complete, inputs.orders_complete):
                require(type(flag) is bool, 'INVALID_METRIC_COVERAGE_FLAG')
            require(all(start <= utc_timestamp(event.occurred_at) <= end for event in events.events),
                    'EVENT_OUTSIDE_METRIC_WINDOW')
        else:
            missing.add('METRIC_COVERAGE_MISSING')
        for name, values, complete in (
            ('fees', assumptions.fees, inputs is not None and inputs.fees_complete),
            ('funding', assumptions.funding, inputs is not None and inputs.funding_complete),
            ('model_costs', assumptions.model_costs, inputs is not None and inputs.model_costs_complete)):
            require(isinstance(values, tuple) and all(isinstance(item, t.Money) for item in values),
                    'TYPED_COSTS_REQUIRED')
            if not complete or not values or not assumptions.evidence:
                amounts[name] = None
                missing.add(name.upper() + '_INCOMPLETE')
            elif any(item.currency != currency for item in values):
                amounts[name] = None
                missing.add(name.upper() + '_FX_MISSING')
            else:
                amounts[name] = sum((item.amount for item in values), D(0))
                require(name == 'funding' or amounts[name] >= 0, 'NEGATIVE_COST_TOTAL')
        costs_known = all(amounts[key] is not None for key in ('fees', 'funding', 'model_costs'))
        curve = inputs.equity if inputs else ()
        adjusted = []
        net_curve = []
        if curve:
            require(isinstance(curve, tuple) and len(curve) >= 2, 'EQUITY_CURVE_TOO_SHORT')
            times = tuple(utc_timestamp(point.at) for point in curve)
            require(times == tuple(sorted(set(times))) and times[0] == start and times[-1] == end,
                    'EQUITY_WINDOW_INCOMPLETE')
            for point in curve:
                require(point.gross_equity.currency == point.cumulative_external_flow.currency == currency
                        and (point.cumulative_cost is None or point.cumulative_cost.currency == currency),
                        'EQUITY_CURRENCY_MISMATCH')
                adjusted.append(point.gross_equity.amount - (
                    point.cumulative_external_flow.amount - curve[0].cumulative_external_flow.amount))
            require(adjusted[0] > 0, 'POSITIVE_INITIAL_EQUITY_REQUIRED')
            if all(point.cumulative_cost is not None for point in curve) and costs_known:
                total = sum((amounts[key] for key in ('fees', 'funding', 'model_costs')), D(0))
                require(curve[-1].cumulative_cost.amount - curve[0].cumulative_cost.amount == total,
                        'COST_TIMELINE_TOTAL_MISMATCH')
                net_curve = [value - (point.cumulative_cost.amount - curve[0].cumulative_cost.amount)
                             for value, point in zip(adjusted, curve)]
        else:
            missing.add('EQUITY_AND_CASHFLOW_CURVE_MISSING')
        amounts['net_pnl'] = amounts['net_return'] = None
        if adjusted and costs_known:
            amounts['net_pnl'] = adjusted[-1] - adjusted[0] - sum(
                (amounts[key] for key in ('fees', 'funding', 'model_costs')), D(0))
            amounts['net_return'] = amounts['net_pnl'] / adjusted[0]
        else:
            missing.add('NET_RETURN_INCOMPLETE')
        amounts['max_drawdown'] = amounts['tail_loss'] = None
        if net_curve:
            peak, drawdown = net_curve[0], D(0)
            for value in net_curve:
                peak = max(peak, value)
                drawdown = max(drawdown, (peak - value) / peak)
            amounts['max_drawdown'] = drawdown
            amounts['tail_loss'] = max(D(0), max((a - b) / adjusted[0]
                for a, b in zip(net_curve, net_curve[1:])))
        else:
            missing.add('NET_EQUITY_COST_TIMELINE_MISSING')
        amounts['turnover'] = None
        multiplier = (inputs.contract_multiplier if inputs and events.scope.product == t.Product.USD_M_PERP
                      else D(1))
        fill_missing = False
        trade_ids = set()
        for fill in fills:
            expected_unit = {t.Product.SPOT: 'BASE', t.Product.USD_M_PERP: 'CONTRACT',
                             t.Product.US_EQUITY: 'SHARE'}[events.scope.product]
            if fill.quantity.unit != expected_unit:
                fill_missing = True
                missing.add('FILL_UNIT_MISMATCH')
            if fill.native_trade_id is not None and fill.correction_of is None:
                key = fill.venue, fill.order.scope, fill.native_trade_id
                if key in trade_ids:
                    fill_missing = True
                    missing.add('AMBIGUOUS_NATIVE_TRADE_DUPLICATE')
                trade_ids.add(key)
            if fill.price.currency != currency:
                fill_missing = True
                missing.add('FILL_FX_MISSING')
            if fill.correction_of is not None:
                original = seen_fills.get(fill.correction_of)
                if (original is None or original.correction_of is not None
                        or original.order != fill.order or original.quantity.unit != fill.quantity.unit
                        or original.venue != fill.venue
                        or utc_timestamp(original.occurred_at) > utc_timestamp(fill.occurred_at)):
                    fill_missing = True
                    missing.add('FILL_CORRECTION_BASIS_MISSING')
        for original in (fill for fill in fills if fill.correction_of is None):
            corrected = original.quantity.value + sum((fill.quantity.value for fill in fills
                if fill.correction_of == original.fill_id), D(0))
            if corrected < 0:
                fill_missing = True
                missing.add('FILL_CORRECTION_EXCEEDS_ORIGINAL')
        if (inputs and inputs.fills_complete and contiguous and adjusted and not fill_missing
                and isinstance(multiplier, D) and multiplier.is_finite() and multiplier > 0):
            turnover = sum((fill.quantity.value * fill.price.value * multiplier for fill in fills), D(0))
            require(turnover >= 0, 'NEGATIVE_CORRECTED_TURNOVER')
            amounts['turnover'] = turnover / adjusted[0]
        else:
            missing.add('TURNOVER_INCOMPLETE')
        amounts['order_uncertain_seconds'] = None
        if inputs and inputs.orders_complete and contiguous:
            states, opened, intervals = {}, {}, []
            for order in inputs.initial_orders:
                require(order.order.identity.scope == events.scope, 'INITIAL_ORDER_SCOPE_MISMATCH')
                key = order.order.identity.logical_client_order_id
                require(key not in states, 'DUPLICATE_INITIAL_ORDER')
                states[key] = order
                if order.command_certainty == 'UNKNOWN':
                    opened[key] = start
            initial_missing = False
            for event in sorted(order_events, key=lambda item: (utc_timestamp(item.occurred_at), item.revision)):
                order = event.payload
                key = order.order.identity.logical_client_order_id
                at = utc_timestamp(event.occurred_at)
                if key not in states and at > start:
                    initial_missing = True
                if order.command_certainty == 'UNKNOWN':
                    opened.setdefault(key, at)
                elif key in opened:
                    intervals.append((opened.pop(key), at))
                states[key] = order
            intervals.extend((at, end) for at in opened.values())
            if initial_missing:
                missing.add('INITIAL_ORDER_STATE_MISSING')
            else:
                # Sum order-seconds: simultaneous unknown orders each contribute.
                amounts['order_uncertain_seconds'] = sum((_seconds(a, b) for a, b in intervals), D(0))
                if opened:
                    missing.add('UNCERTAINTY_RIGHT_CENSORED_AT_WINDOW_END')
        else:
            missing.add('ORDER_STATE_COVERAGE_MISSING')
        amounts['protection_coverage'] = None
        if inputs and inputs.protection:
            cursor, covered = start, D(0)
            for interval in inputs.protection:
                a, b = utc_timestamp(interval.start), utc_timestamp(interval.end)
                require(a == cursor and a < b <= end and type(interval.covered) is bool,
                        'PROTECTION_INTERVAL_GAP_OR_OVERLAP')
                if interval.covered:
                    covered += _seconds(a, b)
                cursor = b
            require(cursor == end, 'PROTECTION_WINDOW_INCOMPLETE')
            amounts['protection_coverage'] = covered / _seconds(start, end)
        else:
            missing.add('PROTECTION_COVERAGE_MISSING')
        amounts['slippage'] = amounts['overfit_test'] = None
        missing.update(('SLIPPAGE_REFERENCE_PRICES_MISSING', 'OUT_OF_SAMPLE_OVERFIT_TEST_NOT_RUN'))
        # Gaps or evidence-less events invalidate all event-derived metrics.
        if 'EVENT_EVIDENCE_MISSING' in missing:
            amounts['turnover'] = amounts['order_uncertain_seconds'] = None
        money_names = {'net_pnl', 'fees', 'funding', 'model_costs', 'slippage'}
        metrics = tuple(t.MetricValue(name=name, value=value,
            unit=currency if name in money_names else 'order_seconds' if name == 'order_uncertain_seconds'
            else 'ratio') for name, value in amounts.items())
        return t.MetricReport(metrics=metrics, assumptions=assumptions, missing=tuple(sorted(missing)))
