"""Offline semantic compilation evidence, never native qualification."""
from dataclasses import replace
from decimal import Decimal

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.order_program import (
    ActivationLink, AlgorithmSpec, PriceSpec, QuantitySpec, TimeInForceSpec, TriggerSpec,
)
from trading_system.intents.product_rules import (
    DecimalRange, EquityRules, ExecutionAlgorithmRules, PositionMode, SpotRules,
    TrustedProductRules, UsdMRules,
)
from trading_system.intents.program_compilation import compile_order_program, FieldName, Namespace, Timing
from trading_system.intents.semantic_validation import ProposalRejected
from test_order_program_compiler import fixture


def inputs(product=t.Product.SPOT):
    p, r, c, k = fixture()
    scope = replace(k['scope'], product=product)
    r = replace(r, scope=scope, allowed_tifs=('GTC', 'IOC', 'FOK', 'GTX', 'GTD', 'DAY', 'RPI'),
                maximum_callback_percent='20')
    features = ('SPOT-MARKET', 'SPOT-LIMIT', 'SPOT-LIMIT_MAKER', 'SPOT-STOP_LOSS',
        'SPOT-STOP_LOSS_LIMIT', 'SPOT-TAKE_PROFIT', 'SPOT-TAKE_PROFIT_LIMIT', 'SPOT-QTY',
        'SPOT-TIF', 'SPOT-EXECUTION_RULES', 'SPOT-TRAILING', 'SPOT-PEG', 'SPOT-ICEBERG',
        'SPOT-STP', 'SPOT-SOR', 'SPOT-LIST-OCO', 'SPOT-LIST-OTO', 'SPOT-LIST-OTOCO',
        'SPOT-LIST-OPO', 'SPOT-LIST-OPOCO', 'SPOT-EXEC-TWAP', 'SPOT-EXEC-VP',
        'USDM-MARKET', 'USDM-LIMIT', 'USDM-STOP', 'USDM-STOP_MARKET', 'USDM-TAKE_PROFIT',
        'USDM-TAKE_PROFIT_MARKET', 'USDM-TRAILING_STOP_MARKET', 'USDM-TIF', 'USDM-TRIGGER',
        'USDM-REDUCTION', 'USDM-PRICE_MATCH', 'USDM-STP', 'USDM-BATCH',
        'USD_M_PERP-EXEC-TWAP', 'USD_M_PERP-EXEC-VP', 'EQUITY-MARKET', 'EQUITY-LIMIT', 'EQUITY-SESSIONS')
    c = replace(c, scope=scope, capability_ids=features)
    bounds = DecimalRange(minimum='0.000001', maximum='10000000')
    profile = TrustedProductRules(profile_id='profile:fixture', scope=scope,
        rule_snapshot_ref=r.snapshot_id, rule_revision=r.revision, adapter_build_digest=c.adapter_build_digest,
        venue_symbol='BTCUSDT', allowed_order_types=('MARKET', 'LIMIT', 'LIMIT_MAKER', 'STOP_LOSS',
        'STOP_LOSS_LIMIT', 'TAKE_PROFIT', 'TAKE_PROFIT_LIMIT', 'STOP', 'STOP_MARKET',
        'TAKE_PROFIT_MARKET', 'TRAILING_STOP_MARKET'), quantity=bounds, price=bounds,
        notional=DecimalRange(minimum='10', maximum='10000000'), market_quantity_step=r.quantity_step,
        market_quantity=bounds, evidence=r.evidence, as_of=r.as_of, valid_until=r.valid_until,
        execution_price_envelope=DecimalRange(minimum='80000',maximum='90000'),
        spot=SpotRules(quote_market_quantity=True, sor_enabled=True, maximum_iceberg_parts=100,
            trailing_above_bips=DecimalRange(minimum='10', maximum='2000'),
            trailing_below_bips=DecimalRange(minimum='10', maximum='2000'), allowed_stp=('NONE', 'EXPIRE_BOTH')) if product==t.Product.SPOT else None,
        usd_m=UsdMRules(position_mode=PositionMode.ONE_WAY, callback_percent=DecimalRange(minimum='0.1', maximum='20'),
            quantity_unit='CONTRACT',contract_multiplier='1',
            allowed_price_match=('OPPONENT_5',), allowed_stp=('NONE', 'EXPIRE_BOTH')) if product==t.Product.USD_M_PERP else None,
        equity=EquityRules(allowed_sessions=('RTH', 'EXTENDED', '24H')) if product==t.Product.US_EQUITY else None,
        algorithm=ExecutionAlgorithmRules(minimum_duration_seconds=300, maximum_duration_seconds=86400,
            notional=DecimalRange(minimum='10', maximum='10000000')))
    k.update(scope=scope, expected_capabilities_hash=canonical_hash(domain_payload(c)), profile=profile)
    return p, r, c, k


