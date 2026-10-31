from dataclasses import fields,replace
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.local_child_write import LocalChildWriteV1
from trading_system.contracts.order_codec import VersionedOrderCodec


def child():
    values={f.name:'a'*64 for f in fields(LocalChildWriteV1) if f.name.endswith('_hash')}
    return LocalChildWriteV1(**values,contract_version='2026-10-31',record_kind='LOCAL_CHILD_WRITE_V1',
        command_id='child-1',intent_id='intent-1',program_id='program-1',
        scope=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='paper',product=t.Product.SPOT,
            instrument_uid='BTCUSDT',venue_leg='BOTH'),origin='APPLICATION_GENERATED_CHILD',operation='CREATE_REQUEST',
        service_namespace='BINANCE_SPOT_ORDINARY',activation_id='local-program:1',activation_state_sequence=3,
        venue_client_id='child-client',owner_strategy_id='owner',ownership_epoch=1,execution_epoch=1,
        policy_revision=1,request_index=0,state='DURABLE_READY',durable_record_ref='local-child:1',
        created_at='2026-10-31T00:00:00Z',valid_until='2026-10-31T00:01:00Z')


def test_new_child_roundtrip_preserves_activation_ancestry_without_native_parent():
    w=child();codec=VersionedOrderCodec();payload=codec.encode('local-child-write',w)
    assert codec.decode('local-child-write',payload)==w
    assert 'parent_command_id' not in payload
    assert not w.execution_authorized
    with pytest.raises(ValueError):codec.encode('application-write',w)

@pytest.mark.parametrize('change',[
    {'origin':'VENUE_GENERATED_CHILD'},{'origin':'APPLICATION_ACTION'},
    {'activation_id':''},{'activation_state_sequence':True},{'activation_state_sequence':0},
    {'activation_hash':'bad'},{'schedule_evidence_hash':'bad'},{'venue_client_id':None},
    {'service_namespace':'BINANCE_SPOT_EXECUTION_ALGO'},{'service_namespace':'BINANCE_USD_M_ORDINARY'},
    {'state':'ACKNOWLEDGED'},{'operation':'CANCEL'},{'valid_until':'2026-10-31T00:00:00Z'}])
def test_invalid_child_source_or_send_identity_is_rejected(change):
    with pytest.raises((ValueError,TypeError)):replace(child(),**change)

@pytest.mark.parametrize('state',['DURABLE_READY','MAY_HAVE_SENT','UNKNOWN'])
def test_child_recovery_retains_original_ids(state):
    w=replace(child(),state=state);codec=VersionedOrderCodec()
    restored=codec.decode('local-child-write',codec.encode('local-child-write',w))
    assert restored.command_id==w.command_id and restored.activation_id==w.activation_id
    assert restored.state==state and not restored.execution_authorized



def test_application_conditional_child_is_distinct_from_venue_generated_order():
    w=child()
    conditional=replace(w,scope=replace(w.scope,product=t.Product.USD_M_PERP),
        service_namespace='BINANCE_USD_M_CONDITIONAL_ALGO')
    codec=VersionedOrderCodec()
    assert codec.decode('local-child-write',codec.encode('local-child-write',conditional))==conditional
    assert conditional.origin=='APPLICATION_GENERATED_CHILD' and not conditional.execution_authorized
    with pytest.raises(ValueError):replace(conditional,origin='VENUE_GENERATED_CHILD')
