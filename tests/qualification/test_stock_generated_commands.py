"""Generated commands against offline boundaries; never native qualification."""
from dataclasses import replace
from decimal import Decimal

import pytest
from jsonschema.exceptions import ValidationError

from qualification.support_ip_11 import BridgeCase
from qualification.support_ip_25 import StocksCase
from unit.support_ip_09 import make_case, position, quantity
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ABSENT, ContractCodec
from trading_system.engine.nautilus.bridge import BridgeDenied
from trading_system.execution.children import ChildPlanner
from trading_system.intents.semantic_validation import ProposalRejected
from trading_system.intents.target_compiler import TargetCompiler
from trading_system.protection.manager import CommandCompiler, CommandRequest, validate_command


@pytest.fixture
def stock(tmp_path):
    case = StocksCase(tmp_path)
    try:
        yield case
    finally:
        case.codec.close()


def child_inputs(case, *, reduction=False, order_type='MARKET'):
    base = make_case(case.scope.product)
    original = base.bind()
    case.reading = replace(case.reading, utc_now=base.target.clock.utc_now)
    case.lease = replace(case.lease, expires_at=original.valid_until)
    case.permit = replace(case.permit, expires_at=original.valid_until)
    scope, unit = case.scope, original.quantity_unit
    intent = replace(original, instrument_uid=scope.instrument_uid,
        target_quantity='0.5' if reduction else '1',
        execution=replace(original.execution, order_preference=order_type,
                          limit_price='100' if order_type == 'LIMIT' else None))
    spec = replace(base.target.instrument, instrument_uid=scope.instrument_uid)
    target = replace(base.target, instrument=spec, expected_instrument=spec,
        session=replace(base.target.session, instrument_uid=scope.instrument_uid))
    book = replace(base.book, scope=scope, coverage=replace(base.book.coverage, scope=scope),
        positions=(position(scope, '1', unit=unit, owner=intent.owner_strategy_id),) if reduction else ())
    policy = t.ChildPolicy(expires_at=intent.valid_until, slice_quantity=quantity('1', unit),
        maximum_children=1, compensation_policy='REVIEW_ONLY', mutable_fields=('quantity', 'price'))
    planner = ChildPlanner(TargetCompiler(target), case.codec, 7, clock=lambda: base.target.clock)
    return planner, intent, book, policy


def offline_authority(case, command):
    # In-memory test authority only, never a claim of a PostgreSQL commit.
    command = replace(command, state='MAY_HAVE_SENT', durable_record_ref='synthetic-committed')
    permit = replace(case.permit, command_id=command.command_id, payload_hash=command.payload_hash,
                     durable_record_ref=command.durable_record_ref)
    case.trust(command, permit)
    return command, permit


def assert_equity_payload(command):
    payload = command.canonical_payload
    assert payload.tokenize is False
    assert payload.reduce_only is ABSENT and payload.full_position_exit is ABSENT
    encoded = ContractCodec().encode(command)['canonical_payload']
    assert encoded['tokenize'] is False
    assert 'reduce_only' not in encoded and 'full_position_exit' not in encoded


def request_for(case, *, operation='CLOSE'):
    p = position(case.scope, '1', unit='SHARE')
    plan = t.ProtectionPlan(plan_id='plan', mode='NATIVE', scope=case.scope,
        coverage_quantity=quantity('1', 'SHARE'), stop_price=t.Price(value=Decimal('90'), currency='USDC'),
        stop_trigger='LAST', native_order_ids=(), monitor_id=None, state=t.ProtectionState.PENDING,
        last_verified_at=None, coverage_gap_since=None, replacement_revision=0,
        on_gap_policy='REPAIR_THEN_AUTHORIZED_EXIT')
    target = None
    if operation == 'AMEND':
        target = t.OwnedOrderRef(identity=case.codec.decode(case.command.venue_client_order_id),
                                owner_strategy_id=p.owner_strategy_id, ownership_epoch=p.ownership_epoch)
    return CommandRequest(key=operation, operation=operation, position=p, plan=plan,
        quantity=quantity('0.5', 'SHARE'), target=target,
        order_type='MARKET' if operation == 'CLOSE' else 'STOP_MARKET')


def compile_request(case, request):
    return CommandCompiler(case.command, case.codec, clock=lambda: case.reading.utc_now)(request)


