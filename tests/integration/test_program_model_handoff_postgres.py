"""Actual GenericService handoff from local fake model research; no venue IO."""
import asyncio,json
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import pytest
from integration.test_generic_program_admission_postgres import database,isolated_postgres,real_case,public_service
from trading_system.contracts import types as t
from trading_system.contracts.canonical import domain_payload,utc_timestamp
from trading_system.contracts.order_codec import VersionedOrderCodec
from trading_system.server.program_model_context import build_program_context,parse_program_assessment,admit_program_assessment
from trading_system.server.program_model_service import authorize_research
from trading_system.server.model_service import ModelService
from trading_system.server.model_api import ProfileAnalysis
from trading_system.server.store import Store
from trading_system.server import model_repository,model_client,model_service,program_model_context,market_parse
from trading_system.storage.models import scope_id


def linked_case(database,monkeypatch):
    from unit.support_program_financial import financial_case
    financial_case()
    import test_program_risk_assessment as risk_fixture
    original=risk_fixture.inputs
    def console_v3(product):
        p,r,c,k=original(product);scope=replace(c.scope,instrument_uid='console-paper:BTCUSDT')
        raw=domain_payload(p);raw.update(contract_version='2026-10-29',instrument_ref=scope.instrument_uid,evidence_refs=['quote'])
        raw['program'].update(contract_version='2026-10-29',instrument_ref=scope.instrument_uid)
        r=replace(r,scope=scope);c=replace(c,scope=scope)
        k={**k,'scope':scope,'profile':replace(k['profile'],scope=scope),'expected_instrument_ref':scope.instrument_uid,
           'expected_capabilities_hash':VersionedOrderCodec().hash('order-capability-snapshot',c)}
        return VersionedOrderCodec().decode('order-proposal',raw),r,c,k
    with monkeypatch.context() as patch:
        patch.setattr(risk_fixture,'inputs',console_v3)
        c=real_case(database)
    from server import test_analysis_snapshot as analysis_fixture
    current=utc_timestamp(c.c.facts.inputs.clock.utc_now)
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return current
    monkeypatch.setattr(model_service,'datetime',Clock);monkeypatch.setattr(program_model_context,'datetime',Clock)
    monkeypatch.setattr(market_parse,'utc_now',lambda:current)
    monkeypatch.setattr(analysis_fixture,'NOW',current);monkeypatch.setattr(analysis_fixture,'MS',int(current.timestamp()*1000))
    class Feed(analysis_fixture.Feed):
        def __init__(self):
            super().__init__()
            for row in self.rows:row.update(open='81480',high='81500',low='81460',close='81490')
        def snapshot(self):
            result=super().snapshot();result['quotes'][0]['price']='81490';return result
    class ContextStore(analysis_fixture.Store):
        def analysis_context(self,*args):
            result=super().analysis_context(*args)
            cash=sum((m.amount for m in c.c.book.balances if m.currency=='USDT'),Decimal(0))
            result['account'].update(cash=str(cash),equity=str(cash));return result
    from trading_system.server.analysis_snapshot import build_analysis_context
    snapshot=asyncio.run(build_analysis_context(ContextStore(),Feed(),'BTCUSDT',30,analysis_fixture.PROFILE))['analysis_snapshot']
    frame=build_program_context(snapshot,decision_context=c.record.context,capabilities=c.c.facts.capabilities,
        rules=c.c.facts.rules,strategy_revision=c.record.context.strategy_revision,
        expected_build_digest=c.c.facts.expected_build_digest,
        financial_market_snapshot=c.c.facts.market,financial_book=c.c.book)
    value=dict(schema_revision='program_assessment_v1',context_hash=frame.context_hash,summary='Synthetic typed model candidate',
        proposal=VersionedOrderCodec().encode('order-proposal',c.record.proposal),evidence_claims=[],watch_conditions=[],next_review_at=None)
    return c,frame,value


