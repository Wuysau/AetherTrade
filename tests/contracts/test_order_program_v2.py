import copy,json
from pathlib import Path
import pytest
from trading_system.contracts.order_program import OrderProgramCodec


def example():
    return json.loads((Path(__file__).parents[1]/'fixtures/orders-v2/program.paper.json').read_text(encoding='utf-8'))

def test_limit_and_independent_times_roundtrip_losslessly():
    value=example();codec=OrderProgramCodec();record=codec.decode(value)
    assert codec.encode(record)==value
    assert record.nodes[0].order.price.value=='81490.00'
    assert record.nodes[0].order.trigger.kind=='NONE'
    assert record.submit_valid_until != record.nodes[0].order.tif.expires_at
    assert codec.hash(record)==codec.hash(codec.decode(codec.encode(record)))

@pytest.mark.parametrize('change', ['unknown','float','wrong_variant','cycle','venue_child_write','unknown_version'])
def test_invalid_programs_fail_before_any_execution(change):
    value=example()
    if change=='unknown':value['api_key']='not-allowed'
    if change=='float':value['nodes'][0]['order']['quantity']['value']=4000.0
    if change=='wrong_variant':value['nodes'][0]['order']['price']['kind']='MARKET'
    if change=='cycle':value['links']=[{'source':'entry','target':'entry','condition':'FULL_FILL'}]
    if change=='venue_child_write':value['nodes'][0]['child_origin']='VENUE_GENERATED_CHILD'
    if change=='unknown_version':value['contract_version']='latest'
    with pytest.raises((ValueError,TypeError,__import__('jsonschema').ValidationError)):OrderProgramCodec().decode(value)


def test_original_schema_bytes_unchanged():
    import hashlib
    audit=json.loads(Path('docs/evidence/binance-orders/op-a/checkout-audit.json').read_text(encoding='utf-8'))
    for kind,entry in audit['schemas'].items():
        assert hashlib.sha256(Path(f'contracts/{kind}.schema.json').read_bytes()).hexdigest()==entry['public_sha256']

@pytest.mark.parametrize('template',['OCO','OTO','OTOCO','OPO','OPOCO'])
def test_list_templates_cannot_be_single_unrelated_order(template):
    value=example();value['template']=template
    with pytest.raises(ValueError):OrderProgramCodec().decode(value)


def test_proposal_retains_user_limit_and_stop_without_reinterpreting_old_wire():
    from trading_system.contracts.order_codec import VersionedOrderCodec
    p=dict(contract_version='2026-10-28',proposal_id='new-proposal',instrument_ref='paper:BTCUSDT',action='SET_TARGET',target_side='LONG',target_sizing='QUOTE_NOTIONAL',target_value='4000',requested_leverage=1,program=example(),protection_policy='NATIVE_REQUIRED',exit_plan=dict(stop_price='81330.00',stop_trigger='LAST',review_due_at='2026-10-28T11:00:00Z',maximum_holding_seconds=3600,thesis_invalidation='Fixture stop'),valid_until='2026-10-28T10:00:30Z',evidence_refs=['fixture'],summary='4000 LIMIT 81490 STOP 81330 fixture')
    codec=VersionedOrderCodec();r=codec.decode('order-proposal',p)
    assert codec.encode('order-proposal',r)==p
    assert r.program.nodes[0].order.price.value=='81490.00' and r.exit_plan.stop_price=='81330.00'


def test_explicit_version_dispatch_preserves_legacy_unknown_hash():
    from trading_system.contracts.order_codec import VersionedOrderCodec
    from trading_system.contracts.wire import ContractCodec
    original=json.loads(Path('tests/fixtures/contracts/examples/execution-command.paper.json').read_text())
    original.update(state='UNKNOWN')
    old=ContractCodec();new=VersionedOrderCodec()
    record=new.decode('execution-command',original)
    assert new.encode('execution-command',record)==original
    assert new.hash('execution-command',record)==old.hash(record)
    bad=dict(original,contract_version='2026-10-28')
    with pytest.raises(ValueError):new.decode('execution-command',bad)