def test_generated_equity_submit_limit_preview_keeps_write_gate(stock):
    planner, intent, book, policy = child_inputs(stock, order_type='LIMIT')
    command, = planner.plan(intent, book, policy)
    assert command.operation == 'SUBMIT'
    preview = stock.engine.preview(command, stock.instrument, trading_session='RTH')
    assert (preview.side, preview.quantity, preview.price, preview.tokenize) == ('BUY', '1', '100', False)
    assert_equity_payload(command)
    command, permit = offline_authority(stock, command)
    with pytest.raises(BridgeDenied, match='LIMIT_SESSION_NOT_IN_CANONICAL_CONTRACT'):
        stock.engine.submit(command, permit)
    assert not stock.engine.offline_writes


def test_generated_equity_market_buy_requires_notional(stock):
    planner, intent, book, policy = child_inputs(stock)
    command, = planner.plan(intent, book, policy)
    assert command.operation == 'SUBMIT'
    # A share target does not authorize an invented quote amount.
    with pytest.raises(ValueError, match='MARKET_BUY_REQUIRES_NOTIONAL_ONLY'):
        stock.engine.preview(command, stock.instrument)
    assert_equity_payload(command)
    command, permit = offline_authority(stock, command)
    with pytest.raises(ValueError, match='MARKET_BUY_REQUIRES_NOTIONAL_ONLY'):
        stock.engine.submit(command, permit)
    assert not stock.engine.offline_writes


@pytest.mark.parametrize('generator', ['child', 'protection'])
def test_generated_equity_close_reaches_offline_adapter(stock, generator):
    if generator == 'child':
        planner, intent, book, policy = child_inputs(stock, reduction=True)
        command, = planner.plan(intent, book, policy)
    else:
        command = compile_request(stock, request_for(stock)).command
    assert command.operation == 'CLOSE'
    preview = stock.engine.preview(command, stock.instrument)
    assert (preview.side, preview.quantity, preview.tokenize) == ('SELL', '0.5', False)
    assert_equity_payload(command)
    command, permit = offline_authority(stock, command)
    receipt = stock.engine.submit(command, permit)
    assert receipt.status == t.CommandState.UNKNOWN
    assert stock.engine.offline_writes[0].command == command
    assert len(stock.engine.offline_writes) == 1
    with pytest.raises(BridgeDenied, match='COMMAND_ALREADY_OBSERVED_QUERY_SAME_ID'):
        stock.engine.submit(command, permit)
    assert len(stock.engine.offline_writes) == 1


@pytest.mark.parametrize('change,code', [
    ('owner', 'POSITION_NOT_OWNED'), ('short', 'CASH_SHORT'), ('unit', 'POSITION_EVIDENCE'),
])
def test_equity_child_keeps_inventory_checks(stock, change, code):
    planner, intent, book, policy = child_inputs(stock, reduction=True)
    p, = book.positions
    if change == 'owner':
        p = replace(p, owner_strategy_id='another-owner')
    elif change == 'short':
        p = replace(p, actual_quantity=quantity('-1', 'SHARE'))
    else:
        p = replace(p, actual_quantity=quantity('1', 'BASE'))
    with pytest.raises(ProposalRejected, match=code):
        planner.plan(intent, replace(book, positions=(p,)), policy)
    assert not stock.engine.offline_writes


@pytest.mark.parametrize('amount,unit', [('2', 'SHARE'), ('0.5', 'BASE')])
def test_equity_close_keeps_quantity_checks(stock, amount, unit):
    with pytest.raises(ValueError, match='REDUCTION_PARAMETERS_INVALID'):
        compile_request(stock, replace(request_for(stock), quantity=quantity(amount, unit)))
    assert not stock.engine.offline_writes


@pytest.mark.parametrize('amount', ['0', '-1'])
def test_equity_close_rejects_nonpositive_quantity_at_contract(stock, amount):
    with pytest.raises(ValidationError) as rejected:
        compile_request(stock, replace(request_for(stock), quantity=quantity(amount, 'SHARE')))
    assert list(rejected.value.path) == ['canonical_payload', 'quantity']
    assert not stock.engine.offline_writes


@pytest.mark.parametrize('field,value', [
    ('side', 'BUY'), ('quantity', '2'), ('reduce_only', False),
    ('full_position_exit', False),
])
def test_equity_validator_rejects_rehashed_unsafe_payload(stock, field, value):
    request = request_for(stock)
    compiled = compile_request(stock, request)
    command = replace(compiled.command,
        canonical_payload=replace(compiled.command.canonical_payload, **{field: value}))
    command = replace(command,
        payload_hash=canonical_hash(ContractCodec().encode(command)['canonical_payload']))
    with pytest.raises(ValueError, match='REDUCTION_PARAMETERS_INVALID'):
        validate_command(request, replace(compiled, command=command))
    assert not stock.engine.offline_writes


