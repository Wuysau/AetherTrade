import copy
import json
from datetime import datetime, timezone, timedelta
import pytest
from trading_system.server import model_client
from trading_system.server.decision_assessment import validate_assessment, validate_review


def snapshot():
    now = datetime.now(timezone.utc)
    return dict(snapshot_id='s', snapshot_hash='hash', evidence_cutoff=now.isoformat(),
        instrument_ref='BTCUSDT', product='SPOT', evidence_refs=['quote'],
        task_profile={'strategy_revision': 1, 'allowed_operations': ['SET_TARGET', 'HOLD', 'UPDATE_EXIT']},
        market={'quote': {'price': '100'}}, portfolio={}, costs={'complete': True},
        constraints={'maximum_target_notional': '25', 'protection_supported': True})


def candidate():
    until = (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat()
    proposal = dict(contract_version='2026-10-20', proposal_id='p', instrument_ref='BTCUSDT', action='SET_TARGET',
        target={'side': 'LONG', 'sizing': 'QUOTE_NOTIONAL', 'value': '5'},
        entry={'order_preference': 'MARKET', 'limit_price': None, 'maximum_entry_slippage_bps': '10', 'entry_deadline': until, 'allow_partial': False},
        exit_plan={'stop_price': '90', 'stop_trigger': 'LAST', 'review_due_at': until, 'maximum_holding_seconds': 3600, 'thesis_invalidation': 'Below support'},
        evidence_refs=['quote'], rationale={'summary': 'Measured opportunity', 'counter_evidence': [], 'missing_information': []}, valid_until=until)
    return dict(schema_revision='assessment_v1', summary='Measured opportunity', proposal=proposal, evidence_claims=[], watch_conditions=[], next_review_at=until)


def test_d10_d17_valid_wire_candidate_preserves_requested_size():
    result = validate_assessment(candidate(), snapshot())
    assert result['proposal']['target']['value'] == '5'


@pytest.mark.parametrize('mutate', [
    lambda p: p['target'].update(value='26'),
    lambda p: p['exit_plan'].update(stop_price='110'),
    lambda p: p.update(evidence_refs=['invented']),
    lambda p: p.update(instrument_ref='ETHUSDT'),
    lambda p: p.update(valid_until='2020-01-01T00:00:00Z'),
])
def test_d14_semantic_errors_are_rejected(mutate):
    value = candidate(); mutate(value['proposal'])
    with pytest.raises(ValueError): validate_assessment(value, snapshot())


def test_d22_hold_without_invented_wire_plan():
    value = candidate(); value['proposal'] = None; value['summary'] = 'No economic advantage'
    assert validate_assessment(value, snapshot())['proposal'] is None


def test_d14_unverifiable_critical_issue_not_soft():
    report = validate_review({'schema_revision':'review_v1', 'issues': [{'category':'FACT', 'evidence_ref':'quote', 'predicate':None, 'severity':'HARD', 'suggestion':'Check source'}]}, snapshot())
    assert report['status'] == 'UNVERIFIED'


def test_d17_legacy_output_cannot_be_upgraded():
    with pytest.raises(ValueError):
        validate_assessment({'symbol':'BTCUSDT', 'action':'BUY', 'summary':'buy', 'counter_evidence':[], 'invalidation':'down'}, snapshot())


def test_d02_payload_uses_assessment_schema_and_preserves_snapshot():
    config={'model':'test', 'protocol':'openai_chat', 'base_url':'http://127.0.0.1', 'max_output_tokens':1000}
    _, body=model_client.payload(config, {'analysis_snapshot':snapshot()})
    assert 'DecisionProposal' in body['messages'][0]['content']
    assert json.loads(body['messages'][1]['content'])['analysis_snapshot']['market']['quote']['price']=='100'
import asyncio
from trading_system.server.model_service import ModelService
from trading_system.server.store import Store
from trading_system.server import model_repository

@pytest.fixture
def svc(tmp_path, monkeypatch):
    store=Store(f"sqlite:///{tmp_path / 'assessment.sqlite'}");store.initialize()
    service=ModelService(store,tmp_path/'key')
    monkeypatch.setattr(service, '_clean_context', lambda symbol, context: context)
    for name in ['default','review']:
        with store.transaction() as conn:
            model_repository.write_profile(conn,name,dict(model_repository.DEFAULT,id=name,name=name,model='fixture',enabled=True,local_mode=True,base_url='http://127.0.0.1',protocol='openai_chat'))
    yield service
    asyncio.run(service.close());store.close()


def run(svc, monkeypatch, outputs, mode='single', policy='unanimous'):
    calls=[]
    async def request(*args):
        calls.append(args[-1]); value=outputs.pop(0)
        if isinstance(value, Exception): raise value
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}], 'usage':{'prompt_tokens':3,'completion_tokens':4}}
    monkeypatch.setattr(model_client,'request',request)
    ids=['default'] if mode=='single' else ['default','review']
    context={'analysis_snapshot':snapshot()}
    context['analysis_snapshot']['quality']={'status':'READY','missing_required':[]}
    result=asyncio.run(svc.analyze('BTCUSDT',mode,ids,{p:0 for p in ids},'run',context,decision_policy=policy))
    return result,calls


def test_d09_error_not_hold(svc,monkeypatch):
    result,calls=run(svc,monkeypatch,[TimeoutError()])
    assert result['decision']['assessment_status']=='MODEL_ERROR'
    assert result['decision']['proposal'] is None and result['decision']['action'] is None
    assert len(calls)==1


