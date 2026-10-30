"""Actual matched native fills enter the existing ledger, never invented cash.

This cohort is the raw-fact transaction of the accounting plan. Financial
posting/ownership remain explicitly unresolved until their trusted readers exist.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D

import pytest

from integration.test_program_fill_source_approval import isolated_postgres,command_database,database,venue,setup_source
from integration.test_paper_market_matcher import event
from integration.test_paper_program_reports_postgres import change_observed_fill,reseal_report
from trading_system.contracts.canonical import utc_timestamp
from trading_system.engine.paper_market import PaperMatcher
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork


def setup_accounting(database,venue,tmp_path):
    from trading_system.execution.program_accounting import ProgramPaperAccountingService
    c,source,codec=setup_source(database,venue,tmp_path)
    service=ProgramPaperAccountingService(c.service.uow,c.scope,source_service=source,trade_id_scope='INSTRUMENT')
    return c,service,codec


def test_real_fill_evidence_notice_and_accounting_duties_are_atomic(database,venue,tmp_path):
    c,service,codec=setup_accounting(database,venue,tmp_path)
    with c.service.uow.transaction(c.scope) as tx:
        before=tx.projections.book(c.scope)
        held=tx.execute('SELECT record_id,payload FROM ledger.reservations WHERE scope_id=%s ORDER BY record_id',
            (scope_id(c.scope),)).fetchall()
    try:result=service.apply(c.write.command_id)
    finally:codec.close()
    assert result.status=='RAW_RECORDED_ECONOMICS_BLOCKED' and len(result.fills)==1 and result.fills[0].recorded
    assert 'CURRENT_ACCOUNTING_FACTS_REQUIRED' in result.missing and result.retained_duties
    assert not result.execution_authorized and not result.funds_release_authorized and not result.balance_credit_authorized
    with c.service.uow.transaction(c.scope) as tx:
        key=scope_id(c.scope)
        fill=tx.execute('SELECT quantity,price,payload,xmin::text,transaction_id FROM ledger.fills WHERE scope_id=%s',(key,)).fetchone()
        assert fill[0]==D('.4') and fill[1]==D('81490')
        assert fill[2]['quantity']['unit']==c.bundle.intent.unit.native_unit
        evidence=tx.execute('SELECT xmin::text,transaction_id FROM ledger.venue_events WHERE scope_id=%s',(key,)).fetchall()
        notices=tx.execute("SELECT xmin::text,transaction_id FROM ledger.audit_events WHERE scope_id=%s "
            "AND (record_id LIKE 'ip12:fill-notice:%%' OR record_id LIKE 'program-fill-accounting:%%')",(key,)).fetchall()
        locks=tx.risk_locks.active(c.scope)
        assert len(evidence)==1 and len(notices)==2 and len(locks)==1
        assert all(row==(fill[3],fill[4]) for row in (*evidence,*notices))
        assert locks[0].cause=='PROGRAM_ACCOUNTING_UNRESOLVED'
        assert tx.projections.book(c.scope)==before
        assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',(key,)).fetchone()==(0,)
        assert tx.execute('SELECT record_id,payload FROM ledger.reservations WHERE scope_id=%s ORDER BY record_id',
            (key,)).fetchall()==held


def test_replay_with_later_archive_delivery_does_not_duplicate_native_fill(database,venue,tmp_path):
    c,service,codec=setup_accounting(database,venue,tmp_path)
    try:
        first=service.apply(c.write.command_id)
        old=c.c.facts.inputs.clock
        c.c.facts=replace(c.c.facts,inputs=replace(c.c.facts.inputs,clock=replace(old,
            utc_now=(utc_timestamp(old.utc_now)+timedelta(seconds=1)).isoformat(),monotonic_ns=old.monotonic_ns+1_000_000_000)))
        second=service.apply(c.write.command_id)
    finally:codec.close()
    assert first.fills[0].recorded and not second.fills[0].recorded
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
        assert len(tx.risk_locks.active(c.scope))==1
        assert tx.execute("SELECT count(*) FROM ledger.audit_events WHERE scope_id=%s AND record_id LIKE 'program-fill-accounting:%%'",
            (scope_id(c.scope),)).fetchone()==(1,)


def test_additional_partial_fill_uses_original_ids_without_releasing_reservations(database,venue,tmp_path):
    c,service,codec=setup_accounting(database,venue,tmp_path)
    with c.service.uow.transaction(c.scope) as tx:
        before=tx.execute('SELECT record_id,payload FROM ledger.reservations WHERE scope_id=%s ORDER BY record_id',
            (scope_id(c.scope),)).fetchall()
    try:
        service.apply(c.write.command_id)
        PaperMatcher(c.engine).apply_market_event(event(c,sequence=2,quantity='.2',kind='DEPTH_DELTA'))
        result=service.apply(c.write.command_id)
    finally:codec.close()
    assert len(result.fills)==2 and sum(r.recorded for r in result.fills)==1
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT sum(quantity),count(*) FROM ledger.fills WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(D('.6'),2)
        assert tx.execute('SELECT record_id,payload FROM ledger.reservations WHERE scope_id=%s ORDER BY record_id',
            (scope_id(c.scope),)).fetchall()==before
        assert len(tx.risk_locks.active(c.scope))==2


def test_owner_commit_failure_rolls_back_facts_but_preserves_committed_source(database,venue,tmp_path):
    from trading_system.execution.program_accounting import ProgramPaperAccountingService
    c,service,codec=setup_accounting(database,venue,tmp_path)
    class FailCommit:
        def __init__(self,db):self.db=db
        def __getattr__(self,name):return getattr(self.db,name)
        def commit(self):raise RuntimeError('injected accounting commit failure')
    broken=ProgramPaperAccountingService(UnitOfWork(lambda:FailCommit(service.uow.connection_factory())),
        c.scope,source_service=service.source_service,trade_id_scope='INSTRUMENT')
    try:
        with pytest.raises(RuntimeError,match='injected accounting commit failure'):broken.apply(c.write.command_id)
        with c.service.uow.transaction(c.scope) as tx:
            for table in ('fills','venue_events','risk_locks'):
                assert tx.execute('SELECT count(*) FROM ledger.'+table+' WHERE scope_id=%s',
                    (scope_id(c.scope),)).fetchone()==(0,)
            assert tx.execute('SELECT count(*) FROM ledger.program_fill_source_approvals WHERE scope_id=%s',
                (scope_id(c.scope),)).fetchone()==(1,)
        assert service.apply(c.write.command_id).fills[0].recorded
    finally:codec.close()


def test_source_linked_economic_discrepancy_retains_actual_quantity_and_no_cash(database,venue,tmp_path):
    c,service,codec=setup_accounting(database,venue,tmp_path)
    original=c.engine.query_creation
    def changed(command_id):
        report=original(command_id)
        return reseal_report(replace(report,fills=(change_observed_fill(report.fills[0],gross_notional=D('1')),)))
    c.engine.query_creation=changed
    try:result=service.apply(c.write.command_id)
    finally:codec.close()
    assert result.fills[0].recorded and 'GROSS_NOTIONAL_MISMATCH' in result.missing
    with c.service.uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT quantity,price FROM ledger.fills WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(D('.4'),D('81490'))
        assert tx.execute('SELECT count(*) FROM ledger.cashflows WHERE scope_id=%s',
            (scope_id(c.scope),)).fetchone()==(0,)


@pytest.mark.parametrize('operation',('cash','book'))
def test_legacy_refresh_cannot_ignore_source_approved_generic_execution(database,venue,tmp_path,operation):
    from trading_system.server.paper_runtime import UnifiedPaperRuntime
    from trading_system.server.errors import ApiError
    from trading_system.contracts.canonical import canonical_json
    c,service,codec=setup_accounting(database,venue,tmp_path)
    try:service.apply(c.write.command_id)
    finally:codec.close()
    # Exercise the existing runtime's actual cash/book functions against this
    # isolated ledger. The fixture is explicit virtual capital, not an account.
    runtime=object.__new__(UnifiedPaperRuntime)
    runtime.clock=lambda:c.c.facts.inputs.clock
    with c.service.uow.transaction(c.scope) as tx:
        before=tx.projections.book(c.scope)
        seed=next(b.amount for b in before.balances if b.currency=='USDT')
        tx.execute("INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) "
            "VALUES(%s,'console:seed',1,%s::jsonb,%s)",
            (scope_id(c.scope),canonical_json(dict(initial_cash=format(seed,'f'),source='EXPLICIT_ISOLATED_VIRTUAL_CAPITAL')),tx.transaction_id))
    with c.service.uow.transaction(c.scope) as tx:
        with pytest.raises(ApiError,match='PROGRAM_ACCOUNTING_RECONCILIATION_REQUIRED'):
            if operation=='cash':runtime._cash(tx)
            else:runtime._book(tx,c.scope,refresh=True)
        assert tx.projections.book(c.scope)==before


@pytest.mark.parametrize('operation',('cash','book'))
def test_missing_foreign_source_binding_cannot_hide_unreconciled_execution(database,venue,tmp_path,operation):
    import psycopg
    from integration.test_program_financial_admission import real_case
    from trading_system.server.paper_runtime import UnifiedPaperRuntime
    from trading_system.server.errors import ApiError
    from trading_system.contracts.canonical import canonical_json
    source,service,codec=setup_accounting(database,venue,tmp_path)
    try:service.apply(source.write.command_id)
    finally:codec.close()
    with source.service.uow.transaction(source.scope) as tx:group=tx.group_id
    receiver=real_case(database,collateral_group_id=group)
    runtime=object.__new__(UnifiedPaperRuntime)
    runtime.clock=lambda:receiver.c.facts.inputs.clock
    with receiver.service.uow.transaction(receiver.scope) as tx:
        before=tx.projections.book(receiver.scope)
        seed=next(b.amount for b in before.balances if b.currency=='USDT')
        tx.execute("INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) "
            "VALUES(%s,'console:seed',1,%s::jsonb,%s)",
            (scope_id(receiver.scope),canonical_json(dict(initial_cash=format(seed,'f'),source='EXPLICIT_ISOLATED_VIRTUAL_CAPITAL')),tx.transaction_id))
    # Normal writes must remain blocked. The following admin-only corruption
    # fixture models incomplete historical restore, not runtime permissions.
    with pytest.raises(psycopg.errors.CheckViolation,match='append-only'):
        with psycopg.connect(database) as db:
            db.execute('DELETE FROM ledger.account_bindings WHERE environment=%s AND account_id=%s AND product=%s',
                (str(source.scope.environment),source.scope.venue_account_id,str(source.scope.product)))
    with psycopg.connect(database) as db:
        db.execute('ALTER TABLE ledger.account_bindings DISABLE TRIGGER binding_immutable')
        binding=db.execute('DELETE FROM ledger.account_bindings WHERE environment=%s AND account_id=%s AND product=%s '
            'RETURNING collateral_group_id,payload',
            (str(source.scope.environment),source.scope.venue_account_id,str(source.scope.product))).fetchone()
        db.execute('ALTER TABLE ledger.account_bindings ENABLE TRIGGER binding_immutable')
        assert db.execute("SELECT tgenabled FROM pg_trigger WHERE tgrelid='ledger.account_bindings'::regclass "
            "AND tgname='binding_immutable'").fetchone()==('O',)
    try:
        with receiver.service.uow.transaction(receiver.scope) as tx:
            with pytest.raises(ApiError,match='PROGRAM_ACCOUNTING_RECONCILIATION_REQUIRED'):
                if operation=='cash':runtime._cash(tx)
                else:runtime._book(tx,receiver.scope,refresh=True)
            assert tx.projections.book(receiver.scope)==before
    finally:
        # Restore this test-owned disposable binding even on the intended RED.
        with psycopg.connect(database) as db:
            db.execute('INSERT INTO ledger.account_bindings(environment,account_id,product,collateral_group_id,payload) '
                'VALUES(%s,%s,%s,%s,%s::jsonb)',(str(source.scope.environment),source.scope.venue_account_id,
                str(source.scope.product),binding[0],None if binding[1] is None else canonical_json(binding[1])))
