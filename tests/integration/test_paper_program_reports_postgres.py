"""Actual financial command -> restricted matching -> independent source verifier."""
from dataclasses import replace
from decimal import Decimal as D,localcontext
from hashlib import sha256
import json
from pathlib import Path

import pytest

from integration.test_paper_market_matcher import isolated_postgres,database,venue,case,submit,event
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash,canonical_json,domain_payload
from trading_system.engine.paper_market import PaperMatcher
from trading_system.engine.nautilus.native_events import NativeEventBridge
from trading_system.engine.nautilus.id_codec import VenueIdCodec
from trading_system.storage.models import scope_id


def actual_verifier(c,path):
    from trading_system.execution.paper_program_reports import PaperProgramReportVerifier
    codec=VenueIdCodec(c.scope)
    return PaperProgramReportVerifier(c.service.uow,
        c.scope,engine=c.engine,archive_bridge=NativeEventBridge(codec,path)),codec


def reseal_report(report):
    body=dict(request=domain_payload(report.request),orders=[domain_payload(o) for o in report.orders],
        fills=[domain_payload(f) for f in report.fills],accepted_contexts=[domain_payload(a) for a in report.accepted_contexts],
        dispositions=[domain_payload(d) for d in report.dispositions])
    return replace(report,evidence=replace(report.evidence,hash=canonical_hash(body)))


def reseal_market(market):
    body=dict(command_id=market.command_id,request_hash=market.request_hash,
        inputs=[domain_payload(i) for i in market.inputs],consumptions=[domain_payload(i) for i in market.consumptions])
    return replace(market,evidence=replace(market.evidence,hash=canonical_hash(body)))


def change_observed_fill(fill,**changes):
    """Corrupt an observation AFTER genuine execution; never inject venue fills."""
    raw=json.loads(fill.raw_payload_json)
    for field in ('quantity','gross_notional'):
        if field in changes:raw[field]=format(changes[field],'f')
    if 'fees' in changes:raw['fees']=[domain_payload(f) for f in changes['fees']]
    text=canonical_json(raw)
    return replace(fill,**changes,raw_payload_json=text,
        evidence=replace(fill.evidence,hash=sha256(text.encode('utf-8')).hexdigest()))


def test_real_partial_fill_normalizes_native_facts_and_preserves_management_duties(database,venue,tmp_path):
    c=case(database,venue,tmp_path);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    observer,codec=actual_verifier(c,tmp_path/'archive')
    try:result=observer.verify(c.write.command_id)
    finally:codec.close()
    assert result.source_verified and result.synthetic and not result.execution_authorized,result.discrepancies
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


def test_real_query_preserves_persisted_revision_and_sql_event_hash_domain(database,venue,tmp_path):
    import psycopg
    c=case(database,venue,tmp_path);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    report=c.engine.query_creation(c.write.command_id)
    with psycopg.connect(venue.runtime_dsn) as db:
        revision=db.execute('SELECT revision FROM public.paper_engine_orders WHERE scope_key=%s AND client_id=%s',
            (c.engine._scope_key,report.orders[0].client_id)).fetchone()[0]
        raw,stored_hash=db.execute('SELECT payload::text,payload_hash FROM public.paper_engine_events '
            'WHERE scope_key=%s AND event_id=%s',(c.engine._scope_key,report.fills[0].fill_id)).fetchone()
    assert report.orders[0].lifecycle_revision==revision
    assert report.fills[0].raw_payload_json==raw
    assert report.fills[0].evidence.hash==stored_hash==sha256(raw.encode('utf-8')).hexdigest()


def test_real_query_remaining_and_fill_sum_are_independent_of_ambient_precision(database,venue,tmp_path):
    c=case(database,venue,tmp_path);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.000011'))
    with localcontext() as arithmetic:
        arithmetic.prec=3
        report=c.engine.query_creation(c.write.command_id)
    assert report.filled_quantity==D('.000011') and report.remaining_quantity==D('.999989')


def test_fractional_nonfill_price_retains_sql_raw_and_can_be_verified(database,venue,tmp_path):
    import psycopg
    c=case(database,venue,tmp_path,tif='IOC')
    PaperMatcher(c.engine).apply_market_event(event(c,ask='81489.5',quantity='.4'))
    submit(c)
    report=c.engine.query_creation(c.write.command_id)
    disposition=report.dispositions[-1]
    with psycopg.connect(venue.runtime_dsn) as db:
        raw,stored_hash=db.execute('SELECT payload::text,payload_hash FROM public.paper_engine_events '
            'WHERE scope_key=%s AND event_id=%s',(c.engine._scope_key,disposition.event_id)).fetchone()
    assert disposition.raw_payload_json==raw and disposition.evidence.hash==stored_hash
    assert sha256(raw.encode('utf-8')).hexdigest()==stored_hash
    observer,codec=actual_verifier(c,tmp_path/'archive')
    try:result=observer.verify(c.write.command_id)
    finally:codec.close()
    assert result.source_verified,result.discrepancies
    assert result.orders[0].lifecycle_state==t.OrderLifecycle.EXPIRED
    assert result.fills[0].fill.price.value==D('81489.5')


