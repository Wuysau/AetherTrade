"""Offline application/process tests. T25 and Q02/Q03/Q04 remain NOT_RUN.

Tests are authored for the final batch runner; no fixed PASS fixture or native
qualification substitute is registered here.
"""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import pytest

from support_ip_11 import (
    BridgeCase, NOW, ROOT, bridge_case, build, compatibility, decode_fixture, event_bytes, query,
)
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec
from trading_system.engine.nautilus.bridge import BridgeDenied, NautilusEngine
from trading_system.engine.nautilus.host import NautilusHost
from trading_system.engine.nautilus.id_codec import IdentityConflict, UnknownIdentity, VenueIdCodec
from trading_system.engine.nautilus.native_events import NativeEventBridge


def test_no_permit_no_wire(bridge_case):
    result = bridge_case.try_submit(permit=None)
    assert result.rejected
    assert result.wire_requests == ()


def test_fixture_command_is_valid_wire_data(bridge_case):
    body = ContractCodec().encode(bridge_case.command)
    assert type(body['environment']) is str
    assert type(body['product']) is str
    assert canonical_hash(body['canonical_payload']) == bridge_case.permit.payload_hash
    assert ContractCodec().decode('execution-command', body) == bridge_case.command


@pytest.mark.parametrize('operation,method', [('SUBMIT', 'submit'), ('CLOSE', 'submit'),
    ('SET_PROTECTION', 'submit'), ('CANCEL', 'cancel'), ('AMEND', 'amend')])
def test_each_operation_requires_permit(bridge_case, operation, method):
    command, _ = bridge_case.make_command(operation)
    with pytest.raises(BridgeDenied, match='DURABLE_PERMIT_REQUIRED'):
        getattr(bridge_case.engine, method)(command, None)
    assert bridge_case.engine.offline_writes == ()


def test_old_execution_epoch_no_wire(bridge_case):
    case = bridge_case
    command = replace(case.command, execution_epoch=6)
    permit = replace(case.permit, execution_epoch=6)
    case.trust(command, permit)  # Saved old command is real to the synthetic reader.
    with pytest.raises(BridgeDenied, match='STALE_EPOCH'):
        case.engine.submit(command, permit)
    assert case.engine.offline_writes == ()


@pytest.mark.parametrize('field,value', [
    ('command_id', 'other'), ('durable_record_ref', 'uncommitted'), ('payload_hash', '0' * 64),
    ('ownership_epoch', 2), ('execution_epoch', 8), ('policy_revision', 2),
    ('expires_at', '2027-01-01T00:00:00Z'), ('deadline_monotonic_ns', 99999),
])
def test_caller_cannot_forge_permit(bridge_case, field, value):
    with pytest.raises(BridgeDenied):
        bridge_case.engine.submit(bridge_case.command, replace(bridge_case.permit, **{field: value}))
    assert bridge_case.engine.offline_writes == ()


@pytest.mark.parametrize('field,value', [
    ('operation', 'CLOSE'), ('target_order_id', 'manual-order'), ('attempt_id', 'new-attempt'),
    ('logical_client_order_id', 'another'), ('instrument_uid', 'another'),
    ('parent_command_id', 'another'), ('state', 'UNKNOWN'),
])
def test_whole_committed_command_matches(bridge_case, field, value):
    with pytest.raises(BridgeDenied):
        bridge_case.engine.submit(replace(bridge_case.command, **{field: value}), bridge_case.permit)
    assert bridge_case.engine.offline_writes == ()


@pytest.mark.parametrize('kind', ['permit_utc', 'permit_monotonic', 'lease_utc', 'lease_monotonic',
                                 'unhealthy_clock', 'clock_bool', 'ownership', 'policy', 'future'])
