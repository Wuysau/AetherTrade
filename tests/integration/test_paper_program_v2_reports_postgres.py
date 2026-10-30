"""Genuine protected V2 venue observations -> new trusted verifier (no posting)."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

from integration.test_paper_market_matcher_v2 import isolated_postgres,database,venue,venue_v2,advanced_case,reference,market_order
from integration.test_paper_market_matcher import submit,event
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload,utc_timestamp
from trading_system.contracts.order_program import TriggerSpec
from trading_system.engine.paper_market import PaperMatcher,PaperMarketFill,DepthLevel
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.engine.nautilus.native_events import NativeEventBridge
from trading_system.storage.models import scope_id


def verifier(c,path):
    from trading_system.execution.paper_program_v2_reports import PaperProgramV2ReportVerifier
    codec=VenueIdCodec(c.scope)
    return PaperProgramV2ReportVerifier(c.service.uow,c.scope,engine=c.engine,archive_bridge=NativeEventBridge(codec,path)),codec


def observe(c,path):
    service,codec=verifier(c,path)
    try:return service.verify(c.write.command_id)
    finally:codec.close()


def reseal_report(report):
    body=dict(request=domain_payload(report.request),orders=[domain_payload(o) for o in report.orders],
        fills=[domain_payload(f) for f in report.fills],accepted_contexts=[domain_payload(c) for c in report.accepted_contexts],
        dispositions=[domain_payload(d) for d in report.dispositions],contract_version=report.contract_version,
        record_kind=report.record_kind,native_parent=domain_payload(report.native_parent) if report.native_parent is not None else None)
    return replace(report,evidence=replace(report.evidence,hash=canonical_hash(body)))


def reseal_market(report):
    body=dict(command_id=report.command_id,request_hash=report.request_hash,inputs=[domain_payload(i) for i in report.inputs],
        consumptions=[domain_payload(i) for i in report.consumptions],preventions=[domain_payload(i) for i in report.preventions],
        contract_version=report.contract_version,record_kind=report.record_kind)
    return replace(report,evidence=replace(report.evidence,hash=canonical_hash(body)))


def trailing_case(database,venue_v2,tmp_path):
    def trailing(order):
        return replace(market_order(order),trigger=TriggerSpec(kind='TRAILING',purpose='STOP_LOSS',source='LAST',price=None,
            activation_price=None,offset='100',offset_unit='BIPS',price_protect=False))
    c=advanced_case(database,venue_v2,tmp_path,tif='NONE',order_transform=trailing,actual='1',target='0',quantity='1',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine);matcher.apply_market_event(event(c,quantity='1'))
    matcher.apply_market_event(reference(c,2,'LAST','82000'));matcher.apply_market_event(reference(c,3,'LAST','83000'))
    matcher.apply_market_event(reference(c,4,'LAST','82500'));matcher.apply_market_event(reference(c,5,'LAST','82100'))
    return c


def test_actual_v2_market_partial_preserves_native_economics_and_explicit_v2_provenance(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,tif='NONE',order_transform=market_order)
    PaperMatcher(c.engine).apply_market_event(event(c,ask='81500',quantity='.4'));submit(c)
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert result.contract_version=='2026-10-30' and result.record_kind=='PAPER_PROGRAM_VERIFICATION_V2'
    fill,=result.fills
    assert type(fill.raw) is PaperMarketFill and fill.source_v2.record_kind=='PAPER_FILL_SOURCE_V2'
    assert fill.fill.quantity.value==D('.4') and fill.fill.price.value==D('81500')
    assert fill.economics.reported_gross.amount==D('32600') and result.orders[0].leaves_quantity.value==0
    assert not result.execution_authorized and not result.funds_release_authorized
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


def test_actual_trailing_observation_survives_creation_window_and_keeps_every_causal_input(database,venue_v2,tmp_path):
    c=trailing_case(database,venue_v2,tmp_path);clock=c.c.facts.inputs.clock
    later=replace(clock,utc_now=(utc_timestamp(clock.utc_now)+timedelta(seconds=10)).isoformat(),monotonic_ns=clock.monotonic_ns+10**10)
    c.engine._clock=lambda:later
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert result.fills[0].fill.quantity.value==1 and result.fills[0].fill.price.value==D('81489')
    assert {i.event.sequence for i in result.market_evidence.inputs}=={1,2,3,4,5}
    assert result.fills[0].source_v2.causal_input_ids


def test_missing_nonfinal_trailing_dependency_is_archived_but_never_qualified(database,venue_v2,tmp_path):
    c=trailing_case(database,venue_v2,tmp_path);original=c.engine.query_market_evidence
    def corrupt(command_id):
        report=original(command_id)
        changed=replace(report,inputs=tuple(i for i in report.inputs if i.event.sequence!=4))
        body=dict(command_id=changed.command_id,request_hash=changed.request_hash,inputs=[domain_payload(i) for i in changed.inputs],
            consumptions=[domain_payload(i) for i in changed.consumptions],preventions=[domain_payload(i) for i in changed.preventions],
            contract_version=changed.contract_version,record_kind=changed.record_kind)
        return replace(changed,evidence=replace(changed.evidence,hash=canonical_hash(body)))
    c.engine.query_market_evidence=corrupt
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and not result.fills and result.raw_refs
    assert 'PAPER_V2_CAUSAL_INPUT_SET_CHANGED' in result.discrepancies


def test_actual_native_oco_uses_real_parent_members_and_no_fictitious_child_command(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,template='OCO',actual='1',target='0',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);matcher=PaperMatcher(c.engine)
    matcher.apply_market_event(replace(event(c,ask='83001',quantity='1'),bids=(DepthLevel(price='83000',quantity='.4'),)))
    matcher.apply_market_event(reference(c,2,'LAST','83000'))
    result=observe(c,tmp_path/'raw')
    assert result.source_verified,result.discrepancies
    assert len(result.orders)==2 and result.raw_report.native_parent.parent_client_id==c.request.assigned_ids.parent_client_id
    assert result.fills[0].raw.node_id=='above' and result.fills[0].fill.quantity.value==D('.4')
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s AND sequence=1',(scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)


def test_changed_native_parent_venue_identity_cannot_be_authenticated_by_a_resealed_dto(database,venue_v2,tmp_path):
    c=advanced_case(database,venue_v2,tmp_path,template='OCO',actual='1',target='0',side='SELL',
        financial_capacity=(t.Money(currency='USDT',amount=D('1000000')),t.Money(currency='BTC',amount=D('10'))),
        financial_buffer=(t.Money(currency='USDT',amount=D(0)),t.Money(currency='BTC',amount=D(0))))
    submit(c);report=c.engine.query_creation(c.write.command_id)
    changed=reseal_report(replace(report,native_parent=replace(report.native_parent,venue_parent_id='foreign-parent')))
    c.engine.query_creation=lambda _:changed
    result=observe(c,tmp_path/'raw')
    assert not result.source_verified and result.raw_refs and not result.fills
    assert 'PAPER_V2_NATIVE_PARENT_SOURCE_CHANGED' in result.discrepancies


def test_missing_real_competing_account_allocation_cannot_be_hidden_by_resealing_evidence(database,venue_v2,tmp_path):
    market='actual-v2-shared-'+uuid4().hex
    a=advanced_case(database,venue_v2,tmp_path,market=market);b=advanced_case(database,venue_v2,tmp_path,market=market)
    submit(a);submit(b);PaperMatcher(a.engine).apply_market_event(event(a,quantity='1.4'))
    assert a.engine.query_creation(a.write.command_id).filled_quantity==1
    assert b.engine.query_creation(b.write.command_id).filled_quantity==D('.4')
    original=a.engine.query_market_evidence
    def omit(command):
        report=original(command)
        return reseal_market(replace(report,consumptions=tuple(c for c in report.consumptions if c.scope==a.scope)))
    a.engine.query_market_evidence=omit
    result=observe(a,tmp_path/'raw')
    assert not result.source_verified and result.raw_refs and not result.fills
    assert 'PAPER_V2_ALL_RIVAL_ALLOCATION_SET_CHANGED' in result.discrepancies
