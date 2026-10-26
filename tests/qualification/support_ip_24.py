"""Synthetic offline inputs only. Never native/platform qualification evidence."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from trading_system.contracts import types as t
from trading_system.engine.nautilus import binance_products as b

NOW = '2026-10-23T00:00:00Z'
END = '2026-10-23T00:01:00Z'


class ProductCase:
    def __init__(self, product=t.Product.SPOT):
        spot = product == t.Product.SPOT
        self.scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='offline',
            product=product, instrument_uid='BTC-' + product.value, venue_leg='BOTH')
        self.ref = t.EvidenceRef(id='synthetic', hash='a' * 64,
            environment=t.Environment.PAPER, source='offline-fixture', visible_at=NOW)
        self.coverage = t.Coverage(scope=self.scope, query_succeeded=True,
            pagination_complete=True, supported=True, missing=())
        self.instrument = t.InstrumentSpec(instrument_uid=self.scope.instrument_uid,
            venue='BINANCE', product=product, native_symbol='BTCUSDT',
            tick_size=t.Price(value=D('0.1'), currency='USDT'),
            quantity_step=t.Quantity(value=D('0.001'), unit='BTC'),
            minimum_quantity=t.Quantity(value=D('0.001'), unit='BTC'),
            minimum_notional=t.Money(amount=D('5'), currency='USDT'), rule_revision=1,
            quote_currency='USDT', settlement_currency='USDT')
        self.rules = b.BinanceRules(scope=self.scope, instrument=self.instrument, revision=1,
            observed_at=NOW, valid_until=END, evidence=(self.ref,), maximum_quantity=D('100'),
            minimum_price=D('0.1'), maximum_price=D('1000000'), maximum_notional=D('10000000'),
            fee_fraction=D('0.001'), fee_asset='USDT', maximum_leverage=D(1 if spot else 10),
            order_types=('LIMIT', 'MARKET', 'STOP_LOSS', 'STOP_MARKET'),
            rate_budgets=(b.RateBudget(key='account-window', limit=10, used=0, request_cost=1),),
            market_quantity_step=D('0.001'), market_minimum_quantity=D('0.001'),
            market_maximum_quantity=D('100'))
        self.state = b.BinanceTradingState(scope=self.scope, observed_at=NOW, valid_until=END,
            coverage=self.coverage, evidence=(self.ref,), balances=(
                b.BinanceBalance(asset='BTC', total=D('1'), available=D('1')),
                b.BinanceBalance(asset='USDT', total=D('10000'), available=D('10000'))),
            actual_quantity=D('1'), owned_quantity=D('1'), pending_exit_quantity=D(0),
            held_base=D(0), held_quote=D(0), position_mode='CASH' if spot else 'ONE_WAY',
            margin_mode='CASH' if spot else 'ISOLATED', actual_leverage=D(1 if spot else 2))
        self.request = b.BinanceOrderCheck(scope=self.scope, side='BUY',
            quantity=t.Quantity(value=D('0.01'), unit='BTC'),
            price=t.Price(value=D('50000'), currency='USDT'), order_type='LIMIT', rule_revision=1,
            requested_leverage=self.state.actual_leverage, authorized_maximum_leverage=D(10),
            authorized_position_mode=self.state.position_mode,
            authorized_margin_mode=self.state.margin_mode, reduce=False)
        self.shard = t.ExecutionShard(environment=self.scope.environment,
            venue_account_id=self.scope.venue_account_id, product=product,
            node_id='offline-node', execution_epoch=1)
        self.lease = t.OwnerLease(shard=self.shard, instance_id='offline-owner',
            expires_at=END, deadline_monotonic_ns=1000)
        self.clock = t.ClockReading(utc_now=NOW, monotonic_ns=1, clock_health='HEALTHY')
        self.query = t.AccountQuery(scope=self.scope, session_id='session', as_of=NOW)
        self.response = b.BinanceAccountRead(scope=self.scope, session_id='session', as_of=NOW,
            coverage=self.coverage, evidence=(self.ref,), sections=b.COMMON_READ_SECTIONS +
            (('CASH_ALLOCATIONS',) if spot else ('POSITIONS', 'POSITION_MODE', 'MARGIN_MODE', 'LEVERAGE')),
            balances=self.state.balances, positions=(), active_orders=(), reservations=(), sequence=1,
            position_mode=self.state.position_mode, margin_mode=self.state.margin_mode,
            leverage=self.state.actual_leverage)
        self.reads = []

    def check(self, **changes):
        return b.check_order(replace(self.request, **changes), self.state, self.rules, now=NOW)

    def authorized_isolated_but_actual_cross(self):
        other = ProductCase(t.Product.USD_M_PERP)
        other.state = replace(other.state, margin_mode='CROSS')
        return other.check()

    def read(self, query):
        self.reads.append(query)
        return self.response

    def reader(self, read=None):
        return b.BinanceAccountReader(shard=self.shard, instance_id='offline-owner',
            instrument=self.instrument, lease=lambda: self.lease,
            read=read or self.read, clock=lambda: self.clock)

    def capability(self):
        return t.CapabilityProfile(scope=self.scope, build_digest='b' * 64,
            status=t.CapabilityStatus.UNVERIFIED, capabilities=(), limitations=('NOT_RUN',),
            evidence=(self.ref,), valid_from=NOW, valid_until=END)

    def build(self):
        return t.QualifiedBuild(candidate=t.CandidateSpec(candidate_id='offline',
            review_ref='1' * 40, deployment_ref='2' * 40, source_digest='3' * 64,
            build_environment=t.BuildEnvironment(os='linux', arch='x86_64', python_abi='cp312',
                dependency_digest='4' * 64, image_digest='5' * 64)), artifact_digest='b' * 64,
            probe_results=(), qualification_ref=self.ref)


@pytest.fixture
def product_case():
    return ProductCase()
