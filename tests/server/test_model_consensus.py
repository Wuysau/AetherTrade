"""Durable analysis and conservative aggregation, using a mock HTTP provider."""
import asyncio
import hashlib
import json

import httpx
import pytest
from sqlalchemy import select

from trading_system.server import model_repository as repository
from trading_system.server.errors import ApiError
from trading_system.server.model_service import ModelService
from trading_system.server.store import Store
from server.test_models import fresh_context
from server.test_model_profiles import profile_payload, service


def profiles(service, count=2):
    return [service.create_config(dict(profile_payload(f'Profile {i}', api_key=f'key-{i}'),
                                       model=f'model-{i}')) for i in range(count)]


def response(action='BUY', *, usage=True):
    advice = dict(symbol='BTCUSDT', action=action, summary=f'Actual {action}',
                  counter_evidence=['Actual counter evidence'], invalidation='Actual invalidation')
    body = {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(advice)}}]}
    if usage:
        body['usage'] = {'prompt_tokens': 3, 'completion_tokens': 5}
    return httpx.Response(200, json=body)


def selection(saved):
    return [p['id'] for p in saved], {p['id']: p['revision'] for p in saved}


@pytest.mark.parametrize(('actions', 'status', 'action'), [
    (['BUY', 'BUY'], 'AGREED', 'BUY'),
    (['SELL', 'SELL'], 'AGREED', 'SELL'),
    (['HOLD', 'HOLD'], 'AGREED', 'HOLD'),
    (['BUY', 'SELL'], 'DISAGREED', 'HOLD'),
    (['BUY', 'HOLD'], 'DISAGREED', 'HOLD'),
])
def test_consensus_preserves_actual_advice_and_conservative_direction(service, actions, status, action):
    async def run():
        saved = profiles(service)
        ids, revisions = selection(saved)
        calls = []
        def handle(request):
            body = json.loads(request.content)
            index = int(body['model'].split('-')[-1])
            calls.append(request)
            assert request.headers['authorization'] == f'Bearer key-{index}'
            return response(actions[index])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.client = client
            result = await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'aggregate', fresh_context())
        decision = result['decision']
        assert decision['action'] == action and decision['consensus_status'] == status
        assert decision['model_profile_ids'] == ids and decision['profile_revisions'] == revisions
        assert decision['risk_status'] == 'NOT_EVALUATED'
        assert [r['decision']['summary'] for r in decision['model_results']] == [f'Actual {a}' for a in actions]
        assert [r['status'] for r in decision['model_results']] == ['SUCCEEDED', 'SUCCEEDED']
        assert [r['profile_name'] for r in decision['model_results']] == ['Profile 0', 'Profile 1']
        assert all(r['decision']['risk_status'] == 'NOT_EVALUATED' for r in decision['model_results'])
        assert result['usage'] == {'input_tokens': 6, 'output_tokens': 10}
        assert len(calls) == 2 and service.decisions() == [decision]
        with service.store.transaction() as connection:
            records = list(connection.execute(select(repository.runs.c.data)).scalars())
        assert len(records) == 3 and sum(bool(r.get('component')) for r in records) == 2
    asyncio.run(run())


def test_consensus_runs_three_concurrently_with_one_immutable_minimal_snapshot(service):
    async def run():
        saved = profiles(service, 3)
        ids, revisions = selection(saved)
        context = dict(fresh_context(), secret='must-not-leak', account={'cash': '100', 'password': 'must-not-leak'})
        all_started, release = asyncio.Event(), asyncio.Event()
        received = []
        async def handle(request):
            received.append(json.loads(json.loads(request.content)['messages'][-1]['content']))
            if len(received) == 1:
                context['quote']['price'] = '999'  # Before the other tasks dispatch.
            if len(received) == 3:
                all_started.set()
            await release.wait()
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.client = client
            task = asyncio.create_task(service.analyze('BTCUSDT', 'consensus', ids, revisions, 'shared', context))
            try:
                await asyncio.wait_for(all_started.wait(), timeout=3)
                with service.store.transaction() as connection:
                    assert repository.read_run(connection, 'shared')['status'] == 'IN_FLIGHT'
                context['quote']['price'] = '999'
                service.save_config({'expected_revision': 1, 'name': 'Edited while waiting'}, ids[0])
            finally:
                release.set()
            result = await task
        assert len(received) == 3 and received[0] == received[1] == received[2]
        assert received[0]['quote']['price'] == '100'
        assert received[0]['account'] == {'cash': '100'}
        assert 'must-not-leak' not in json.dumps(received)
        assert result['decision']['model_results'][0]['profile_name'] == 'Profile 0'
        assert result['decision']['profile_revisions'][ids[0]] == 1
        assert result['decision']['data_as_of'] == received[0]['quote']['as_of']
        assert len(result['decision']['snapshot_hash']) == 64
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['http', 'malformed', 'timeout', 'unexpected'])
def test_participant_failure_is_incomplete_hold_and_saved_without_secret_leak(service, failure):
    async def run():
        saved = profiles(service)
        ids, revisions = selection(saved)
        calls = []
        def handle(request):
            calls.append(request)
            if json.loads(request.content)['model'] == 'model-0':
                return response('SELL')
            if failure == 'timeout':
                raise httpx.ReadTimeout('key-1 provider-private-data', request=request)
            if failure == 'unexpected':
                raise RuntimeError('key-1 provider-private-data')
            if failure == 'malformed':
                return httpx.Response(200, json={'choices': []})
            return httpx.Response(500, text='key-1 provider-private-data')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.client = client
            result = await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'failure', fresh_context())
            assert await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'failure', {}) == result
        decision = result['decision']
        assert decision['action'] == 'HOLD' and decision['consensus_status'] == 'INCOMPLETE'
        assert decision['model_results'][0]['decision']['action'] == 'SELL'
        failed = decision['model_results'][1]
        assert failed['status'] == 'FAILED' and failed['error'] and 'decision' not in failed
        assert result['usage'] is None and len(calls) == 2
        assert 'provider-private-data' not in json.dumps(result) and 'key-1' not in json.dumps(result)
        assert service.decisions() == [decision]
    asyncio.run(run())