def test_current_authority_and_deadlines(bridge_case, kind):
    case = bridge_case
    command, permit = case.command, case.permit
    authority = case.authorities[command.command_id]
    if kind.startswith('permit_'):
        permit = replace(permit, **({'expires_at': NOW} if kind.endswith('utc') else
                                     {'deadline_monotonic_ns': 100}))
        authority = replace(authority, permit=permit)
    elif kind.startswith('lease_'):
        lease = replace(case.lease, **({'expires_at': NOW} if kind.endswith('utc') else
                                       {'deadline_monotonic_ns': 100}))
        authority = replace(authority, lease=lease)
    elif kind == 'unhealthy_clock':
        case.reading = replace(case.reading, clock_health='UNKNOWN')
    elif kind == 'clock_bool':
        case.reading = replace(case.reading, monotonic_ns=True)
    elif kind in ('ownership', 'policy'):
        authority = replace(authority, **{kind + ('_epoch' if kind == 'ownership' else '_revision'): 2})
    else:
        command = replace(command, created_at='2027-01-01T00:00:00Z')
        authority = replace(authority, command=command)
    case.authorities[command.command_id] = authority
    with pytest.raises(BridgeDenied):
        case.engine.submit(command, permit)
    assert case.engine.offline_writes == ()


def test_expiry_rechecked_at_observation(bridge_case):
    def expire(call):
        if call == 2:
            bridge_case.reading = replace(bridge_case.reading, monotonic_ns=1000)
    bridge_case.on_clock = expire
    with pytest.raises(BridgeDenied, match='EXPIRED'):
        bridge_case.engine.submit(bridge_case.command, bridge_case.permit)
    assert bridge_case.engine.offline_writes == ()


def test_authority_unavailable_no_wire(bridge_case):
    bridge_case.revoked = True
    with pytest.raises(BridgeDenied):
        bridge_case.engine.submit(bridge_case.command, bridge_case.permit)
    assert bridge_case.engine.offline_writes == ()


def test_untyped_owner_evidence_no_wire(bridge_case):
    command = bridge_case.command
    bridge_case.authorities[command.command_id] = replace(
        bridge_case.authorities[command.command_id], lease=None)
    with pytest.raises(BridgeDenied, match='CURRENT_OWNER_EVIDENCE_MISSING'):
        bridge_case.engine.submit(command, bridge_case.permit)
    assert bridge_case.engine.offline_writes == ()


def test_hash_not_just_permit_equality(bridge_case):
    case = bridge_case
    command = replace(case.command, canonical_payload=replace(case.command.canonical_payload, quantity='2'))
    case.trust(command, case.permit)
    with pytest.raises(BridgeDenied, match='HASH_MISMATCH'):
        case.engine.submit(command, case.permit)
    assert case.engine.offline_writes == ()


@pytest.mark.parametrize('operation,method', [('SUBMIT', 'submit'), ('CLOSE', 'submit'),
    ('SET_PROTECTION', 'submit'), ('CANCEL', 'cancel'), ('AMEND', 'amend')])
def test_operations_share_boundary_and_never_report_fill(bridge_case, operation, method):
    case = bridge_case
    command, permit = case.make_command(operation, suffix='-operation')
    receipt = getattr(case.engine, method)(command, permit)
    assert receipt.status == t.CommandState.UNKNOWN
    assert receipt.existing_result_ref is None
    assert len(case.engine.offline_writes) == 1
    assert case.engine.offline_writes[0].permit == permit
    assert case.engine.offline_writes[0].binding == case.binding
    assert not case.guard_active
    with pytest.raises(BridgeDenied, match='ALREADY_OBSERVED'):
        getattr(case.engine, method)(command, permit)
    assert len(case.engine.offline_writes) == 1


def test_cancel_and_amend_need_independent_command_and_owned_target(bridge_case):
    case = bridge_case
    with pytest.raises(BridgeDenied, match='WRONG_ENGINE_METHOD'):
        case.engine.cancel(case.command, case.permit)
    command, permit = case.make_command('CANCEL')
    with pytest.raises(BridgeDenied):
        case.engine.cancel(command, case.permit)
    command = replace(command, target_order_id='manual-order')
    case.trust(command, permit)
    with pytest.raises(UnknownIdentity):
        case.engine.cancel(command, permit)
    assert case.engine.offline_writes == ()


@pytest.mark.parametrize('operation,method', [('CANCEL', 'cancel'), ('AMEND', 'amend')])
def test_missing_target_is_a_local_rejection(bridge_case, operation, method):
    command, permit = bridge_case.make_command(operation)
    command = replace(command, target_order_id=None)
    bridge_case.trust(command, permit)
    with pytest.raises(BridgeDenied, match='INVALID_COMMAND_CONTRACT'):
        getattr(bridge_case.engine, method)(command, permit)
    assert bridge_case.engine.offline_writes == ()


