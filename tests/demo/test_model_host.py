"""The real ModelService and provider parser, with in-process HTTP only."""
import asyncio
import json
import httpx
from fastapi.testclient import TestClient
from trading_system.server.app import create_app
from trading_system.server.config import Settings
from test_host import host, frame, assessment


def test_native_model_receipt_drives_demo_command_without_paper_order(host, tmp_path):
    service, repo, scope, config = host
    requests = []
    def provider(request):
        body = json.loads(request.content)
        context = json.loads(body['messages'][-1]['content'])
        assert context['analysis_snapshot']['schema_revision'] == 'demo-analysis-snapshot-v1'
        assert 'demo-session:' not in request.content.decode()
        requests.append(body)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(assessment(context['analysis_snapshot']))}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 10}})
    model_client = httpx.AsyncClient(transport=httpx.MockTransport(provider), trust_env=False)
    settings = Settings(database_url=f'sqlite:///{tmp_path / "model.sqlite"}', testing=True, market_enabled=False,
        allowed_origins=('http://testserver',), model_key_path=tmp_path / 'models.key', web_dist=tmp_path / 'web')
    app = create_app(settings, model_client=model_client)
    with TestClient(app) as client:
        client.headers['Origin'] = 'http://testserver'
        setup = client.post('/api/setup', json={'username': 'owner', 'password': 'local-fixture-password', 'initial_cash': '100000'})
        client.headers['X-CSRF-Token'] = setup.json()['csrf_token']
        saved = client.put('/api/model-config', json={'protocol': 'openai_chat', 'base_url': 'http://127.0.0.1:18083/v1',
            'model': 'controlled-model', 'enabled': True, 'local_mode': True, 'expected_revision': 0, 'api_key': 'provider-fixture-secret'})
        assert saved.status_code == 200
        ai = {**config, 'revision': 2, 'mode': 'AI', 'allow_model_calls': True, 'profile': {**config['profile'], 'strategy_revision': 2}}
        repo.save_strategy(scope, ai, expected_revision=1)
        service.models = app.state.models
        def current():
            value = frame(scope, ai)
            value['bindings']['account_revision'] = repo.account(scope)['revision']
            from trading_system.server.demo_snapshot import snapshot_hash
            value['snapshot_hash'] = snapshot_hash(value); value['snapshot_id'] = 'demo-snapshot:' + value['snapshot_hash'][:32]
            return value
        service.frame_loader = current
        decision = asyncio.run(service.assess_once('model-run'))
        assert decision['result']['decision']['assessment_status'] == 'DECIDED', decision['result']
        command = service.admit_assessment('model-run')
        assert command['state'] == 'DURABLE_READY'
        assert len(requests) == 1
        assert asyncio.run(service.assess_once('model-run'))['command_id'] == command['command_id']
        assert len(requests) == 1
        assert client.get('/api/dashboard').json()['orders'] == []
        assert client.get('/api/dashboard').json()['decisions'] == []
        assert decision['result']['decision']['environment'] == 'BINANCE_DEMO'
    asyncio.run(model_client.aclose())
