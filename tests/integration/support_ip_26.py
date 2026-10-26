"""Synthetic offline evidence drives production logic; not venue qualification."""
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_system.contracts import types as t
from trading_system.equities.cash_book import AvailabilityProof, CashBook
from trading_system.equities.corporate_actions import ActionProof, EquityBook, EquityState
from trading_system.equities.sessions import MarketSessionService, SessionEvidence
from trading_system.protection.hosted_cash import HostedCashProtection, HostedInputs
from trading_system.protection.native import digest


STAMP = '2026-10-23T14:00:00Z'
END = '2026-10-23T15:00:00Z'
START = '2026-10-23T13:00:00Z'


def qty(value):
    return t.Quantity(value=Decimal(value), unit='share')


def money(value):
    return t.Money(amount=Decimal(value), currency='USD')


def price(value):
    return t.Price(value=Decimal(value), currency='USD')


class EquityCase:
    def __init__(self):
        self.scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='offline-equity',
            product=t.Product.US_EQUITY, instrument_uid='equity:ABC', venue_leg='BOTH')
        self.ref = t.EvidenceRef(id='synthetic-evidence', hash='a' * 64,
            environment=self.scope.environment, source='synthetic-offline', visible_at=STAMP)
        self.now = t.ClockReading(utc_now=STAMP, monotonic_ns=20_000_000_000, clock_health='HEALTHY')
        self.coverage = t.Coverage(scope=self.scope, query_succeeded=True, pagination_complete=True,
            supported=True, missing=())
        self.instrument = t.InstrumentSpec(instrument_uid=self.scope.instrument_uid, venue='OFFLINE',
            product=self.scope.product, native_symbol='ABC', tick_size=price('.01'),
            quantity_step=qty('1'), minimum_quantity=qty('1'), minimum_notional=money('1'),
            rule_revision=1, quote_currency='USD', settlement_currency='USD')
        self.position = t.ManagedPosition(position_id='position-1', scope=self.scope,
            actual_quantity=qty('100'), owner_strategy_id='owner-1', owner_book_id='book-1',
            ownership_epoch=1, cost_evidence=self.coverage, realized_pnl=None,
            unrealized_valuation=None, active_exit_plan_id=None, risk_locks=())
        self.plan = t.ProtectionPlan(plan_id='plan-1', mode='HOSTED', scope=self.scope,
            coverage_quantity=qty('100'), stop_price=price('100'), stop_trigger='LAST',
            native_order_ids=(), monitor_id='monitor-1', state=t.ProtectionState.PENDING,
            last_verified_at=None, coverage_gap_since=None, replacement_revision=1,
            on_gap_policy='LOCK_AND_ALERT')
        self.report = t.VenueReport(coverage=self.coverage, orders=(), positions=(self.position,),
            balances=(money('10000'),), fills=(), evidence=(self.ref,), next_cursor=None, as_of=STAMP)
        self.session_row = SessionEvidence(scope=self.scope, instrument_revision=1,
            calendar_revision='calendar-revision-9', session_id='regular', starts_at=START,
            ends_at=END, state='OPEN', observed_at=STAMP, next_open_at=None, evidence=self.ref)
        self.sessions = MarketSessionService(scope=self.scope, read_session=lambda *_: self.session_row)
        self.session = self.sessions.state(self.instrument, self.now)
        self.availability = AvailabilityProof(scope=self.scope, report_hash=digest(self.report),
            session_hash=digest(self.session), instrument_hash=digest(self.instrument),
            rule_revision=1, session_id='regular', valid_until=END, settled_available=money('6000'),
            unsettled=money('4000'), sellable=qty('40'), position_id=self.position.position_id,
            ownership_epoch=1, reservations_reconciled=True, evidence=self.ref)
        self.cash = CashBook(read_proof=lambda *_: self.availability)
        self.market = t.SnapshotPacket(ref=t.SnapshotRef(id='market', hash='b' * 64,
            environment=self.scope.environment, source='synthetic-offline', visible_at=STAMP),
            scope=self.scope, quote=t.Quote(bid=price('109'), ask=price('111'), last=price('110'),
                occurred_at=STAMP, received_at=STAMP), private_refs=(), economic_refs=(),
            published_at=STAMP, revision_visible_at=STAMP, coverage=self.coverage)
        self.authorization = t.Authorization(authorization_id='explicit-cash', owner_strategy_id='owner-1',
            environment=self.scope.environment, deployment_id='offline', scopes=(self.scope,),
            maximum_capital=money('20000'), policy_revision=3,
            protection_options=('HOSTED_ALLOWED_CASH_ONLY',), model_cost_budget=money('0'),
            valid_from=START, expires_at=END, revoked_at=None)
        self.inputs = HostedInputs(plan_hash=digest(self.plan), market_hash=digest(self.market),
            session_hash=digest(self.session), position=self.position, authorization=self.authorization,
            policy_revision=3, fully_cash_funded=True, capital_at_risk=money('11000'),
            instrument_cap=money('15000'), allowed_sessions=('regular',), sellable=qty('40'),
            heartbeat_monotonic_ns=19_000_000_000, heartbeat_monitor_id='monitor-1',
            network_healthy=True, evidence=self.ref, exit_latched=False)
        self.observations = []
        self.fail_record = False
        self.hosted = self.new_hosted()
        self.action_proof = None
        self.book = EquityBook(EquityState(instrument=self.instrument, position=self.position,
            protection=self.plan), clock=lambda: self.now.utc_now,
            read_proof=lambda *_: self.action_proof)

    def new_hosted(self):
        def read(*_):
            latched = self.inputs.exit_latched or any(o.exit_latched for o in self.observations)
            return replace(self.inputs, exit_latched=latched)

        def record(observation):
            if self.fail_record:
                raise OSError('synthetic persistence failure')
            self.observations.append(observation)
            return True

        return HostedCashProtection(clock=lambda: self.now, read_inputs=read, record=record)

    def rebind(self):
        self.inputs = replace(self.inputs, plan_hash=digest(self.plan),
            market_hash=digest(self.market), session_hash=digest(self.session))

    def tick(self):
        return self.hosted.tick(self.plan, self.market, self.session)

    def require(self, **kwargs):
        return self.cash.require(report=self.report, position=self.position, instrument=self.instrument,
            session=self.session, now=self.now, **kwargs)

    def stop_triggered_while_halted(self):
        self.session = replace(self.session, state='HALTED', next_open_at=END)
        self.market = replace(self.market, quote=replace(self.market.quote, last=price('90')))
        self.rebind()
        self.tick()
        return self.observations[-1]

    def action(self, kind='SPLIT', *, ratio=Decimal('2'), after_qty='200', after_stop='50',
               new_uid=None, cashflow=None):
        action = t.CorporateAction(action_id='action-1', scope=self.scope, kind=kind,
            effective_at=STAMP, ratio=ratio, cashflow=cashflow, evidence=self.ref)
        target = replace(self.instrument, rule_revision=2)
        if new_uid:
            target = replace(target, instrument_uid=new_uid, native_symbol='XYZ')
        scope = replace(self.scope, instrument_uid=target.instrument_uid)
        p = replace(self.position, scope=scope, actual_quantity=qty(after_qty),
                    cost_evidence=replace(self.coverage, scope=scope))
        plan = replace(self.plan, scope=scope, coverage_quantity=qty(after_qty), stop_price=price(after_stop),
            state=t.ProtectionState.GAP, last_verified_at=None, coverage_gap_since=STAMP,
            replacement_revision=2)
        report = replace(self.report, coverage=replace(self.coverage, scope=scope), positions=(p,))
        self.action_proof = ActionProof(action_hash=digest(action), report_hash=digest(report),
            before_hash=digest(self.book.state), rule_revision=1, after_instrument=target,
            after_position=p, after_protection=plan, cashflows=(cashflow,) if cashflow else ())
        return action, report


@pytest.fixture
def equity_case():
    return EquityCase()