def test_reusing_client_id_with_new_command_is_rejected(bridge_case):
    case = bridge_case
    case.engine.submit(case.command, case.permit)
    command = replace(case.command, command_id='second-command', attempt_id='second-attempt',
                      durable_record_ref='second-record')
    permit = replace(case.permit, command_id=command.command_id,
                     durable_record_ref=command.durable_record_ref)
    case.trust(command, permit)
    with pytest.raises(BridgeDenied, match='CLIENT_ID_ALREADY_OBSERVED'):
        case.engine.submit(command, permit)
    assert len(case.engine.offline_writes) == 1


def test_concurrent_delivery_is_observed_once(bridge_case):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: bridge_case.try_submit(bridge_case.permit), range(2)))
    assert sorted(result.rejected for result in results) == [False, True]
    assert len(bridge_case.engine.offline_writes) == 1


def test_position_leg_must_match_scope_even_with_matching_hash(bridge_case):
    case = bridge_case
    command = replace(case.command, canonical_payload=replace(
        case.command.canonical_payload, position_side='LONG'))
    digest = canonical_hash(ContractCodec().encode(command)['canonical_payload'])
    command, permit = replace(command, payload_hash=digest), replace(case.permit, payload_hash=digest)
    case.trust(command, permit)
    with pytest.raises(BridgeDenied, match='POSITION_LEG_MISMATCH'):
        case.engine.submit(command, permit)
    assert case.engine.offline_writes == ()


@pytest.mark.parametrize('field,value', [('logical_client_order_id', 'unregistered-logical'),
                                       ('venue_order_id', 'unbound-order')])
def test_registered_identity_must_match_committed_command(bridge_case, field, value):
    command = replace(bridge_case.command, **{field: value})
    bridge_case.trust(command, bridge_case.permit)
    with pytest.raises(BridgeDenied, match='REGISTERED_IDENTITY_MISMATCH'):
        bridge_case.engine.submit(command, bridge_case.permit)
    assert bridge_case.engine.offline_writes == ()


def test_no_child_bypass(bridge_case):
    case = bridge_case
    for role in ('ALGORITHM', 'CHILD'):
        command, permit = case.make_command(suffix=role, role=role, parent=case.command.command_id)
        with pytest.raises(BridgeDenied, match='CHILD_DISABLED'):
            case.engine.submit(command, permit)
    inventory = json.loads((ROOT / 'qualification/nautilus/write-path-inventory.json').read_text('utf-8'))
    for path in inventory['paths']:
        with pytest.raises(BridgeDenied, match='NOT_QUALIFIED'):
            case.engine.enable_native_path(path['path'])
    with pytest.raises(BridgeDenied):
        case.engine.enable_native_path('UNLISTED_EMULATOR_WRITE')
    assert case.engine.offline_writes == ()


def test_native_mapping_cannot_enable_real_client(bridge_case):
    claimed = replace(compatibility(), uncovered_paths=(), send_path_inventory=('SUBMIT',),
        entries=(t.CompatibilityEntry(port='SUBMIT', native_api='untrusted.module.function',
            evidence=t.EvidenceRef(id='unverified', hash='0' * 64, environment=t.Environment.LIVE,
                                   source='caller', visible_at=NOW)),))
    engine = NautilusEngine(claimed)
    with pytest.raises(BridgeDenied, match='NOT_QUALIFIED'):
        engine.submit(bridge_case.command, bridge_case.permit)
    assert not engine.live_enabled
    assert engine.offline_writes == ()


def test_cash_and_perp_product_binding(bridge_case, tmp_path):
    spot, perp = bridge_case, BridgeCase(tmp_path, t.Product.USD_M_PERP)
    try:
        for case in (spot, perp):
            case.engine.submit(case.command, case.permit)
            observation = case.engine.offline_writes[0]
            assert observation.binding.scope == case.scope
            assert observation.binding.client_id.endswith(str(case.scope.product))
            assert observation.binding.native_instrument_id.endswith(str(case.scope.product))
        # Authoritative command/permit from the wrong product is still refused.
        spot.trust(perp.command, perp.permit)
        with pytest.raises(BridgeDenied):
            spot.engine.submit(perp.command, perp.permit)
        assert len(spot.engine.offline_writes) == 1
        assert spot.command.venue_client_order_id != perp.command.venue_client_order_id
    finally:
        perp.codec.close()