def change(p, **fields):
    node = p.program.nodes[0]
    return replace(p, program=replace(p.program, nodes=(replace(node, order=replace(node.order, **fields)),)))


def price(kind='MARKET', **kw):
    return PriceSpec(kind=kind, value=kw.get('value'), peg_type=kw.get('peg_type'),
        offset_levels=kw.get('offset_levels'), price_match=kw.get('price_match'))


def trigger(kind='STATIC', **kw):
    return TriggerSpec(kind=kind, purpose=kw.get('purpose', 'STOP_LOSS'), source=kw.get('source', 'LAST'),
        price=kw.get('price', '81330.00') if kind=='STATIC' else None,
        activation_price=kw.get('activation_price'), offset=kw.get('offset'), offset_unit=kw.get('offset_unit'),
        price_protect=kw.get('price_protect', False))


def fields(node):
    return {parameter.name: parameter.value for parameter in node.parameters}


def test_exact_limit_fixture_preserves_originals_and_hashes():
    p,r,c,k=inputs(); result=compile_order_program(p,r,c,**k)
    f=fields(result.nodes[0])
    assert f[FieldName.PRICE]=='81490.00'
    assert f[FieldName.QUANTITY]=='0.049085'
    assert Decimal(f[FieldName.QUANTITY])*Decimal(f[FieldName.PRICE])<=4000
    assert result.original_proposal.exit_plan.stop_price=='81330.00'
    assert result.program_hash==canonical_hash(domain_payload(p.program))
    assert result.original_program==p.program and not result.execution_authorized


@pytest.mark.parametrize('product,source,limit,expected,namespace', [
    (t.Product.SPOT,'LAST',False,'STOP_LOSS',Namespace.SPOT_ORDINARY),
    (t.Product.SPOT,'LAST',True,'STOP_LOSS_LIMIT',Namespace.SPOT_ORDINARY),
    (t.Product.USD_M_PERP,'MARK_PRICE',False,'STOP_MARKET',Namespace.USD_M_CONDITIONAL),
    (t.Product.USD_M_PERP,'CONTRACT_PRICE',True,'STOP',Namespace.USD_M_CONDITIONAL),
])
def test_conditional_type_source_and_route_are_lossless(product,source,limit,expected,namespace):
    p,r,c,k=inputs(product)
    p=change(p, quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),
        price=p.program.nodes[0].order.price if limit else price(),
        trigger=trigger(source=source), tif=TimeInForceSpec(kind='GTC' if limit else 'NONE',expires_at=None))
    result=compile_order_program(p,r,c,**k); f=fields(result.nodes[0])
    assert result.nodes[0].order_type==expected and result.requests[0].namespace==namespace
    assert f[FieldName.TRIGGER_PRICE if product==t.Product.USD_M_PERP else FieldName.STOP_PRICE]=='81330.00'
    assert result.nodes[0].original.order.trigger.source==source


