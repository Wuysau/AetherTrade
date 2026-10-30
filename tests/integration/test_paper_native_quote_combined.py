"""Actual native/quote/BASE rivals share depth and authenticate complete sources."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from integration.test_paper_quote_budget_postgres import (
    isolated_postgres,database,base_database,venue,quote_venue,execution_case,
)
from integration.test_paper_market_matcher import event,submit
from integration.test_program_financial_admission import real_case
from integration.test_paper_market_matcher_v2 import advanced_case,reference
from integration.test_paper_native_parent_postgres import native_verifier
from integration.test_paper_program_v2_reports_postgres import observe
from trading_system.engine.paper_market import PaperMatcher,DepthLevel
from trading_system.engine.paper_market_v2 import provision_paper_market_v2
from trading_system.execution.paper_quote_reports import verify_quote_report


@pytest.fixture(scope='module')
def combined_venue(database,quote_venue):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(quote_venue.runtime_dsn)['dbname']})
    provision_paper_market_v2(admin,quote_venue)
    return quote_venue


def lower_limit(proposal):
    n=proposal.program.nodes[0]
    n=replace(n,order=replace(n.order,price=replace(n.order.price,value='50000')))
    return replace(proposal,program=replace(proposal.program,nodes=(n,)))


@pytest.mark.parametrize('quote_first',[False,True])
def test_actual_native_quote_and_v2_base_authenticate_one_shared_book(database,combined_venue,tmp_path,quote_first):
    q=execution_case(database,combined_venue,tmp_path)
    # Native TWAP remains a MARKET program. Its independently admitted model
    # envelope overlaps the quote model at50000; maximum90000 and the complete
    # original gross reservation stay intact. No LIMIT variant is fabricated.
    prepared=real_case(database,instrument_uid=q.scope.instrument_uid,template='TWAP',stop='40000',
        profile_transform=lambda profile:replace(profile,execution_price_envelope=replace(
            profile.execution_price_envelope,minimum='50000')))
    prepared.c.facts=replace(prepared.c.facts,node_bounds=tuple(replace(bound,
        minimum_price=replace(bound.minimum_price,value=D('50000'))) for bound in prepared.c.facts.node_bounds))
    n=advanced_case(database,combined_venue,tmp_path,prepared_case=prepared)
    snapshot=replace(event(q),asks=(DepthLevel(price='50000',quantity='.3'),),bids=())
    matcher=PaperMatcher(q.engine)
    if quote_first:
        matcher.apply_market_event(snapshot);submit(q);submit(n)
        # Accepted native parent needs a later input. This reference carries
        # remaining depth; it cannot replenish the .08 already used by quote.
        matcher.apply_market_event(reference(q,2,'LAST','50000'))
    else:
        submit(n);matcher.apply_market_event(snapshot);submit(q)
    b=advanced_case(database,combined_venue,tmp_path,market=q.scope.instrument_uid,
        quantity='.02',target='.02',stop='40000',plan_proposal_transform=lower_limit)
    submit(b)
    quote=q.engine.query_creation(q.cid);native=n.engine.query_creation(n.write.command_id)
    base=b.engine.query_creation(b.write.command_id)
    assert quote.actual_base==D('.08') and quote.gross_quote==4000 and quote.remaining_quote==0
    assert native.pool.executed_quantity==D('.2') and native.pool.remaining_pool_quantity==D('.8')
    assert len(native.orders)==1 and native.fills[0].quantity==D('.2')
    assert not n.request.assigned_ids.members and base.filled_quantity==D('.02')
    with q.engine._connect() as db:
        assert db.execute("SELECT available FROM public.paper_market_levels WHERE market_key=%s AND side='ASK'",
            (snapshot.market_key,)).fetchone()['available']==0
        assert db.execute('SELECT count(*) AS count FROM public.paper_engine_commands WHERE scope_key=%s',
            (n.engine._scope_key,)).fetchone()['count']==1
    assert verify_quote_report(q.engine,quote,boundary=q.gate).discrepancies==()
    assert observe(b,tmp_path/'base-source').source_verified
    source=n.engine.query_market_evidence(n.write.command_id)
    assert sum((a.quantity for a in source.consumptions),D(0))==D('.3')
    verifier,codec=native_verifier(n,tmp_path/'native-source')
    try:
        result=verifier.verify(n.write.command_id)
        assert result.source_verified,result.discrepancies
        assert not result.discrepancies and result.status=='VERIFIED'
    finally:codec.close()
