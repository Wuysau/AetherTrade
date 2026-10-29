"""Deterministic offline risk calculation, never an execution authorization.

RiskInputs are service-bound facts, not proposal fields. They must be rebuilt
for each decision. No account access, mode switching, or order side effects.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, Inexact, Rounded
from jsonschema import ValidationError

from trading_system.contracts import types as t
from trading_system.contracts.wire import TradeIntent, RiskPolicy
from trading_system.contracts.canonical import canonical_hash, decimal_string, utc_timestamp
from trading_system.market.freshness import FreshnessPolicy
from .exposure import ExposureCalculator, ExposureMark, WorkingOrder
from .valuation import FxRate, Valuation, floor_quantity, nonnegative
from .policy_checks import (exact_risk_arithmetic,account_policy_codes,protection_policy_codes,
    portfolio_limit_codes,spendable_codes,capital_limit_exceeded,legacy_margin_upper_bound)
from .terms import legacy_risk_terms
from .bindings import legacy_binding,initial_binding_codes

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
        with exact_risk_arithmetic():
            try:
                terms=legacy_risk_terms(intent)
            except (ValueError,TypeError,ValidationError):
                # Preserve established diagnostic DENY ordering for malformed
                # legacy inputs. A malformed source can never acquire ALLOW.
                terms=None
            return self._evaluate(intent, book, market, policy,terms=terms)

    def evaluate_program(self,terms,compilation,allocation,program_exposure,book,policy,facts):
        """Pure generic semantic adapter into this same risk/policy core."""
        from .program_assessment import evaluate_program
        return evaluate_program(self.inputs,terms,compilation,allocation,program_exposure,book,policy,facts)

    def _evaluate(self, intent, book, market, policy,*,terms=None):
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
            if not codes and terms is None:codes.append('INVALID_LEGACY_RISK_SOURCE')
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
            for code in initial_binding_codes(legacy_binding(intent),facts,book,market,policy):reject(code)
            spec = facts.instrument
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
            for code in account_policy_codes(product=terms.scope.product if terms is not None else intent.product,
                    venue_leg=terms.scope.venue_leg if terms is not None else intent.venue_leg,
                    target=target,leverage=terms.requested_leverage if terms is not None else intent.requested_leverage,multiplier=facts.multiplier,
                    reducing=reducing,policy=policy,authorized_margin_mode=facts.authorized_margin_mode,
                    observed_margin_mode=facts.observed_margin_mode,authorized_position_mode=facts.authorized_position_mode,
                    observed_position_mode=facts.observed_position_mode):
                reject(code,plan=code.startswith('REPLAN:'))
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
                for code in protection_policy_codes(product=intent.product,
                        protection_policy=terms.protection_policy if terms is not None else intent.exit_plan.protection_policy,
                        policy=policy):reject(code)
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
                count = sum(p.actual_quantity.value != 0 for p in book.positions) + (not positions and target != 0)
                for code in portfolio_limit_codes(notional=notional,loss=loss.amount,
                        requested_loss=intent.max_planned_loss.amount,exposure=exposure,
                        existing_loss=valuation.convert(facts.existing_planned_loss,liability=True).amount,
                        group_limits=facts.group_limits,position_count=count,
                        active_order_count=len(book.active_orders)+1 if delta_value else None,policy=policy):
                    reject(code,plan=code.startswith('REPLAN:'))
            else:
                loss = money(fees.amount + funding)
            margin = (legacy_margin_upper_bound(notional_quote,intent.requested_leverage) if intent.product == 'USD_M_PERP' and not reducing
                      else notional_quote if intent.product != 'USD_M_PERP' and delta_value > 0 else D(0))
            amounts[spec.settlement_currency] = amounts.get(spec.settlement_currency, D(0)) + margin
            if intent.product != 'USD_M_PERP' and delta_value < 0:
                amounts[facts.base_asset] = amounts.get(facts.base_asset, D(0)) + abs(delta_value)
            for code in spendable_codes(required_assets=amounts,spendable=facts.spendable,book=book):reject(code)
            reserves = tuple(t.Money(amount=value, currency=asset) for asset, value in sorted(amounts.items()) if value)
            if not reducing:
                capital = valuation.convert(t.Money(amount=margin, currency=spec.settlement_currency), liability=True).amount
                if capital_limit_exceeded(capital=capital,fees=fees.amount,funding=funding,
                        capital_in_use=valuation.convert(facts.capital_in_use,liability=True).amount,policy=policy):
                    reject('REPLAN:CAPITAL', plan=True)
        except (Inexact,Rounded):
            reject('ARITHMETIC_PRECISION_UNPROVEN')
        except (ValueError, ArithmeticError) as exc:
            reject('INVALID_OR_MISSING_INPUT:' + str(exc))
        return result()