@pytest.mark.parametrize('callback',['0.1','10','20'])
def test_usdm_trailing_current_callback_range(callback):
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),price=price(),
        trigger=trigger('TRAILING',source='MARK_PRICE',offset=callback,offset_unit='PERCENT',activation_price='81330.00'),
        tif=TimeInForceSpec(kind='NONE',expires_at=None))
    result=compile_order_program(p,r,c,**k); f=fields(result.nodes[0])
    assert f[FieldName.CALLBACK_RATE]==callback and f[FieldName.ACTIVATE_PRICE]=='81330.00'
    assert f[FieldName.WORKING_TYPE]=='MARK_PRICE'


@pytest.mark.parametrize('side,quantity_kind,field', [('BUY','QUOTE_NOTIONAL',FieldName.NOTIONAL),('SELL','BASE_QUANTITY',FieldName.QUANTITY)])
def test_real_equity_market_asymmetry(side,quantity_kind,field):
    p,r,c,k=inputs(t.Product.US_EQUITY)
    p=change(p,side=side,price=price(),tokenize=False,quantity=QuantitySpec(kind=quantity_kind,value='4000' if side=='BUY' else '1',source_node=None),
        tif=TimeInForceSpec(kind='DAY',expires_at=None))
    result=compile_order_program(p,r,c,**k); f=fields(result.nodes[0])
    assert field in f and f[FieldName.TOKENIZE] is False
    assert FieldName.PRICE not in f and FieldName.TRADING_SESSION not in f
    assert {param.wire_name:param.value for param in result.requests[0].parameters}['orderType']=='MARKET'
    assert 'type' not in {param.wire_name for param in result.requests[0].parameters}


