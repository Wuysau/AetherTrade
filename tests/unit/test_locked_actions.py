"""Synthetic local proof probes; no database/native/venue qualification."""
from contextlib import contextmanager
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from jsonschema.exceptions import ValidationError

from trading_system.accounts.authorization import AuthorizationBinding
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.wire import ABSENT, ContractCodec
from trading_system.execution.commands import CommandRepository, command_hash
from trading_system.execution.dispatcher import CommandDispatcher, DispatchDenied, DispatchFacts, REQUIRED_CHECKS
from trading_system.storage.models import SequenceConflict
from trading_system.risk.locked_actions import WorkingTerms
from trading_system.risk.reservations import ReservationInputs, ReservationService
from unit.support_ip_06 import make_case, money, NOW, BEFORE, END


def digest(value):
    return canonical_hash(domain_payload(value))


class LockedCase:
    def __init__(self, permission=t.RiskPermission.NO_NEW_RISK, product=t.Product.USD_M_PERP):
        self.risk = make_case(product).exiting(emergency=False)
        self.intent, self.book = self.risk.intent, self.risk.book
        self.scope = self.book.scope
        raw = json.loads((Path(__file__).parents[1] / 'fixtures/contracts/examples/'
                          'execution-command.paper.json').read_text(encoding='utf-8'))
        command = ContractCodec().decode('execution-command', raw)
        payload = replace(command.canonical_payload, side='SELL', quantity='2',
                          order_type='MARKET', price=ABSENT, reduce_only=product == t.Product.USD_M_PERP,
                          tokenize=False if product == t.Product.US_EQUITY else ABSENT)
        self.command = replace(command, intent_id=self.intent.intent_id,
            product=self.intent.product, venue_account_id=self.intent.venue_account_id,
            canonical_payload=payload, operation='CLOSE', created_at=BEFORE)
        self.rehash()
        self.owner = (self.intent.owner_strategy_id, self.intent.ownership_epoch,
                      self.intent.risk_policy_revision)
        self.locks = (t.RiskLock(lock_id='daily', scope=self.scope, cause='DAILY_LOSS',
            state='ACTIVE', permission=permission, required_evidence=('REVIEW',), raised_at=BEFORE),)
        self.unresolved = []
        self.queued = []
        self.terms = ()
        self.plan = None
        self.proof_transform = lambda p: p
        self.proof_calls = 0
        self.tx = SimpleNamespace(scope=self.scope, group_id=self.intent.collateral_group_id,
            transaction_id='fixture-transaction',
            projections=SimpleNamespace(book=lambda scope: self.book),
            risk_locks=SimpleNamespace(active=lambda scope: self.locks),
            _latest=self.latest, execute=self.execute)
        self.lease = t.OwnerLease(shard=t.ExecutionShard(environment=self.scope.environment,
            venue_account_id=self.scope.venue_account_id, product=self.scope.product,
            node_id='fixture-node', execution_epoch=self.command.execution_epoch),
            instance_id='fixture-owner', expires_at=END, deadline_monotonic_ns=100)
        self.binding = AuthorizationBinding(authorization=t.Authorization(
            authorization_id=self.intent.authorization_id, owner_strategy_id=self.intent.owner_strategy_id,
            environment=self.scope.environment, deployment_id='fixture', scopes=(self.scope,),
            maximum_capital=money('10000'), policy_revision=1,
            protection_options=('NATIVE_REQUIRED',), model_cost_budget=money('1'),
            valid_from=BEFORE, expires_at=END, revoked_at=None), identity_id='fixture-user',
            strategy_revision=self.intent.strategy_revision, ownership_epoch=1,
            queue_environment=self.scope.environment, capabilities=('fixture',), evidence=self.book.evidence)
        self.dispatcher = CommandDispatcher(None, self.scope, load_facts=self.facts,
            clock=lambda: t.ClockReading(utc_now=NOW, monotonic_ns=1, clock_health='HEALTHY'))
        self.dispatcher.load_action_proof = self.proof

    def rehash(self):
        self.command = replace(self.command, payload_hash=canonical_hash(
            ContractCodec().encode(self.command)['canonical_payload']))

    def latest(self, table, key, cls):
        if table == 'intents':
            return self.intent
        if table == 'protection_plans':
            return self.plan
        return None

    def execute(self, sql, args=()):
        rows = self.queued if "='DURABLE_READY'" in sql else self.unresolved
        return SimpleNamespace(fetchone=lambda: self.owner, fetchall=lambda: rows)

    def facts(self, tx, command, intent):
        return DispatchFacts(binding=self.binding, lease=self.lease,
            request_hash=command_hash(command), valid_until=END,
            deadline_monotonic_ns=100, checks=REQUIRED_CHECKS | {'OWNED_TARGET'})

    def proof(self, tx, command, intent, book):
        # This reader is a synthetic server-side fixture, never a request field.
        from trading_system.risk.locked_actions import ActionProof
        self.proof_calls += 1
        report = t.VenueReport(coverage=book.coverage, positions=book.positions,
            orders=book.active_orders, balances=book.balances, fills=(),
            evidence=book.evidence, next_cursor=None, as_of=NOW)
        return self.proof_transform(ActionProof(transaction_id=tx.transaction_id,
            request_hash=command_hash(command),
            intent_hash=digest(intent), book_hash=digest(book), report=report,
            instrument=self.risk.inputs.instrument, orders=self.terms, valid_until=END))

    def check(self):
        return self.dispatcher._check(self.tx, self.command, self.lease)