@pytest.mark.parametrize('mutation', ['empty', 'duplicate', 'client', 'instrument', 'product'])
def test_no_default_or_ambiguous_route(bridge_case, mutation):
    case = bridge_case
    binding = case.binding
    bindings = (binding,)
    if mutation == 'empty':
        bindings = ()
    elif mutation == 'duplicate':
        bindings = (binding, binding)
    elif mutation == 'client':
        bindings = (replace(binding, client_id=''),)
    elif mutation == 'instrument':
        bindings = (replace(binding, native_instrument_id=''),)
    else:
        bindings = (replace(binding, scope=replace(case.scope, product=t.Product.USD_M_PERP)),)
    with pytest.raises(BridgeDenied):
        NautilusEngine(compatibility(), offline=replace(case.configuration, bindings=bindings))


@pytest.mark.parametrize('environment', [t.Environment.LIVE, t.Environment.TESTNET])
def test_offline_configuration_cannot_enable_live(bridge_case, environment):
    with pytest.raises(BridgeDenied):
        NautilusEngine(compatibility(), offline=replace(bridge_case.configuration,
            scope=replace(bridge_case.scope, environment=environment)))


def test_query_empty_is_not_zero_position(bridge_case):
    report = bridge_case.engine.query(query(bridge_case.scope))
    assert report.orders == report.positions == report.balances == report.fills == ()
    assert not report.coverage.query_succeeded
    assert not report.coverage.pagination_complete
    assert not report.coverage.supported
    assert report.coverage.missing
    with pytest.raises(BridgeDenied):
        bridge_case.engine.query(query(replace(bridge_case.scope, venue_account_id='other')))


def test_id_roundtrip(bridge_case, tmp_path):
    codec = bridge_case.codec
    ids = set()
    for logical in ('same-original', '原始/订单:01', 'x' * 4096):
        for role in ('ORDER', 'ALGORITHM', 'CHILD', 'PROTECTION', 'CLOSE', 'CANCEL', 'AMEND'):
            client = codec.encode(logical, bridge_case.scope.product, role)
            identity = codec.decode(client)
            assert identity.logical_client_order_id == logical
            assert codec.decode_native(identity.native_client_order_id) == identity
            assert identity.native_client_order_id not in ids
            assert client not in ids
            ids.update((client, identity.native_client_order_id))
            assert codec.encode(logical, bridge_case.scope.product, role) == client
            identity = codec.bind_venue_order(client, 'observed-' + client)
            assert codec.decode_order(identity.venue_order_id) == identity
    reopened = VenueIdCodec(bridge_case.scope, database=tmp_path / 'SPOT.sqlite')
    try:
        client = reopened.encode('原始/订单:01', bridge_case.scope.product, 'ALGORITHM')
        assert reopened.decode(client) == codec.decode(client)
        assert reopened.role(client) == 'ALGORITHM'
    finally:
        reopened.close()


@pytest.mark.parametrize('field,value', [('venue_account_id', 'other'), ('instrument_uid', 'other'),
    ('product', t.Product.USD_M_PERP), ('environment', t.Environment.RESEARCH), ('venue_leg', 'LONG')])
def test_identity_is_scoped(bridge_case, tmp_path, field, value):
    scope = replace(bridge_case.scope, **{field: value})
    codec = VenueIdCodec(scope, database=tmp_path / 'SPOT.sqlite')
    try:
        with pytest.raises(UnknownIdentity):
            codec.decode(bridge_case.command.venue_client_order_id)
        assert codec.encode(bridge_case.command.logical_client_order_id, scope.product, 'ORDER') != (
            bridge_case.command.venue_client_order_id)
    finally:
        codec.close()


def test_id_collision_does_not_overwrite(bridge_case, monkeypatch):
    codec = bridge_case.codec
    monkeypatch.setattr(codec, '_token', lambda logical, role: 'forced-collision')
    first = codec.encode('first', bridge_case.scope.product, 'ORDER')
    with pytest.raises(IdentityConflict):
        codec.encode('second', bridge_case.scope.product, 'ALGORITHM')
    assert codec.decode(first).logical_client_order_id == 'first'


