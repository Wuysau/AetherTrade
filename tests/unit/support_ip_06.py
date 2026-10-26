"""Synthetic offline inputs driving the real IP-06 calculator; no venue proof."""
from dataclasses import dataclass, replace
from decimal import Decimal
import json
from pathlib import Path

from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec
from trading_system.market.snapshots import snapshot_content_hash
from trading_system.risk.evaluator import RiskEvaluator, RiskInputs, GroupLimit, EmergencyReduction
from trading_system.risk.exposure import ExposureMark

D = Decimal
NOW = '2026-10-22T12:00:00Z'
BEFORE = '2026-10-22T11:59:59Z'
END = '2026-10-22T12:01:00Z'


def money(value='0', currency='USDT'):
    return t.Money(amount=D(value), currency=currency)


def quantity(value='0', unit='CONTRACT'):
    return t.Quantity(value=D(value), unit=unit)


def price(value='100', currency='USDT'):
    return t.Price(value=D(value), currency=currency)


def evidence():
    return t.EvidenceRef(id='offline-evidence', hash='a' * 64, environment=t.Environment.PAPER,
                         source='synthetic-test', visible_at=BEFORE)


def coverage(scope):
    return t.Coverage(scope=scope, query_succeeded=True, pagination_complete=True,
                       supported=True, missing=())


def position(scope, value, unit='CONTRACT', owner='demo-ai-strategy'):
    return t.ManagedPosition(position_id='position-' + scope.venue_leg, scope=scope,
        actual_quantity=quantity(value, unit), owner_strategy_id=owner, owner_book_id='book',
        ownership_epoch=1, cost_evidence=coverage(scope), realized_pnl=None,
        unrealized_valuation=None, active_exit_plan_id='protection', risk_locks=())


@dataclass
class RiskCase:
    intent: t.TradeIntent
    book: t.BookSnapshot
    market: t.SnapshotPacket
    policy: t.RiskPolicy
    inputs: RiskInputs

    def evaluate(self, *, max_leverage=None, requested_leverage=None):
        policy = self.policy if max_leverage is None else replace(self.policy, maximum_leverage=max_leverage)
        intent = self.intent if requested_leverage is None else replace(self.intent, requested_leverage=requested_leverage)
        return RiskEvaluator(self.inputs).evaluate(intent, self.book, self.market, policy)

    def quote(self, bid, ask, *, occurred=BEFORE):
        currency = self.inputs.instrument.quote_currency
        market = replace(self.market, quote=t.Quote(bid=price(bid, currency), ask=price(ask, currency),
            last=price(bid, currency), occurred_at=occurred, received_at=occurred))
        self.market = replace(market, ref=replace(market.ref, hash=snapshot_content_hash(market)))
        self.inputs = replace(self.inputs, snapshot_ref=self.market.ref)

    def exiting(self, value='2', *, emergency=True):
        self.book = replace(self.book, positions=(position(self.book.scope, value, self.intent.quantity_unit),))
        self.intent = replace(self.intent, target_side='FLAT', target_quantity='0')
        if emergency:
            self.inputs = replace(self.inputs, emergency=EmergencyReduction(
                maximum_quantity=quantity(value, self.intent.quantity_unit), allowed_order_types=('MARKET', 'LIMIT'),
                reference_price=price('100', self.inputs.instrument.quote_currency),
                maximum_slippage_bps=D('500'), maximum_quote_age_ms=10000))
        return self


