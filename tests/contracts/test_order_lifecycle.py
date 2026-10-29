from dataclasses import replace
from decimal import Decimal
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.order_lifecycle import *
from trading_system.contracts.canonical import canonical_hash,domain_payload

NOW='2026-10-28T10:00:00Z'
SCOPE=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='fixture-account',product=t.Product.SPOT,instrument_uid='fixture:BTCUSDT',venue_leg='BOTH')
def authority():
 return ParentAuthority(contract_version='2026-10-28',program_id='p',program_hash='a'*64,scope=SCOPE,namespace='EXECUTION_ALGO',venue='BINANCE',parent_command_id='mother',parent_client_id='parent-client',parent_venue_id='parent-native',owner_strategy_id='strategy',ownership_epoch=1,authorization_id='grant',funding_intent_id='intent',reservation_ids=('reserve',),funding_requirements=(AssetBudget(asset='USDT',amount='1010'),),base_asset='BTC',starting_position_quantity='0',allowed_child_sides=('BUY',),maximum_quantity='10',maximum_price='100',maximum_notional='1010',quote_asset='USDT',fee_budgets=(AssetBudget(asset='USDT',amount='10'),),management_policy='MANAGE_UNTIL_FLAT',protection_required=True,evidence=t.EvidenceRef(id='proof',hash='b'*64,environment=t.Environment.PAPER,source='PAPER_MODEL_EVENT',visible_at=NOW))
def state():return ProgramState(program_id='p',sequence=0,status='ACTIVE',unallocated_quantity='10',children=(),observation_ids=(),restriction_codes=(),protection_quantity='0')
def event(**kw):
 values=dict(contract_version='2026-10-28',origin='VENUE_GENERATED_CHILD',observation_id='event',scope=SCOPE,venue='BINANCE',namespace='EXECUTION_ALGO',parent_client_id='parent-client',parent_venue_id='parent-native',child_order_id='native-child',child_client_id=None,side='BUY',status='SUBMITTED',quantity='2',cumulative_quantity='0',fills=(),occurred_at=NOW)
 return ChildObservation(**dict(values,**kw))

def test_parent_cancel_race_preserves_evidence_and_restricts():
 result=apply_child_observation(authority(),replace(state(),status='CANCEL_PENDING'),event())
 assert result.restriction=='PARENT_CANCEL_RACE'
 assert result.state.status=='RECONCILIATION_REQUIRED'


def test_unknown_parent_never_auto_assigns_children():
 result=apply_child_observation(authority(),replace(state(),status='SUBMIT_UNKNOWN'),event())
 assert result.restriction=='PARENT_STATE_UNKNOWN' and not result.state.children


def test_native_child_fills_and_remaining_possible_exposure_are_bounded():
 fill=ChildFill(native_trade_id='trade1',quantity='1',price='99',fees=(AssetBudget(asset='USDT',amount='0.1'),),occurred_at=NOW)
 result=apply_child_observation(authority(),state(),event(status='PARTIALLY_FILLED',cumulative_quantity='1',fills=(fill,)))
 assert result.restriction is None and result.state.unallocated_quantity=='8'
 assert result.state.protection_quantity=='1'
 over=event(observation_id='second',child_order_id='second-child',quantity='9')
 assert apply_child_observation(authority(),result.state,over).restriction=='PARENT_QUANTITY_EXCEEDED'

@pytest.mark.parametrize('update,code',[
 ({'parent_venue_id':'foreign'},'PARENT_LINK_UNVERIFIED'),
 ({'namespace':'ORDER_LIST'},'PARENT_LINK_UNVERIFIED'),
 ({'status':'CANCEL_UNKNOWN'},'CHILD_STATE_UNKNOWN'),
 ({'cumulative_quantity':'3'},'CHILD_OVERFILL'),
 ({'cumulative_quantity':'1'},'FILL_DETAILS_MISSING')])
def test_child_faults_are_not_fills_or_permissions(update,code):
 assert apply_child_observation(authority(),state(),event(**update)).restriction==code


def test_duplicate_trade_identity_cannot_change_fees():
 fill=ChildFill(native_trade_id='trade1',quantity='1',price='99',fees=(),occurred_at=NOW)
 first=apply_child_observation(authority(),state(),event(status='PARTIALLY_FILLED',cumulative_quantity='1',fills=(fill,)))
 changed=event(observation_id='changed',status='PARTIALLY_FILLED',cumulative_quantity='1',fills=(replace(fill,price='98'),))
 assert apply_child_observation(authority(),first.state,changed).restriction=='TRADE_CONTENT_CONFLICT'


def test_cancel_then_late_fill_requires_reconciliation():
 first=apply_child_observation(authority(),state(),event(status='CANCELED'))
 fill=ChildFill(native_trade_id='late',quantity='2',price='99',fees=(),occurred_at=NOW)
 assert apply_child_observation(authority(),first.state,event(observation_id='late',status='FILLED',cumulative_quantity='2',fills=(fill,))).restriction=='CANCEL_FILL_RACE'


def test_venue_observation_cannot_become_application_write():
 with pytest.raises(ValueError,match='cannot become'):
  ApplicationWrite(owner_strategy_id='strategy',contract_version='2026-10-28',command_id='c',program_id='p',scope=SCOPE,origin='VENUE_GENERATED_CHILD',operation='SUBMIT',parent_command_id='mother',target_venue_id=None,canonical_program_hash='a'*64,ownership_epoch=1,execution_epoch=1,policy_revision=1,durable_record_ref='committed',state='DURABLE_READY',created_at=NOW)


def test_decimal_overfill_is_not_rounded_away():
    f=ChildFill(native_trade_id='precise',quantity='1.00000000000000000000000000001',price='99',fees=(),occurred_at=NOW)
    result=apply_child_observation(authority(),state(),event(cumulative_quantity='1',status='PARTIALLY_FILLED',fills=(f,)))
    assert result.restriction=='FILL_DETAILS_MISSING'


def test_restored_state_cannot_exceed_parent_quantity():
    assert apply_child_observation(replace(authority(),maximum_quantity='1'),state(),event()).restriction=='PARENT_STATE_BUDGET_MISMATCH'


def test_partial_state_cannot_regress_to_submitted():
    f=ChildFill(native_trade_id='t',quantity='1',price='99',fees=(),occurred_at=NOW)
    first=apply_child_observation(authority(),state(),event(status='PARTIALLY_FILLED',cumulative_quantity='1',fills=(f,)))
    assert apply_child_observation(authority(),first.state,event(observation_id='regress',status='SUBMITTED',cumulative_quantity='1',fills=())).restriction=='ORDER_STATE_REGRESSION'