@pytest.mark.parametrize('mutation,code', [
    ('expired','PROFILE_EXPIRED'),('revision','PRODUCT_RULE_REVISION_CHANGED'),
    ('build','PRODUCT_RULE_BUILD_CHANGED'),('capability','FEATURE_NOT_AVAILABLE'),
    ('peg_quote','QUOTE_SIZING_REQUIRES_FIXED_LIMIT'),('trigger_source','SPOT_TRIGGER_SOURCE_UNSUPPORTED'),
    ('hedge_reduce','HEDGE_REDUCE_ONLY_FORBIDDEN'),('equity_fraction','FRACTIONAL_GTC_SESSION'),
])
def test_fail_closed_before_requests(mutation,code):
    product=t.Product.USD_M_PERP if mutation=='hedge_reduce' else t.Product.US_EQUITY if mutation=='equity_fraction' else t.Product.SPOT
    p,r,c,k=inputs(product)
    if mutation=='expired': k['profile']=replace(k['profile'],valid_until=k['now'])
    if mutation=='revision': k['profile']=replace(k['profile'],rule_revision=2)
    if mutation=='build': k['profile']=replace(k['profile'],adapter_build_digest='c'*64)
    if mutation=='capability': c=replace(c,capability_ids=());k['expected_capabilities_hash']=canonical_hash(domain_payload(c))
    if mutation=='peg_quote': p=change(p,price=price('PEG',peg_type='PRIMARY_PEG',offset_levels=1))
    if mutation=='trigger_source': p=change(p,trigger=trigger(source='BID_ASK'))
    if mutation=='hedge_reduce':
        k['scope']=replace(k['scope'],venue_leg='LONG');r=replace(r,scope=k['scope']);c=replace(c,scope=k['scope'])
        k['profile']=replace(k['profile'],scope=k['scope'],usd_m=replace(k['profile'].usd_m,position_mode=PositionMode.HEDGE))
        k['expected_capabilities_hash']=canonical_hash(domain_payload(c));p=change(p,position_side='LONG',reduce_only=True)
    if mutation=='equity_fraction': p=change(p,tokenize=False,trading_session='RTH',quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.5',source_node=None))
    with pytest.raises(ProposalRejected,match=code):compile_order_program(p,r,c,**k)


def test_native_opo_uses_one_mother_and_staged_net_receipt():
    p,r,c,k=inputs(); working=replace(p.program.nodes[0],role='WORKING')
    pending=replace(working,node_id='exit',role='PENDING',order=replace(working.order,side='SELL',
        quantity=QuantitySpec(kind='DERIVED_RECEIPT',value=None,source_node=working.node_id)))
    program=replace(p.program,template='OPO',execution_mode='VENUE_NATIVE',nodes=(working,pending),
        links=(ActivationLink(source=working.node_id,target=pending.node_id,condition='NET_RECEIPT'),))
    result=compile_order_program(replace(p,program=program),r,c,**k)
    assert len(result.requests)==1 and result.requests[0].native_mother
    assert result.requests[0].namespace==Namespace.SPOT_LIST
    assert result.nodes[1].quantity.kind=='DERIVED_RECEIPT' and result.nodes[1].quantity.value is None
    assert FieldName.QUANTITY not in fields(result.nodes[1])
    assert result.nodes[1].trusted_origin=='VENUE_GENERATED_CHILD'
    assert result.nodes[1].activation_requires_full_fill
    assert all(node.child_origin=='APPLICATION_GENERATED_CHILD' for node in program.nodes)
    assert len(result.requests)==1 and result.requests[0].node_ids==(working.node_id,pending.node_id)


def test_local_oto_children_are_staged_independent_writes():
    p,r,c,k=inputs(); working=replace(p.program.nodes[0],role='WORKING')
    pending=replace(working,node_id='exit',role='PENDING',order=replace(working.order,side='SELL'))
    program=replace(p.program,template='OTO',execution_mode='LOCAL_SYNTHETIC',nodes=(working,pending),
        links=(ActivationLink(source=working.node_id,target=pending.node_id,condition='FULL_FILL'),))
    result=compile_order_program(replace(p,program=program),r,c,**k)
    assert len(result.requests)==2 and result.requests[1].timing==Timing.AFTER_FULL_FILL
    assert all(request.requires_independent_command for request in result.requests)
    assert all(node.trusted_origin=='APPLICATION_GENERATED_CHILD' for node in result.nodes)


def test_old_native_vp_participation_is_rejected_instead_of_dropped():
    p,r,c,k=inputs();node=replace(p.program.nodes[0],role='ALGORITHM_PARENT',order=replace(p.program.nodes[0].order,
        price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None),quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None)))
    p=replace(p,program=replace(p.program,template='VP',execution_mode='VENUE_NATIVE',nodes=(node,),
        algorithm=AlgorithmSpec(kind='VP',duration_seconds=300,participation_bps='100',urgency='LOW')))
    with pytest.raises(ProposalRejected,match='NATIVE_VP_PARTICIPATION_UNMAPPABLE'):compile_order_program(p,r,c,**k)


@pytest.mark.parametrize('purpose,limit,expected', [('TAKE_PROFIT',False,'TAKE_PROFIT'),('TAKE_PROFIT',True,'TAKE_PROFIT_LIMIT')])
def test_spot_take_profit_variants(purpose,limit,expected):
    p,r,c,k=inputs();p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),
        trigger=trigger(purpose=purpose),price=p.program.nodes[0].order.price if limit else price(),
        tif=TimeInForceSpec(kind='GTC' if limit else 'NONE',expires_at=None))
    assert compile_order_program(p,r,c,**k).nodes[0].order_type==expected


def test_spot_quote_market_remains_quote_order_quantity():
    p,r,c,k=inputs();p=change(p,price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None))
    result=compile_order_program(p,r,c,**k); f=fields(result.nodes[0])
    assert f[FieldName.QUOTE_ORDER_QTY]=='4000' and FieldName.QUANTITY not in f