def research(tmp_path,monkeypatch,frame,value,*,authorized=True):
    store=Store(f"sqlite:///{tmp_path/'model.sqlite'}");store.initialize()
    svc=ModelService(store,tmp_path/'model.key',client=object())
    with store.transaction() as conn:
        model_repository.write_profile(conn,'default',dict(model_repository.DEFAULT,id='default',name='fixture',model='fixture',
            enabled=True,local_mode=True,base_url='http://127.0.0.1',protocol='openai_chat'))
    calls=[]
    async def local_provider(*args):
        calls.append(args[-1]);assert authorized,'Unqualified automatic branch called provider'
        return {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}
    monkeypatch.setattr(model_client,'request',local_provider)
    witness=None
    if authorized:
        session=store.setup('researcher','isolated-fake-only','100000',24)
        request=ProfileAnalysis(symbol='BTCUSDT',analysis_mode='single',model_profile_ids=['default'],
            expected_revisions={'default':0},acknowledge_cost=True,decision_schema='assessment_v1',
            task_profile={k:v for k,v in frame.payload['analysis_snapshot']['task_profile'].items() if k!='strategy_revision'})
        witness=authorize_research(svc,request,session['token'],'isolated-model-handoff',frame)
    try:
        result=asyncio.run(svc.analyze('BTCUSDT','single',['default'],{'default':0},'isolated-model-handoff',frame,research_request=witness))
    finally:store.close()
    return result,calls


def test_local_fake_complete_v3_research_admits_through_actual_same_financial_kernel(database,monkeypatch,tmp_path):
    c,frame,value=linked_case(database,monkeypatch)
    result,calls=research(tmp_path,monkeypatch,frame,value)
    assert len(calls)==1 and result['decision']['qualification']=='UNQUALIFIED'
    assert frame.payload['order_context']['analysis_snapshot_hash']!=frame.payload['order_context']['financial_market_snapshot_ref']['hash']
    assessment=parse_program_assessment({k:result['decision'][k] for k in value},frame)
    receipt=admit_program_assessment(assessment,frame,admission_service=public_service(c))
    assert not receipt.execution_authorized and len(receipt.attachment.command_ids)==1
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(receipt.intent_id) is not None
        assert len(tx.reservations.for_intent(receipt.intent_id))==1
        assert tx.execute('SELECT count(*) FROM ledger.program_request_bindings WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute("SELECT count(*) FROM ledger.outbox WHERE scope_id=%s AND kind='PROGRAM_COMMAND_READY'",(scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


@pytest.mark.parametrize('changed',['capability','rule','market','book'])
def test_changed_current_model_facts_replan_without_new_financial_bundle(database,monkeypatch,changed):
    from trading_system.server.errors import ApiError
    c,frame,value=linked_case(database,monkeypatch);assessment=parse_program_assessment(value,frame)
    service=public_service(c)
    if changed in ('capability','rule'):
        original=c.ingress.load_inputs
        def altered(*args):
            facts=original(*args)
            return replace(facts,capabilities=replace(facts.capabilities,snapshot_id='changed')) if changed=='capability' else replace(facts,rules=replace(facts.rules,revision=facts.rules.revision+1))
        c.ingress.load_inputs=altered
    elif changed=='book':
        with c.owner.transaction(c.scope) as tx:
            book=tx.projections.book(c.scope)
            tx.projections.save_book(replace(book,sequence=book.sequence+1),book.sequence)
    else:
        original=service.load_inputs
        def altered(*args):
            facts=original(*args);market=facts.archive.risk_facts.market
            from trading_system.market.snapshots import snapshot_content_hash
            market=replace(market,quote=replace(market.quote,last=replace(market.quote.last,value=Decimal('81491'))))
            market=replace(market,ref=replace(market.ref,hash=snapshot_content_hash(market)))
            return replace(facts,archive=replace(facts.archive,risk_facts=replace(facts.archive.risk_facts,market=market)))
        service.load_inputs=altered
    with pytest.raises(ApiError,match='REPLAN_REQUIRED'):
        admit_program_assessment(assessment,frame,admission_service=service)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
        assert tx.reservations.for_intent(c.record.intent_id)==()
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


def test_unqualified_automatic_context_has_zero_fake_and_paid_provider_calls(database,monkeypatch,tmp_path):
    c,frame,value=linked_case(database,monkeypatch)
    result,calls=research(tmp_path,monkeypatch,frame,value,authorized=False)
    assert calls==[] and result['decision']['assessment_status']=='CAPABILITY_BLOCKED'
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
