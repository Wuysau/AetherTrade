from dataclasses import replace

import pytest

from test_program_compilation import inputs,change,price
from test_program_exposure import make_plan
from trading_system.contracts import types as t
from trading_system.contracts.order_program import TimeInForceSpec
from trading_system.execution.creation_requests import assign_client_ids,materialize_creation,verify_creation,EquityWalletSelection
from trading_system.intents.program_compilation import compile_order_program


def ordinary(product=t.Product.SPOT):
    p,r,c,k=inputs(product)
    if product==t.Product.US_EQUITY:p=change(p,tokenize=False,trading_session='24H')
    return compile_order_program(p,r,c,**k),k['profile']


def test_limit_full_fields_and_original_ids_are_materialized_without_permission():
    plan,profile=ordinary();ids=assign_client_ids(plan,0,'stable-command')
    request=materialize_creation(plan,0,profile,ids)
    values=dict(request.form_parameters())
    assert values['price']=='81490.00' and values['type']=='LIMIT' and values['timeInForce']=='GTC'
    assert values['quantity']==plan.nodes[0].quantity.value
    assert values['newClientOrderId']==ids.members[0].client_id
    assert not request.execution_authorized and request.endpoint.value=='/api/v3/order'
    verify_creation(request,plan,profile,ids)


def test_quote_market_never_turns_into_base_quantity():
    p,r,c,k=inputs();p=change(p,price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None))
    plan=compile_order_program(p,r,c,**k)
    request=materialize_creation(plan,0,k['profile'],assign_client_ids(plan,0,'quote-command'))
    values=dict(request.form_parameters())
    assert values['quoteOrderQty']=='4000' and 'quantity' not in values and 'price' not in values


@pytest.mark.parametrize('template',['OCO','OTO','OTOCO','OPO','OPOCO'])
def test_native_list_has_one_request_and_complete_explicit_member_ids(template):
    plan=make_plan(template,'VENUE_NATIVE');profile=inputs()[3]['profile']
    ids=assign_client_ids(plan,0,'list-command')
    request=materialize_creation(plan,0,profile,ids);values=dict(request.form_parameters())
    assert values['listClientOrderId']==ids.parent_client_id
    assert len(ids.members)==len(plan.nodes)
    assert len({v.client_id for v in ids.members}|{ids.parent_client_id})==len(plan.nodes)+1
    if template.startswith('OPO'):assert not any('quantity' in k.lower() and k.startswith('pending') for k in values)
    assert 'timestamp' not in values and not request.execution_authorized


def test_batch_has_member_ids_and_no_fake_native_parent_id():
    import json
    plan=make_plan('BATCH','VENUE_NATIVE',t.Product.USD_M_PERP)
    profile=inputs(t.Product.USD_M_PERP)[3]['profile'];ids=assign_client_ids(plan,0,'batch-command')
    request=materialize_creation(plan,0,profile,ids)
    assert ids.parent_client_id is None
    fields=dict(request.form_parameters());assert set(fields)=={'batchOrders'}
    orders=json.loads(fields['batchOrders'])
    assert len(orders)==len(plan.nodes)
    assert all(o['symbol']==profile.venue_symbol and o['newClientOrderId'] for o in orders)


@pytest.mark.parametrize('product',[t.Product.SPOT,t.Product.USD_M_PERP])
@pytest.mark.parametrize('template',['TWAP','VP'])
def test_execution_algorithms_use_parent_client_algo_id_only(product,template):
    plan=make_plan(template,'VENUE_NATIVE',product);profile=inputs(product)[3]['profile']
    ids=assign_client_ids(plan,0,'algo-command')
    fields=dict(materialize_creation(plan,0,profile,ids).form_parameters())
    assert len(ids.parent_client_id)==32 and fields['clientAlgoId']==ids.parent_client_id
    assert ids.members==() and 'newClientOrderId' not in fields
    assert 'urgency' in fields if template=='VP' else 'duration' in fields


def test_local_algorithm_is_internal_activation_not_external_submit():
    plan=make_plan('TWAP','LOCAL_SYNTHETIC');profile=inputs()[3]['profile']
    with pytest.raises(ValueError,match='LOCAL_ALGORITHM_ACTIVATION'):
        assign_client_ids(plan,0,'local-command')


def test_profile_or_materialized_price_tampering_is_rejected():
    plan,profile=ordinary();ids=assign_client_ids(plan,0,'original-command')
    request=materialize_creation(plan,0,profile,ids)
    with pytest.raises(ValueError,match='PROFILE'):
        materialize_creation(plan,0,replace(profile,venue_symbol='OTHER'),ids)
    fields=tuple(replace(f,value='1') if f.name=='price' else f for f in request.fields)
    with pytest.raises(ValueError,match='CREATION_REQUEST_CHANGED'):
        verify_creation(replace(request,fields=fields),plan,profile,ids)


def test_different_original_command_cannot_reuse_assigned_client_ids():
    plan,profile=ordinary();first=assign_client_ids(plan,0,'original-command')
    second=assign_client_ids(plan,0,'different-command')
    assert first.members[0].client_id!=second.members[0].client_id
    request=materialize_creation(plan,0,profile,first)
    with pytest.raises(ValueError,match='CREATION_REQUEST_CHANGED'):
        verify_creation(request,plan,profile,second)


