import json
from pathlib import Path

from trading_system.contracts.canonical import canonical_hash
from trading_system.server.order_observation import project_program, classify_rejection, capability_context

ROOT = Path(__file__).resolve().parents[2]


def program():
    value = json.loads((ROOT / 'tests/fixtures/orders-v2/program.paper.json').read_text())
    order = value['nodes'][0]['order']
    order['price'].update(kind='FIXED_LIMIT', value='81490')
    order['tif']['kind'] = 'GTC'
    return value


def test_projection_preserves_limit_and_full_parameters_and_is_not_execution():
    original = program()
    view = project_program(original, source='order_programs')
    assert view['canonical_payload_hash'] == canonical_hash(original)
    assert view['payload'] == original
    assert view['codec_status'] == 'DECODED'
    assert view['execution_authorized'] is False
    assert view['nodes'][0]['order']['price']['value'] == '81490'
    assert view['nodes'][0]['order']['tif']['kind'] == 'GTC'
    assert view['execution_state'] == 'NOT_OBSERVED'
    assert view['protection_status'] == 'NOT_OBSERVED'
    assert view['children_complete'] is None


def test_unknown_version_keeps_original_hash_without_interpretation():
    original = program(); original['contract_version'] = '2099-01-01'
    view = project_program(original, source='order_programs')
    assert view['codec_status'] == 'UNSUPPORTED_VERSION'
    assert view['payload'] == original
    assert view['canonical_payload_hash'] == canonical_hash(original)
    assert view['nodes'] == []
    assert view['execution_state'] == 'NOT_OBSERVED'


def test_invalid_known_version_is_visible_and_never_given_defaults():
    original = program(); del original['nodes'][0]['order']['tif']
    view = project_program(original, source='order_programs')
    assert view['codec_status'] == 'INVALID_RECORDED_CONTRACT'
    assert view['nodes'] == []
    assert 'tif' not in view['payload']['nodes'][0]['order']


def test_capability_context_is_exact_archived_snapshot_not_current_permission():
    snapshot = {'snapshot_id':'historical', 'qualification':'PAPER_VERIFIED', 'rule_revision':7}
    view = capability_context(snapshot)
    assert view['archived_snapshot'] == snapshot
    assert view['current_qualification'] == 'UNKNOWN'
    assert view['execution_authorized'] is False
    assert 'CURRENT_QUALIFICATION_NOT_REGISTERED' in view['admission_blockers']
    assert 'HOST_ADMISSION_NOT_REGISTERED' in view['admission_blockers']


def test_rejections_keep_specific_codes_and_separate_stages():
    assert classify_rejection('ADAPTER_NOT_QUALIFIED')['stage'] == 'CAPABILITY'
    assert classify_rejection('RULE_REVISION_CHANGED')['stage'] == 'RULE'
    assert classify_rejection('RISK_REJECTED')['stage'] == 'RISK'
    assert classify_rejection('SIMULATION_DATA_INSUFFICIENT')['stage'] == 'DATA'
    assert classify_rejection('CANCEL_UNKNOWN')['stage'] == 'EXECUTION'
    assert classify_rejection('future-code') == {'code':'future-code', 'stage':'UNKNOWN'}

def test_reservation_current_projection_does_not_reuse_initial_held_or_malformed_balance():
    from trading_system.server.order_observation import project_reservation
    initial=dict(reservation_id='r',environment='PAPER',collateral_group_id='pool',venue_account_id='account',
        owner_strategy_id='owner',intent_id='intent',asset='USDT',amount='100',reflected_amount='0',state='HELD',
        created_at='2026-10-29T00:00:00Z',expires_at='2026-10-29T01:00:00Z',command_refs=[],evidence_watermark='original')
    unknown=project_reservation(initial,None)
    assert unknown['current_status']=='NOT_OBSERVED' and unknown['projection'] is None
    latest=dict(sequence=2,payload=dict(reservation=dict(initial,state='RELEASED'),held='0',reflected='0',release_pending='0',sequence=2))
    observed=project_reservation(initial,latest)
    assert observed['current_status']=='RECORDED_PROJECTION'
    assert observed['projection']['held']=='0' and initial['state']=='HELD'
    latest['payload']['held']='NaN'
    invalid=project_reservation(initial,latest)
    assert invalid['current_status']=='UNKNOWN' and invalid['projection'] is None
    assert invalid['raw_projection']==latest



def test_local_child_archive_is_scoped_bounded_and_never_a_fill():
    from types import SimpleNamespace
    from trading_system.server.order_observation import OrderObservationService
    from trading_system.contracts import types as t
    scope=t.ScopeKey(environment=t.Environment.PAPER,venue_account_id='fixture',product=t.Product.SPOT,instrument_uid='BTCUSDT',venue_leg='BOTH')
    service=OrderObservationService(None,(scope,))
    payload={'opaque':'original-child-source'}
    def execute(sql,args):
        assert 'JOIN ledger.audit_events' in sql and 'LIMIT 501' in sql
        assert args==('allowed-scope','parent-program')
        return SimpleNamespace(fetchall=lambda:[('child-'+str(i),payload,'tx') for i in range(501)])
    conn=SimpleNamespace(execute=execute)
    records,truncated=service._local_children(conn,{'local_child_commitments_v1'},'allowed-scope','parent-program')
    assert len(records)==500 and truncated
    assert records[0]['payload']==payload
    assert records[0]['observation_status']=='UNVERIFIED_COMMITMENT_ARCHIVE'
    assert records[0]['execution_authorized'] is False
    assert 'fills' not in records[0] and 'venue_order_id' not in records[0]
    assert service._local_children(None,set(),'allowed-scope','parent-program')==([],False)
