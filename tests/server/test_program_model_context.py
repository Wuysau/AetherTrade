"""Frozen model context/parser component tests. No provider/account calls."""
import copy,json
from dataclasses import replace
from datetime import timedelta
import pytest
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.server.program_model_context import build_program_context,parse_program_assessment
from trading_system.server import market_parse
from test_analysis_snapshot import build,NOW
from intents.test_order_program_compiler import fixture


def case(monkeypatch):
    monkeypatch.setattr(market_parse,'utc_now',lambda:NOW)
    snapshot=build();p,r,c,k=fixture()
    scope=replace(c.scope,instrument_uid=snapshot['instrument_ref'])
    r=replace(r,scope=scope,as_of=NOW.isoformat(),valid_until=(NOW+timedelta(hours=1)).isoformat())
    c=replace(c,scope=scope,as_of=r.as_of,valid_until=r.valid_until)
    context=t.DecisionContext(identity_id='user',environment=t.Environment.PAPER,deployment_id='deployment',
        authorization_id='grant',policy_revision=1,ownership_epoch=1,scopes=(scope,),strategy_revision=1,
        decision_run_id='run',snapshot_ref=t.SnapshotRef(id=snapshot['snapshot_id'],hash=snapshot['snapshot_hash'],
            environment=t.Environment.PAPER,source='SERVER_ANALYSIS_SNAPSHOT',visible_at=NOW.isoformat()))
    frame=build_program_context(snapshot,decision_context=context,capabilities=c,rules=r,
        strategy_revision=1,expected_build_digest=c.adapter_build_digest)
    raw=domain_payload(p);until=(NOW+timedelta(seconds=30)).isoformat()
    raw.update(contract_version='2026-10-29',instrument_ref=snapshot['instrument_ref'],valid_until=until,evidence_refs=['quote'])
    raw['program'].update(contract_version='2026-10-29',instrument_ref=snapshot['instrument_ref'],submit_valid_until=until)
    raw['exit_plan']['review_due_at']=until
    value=dict(schema_revision='program_assessment_v1',context_hash=frame.context_hash,summary='Typed candidate',
        proposal=raw,evidence_claims=[],watch_conditions=[],next_review_at=None)
    return snapshot,context,c,r,frame,value


def test_frozen_context_preserves_old_snapshot_hash_and_does_not_mint_qualification(monkeypatch):
    snapshot,context,c,r,frame,_=case(monkeypatch);old=copy.deepcopy(snapshot)
    assert frame.payload['analysis_snapshot']==old
    assert frame.payload['order_context']['qualification_status']=='UNQUALIFIED'
    assert 'HOST_ADMISSION_NOT_REGISTERED' in frame.payload['order_context']['blockers']
    assert frame.context_hash==canonical_hash(frame.payload)
    snapshot['portfolio']['account']['cash']='999'
    assert frame.payload['analysis_snapshot']==old
    assert frame.execution_authorized is False


def test_exact_v3_parser_keeps_every_parameter_and_context_hash(monkeypatch):
    *_,frame,value=case(monkeypatch)
    result=parse_program_assessment(value,frame,now=NOW.isoformat())
    assert VersionedOrderCodec().encode('order-proposal',result.proposal)==value['proposal']
    assert result.proposal.program.nodes[0].order.price.value=='81490.00'
    assert result.proposal.program.nodes[0].order.tif.kind=='GTC'
    assert result.execution_authorized is False


@pytest.mark.parametrize('change',[
    lambda v:v.update(context_hash='0'*64),
    lambda v:v['proposal'].update(contract_version='2026-10-30'),
    lambda v:v['proposal']['program']['nodes'][0]['order'].pop('tif'),
    lambda v:v['proposal'].update(instrument_ref='other'),
    lambda v:v['proposal']['program'].update(capability_snapshot_ref='model-invented'),
])
def test_hash_mismatch_unknown_source_and_lost_fields_are_rejected(monkeypatch,change):
    *_,frame,value=case(monkeypatch);change(value)
    with pytest.raises(ValueError):parse_program_assessment(value,frame,now=NOW.isoformat())


