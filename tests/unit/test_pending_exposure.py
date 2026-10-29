from dataclasses import replace
from decimal import Decimal as D
import pytest
from trading_system.contracts import types as t
from trading_system.risk.exposure import ExposureCalculator,PendingExposure,ProposedExposure,ExposureMark,WorkingOrder
from trading_system.risk.valuation import Valuation
from unit.support_ip_06 import make_case,quantity


def case():
    c=make_case()
    delta=t.DeltaPlan(scope=c.book.scope,target=quantity('3'),actual=quantity('0'),
        working=quantity('1'),remaining=quantity('2'),classification='INCREASE',cancel_first=())
    pending=PendingExposure(obligation_id='held-source',scope=c.book.scope,signed_quantity=quantity('1'),
        command_refs=('durable-original',),represented_order_ids=())
    return c,delta,pending


def test_pending_quantity_uses_same_portfolio_calculator_without_fake_order():
    c,delta,pending=case()
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,pending=(pending,)).project(c.book,delta)
    assert not c.book.active_orders and not report.missing
    assert report.gross.amount==report.net.amount==300
    assert report.groups[0].gross.amount==300
    legacy=ExposureCalculator(Valuation('USDT'),c.inputs.marks).project(c.book,delta)
    assert 'WORKING_QUANTITY_MISMATCH' in legacy.missing


@pytest.mark.parametrize('fault',['duplicate','unit','refs','zero','represented','scope'])
def test_ambiguous_pending_obligations_do_not_become_zero_risk(fault):
    c,delta,pending=case();items=(pending,)
    if fault=='duplicate':items=(pending,pending)
    if fault=='unit':items=(replace(pending,signed_quantity=quantity('1','BASE')),)
    if fault=='refs':items=(replace(pending,command_refs=()),)
    if fault=='zero':items=(replace(pending,signed_quantity=quantity('0')),)
    if fault=='represented':items=(replace(pending,represented_order_ids=('absent',),signed_quantity=quantity('0')),)
    if fault=='scope':items=(replace(pending,scope=replace(c.book.scope,venue_account_id='foreign')),)
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,pending=items).project(c.book,delta)
    assert report.missing


def test_distinct_fill_bounds_do_not_reprice_retained_inventory_as_new_buys():
    from unit.support_ip_06 import position,price
    c=make_case();scope=c.book.scope
    book=replace(c.book,positions=(position(scope,'1'),))
    delta=t.DeltaPlan(scope=scope,target=quantity('4'),actual=quantity('1'),working=quantity('0'),
        remaining=quantity('3'),classification='INCREASE',cancel_first=())
    proposed=tuple(ProposedExposure(node_id=node,scope=scope,signed_quantity=quantity(amount),
        mark=replace(c.inputs.marks[0],price=price(value))) for node,amount,value in [('a','1','80'),('b','2','90')])
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,proposed=proposed).project(book,delta)
    assert not report.missing and report.gross.amount==report.net.amount==360


def test_proposed_reductions_value_remaining_owned_inventory_once():
    from unit.support_ip_06 import position,price
    c=make_case();scope=c.book.scope;book=replace(c.book,positions=(position(scope,'4'),))
    delta=t.DeltaPlan(scope=scope,target=quantity('2'),actual=quantity('4'),working=quantity('0'),
        remaining=quantity('-2'),classification='REDUCE',cancel_first=())
    proposed=tuple(ProposedExposure(node_id=node,scope=scope,signed_quantity=quantity('-1'),
        mark=replace(c.inputs.marks[0],price=price(value))) for node,value in [('a','80'),('b','90')])
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,proposed=proposed).project(book,delta)
    assert not report.missing and report.gross.amount==report.net.amount==200


def test_proposed_quantity_cannot_disagree_with_actual_delta():
    c,delta,_=case()
    proposed=(ProposedExposure(node_id='entry',scope=c.book.scope,signed_quantity=quantity('3'),mark=c.inputs.marks[0]),)
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,proposed=proposed).project(c.book,delta)
    assert 'PROPOSED_EXPOSURE_DELTA_MISMATCH' in report.missing