def test_order_id_collision_and_rebinding(bridge_case):
    codec, product = bridge_case.codec, bridge_case.scope.product
    first, second = [codec.encode(value, product, 'ORDER') for value in ('first', 'second')]
    identity = codec.bind_venue_order(first, 'observed-id')
    assert codec.bind_venue_order(first, 'observed-id') == identity
    with pytest.raises(IdentityConflict):
        codec.bind_venue_order(second, 'observed-id')
    with pytest.raises(IdentityConflict):
        codec.bind_venue_order(first, 'replacement-id')
    assert codec.decode(first) == identity
    assert codec.decode(second).venue_order_id is None


def test_manual_id_and_namespaces_are_not_guessed(bridge_case):
    codec = bridge_case.codec
    identity = codec.decode(bridge_case.command.venue_client_order_id)
    for value in ('manual-order', 'V1-looking-like-owned', identity.native_client_order_id):
        with pytest.raises(UnknownIdentity):
            codec.decode(value)
    with pytest.raises(ValueError):
        codec.encode('logical', t.Product.USD_M_PERP, 'ORDER')
    with pytest.raises(ValueError):
        codec.encode('logical', t.Product.SPOT, 'UNREVIEWED_ROLE')


def test_event_raw_evidence_and_id_correlation(bridge_case, tmp_path):
    codec = bridge_case.codec
    identity = codec.bind_venue_order(bridge_case.command.venue_client_order_id, 'observed-order')
    payload = event_bytes(identity)
    bridge = NativeEventBridge(codec, tmp_path / 'raw', decoder=decode_fixture)
    result = bridge.ingest(payload, source='synthetic-native', received_at=NOW, processed_at=NOW)
    assert result.status == 'OBSERVED'
    assert result.event.order_refs == (identity,)
    assert result.event.account_id == bridge_case.scope.venue_account_id
    assert result.event.payload_hash == sha256(payload).hexdigest()
    assert Path(result.raw.raw_payload_ref.id).read_bytes() == payload
    assert bridge.ingest(payload, source='synthetic-native', received_at=NOW,
                         processed_at=NOW).event.event_id == result.event.event_id


@pytest.mark.parametrize('payload', [b'broken-json', b'null', b'{}', b'\xff'])
def test_parse_error_preserves_raw_and_unknown(bridge_case, tmp_path, payload):
    result = NativeEventBridge(bridge_case.codec, tmp_path, decoder=decode_fixture).ingest(
        payload, source='synthetic-native', received_at=NOW, processed_at=NOW)
    assert result.status == 'UNKNOWN'
    assert result.event is None
    assert Path(result.raw.raw_payload_ref.id).read_bytes() == payload


def test_missing_native_decoder_stays_unknown(bridge_case, tmp_path):
    result = NativeEventBridge(bridge_case.codec, tmp_path).ingest(
        b'raw', source='synthetic-native', received_at=NOW, processed_at=NOW)
    assert result.status == 'UNKNOWN'
    assert result.reason == 'NATIVE_DECODER_NOT_QUALIFIED'


def test_manual_event_is_unattributed(bridge_case, tmp_path):
    identity = bridge_case.codec.decode(bridge_case.command.venue_client_order_id)
    payload = event_bytes(identity, native_client_order_id='manual-native',
                          venue_client_order_id='manual-client', venue_order_id='manual-order')
    result = NativeEventBridge(bridge_case.codec, tmp_path, decoder=decode_fixture).ingest(
        payload, source='synthetic-native', received_at=NOW, processed_at=NOW)
    assert result.status == 'UNATTRIBUTED'
    assert result.event is None


@pytest.mark.parametrize('conflict', ['known-other', 'unknown-order'])
def test_conflicting_event_ids_stay_unknown(bridge_case, tmp_path, conflict):
    codec = bridge_case.codec
    identity = codec.decode(bridge_case.command.venue_client_order_id)
    if conflict == 'known-other':
        other = codec.encode('other', bridge_case.scope.product, 'ORDER')
        payload = event_bytes(identity, venue_client_order_id=other)
    else:
        payload = event_bytes(identity, venue_order_id='unbound-order')
    result = NativeEventBridge(codec, tmp_path, decoder=decode_fixture).ingest(
        payload, source='synthetic-native', received_at=NOW, processed_at=NOW)
    assert result.status == 'UNKNOWN'
    assert result.event is None
    assert codec.decode(identity.venue_client_order_id).venue_order_id is None