@pytest.mark.parametrize('permission', [t.RiskPermission.NO_NEW_RISK, t.RiskPermission.EXIT_ONLY])
def test_locked_dispatch_allows_verified_owned_reduction(permission):
    case = LockedCase(permission)
    case.check()
    assert case.proof_calls == 1


def protection(case):
    case.plan = t.ProtectionPlan(plan_id='protection', mode='NATIVE', scope=case.scope,
        coverage_quantity=case.book.positions[0].actual_quantity, stop_price=case.risk.inputs.instrument.tick_size,
        stop_trigger='LAST', native_order_ids=(), monitor_id=None, state=t.ProtectionState.GAP,
        last_verified_at=None, coverage_gap_since=BEFORE, replacement_revision=0, on_gap_policy='EXIT')
    case.plan = replace(case.plan, stop_price=replace(case.plan.stop_price, value=money('90').amount))
    case.command = replace(case.command, operation='SET_PROTECTION', canonical_payload=replace(
        case.command.canonical_payload, order_type='STOP_MARKET', trigger_price='90'))
    case.rehash()


def working(case, leaves='0.5', side='SELL'):
    from unit.support_ip_06 import quantity
    order = t.OwnedOrderRef(identity=t.OrderIdentity(scope=case.scope,
        logical_client_order_id='working', native_client_order_id='native-working',
        venue_client_order_id='venue-working', venue_order_id='order-working'),
        owner_strategy_id=case.intent.owner_strategy_id, ownership_epoch=case.intent.ownership_epoch)
    projection = t.OrderProjection(order=order, lifecycle_state=t.OrderLifecycle.ACCEPTED,
        pending_operation='NONE', command_certainty='CONFIRMED', outcome_provenance='VENUE_QUERY',
        filled_quantity=quantity('0'), leaves_quantity=quantity(leaves), lifecycle_revision=1, sequence=1)
    case.book = replace(case.book, active_orders=(projection,))
    case.terms = (WorkingTerms(order=order, side=side, evidence=case.book.evidence[0]),)


def test_protection_repair_requires_no_new_risk_permission_and_durable_gap():
    case = LockedCase()
    protection(case)
    case.check()
    case.locks = (replace(case.locks[0], permission=t.RiskPermission.EXIT_ONLY),)
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('fault', ['missing', 'verified', 'wrong_scope', 'wrong_plan', 'wrong_stop',
                                  'mark_trigger', 'no_gap_time', 'oversized'])
