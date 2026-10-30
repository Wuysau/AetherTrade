"""SDK management/query shapes remain unsigned, independently unauthorized."""
from dataclasses import replace

import pytest

from test_management_requests import setup,materialize
from trading_system.contracts import types as t
from trading_system.engine.nautilus.order_query import HISTORY,query_page_request
from trading_system.intents.program_compilation import Namespace
from trading_system.intents.order_management_compilation import ManagementKind,ManagementEndpoint


@pytest.mark.parametrize('namespace',list(Namespace))
@pytest.mark.parametrize('kind',[ManagementKind.QUERY,ManagementKind.CANCEL])
def test_every_service_query_or_cancel_preserves_original_fields_and_scoped_identity(namespace,kind):
    from trading_system.engine.nautilus.native_management import management_call
    plan,k=setup(namespace,kind)
    for request in materialize(plan,**k):
        history=request.endpoint in HISTORY
        kwargs=dict(page_number=2,page_size=10) if history else {}
        call=management_call(request,clock=t.ClockReading(utc_now=k['now'],monotonic_ns=123,clock_health='HEALTHY'),**kwargs)
        expected=query_page_request(request,**kwargs).form_parameters() if kind==ManagementKind.QUERY else request.form_parameters()
        assert call.original_parameters==expected and call.namespace==namespace and call.endpoint==request.endpoint
        assert call.original_client_id==request.original_client_id and call.target_venue_id==request.target_venue_id
        assert call.method==request.method and call.command_id==request.command_id
        assert call.requires_independent_command==(kind!=ManagementKind.QUERY)
        assert not call.execution_authorized and not call.funds_release_authorized and not call.native_order_qualified


@pytest.mark.parametrize('namespace,kind',[(Namespace.SPOT_ORDINARY,ManagementKind.KEEP_PRIORITY),
    (Namespace.SPOT_ORDINARY,ManagementKind.CANCEL_REPLACE),(Namespace.USD_M_ORDINARY,ManagementKind.MODIFY_LIMIT)])
def test_amend_and_independent_cancel_replace_parameters_are_lossless(namespace,kind):
    from trading_system.engine.nautilus.native_management import management_call
    plan,k=setup(namespace,kind);request,=materialize(plan,**k)
    call=management_call(request,clock=t.ClockReading(utc_now=k['now'],monotonic_ns=123,clock_health='HEALTHY'))
    assert call.original_parameters==request.form_parameters() and call.requires_independent_command
    assert not call.execution_authorized and not call.funds_release_authorized
    if kind==ManagementKind.CANCEL_REPLACE:
        assert dict(call.original_parameters)['cancelReplaceMode']=='ALLOW_FAILURE'
        assert dict(call.original_parameters)['orderRateLimitExceededMode']=='CANCEL_ONLY'


@pytest.mark.parametrize('fault',['product','namespace','endpoint','method','client_id','venue_id','duplicate','write_without_command','query_with_command'])
def test_cross_service_identity_and_permission_substitutions_are_rejected(fault):
    from trading_system.engine.nautilus.native_management import management_call
    kind=ManagementKind.CANCEL if fault in ('venue_id','write_without_command') else ManagementKind.QUERY
    plan,k=setup(Namespace.SPOT_ORDINARY,kind);request,=materialize(plan,**k)
    if fault=='product':request=replace(request,scope=replace(request.scope,product=t.Product.US_EQUITY))
    if fault=='namespace':request=replace(request,namespace=Namespace.USD_M_ORDINARY)
    if fault=='endpoint':request=replace(request,endpoint=ManagementEndpoint.SPOT_CANCEL_REPLACE)
    if fault=='method':request=replace(request,method='POST')
    if fault=='client_id':request=replace(request,original_client_id='foreign-original')
    if fault=='venue_id':request=replace(request,target_venue_id='different-venue-id')
    if fault=='duplicate':request=replace(request,parameters=(*request.parameters,request.parameters[0]))
    if fault=='write_without_command':request=replace(request,command_id=None)
    if fault=='query_with_command':request=replace(request,command_id='not-a-read')
    with pytest.raises(ValueError):management_call(request,clock=t.ClockReading(utc_now=k['now'],monotonic_ns=123,clock_health='HEALTHY'))