def test_equity_requires_current_explicit_wallet_and_original_quote_currency():
    plan,profile=ordinary(t.Product.US_EQUITY);ids=assign_client_ids(plan,0,'stock-command')
    with pytest.raises(ValueError,match='EQUITY_PAYMENT_WALLET_REQUIRED'):
        materialize_creation(plan,0,profile,ids)
    selection=EquityWalletSelection(scope=plan.scope,profile_hash=plan.profile_hash,
        quote_asset=plan.nodes[0].notional_asset,wallet='MAIN',evidence=profile.evidence[0],valid_until=profile.valid_until)
    now=profile.as_of
    request=materialize_creation(plan,0,profile,ids,wallet_selection=selection,now=now)
    fields=dict(request.form_parameters())
    assert fields['walletType']=='MAIN' and fields['quoteAsset']==plan.nodes[0].notional_asset
    assert fields['clientOrderId']==ids.members[0].client_id and fields['tokenize']=='false'
    verify_creation(request,plan,profile,ids,wallet_selection=selection,now=now)
    changed=materialize_creation(plan,0,profile,ids,wallet_selection=replace(selection,wallet='CARD'),now=now)
    with pytest.raises(ValueError,match='CREATION_REQUEST_CHANGED'):
        verify_creation(changed,plan,profile,ids,wallet_selection=selection,now=now)
    with pytest.raises(ValueError,match='EQUITY_PAYMENT_WALLET_STALE'):
        verify_creation(request,plan,profile,ids,wallet_selection=selection,now=profile.valid_until)


def test_source_symbol_cannot_override_the_bound_profile():
    from trading_system.intents.program_compilation import FieldName
    plan,profile=ordinary();r=plan.requests[0]
    changed=replace(plan,requests=(replace(r,parameters=tuple(replace(p,value='OTHER')
        if p.name==FieldName.SYMBOL else p for p in r.parameters)),))
    with pytest.raises(ValueError,match='CREATION_SYMBOL_PROFILE_MISMATCH'):
        materialize_creation(changed,0,profile,assign_client_ids(changed,0,'symbol-command'))


def test_unsigned_request_roundtrip_keeps_every_field_and_identity():
    from trading_system.execution.creation_requests import VenueCreationRequest
    from trading_system.storage.models import encode,decode
    plan,profile=ordinary();ids=assign_client_ids(plan,0,'roundtrip-command')
    request=materialize_creation(plan,0,profile,ids)
    restored=decode(VenueCreationRequest,encode(request))
    assert restored==request and restored.form_parameters()==request.form_parameters()
    assert verify_creation(restored,plan,profile,ids)==verify_creation(request,plan,profile,ids)


def test_equity_wire_quote_cannot_disagree_with_reserved_wallet_currency():
    from trading_system.intents.program_compilation import FieldName
    plan,profile=ordinary(t.Product.US_EQUITY);r=plan.requests[0]
    changed=replace(plan,requests=(replace(r,parameters=tuple(replace(p,value='OTHER_QUOTE')
        if p.name==FieldName.QUOTE_ASSET else p for p in r.parameters)),))
    selection=EquityWalletSelection(scope=plan.scope,profile_hash=plan.profile_hash,
        quote_asset=plan.nodes[0].notional_asset,wallet='MAIN',evidence=profile.evidence[0],valid_until=profile.valid_until)
    with pytest.raises(ValueError,match='CREATION_QUOTE_ASSET_BINDING_MISMATCH'):
        materialize_creation(changed,0,profile,assign_client_ids(changed,0,'currency-command'),
            wallet_selection=selection,now=profile.as_of)


def test_generated_client_ids_cannot_be_rebound_to_a_different_durable_command():
    plan,profile=ordinary();original=assign_client_ids(plan,0,'original-command')
    reassigned=replace(original,command_id='different-command')
    with pytest.raises(ValueError,match='CREATION_CLIENT_ASSIGNMENT_CHANGED'):
        materialize_creation(plan,0,profile,reassigned)


def test_boolean_and_integer_equality_cannot_hide_different_wire_requests():
    plan,profile=ordinary(t.Product.USD_M_PERP);ids=assign_client_ids(plan,0,'typed-command')
    original=materialize_creation(plan,0,profile,ids)
    changed=replace(original,fields=tuple(replace(f,value=0) if f.name=='reduceOnly' else f for f in original.fields))
    assert original==changed  # Python value equality is insufficient here.
    assert dict(original.form_parameters())['reduceOnly']=='false'
    assert dict(changed.form_parameters())['reduceOnly']=='0'
    with pytest.raises(ValueError,match='CREATION_REQUEST_CHANGED'):
        verify_creation(changed,plan,profile,ids)


def test_spot_compilation_cannot_be_redirected_to_a_futures_endpoint():
    from trading_system.intents.program_compilation import Namespace
    plan,profile=ordinary()
    changed=replace(plan,requests=(replace(plan.requests[0],namespace=Namespace.USD_M_ORDINARY),))
    with pytest.raises(ValueError,match='CREATION_PRODUCT_ROUTE_MISMATCH'):
        materialize_creation(changed,0,profile,assign_client_ids(changed,0,'route-command'))


def test_venue_generated_list_child_cannot_become_a_standalone_local_send():
    from trading_system.intents.program_compilation import RequestPlan,Namespace,Operation,Parameter,FieldName
    plan=make_plan('OCO','VENUE_NATIVE');profile=inputs()[3]['profile'];node=plan.nodes[0]
    assert node.trusted_origin=='VENUE_GENERATED_CHILD'
    request=RequestPlan(namespace=Namespace.SPOT_ORDINARY,operation=Operation.ORDER,
        node_ids=(node.original.node_id,),parameters=(Parameter(name=FieldName.SYMBOL,value=profile.venue_symbol),*node.parameters))
    changed=replace(plan,requests=(request,))
    with pytest.raises(ValueError,match='VENUE_CHILD_REQUIRES_ORIGINAL_NATIVE_PARENT'):
        materialize_creation(changed,0,profile,assign_client_ids(changed,0,'duplicate-child-command'))