def test_archive_failure_does_not_invoke_decoder(bridge_case, tmp_path):
    archive = tmp_path / 'not-a-directory'
    archive.write_bytes(b'occupied')
    def forbidden(payload):
        pytest.fail('decoder must not run after archive failure')
    with pytest.raises(OSError):
        NativeEventBridge(bridge_case.codec, archive, decoder=forbidden).ingest(
            b'raw', source='synthetic-native', received_at=NOW, processed_at=NOW)


def test_archive_corruption_is_not_overwritten(bridge_case, tmp_path):
    payload = b'raw'
    target = tmp_path / (sha256(payload).hexdigest() + '.raw')
    target.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='hash/content mismatch'):
        NativeEventBridge(bridge_case.codec, tmp_path, decoder=decode_fixture).ingest(
            payload, source='synthetic-native', received_at=NOW, processed_at=NOW)
    assert target.read_bytes() == b'corrupt'


def test_existing_archive_must_be_fsynced_before_decode(bridge_case, tmp_path, monkeypatch):
    payload = b'raw'
    target = tmp_path / (sha256(payload).hexdigest() + '.raw')
    target.write_bytes(payload)
    def fail_sync(fd):
        raise OSError('synthetic fsync failure')
    def forbidden(payload):
        pytest.fail('decoder must not run after fsync failure')
    monkeypatch.setattr(os, 'fsync', fail_sync)
    with pytest.raises(OSError, match='fsync failure'):
        NativeEventBridge(bridge_case.codec, tmp_path, decoder=forbidden).ingest(
            payload, source='synthetic-native', received_at=NOW, processed_at=NOW)
    assert target.read_bytes() == payload


@pytest.mark.parametrize('processed_at', ['not-a-time', '2026-10-22T00:00:00Z'])
def test_event_invalid_local_time_preserves_raw(bridge_case, tmp_path, processed_at):
    identity = bridge_case.codec.decode(bridge_case.command.venue_client_order_id)
    payload = event_bytes(identity)
    result = NativeEventBridge(bridge_case.codec, tmp_path, decoder=decode_fixture).ingest(
        payload, source='synthetic-native', received_at=NOW, processed_at=processed_at)
    assert result.status == 'UNKNOWN'
    assert result.event is None
    assert Path(result.raw.raw_payload_ref.id).read_bytes() == payload


def test_host_native_start_is_disabled(bridge_case, build):
    host = NautilusHost()
    result = host.run(bridge_case.shard, build)
    assert result.exit_code != 0
    assert host.last_reason == 'NATIVE_BINDING_NOT_QUALIFIED'
    assert result.evidence == ()
    assert not host.live_enabled and not host.recovery_ready


def test_host_offline_process_is_independent(bridge_case, build, tmp_path):
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path / 'locks')
    result = host.run(bridge_case.shard, build)
    assert result.exit_code == 0
    assert host.observation.child_pid != host.observation.supervisor_pid
    assert host.observation.build_digest == build.artifact_digest
    assert host.last_reason == 'OFFLINE_PROBE_ONLY_NATIVE_NOT_RUN'
    assert not host.recovery_ready and not host.live_enabled
    assert result.evidence == ()  # Process observation does not manufacture qualification.


def test_host_account_product_claim_ignores_epoch_and_node(bridge_case, build, tmp_path):
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    other = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    with host._claim(bridge_case.shard, 'first-owner'):
        result = other.run(replace(bridge_case.shard, node_id='second', execution_epoch=8), build)
        assert result.exit_code != 0
        assert other.last_reason == 'SHARD_ALREADY_CLAIMED_OR_STALE_MARKER'
    assert not list(tmp_path.glob('*.lock'))


def test_stale_marker_is_not_removed(bridge_case, build, tmp_path):
    shard = bridge_case.shard
    key = canonical_hash({'environment': str(shard.environment),
                          'account': shard.venue_account_id, 'product': str(shard.product)})
    marker = tmp_path / (key + '.lock')
    marker.write_text('unknown-old-owner', encoding='utf-8')
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    assert host.run(shard, build).exit_code != 0
    assert marker.read_text('utf-8') == 'unknown-old-owner'


