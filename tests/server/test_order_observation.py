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