def test_unknown_usage_stays_unknown_in_aggregate(service):
    async def run():
        saved = profiles(service)
        ids, revisions = selection(saved)
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: response(usage=json.loads(request.content)['model'] == 'model-0'))) as client:
            service.client = client
            result = await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'usage', fresh_context())
        assert result['usage'] is None
        assert result['decision']['model_results'][0]['usage'] == {'input_tokens': 3, 'output_tokens': 5}
        assert result['decision']['model_results'][1]['usage'] == {'input_tokens': None, 'output_tokens': None}
    asyncio.run(run())


def test_consensus_restart_replays_after_profile_edit_and_conflicts_on_changed_selection(service):
    async def run():
        saved = profiles(service, 3)
        ids, revisions = selection(saved[:2])
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request) or response())) as client:
            service.client = client
            result = await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'durable', fresh_context())
            service.save_config({'expected_revision': 1, 'enabled': False}, ids[0])
            reopened = Store(service.store.engine.url.render_as_string(hide_password=False))
            reopened.initialize()
            restarted = ModelService(reopened, service.vault.path, client=client)
            try:
                assert await restarted.analyze('BTCUSDT', 'consensus', ids, revisions, 'durable', {}) == result
                different_ids, different_revs = selection(saved[1:])
                with pytest.raises(ApiError) as error:
                    await restarted.analyze('BTCUSDT', 'consensus', different_ids, different_revs, 'durable', {})
                assert error.value.code == 'IDEMPOTENCY_CONFLICT'
            finally:
                await restarted.close()
                reopened.close()
        assert len(calls) == 2
    asyncio.run(run())


def test_inflight_parent_is_not_resubmitted(service):
    async def run():
        saved = profiles(service)
        ids, revisions = selection(saved)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def handle(request):
            calls.append(request)
            entered.set()
            await release.wait()
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.client = client
            pending = asyncio.create_task(service.analyze('BTCUSDT', 'consensus', ids, revisions, 'inflight', fresh_context()))
            try:
                await entered.wait()
                with pytest.raises(ApiError) as error:
                    await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'inflight', fresh_context())
                assert error.value.code == 'MODEL_RUN_NOT_RETRYABLE'
            finally:
                release.set()
                await pending
        assert len(calls) == 2
    asyncio.run(run())


def test_cancelled_parent_never_resends_after_restart(service):
    async def run():
        saved = profiles(service)
        ids, revisions = selection(saved)
        entered = asyncio.Event()
        calls = []
        async def handle(request):
            calls.append(request)
            if len(calls) == 2:
                entered.set()
            await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.client = client
            pending = asyncio.create_task(service.analyze('BTCUSDT', 'consensus', ids, revisions, 'cancelled', fresh_context()))
            await asyncio.wait_for(entered.wait(), timeout=3)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            reopened = Store(service.store.engine.url.render_as_string(hide_password=False))
            reopened.initialize()
            restarted = ModelService(reopened, service.vault.path, client=client)
            try:
                with pytest.raises(ApiError) as error:
                    await restarted.analyze('BTCUSDT', 'consensus', ids, revisions, 'cancelled', fresh_context())
                assert error.value.code == 'MODEL_RUN_NOT_RETRYABLE'
            finally:
                await restarted.close()
                reopened.close()
        with service.store.transaction() as connection:
            records = list(connection.execute(select(repository.runs.c.data)).scalars())
        assert len(records) == 3 and all(r['status'] == 'FAILED' for r in records)
        assert len(calls) == 2 and service.decisions() == []
    asyncio.run(run())


