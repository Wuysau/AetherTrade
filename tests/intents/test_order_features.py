from dataclasses import replace
import json
from pathlib import Path
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.order_program import OrderProgramCodec
from trading_system.intents.order_features import requested_order_features
from trading_system.intents.semantic_validation import ProposalRejected

def program():return OrderProgramCodec().decode(json.loads(Path('tests/fixtures/orders-v2/program.paper.json').read_text()))

def test_spot_limit_requires_actual_quantity_tif_execution_rules_features():
    result=requested_order_features(program(),t.Product.SPOT)
    assert set(result)=={'SPOT-LIMIT','SPOT-QTY','SPOT-TIF','SPOT-EXECUTION_RULES'}


def test_usdm_conditional_type_and_trigger_features_are_not_spot_aliases():
    p=program();node=p.nodes[0]
    order=replace(node.order,trigger=replace(node.order.trigger,kind='STATIC',purpose='STOP_LOSS',source='MARK_PRICE',price='81000',price_protect=True))
    p=replace(p,nodes=(replace(node,order=order),))
    assert {'USDM-STOP','USDM-TRIGGER'}<=set(requested_order_features(p,t.Product.USD_M_PERP))
    with pytest.raises(ProposalRejected,match='SPOT_TRIGGER_SOURCE_UNSUPPORTED'):requested_order_features(p,t.Product.SPOT)


def test_every_derived_feature_remains_in_the_complete_catalog():
    catalog=json.loads(Path('qualification/binance_orders/reference/binance-order-capability-catalog.json').read_text(encoding='utf-8'))
    ids={f['id'] for f in catalog['features']}
    for product in (t.Product.SPOT,t.Product.USD_M_PERP):assert set(requested_order_features(program(),product))<=ids


def test_real_stock_identity_does_not_default_from_crypto_shape():
    with pytest.raises(ProposalRejected,match='REAL_EQUITY_REQUIRED'):requested_order_features(program(),t.Product.US_EQUITY)


def test_market_post_only_is_not_reclassified_as_limit_maker():
    p=program();n=p.nodes[0]
    o=replace(n.order,post_only=True,price=replace(n.order.price,kind='MARKET',value=None))
    with pytest.raises(ProposalRejected,match='POST_ONLY_REQUIRES_LIMIT'):requested_order_features(replace(p,nodes=(replace(n,order=o),)),t.Product.SPOT)


def test_spot_trailing_never_interprets_percent_as_bips():
    p=program();n=p.nodes[0]
    o=replace(n.order,trigger=replace(n.order.trigger,kind='TRAILING',purpose='STOP_LOSS',source='LAST',offset='1',offset_unit='PERCENT',price_protect=False))
    with pytest.raises(ProposalRejected,match='SPOT_TRAILING_REQUIRES_BIPS'):requested_order_features(replace(p,nodes=(replace(n,order=o),)),t.Product.SPOT)