def test_protection_operation_name_cannot_prove_repair(fault):
    case = LockedCase()
    protection(case)
    if fault == 'missing':
        case.plan = None
    elif fault == 'verified':
        case.plan = replace(case.plan, state=t.ProtectionState.VERIFIED_NATIVE)
    elif fault == 'wrong_scope':
        case.plan = replace(case.plan, scope=replace(case.scope, venue_account_id='foreign'))
    elif fault == 'wrong_plan':
        case.plan = replace(case.plan, plan_id='foreign')
    elif fault == 'wrong_stop':
        case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, trigger_price='91'))
    elif fault == 'mark_trigger':
        case.plan = replace(case.plan, stop_trigger='MARK')
        case.intent = replace(case.intent, exit_plan=replace(case.intent.exit_plan, stop_trigger='MARK'))
    elif fault == 'no_gap_time':
        case.plan = replace(case.plan, coverage_gap_since=None)
    else:
        case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity='3'))
    case.rehash()
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('forged', [None, True, {'reduce_only': True}, 'PROTECTION', 'EXIT'])
def test_missing_and_caller_assertions_are_not_proof(forged):
    case = LockedCase()
    case.dispatcher.load_action_proof = lambda *args: forged
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('field', ['transaction_id', 'request_hash', 'intent_hash', 'book_hash', 'valid_until'])
def test_exact_proof_binding_and_expiry(field):
    case = LockedCase()
    case.proof_transform = lambda proof: replace(proof, **{field: BEFORE if field == 'valid_until' else 'wrong'})
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('fault', ['stale', 'future', 'coverage', 'evidence', 'position', 'balance', 'cursor'])
def test_report_must_be_fresh_complete_and_match_ledger(fault):
    case = LockedCase()
    def change(proof):
        report = proof.report
        changes = {'stale': {'as_of': '2026-10-22T11:58:00Z'}, 'future': {'as_of': END},
            'coverage': {'coverage': replace(report.coverage, pagination_complete=False)},
            'evidence': {'evidence': ()}, 'position': {'positions': ()},
            'balance': {'balances': ()}, 'cursor': {'next_cursor': 'more'}}
        return replace(proof, report=replace(report, **changes[fault]))
    case.proof_transform = change
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('field,value', [
    ('side', 'BUY'),
    ('quantity', '2.1'), ('quantity', '1.99'), ('reduce_only', False),
    ('quote_notional', '2'), ('full_position_exit', True), ('position_side', 'SHORT'),
    ('trigger_price', '90'), ('order_type', 'STOP_MARKET'),
])
def test_closing_payload_must_prove_bounded_correct_direction(field, value):
    case = LockedCase()
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, **{field: value}))
    case.rehash()
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('quantity', ['0', '-1', 'NaN'])
def test_invalid_quantity_is_rejected_by_existing_wire_contract(quantity):
    case = LockedCase()
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity=quantity))
    with pytest.raises(ValidationError):
        case.rehash()


@pytest.mark.parametrize('fault', ['owner', 'epoch', 'unit', 'flat', 'duplicate', 'short_leg'])
def test_position_ownership_direction_and_units_are_required(fault):
    case = LockedCase()
    p = case.book.positions[0]
    if fault == 'owner':
        p = replace(p, owner_strategy_id='foreign')
    elif fault == 'epoch':
        p = replace(p, ownership_epoch=2)
    elif fault == 'unit':
        p = replace(p, actual_quantity=replace(p.actual_quantity, unit='BASE'))
    elif fault == 'flat':
        p = replace(p, actual_quantity=replace(p.actual_quantity, value=money('0').amount))
    elif fault == 'short_leg':
        p = replace(p, scope=replace(case.scope, venue_leg='SHORT'))
    case.book = replace(case.book, positions=(p, p) if fault == 'duplicate' else (p,))
    with pytest.raises(DispatchDenied):
        case.check()


