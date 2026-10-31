"""Explicit local fake research only; no provider network or trading authority."""
import asyncio,copy,json
from contextlib import contextmanager
from datetime import datetime,timedelta

import pytest

from trading_system.server import model_client,model_repository as repository,model_service,program_model_context
from trading_system.server.model_api import ProfileAnalysis
from trading_system.server.model_service import ModelService
from trading_system.server.program_model_service import authorize_research
from trading_system.server.store import Store
from test_program_model_context import case,NOW


@contextmanager
def research(monkeypatch,tmp_path):
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(model_service,'datetime',Clock)
    monkeypatch.setattr(program_model_context,'datetime',Clock)
    *_,frame,value=case(monkeypatch)
    store=Store(f"sqlite:///{tmp_path/'cross-program.sqlite'}");store.initialize()
    service=ModelService(store,tmp_path/'key',client=object())
    with store.transaction() as connection:
        for name in ('default','review'):
            repository.write_profile(connection,name,dict(repository.DEFAULT,id=name,name=name,model=name,
                enabled=True,local_mode=True,base_url='http://127.0.0.1',protocol='openai_chat'))
    session=store.setup('researcher','explicit-local-research','100000',24)
    request=ProfileAnalysis(symbol='BTCUSDT',analysis_mode='consensus',model_profile_ids=['default','review'],
        expected_revisions={'default':0,'review':0},acknowledge_cost=True,decision_schema='assessment_v1',
        decision_policy='evidence_review',task_profile={k:v for k,v in frame.payload['analysis_snapshot']['task_profile'].items() if k!='strategy_revision'})
    try:yield service,store,frame,value,request,session['token']
    finally:store.close()


def envelope(value):
    return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}],
        'usage':{'prompt_tokens':10,'completion_tokens':20}}


def review_issue(category='PREFERENCE',predicate=None):
    return dict(schema_revision='review_v1',issues=[dict(category=category,evidence_ref='quote',
        predicate=predicate,severity='SOFT' if category=='PREFERENCE' else 'HARD',suggestion='Review supplied evidence')])


def test_programme_reviewer_prompt_reuses_review_contract_and_keeps_exact_candidate(monkeypatch):
    *_,frame,value=case(monkeypatch)
    config=dict(model='fixture',protocol='openai_chat',base_url='http://127.0.0.1',max_output_tokens=None)
    context=dict(frame.payload,context_hash=frame.context_hash,role='review',candidate=value)
    _,body=model_client.payload(config,context)
    assert 'Return only review_v1' in body['messages'][0]['content']
    assert json.loads(body['messages'][1]['content'])['candidate']==value


def test_soft_preferences_return_to_primary_without_discarding_valid_v3_candidate(monkeypatch,tmp_path):
    with research(monkeypatch,tmp_path) as (service,store,frame,value,request,session):
        authorized=authorize_research(service,request,session,'cross-soft',frame)
        calls=[]
        async def provider(*args):
            body=args[-1];context=json.loads(body['messages'][1]['content']);role=context['role'];calls.append(role)
            with store.transaction() as connection:
                saved=repository.read_run(connection,'cross-soft')
                assert len(saved['requests'])==len(calls) and saved['requests'][-1]['role']==role
            if role=='review':
                assert context['candidate']['proposal']==value['proposal']
                return envelope(review_issue())
            return envelope(value)
        monkeypatch.setattr(model_client,'request',provider)
        result=asyncio.run(service.analyze('BTCUSDT','consensus',['default','review'],{'default':0,'review':0},
            'cross-soft',frame,decision_policy='evidence_review',research_request=authorized))
        decision=result['decision']
        assert calls==['primary','review','adjudication']
        assert decision['proposal']==value['proposal'] and decision['review_report']['status']=='SOFT'
        assert decision['assessment_status']=='UNQUALIFIED' and decision['action'] is None
        assert decision['execution_authorized'] is False
        assert asyncio.run(service.analyze('BTCUSDT','consensus',['default','review'],{'default':0,'review':0},
            'cross-soft',frame,decision_policy='evidence_review',research_request=authorized))==result
        assert len(calls)==3


