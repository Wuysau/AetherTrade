"""Synthetic offline fixtures exercising production IP-09 logic.

MemoryLedger is a transaction/CAS test double, NOT PostgreSQL concurrency,
durability, native platform, acceptance, or qualification evidence.
"""
from contextlib import contextmanager
from dataclasses import dataclass, replace
from decimal import Decimal
import json
from pathlib import Path
from threading import RLock

from trading_system.accounts.authorization import AuthorizationBinding
from trading_system.contracts import types as t
from trading_system.contracts.wire import ContractCodec
from trading_system.intents.ingress import (
    IngressInputs, IngressRevision, IntentCompiler, ProposalIngress, revision_key,
)
from trading_system.intents.semantic_validation import BindingInputs
from trading_system.intents.target_compiler import TargetCompiler, TargetInputs
from trading_system.market.snapshots import snapshot_content_hash
from trading_system.risk.exposure import WorkingOrder
from trading_system.storage.models import SequenceConflict

D = Decimal
NOW = '2026-10-23T06:39:01Z'
BEFORE = '2026-10-23T06:39:00Z'
END = '2026-10-23T06:40:00Z'
REVIEW = '2026-10-23T06:45:00Z'


def quantity(value, unit='BASE'):
    return t.Quantity(value=D(value), unit=unit)


def price(value, currency='USDT'):
    return t.Price(value=D(value), currency=currency)


def coverage(scope):
    return t.Coverage(scope=scope, query_succeeded=True, pagination_complete=True, supported=True, missing=())


def evidence(identifier='private'):
    return t.EvidenceRef(id=identifier, hash='a' * 64, environment=t.Environment.PAPER,
                         source='ip09-offline-fixture', visible_at=BEFORE)


def position(scope, amount, unit='BASE', owner='demo-ai-strategy'):
    return t.ManagedPosition(position_id='position', scope=scope, actual_quantity=quantity(amount, unit),
        owner_strategy_id=owner, owner_book_id='book', ownership_epoch=1, cost_evidence=coverage(scope),
        realized_pnl=None, unrealized_valuation=None, active_exit_plan_id='protected', risk_locks=())


def order(scope, amount, *, unit='BASE', identifier='order', pending='NONE', certainty='CONFIRMED',
          owner='demo-ai-strategy', lifecycle=t.OrderLifecycle.ACCEPTED):
    identity = t.OrderIdentity(scope=scope, logical_client_order_id=identifier,
        native_client_order_id='native-' + identifier, venue_client_order_id='venue-' + identifier,
        venue_order_id=None)
    return t.OrderProjection(order=t.OwnedOrderRef(identity=identity, owner_strategy_id=owner, ownership_epoch=1),
        lifecycle_state=lifecycle, pending_operation=pending, command_certainty=certainty,
        outcome_provenance='VENUE_EVENT', filled_quantity=quantity('0', unit),
        leaves_quantity=quantity(amount, unit), lifecycle_revision=1, sequence=1)


def reservation(case, identifier='reservation', intent_id='older-intent', state=t.ReservationState.HELD):
    r = t.CapitalReservation(reservation_id=identifier, environment=case.book.scope.environment,
        collateral_group_id=case.binding.collateral_group_id, venue_account_id=case.book.scope.venue_account_id,
        owner_strategy_id=case.binding.authorization.authorization.owner_strategy_id, intent_id=intent_id,
        asset='USDT', amount=D('150'), reflected_amount=D(0), state=state, created_at=BEFORE,
        expires_at=END, command_refs=('command',), evidence_watermark='')
    return t.ReservationProjection(reservation=r, held=D('150'), reflected=D(0), release_pending=D(0), sequence=1)


@dataclass
class TargetCase:
    proposal: t.DecisionProposal
    context: t.DecisionContext
    book: t.BookSnapshot
    binding: BindingInputs
    target: TargetInputs

    def bind(self, proposal=None):
        return IntentCompiler(self.binding).bind(proposal or self.proposal, self.context, self.book)

    def compile(self, target='0.010', actual='0.004', working='0.003', *, side='LONG'):
        proposal = replace(self.proposal, target=replace(self.proposal.target, value=target, side=side))
        unit = self.target.instrument.quantity_step.unit
        book = replace(self.book, positions=(position(self.book.scope, actual, unit),),
            active_orders=(order(self.book.scope, working, unit=unit),) if D(working) else ())
        facts = replace(self.target, order_sides=(WorkingOrder(logical_client_order_id='order', side='BUY'),)
                        if D(working) else ())
        intent = IntentCompiler(self.binding).bind(proposal, self.context, book)
        return TargetCompiler(facts).compile(intent, book)

    def previous(self):
        intent = self.bind()
        self.binding = replace(self.binding, previous_intent=intent, previous_proposal=self.proposal)
        self.target = replace(self.target, previous_intent=intent)
        return intent