def test_existing_working_reductions_consume_reducible_quantity():
    case = LockedCase()
    working(case)
    with pytest.raises(DispatchDenied):
        case.check()  # 2 queued + .5 working > 2 actual
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity='1.5'))
    case.rehash()
    case.check()


def test_tiny_working_remainder_is_not_rounded_out_of_reducible_quantity():
    case = LockedCase()
    working(case, leaves='0.' + '0' * 90 + '1')
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('fault', ['missing_terms', 'wrong_side', 'unknown', 'cancel_pending', 'foreign_owner'])
def test_unverified_working_orders_block_exit(fault):
    case = LockedCase()
    working(case)
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity='1'))
    case.rehash()
    order = case.book.active_orders[0]
    if fault == 'missing_terms':
        case.terms = ()
    elif fault == 'wrong_side':
        case.terms = (replace(case.terms[0], side='BUY'),)
    elif fault == 'unknown':
        order = replace(order, command_certainty='UNKNOWN')
    elif fault == 'cancel_pending':
        order = replace(order, pending_operation='CANCEL')
    else:
        order = replace(order, order=replace(order.order, owner_strategy_id='foreign'))
        case.terms = (replace(case.terms[0], order=order.order),)
    case.book = replace(case.book, active_orders=(order,))
    with pytest.raises(DispatchDenied):
        case.check()


def test_unknown_and_queued_commands_cannot_spend_same_position(monkeypatch):
    case = LockedCase()
    monkeypatch.setattr(CommandRepository, 'unresolved_in_group', lambda *args: ('unknown',))
    with pytest.raises(DispatchDenied):
        case.check()
    assert case.proof_calls == 0
    monkeypatch.setattr(CommandRepository, 'unresolved_in_group', lambda *args: ())
    case.queued = [('another-queued-reduction',)]
    with pytest.raises(DispatchDenied):
        case.check()
    assert case.proof_calls == 0


@pytest.mark.parametrize('permission', [t.RiskPermission.EXIT_ONLY, t.RiskPermission.RECOVERY_REQUIRED])
def test_lock_intersection_uses_strictest_permission(permission):
    case = LockedCase()
    protection(case)
    case.locks += (replace(case.locks[0], lock_id='stricter', permission=permission),)
    with pytest.raises(DispatchDenied):
        case.check()


def test_recovery_lock_and_reservation_unknown_never_allow_write():
    case = LockedCase(t.RiskPermission.RECOVERY_REQUIRED)
    with pytest.raises(DispatchDenied):
        case.check()
    case.locks = (replace(case.locks[0], permission=t.RiskPermission.NO_NEW_RISK,
                          cause='RESERVATION_COMMAND_UNKNOWN'),)
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('fault', ['owner', 'revoked', 'execution_epoch', 'authorization_epoch', 'checks'])
def test_verified_exit_preserves_authority_gates(fault):
    case = LockedCase()
    if fault == 'owner':
        case.owner = ('foreign', 1, 1)
    elif fault == 'revoked':
        case.binding = replace(case.binding, authorization=replace(case.binding.authorization, revoked_at=NOW))
    elif fault == 'execution_epoch':
        case.lease = replace(case.lease, shard=replace(case.lease.shard, execution_epoch=2))
    elif fault == 'authorization_epoch':
        case.binding = replace(case.binding, ownership_epoch=2)
    else:
        facts = case.facts
        case.dispatcher.load_facts = lambda *args: replace(facts(*args), checks=frozenset({'EXIT'}))
    with pytest.raises((DispatchDenied, SequenceConflict)):
        case.check()