def make_case(product=t.Product.USD_M_PERP):
    currency = 'USD' if product == t.Product.US_EQUITY else 'USDT'
    unit = {t.Product.USD_M_PERP: 'CONTRACT', t.Product.SPOT: 'BASE', t.Product.US_EQUITY: 'SHARE'}[product]
    root = Path(__file__).resolve().parents[2] / 'tests/fixtures/contracts/examples'
    codec = ContractCodec()
    intent = codec.decode('trade-intent', json.loads((root / 'trade-intent.paper.json').read_text(encoding='utf-8')))
    policy = codec.decode('risk-policy', json.loads((root / 'risk-policy.paper.json').read_text(encoding='utf-8')))
    intent = replace(intent, product=product.value, quantity_unit=unit, target_quantity='2',
        requested_leverage=2 if product == t.Product.USD_M_PERP else 1,
        as_of=BEFORE, created_at=BEFORE, valid_until=END,
        execution=replace(intent.execution, order_preference='MARKET', limit_price=None,
                          maximum_entry_slippage_bps='20', entry_deadline=END),
        exit_plan=replace(intent.exit_plan, stop_price='90', review_due_at=END),
        max_planned_loss=replace(intent.max_planned_loss, amount='100', currency=currency))
    policy = replace(policy, valuation_currency=currency, maximum_capital='100000', cash_buffer='100',
        maximum_order_notional='10000', maximum_planned_loss_per_intent='100', maximum_total_planned_loss='1000',
        maximum_gross_exposure='10000', maximum_net_exposure='10000', maximum_leverage=5,
        maximum_entry_slippage_bps='20')
    scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id=intent.venue_account_id,
        product=product, instrument_uid=intent.instrument_uid, venue_leg='BOTH')
    ref = t.SnapshotRef(id=intent.snapshot_id, hash='0' * 64, environment=t.Environment.PAPER,
                       source='synthetic-test', visible_at=BEFORE)
    market = t.SnapshotPacket(ref=ref, scope=scope,
        quote=t.Quote(bid=price('100', currency), ask=price('100', currency), last=price('100', currency),
                      occurred_at=BEFORE, received_at=BEFORE), private_refs=(), economic_refs=(),
        published_at=BEFORE, revision_visible_at=BEFORE, coverage=coverage(scope))
    market = replace(market, ref=replace(ref, hash=snapshot_content_hash(market)))
    balances = (money('100000', currency), money('10', 'ASSET'))
    book = t.BookSnapshot(scope=scope, positions=(), active_orders=(), reservations=(), balances=balances,
        sequence=1, basis_fingerprint=intent.basis_fingerprint, coverage=coverage(scope), evidence=(evidence(),))
    spec = t.InstrumentSpec(instrument_uid=intent.instrument_uid, venue='BINANCE', product=product,
        native_symbol='SYNTHETIC', tick_size=price('0.01', currency), quantity_step=quantity('0.1', unit),
        minimum_quantity=quantity('0.1', unit), minimum_notional=money('1', currency), rule_revision=1,
        quote_currency=currency, settlement_currency=currency)
    inputs = RiskInputs(intent_id=intent.intent_id, authorization_id=intent.authorization_id,
        owner_strategy_id=intent.owner_strategy_id, ownership_epoch=1, policy_revision=1, scope=scope,
        collateral_group_id=intent.collateral_group_id, basis_fingerprint=book.basis_fingerprint,
        book_sequence=1, snapshot_ref=market.ref,
        clock=t.ClockReading(utc_now=NOW, monotonic_ns=1000000000, clock_health='HEALTHY'), expires_at=END,
        instrument=spec, session=t.SessionState(instrument_uid=intent.instrument_uid, session_id='session',
            state='OPEN', observed_at=BEFORE, next_open_at=None, evidence=evidence()),
        multiplier=D('1'), base_asset='ASSET', fee_reserve=(money('2', currency),),
        funding_reserve=(money('1', currency),), existing_planned_loss=money('0', currency),
        capital_in_use=money('0', currency), spendable=balances, fx_rates=(),
        marks=(ExposureMark(scope=scope, price=price('100', currency), multiplier=D(1), unit=unit, related_group='related'),),
        order_sides=(), group_limits=(GroupLimit(group_id='related', maximum_gross=money('10000', currency)),),
        authorized_margin_mode='ISOLATED', observed_margin_mode='ISOLATED',
        authorized_position_mode='ONE_WAY', observed_position_mode='ONE_WAY', new_risk_allowed=True,
        allow_round_down=False, emergency=None, evidence=(evidence(),))
    return RiskCase(intent, book, market, policy, inputs)
