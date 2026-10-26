"""Deterministic offline risk calculation, never an execution authorization.

RiskInputs are service-bound facts, not proposal fields. They must be rebuilt
for each decision. No account access, mode switching, or order side effects.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext

from trading_system.contracts import types as t
from trading_system.contracts.wire import TradeIntent, RiskPolicy
from trading_system.contracts.canonical import canonical_hash, decimal_string, utc_timestamp
from trading_system.market.freshness import FreshnessPolicy
from .exposure import ExposureCalculator, ExposureMark, WorkingOrder
from .valuation import FxRate, Valuation, floor_quantity, nonnegative

D = Decimal


@dataclass(frozen=True, kw_only=True)
class GroupLimit:
    group_id: str
    maximum_gross: t.Money


@dataclass(frozen=True, kw_only=True)
class EmergencyReduction:
    """Preauthorized reduction bound to this decision's owner and leg."""
    maximum_quantity: t.Quantity
    allowed_order_types: tuple[str, ...]
    reference_price: t.Price
    maximum_slippage_bps: Decimal
    maximum_quote_age_ms: int


@dataclass(frozen=True, kw_only=True)
class RiskInputs:
    """One decision's authenticated evidence bundle.

    fee_reserve covers both entry and planned exit; funding_reserve covers
    the authorized holding horizon. Empty tuples mean evidenced zero cost,
    None means unknown. spendable is currency-specific, net of venue freezes.
    The binding service must validate provenance and authorization before
    constructing this bundle; construction alone grants no authority.
    """
    intent_id: str
    authorization_id: str
    owner_strategy_id: str
    ownership_epoch: int
    policy_revision: int
    scope: t.ScopeKey
    collateral_group_id: str
    basis_fingerprint: str
    book_sequence: int
    snapshot_ref: t.SnapshotRef
    clock: t.ClockReading
    expires_at: str
    instrument: t.InstrumentSpec
    session: t.SessionState
    multiplier: Decimal
    base_asset: str
    fee_reserve: tuple[t.Money, ...] | None
    funding_reserve: tuple[t.Money, ...] | None
    existing_planned_loss: t.Money
    capital_in_use: t.Money
    spendable: tuple[t.Money, ...]
    fx_rates: tuple[FxRate, ...]
    marks: tuple[ExposureMark, ...]
    order_sides: tuple[WorkingOrder, ...]
    group_limits: tuple[GroupLimit, ...]
    authorized_margin_mode: str
    observed_margin_mode: str
    authorized_position_mode: str
    observed_position_mode: str
    new_risk_allowed: bool
    allow_round_down: bool
    emergency: EmergencyReduction | None
    evidence: tuple[t.EvidenceRef, ...]


