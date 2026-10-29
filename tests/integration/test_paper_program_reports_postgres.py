"""Actual financial command -> restricted matching -> independent source verifier."""
from dataclasses import replace
from decimal import Decimal as D

from integration.test_paper_market_matcher import isolated_postgres,database,venue,case,submit,event
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,domain_payload
from trading_system.engine.paper_market import PaperMatcher
from trading_system.engine.nautilus.native_events import NativeEventBridge
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.storage.models import scope_id


def actual_verifier(c,path):
    from trading_system.execution.paper_program_reports import PaperProgramReportVerifier
    codec=VenueIdCodec(c.scope)
    return PaperProgramReportVerifier(c.service.uow,
        c.scope,engine=c.engine,archive_bridge=NativeEventBridge(codec,path)),codec


def test_real_partial_fill_normalizes_native_facts_and_preserves_management_duties(database,venue,tmp_path):
    c=case(database,venue,tmp_path);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    observer,codec=actual_verifier(c,tmp_path/'archive')
    try:result=observer.verify(c.write.command_id)
    finally:codec.close()
    assert result.source_verified and result.synthetic and not result.execution_authorized
    assert len(result.fills)==1 and result.fills[0].fill.quantity==t.Quantity(value=D('.4'),unit=c.bundle.intent.unit.native_unit)
    assert result.fills[0].fill.price.value==D('81490')
    assert result.fills[0].economics.reported_gross.amount==D('32596')
    assert result.orders[0].filled_quantity.value==D('.4') and result.orders[0].leaves_quantity.value==D('.6')
    assert result.raw_refs and 'PROTECTION_RECHECK_REQUIRED' in result.retained_duties
    assert not result.funds_release_authorized
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


def test_consumed_but_not_observed_never_becomes_not_sent_or_releases_funds(database,venue,tmp_path):
    c=case(database,venue,tmp_path)
    permit=c.gate.mark_may_have_sent(c.write.command_id,c.lease)
    c.gate.consume(permit,c.lease)
    observer,codec=actual_verifier(c,tmp_path/'archive')
    try:result=observer.verify(c.write.command_id)
    finally:codec.close()
    assert not result.source_verified and not result.fills and not result.orders
    assert result.status=='NOT_OBSERVED' and 'QUERY_ORIGINAL_IDS' in result.retained_duties
    assert not result.funds_release_authorized


def test_changed_venue_request_is_archived_but_never_adopted(database,venue,tmp_path):
    c=case(database,venue,tmp_path);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    original=c.engine.query_creation
    def corrupt(command_id):
        report=original(command_id)
        changed=replace(report.request,fields=tuple(replace(f,value='MARKET') if f.name=='type' else f for f in report.request.fields))
        return replace(report,request=changed)
    c.engine.query_creation=corrupt
    observer,codec=actual_verifier(c,tmp_path/'archive')
    try:result=observer.verify(c.write.command_id)
    finally:codec.close()
    assert not result.source_verified and not result.fills and result.discrepancies and result.raw_refs
    assert not result.funds_release_authorized


def test_source_with_excess_competing_allocation_cannot_qualify_an_own_fill(database,venue,tmp_path):
    c=case(database,venue,tmp_path);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    original=c.engine.query_market_evidence
    def corrupt(command_id):
        report=original(command_id)
        rival=replace(report.consumptions[0],scope=replace(c.scope,venue_account_id='rival'),
            client_id='rival-client',fill_id='rival-fill',quantity=D('.4'))
        changed=replace(report,consumptions=(*report.consumptions,rival))
        body=dict(command_id=changed.command_id,request_hash=changed.request_hash,
            inputs=[domain_payload(i) for i in changed.inputs],consumptions=[domain_payload(i) for i in changed.consumptions])
        return replace(changed,evidence=replace(changed.evidence,hash=canonical_hash(body)))
    c.engine.query_market_evidence=corrupt
    observer,codec=actual_verifier(c,tmp_path/'archive')
    try:result=observer.verify(c.write.command_id)
    finally:codec.close()
    assert not result.source_verified and 'PAPER_LIQUIDITY_OVERSPENT' in result.discrepancies
    assert result.raw_refs and not result.funds_release_authorized
