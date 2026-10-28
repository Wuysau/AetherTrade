"""Actual HTTP -> shared snapshot -> persisted assessment, zero external IO."""
import json
from server.test_model_api import api, login, configure
from support.decision_fixture import PROFILE, ResearchFeed
from trading_system.server import model_client


def body():
    return dict(symbol='BTCUSDT',analysis_mode='single',model_profile_ids=['default'],expected_revisions={'default':1},acknowledge_cost=True,decision_schema='assessment_v1',task_profile=PROFILE)


def test_d01_http_missing_candles_does_not_call_provider(api):
    client,app,feed,requests=api
    login(client);configure(client)
    result=client.post('/api/decisions/analyze',json=body(),headers={'Idempotency-Key':'data-blocked'})
    assert result.status_code==200,result.text
    assert result.json()['decision']['assessment_status']=='DATA_BLOCKED'
    assert result.json()['decision']['proposal'] is None
    assert requests==[]


def test_d02_d03_http_uses_closed_snapshot_and_replay_artifact(api,monkeypatch):
    client,app,feed,requests=api
    login(client);configure(client)
    research=ResearchFeed(feed.snapshot)
    original=feed.snapshot
    feed.snapshot=lambda: dict(original(),source_id='fixture',source_generation=1,source_revision=1,venue='BINANCE',product='SPOT')
    research._snapshot=original
    feed.candles=research.candles
    actual=[]
    async def request(client,config,secret,method,path,payload):
        actual.append(payload)
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(schema_revision='assessment_v1',summary='No economic advantage',proposal=None,evidence_claims=[],watch_conditions=[],next_review_at=None))}}]}
    monkeypatch.setattr(model_client,'request',request)
    response=client.post('/api/decisions/analyze',json=body(),headers={'Idempotency-Key':'same'})
    assert response.status_code==200,response.text
    decision=response.json()['decision']
    assert decision['assessment_status']=='DECIDED'
    assert decision['symbol']=='BTCUSDT' and decision['reason_code']=='NO_EDGE'
    sent=json.loads(actual[0]['messages'][-1]['content'])['analysis_snapshot']
    assert len(sent['market']['candles']['1h'])==3 and sent['market']['features']['1h']['bars_used']==3
    again=client.post('/api/decisions/analyze',json=body(),headers={'Idempotency-Key':'same'})
    assert again.json()==response.json() and len(actual)==1
    assert client.get('/api/dashboard').json()['orders']==[]