@pytest.mark.parametrize('environment', [t.Environment.LIVE, t.Environment.TESTNET])
def test_host_refuses_non_offline_shards(bridge_case, build, tmp_path, environment):
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    result = host.run(replace(bridge_case.shard, environment=environment), build)
    assert result.exit_code != 0
    assert host.observation is None


def test_host_incomplete_build_does_not_start(bridge_case, build, tmp_path):
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    result = host.run(bridge_case.shard, replace(build, artifact_digest=''),)
    assert result.exit_code != 0
    assert host.last_reason == 'BUILD_METADATA_INCOMPLETE'


@pytest.mark.parametrize('field,value', [('candidate', None), ('qualification_ref', None),
                                       ('artifact_digest', 123)])
def test_host_malformed_build_does_not_start(bridge_case, build, tmp_path, monkeypatch, field, value):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid metadata must not start a process')
    monkeypatch.setattr(subprocess, 'run', forbidden)
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    result = host.run(bridge_case.shard, replace(build, **{field: value}))
    assert result.exit_code == 2
    assert host.last_reason == 'BUILD_METADATA_INCOMPLETE'
    assert host.observation is None


@pytest.mark.parametrize('output', ['null', '[]', 'invalid-json', '{}',
    '{"mode":"OFFLINE_PROCESS_PROBE","pid":true}',
    '{"mode":"OFFLINE_PROCESS_PROBE","pid":-1}'])
def test_host_malformed_child_response_is_failure(bridge_case, build, tmp_path, monkeypatch, output):
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, output, ''))
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    result = host.run(bridge_case.shard, build)
    assert result.exit_code == 3
    assert host.last_reason == 'OFFLINE_PROCESS_PROBE_FAILED'
    assert host.observation is None
    assert result.evidence == ()
    assert not list(tmp_path.glob('*.lock'))


def test_host_cleanup_failure_does_not_publish_observation(bridge_case, build, tmp_path, monkeypatch):
    def fail_unlink(path, *args, **kwargs):
        raise OSError('synthetic cleanup failure')
    monkeypatch.setattr(Path, 'unlink', fail_unlink)
    monkeypatch.setattr(subprocess, 'run', lambda *args, **kwargs: subprocess.CompletedProcess(
        args[0], 0, json.dumps({'mode': 'OFFLINE_PROCESS_PROBE', 'pid': os.getpid() + 1}), ''))
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    result = host.run(bridge_case.shard, build)
    assert result.exit_code == 3
    assert host.observation is None
    assert len(list(tmp_path.glob('*.lock'))) == 1
    assert not host.recovery_ready


def test_host_changed_claim_is_preserved_and_reported(bridge_case, build, tmp_path, monkeypatch):
    def replace_claim(*args, **kwargs):
        marker, = tmp_path.glob('*.lock')
        marker.write_text('different-owner', encoding='utf-8')
        return subprocess.CompletedProcess(args[0], 0, json.dumps(
            {'mode': 'OFFLINE_PROCESS_PROBE', 'pid': os.getpid() + 1}), '')
    monkeypatch.setattr(subprocess, 'run', replace_claim)
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path)
    assert host.run(bridge_case.shard, build).exit_code == 3
    assert host.observation is None
    marker, = tmp_path.glob('*.lock')
    assert marker.read_text('utf-8') == 'different-owner'


def test_host_offline_probe_requires_explicit_lock_directory(bridge_case, build):
    host = NautilusHost(offline_probe=True)
    result = host.run(bridge_case.shard, build)
    assert result.exit_code == 3
    assert host.observation is None


def test_host_timeout_preserves_unresolved(bridge_case, build, tmp_path, monkeypatch):
    pending = query(bridge_case.scope)
    host = NautilusHost(offline_probe=True, lock_directory=tmp_path, unresolved=(pending,))
    def timeout(*args, **kwargs):
        assert kwargs['env'] == {}
        assert '-I' in args[0] and '-S' in args[0]
        raise subprocess.TimeoutExpired(args[0], 1)
    monkeypatch.setattr(subprocess, 'run', timeout)
    result = host.run(bridge_case.shard, build)
    assert result.exit_code == 3
    assert result.unresolved == (pending,)
    assert result.evidence == ()
    assert host.observation is None
