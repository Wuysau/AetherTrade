"""Exact public V3 write envelope; it is never a permit."""
from dataclasses import replace
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.contracts.application_write_v3 import ApplicationWriteV3

def write():
    scope=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='fixture',product=t.Product.SPOT,instrument_uid='BTCUSDT',venue_leg='BOTH')
    return ApplicationWriteV3(contract_version='2026-10-29',record_kind='PROGRAM_APPLICATION_WRITE',command_id='original',intent_id='intent',program_id='program',scope=scope,
        origin='APPLICATION_ACTION',operation='CREATE_REQUEST',service_namespace='BINANCE_SPOT_ORDINARY',parent_command_id=None,venue_client_id='a'*32,
        owner_strategy_id='owner',ownership_epoch=1,execution_epoch=1,policy_revision=1,request_index=0,
        canonical_program_hash='1'*64,compilation_hash='2'*64,allocation_hash='3'*64,intent_hash='4'*64,basis_hash='5'*64,decision_hash='6'*64,
        archive_hash='7'*64,request_hash='8'*64,binding_hash='9'*64,state='DURABLE_READY',durable_record_ref='pending:original',
        created_at='2026-10-29T06:00:00Z',valid_until='2026-10-29T06:01:00Z')

def test_v3_write_roundtrip_keeps_typed_request_and_financial_hashes():
    codec=VersionedOrderCodec();record=write();payload=codec.encode('application-write',record)
    assert codec.decode('application-write',payload)==record
    assert not record.execution_authorized
    with pytest.raises(Exception):codec.decode('application-write',dict(payload,request_hash='not-a-hash'))

def test_v3_native_child_and_raw_terminal_claim_cannot_be_new_application_write():
    with pytest.raises(ValueError):replace(write(),origin='VENUE_GENERATED_CHILD')
    with pytest.raises(ValueError):replace(write(),state='VENUE_CONFIRMED')
