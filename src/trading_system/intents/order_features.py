"""Requested feature derivation, separate from capability qualification/authority.

All official feature IDs come from the retained catalog. This never infers a
native permission from an enum or translates native lists to software orders.
"""
from trading_system.contracts import types as t
from trading_system.contracts.order_program import OrderProgram,OrderProgramCodec
from .semantic_validation import require

LISTS={'OCO','OTO','OTOCO','OPO','OPOCO'}

def requested_order_features(program:OrderProgram,product:t.Product)->tuple[str,...]:
    OrderProgramCodec().encode(program)
    result=set()
    if program.template in LISTS:
        require(product==t.Product.SPOT,'NATIVE_LIST_PRODUCT_UNSUPPORTED')
        result.add('SPOT-LIST-'+program.template)
    if program.template in ('TWAP','VP'):
        require(product in (t.Product.SPOT,t.Product.USD_M_PERP),'EXECUTION_ALGORITHM_PRODUCT_UNSUPPORTED')
        result.add(('SPOT' if product==t.Product.SPOT else 'USD_M_PERP')+'-EXEC-'+program.template)
    if program.template=='BATCH':
        require(product==t.Product.USD_M_PERP,'NATIVE_BATCH_PRODUCT_UNSUPPORTED')
        result.add('USDM-BATCH')
    for node in program.nodes:
        order=node.order
        limit=order.price.kind in ('FIXED_LIMIT','PEG','PRICE_MATCH')
        triggered=order.trigger.kind!='NONE'
        require(not order.post_only or limit,'POST_ONLY_REQUIRES_LIMIT')
        if product==t.Product.SPOT:
            require(order.price.kind!='PRICE_MATCH','PRICE_MATCH_PRODUCT_UNSUPPORTED')
            if triggered:
                require(order.trigger.source=='LAST','SPOT_TRIGGER_SOURCE_UNSUPPORTED')
                name=order.trigger.purpose+('_LIMIT' if limit else '')
                result.add('SPOT-'+name)
                if order.trigger.kind=='TRAILING':
                    require(order.trigger.offset_unit=='BIPS','SPOT_TRAILING_REQUIRES_BIPS')
                    result.add('SPOT-TRAILING')
            else:result.add('SPOT-'+('LIMIT_MAKER' if order.post_only else 'LIMIT' if limit else 'MARKET'))
            result.add('SPOT-QTY')
            if order.tif.kind!='NONE':result.add('SPOT-TIF')
            if order.price.kind=='PEG':result.add('SPOT-PEG')
            if order.iceberg_quantity is not None:result.add('SPOT-ICEBERG')
            if order.routing=='SOR':result.add('SPOT-SOR')
            if order.stp!='NONE':result.add('SPOT-STP')
            result.add('SPOT-EXECUTION_RULES')
        elif product==t.Product.USD_M_PERP:
            require(order.price.kind!='PEG' and order.iceberg_quantity is None and order.routing=='STANDARD','PRODUCT_MODIFIER_UNSUPPORTED')
            if order.trigger.kind=='TRAILING':
                require(not limit and order.trigger.purpose=='STOP_LOSS','TRAILING_TYPE_UNSUPPORTED')
                result.add('USDM-TRAILING_STOP_MARKET')
            elif triggered:
                name=('STOP' if order.trigger.purpose=='STOP_LOSS' else 'TAKE_PROFIT')+('' if limit else '_MARKET')
                result.add('USDM-'+name)
            else:result.add('USDM-'+('LIMIT' if limit else 'MARKET'))
            if triggered:result.add('USDM-TRIGGER')
            if order.price.kind=='PRICE_MATCH':result.add('USDM-PRICE_MATCH')
            if order.tif.kind!='NONE':result.add('USDM-TIF')
            if order.reduce_only or order.quantity.kind=='CLOSE_ALL' or order.position_side!='BOTH':result.add('USDM-REDUCTION')
            if order.stp!='NONE':result.add('USDM-STP')
        elif product==t.Product.US_EQUITY:
            require(not triggered and order.price.kind in ('MARKET','FIXED_LIMIT'),'EQUITY_ORDER_TYPE_UNSUPPORTED')
            require(order.tokenize is False,'REAL_EQUITY_REQUIRED')
            result.add('EQUITY-'+('LIMIT' if limit else 'MARKET'))
            result.add('EQUITY-SESSIONS')
        else:require(False,'PRODUCT_UNSUPPORTED')
    return tuple(sorted(result))