def test_spot_peg_iceberg_stp_and_maker_fields():
    p,r,c,k=inputs();p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),
        price=price('PEG',peg_type='PRIMARY_PEG',offset_levels=5),post_only=True,
        tif=TimeInForceSpec(kind='NONE',expires_at=None),iceberg_quantity='0.01',stp='EXPIRE_BOTH')
    result=compile_order_program(p,r,c,**k);f=fields(result.nodes[0])
    assert f[FieldName.TYPE]=='LIMIT_MAKER' and f[FieldName.PEG_OFFSET_TYPE]=='PRICE_LEVEL'
    assert f[FieldName.PEG_OFFSET_VALUE]==5 and f[FieldName.ICEBERG_QTY]=='0.01'
    assert f[FieldName.STP]=='EXPIRE_BOTH' and FieldName.PRICE not in f


def test_spot_primary_peg_contingent_ioc_is_not_mistaken_for_plain_limit():
    p,r,c,k=inputs();p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),
        price=price('PEG',peg_type='PRIMARY_PEG',offset_levels=1),trigger=trigger(),
        tif=TimeInForceSpec(kind='IOC',expires_at=None))
    assert compile_order_program(p,r,c,**k).nodes[0].order_type=='STOP_LOSS_LIMIT'


@pytest.mark.parametrize('callback,unit,code',[('20.1','PERCENT','CALLBACK_RATE_OUT_OF_RANGE'),('10','BIPS','USDM_TRAILING_COMBINATION_UNSUPPORTED')])
def test_usdm_callback_rejects_wrong_range_or_units(callback,unit,code):
    p,r,c,k=inputs(t.Product.USD_M_PERP);p=change(p,price=price(),
        quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),
        trigger=trigger('TRAILING',source='MARK_PRICE',offset=callback,offset_unit=unit),
        tif=TimeInForceSpec(kind='NONE',expires_at=None))
    with pytest.raises(ProposalRejected,match=code):compile_order_program(p,r,c,**k)


def test_spot_trailing_activation_is_stop_price_and_integer_bips():
    p,r,c,k=inputs();p=change(p,quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),
        trigger=trigger('TRAILING',offset='100',offset_unit='BIPS',activation_price='81330.00'))
    f=fields(compile_order_program(p,r,c,**k).nodes[0])
    assert f[FieldName.TRAILING_DELTA]==100 and f[FieldName.STOP_PRICE]=='81330.00'
    assert FieldName.CALLBACK_RATE not in f


def test_usdm_price_match_gtd_and_stp_keep_exact_fields():
    p,r,c,k=inputs(t.Product.USD_M_PERP);p=change(p,price=price('PRICE_MATCH',price_match='OPPONENT_5'),
        quantity=QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None),stp='EXPIRE_BOTH',
        tif=TimeInForceSpec(kind='GTD',expires_at='2026-10-28T11:00:00Z'))
    f=fields(compile_order_program(p,r,c,**k).nodes[0])
    assert f[FieldName.PRICE_MATCH]=='OPPONENT_5' and FieldName.PRICE not in f
    assert f[FieldName.GOOD_TILL_DATE]==1793185200000 and f[FieldName.STP]=='EXPIRE_BOTH'


def test_usdm_stp_cannot_silently_be_ineffective_with_fok():
    p,r,c,k=inputs(t.Product.USD_M_PERP);p=change(p,stp='EXPIRE_BOTH',tif=TimeInForceSpec(kind='FOK',expires_at=None))
    with pytest.raises(ProposalRejected,match='STP_TIF_INEFFECTIVE'):compile_order_program(p,r,c,**k)


def test_close_all_omits_quantity_and_reduce_only():
    p,r,c,k=inputs(t.Product.USD_M_PERP);p=change(p,side='SELL',price=price(),
        quantity=QuantitySpec(kind='CLOSE_ALL',value=None,source_node=None),trigger=trigger(source='MARK_PRICE',price_protect=True),
        tif=TimeInForceSpec(kind='NONE',expires_at=None))
    f=fields(compile_order_program(p,r,c,**k).nodes[0])
    assert f[FieldName.CLOSE_POSITION] is True and f[FieldName.PRICE_PROTECT] is True
    assert FieldName.QUANTITY not in f and FieldName.REDUCE_ONLY not in f


