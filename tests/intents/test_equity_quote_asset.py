from dataclasses import replace

import pytest

from test_program_compilation import inputs,change,price
from trading_system.contracts import types as t
from trading_system.contracts.order_program import QuantitySpec,TimeInForceSpec
from trading_system.intents.program_compilation import compile_order_program


@pytest.mark.parametrize('side',['BUY','SELL'])
@pytest.mark.parametrize('market',[False,True])
@pytest.mark.parametrize('quote',['USDC','USD'])
def test_equity_request_explicitly_binds_reserved_quote_asset(side,market,quote):
    proposal,rules,caps,options=inputs(t.Product.US_EQUITY)
    rules=replace(rules,quote_asset=quote)
    proposal=change(proposal,side=side,tokenize=False,trading_session=None if market else 'RTH',
        price=price('MARKET') if market else price('FIXED_LIMIT',value='81490'),
        quantity=QuantitySpec(kind='QUOTE_NOTIONAL' if market and side=='BUY' else 'BASE_QUANTITY',
            value='81490' if market and side=='BUY' else '1',source_node=None),
        tif=TimeInForceSpec(kind='DAY',expires_at=None))
    plan=compile_order_program(proposal,rules,caps,**options)
    fields={p.wire_name:p.value for p in plan.requests[0].parameters}
    assert fields['quoteAsset']==quote
    assert fields['tokenize'] is False
    assert fields['orderType']==('MARKET' if market else 'LIMIT')
    assert plan.nodes[0].notional_asset==quote
