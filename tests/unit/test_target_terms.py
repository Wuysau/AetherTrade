"""Shared target arithmetic tests, with no fabricated legacy execution fields."""
from dataclasses import replace
import pytest
from trading_system.contracts import types as t
from trading_system.intents.target_compiler import TargetCompiler,TargetTerms,legacy_target_terms
from trading_system.intents.semantic_validation import ProposalRejected
from trading_system.risk.exposure import WorkingOrder
from unit.support_ip_09 import make_case,position,order,D,END

@pytest.mark.parametrize('product',list(t.Product))
def test_terms_and_legacy_adapter_share_the_same_target_math(product):
    case=make_case(product);intent=case.bind();unit=intent.quantity_unit
    book=replace(case.book,positions=(position(case.book.scope,'.004',unit),),active_orders=(order(case.book.scope,'.003',unit=unit),))
    inputs=replace(case.target,order_sides=(WorkingOrder(logical_client_order_id='order',side='BUY'),))
    compiler=TargetCompiler(inputs)
    assert compiler.compile_terms(legacy_target_terms(intent),book)==compiler.compile(intent,book)
    assert compiler.compile_terms(legacy_target_terms(intent),book).remaining.value==D('.003')


def test_terms_do_not_construct_or_validate_a_legacy_execution_record(monkeypatch):
    case=make_case()
    terms=TargetTerms(scope=case.book.scope,venue='BINANCE',owner_strategy_id='demo-ai-strategy',ownership_epoch=1,
        collateral_group_id='paper-group-demo',basis_fingerprint=case.book.basis_fingerprint,
        action='SET_TARGET',target_side='LONG',target=t.Quantity(value=D('.010'),unit='BASE'),
        valid_until=END,submission_deadline=END)
    from trading_system.contracts.wire import ContractCodec
    monkeypatch.setattr(ContractCodec,'encode',lambda *a: (_ for _ in ()).throw(AssertionError('legacy wire path used')))
    plan=TargetCompiler(case.target).compile_terms(terms,case.book)
    assert plan.remaining.value==D('.010') and not hasattr(terms,'execution')


def test_shared_terms_do_not_widen_fixed_ledger_units():
    case=make_case();terms=legacy_target_terms(case.bind())
    spec=replace(case.target.instrument,quantity_step=t.Quantity(value=D('.001'),unit='BTC'),minimum_quantity=t.Quantity(value=D('.001'),unit='BTC'))
    compiler=TargetCompiler(replace(case.target,instrument=spec,expected_instrument=spec))
    with pytest.raises(ProposalRejected,match='QUANTITY_UNIT_MISMATCH'):
        compiler.compile_terms(replace(terms,target=t.Quantity(value=D('.010'),unit='BTC')),case.book)


def test_generic_management_requires_its_own_responsibility_adapter():
    case=make_case();terms=legacy_target_terms(case.bind())
    with pytest.raises(ProposalRejected,match='MANAGEMENT_TARGET_TERMS_UNSUPPORTED'):
        TargetCompiler(case.target).compile_terms(replace(terms,action='UPDATE_EXIT'),case.book)


def test_terms_reject_nonzero_flat_target():
    case=make_case()
    with pytest.raises(ProposalRejected,match='TARGET_SIDE_QUANTITY_MISMATCH'):
        replace(legacy_target_terms(case.bind()),target_side='FLAT')