@pytest.mark.parametrize('template',['OCO','OTOCO','OPOCO'])
def test_native_exclusive_list_shared_and_qualified_fields(template):
    p,r,c,k=inputs();base=p.program.nodes[0]
    q=QuantitySpec(kind='DERIVED_RECEIPT',value=None,source_node=base.node_id) if template=='OPOCO' else QuantitySpec(kind='BASE_QUANTITY',value='0.05',source_node=None)
    stop=replace(base,node_id='stop',role='LEG' if template=='OCO' else 'PENDING',order=replace(base.order,
        side='SELL',quantity=q,trigger=trigger(),price=price(),tif=TimeInForceSpec(kind='NONE',expires_at=None)))
    take=replace(stop,node_id='take',order=replace(stop.order,price=price('FIXED_LIMIT',value='82000.00'),
        trigger=replace(base.order.trigger),post_only=True))
    nodes=(take,stop) if template=='OCO' else (replace(base,role='WORKING'),take,stop)
    links=() if template=='OCO' else tuple(ActivationLink(source=base.node_id,target=n.node_id,
        condition='NET_RECEIPT' if template=='OPOCO' else 'FULL_FILL') for n in (take,stop))
    program=replace(p.program,template=template,execution_mode='VENUE_NATIVE',nodes=nodes,links=links,
        exclusive_groups=(('take','stop'),))
    result=compile_order_program(replace(p,program=program),r,c,**k)
    f={parameter.wire_name:parameter.value for parameter in result.requests[0].parameters}
    prefix='' if template=='OCO' else 'pending'
    assert f['aboveType' if not prefix else 'pendingAboveType']=='LIMIT_MAKER'
    assert f['belowStopPrice' if not prefix else 'pendingBelowStopPrice']=='81330.00'
    assert all(n.trusted_origin=='VENUE_GENERATED_CHILD' for n in result.nodes)
    assert ('pendingQuantity' in f)==(template=='OTOCO')
    assert len(result.requests)==1


@pytest.mark.parametrize('product,limit',[(t.Product.SPOT,False),(t.Product.SPOT,True),(t.Product.USD_M_PERP,False),(t.Product.USD_M_PERP,True)])
def test_native_twap_mother_separate_from_observed_children(product,limit):
    p,r,c,k=inputs(product);o=p.program.nodes[0].order
    node=replace(p.program.nodes[0],role='ALGORITHM_PARENT',order=replace(o,
        price=o.price if limit else price(),quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None),
        tif=TimeInForceSpec(kind='NONE',expires_at=None)))
    p=replace(p,program=replace(p.program,template='TWAP',execution_mode='VENUE_NATIVE',nodes=(node,),
        algorithm=AlgorithmSpec(kind='TWAP',duration_seconds=300,participation_bps=None,urgency=None)))
    result=compile_order_program(p,r,c,**k); request=result.requests[0]
    assert len(result.requests)==1 and node.child_origin=='APPLICATION_GENERATED_CHILD'
    f={param.name:param.value for param in request.parameters}
    assert request.native_mother and request.generated_child_origin=='VENUE_GENERATED_CHILD'
    assert f[FieldName.DURATION]==300 and (FieldName.LIMIT_PRICE in f)==limit
    assert FieldName.TIME_IN_FORCE not in f and FieldName.PRICE not in f


def test_quantity_rounding_requires_explicit_permission():
    p,r,c,k=inputs();k['allow_round_down']=False
    with pytest.raises(ProposalRejected,match='QUANTITY_OFF_GRID'):compile_order_program(p,r,c,**k)


@pytest.mark.parametrize('batch_size,conditional,code',[(6,False,'BATCH_SIZE_EXCEEDED'),(1,True,'BATCH_CONDITIONAL_FORBIDDEN')])
def test_batch_is_bounded_and_ordinary_only(batch_size,conditional,code):
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    if conditional:p=change(p,trigger=trigger(source='MARK_PRICE'))
    nodes=tuple(replace(p.program.nodes[0],node_id='node'+str(i)) for i in range(batch_size))
    p=replace(p,program=replace(p.program,template='BATCH',execution_mode='VENUE_NATIVE',nodes=nodes))
    with pytest.raises(ProposalRejected,match=code):compile_order_program(p,r,c,**k)