@pytest.mark.parametrize('fault',['gross','fees','cumulative','remaining','overfill','missing_fees'])
def test_observed_economic_discrepancies_preserve_uncapped_native_facts(database,venue,tmp_path,fault):
    from trading_system.contracts.order_lifecycle_v3 import FeeV3
    from trading_system.risk.program_exposure import FeeBound
    options={}
    if fault=='missing_fees':
        options=dict(quote_fee_rate='.001',node_fees=(FeeBound(amount=t.Money(amount=D('100'),currency='USDT'),debit='EXTERNAL_BALANCE'),))
    c=case(database,venue,tmp_path,**options);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='2' if fault=='overfill' else '.4'))
    actual_report=c.engine.query_creation(c.write.command_id);actual_market=c.engine.query_market_evidence(c.write.command_id)
    fill=actual_report.fills[0];order=actual_report.orders[0]
    expected=''
    if fault=='gross':fill=change_observed_fill(fill,gross_notional=D('1'));expected='GROSS_NOTIONAL_MISMATCH'
    if fault=='fees':
        fill=change_observed_fill(fill,fees=(FeeV3(asset='USDT',amount='200',receipt_effect='NO_RECEIPT_DEBIT',position_effect='NO_POSITION_DEBIT'),))
        expected='PROGRAM_FEE_ENVELOPE_EXCEEDED'
    if fault=='cumulative':order=replace(order,filled_quantity=D('.5'));expected='ORDER_CUMULATIVE_FILL_MISMATCH'
    if fault=='remaining':order=replace(order,remaining_quantity=D('.9'));expected='ORDER_REMAINING_QUANTITY_MISMATCH'
    if fault=='missing_fees':fill=change_observed_fill(fill,fees=());expected='FILL_FEES_UNKNOWN'
    if fault=='overfill':
        fill=change_observed_fill(fill,quantity=D('1.1'),gross_notional=D('89639'))
        order=replace(order,filled_quantity=D('1.1'))
        actual_market=reseal_market(replace(actual_market,consumptions=(replace(actual_market.consumptions[0],quantity=D('1.1')),)))
        expected='ORDER_OVERFILLED'
    report=reseal_report(replace(actual_report,orders=(order,),fills=(fill,)))
    c.engine.query_creation=lambda _:report;c.engine.query_market_evidence=lambda _:actual_market
    observer,codec=actual_verifier(c,tmp_path/'archive')
    try:result=observer.verify(c.write.command_id)
    finally:codec.close()
    assert result.source_verified and result.status=='DISCREPANCY',result.discrepancies
    assert expected in result.discrepancies
    assert result.fills[0].fill.quantity.value==fill.quantity and result.fills[0].fill.price.value==fill.price
    assert result.fills[0].economics.reported_gross.amount==fill.gross_notional
    assert result.orders[0].filled_quantity.value==order.filled_quantity and result.orders[0].leaves_quantity.value==order.remaining_quantity
    assert any(Path(ref.id).read_bytes()==fill.raw_payload_json.encode('utf-8') for ref in result.raw_refs)
    if fault=='overfill':assert 'PROGRAM_GROSS_ENVELOPE_EXCEEDED' in result.discrepancies
    if fault=='missing_fees':assert not result.fills[0].fees_known and result.fills[0].fill.fees==()
    assert not result.funds_release_authorized and 'PROTECTION_RECHECK_REQUIRED' in result.retained_duties


@pytest.mark.parametrize('fault',['raw_hash','raw_type','fill_clock','accepted_scope','accepted_multiplier','unhealthy_read_clock'])
def test_real_source_substitution_is_blocked_with_raw_observations_retained(database,venue,tmp_path,fault):
    c=case(database,venue,tmp_path);submit(c)
    PaperMatcher(c.engine).apply_market_event(event(c,quantity='.4'))
    report=c.engine.query_creation(c.write.command_id)
    if fault=='raw_hash':report=replace(report,fills=(replace(report.fills[0],raw_payload_json='{"corrupt":true}'),))
    if fault=='raw_type':report=replace(report,fills=(replace(report.fills[0],raw_payload_json=None),))
    if fault=='fill_clock':report=replace(report,fills=(replace(report.fills[0],occurred_at='2026-10-28T09:59:59Z'),))
    if fault.startswith('accepted_'):
        accepted=report.accepted_contexts[0]
        terms=replace(accepted.terms,scope=replace(c.scope,venue_account_id='foreign')) if fault=='accepted_scope' else replace(accepted.terms,multiplier='.001')
        report=replace(report,accepted_contexts=(replace(accepted,terms=terms),))
    report=reseal_report(report);c.engine.query_creation=lambda _:report
    if fault=='unhealthy_read_clock':c.engine._clock=lambda:replace(c.c.facts.inputs.clock,clock_health='UNHEALTHY')
    observer,codec=actual_verifier(c,tmp_path/'archive')
    try:result=observer.verify(c.write.command_id)
    finally:codec.close()
    assert not result.source_verified and not result.fills and result.discrepancies
    assert result.raw_refs and all(Path(ref.id).is_file() for ref in result.raw_refs)
    assert not result.funds_release_authorized
