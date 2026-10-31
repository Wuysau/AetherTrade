"""Intentional RED capability probes against frozen V2, not venue tests.

Exit 1 documents that V2 cannot represent the required new unit/group facts.
Do not fix these probes by weakening V2; introduce an explicit additive version.
"""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'src'))
from trading_system.contracts import types as t
from trading_system.contracts.order_lifecycle import ParentAuthority,AssetBudget,ProgramState,ChildObservation,ChildFill,apply_child_observation

NOW='2026-10-29T06:00:00Z'
scope=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='red-only',product=t.Product.USD_M_PERP,instrument_uid='fixture:BTCUSDT',venue_leg='BOTH')
a=ParentAuthority(contract_version='2026-10-28',program_id='red',program_hash='a'*64,scope=scope,namespace='EXECUTION_ALGO',venue='BINANCE',parent_command_id='original-mother',parent_client_id='original-client',parent_venue_id='original-native',owner_strategy_id='owner',ownership_epoch=1,authorization_id='grant',funding_intent_id='intent',reservation_ids=('hold',),funding_requirements=(AssetBudget(asset='USDT',amount='20'),),base_asset='BTC',starting_position_quantity='0',allowed_child_sides=('BUY',),maximum_quantity='100',maximum_price='100',maximum_notional='20',quote_asset='USDT',fee_budgets=(),management_policy='MANAGE_UNTIL_FLAT',protection_required=True,evidence=t.EvidenceRef(id='synthetic-parent',hash='b'*64,environment=t.Environment.PAPER,source='RED_PROBE_ONLY',visible_at=NOW))
s=ProgramState(program_id='red',sequence=0,status='ACTIVE',unallocated_quantity='100',children=(),observation_ids=(),restriction_codes=(),protection_quantity='0')
e=ChildObservation(contract_version='2026-10-28',origin='VENUE_GENERATED_CHILD',observation_id='fill-event',scope=scope,venue='BINANCE',namespace='EXECUTION_ALGO',parent_client_id='original-client',parent_venue_id='original-native',child_order_id='child',child_client_id=None,side='BUY',status='FILLED',quantity='100',cumulative_quantity='100',fills=(ChildFill(native_trade_id='trade',quantity='100',price='100',fees=(),occurred_at=NOW),),occurred_at=NOW)
nonunit=apply_child_observation(a,s,e)
spot=replace(scope,product=t.Product.SPOT)
oco_a=replace(a,scope=spot,namespace='ORDER_LIST',starting_position_quantity='1',allowed_child_sides=('SELL',),maximum_quantity='1',maximum_notional='110')
oco_s=replace(s,unallocated_quantity='1',protection_quantity='1')
first=replace(e,scope=spot,namespace='ORDER_LIST',observation_id='leg-a',child_order_id='leg-a',side='SELL',status='SUBMITTED',quantity='1',cumulative_quantity='0',fills=())
a_result=apply_child_observation(oco_a,oco_s,first)
b_result=apply_child_observation(oco_a,a_result.state,replace(first,observation_id='leg-b',child_order_id='leg-b'))
result={'probe':'frozen-v2-capability-gaps','scope':'synthetic offline only','native_qualification':'NOT_RUN','cases':[
 {'case':'explicit nonunit quantity100 price100 multiplier0.001','required_quote_notional':str(Decimal('100')*Decimal('100')*Decimal('0.001')),'v2_formula_notional':'10000','v2_restriction':nonunit.restriction,'new_version_required':'typed unit/multiplier; V2 has no such field'},
 {'case':'independently proven native OCO, two fixed venue legs quantity1, group capacity1','v2_first_restriction':a_result.restriction,'v2_second_restriction':b_result.restriction,'new_version_required':'explicit verified group/membership; V2 has no such field'}]}
print(json.dumps(result,indent=2))
sys.exit(1 if nonunit.restriction or b_result.restriction else 0)