def test_provider_explicit_program_context_uses_full_v3_schema_without_upgrading_old_prompt(monkeypatch):
    from trading_system.server import model_client
    *_,frame,value=case(monkeypatch)
    config=dict(model='fixture',protocol='openai_chat',base_url='http://127.0.0.1',max_output_tokens=4096)
    _,body=model_client.payload(config,dict(frame.payload,context_hash=frame.context_hash))
    instructions=body['messages'][0]['content']
    assert 'program_assessment_v1' in instructions and '2026-10-29' in instructions
    assert 'iceberg_quantity' in instructions and 'price_protect' in instructions
    assert json.loads(body['messages'][1]['content'])['order_context']['capabilities_hash']==frame.payload['order_context']['capabilities_hash']
    _,legacy=model_client.payload(config,{'quote':{'symbol':'BTCUSDT'}})
    assert 'program_assessment_v1' not in legacy['messages'][0]['content']

def test_explicit_model_service_uses_durable_existing_journal_and_never_returns_execution_approval(monkeypatch,tmp_path):
    import asyncio
    from datetime import datetime
    from trading_system.server import model_client,model_repository as repository,model_service,program_model_context
    from trading_system.server.store import Store
    from trading_system.server.model_service import ModelService
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(model_service,'datetime',Clock)
    monkeypatch.setattr(program_model_context,'datetime',Clock)
    *_,frame,value=case(monkeypatch)
    store=Store(f"sqlite:///{tmp_path/'program-model.sqlite'}");store.initialize()
    service=ModelService(store,tmp_path/'key',client=object())
    with store.transaction() as conn:
        repository.write_profile(conn,'default',dict(repository.DEFAULT,id='default',name='fixture',model='fixture',enabled=True,local_mode=True,base_url='http://127.0.0.1',protocol='openai_chat'))
    from trading_system.server.program_model_service import authorize_research
    from trading_system.server.model_api import ProfileAnalysis
    session=store.setup('researcher','isolated-research-only','100000',24)
    request_input=ProfileAnalysis(symbol='BTCUSDT',analysis_mode='single',model_profile_ids=['default'],
        expected_revisions={'default':0},acknowledge_cost=True,decision_schema='assessment_v1',
        task_profile={k:v for k,v in frame.payload['analysis_snapshot']['task_profile'].items() if k!='strategy_revision'})
    authorized=authorize_research(service,request_input,session['token'],'program-analysis-run-1',frame)
    calls=[]
    async def request(*args):
        calls.append(args[-1])
        with store.transaction() as conn:
            saved=repository.read_run(conn,'program-analysis-run-1')
            assert saved['status']=='IN_FLIGHT' and len(saved['requests'])==1
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}],'usage':{'prompt_tokens':10,'completion_tokens':20}}
    monkeypatch.setattr(model_client,'request',request)
    try:
        result=asyncio.run(service.analyze('BTCUSDT','single',['default'],{'default':0},'program-analysis-run-1',frame,research_request=authorized))
        assert len(calls)==1
        assert result['decision']['assessment_status']=='UNQUALIFIED'
        assert result['decision']['qualification']=='UNQUALIFIED'
        assert result['decision']['proposal']==value['proposal']
        assert result['decision']['execution_authorized'] is False
        assert result['decision']['snapshot_hash']==frame.payload['analysis_snapshot']['snapshot_hash']
        assert asyncio.run(service.analyze('BTCUSDT','single',['default'],{'default':0},'program-analysis-run-1',frame,research_request=authorized))==result
        assert len(calls)==1
    finally:store.close()