class RiskEvaluator:
    def __init__(self, inputs: RiskInputs):
        self.inputs = inputs

    def evaluate(self, intent: TradeIntent, book: t.BookSnapshot,
                 market: t.SnapshotPacket, policy: RiskPolicy) -> t.RiskDecision:
        with localcontext() as ctx:
            ctx.prec = 80
            return self._evaluate(intent, book, market, policy)

    def _evaluate(self, intent, book, market, policy):
        facts = self.inputs
        currency = policy.valuation_currency
        money = lambda value: t.Money(amount=value, currency=currency)
        empty = t.ExposureReport(gross=money(D(0)), net=money(D(0)), groups=(),
                                 missing=('NOT_CALCULATED',))
        quantity = t.Quantity(value=D(0), unit=intent.quantity_unit)
        exposure, fees, loss, reserves = empty, money(D(0)), money(D(0)), ()
        codes: list[str] = []
        replan = False

        def reject(code, *, plan=False):
            nonlocal replan
            codes.append(code)
            replan = replan or plan

        def result():
            # Diagnostic zeros on DENY are explicitly incomplete, not estimates.
            verdict = ('REPLAN_REQUIRED' if replan and all(c.startswith('REPLAN:') for c in codes)
                       else 'DENY') if codes else 'ALLOW'
            coverage = replace(book.coverage, missing=tuple(sorted(set(
                book.coverage.missing + exposure.missing + tuple(codes)))))
            key = canonical_hash({'intent': intent.intent_id, 'snapshot': market.ref.hash,
                                  'book': book.basis_fingerprint, 'sequence': book.sequence,
                                  'policy': policy.revision, 'at': facts.clock.utc_now,
                                  'verdict': verdict, 'codes': codes,
                                  'quantity': str(quantity.value), 'loss': str(loss.amount),
                                  'gross': str(exposure.gross.amount)})
            return t.RiskDecision(risk_decision_id=key, intent_id=intent.intent_id,
                verdict=verdict, policy_revision=policy.revision, evaluated_at=facts.clock.utc_now,
                expires_at=min((intent.valid_until, facts.expires_at), key=utc_timestamp),
                snapshot_ref=market.ref, permitted_quantity=quantity,
                permitted_leverage=intent.requested_leverage, reserved_assets=reserves,
                estimated_loss=loss, fees_assumption=fees, aggregate_exposure_after=exposure,
                rejection_codes=tuple(codes), evidence_completeness=coverage)

        try:
            now = utc_timestamp(facts.clock.utc_now)
            if intent.environment == 'LIVE' or policy.environment == 'LIVE':
                reject('LIVE_DISABLED')
            if (facts.scope != book.scope or facts.scope != market.scope
                    or facts.scope.environment != intent.environment
                    or facts.scope.venue_account_id != intent.venue_account_id
                    or facts.scope.product != intent.product
                    or facts.scope.instrument_uid != intent.instrument_uid
                    or facts.scope.venue_leg != intent.venue_leg
                    or policy.environment != intent.environment):
                reject('SCOPE_MISMATCH')
            if (facts.intent_id != intent.intent_id or facts.authorization_id != intent.authorization_id
                    or facts.owner_strategy_id != intent.owner_strategy_id
                    or facts.ownership_epoch != intent.ownership_epoch
                    or facts.policy_revision != intent.risk_policy_revision
                    or facts.policy_revision != policy.revision
                    or facts.collateral_group_id != intent.collateral_group_id
                    or facts.collateral_group_id != policy.collateral_group_id):
                reject('AUTHORITY_MISMATCH')
            if (facts.basis_fingerprint != book.basis_fingerprint
                    or intent.basis_fingerprint != book.basis_fingerprint
                    or facts.book_sequence != book.sequence or facts.snapshot_ref != market.ref
                    or intent.snapshot_id != market.ref.id):
                reject('STALE_BASIS')
            if (now >= utc_timestamp(intent.valid_until) or now >= utc_timestamp(facts.expires_at)
                    or now >= utc_timestamp(intent.execution.entry_deadline)
                    or utc_timestamp(intent.created_at) > now
                    or utc_timestamp(intent.as_of) > utc_timestamp(intent.created_at)):
                reject('EXPIRED_OR_FUTURE_INTENT')
            if (not facts.evidence or any(not e.id or not e.hash
                    or e.environment != facts.scope.environment or utc_timestamp(e.visible_at) > now
                    for e in facts.evidence)):
                reject('RISK_EVIDENCE_MISSING')
            if not (book.coverage.query_succeeded and book.coverage.pagination_complete
                    and book.coverage.supported and not book.coverage.missing
                    and book.coverage.scope == book.scope):
                reject('BOOK_INCOMPLETE')
            if intent.action != 'SET_TARGET':
                reject('ACTION_REQUIRES_INGRESS_VALIDATION')
            spec = facts.instrument
            if (spec.instrument_uid != intent.instrument_uid or spec.product != intent.product
                    or spec.quantity_step.unit != intent.quantity_unit
                    or spec.minimum_quantity.unit != intent.quantity_unit
                    or spec.tick_size.currency != spec.quote_currency
                    or spec.minimum_notional.currency != spec.quote_currency
                    or spec.settlement_currency != spec.quote_currency
                    or nonnegative(facts.multiplier) == 0):
                reject('INSTRUMENT_RULES_UNSUPPORTED')
            requested = nonnegative(decimal_string(intent.target_quantity))
            if intent.target_side not in ('LONG', 'SHORT', 'FLAT') or (intent.target_side == 'FLAT' and requested):
                reject('INVALID_TARGET')
            adjusted = floor_quantity(t.Quantity(value=requested, unit=intent.quantity_unit), spec.quantity_step)
            if adjusted.permitted != adjusted.original and not facts.allow_round_down:
                reject('REPLAN:QUANTITY_PRECISION', plan=True)
            # Original remains in the immutable TradeIntent; decision records actual.
            quantity = adjusted.permitted
            if requested > 0 and quantity.value == 0:
                reject('REPLAN:BELOW_MINIMUM', plan=True)
            target = quantity.value * (-1 if intent.target_side == 'SHORT' else 1)
            positions = [p for p in book.positions if p.scope == facts.scope]
            if len(positions) > 1:
                reject('AMBIGUOUS_POSITION')
            actual = sum((p.actual_quantity.value for p in positions), D(0))
            for position in positions:
                if (position.owner_strategy_id != facts.owner_strategy_id
                        or position.ownership_epoch != facts.ownership_epoch
                        or position.actual_quantity.unit != intent.quantity_unit):
                    reject('POSITION_OWNER_OR_UNIT_MISMATCH')
            if ((intent.venue_leg == 'LONG' and (actual < 0 or target < 0))
                    or (intent.venue_leg == 'SHORT' and (actual > 0 or target > 0))):
                reject('LEG_DIRECTION_MISMATCH')
            if actual * target < 0:
                reject('REVERSAL_REQUIRES_FLAT')
            reducing = actual != 0 and actual * target >= 0 and abs(target) < abs(actual)
            delta_value = target - actual
            if book.active_orders:
                # Shared order type lacks execution side/reduce-only semantics.
                # Do not invent a safe remaining delta while orders can still fill.
                reject('ACTIVE_ORDERS_REQUIRE_RECONCILIATION')
            if not reducing and (not facts.new_risk_allowed
                    or any(p.risk_locks for p in book.positions)):
                reject('NEW_RISK_LOCKED')
            if intent.product != 'USD_M_PERP' and (target < 0 or intent.requested_leverage != 1
                                                 or facts.multiplier != 1):
                reject('CASH_ONLY')
            if type(intent.requested_leverage) is not int or intent.requested_leverage < 1:
                reject('INVALID_LEVERAGE')
            elif not reducing and intent.requested_leverage > policy.maximum_leverage:
                reject('REPLAN:LEVERAGE_LIMIT', plan=True)
            if not reducing and intent.product == 'USD_M_PERP' and (
                    facts.authorized_margin_mode not in ('ISOLATED', 'CROSS')
                    or facts.authorized_position_mode not in ('ONE_WAY', 'HEDGE')
                    or facts.authorized_margin_mode != facts.observed_margin_mode
                    or facts.authorized_position_mode != facts.observed_position_mode):
                reject('ACCOUNT_MODE_MISMATCH')
            if not reducing and intent.product == 'USD_M_PERP' and (
                    (facts.observed_position_mode == 'ONE_WAY' and intent.venue_leg != 'BOTH')
                    or (facts.observed_position_mode == 'HEDGE' and intent.venue_leg == 'BOTH')):
                reject('POSITION_MODE_LEG_MISMATCH')
            emergency = facts.emergency if reducing else None
            age = policy.maximum_quote_age_ms
            if emergency is not None:
                nonnegative(emergency.maximum_slippage_bps)
                if (emergency.maximum_quantity.unit != intent.quantity_unit
                        or abs(delta_value) > nonnegative(emergency.maximum_quantity.value)
                        or intent.execution.order_preference not in emergency.allowed_order_types
                        or emergency.maximum_quote_age_ms <= 0):
                    reject('EMERGENCY_SCOPE_INVALID')
                age = emergency.maximum_quote_age_ms
            freshness = FreshnessPolicy({facts.scope.product: age}).evaluate(market, facts.clock, facts.session)
            codes.extend(freshness.rejection_codes)
            bid, ask = market.quote.bid, market.quote.ask
            if bid is None or ask is None:
                reject('PRICE_MISSING')
                return result()
            if bid.currency != spec.quote_currency or ask.currency != spec.quote_currency or ask.value < bid.value:
                reject('INVALID_QUOTE')
            price = ask.value if delta_value > 0 else bid.value
            slippage = min(nonnegative(decimal_string(intent.execution.maximum_entry_slippage_bps)),
                           nonnegative(decimal_string(policy.maximum_entry_slippage_bps)))
            if emergency is not None:
                slippage = emergency.maximum_slippage_bps
                if emergency.reference_price.currency != spec.quote_currency:
                    reject('EMERGENCY_CURRENCY_MISMATCH')
                reference = emergency.reference_price.value
                adverse = ((price - reference) if delta_value > 0 else (reference - price))
                if adverse * 10000 > reference * slippage:
                    reject('EMERGENCY_PRICE_TOLERANCE')
            elif (ask.value - bid.value) * 10000 > bid.value * slippage:
                reject('ENTRY_SPREAD_LIMIT')
            if intent.execution.order_preference == 'LIMIT':
                if intent.execution.limit_price is None:
                    reject('LIMIT_PRICE_MISSING')
                    return result()
                limit = decimal_string(intent.execution.limit_price)
                if limit <= 0 or limit % spec.tick_size.value:
                    reject('INVALID_LIMIT_PRICE')
                reference = emergency.reference_price.value if emergency else price
                adverse = limit - reference if delta_value > 0 else reference - limit
                if adverse * 10000 > reference * slippage:
                    reject('LIMIT_PRICE_TOLERANCE')
                price = max(price, limit) if delta_value > 0 else min(price, limit)
            elif intent.execution.order_preference != 'MARKET':
                reject('ORDER_TYPE_UNSUPPORTED')
            valuation = Valuation(currency, facts.fx_rates)
            nonnegative(facts.existing_planned_loss.amount)
            nonnegative(facts.capital_in_use.amount)
            if any(rate.evidence_id not in {e.id for e in facts.evidence} for rate in facts.fx_rates):
                reject('FX_EVIDENCE_UNBOUND')
            marks = tuple(m for m in facts.marks if m.scope != facts.scope) + (
                ExposureMark(scope=facts.scope, price=t.Price(value=max(ask.value, price), currency=spec.quote_currency),
                    multiplier=facts.multiplier, unit=intent.quantity_unit,
                    related_group=next((m.related_group for m in facts.marks if m.scope == facts.scope), '')),
            )
            q = lambda value: t.Quantity(value=value, unit=intent.quantity_unit)
            delta = t.DeltaPlan(scope=facts.scope, target=q(target), actual=q(actual), working=q(D(0)),
                remaining=q(delta_value), classification='REDUCE' if reducing else 'INCREASE', cancel_first=())
            exposure = ExposureCalculator(valuation, marks, facts.order_sides).project(book, delta)
            codes.extend(exposure.missing)
            if facts.fee_reserve is None or facts.funding_reserve is None:
                reject('FEE_OR_FUNDING_EVIDENCE_MISSING')
                return result()
            amounts: dict[str, Decimal] = {}
            for charge in facts.fee_reserve + facts.funding_reserve:
                amounts[charge.currency] = amounts.get(charge.currency, D(0)) + nonnegative(charge.amount)
            fees = money(sum((valuation.convert(m, liability=True).amount for m in facts.fee_reserve), D(0)))
            funding = sum((valuation.convert(m, liability=True).amount for m in facts.funding_reserve), D(0))
            notional_quote = abs(delta_value * facts.multiplier * price)
            notional = valuation.convert(t.Money(amount=notional_quote, currency=spec.quote_currency), liability=True).amount
            if delta_value and (abs(delta_value) < spec.minimum_quantity.value or notional_quote < spec.minimum_notional.amount):
                reject('REPLAN:BELOW_MINIMUM', plan=True)
            if not reducing:
                if intent.exit_plan.protection_policy != policy.protection_policy:
                    reject('PROTECTION_POLICY_MISMATCH')
                if intent.product == 'USD_M_PERP' and intent.exit_plan.protection_policy != 'NATIVE_REQUIRED':
                    reject('NATIVE_PROTECTION_REQUIRED')
                if intent.exit_plan.stop_price is None:
                    reject('STOP_REQUIRED')
                    return result()
                stop = decimal_string(intent.exit_plan.stop_price)
                if stop <= 0 or stop % spec.tick_size.value or (target > 0 and stop >= price) or (target < 0 and stop <= price):
                    reject('INVALID_STOP')
                price_loss = abs(target * facts.multiplier) * (abs(price - stop) + price * slippage / 10000)
                loss = money(valuation.convert(t.Money(amount=price_loss, currency=spec.quote_currency), liability=True).amount + fees.amount + funding)
                if intent.max_planned_loss.currency != currency:
                    reject('PLANNED_LOSS_CURRENCY_MISMATCH')
                limits = ((notional, policy.maximum_order_notional, 'ORDER_NOTIONAL'),
                          (loss.amount, policy.maximum_planned_loss_per_intent, 'INTENT_LOSS'),
                          (loss.amount, intent.max_planned_loss.amount, 'REQUESTED_LOSS'),
                          (exposure.gross.amount, policy.maximum_gross_exposure, 'GROSS'),
                          (abs(exposure.net.amount), policy.maximum_net_exposure, 'NET'),
                          (loss.amount + valuation.convert(facts.existing_planned_loss, liability=True).amount,
                           policy.maximum_total_planned_loss, 'TOTAL_LOSS'))
                for value, limit, name in limits:
                    if value > nonnegative(decimal_string(limit)):
                        reject('REPLAN:' + name, plan=True)
                group_limits = {g.group_id: g.maximum_gross for g in facts.group_limits}
                if len(group_limits) != len(facts.group_limits):
                    reject('DUPLICATE_GROUP_LIMIT')
                for group in exposure.groups:
                    bound = group_limits.get(group.group_id)
                    if bound is None or bound.currency != currency:
                        reject('GROUP_LIMIT_MISSING')
                    elif group.gross.amount > nonnegative(bound.amount):
                        reject('REPLAN:RELATED_GROUP', plan=True)
                count = sum(p.actual_quantity.value != 0 for p in book.positions) + (not positions and target != 0)
                if count > policy.maximum_open_positions or (delta_value != 0 and len(book.active_orders) + 1 > policy.maximum_active_orders):
                    reject('REPLAN:COUNT_LIMIT', plan=True)
            else:
                loss = money(fees.amount + funding)
            margin = (notional_quote / intent.requested_leverage if intent.product == 'USD_M_PERP' and not reducing
                      else notional_quote if intent.product != 'USD_M_PERP' and delta_value > 0 else D(0))
            amounts[spec.settlement_currency] = amounts.get(spec.settlement_currency, D(0)) + margin
            if intent.product != 'USD_M_PERP' and delta_value < 0:
                amounts[facts.base_asset] = amounts.get(facts.base_asset, D(0)) + abs(delta_value)
            spendable = {m.currency: nonnegative(m.amount) for m in facts.spendable}
            balances = {m.currency: nonnegative(m.amount) for m in book.balances}
            if len(spendable) != len(facts.spendable) or len(balances) != len(book.balances):
                reject('AMBIGUOUS_BALANCES')
            # spendable is net of venue freezes; subtract local unreflected holds.
            for reservation in book.reservations:
                if reservation.reservation.state == t.ReservationState.RELEASED:
                    continue
                asset = reservation.reservation.asset
                held, reflected, pending = (nonnegative(v) for v in
                    (reservation.held, reservation.reflected, reservation.release_pending))
                if reflected > held:
                    reject('INVALID_RESERVATION')
                spendable[asset] = spendable.get(asset, D(0)) - max(held - reflected, pending)
            for asset, amount in amounts.items():
                if amount > min(spendable.get(asset, D(0)), balances.get(asset, D(0))):
                    reject('INSUFFICIENT_SPENDABLE:' + asset)
            reserves = tuple(t.Money(amount=value, currency=asset) for asset, value in sorted(amounts.items()) if value)
            if not reducing:
                capital = valuation.convert(t.Money(amount=margin, currency=spec.settlement_currency), liability=True).amount
                if (capital + fees.amount + funding + nonnegative(valuation.convert(facts.capital_in_use, liability=True).amount)
                        + nonnegative(decimal_string(policy.cash_buffer)) > nonnegative(decimal_string(policy.maximum_capital))):
                    reject('REPLAN:CAPITAL', plan=True)
        except (ValueError, ArithmeticError) as exc:
            reject('INVALID_OR_MISSING_INPUT:' + str(exc))
        return result()
