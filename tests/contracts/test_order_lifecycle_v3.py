"""Pure lifecycle v3 proofs; no database, accounts, native qualification or sends."""
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import json
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.order_lifecycle_v3 import *
from trading_system.contracts.canonical import canonical_hash,domain_payload

NOW='2026-10-29T06:00:00Z'
LATER='2026-10-29T06:30:00Z'
SCOPE=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='v3-fixture',product=t.Product.SPOT,instrument_uid='fixture:BTCUSDT',venue_leg='BOTH')

def ref(name='source'):
    return t.EvidenceRef(id=name,hash='b'*64,environment=t.Environment.PAPER,source='SYNTHETIC_VERIFIER_FIXTURE',visible_at=NOW)

def authority(*,exclusive=False,local=False,algorithm=False,multiplier='1',product=t.Product.SPOT):
    scope=replace(SCOPE,product=product)
    node=NodeAllocationV3(node_id='a',group_id='g',side='BUY',origin='APPLICATION_GENERATED_CHILD' if local else 'VENUE_GENERATED_CHILD',
        child_mode='ALGORITHM' if algorithm else 'FIXED_LEG',quantity_mode='FIXED',quantity_limit='1',price_upper='100',activation='IMMEDIATE',source_node_id=None)
    nodes=(node,replace(node,node_id='b')) if exclusive else (node,)
    group=AllocationGroupV3(group_id='g',node_ids=tuple(n.node_id for n in nodes),aggregation='NATIVE_EXCLUSIVE' if exclusive and not local else 'SUM',
        quantity_limit='2' if exclusive and local else '1',notional_limit='200' if exclusive and local else '100')
    return ParentAuthorityV3(contract_version=VERSION,authority_id='auth',program_id='program',program_hash='1'*64,compilation_hash='2'*64,
        target_allocation_hash='3'*64,funding_admission_hash='4'*64,scope=scope,venue='BINANCE',namespace='ORDINARY' if local else 'ORDER_LIST' if exclusive else 'EXECUTION_ALGO',
        template='OCO' if exclusive else 'TWAP' if algorithm else 'ATOMIC',execution_mode='LOCAL_SYNTHETIC' if local else 'VENUE_NATIVE',
        parent_command_id=None if local else 'original-command',parent_client_id=None if local else 'original-client',owner_strategy_id='owner',ownership_epoch=1,
        authorization_id='grant',funding_intent_id='funding',reservation_ids=('hold',),funding_requirements=(AssetBudget(asset='USDT',amount='210'),),
        units=QuantityTermsV3(quantity_unit='CONTRACT' if product==t.Product.USD_M_PERP else 'BTC',position_unit='CONTRACT' if product==t.Product.USD_M_PERP else 'BASE',
            quantity_to_position='1',notional_multiplier=multiplier,base_asset='BTC',quote_asset='USDT',position_asset_per_unit=None if product==t.Product.USD_M_PERP else '1'),
        unit_evidence=ref('unit'),rule_revision=1,rule_snapshot_hash='5'*64,adapter_build_digest='6'*64,nodes=nodes,groups=(group,),starting_position_quantity='0',
        maximum_notional='210',fee_budgets=(AssetBudget(asset='USDT',amount='10'),AssetBudget(asset='BTC',amount='1')),submission_valid_until=LATER,budget_valid_until=LATER,
        management_policy='MANAGE_UNTIL_FLAT',protection_required=True)


def test_explicit_v3_authority_roundtrip_without_a_venue_id():
    a=authority();codec=VersionedOrderCodec();body=codec.encode('parent-authority',a)
    assert 'parent_venue_id' not in body and body['units']['notional_multiplier']=='1'
    assert codec.decode('parent-authority',body)==a
    bad=dict(body,parent_venue_id='guessed')
    with pytest.raises(Exception):codec.decode('parent-authority',bad)


def test_all_existing_schema_bytes_are_unchanged():
    root=Path(__file__).resolve().parents[2]
    hashes=json.loads((root/'docs/evidence/binance-orders/op-c-lifecycle-v3/schema-baseline.json').read_text(encoding='utf-8'))
    for path,digest in hashes.items():assert sha256((root/path).read_bytes()).hexdigest()==digest,path


def test_every_explicit_new_kind_has_an_exact_packaged_schema():
    from trading_system.contracts.order_codec import KINDS_BY_VERSION
    kinds={'parent-authority','parent-binding','child-membership','native-exclusivity','child-observation',
        'parent-observation','receipt-allocation','lifecycle-proof','program-state'}
    assert kinds<=set(KINDS_BY_VERSION[VERSION])
    root=Path(__file__).resolve().parents[2]
    for kind in kinds:
        name=kind+'.v'+VERSION+'.schema.json'
        assert (root/'contracts'/name).read_bytes()==(root/'src/trading_system/schemas'/name).read_bytes()


def test_v2_records_keep_original_type_and_canonical_hash():
    from test_order_lifecycle import authority as old_authority,event as old_event,state as old_state
    from trading_system.contracts.order_lifecycle import ParentAuthority,ChildObservation,ProgramState
    codec=VersionedOrderCodec()
    for kind,record,cls in [('parent-authority',old_authority(),ParentAuthority),('child-observation',old_event(),ChildObservation),('program-state',old_state(),ProgramState)]:
        raw=codec.encode(kind,record);digest=canonical_hash(raw)
        restored=codec.decode(kind,raw)
        assert type(restored) is cls and codec.hash(kind,restored)==digest
        with pytest.raises(Exception):codec.decode(kind,raw,version=VERSION)


def test_local_activation_cannot_claim_native_parent_or_exclusive_discount():
    a=authority(exclusive=True,local=True)
    with pytest.raises(ValueError,match='Local activation'):
        replace(a,parent_command_id='invented-parent')
    with pytest.raises(ValueError,match='Native list exclusivity'):
        replace(a,groups=(replace(a.groups[0],aggregation='NATIVE_EXCLUSIVE'),))


def test_derivative_position_units_cannot_be_declared_as_collateral_assets():
    a=authority(product=t.Product.USD_M_PERP,multiplier='0.001')
    with pytest.raises(ValueError,match='Derivative collateral'):
        replace(a,units=replace(a.units,position_asset_per_unit='0.001'))


def test_node_groups_cannot_double_assign_or_hide_a_member():
    a=authority(exclusive=True)
    with pytest.raises(ValueError):replace(a,groups=(a.groups[0],replace(a.groups[0],group_id='other')))
    with pytest.raises(ValueError):replace(a,groups=(replace(a.groups[0],node_ids=('a',)),))