def test_disabled_participant_is_incomplete_and_never_falls_back_to_default(service):
    async def run():
        saved = profiles(service)
        saved[0] = service.save_config({'expected_revision': 1, 'enabled': False}, saved[0]['id'])
        ids, revisions = selection(saved)
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request) or response())) as client:
            service.client = client
            result = await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'disabled', fresh_context())
            with pytest.raises(ApiError) as error:
                await service.analyze('BTCUSDT', 'single', [ids[0]], {ids[0]: 2}, 'disabled-single', fresh_context())
            assert error.value.code == 'MODEL_NOT_CONFIGURED'
        assert len(calls) == 1 and json.loads(calls[0].content)['model'] == 'model-1'
        assert result['decision']['action'] == 'HOLD' and result['decision']['consensus_status'] == 'INCOMPLETE'
        assert result['decision']['model_results'][0]['status'] == 'FAILED'
        assert result['decision']['model_results'][0]['config_revision'] == 2
    asyncio.run(run())


def test_selected_revision_mismatch_or_missing_profile_prevents_dispatch(service):
    async def run():
        saved = profiles(service)
        ids, revisions = selection(saved)
        service.save_config({'expected_revision': 1, 'enabled': False}, ids[0])
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request) or response())) as client:
            service.client = client
            with pytest.raises(ApiError) as error:
                await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'stale', fresh_context())
            assert error.value.code == 'MODEL_REVISION_CONFLICT'
            with pytest.raises(ApiError) as error:
                await service.analyze('BTCUSDT', 'consensus', [ids[1], 'missing'], {ids[1]: 1, 'missing': 1}, 'missing', fresh_context())
            assert error.value.code == 'MODEL_PROFILE_NOT_FOUND'
        with service.store.transaction() as connection:
            assert repository.read_run(connection, 'stale') is None
            assert repository.read_run(connection, 'missing') is None
        assert calls == []
    asyncio.run(run())


def test_selected_key_in_context_never_reaches_another_provider(service):
    async def run():
        saved = profiles(service)
        ids, revisions = selection(saved)
        context = dict(fresh_context(), account={'currency': 'key-1'})
        calls = []
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: calls.append(request) or response())) as client:
            service.client = client
            with pytest.raises(ApiError) as error:
                await service.analyze('BTCUSDT', 'consensus', ids, revisions, 'secret', context)
            assert error.value.code == 'INVALID_MODEL_CONTEXT'
        assert calls == []
    asyncio.run(run())


def test_single_nondefault_routes_selected_key_and_snapshot(service):
    async def run():
        saved = profiles(service, 1)
        ids, revisions = selection(saved)
        def handle(request):
            assert request.headers['authorization'] == 'Bearer key-0'
            assert json.loads(request.content)['model'] == 'model-0'
            return response()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            service.client = client
            result = await service.analyze('BTCUSDT', 'single', ids, revisions, 'single', fresh_context())
        decision = result['decision']
        assert decision['analysis_mode'] == 'single' and decision['model_profile_id'] == ids[0]
        assert decision['profile_name'] == 'Profile 0' and decision['profile_revisions'] == revisions
        assert decision['config_revision'] == 1 and decision['model'] == 'model-0'
    asyncio.run(run())


def test_default_single_supports_legacy_generate_double_and_old_receipts(service, monkeypatch):
    async def run():
        calls = []
        async def old_generate(symbol, revision, key, context):
            calls.append((symbol, revision, key, context))
            return {'decision': {'id': 'old-test-double'}}
        with monkeypatch.context() as patch:
            patch.setattr(service, 'generate', old_generate)
            result = await service.analyze('BTCUSDT', 'single', ['default'], {'default': 4}, 'compat', {})
        assert result['decision']['id'] == 'old-test-double' and len(calls) == 1
        cached = {'decision': {'id': 'old-persisted', 'created_at': '2020-01-01T00:00:00+00:00'}, 'usage': None}
        fingerprint = hashlib.sha256(json.dumps(['BTCUSDT', 4]).encode()).hexdigest()
        with service.store.transaction() as connection:
            repository.write_run(connection, 'old-receipt', {'fingerprint': fingerprint, 'status': 'SUCCEEDED', 'result': cached}, new=True)
        assert await service.analyze('BTCUSDT', 'single', ['default'], {'default': 4}, 'old-receipt', {}) == cached
        assert service.decisions() == [cached['decision']]
    asyncio.run(run())


@pytest.mark.parametrize(('mode', 'ids', 'revisions'), [
    ('consensus', ['default'], {'default': 0}),
    ('consensus', ['default', 'default'], {'default': 0}),
    ('consensus', ['a', 'b', 'c', 'd'], dict(a=1, b=1, c=1, d=1)),
    ('single', ['a', 'b'], dict(a=1, b=1)),
    ('other', ['default'], {'default': 0}),
    ('single', ['default'], {'default': True}),
    ('single', ['default'], {}),
])
def test_invalid_analysis_selection_rejected_before_provider(service, mode, ids, revisions):
    async def run():
        with pytest.raises(ApiError):
            await service.analyze('BTCUSDT', mode, ids, revisions, 'invalid', fresh_context())
        assert service.decisions() == []
    asyncio.run(run())
