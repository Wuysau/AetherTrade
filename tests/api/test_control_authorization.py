"""IP-27 unit/API probes. These do not register or pass T/Q acceptance cases."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json

import pytest

from api.support_ip_27 import ApiCase, SQLiteUow
from trading_system.contracts import types as t
from trading_system.control.app import ControlApp
from trading_system.control.commands import DurableInbox


@pytest.fixture
def api_case(tmp_path):
    return ApiCase(tmp_path / 'control.sqlite')


def test_idempotent_control_command(api_case):
    case = api_case
    first = case.post()
    assert first[0] == 202
    assert case.post() == first
    conflict = case.post(case.request('pause_new'))
    assert conflict[0] == 409
    assert conflict[1]['reason_code'] == 'IDEMPOTENCY_CONFLICT'
    assert case.uow.count() == 2  # one key record, one ID record
    assert case.get(first[1]['command_id'])[1]['request']['action'] == 'exit_owned'


def test_policy_cas_and_privileges(api_case):
    case = api_case
    draft = case.draft()
    path = '/api/risk-policies/paper-policy-demo/revisions'
    case.review('reviseRiskPolicy', {'policy_id': 'paper-policy-demo', 'draft': draft})
    case.grant = replace(case.grant, role='MODEL')
    assert case.post(draft, path=path)[0] == 403
    assert case.grant.actor.policy_revision == 1
    assert case.uow.count() == 0
    case.grant = replace(case.grant, role='USER')
    assert case.post(draft, path=path, revision='0')[0] == 409
    case.grant = replace(case.grant, actor=replace(case.actor, policy_revision=2))
    assert case.post(draft, path=path)[0] == 409
    assert case.uow.count() == 0


def test_ack_is_queued_not_flat(api_case):
    status, receipt = api_case.post()
    assert status == 202 and receipt['status'] == 'QUEUED'
    status, projection = api_case.get(receipt['command_id'])
    assert status == 200
    assert projection['receipt'] == receipt
    assert projection['incomplete_reasons'] == ['EXECUTION_OWNER_UNCONFIRMED']
    assert 'FLAT' not in json.dumps(projection)


@pytest.mark.parametrize('remove_threshold', [False, True])
def test_live_requires_user_review(api_case, remove_threshold):
    draft = api_case.draft()
    draft['policy']['environment'] = 'LIVE'
    draft['policy']['live_enabled'] = True
    if remove_threshold:
        del draft['policy']['daily_loss_fraction']
    api_case.review('reviseRiskPolicy', {'policy_id': 'paper-policy-demo', 'draft': draft})
    assert api_case.post(draft, path='/api/risk-policies/paper-policy-demo/revisions')[0] in (403, 422)
    assert api_case.uow.count() == 0


def test_policy_review_binds_exact_revision_and_never_applies(api_case):
    draft = api_case.draft()
    path = '/api/risk-policies/paper-policy-demo/revisions'
    assert api_case.post(draft, path=path)[0] == 403
    api_case.review('reviseRiskPolicy', {'policy_id': 'paper-policy-demo', 'draft': draft})
    status, receipt = api_case.post(draft, path=path)
    assert status == 202 and receipt['status'] == 'QUEUED'
    assert api_case.grant.actor.policy_revision == 1  # no activation or permit issuance
    assert api_case.get(receipt['command_id'])[0] == 503  # frozen projection limitation
    draft['policy']['maximum_capital'] = '2000'
    assert api_case.post(draft, path=path, key='relaxation')[0] == 403
    assert api_case.uow.count() == 2


def test_tightening_is_not_effective_without_owner_permit_invalidation(api_case):
    draft = api_case.draft()
    draft['policy']['maximum_capital'] = '500'
    api_case.review('reviseRiskPolicy', {'policy_id': 'paper-policy-demo', 'draft': draft})
    status, receipt = api_case.post(draft, path='/api/risk-policies/paper-policy-demo/revisions')
    assert status == 202 and receipt['reason'] == 'EXECUTION_OWNER_UNCONFIRMED'
    assert receipt['status'] != 'APPLIED'
    assert api_case.grant.actor.policy_revision == 1


def test_deployment_requires_exact_human_approval(api_case):
    body = {'strategy_revision': 4, 'authorization_id': 'authorization',
            'environment': 'PAPER', 'expected_revision': 1}
    path = '/api/strategies/strategy/deployments'
    assert api_case.post(body, path=path)[0] == 403
    api_case.review('createDeployment', {'strategy_id': 'strategy', 'request': body})
    status, receipt = api_case.post(body, path=path)
    assert status == 202 and receipt['status'] == 'QUEUED'
    assert api_case.post(body, path=path) == (status, receipt)
    assert api_case.post(body, path='/api/strategies/other/deployments', key='other')[0] == 403
    api_case.grant = replace(api_case.grant, role='MODEL')
    assert api_case.post(body, path=path)[0] == 403


@pytest.mark.parametrize('scheme,token', [('http', None), ('https', 'unknown'), ('https', '')])
def test_secure_session_required(api_case, scheme, token):
    assert api_case.post(scheme=scheme, token=token)[0] == 401
    assert api_case.uow.count() == 0


@pytest.mark.parametrize('changes', [{'revoked': True}, {'expires_at': 100}])
def test_session_revocation_and_expiry_apply_to_replays(api_case, changes):
    assert api_case.post()[0] == 202
    token_hash = sha256(api_case.token.encode()).hexdigest()
    api_case.sessions[token_hash] = replace(api_case.sessions[token_hash], **changes)
    assert api_case.post()[0] == 401


def test_storage_failure_has_no_ack_or_partial_record(api_case):
    api_case.uow.fail_commit = True
    status, body = api_case.post()
    assert status == 503 and 'command_id' not in body
    assert api_case.uow.count() == 0
    api_case.uow.fail_commit = False
    assert api_case.post()[0] == 202


def test_failure_between_key_and_id_records_rolls_back(api_case):
    api_case.uow.fail_append_at = 2
    status, error = api_case.post()
    assert status == 503 and 'command_id' not in error
    assert api_case.uow.count() == 0
    api_case.uow.fail_append_at = None
    assert api_case.post()[0] == 202
    assert api_case.uow.count() == 2


def test_restart_recovers_same_receipt(api_case):
    result = api_case.post()
    api_case.inbox = DurableInbox(SQLiteUow(api_case.uow.path), lambda tx, actor, scope: api_case.grant)
    api_case.app = ControlApp(api_case.app.auth, api_case.inbox)
    assert api_case.post() == result
    assert api_case.get(result[1]['command_id'])[0] == 200


def test_concurrent_duplicate_requests(api_case):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: api_case.post(), range(8)))
    assert all(result == results[0] for result in results)
    assert results[0][0] == 202
    assert api_case.uow.count() == 2


def test_same_identity_key_cannot_move_to_another_scope(api_case):
    other = replace(api_case.scope, instrument_uid='ETH-USDT-SPOT')
    api_case.change_actor(scopes=(api_case.scope, other))
    assert api_case.post()[0] == 202
    body = api_case.request()
    body['scope']['instrument_uid'] = other.instrument_uid
    assert api_case.post(body)[0] == 409
    assert api_case.uow.count() == 2


def test_query_hides_other_identity_and_scope(api_case):
    receipt = api_case.post()[1]
    query = dict(api_case.request()['scope'], venue_account_id='another-account')
    assert api_case.get(receipt['command_id'], query=query)[0] == 403
    api_case.change_actor(identity_id='another-human')
    assert api_case.get(receipt['command_id'])[0] == 404


@pytest.mark.parametrize('field,value,status', [
    ('ownership_epoch', 2, 409), ('expected_revision', 0, 409),
    ('ownership_epoch', True, 422), ('action', 'cancel_all', 422),
    ('credentials', 'forbidden', 422), ('confirmed', True, 422)])
def test_invalid_or_stale_control_is_not_queued(api_case, field, value, status):
    body = api_case.request()
    body[field] = value
    assert api_case.post(body)[0] == status
    assert api_case.uow.count() == 0


def test_transfer_requires_scope_epoch_and_exact_review(api_case):
    body = api_case.request('transfer')
    body['transfer'] = {'transfer_id': 'transfer', 'scope': body['scope'].copy(),
        'mode': 'RETAIN_OWNER', 'old_owner': 'old', 'new_owner': 'new', 'ownership_epoch': 1}
    assert api_case.post(body)[0] == 403
    api_case.review('enqueueControl', body)
    assert api_case.post(body)[0] == 202
    body['transfer']['scope']['instrument_uid'] = 'other'
    assert api_case.post(body, key='other')[0] == 422


def test_unbound_query_is_explicitly_unavailable(api_case):
    for path in ['/api/positions', '/api/orders', '/api/risk-locks',
                 '/api/intents/unknown', '/api/accounts/paper-account/capabilities', '/api/events']:
        query = api_case.request()['scope'].copy()
        # Optional collection filters are intentionally omitted.
        status, result = api_case.app.dispatch('GET', path, scheme='https',
            headers={'authorization': 'Bearer ' + api_case.token}, query=query)
        assert status == 503
        assert result['reason_code'] == 'DOWNSTREAM_UNAVAILABLE'


@pytest.mark.parametrize('raw', [b'{"action":"exit_owned","action":"pause_new"}', b'not-json'])
def test_asgi_rejects_ambiguous_json(api_case, raw):
    sent = []
    async def receive():
        return {'type': 'http.request', 'body': raw}
    async def send(message):
        sent.append(message)
    asyncio.run(api_case.app({'type': 'http', 'method': 'POST', 'path': '/api/control-commands',
        'scheme': 'https', 'headers': [(b'content-type', b'application/json')]}, receive, send))
    assert sent[0]['status'] == 400
    assert api_case.uow.count() == 0


def test_revoked_authority_denies_even_idempotent_replay(api_case):
    assert api_case.post()[0] == 202
    api_case.grant = None
    assert api_case.post()[0] == 403
    assert api_case.uow.count() == 2


def test_models_cannot_control_or_grant_authorization(api_case):
    api_case.grant = replace(api_case.grant, role='MODEL')
    assert api_case.post()[0] == 403
    # No unreviewed authorization-creation endpoint is invented.
    assert api_case.post({}, path='/api/authorizations')[0] == 404
    assert api_case.uow.count() == 0


def test_missing_write_headers(api_case):
    for missing in ['idempotency-key', 'if-match']:
        headers = {'authorization': 'Bearer ' + api_case.token,
                   'idempotency-key': 'key', 'if-match': '1'}
        del headers[missing]
        assert api_case.app.dispatch('POST', '/api/control-commands', scheme='https',
            headers=headers, body=api_case.request())[0] == 400
    assert api_case.uow.count() == 0


def test_receipt_and_projection_follow_frozen_contract(api_case):
    status, receipt = api_case.post()
    assert status == 202
    api_case.app.validate({'$ref': '#/components/schemas/ControlCommandReceipt'}, receipt)
    status, projection = api_case.get(receipt['command_id'])
    assert status == 200
    api_case.app.validate({'$ref': '#/components/schemas/ControlCommandProjection'}, projection)


def test_asgi_success_and_duplicate_security_header(api_case):
    async def exchange(headers):
        sent = []
        async def receive():
            return {'type': 'http.request', 'body': json.dumps(api_case.request()).encode()}
        async def send(message):
            sent.append(message)
        await api_case.app({'type': 'http', 'method': 'POST', 'path': '/api/control-commands',
            'scheme': 'https', 'headers': headers}, receive, send)
        return sent
    headers = [(b'authorization', ('Bearer ' + api_case.token).encode()),
               (b'content-type', b'application/json'), (b'if-match', b'1'),
               (b'idempotency-key', b'key')]
    sent = asyncio.run(exchange(headers))
    assert sent[0]['status'] == 202
    assert (b'cache-control', b'no-store') in sent[0]['headers']
    assert json.loads(sent[1]['body'])['status'] == 'QUEUED'
    sent = asyncio.run(exchange(headers + [(b'authorization', b'Bearer other')]))
    assert sent[0]['status'] == 400


def test_unbound_proposal_ingress_does_not_fake_acceptance(api_case):
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / 'tests/fixtures/contracts/examples/decision-proposal.paper.json'
    proposal = json.loads(path.read_text(encoding='utf-8'))
    status, result = api_case.post(proposal, path='/api/decision-proposals')
    assert status == 503
    assert result['reason_code'] == 'DOWNSTREAM_UNAVAILABLE'
    assert api_case.uow.count() == 0


@pytest.mark.parametrize('field,reason', [
    ('policy_revision', 'POLICY_CHANGED'),
    ('ownership_epoch', 'STALE_OWNER_EPOCH')])
def test_replay_rechecks_current_revision_and_owner(api_case, field, reason):
    assert api_case.post()[0] == 202
    api_case.grant = replace(api_case.grant, actor=replace(api_case.actor, **{field: 2}))
    status, error = api_case.post()
    assert status == 409 and error['reason_code'] == reason
    assert api_case.uow.count() == 2


def test_policy_replay_cannot_rebind_implicit_scope(api_case):
    draft = api_case.draft()
    path = '/api/risk-policies/paper-policy-demo/revisions'
    api_case.review('reviseRiskPolicy', {'policy_id': 'paper-policy-demo', 'draft': draft})
    assert api_case.post(draft, path=path)[0] == 202
    other = replace(api_case.scope, instrument_uid='ETH-USDT-SPOT')
    api_case.change_actor(scopes=(other,))
    status, error = api_case.post(draft, path=path)
    assert status == 409 and error['reason_code'] == 'IDEMPOTENCY_CONFLICT'
    assert api_case.uow.count() == 2


def test_query_authorizes_only_requested_scope(api_case):
    receipt = api_case.post()[1]
    unrelated = replace(api_case.scope, instrument_uid='ETH-USDT-SPOT')
    api_case.change_actor(scopes=(unrelated, api_case.scope))
    visited = []

    def authority(tx, actor, scope):
        visited.append(scope)
        return api_case.grant if scope == api_case.scope else None

    api_case.inbox.authority = authority
    assert api_case.get(receipt['command_id'])[0] == 200
    assert visited == [api_case.scope]


@pytest.mark.parametrize('changes', [
    {'authorization_id': 'another-authorization'},
    {'deployment_id': 'another-deployment'}])
def test_rebound_session_cannot_replay_or_read_old_command(api_case, changes):
    receipt = api_case.post()[1]
    api_case.change_actor(**changes)
    assert api_case.post()[0] == 403
    assert api_case.get(receipt['command_id'])[0] == 404
    assert api_case.uow.count() == 2


def test_policy_collateral_mismatch_cannot_be_reviewed_away(api_case):
    draft = api_case.draft()
    draft['policy']['collateral_group_id'] = 'other-group'
    api_case.review('reviseRiskPolicy', {'policy_id': 'paper-policy-demo', 'draft': draft})
    status, error = api_case.post(draft, path='/api/risk-policies/paper-policy-demo/revisions')
    assert status == 403 and error['reason_code'] == 'COLLATERAL_SCOPE_MISMATCH'
    assert api_case.uow.count() == 0


def test_live_session_cannot_enqueue_even_with_user_grant(api_case):
    live_scope = replace(api_case.scope, environment=t.Environment.LIVE)
    api_case.change_actor(environment=t.Environment.LIVE, scopes=(live_scope,))
    body = api_case.request()
    body['scope']['environment'] = 'LIVE'
    assert api_case.post(body)[0] == 403
    assert api_case.uow.count() == 0


@pytest.mark.parametrize('kind,status', [('oversized', 413), ('duplicate-query', 400),
                                       ('disconnect', None)])
def test_asgi_transport_rejections_do_not_enqueue(api_case, kind, status):
    sent = []

    async def receive():
        if kind == 'disconnect':
            return {'type': 'http.disconnect'}
        return {'type': 'http.request', 'body': b'x' * 131073 if kind == 'oversized' else b''}

    async def send(message):
        sent.append(message)

    asyncio.run(api_case.app({'type': 'http', 'method': 'GET', 'path': '/api/positions',
        'scheme': 'https', 'headers': [], 'query_string': b'environment=PAPER&environment=LIVE'},
        receive, send))
    if status is None:
        assert sent == []
    else:
        assert sent[0]['status'] == status
    assert api_case.uow.count() == 0