def test_research_acknowledgement_cannot_be_reused_for_a_different_review_policy(monkeypatch,tmp_path):
    from trading_system.server.errors import ApiError
    with research(monkeypatch,tmp_path) as (service,store,frame,value,request,session):
        unanimous=request.model_copy(update={'decision_policy':'unanimous'})
        authorized=authorize_research(service,unanimous,session,'cross-wrong-policy',frame)
        async def forbidden(*args):raise AssertionError('Policy mismatch must be rejected before provider')
        monkeypatch.setattr(model_client,'request',forbidden)
        with pytest.raises(ApiError,match='EXPLICIT_RESEARCH_AUTHORIZATION_REQUIRED'):
            asyncio.run(service.analyze('BTCUSDT','consensus',['default','review'],{'default':0,'review':0},
                'cross-wrong-policy',frame,decision_policy='evidence_review',research_request=authorized))


def test_plan_expiring_during_review_is_not_exposed_as_a_current_candidate(monkeypatch,tmp_path):
    with research(monkeypatch,tmp_path) as (service,store,frame,value,request,session):
        authorized=authorize_research(service,request,session,'cross-expired',frame)
        class LateClock(datetime):
            @classmethod
            def now(cls,tz=None):return NOW+timedelta(seconds=60)
        async def provider(*args):
            context=json.loads(args[-1]['messages'][1]['content'])
            if context['role']=='review':
                monkeypatch.setattr(program_model_context,'datetime',LateClock)
                return envelope({'schema_revision':'review_v1','issues':[]})
            return envelope(value)
        monkeypatch.setattr(model_client,'request',provider)
        result=asyncio.run(service.analyze('BTCUSDT','consensus',['default','review'],{'default':0,'review':0},
            'cross-expired',frame,decision_policy='evidence_review',research_request=authorized))
        assert result['decision']['assessment_status']=='EXPIRED'
        assert result['decision']['proposal'] is None and result['decision']['candidate_proposal']==value['proposal']


@pytest.mark.parametrize('failure,expected',[
    ('hard','INVALID_PLAN'),('unverified','REVIEW_REQUIRED'),('request_failed','REVIEW_REQUIRED'),
])
def test_critical_or_failed_review_retains_primary_evidence_without_an_approvable_proposal(monkeypatch,tmp_path,failure,expected):
    with research(monkeypatch,tmp_path) as (service,store,frame,value,request,session):
        authorized=authorize_research(service,request,session,'cross-critical',frame)
        calls=[]
        async def provider(*args):
            context=json.loads(args[-1]['messages'][1]['content']);role=context['role'];calls.append(role)
            if role=='primary':return envelope(value)
            if failure=='request_failed':raise model_client.invalid()
            predicate=dict(field='market.quote.price' if failure=='hard' else 'market.quote.unavailable',operator='GT',value='0')
            return envelope(review_issue('FACT',predicate))
        monkeypatch.setattr(model_client,'request',provider)
        result=asyncio.run(service.analyze('BTCUSDT','consensus',['default','review'],{'default':0,'review':0},
            'cross-critical',frame,decision_policy='evidence_review',research_request=authorized))
        assert calls==['primary','review']
        decision=result['decision']
        assert decision['assessment_status']==expected and decision['proposal'] is None
        assert decision['candidate_proposal']==value['proposal'] and decision['action'] is None
        assert decision['component_results'][0]['response']['proposal']==value['proposal']


@pytest.mark.parametrize('change',['target','reverse','leverage'])
def test_adjudication_cannot_increase_or_reverse_primary_exposure(monkeypatch,tmp_path,change):
    with research(monkeypatch,tmp_path) as (service,store,frame,value,request,session):
        authorized=authorize_research(service,request,session,'cross-bounded',frame)
        async def provider(*args):
            context=json.loads(args[-1]['messages'][1]['content'])
            if context['role']=='review':return envelope(review_issue())
            revised=copy.deepcopy(value)
            if context['role']=='adjudication':
                if change=='target':revised['proposal']['target_value']='8000'
                if change=='reverse':revised['proposal']['target_side']='SHORT'
                if change=='leverage':revised['proposal']['requested_leverage']=2
            return envelope(revised)
        monkeypatch.setattr(model_client,'request',provider)
        result=asyncio.run(service.analyze('BTCUSDT','consensus',['default','review'],{'default':0,'review':0},
            'cross-bounded',frame,decision_policy='evidence_review',research_request=authorized))
        assert result['decision']['assessment_status']=='INVALID_PLAN'
        assert result['decision']['proposal'] is None and result['decision']['candidate_proposal']==value['proposal']