def test_default_unqualified_program_context_stops_before_provider_and_payload_flag_is_not_authority(monkeypatch,tmp_path):
    import asyncio
    from datetime import datetime
    from trading_system.server import model_service,model_client
    from trading_system.server.model_service import ModelService
    from trading_system.server.store import Store
    from trading_system.server.errors import ApiError
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(model_service,'datetime',Clock)
    *_,frame,value=case(monkeypatch)
    store=Store(f"sqlite:///{tmp_path/'blocked-model.sqlite'}");store.initialize()
    service=ModelService(store,tmp_path/'key',client=object())
    async def forbidden(*args):raise AssertionError('Provider must not run for default UNQUALIFIED')
    monkeypatch.setattr(model_client,'request',forbidden)
    try:
        result=asyncio.run(service.analyze('BTCUSDT','single',['default'],{'default':0},'program-blocked-1',frame))
        assert result['decision']['assessment_status']=='CAPABILITY_BLOCKED'
        assert result['decision']['qualification']=='UNQUALIFIED' and result['usage'] is None
        with pytest.raises(ApiError,match='EXPLICIT_RESEARCH_AUTHORIZATION_REQUIRED'):
            asyncio.run(service.analyze('BTCUSDT','single',['default'],{'default':0},'program-forged-1',frame,research_request={'approved':True}))
    finally:store.close()

def test_unregistered_admission_host_never_turns_model_context_hash_into_authority(monkeypatch):
    from trading_system.server.program_model_context import admit_program_assessment
    from trading_system.server.errors import ApiError
    *_,frame,value=case(monkeypatch)
    assessment=parse_program_assessment(value,frame,now=NOW.isoformat())
    with pytest.raises(ApiError,match='HOST_ADMISSION_NOT_REGISTERED'):
        admit_program_assessment(assessment,frame)

def test_server_context_uses_actual_existing_four_layer_catalog_intersection(monkeypatch):
    from trading_system.accounts.capabilities import CapabilityCatalog
    from trading_system.server.program_model_context import build_program_context_from_catalog
    from unit.support_ip_04 import scenario
    snapshot,context,c,r,frame,_=case(monkeypatch);old=scenario()
    authorization=replace(old.query.authorization,authorization_id=context.authorization_id,scopes=(c.scope,))
    profile=replace(old.query.platform,scope=c.scope,build_digest=c.adapter_build_digest,
        capabilities=c.capability_ids,valid_from=r.as_of,valid_until=r.valid_until)
    query=t.CapabilityQuery(scope=c.scope,requested=c.capability_ids,authorization=authorization,
        platform=profile,adapter=profile,account=profile)
    catalog=CapabilityCatalog(c.adapter_build_digest,lambda _:None,clock=lambda:NOW)
    actual=build_program_context_from_catalog(snapshot,decision_context=context,capabilities=c,rules=r,
        strategy_revision=1,expected_build_digest=c.adapter_build_digest,
        capability_catalog=catalog,capability_query=query)
    assert actual.payload['order_context']['capability_intersection']['rejection_codes']==['AUTHORIZATION_UNKNOWN_OR_CHANGED']
    assert actual.payload['order_context']['qualification_status']=='UNQUALIFIED'

def test_analysis_and_financial_snapshot_hash_domains_are_separate_with_explicit_missing_link(monkeypatch):
    snapshot,context,c,r,frame,_=case(monkeypatch)
    financial_ref=replace(context.snapshot_ref,id='financial-market',hash='b'*64)
    result=build_program_context(snapshot,decision_context=replace(context,snapshot_ref=financial_ref),
        capabilities=c,rules=r,strategy_revision=1,expected_build_digest=c.adapter_build_digest)
    binding=result.payload['order_context']
    assert binding['analysis_snapshot_hash']==snapshot['snapshot_hash']
    assert binding['financial_market_snapshot_ref']['hash']=='b'*64
    assert 'ANALYSIS_FINANCIAL_LINK_NOT_ESTABLISHED' in binding['blockers']