def reservation_case(permission=t.RiskPermission.NO_NEW_RISK):
    case = LockedCase(permission)
    case.writes = []
    case.tx._latest = lambda *args: None
    case.tx._append = lambda *args: case.writes.append(args)
    case.tx.intents = SimpleNamespace(append=lambda *args: case.writes.append(('intent', *args)))
    case.tx.commands = SimpleNamespace(append=lambda *args: case.writes.append(('command', *args)))
    case.tx.reservations = SimpleNamespace(append=lambda *args: case.writes.append(('reservation', *args)))
    def inputs(tx, intent, book):
        return ReservationInputs(risk=replace(case.risk.inputs, new_risk_allowed=False),
            market=case.risk.market, policy=case.risk.policy,
            authorization=t.AuthorizationDecision(allowed=True, authorization_id=intent.authorization_id,
                rejection_codes=(), evidence=book.evidence), command=case.command,
            group_capacity=book.balances, strategy_capacity=book.balances,
            buffer=tuple(replace(m, amount=money('0').amount) for m in book.balances))
    case.service = ReservationService(None, case.scope, inputs, clock=lambda: NOW,
                                      load_action_proof=case.proof)
    return case


@pytest.mark.parametrize('permission', [t.RiskPermission.NO_NEW_RISK, t.RiskPermission.EXIT_ONLY])
def test_reservation_uses_verified_reduction_policy_and_preserves_holds(permission):
    case = reservation_case(permission)
    result = case.service._reserve(case.tx, case.intent, 1)
    assert result.accepted, result.rejection_codes
    assert [p.held for p in result.reservations] == [money('3').amount]
    assert any(row[0] == 'intent' for row in case.writes)
    assert any(row[0] == 'command' for row in case.writes)
    assert case.proof_calls == 1


@pytest.mark.parametrize('fault', ['missing_proof', 'wrong_side', 'oversized', 'new_risk',
                                  'unknown', 'owner', 'authorization', 'capacity', 'recovery'])
def test_locked_reservation_denials_write_no_bundle(fault, monkeypatch):
    case = reservation_case()
    if fault == 'missing_proof':
        case.service.load_action_proof = None
    elif fault == 'wrong_side':
        case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, side='BUY'))
        case.rehash()
    elif fault == 'oversized':
        case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity='3'))
        case.rehash()
    elif fault == 'new_risk':
        case.book = replace(case.book, positions=())
        case.intent = replace(case.intent, target_quantity='2', target_side='LONG')
        case.command = replace(case.command, operation='SUBMIT', canonical_payload=replace(
            case.command.canonical_payload, side='BUY', reduce_only=False))
        case.rehash()
    elif fault == 'unknown':
        monkeypatch.setattr(CommandRepository, 'unresolved_in_group', lambda *args: ('unknown',))
    elif fault == 'owner':
        case.owner = ('foreign', 1, 1)
    elif fault == 'authorization':
        load = case.service.load_inputs
        case.service.load_inputs = lambda *args: replace(load(*args), authorization=replace(
            load(*args).authorization, allowed=False))
    elif fault == 'capacity':
        load = case.service.load_inputs
        case.service.load_inputs = lambda *args: replace(load(*args), group_capacity=())
    else:
        case.locks = (replace(case.locks[0], permission=t.RiskPermission.RECOVERY_REQUIRED),)
    result = case.service._reserve(case.tx, case.intent, 1)
    assert not result.accepted
    assert not case.writes


class MemoryCommands(CommandRepository):
    """Only lifecycle probe support; SQL durability is explicitly NOT tested."""
    def get(self, command_id):
        return self.tx.case.command

    def mark_may_have_sent(self, tx, command_id, expected_state):
        case = tx.case
        self.validate_binding(case.command)
        assert case.command.state == expected_state
        case.command = replace(case.command, state='MAY_HAVE_SENT', durable_record_ref='fixture-durable')
        permit = t.DurablePermit(command_id=command_id, payload_hash=case.command.payload_hash,
            scope=case.scope, ownership_epoch=case.command.ownership_epoch,
            execution_epoch=case.command.execution_epoch, policy_revision=case.command.policy_revision,
            durable_record_ref='fixture-durable', expires_at=END, deadline_monotonic_ns=100)
        case.after_durable()
        return permit

    def recover_unknown(self, command_id, now):
        self.tx.case.command = replace(self.tx.case.command, state='UNKNOWN')
        return self.receipt(command_id)

    def receipt(self, command_id):
        return t.CommandReceipt(receipt_id='fixture-receipt', command_id=command_id,
            status=t.CommandState(self.tx.case.command.state), reason=None, existing_result_ref='fixture')

    def record_outcome(self, receipt):
        self.tx.case.command = replace(self.tx.case.command, state=str(receipt.status))
        return receipt