def test_d10_single_and_replay(svc,monkeypatch):
    result,calls=run(svc,monkeypatch,[candidate()])
    assert result['decision']['assessment_status']=='DECIDED'
    assert len(calls)==1
    with svc.store.transaction() as conn: receipt=model_repository.read_run(conn,'run')
    assert receipt['requests'][0]['body']==calls[0]
    assert receipt['requests'][0]['input_hash']


def test_d13_soft_review_bounded_adjudication(svc,monkeypatch):
    soft={'schema_revision':'review_v1','issues':[{'category':'PREFERENCE','evidence_ref':'quote','predicate':None,'severity':'SOFT','suggestion':'Weak trend'}]}
    result,calls=run(svc,monkeypatch,[candidate(),soft,candidate()], 'consensus','evidence_review')
    assert result['decision']['assessment_status']=='DECIDED'
    assert result['decision']['proposal']['target']['value']=='5'
    assert len(calls)==3 and result['usage']['output_tokens']==12
    assert len(result['decision']['adjudication_history'])==1


def test_d15_required_review_failure(svc,monkeypatch):
    result,calls=run(svc,monkeypatch,[candidate(),TimeoutError()], 'consensus','evidence_review')
    assert result['decision']['assessment_status']=='REVIEW_REQUIRED'
    assert result['decision']['proposal'] is None


def test_d20_invalid_repair_once(svc,monkeypatch):
    bad=candidate();bad['proposal']['exit_plan']['stop_price']='110'
    result,calls=run(svc,monkeypatch,[bad,bad,candidate()])
    assert len(calls)==2 and result['decision']['assessment_status']=='INVALID_PLAN'
@pytest.mark.parametrize('issue,status', [
    ({'category':'FACT','evidence_ref':'quote','predicate':{'field':'market.quote.price','operator':'GT','value':'90'},'severity':'HARD','suggestion':'Price violates thesis'},'INVALID_PLAN'),
    ({'category':'FACT','evidence_ref':'quote','predicate':None,'severity':'HARD','suggestion':'Unknown fact'},'REVIEW_REQUIRED'),
])
def test_d14_review_verified_and_unknown(svc,monkeypatch,issue,status):
    result,calls=run(svc,monkeypatch,[candidate(),{'schema_revision':'review_v1','issues':[issue]}], 'consensus','evidence_review')
    assert result['decision']['assessment_status']==status
    assert result['decision']['proposal'] is None and len(calls)==2


def test_d09_d20_malformed_output_accounted_without_retry(svc,monkeypatch):
    result,calls=run(svc,monkeypatch,[{'bad':'format'}])
    assert result['decision']['assessment_status']=='MODEL_ERROR'
    assert result['usage']['output_tokens']==4


def test_required_readiness_no_paid_call(svc,monkeypatch):
    snap=snapshot();snap['quality']={'status':'DATA_BLOCKED','missing_required':['candles:15m']}
    async def fail(*args): raise AssertionError('No provider calls allowed')
    monkeypatch.setattr(model_client,'request',fail)
    result=asyncio.run(svc.analyze('BTCUSDT','single',['default'],{'default':0},'blocked',{'analysis_snapshot':snap}))
    assert result['decision']['assessment_status']=='DATA_BLOCKED'
    assert result['decision']['budget']['calls']==0


def test_d22_single_observation(svc,monkeypatch):
    value=candidate();value['proposal']=None;value['summary']='No economic advantage'
    result,calls=run(svc,monkeypatch,[value])
    assert result['decision']['assessment_status']=='DECIDED'
    assert result['decision']['action']=='HOLD' and result['decision']['proposal'] is None

def test_retry_replays_original_snapshot_and_policy_conflicts(svc,monkeypatch):
    result,calls=run(svc,monkeypatch,[candidate()])
    snap=snapshot();snap['snapshot_hash']='different';snap['market']['quote']['price']='101'
    repeated=asyncio.run(svc.analyze('BTCUSDT','single',['default'],{'default':0},'run',{'analysis_snapshot':snap}))
    assert repeated==result and len(calls)==1
    from trading_system.server.errors import ApiError
    with pytest.raises(ApiError):
        asyncio.run(svc.analyze('BTCUSDT','single',['default'],{'default':0},'run',{'analysis_snapshot':snap},decision_policy='evidence_review'))

def test_d20_repair_and_adjudication_share_extra_call_budget(svc,monkeypatch):
    bad=candidate();bad['proposal']['exit_plan']['stop_price']='110'
    soft={'schema_revision':'review_v1','issues':[{'category':'PREFERENCE','evidence_ref':'quote','predicate':None,'severity':'SOFT','suggestion':'Weak trend'}]}
    result,calls=run(svc,monkeypatch,[bad,candidate(),soft,candidate()], 'consensus','evidence_review')
    assert len(calls)==3
    assert result['decision']['reason_code']=='MODEL_BUDGET_EXHAUSTED'
    assert result['decision']['proposal'] is None

def test_d13_soft_review_cannot_increase_target(svc,monkeypatch):
    soft={'schema_revision':'review_v1','issues':[{'category':'PREFERENCE','evidence_ref':'quote','predicate':None,'severity':'SOFT','suggestion':'Weak trend'}]}
    larger=candidate();larger['proposal']['target']['value']='10'
    result,calls=run(svc,monkeypatch,[candidate(),soft,larger], 'consensus','evidence_review')
    assert result['decision']['assessment_status']=='INVALID_PLAN'
    assert result['decision']['proposal'] is None