@pytest.mark.parametrize('product,duration',[(t.Product.SPOT,None),(t.Product.SPOT,300),(t.Product.USD_M_PERP,None)])
def test_v3_native_vp_uses_urgency_without_inventing_participation(product,duration):
    from trading_system.contracts.order_codec import VersionedOrderCodec
    from trading_system.intents.program_compilation import ProgramCompilation
    from trading_system.storage.models import decode
    p,r,c,k=inputs(product);p=change(p,price=price(),quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None),
        tif=TimeInForceSpec(kind='NONE',expires_at=None))
    payload=domain_payload(p);payload['contract_version']='2026-10-29'
    payload['program'].update(contract_version='2026-10-29',template='VP',execution_mode='VENUE_NATIVE',
        algorithm=dict(kind='VP',duration_seconds=duration,urgency='HIGH'))
    payload['program']['nodes'][0]['role']='ALGORITHM_PARENT'
    proposal=VersionedOrderCodec().decode('order-proposal',payload)
    result=compile_order_program(proposal,r,c,**k)
    request=result.requests[0];f={param.wire_name:param.value for param in request.parameters}
    assert f['urgency']=='HIGH' and ('vpTimeLimit' in f)==(duration is not None)
    assert not any('participation' in field for field in f)
    assert result.program_hash==VersionedOrderCodec().hash('order-program',proposal.program)
    assert decode(ProgramCompilation,domain_payload(result))==result
    assert request.endpoint.value==('/sapi/v1/algo/spot/newOrderVp' if product==t.Product.SPOT else '/sapi/v1/algo/futures/newOrderVp')


def test_v3_futures_vp_cannot_drop_unmapped_duration():
    from trading_system.contracts.order_codec import VersionedOrderCodec
    p,r,c,k=inputs(t.Product.USD_M_PERP);p=change(p,price=price(),quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None),
        tif=TimeInForceSpec(kind='NONE',expires_at=None))
    payload=domain_payload(p);payload['contract_version']='2026-10-29'
    payload['program'].update(contract_version='2026-10-29',template='VP',execution_mode='VENUE_NATIVE',
        algorithm=dict(kind='VP',duration_seconds=300,urgency='HIGH'))
    payload['program']['nodes'][0]['role']='ALGORITHM_PARENT'
    with pytest.raises(ProposalRejected,match='FUTURES_VP_DURATION_UNMAPPABLE'):
        compile_order_program(VersionedOrderCodec().decode('order-proposal',payload),r,c,**k)


def test_base_market_without_execution_price_envelope_does_not_assume_unlimited_spend():
    p,r,c,k=inputs();p=change(p,price=price(),quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None),
        tif=TimeInForceSpec(kind='NONE',expires_at=None))
    k['profile']=replace(k['profile'],execution_price_envelope=None)
    with pytest.raises(ProposalRejected,match='BOUNDED_EXECUTION_PRICE_REQUIRED'):compile_order_program(p,r,c,**k)


def test_base_market_price_envelope_is_visible_to_financial_admission():
    p,r,c,k=inputs();p=change(p,price=price(),quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None),
        tif=TimeInForceSpec(kind='NONE',expires_at=None))
    result=compile_order_program(p,r,c,**k)
    assert result.nodes[0].worst_case_quote_notional=='90000'
    assert result.nodes[0].sizing_price_bound=='90000'


def test_catalog_instrument_handle_is_distinct_from_bound_native_instrument_uid():
    p,r,c,k=inputs()
    handle='catalog:BTC-USDT'
    p=replace(p,instrument_ref=handle,program=replace(p.program,instrument_ref=handle))
    k['expected_instrument_ref']=handle
    result=compile_order_program(p,r,c,**k)
    assert result.original_program.instrument_ref==handle
    assert result.scope.instrument_uid==r.scope.instrument_uid!=handle