def execution_case(monkeypatch):
    from trading_system.execution import dispatcher
    case = LockedCase()
    case.tx.case = case
    case.after_durable = lambda: None
    case.engine_calls = []
    case.tx_open = False
    @contextmanager
    def transaction(scope):
        case.tx_open = True
        try:
            yield case.tx
        finally:
            case.tx_open = False
    @contextmanager
    def guard(lease):
        yield
    def submit(command, permit):
        assert not case.tx_open
        assert command.state == 'MAY_HAVE_SENT' and case.proof_calls == 2
        case.engine_calls.append((command, permit))
        return t.CommandReceipt(receipt_id='fixture-confirmed', command_id=command.command_id,
            status=t.CommandState.VENUE_CONFIRMED, reason=None, existing_result_ref='fixture-event')
    case.dispatcher.uow = SimpleNamespace(transaction=transaction)
    case.dispatcher.owner_guard = guard
    case.dispatcher.engine = SimpleNamespace(submit=submit, cancel=submit, amend=submit)
    monkeypatch.setattr(dispatcher, 'CommandRepository', MemoryCommands)
    return case


def test_verified_exit_passes_both_checks_and_calls_offline_engine_once(monkeypatch):
    case = execution_case(monkeypatch)
    assert case.dispatcher.dispatch(case.command.command_id, case.lease).status == t.CommandState.VENUE_CONFIRMED
    assert len(case.engine_calls) == 1
    assert case.dispatcher.dispatch(case.command.command_id, case.lease).status == t.CommandState.VENUE_CONFIRMED
    assert len(case.engine_calls) == 1


@pytest.mark.parametrize('fault', ['proof', 'position', 'lock', 'owner', 'authorization', 'working', 'epoch'])
def test_after_durable_changes_fail_unknown_without_engine_call(monkeypatch, fault):
    case = execution_case(monkeypatch)
    def change():
        if fault == 'proof':
            case.dispatcher.load_action_proof = None
        elif fault == 'position':
            p = case.book.positions[0]
            case.book = replace(case.book, positions=(replace(p,
                actual_quantity=replace(p.actual_quantity, value=money('1').amount)),))
        elif fault == 'lock':
            case.locks += (replace(case.locks[0], lock_id='recovery', permission=t.RiskPermission.RECOVERY_REQUIRED),)
        elif fault == 'owner':
            case.owner = ('foreign', 1, 1)
        elif fault == 'authorization':
            case.binding = replace(case.binding, authorization=replace(case.binding.authorization, revoked_at=NOW))
        elif fault == 'working':
            working(case)
        else:
            case.lease = replace(case.lease, shard=replace(case.lease.shard, execution_epoch=2))
    case.after_durable = change
    lease = case.lease
    assert case.dispatcher.dispatch(case.command.command_id, lease).status == t.CommandState.UNKNOWN
    assert not case.engine_calls
    assert case.command.state == 'UNKNOWN'
    assert case.dispatcher.dispatch(case.command.command_id, lease).status == t.CommandState.UNKNOWN
    assert not case.engine_calls


@pytest.mark.parametrize('environment', [t.Environment.LIVE, t.Environment.TESTNET])
def test_proof_api_does_not_enable_qualified_environments(environment):
    case = LockedCase()
    with pytest.raises(DispatchDenied):
        CommandDispatcher(None, replace(case.scope, environment=environment), load_action_proof=case.proof)


def test_action_proof_expiry_is_rechecked_after_second_transaction_closes(monkeypatch):
    case = execution_case(monkeypatch)
    case.proof_transform = lambda proof: replace(proof, valid_until='2026-10-22T12:00:01Z')
    case.dispatcher.clock = lambda: t.ClockReading(
        utc_now='2026-10-22T12:00:02Z' if case.proof_calls == 2 and not case.tx_open else NOW,
        monotonic_ns=1, clock_health='HEALTHY')
    assert case.dispatcher.dispatch(case.command.command_id, case.lease).status == t.CommandState.UNKNOWN
    assert case.proof_calls == 2 and not case.engine_calls


