"""Offline OP-F regressions: stage classification, catalog facts, projections."""
from trading_system.server.errors import ApiError
from trading_system.server.order_observation import (
    capability_catalog, classify_rejection, project_admission, project_command, project_fill,
)


def test_capacity_stage_carries_the_specific_asset():
    result = classify_rejection('INSUFFICIENT_CAPACITY:USDT')
    assert result['stage'] == 'RISK' and result['code'] == 'INSUFFICIENT_CAPACITY:USDT'
    assert result['fields'] == [{'field': 'capacity', 'asset': 'USDT'}]


def test_new_admission_stages_are_separate():
    assert classify_rejection('CAPABILITY_SNAPSHOT_CHANGED')['stage'] == 'CAPABILITY'
    assert classify_rejection('PAPER_QUALIFICATION_REQUIRED')['stage'] == 'CAPABILITY'
    assert classify_rejection('PROGRAM_CURRENT_BOOK_CHANGED')['stage'] == 'DATA'
    assert classify_rejection('PROGRAM_HOST_ORIGINAL_REQUIRED')['stage'] == 'EXECUTION'
    assert classify_rejection('PROGRAM_INTAKE_SOURCE_MISSING')['stage'] == 'DATA'
    assert classify_rejection('future-code') == {'code': 'future-code', 'stage': 'UNKNOWN'}


def test_catalog_keeps_real_execution_support_fact():
    catalog = capability_catalog()
    assert catalog['catalog_status'] == 'REFERENCE_ONLY'
    limit = next(f for f in catalog['features'] if f['id'] == 'SPOT-LIMIT')
    assert limit['observed_current_support']
    assert limit['paper_semantic_tests'] in ('NOT_RUN', 'PASS', 'FAIL')
    assert catalog['current_qualification'] == 'UNKNOWN'
    assert catalog['execution_authorized'] is False


def test_projection_separates_receipt_command_fill_and_duty():
    admission = project_admission(dict(record_id='generic-program-admission:1', transaction_id='tx',
                                       canonical_payload_hash='h' * 64,
                                       payload=dict(intent_id='i', bundle_hash='b', mode='EXTERNAL_REQUESTS',
                                                    command_ids=['c'], activation_id='a')))
    assert admission['execution_authorized'] is False
    command = project_command(dict(record_id='c', sequence=1, payload=dict(
        state='MAY_HAVE_SENT', request_hash='r', commitment_hash='m', activation_id='a')))
    assert command['state'] == 'MAY_HAVE_SENT' and command['execution_authorized'] is False
    fill = project_fill(dict(record_id='f', payload=dict(fill_id='f', quantity='0.1', price='100'),
                             canonical_payload_hash='h' * 64))
    assert fill['provenance'] == 'UNVERIFIED_RECORDED_FILL'
    assert fill['execution_authorized'] is False


def test_api_error_carries_sanitized_details():
    error = ApiError('REPLAN_REQUIRED', 'changed', 409, details={'changed_fields': ['capability']})
    assert error.details == {'changed_fields': ['capability']}
    assert ApiError('X', 'y').details is None