def make_case(product=t.Product.SPOT):
    path = Path(__file__).resolve().parents[2] / 'tests/fixtures/contracts/examples/decision-proposal.paper.json'
    proposal = ContractCodec().decode('decision-proposal', json.loads(path.read_text(encoding='utf-8')))
    proposal = replace(proposal, target=replace(proposal.target, value='0.010'), valid_until=END,
        entry=replace(proposal.entry, entry_deadline=END),
        exit_plan=replace(proposal.exit_plan, review_due_at=REVIEW))
    scope = t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='paper-account-demo', product=product,
        instrument_uid=proposal.instrument_ref, venue_leg='BOTH')
    ref = t.SnapshotRef(id='paper-snapshot-demo', hash='0' * 64, environment=scope.environment,
                        source='ip09-offline-fixture', visible_at=BEFORE)
    snapshot = t.SnapshotPacket(ref=ref, scope=scope,
        quote=t.Quote(bid=price('50000'), ask=price('50000'), last=price('50000'),
                      occurred_at=BEFORE, received_at=BEFORE), private_refs=(evidence(),), economic_refs=(),
        published_at=BEFORE, revision_visible_at=BEFORE, coverage=coverage(scope))
    snapshot = replace(snapshot, ref=replace(ref, hash=snapshot_content_hash(snapshot)))
    context = t.DecisionContext(identity_id='user', environment=scope.environment, deployment_id='deployment',
        authorization_id='authorization', policy_revision=1, ownership_epoch=1, scopes=(scope,),
        strategy_revision=1, decision_run_id='decision', snapshot_ref=snapshot.ref)
    grant = t.Authorization(authorization_id='authorization', owner_strategy_id='demo-ai-strategy',
        environment=scope.environment, deployment_id='deployment', scopes=(scope,),
        maximum_capital=t.Money(amount=D('10000'), currency='USDT'), policy_revision=1,
        protection_options=('NATIVE_REQUIRED',), model_cost_budget=t.Money(amount=D('10'), currency='USDT'),
        valid_from=BEFORE, expires_at=END, revoked_at=None)
    authorization = AuthorizationBinding(authorization=grant, identity_id='user', strategy_revision=1,
        ownership_epoch=1, queue_environment=scope.environment, capabilities=('NATIVE_STOP',), evidence=(evidence(),))
    unit = {t.Product.SPOT: 'BASE', t.Product.USD_M_PERP: 'CONTRACT', t.Product.US_EQUITY: 'SHARE'}[product]
    spec = t.InstrumentSpec(instrument_uid=scope.instrument_uid, venue='BINANCE', product=product,
        native_symbol='SYNTHETIC', tick_size=price('0.01'), quantity_step=quantity('0.001', unit),
        minimum_quantity=quantity('0.001', unit), minimum_notional=t.Money(amount=D('1'), currency='USDT'),
        rule_revision=1, quote_currency='USDT', settlement_currency='USDT')
    clock = t.ClockReading(utc_now=NOW, monotonic_ns=100, clock_health='HEALTHY')
    binding = BindingInputs(authorization=authorization, instrument_ref=proposal.instrument_ref,
        instrument=spec, collateral_group_id='paper-group-demo', snapshot=snapshot,
        freshness=t.DataVerdict(usable=True, age_ms=1000, missing=(), rejection_codes=()),
        evidence=(snapshot.ref, evidence()), clock=clock, deadline_monotonic_ns=1000,
        maximum_valid_until=END, max_planned_loss=t.Money(amount=D('10'), currency='USDT'),
        protection_policy='NATIVE_REQUIRED', allowed_stop_triggers=('LAST',))
    target = TargetInputs(instrument=spec, expected_instrument=spec, clock=clock, deadline_monotonic_ns=1000,
        context=context,
        session=t.SessionState(instrument_uid=scope.instrument_uid, session_id='session', state='OPEN',
            observed_at=BEFORE, next_open_at=None, evidence=evidence()))
    book = t.BookSnapshot(scope=scope, positions=(), active_orders=(), reservations=(), balances=(), sequence=1,
        basis_fingerprint='b' * 64, coverage=coverage(scope), evidence=(evidence(),))
    return TargetCase(proposal, context, book, binding, target)


class MemoryLedger:
    """Rollback and CAS orchestration probe only; deliberately not durable."""
    def __init__(self, case):
        self.book_value = case.book
        self.group_id = case.binding.collateral_group_id
        self.owner = ('demo-ai-strategy', 1, 1)
        self.records = {}
        self.lock = RLock()
        self.fail_commit = False
        self.fail_cas = False
        self.fail_append = False
        self.commits = 0
        self.active = False
        self.scope = case.book.scope
        self.projections = self
        c = case.context
        self.records[('audit_events', revision_key(c))] = (1, IngressRevision(sequence=1,
            identity_id=c.identity_id, deployment_id=c.deployment_id, authorization_id=c.authorization_id,
            owner_strategy_id='demo-ai-strategy', strategy_revision=1, risk_policy_revision=1,
            ownership_epoch=1, enabled=True))

    @contextmanager
    def transaction(self, scope):
        assert scope == self.scope
        with self.lock:
            before = self.records.copy()
            self.active = True
            try:
                yield self
                if self.fail_commit:
                    raise OSError('injected commit failure')
                self.commits += 1
            except BaseException:
                self.records = before
                raise
            finally:
                self.active = False

    def book(self, scope):
        assert self.active and scope == self.scope
        return self.book_value

    def execute(self, sql, params):
        assert self.active and 'FOR UPDATE' in sql and 'ledger.active_owners' in sql
        return self

    def fetchone(self):
        return self.owner

    def _latest(self, table, identifier, cls):
        item = self.records.get((table, identifier))
        return None if item is None else item[1]

    def _append(self, table, identifier, record, expected_sequence):
        assert self.active
        if self.fail_append and table == 'proposals':
            raise OSError('injected proposal write failure after fence CAS')
        current = self.records.get((table, identifier), (0, None))[0]
        if self.fail_cas or current != expected_sequence:
            raise SequenceConflict('injected/stale sequence')
        self.records[(table, identifier)] = (current + 1, record)


def ingress_case(case):
    ledger = MemoryLedger(case)
    service = ProposalIngress(ledger, case.book.scope,
        lambda tx, proposal, context, book: IngressInputs(binding=case.binding, target=case.target))
    return ledger, service