@pytest.mark.parametrize('product', list(t.Product))
def test_each_supported_product_requires_owned_inventory_and_quantity_units(product):
    case = LockedCase(product=product)
    case.check()


@pytest.mark.parametrize('leg,actual,side', [('BOTH', '-2', 'BUY'), ('LONG', '2', 'SELL'), ('SHORT', '-2', 'BUY')])
def test_derivative_leg_reduction_uses_signed_owned_position(leg, actual, side):
    case = LockedCase()
    scope = replace(case.scope, venue_leg=leg)
    case.scope = case.tx.scope = case.dispatcher.scope = scope
    case.locks = tuple(replace(lock, scope=scope) for lock in case.locks)
    case.intent = replace(case.intent, venue_leg=leg)
    p = case.book.positions[0]
    case.book = replace(case.book, scope=scope, coverage=replace(case.book.coverage, scope=scope),
        positions=(replace(p, scope=scope, actual_quantity=replace(p.actual_quantity, value=money(actual).amount)),))
    case.binding = replace(case.binding, authorization=replace(case.binding.authorization, scopes=(scope,)))
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload,
        side=side, position_side=leg))
    case.rehash()
    case.check()


@pytest.mark.parametrize('price', ['100', '100.001'])
def test_limit_reduction_keeps_instrument_tick_rules(price):
    case = LockedCase()
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload,
        order_type='LIMIT', price=price))
    case.rehash()
    if price == '100':
        case.check()
    else:
        with pytest.raises(DispatchDenied):
            case.check()


@pytest.mark.parametrize('permission', [t.RiskPermission.NO_NEW_RISK,
    t.RiskPermission.EXIT_ONLY, t.RiskPermission.RECOVERY_REQUIRED])
def test_position_only_lock_blocks_increase_without_proof(permission):
    case = LockedCase(permission)
    position_lock = case.locks[0]
    case.locks = ()
    case.book = replace(case.book, positions=(replace(case.book.positions[0], risk_locks=(position_lock,)),))
    case.command = replace(case.command, operation='SUBMIT', canonical_payload=replace(
        case.command.canonical_payload, side='BUY', reduce_only=False))
    case.rehash()
    case.dispatcher.load_action_proof = None
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('boundary', ['proof', 'owner_read'])
def test_reservation_rechecks_clock_after_proof_and_before_writes(boundary):
    case = reservation_case()
    current = [NOW]
    case.service.clock = lambda: current[0]
    if boundary == 'proof':
        proof = case.service.load_action_proof
        def advance_after_proof(*args):
            result = proof(*args)
            current[0] = END
            return result
        case.service.load_action_proof = advance_after_proof
    else:
        execute = case.tx.execute
        def advance_during_owner_read(sql, args=()):
            result = execute(sql, args)
            if 'FROM ledger.active_owners' in sql:
                current[0] = END
            return result
        case.tx.execute = advance_during_owner_read
    result = case.service._reserve(case.tx, case.intent, 1)
    assert not result.accepted
    assert not case.writes


@pytest.mark.parametrize('permission', [t.RiskPermission.NO_NEW_RISK,
    t.RiskPermission.EXIT_ONLY, t.RiskPermission.RECOVERY_REQUIRED])