@pytest.mark.parametrize('expiry',['9999-12-31T23:59:58.000001Z','9999-12-31T23:59:58.000000001Z'])
def test_gtd_far_future_fraction_is_not_erased_by_float_or_datetime_precision(expiry):
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    p=change(p,tif=TimeInForceSpec(kind='GTD',expires_at=expiry))
    with pytest.raises(ProposalRejected,match='GTD_REQUIRES_WHOLE_SECONDS'):
        compile_order_program(p,r,c,**k)


def test_gtd_far_future_whole_seconds_use_exact_epoch_milliseconds():
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    p=change(p,tif=TimeInForceSpec(kind='GTD',expires_at='9999-12-31T23:59:58Z'))
    result=compile_order_program(p,r,c,**k)
    assert fields(result.nodes[0])[FieldName.GOOD_TILL_DATE]==253402300798000


@pytest.mark.parametrize('expiry',['2026-10-28T10:10:00Z','9999-12-31T23:59:59Z'])
def test_gtd_exact_lower_and_upper_boundaries_remain_exclusive(expiry):
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    p=change(p,tif=TimeInForceSpec(kind='GTD',expires_at=expiry))
    with pytest.raises(ProposalRejected,match='INVALID_GTD_DEADLINE'):
        compile_order_program(p,r,c,**k)


def test_resolved_handle_does_not_relax_scope_instrument_binding():
    p,r,c,k=inputs()
    p=replace(p,instrument_ref='catalog:BTC-USDT',program=replace(p.program,instrument_ref='catalog:BTC-USDT'))
    k['expected_instrument_ref']='catalog:BTC-USDT'
    k['profile']=replace(k['profile'],scope=replace(k['scope'],instrument_uid='native:other'))
    with pytest.raises(ProposalRejected,match='SCOPE_MISMATCH'):
        compile_order_program(p,r,c,**k)


def test_usdm_quote_sizing_uses_explicit_contract_multiplier_and_preserves_native_unit():
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    k['profile']=replace(k['profile'],usd_m=replace(k['profile'].usd_m,contract_multiplier='10'))
    p=replace(change(p,quantity=QuantitySpec(kind='QUOTE_NOTIONAL',value='8149',source_node=None)),target_value='8149')
    result=compile_order_program(p,r,c,**k);node=result.nodes[0]
    assert Decimal(node.quantity.value)==Decimal('0.01')
    assert Decimal(node.worst_case_quote_notional)==Decimal('8149')
    assert node.quantity_unit=='CONTRACT' and node.quantity_multiplier=='10'
    assert node.notional_asset==r.quote_asset


@pytest.mark.parametrize('unit,multiplier',[(None,'1'),('CONTRACT',None),(None,None)])
def test_usdm_missing_quantity_unit_or_multiplier_is_never_assumed_one(unit,multiplier):
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    k['profile']=replace(k['profile'],usd_m=replace(k['profile'].usd_m,quantity_unit=unit,contract_multiplier=multiplier))
    with pytest.raises(ProposalRejected,match='CONTRACT_MULTIPLIER_REQUIRED'):
        compile_order_program(p,r,c,**k)


def test_usdm_base_market_worst_case_exposure_includes_explicit_multiplier():
    p,r,c,k=inputs(t.Product.USD_M_PERP)
    k['profile']=replace(k['profile'],usd_m=replace(k['profile'].usd_m,contract_multiplier='10'))
    p=change(p,price=price(),quantity=QuantitySpec(kind='BASE_QUANTITY',value='1',source_node=None),
        tif=TimeInForceSpec(kind='NONE',expires_at=None))
    result=compile_order_program(p,r,c,**k)
    assert Decimal(result.nodes[0].worst_case_quote_notional)==Decimal('900000')