def test_pending_already_represented_by_actual_order_is_not_counted_twice():
    from unit.support_ip_09 import order
    c,_,pending=case();scope=c.book.scope
    book=replace(c.book,active_orders=(order(scope,'1',unit='CONTRACT'),))
    delta=t.DeltaPlan(scope=scope,target=quantity('1'),actual=quantity('0'),working=quantity('1'),
        remaining=quantity('0'),classification='NONE',cancel_first=())
    pending=replace(pending,signed_quantity=quantity('0'),represented_order_ids=('order',))
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,(WorkingOrder(logical_client_order_id='order',side='BUY'),),
        pending=(pending,)).project(book,delta)
    assert not report.missing and report.gross.amount==report.net.amount==100


def test_full_flat_sell_does_not_report_sale_mark_difference_as_residual_exposure():
    from unit.support_ip_06 import position,price
    c=make_case();scope=c.book.scope;book=replace(c.book,positions=(position(scope,'1'),))
    delta=t.DeltaPlan(scope=scope,target=quantity('0'),actual=quantity('1'),working=quantity('0'),
        remaining=quantity('-1'),classification='REDUCE',cancel_first=())
    p=ProposedExposure(node_id='exit',scope=scope,signed_quantity=quantity('-1'),
        mark=replace(c.inputs.marks[0],price=price('80')))
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,proposed=(p,)).project(book,delta)
    assert not report.missing and report.gross.amount==report.net.amount==0


@pytest.mark.parametrize('dimension',['product','instrument','leg'])
def test_same_pending_source_id_in_different_physical_scope_is_legal(dimension):
    c,delta,pending=case()
    changes={'product':{'product':t.Product.SPOT},'instrument':{'instrument_uid':'other-instrument'},'leg':{'venue_leg':'LONG'}}[dimension]
    other=replace(c.book.scope,**changes);unit='BASE' if dimension=='product' else 'CONTRACT'
    second=replace(pending,scope=other,signed_quantity=quantity('1',unit))
    marks=(*c.inputs.marks,replace(c.inputs.marks[0],scope=other,unit=unit))
    report=ExposureCalculator(Valuation('USDT'),marks,pending=(pending,second)).project(c.book,delta)
    assert not report.missing and report.gross.amount==report.net.amount==400


def test_same_original_id_in_distinct_source_families_is_not_a_duplicate():
    c,delta,pending=case();delta=replace(delta,target=quantity('4'),working=quantity('2'))
    second=replace(pending,source_family='APPLICATION_WRITE',service_namespace='EXECUTION_ALGO')
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,pending=(pending,second)).project(c.book,delta)
    assert not report.missing and report.gross.amount==report.net.amount==400


def test_same_original_id_in_distinct_service_namespaces_is_not_a_duplicate():
    c,delta,pending=case();delta=replace(delta,target=quantity('4'),working=quantity('2'))
    pending=replace(pending,source_family='APPLICATION_WRITE')
    second=replace(pending,service_namespace='EXECUTION_ALGO')
    report=ExposureCalculator(Valuation('USDT'),c.inputs.marks,pending=(pending,second)).project(c.book,delta)
    assert not report.missing and report.gross.amount==report.net.amount==400


@pytest.mark.parametrize('fault',['currency','group','multiplier','unit'])
def test_proposed_mark_cannot_change_physical_valuation_binding(fault):
    from unit.support_ip_06 import price
    from trading_system.risk.valuation import FxRate
    c,delta,_=case();delta=replace(delta,target=quantity('2'),working=quantity('0'))
    mark=c.inputs.marks[0]
    if fault=='currency':mark=replace(mark,price=price('100','USD'))
    if fault=='group':mark=replace(mark,related_group='another')
    if fault=='multiplier':mark=replace(mark,multiplier=D(2))
    if fault=='unit':mark=replace(mark,unit='BASE')
    p=ProposedExposure(node_id='entry',scope=c.book.scope,signed_quantity=quantity('2'),mark=mark)
    valuation=Valuation('USDT',(FxRate(source='USD',target='USDT',asset_rate=D(1),liability_rate=D(1),haircut=D(0),evidence_id='fx'),))
    assert ExposureCalculator(valuation,c.inputs.marks,proposed=(p,)).project(c.book,delta).missing