def test_position_only_locks_require_proof_and_intersect_reductions(permission):
    case = LockedCase(permission)
    position_lock = case.locks[0]
    case.locks = ()
    case.book = replace(case.book, positions=(replace(case.book.positions[0], risk_locks=(position_lock,)),))
    if permission == t.RiskPermission.RECOVERY_REQUIRED:
        with pytest.raises(DispatchDenied):
            case.check()
    else:
        case.check()
    case.dispatcher.load_action_proof = None
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('fault', ['missing', 'stale', 'incomplete'])
def test_no_account_lock_does_not_imply_missing_or_stale_book_is_safe(fault):
    case = LockedCase()
    case.locks = ()
    if fault == 'missing':
        case.tx.projections.book = lambda scope: None
    elif fault == 'stale':
        case.book = replace(case.book, evidence=(replace(case.book.evidence[0], visible_at='2026-10-22T11:58:00Z'),))
    else:
        case.book = replace(case.book, coverage=replace(case.book.coverage, pagination_complete=False))
    with pytest.raises(DispatchDenied):
        case.check()


@pytest.mark.parametrize('boundary', ['proof', 'owner_read'])
@pytest.mark.parametrize('expiry', ['proof', 'evidence', 'intent', 'risk', 'quote'])
def test_reservation_retains_earliest_validity_across_slow_local_reads(boundary, expiry):
    case = reservation_case()
    current = [NOW]
    case.service.clock = lambda: current[0]
    # All cases expire before the ordinary one-minute intent/risk deadline.
    due = '2026-10-22T12:00:01Z'
    if expiry == 'proof':
        case.proof_transform = lambda proof: replace(proof, valid_until=due)
    elif expiry == 'evidence':
        case.book = replace(case.book, evidence=(replace(case.book.evidence[0], visible_at='2026-10-22T11:59:01Z'),))
    elif expiry == 'intent':
        case.intent = replace(case.intent, valid_until=due)
    elif expiry == 'risk':
        case.risk.inputs = replace(case.risk.inputs, expires_at=due)
    if boundary == 'proof':
        proof = case.service.load_action_proof
        def advance_after_proof(*args):
            result = proof(*args)
            current[0] = due
            return result
        case.service.load_action_proof = advance_after_proof
    else:
        execute = case.tx.execute
        def advance_during_owner_read(sql, args=()):
            result = execute(sql, args)
            if 'FROM ledger.active_owners' in sql:
                current[0] = due
            return result
        case.tx.execute = advance_during_owner_read
    result = case.service._reserve(case.tx, case.intent, 1)
    assert not result.accepted
    assert not case.writes


@pytest.mark.parametrize('boundary', ['proof', 'owner_read'])
@pytest.mark.parametrize('clock_fault', ['expired', 'backwards'])
def test_reservation_monotonic_clock_prevents_stalled_wall_clock_extension(boundary, clock_fault):
    case = reservation_case()
    current_ns = [1]
    case.service.monotonic_clock = lambda: current_ns[0]
    def advance():
        current_ns[0] = 61_000_000_001 if clock_fault == 'expired' else 0
    if boundary == 'proof':
        proof = case.service.load_action_proof
        def advance_after_proof(*args):
            result = proof(*args)
            advance()
            return result
        case.service.load_action_proof = advance_after_proof
    else:
        execute = case.tx.execute
        def advance_during_owner_read(sql, args=()):
            result = execute(sql, args)
            if 'FROM ledger.active_owners' in sql:
                advance()
            return result
        case.tx.execute = advance_during_owner_read
    result = case.service._reserve(case.tx, case.intent, 1)
    assert not result.accepted
    assert not case.writes


@pytest.mark.parametrize('partial_plan', [False, True])
def test_partial_stop_repair_is_allowed_but_does_not_verify_full_coverage(partial_plan):
    from trading_system.protection.native import NativeVerifier
    case = LockedCase()
    protection(case)
    case.command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity='1'))
    if partial_plan:
        case.plan = replace(case.plan, coverage_quantity=replace(case.plan.coverage_quantity, value=money('1').amount))
    case.rehash()
    plan_before = case.plan
    case.check()
    assert case.plan == plan_before and case.plan.state == t.ProtectionState.GAP
    proof = case.proof(case.tx, case.command, case.intent, case.book)
    assert NativeVerifier(clock=lambda: NOW).verify(case.plan, proof.report,
        case.book.positions[0]).state == t.ProtectionState.GAP