def test_equity_close_rejects_tokenization_at_contract_and_preview(stock):
    request = request_for(stock)
    compiled = compile_request(stock, request)
    command = replace(compiled.command,
        canonical_payload=replace(compiled.command.canonical_payload, tokenize=True))
    with pytest.raises(ValidationError) as rejected:
        validate_command(request, replace(compiled, command=command))
    assert list(rejected.value.path) == ['canonical_payload', 'tokenize']
    with pytest.raises(BridgeDenied, match='EXPLICIT_TOKENIZE_FALSE_REQUIRED'):
        stock.engine.preview(command, stock.instrument)
    assert not stock.engine.offline_writes


def test_equity_validator_keeps_owner_binding(stock):
    request = request_for(stock)
    compiled = compile_request(stock, request)
    with pytest.raises(ValueError, match='COMMAND_BINDING_MISMATCH'):
        validate_command(request, replace(compiled,
            order=replace(compiled.order, owner_strategy_id='another-owner')))
    assert not stock.engine.offline_writes


@pytest.mark.parametrize('operation', ['SET_PROTECTION', 'AMEND'])
def test_generated_equity_protection_stays_unqualified(stock, operation):
    compiled = compile_request(stock, request_for(stock, operation=operation))
    assert_equity_payload(compiled.command)
    assert compiled.command.canonical_payload.side == 'SELL'
    assert compiled.command.canonical_payload.trigger_price == '90'
    command, permit = offline_authority(stock, compiled.command)
    with pytest.raises(BridgeDenied, match='UNSUPPORTED_STOCKS_OPERATION'):
        stock.engine.preview(command, stock.instrument)
    with pytest.raises(BridgeDenied, match='STOCKS_OPERATION_NOT_QUALIFIED'):
        (stock.engine.amend if operation == 'AMEND' else stock.engine.submit)(command, permit)
    assert not stock.engine.offline_writes


def test_equity_native_gate_remains_closed(stock):
    assert not stock.engine.live_enabled
    with pytest.raises(BridgeDenied, match='NATIVE_PATH_NOT_QUALIFIED'):
        stock.engine.enable_native_path('submit')
    assert not stock.engine.offline_writes


@pytest.mark.parametrize('product', [t.Product.SPOT, t.Product.USD_M_PERP])
@pytest.mark.parametrize('reduction', [False, True])
def test_non_equity_child_regression(tmp_path, product, reduction):
    case = BridgeCase(tmp_path, product=product)
    try:
        planner, intent, book, policy = child_inputs(case, reduction=reduction)
        command, = planner.plan(intent, book, policy)
        assert command.operation == ('CLOSE' if reduction else 'SUBMIT')
        assert command.canonical_payload.reduce_only is reduction
        assert command.canonical_payload.full_position_exit is False
        assert command.canonical_payload.tokenize is ABSENT
        command, permit = offline_authority(case, command)
        assert case.engine.submit(command, permit).status == t.CommandState.UNKNOWN
        assert len(case.engine.offline_writes) == 1
        assert not case.engine.live_enabled
    finally:
        case.codec.close()


@pytest.mark.parametrize('product', [t.Product.SPOT, t.Product.USD_M_PERP])
@pytest.mark.parametrize('operation', ['CLOSE', 'SET_PROTECTION'])
def test_non_equity_protection_regression(tmp_path, product, operation):
    case = BridgeCase(tmp_path, product=product)
    try:
        unit = 'BASE' if product == t.Product.SPOT else 'CONTRACT'
        request = request_for(case, operation=operation)
        request = replace(request,
            position=replace(request.position, actual_quantity=quantity('1', unit)),
            quantity=quantity('0.5', unit),
            plan=replace(request.plan, coverage_quantity=quantity('1', unit)))
        payload = (compiled := compile_request(case, request)).command.canonical_payload
        assert payload.reduce_only is (product == t.Product.USD_M_PERP)
        assert payload.full_position_exit is False and payload.tokenize is ABSENT
        command, permit = offline_authority(case, compiled.command)
        assert case.engine.submit(command, permit).status == t.CommandState.UNKNOWN
        assert len(case.engine.offline_writes) == 1
        assert not case.engine.live_enabled
    finally:
        case.codec.close()
