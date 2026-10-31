"""Actual frozen request materialization -> lossless unsigned SDK call shape.

These tests construct no permission, account client or venue qualification.
"""
from dataclasses import replace
from urllib.parse import parse_qsl,urlencode

import pytest

from test_creation_request_binding import ordinary
from test_program_compilation import inputs,change,price,trigger
from test_program_exposure import make_plan
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_program import QuantitySpec,TimeInForceSpec
from trading_system.execution.creation_requests import assign_client_ids,materialize_creation,EquityWalletSelection
from trading_system.intents.program_compilation import compile_order_program,Namespace,Endpoint


KINDS=('spot','sor','usdm','conditional','batch','equity','OCO','OTO','OTOCO','OPO','OPOCO',
    'spot-TWAP','spot-VP','usdm-TWAP','usdm-VP')


def request_for(kind):
    product=t.Product.USD_M_PERP if kind in ('usdm','conditional','batch') or kind.startswith('usdm-') else t.Product.US_EQUITY if kind=='equity' else t.Product.SPOT
    if kind in ('OCO','OTO','OTOCO','OPO','OPOCO','batch') or '-' in kind:
        template='BATCH' if kind=='batch' else kind.split('-')[-1]
        plan=make_plan(template,'VENUE_NATIVE',product);profile=inputs(product)[3]['profile']
    elif kind in ('sor','conditional'):
        p,r,c,k=inputs(product)
        if kind=='sor':p=change(p,routing='SOR',quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None))
        else:p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),
            price=price(),trigger=trigger(source='MARK_PRICE'),tif=TimeInForceSpec(kind='NONE',expires_at=None))
        plan=compile_order_program(p,r,c,**k);profile=k['profile']
    else:plan,profile=ordinary(product)
    selection=None
    if kind=='equity':selection=EquityWalletSelection(scope=plan.scope,profile_hash=plan.profile_hash,
        quote_asset=plan.nodes[0].notional_asset,wallet='MAIN',evidence=profile.evidence[0],valid_until=profile.valid_until)
    request=materialize_creation(plan,0,profile,assign_client_ids(plan,0,'original-'+kind),
        wallet_selection=selection,now=profile.as_of)
    return request,profile.as_of


@pytest.mark.parametrize('kind',KINDS)
def test_all_fifteen_static_service_routes_preserve_every_original_field_and_id(kind):
    from trading_system.engine.nautilus.native_creation import creation_call
    request,now=request_for(kind);before=domain_payload(request)
    clock=t.ClockReading(utc_now=now,monotonic_ns=123,clock_health='HEALTHY')
    call=creation_call(request,clock=clock,recv_window_ms=999)
    assert call.request_hash==canonical_hash(before) and call.command_id==request.command_id
    assert call.namespace==request.namespace and call.endpoint==request.endpoint and call.method=='POST'
    assert call.original_parameters==request.form_parameters()
    assert tuple(call.parameters[:-2])==request.form_parameters()
    assert dict(call.parameters)['recvWindow']=='999' and dict(call.parameters)['timestamp'].isdigit()
    assert parse_qsl(urlencode(call.parameters))==list(call.parameters)
    assert domain_payload(request)==before and not call.execution_authorized
    assert not call.native_order_qualified and call.external_activation_allowed is False


@pytest.mark.parametrize('fault',['product','namespace','endpoint','command','member','duplicate','leg','version'])
def test_source_route_and_identity_substitutions_fail_closed_without_sdk_or_sender(fault):
    from trading_system.engine.nautilus.native_creation import creation_call
    request,now=request_for('usdm')
    if fault=='product':request=replace(request,scope=replace(request.scope,product=t.Product.SPOT))
    if fault=='namespace':request=replace(request,namespace=Namespace.SPOT_LIST)
    if fault=='endpoint':request=replace(request,endpoint=Endpoint.USD_M_CONDITIONAL)
    if fault=='command':request=replace(request,command_id='different-original')
    if fault=='member':request=replace(request,assigned_ids=replace(request.assigned_ids,
        members=(replace(request.assigned_ids.members[0],client_id='f'*32),)))
    if fault=='duplicate':request=replace(request,fields=(*request.fields,request.fields[0]))
    if fault=='leg':request=replace(request,scope=replace(request.scope,venue_leg='LONG'))
    if fault=='version':request=replace(request,contract_version='unknown')
    with pytest.raises(ValueError):creation_call(request,clock=t.ClockReading(utc_now=now,monotonic_ns=123,clock_health='HEALTHY'))


@pytest.mark.parametrize('kind',['OCO','OTOCO','batch','spot-TWAP','conditional','equity'])
def test_foreign_parent_or_member_ids_cannot_be_hidden_by_the_service_shape(kind):
    from trading_system.engine.nautilus.native_creation import creation_call
    request,now=request_for(kind);ids=request.assigned_ids
    changed=replace(ids,parent_client_id='f'*32) if ids.parent_client_id else replace(ids,members=(replace(ids.members[0],client_id='f'*32),*ids.members[1:]))
    with pytest.raises(ValueError):creation_call(replace(request,assigned_ids=changed),clock=t.ClockReading(utc_now=now,monotonic_ns=123,clock_health='HEALTHY'))


@pytest.mark.parametrize('environment',[t.Environment.LIVE,t.Environment.TESTNET])
def test_external_environments_cannot_be_activated_by_a_typed_unsigned_request(environment):
    from trading_system.engine.nautilus.native_creation import creation_call
    request,now=request_for('spot')
    with pytest.raises(ValueError,match='ENVIRONMENT_DISABLED'):
        creation_call(replace(request,scope=replace(request.scope,environment=environment)),clock=t.ClockReading(utc_now=now,monotonic_ns=123,clock_health='HEALTHY'))


def test_submillisecond_clock_uses_exact_integer_milliseconds_and_no_float_rounding():
    from trading_system.engine.nautilus.native_creation import creation_call
    request,_=request_for('spot')
    clock=t.ClockReading(utc_now='2026-10-28T10:00:00.999999999Z',monotonic_ns=123,clock_health='HEALTHY')
    assert dict(creation_call(request,clock=clock).parameters)['timestamp']=='1793181600999'
