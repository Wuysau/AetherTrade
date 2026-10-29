"""Actual committed consumption is necessary but never reusable authority."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import timedelta
import time

import pytest

from integration.test_program_commands_postgres import isolated_postgres,database,attach,boundary,lease
from integration.test_program_financial_admission import real_case
from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp,canonical_hash,domain_payload
from trading_system.storage.models import scope_id


def ready(database,*,consume=True):
    c=real_case(database);c.write,c.request,c.bundle=c.service._transaction(lambda tx:attach(c,tx))
    c.gate=boundary(c);c.lease=lease(c);c.permit=c.gate.mark_may_have_sent(c.write.command_id,c.lease)
    if consume:c.gate.consume(c.permit,c.lease)
    return c


def check(c,*,permit=None):
    from trading_system.execution.program_consumed_boundary import recheck_consumed
    return recheck_consumed(c.gate,c.permit if permit is None else permit,c.lease)


def test_actual_committed_context_rechecks_without_historical_math_or_second_permit(database):
    c=ready(database)
    def forbidden(*args):raise AssertionError('historical compiler/risk replay at last-call barrier')
    c.gate.readers['load_current_facts']=forbidden;c.gate.readers['load_qualification']=forbidden
    result=check(c)
    assert result.request==c.request and result.write.command_id==c.write.command_id and result.write.state=='MAY_HAVE_SENT'
    assert result.clock==c.c.facts.inputs.clock and result.consumption_context_hash and result.source_ref
    assert result.deadline_monotonic_ns>result.clock.monotonic_ns and not result.execution_authorized
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_permit_consumptions WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(1,)
        assert tx.execute('SELECT count(*) FROM ledger.order_writes_v2 WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(2,)
        assert tx.execute('SELECT count(*) FROM ledger.fills WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


@pytest.mark.parametrize('fault',['missing_consumption','token','request_hash','binding_hash','scope','lease','clock','fence','book','lock','unknown',
    'expiry_label','deadline_label','both_window_labels'])
def test_real_post_consumption_changes_never_clear_a_new_side_effect(database,fault):
    c=ready(database,consume=fault!='missing_consumption');permit=c.permit
    if fault=='token':permit=replace(permit,token='forged')
    if fault=='request_hash':permit=replace(permit,request_hash='f'*64)
    if fault=='binding_hash':permit=replace(permit,binding_hash='f'*64)
    if fault=='scope':permit=replace(permit,scope=replace(c.scope,venue_account_id='foreign-account'))
    if fault in ('expiry_label','both_window_labels'):
        permit=replace(permit,expires_at=(utc_timestamp(permit.expires_at)+timedelta(milliseconds=1)).isoformat())
    if fault in ('deadline_label','both_window_labels'):
        permit=replace(permit,deadline_monotonic_ns=permit.deadline_monotonic_ns+1_000_000)
    if fault=='lease':c.gate.readers['load_lease']=lambda _:replace(c.lease,instance_id='new-owner-instance')
    if fault=='clock':
        before=c.c.facts.inputs.clock
        c.gate.readers['load_clock']=lambda _:replace(before,utc_now=(utc_timestamp(before.utc_now)+timedelta(seconds=6)).isoformat(),monotonic_ns=before.monotonic_ns+6*10**9)
    if fault=='fence':
        from trading_system.intents.ingress import revision_key,IngressRevision
        with c.owner.transaction(c.scope) as tx:
            key=revision_key(c.source.context);previous=tx._latest('audit_events',key,IngressRevision)
            tx._append('audit_events',key,replace(previous,sequence=previous.sequence+1,enabled=False),previous.sequence)
    if fault=='book':
        with c.owner.transaction(c.scope) as tx:
            before=tx.projections.book(c.scope)
            tx.projections.save_book(replace(before,sequence=before.sequence+1),before.sequence)
    if fault=='lock':
        with c.owner.transaction(c.scope) as tx:tx.risk_locks.append(t.RiskLock(lock_id='post-consumption-lock',scope=c.scope,
            cause='ORDER_OUTCOME_UNKNOWN',state='ACTIVE',permission=t.RiskPermission.RECOVERY_REQUIRED,
            required_evidence=('ORIGINAL_IDS',),raised_at=c.c.facts.inputs.clock.utc_now),0)
    if fault=='unknown':
        from trading_system.execution.program_commands import ProgramCommandRepository
        with c.owner.transaction(c.scope) as tx:ProgramCommandRepository(tx).record_unknown(c.write.command_id)
    with pytest.raises(ValueError):check(c,permit=permit)


def test_real_sql_permit_expiry_cannot_be_hidden_by_frozen_fixture_clock(database):
    c=ready(database)
    with c.owner.transaction(c.scope) as tx:
        remaining=tx.execute('SELECT extract(epoch FROM expires_at-clock_timestamp()) FROM ledger.program_dispatch_marks '
            'WHERE scope_id=%s AND command_id=%s',(scope_id(c.scope),c.write.command_id)).fetchone()[0]
    time.sleep(max(0,float(remaining))+.05)
    with pytest.raises(ValueError):check(c)


def test_uncommitted_actual_consumption_in_same_physical_transaction_is_rejected(database):
    from trading_system.execution.program_commands import ProgramCommandRepository
    c=ready(database,consume=False);original_uow=c.gate.uow
    with original_uow.transaction(c.scope) as tx:
        ProgramCommandRepository(tx,**c.gate.readers).consume(c.permit,c.lease)
        class BorrowedTransaction:
            @contextmanager
            def transaction(self,scope):
                assert scope==c.scope
                yield tx
        c.gate.uow=BorrowedTransaction()
        try:
            with pytest.raises(ValueError):check(c)
        finally:c.gate.uow=original_uow
