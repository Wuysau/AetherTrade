"""Authenticated HTTP contracts for profile selection; only mocked model calls."""
import json

import pytest

from server.test_model_api import api, configure, login


def second(client):
    response = client.post('/api/model-configs', json={
        'name': 'Independent Claude-compatible profile', 'protocol': 'openai_chat',
        'base_url': 'http://127.0.0.1:18083/v1', 'model': 'controlled-model',
        'enabled': True, 'local_mode': True, 'api_key': 'second-fixture-secret'})
    assert response.status_code == 200, response.text
    return response.json()


def test_profile_http_auth_csrf_independent_state_and_delete(api):
    client, _, _, requests = api
    assert client.get('/api/model-configs').status_code == 401
    login(client)
    assert configure(client).status_code == 200
    profile = second(client)
    identifier = profile['id']
    csrf = client.headers.pop('X-CSRF-Token')
    assert client.request('DELETE', f'/api/model-configs/{identifier}', json={'expected_revision': 1}).status_code == 403
    client.headers['X-CSRF-Token'] = csrf
    result = client.post(f'/api/model-configs/{identifier}/test', json={'expected_revision': 1})
    assert result.status_code == 200
    assert requests[-1].headers['authorization'] == 'Bearer second-fixture-secret'
    assert client.get('/api/model-config').json()['last_test_at'] is None
    edited = client.put(f'/api/model-configs/{identifier}', json={'expected_revision': 1, 'name': 'Second renamed'})
    assert edited.status_code == 200 and edited.json()['revision'] == 2
    assert client.get('/api/model-config').json()['revision'] == 1
    dashboard = client.get('/api/dashboard')
    assert len(dashboard.json()['model_profiles']) == 2
    assert all(secret not in dashboard.text for secret in ('second-fixture-secret', 'provider-fixture-secret'))
    assert client.request('DELETE', f'/api/model-configs/{identifier}', json={'expected_revision': 1}).status_code == 409
    assert client.request('DELETE', '/api/model-configs/default', json={'expected_revision': 1}).status_code == 409
    assert client.request('DELETE', f'/api/model-configs/{identifier}', json={'expected_revision': 2}).json()['deleted'] is True
    assert client.get(f'/api/model-configs/{identifier}').status_code == 404


def test_cross_analysis_http_same_context_and_replay_without_order(api):
    client, _, feed, requests = api
    login(client)
    configure(client)
    profile = second(client)
    body = dict(symbol='TSLABUSDT', analysis_mode='consensus', model_profile_ids=['default', profile['id']],
                expected_revisions={'default': 1, profile['id']: 1}, acknowledge_cost=True)
    response = client.post('/api/decisions/analyze', headers={'Idempotency-Key': 'cross-http'}, json=body)
    assert response.status_code == 200, response.text
    decision = response.json()['decision']
    assert decision['consensus_status'] == 'AGREED' and decision['action'] == 'HOLD'
    assert {item['profile_id'] for item in decision['model_results']} == set(body['model_profile_ids'])
    assert len(requests) == 2
    assert {request.headers['authorization'] for request in requests} == {'Bearer provider-fixture-secret', 'Bearer second-fixture-secret'}
    contexts = [json.loads(request.content)['messages'][-1]['content'] for request in requests]
    assert contexts[0] == contexts[1]
    client.put(f"/api/model-configs/{profile['id']}", json={'expected_revision': 1, 'enabled': False})
    feed.stale = True
    replay = client.post('/api/decisions/analyze', headers={'Idempotency-Key': 'cross-http'}, json=body)
    assert replay.status_code == 200 and replay.json() == response.json()
    assert len(requests) == 2
    dashboard = client.get('/api/dashboard').json()
    assert len(dashboard['decisions']) == 1 and not dashboard['orders']


def test_strategy_selection_and_referenced_delete_http(api):
    client, _, _, requests = api
    login(client)
    profile = second(client)
    base = dict(name='Bound draft', symbol='BTCUSDT', mode='AI', budget='100', target_pct='1')
    saved = client.post('/api/strategies', json={**base, 'model_profile_ids': [profile['id']]})
    assert saved.status_code == 200, saved.text
    assert saved.json()['model_profile_ids'] == [profile['id']]
    assert client.request('DELETE', f"/api/model-configs/{profile['id']}", json={'expected_revision': 1}).status_code == 409
    empty = client.post('/api/strategies', json={**base, 'name': 'Incomplete draft', 'model_profile_ids': []})
    legacy = client.post('/api/strategies', json={**base, 'name': 'Legacy draft'})
    assert empty.status_code == legacy.status_code == 200
    assert empty.json()['model_profile_ids'] == []
    assert legacy.json()['model_profile_ids'] == ['default']
    assert not requests


@pytest.mark.parametrize('change', [
    {'acknowledge_cost': False}, {'acknowledge_cost': 'true'},
    {'model_profile_ids': ['default', 'default']}, {'expected_revisions': {'default': -1}},
    {'expected_revisions': {'default': True}}, {'analysis_mode': 'consensus'},
    {'model_profile_ids': ['default', 'other'], 'expected_revisions': {'default': 1, 'other': 1}},
])
def test_analysis_http_rejects_ambiguous_selection_before_provider(api, change):
    client, _, _, requests = api
    login(client)
    body = dict(symbol='BTCUSDT', analysis_mode='single', model_profile_ids=['default'],
                expected_revisions={'default': 0}, acknowledge_cost=True)
    response = client.post('/api/decisions/analyze', headers={'Idempotency-Key': 'invalid'}, json={**body, **change})
    assert response.status_code == 422 and not requests
