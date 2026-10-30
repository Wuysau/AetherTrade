"""Actual current quote mark/consume, prepared financial book and foreign holds."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from integration.test_program_commands_postgres import database as base_database,isolated_postgres,lease,attach
from integration.test_program_financial_admission import real_case
from integration.test_quote_financial_kernel_postgres import quote_case,digest
from integration.test_quote_program_admission_postgres import public
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.execution.program_commands import ProgramCommandBoundary
from trading_system.storage.uow import UnitOfWork
from trading_system.storage.models import scope_id


@pytest.fixture(scope='module')
def database(base_database):
    from integration.support_ip_03 import ROOT
    import psycopg
    path=ROOT/'migrations/0015_quote_program_dispatch.sql'
    # The first RED predates this additive migration. The explicit installation
    # assertion and actual positive SQL mark below prevent an absent-DDL green.
    if path.exists():
        with psycopg.connect(base_database) as db:db.execute(path.read_text(encoding='utf-8'))
    return base_database


def gate(c,*,bounds=True):
    import psycopg
    def connect():
        db=psycopg.connect(c.dsn);db.execute('SET ROLE ledger_program_dispatch_verifier');db.commit();return db
    return ProgramCommandBoundary(UnitOfWork(connect,permit_ttl_ms=5000),c.scope,
        load_clock=lambda _:c.c.facts.inputs.clock,load_lease=lambda _:lease(c),
        load_current_facts=c.qload,load_qualification=c.qqualify,
        load_execution_bounds=c.bounds if bounds else None)


def admitted(database):
    c=quote_case(database);c.receipt=public(c).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan)
    c.cid=c.receipt.attachment.command_ids[0]
    return c


def test_exact_new_quote_context_migration_is_installed_without_old_history_rewrite(database):
    import psycopg
    with psycopg.connect(database) as db:
        assert db.execute("SELECT to_regprocedure('ledger.quote_dispatch_context_current(text,jsonb,jsonb)') IS NOT NULL").fetchone()==(True,)
        body=db.execute("SELECT prosrc FROM pg_proc WHERE oid='ledger.check_order_write_v2()'::regprocedure").fetchone()[0]
        assert '2026-10-28' in body and '2026-10-30' not in body


def test_original_scalar_v3_permit_and_unknown_identity_survive_additive_quote_migration(database):
    from integration.test_program_commands_postgres import boundary
    from trading_system.execution.program_commands import ProgramCommandRepository
    from trading_system.recovery.original_creation import read_original_creation
    c=real_case(database);w,r,_=c.service._transaction(lambda tx:attach(c,tx))
    g=boundary(c);permit=g.mark_may_have_sent(w.command_id,lease(c))
    assert g.consume(permit,lease(c))==r
    with c.owner.transaction(c.scope) as tx:
        ProgramCommandRepository(tx).record_unknown(w.command_id)
        original=read_original_creation(tx,w.command_id)
        assert original.current_state=='UNKNOWN' and original.request==r and not original.can_resubmit


def test_actual_quote_mark_commits_and_consumes_exact_original_request_once(database):
    import psycopg
    c=admitted(database);g=gate(c);permit=g.mark_may_have_sent(c.cid,lease(c))
    request=g.consume(permit,lease(c))
    assert dict(request.form_parameters())['quoteOrderQty']=='4000'
    with pytest.raises((ValueError,psycopg.errors.UniqueViolation)):g.consume(permit,lease(c))
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(1,)
        assert tx.reservations.for_intent(c.qrecord.intent_id)[0].amount==4000


def test_real_foreign_financial_write_counts_cash_once_without_becoming_quote_quantity(database):
    c=quote_case(database)
    other=real_case(database,collateral_group_id=c.q.terms.collateral_group_id)
    _,_,foreign=other.service._transaction(lambda tx:attach(other,tx))
    foreign_amount=sum((p.held for p in foreign.reservations),D(0));original=c.qload
    def load(*args):
        f=original(*args);cap=(t.Money(currency='USDT',amount=foreign_amount+D(4000)),)
        return replace(f,group_capacity=cap,strategy_capacity=cap)
    c.qload=load
    receipt=public(c).admit(c.qrecord.proposal,c.qrecord.context,c.q.plan);c.cid=receipt.attachment.command_ids[0]
    g=gate(c);permit=g.mark_may_have_sent(c.cid,lease(c));request=g.consume(permit,lease(c))
    assert dict(request.form_parameters())['quoteOrderQty']=='4000'
    assert c.q.allocation.scope_state.working.value==0
    with c.owner.transaction(c.scope) as tx:
        context=tx.execute('SELECT context FROM ledger.program_permit_consumptions WHERE scope_id=%s AND command_id=%s',
            (scope_id(c.scope),c.cid)).fetchone()[0]
        assert context['source_book_hash']==digest(tx.projections.book(c.scope))
        actual=context['assessment']['book']['reservations']
        assert len(actual)==len(foreign.reservations)==1
        assert actual[0]['reservation']['reservation_id']==foreign.reservations[0].reservation.reservation_id
        assert canonical_hash(context['assessment']['book'])!=context['source_book_hash']


@pytest.mark.parametrize('fault',['missing_reader','changed_bound','changed_raw_book'])
def test_quote_current_gaps_never_renew_or_consume_the_marked_request(database,fault):
    c=admitted(database)
    if fault=='missing_reader':
        with pytest.raises(ValueError,match='QUOTE_CURRENT_ENFORCEMENT_REQUIRED'):gate(c,bounds=False).mark_may_have_sent(c.cid,lease(c))
    else:
        g=gate(c);permit=g.mark_may_have_sent(c.cid,lease(c))
        if fault=='changed_bound':g.readers['load_execution_bounds']=lambda *args:replace(c.q.facts.target_facts.bounds,model_hash='f'*64)
        else:
            with c.owner.transaction(c.scope) as tx:
                book=tx.projections.book(c.scope);tx.projections.save_book(replace(book,sequence=book.sequence+1),book.sequence)
        with pytest.raises(ValueError):g.consume(permit,lease(c))
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)


@pytest.mark.parametrize('fault',['source_book_hash','financial_book_hash','execution_bounds_hash','unknown_kind','downgrade'])
def test_protected_sql_cannot_bypass_new_quote_context_binding(database,fault):
    import psycopg
    from trading_system.contracts.canonical import canonical_json
    from trading_system.execution.program_commands import ProgramCommandRepository
    c=admitted(database);g=gate(c)
    with pytest.raises(psycopg.errors.CheckViolation):
        with g.uow.transaction(c.scope) as tx:
            repo=ProgramCommandRepository(tx,**g.readers);w=repo.write(c.cid)
            context,*_=repo._current(w,lease(c))
            if fault in ('source_book_hash','financial_book_hash','execution_bounds_hash'):context[fault]='f'*64
            else:
                context['assessment']['record_kind']='UNKNOWN_QUOTE_ARCHIVE'
                if fault=='downgrade':
                    context['assessment']['record_kind']='PROGRAM_FINANCIAL_ASSESSMENT_ARCHIVE'
                    context['assessment']['contract_version']='2026-10-29'
                    context['assessment']['capabilities']=context['assessment']['risk_facts']['target_facts']['capabilities']
                    del context['source_book_hash']
                context['assessment_hash']=canonical_hash(context['assessment'])
            tx.execute('SELECT ledger.mark_program_may(%s,%s,%s::jsonb,%s,%s,%s)',
                (scope_id(c.scope),c.cid,canonical_json(context),'b'*64,5000,tx.transaction_id))
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_dispatch_marks WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)
