from dataclasses import replace
from decimal import Decimal as D
import pytest
from integration.test_paper_quote_budget_postgres import database,base_database,isolated_postgres,quote_venue,venue,execution_case
from integration.test_paper_market_matcher import submit,event
from integration.test_paper_market_matcher_v2 import advanced_case,reference,market_order
from integration.test_paper_program_v2_reports_postgres import observe
from trading_system.engine.paper_market import PaperMatcher,DepthLevel
from trading_system.engine.paper_market_v2 import provision_paper_market_v2
from trading_system.execution.paper_quote_reports import verify_quote_report

@pytest.fixture(scope='module')
def mixed_venue(database,quote_venue):
    from psycopg.conninfo import conninfo_to_dict,make_conninfo
    admin=make_conninfo(**{**conninfo_to_dict(database),'dbname':conninfo_to_dict(quote_venue.runtime_dsn)['dbname']})
    provision_paper_market_v2(admin,quote_venue)
    return quote_venue

@pytest.mark.parametrize('delta',[False,True])
def test_real_quote_then_v2_shared_event_authenticates(database,mixed_venue,tmp_path,delta):
    q=execution_case(database,mixed_venue,tmp_path)
    matcher=PaperMatcher(q.engine)
    snapshot=replace(event(q),asks=(DepthLevel(price='50000',quantity='.1'),),bids=())
    matcher.apply_market_event(snapshot)
    if delta:
        matcher.apply_market_event(replace(snapshot,event_id='delta-'+q.scope.instrument_uid,sequence=2,kind='DEPTH_DELTA',asks=(),bids=()))
    submit(q)
    assert q.engine.query_creation(q.cid).actual_base==D('.08')
    assert verify_quote_report(q.engine,q.engine.query_creation(q.cid),boundary=q.gate).discrepancies==()
    def lower_limit(proposal):
        n=proposal.program.nodes[0]
        n=replace(n,order=replace(n.order,price=replace(n.order.price,value='50000')))
        return replace(proposal,program=replace(proposal.program,nodes=(n,)))
    b=advanced_case(database,mixed_venue,tmp_path,market=q.scope.instrument_uid,
        quantity='.02',target='.02',stop='40000',plan_proposal_transform=lower_limit)
    submit(b)
    assert b.engine.query_creation(b.write.command_id).filled_quantity==D('.02')
    source=b.engine.query_market_evidence(b.write.command_id)
    assert sum((a.quantity for a in source.consumptions),D(0))==D('.1')
    result=observe(b,tmp_path/'v2-source')
    assert result.source_verified,result.discrepancies

def test_real_quote_after_reference_preserves_depth_source(database,mixed_venue,tmp_path):
    q=execution_case(database,mixed_venue,tmp_path)
    matcher=PaperMatcher(q.engine)
    matcher.apply_market_event(replace(event(q),asks=(DepthLevel(price='50000',quantity='.1'),),bids=()))
    matcher.apply_market_event(reference(q,2,'LAST','50000'))
    submit(q)
    report=q.engine.query_creation(q.cid)
    assert report.actual_base==D('.08')
    assert verify_quote_report(q.engine,report,boundary=q.gate).discrepancies==()
