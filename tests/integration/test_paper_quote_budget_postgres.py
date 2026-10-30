"""Real funded quote admission through the public PAPER execution boundary."""
from contextlib import contextmanager
from decimal import Decimal as D
from dataclasses import replace
import pytest
from integration.test_quote_current_boundary_postgres import database, isolated_postgres, admitted, gate
from integration.test_paper_market_matcher import venue, event, submit
from integration.test_program_commands_postgres import lease
from trading_system.execution.program_commands import ProgramCommandRepository


def execution_case(database, venue, tmp_path):
    from trading_system.engine.paper import PaperEngine
    from trading_system.engine.paper_market import PaperMatchingModel
    from trading_system.runtime.execution_owner import ExecutionOwner
    c=admitted(database)
    with c.owner.transaction(c.scope) as tx:
        repo=ProgramCommandRepository(tx);c.write=repo.write(c.cid);c.request=repo.request(c.cid)
    c.gate=gate(c);c.lease=lease(c);c.permit=None
    def denied(*args):raise ValueError('legacy cannot authorize quote')
    c.engine=PaperEngine(c.scope,dsn=venue.runtime_dsn,event_dsn=venue.event_dsn,
        authority_guard=denied,owner_strategy_id=c.write.owner_strategy_id,
        quantity_unit='BASE',price_currency='USDT',clock=lambda:c.c.facts.inputs.clock,
        matching_model=PaperMatchingModel(model_id='ISOLATED_QUOTE_DEPTH_V1',quote_fee_rate='0'))
    @contextmanager
    def claim(shard):
        import fcntl
        with (tmp_path/(c.scope.venue_account_id+'.lock')).open('a') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:yield
            finally:fcntl.flock(f,fcntl.LOCK_UN)
    c.owner=ExecutionOwner(c.lease,journal_path=tmp_path/'quote.ndjson',engine=c.engine,
        claim=claim,clock=lambda:c.c.facts.inputs.clock)
    return c


@pytest.mark.parametrize('price,expected_base,expected_gross',[('50000','.08','4000'),('60000','.066','3960')])
def test_native_quote_request_executes_actual_depth_without_original_base_quantity(database,venue,tmp_path,price,expected_base,expected_gross):
    from trading_system.engine.paper_market import PaperMatcher,DepthLevel
    c=execution_case(database,venue,tmp_path)
    snapshot=replace(event(c,ask=price,quantity='.1'),bids=(DepthLevel(price='49999',quantity='.1'),))
    PaperMatcher(c.engine).apply_market_event(snapshot)
    submit(c)
    report=c.engine.query_creation(c.cid)
    assert dict(report.request.form_parameters())['quoteOrderQty']=='4000'
    assert 'quantity' not in dict(report.request.form_parameters())
    assert report.record_kind=='PAPER_QUOTE_REPORT_V1'
    assert report.actual_base==D(expected_base)
    assert report.gross_quote==D(expected_gross)
    assert report.remaining_quote==4000-D(expected_gross)
    assert sum((f.quantity*f.price for f in report.fills),D(0))==report.gross_quote
