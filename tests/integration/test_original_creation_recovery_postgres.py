"""Original-ID read recovery in a real isolated ledger, without send authority."""
from dataclasses import replace

import pytest

from integration.support_ip_03 import isolated_postgres
from integration.test_program_commands_postgres import database,attach,boundary,lease
from integration.test_program_financial_admission import real_case
from trading_system.contracts import types as t
from trading_system.storage.models import scope_id


def read(c,command_id):
    from trading_system.recovery.original_creation import read_original_creation
    with c.owner.transaction(c.scope) as tx:return read_original_creation(tx,command_id)


def test_original_ready_identity_read_does_not_create_a_case_command_or_permit(database):
    c=real_case(database)
    w,r,b=c.service._transaction(lambda tx:attach(c,tx))
    recovered=read(c,w.command_id)
    assert recovered.initial==w and recovered.request==r and recovered.current_state=='DURABLE_READY'
    assert recovered.can_resubmit is False and recovered.execution_authorized is False
    with c.owner.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.program_dispatch_marks WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)
        assert tx.execute('SELECT count(*) FROM ledger.reconciliation_cases WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()==(0,)


@pytest.mark.parametrize('unknown',[False,True])
def test_original_may_and_unknown_survive_later_reservation_version(database,unknown):
    from trading_system.execution.program_commands import ProgramCommandRepository
    c=real_case(database)
    w,r,b=c.service._transaction(lambda tx:attach(c,tx))
    gate=boundary(c)
    gate.mark_may_have_sent(w.command_id,lease(c))
    with c.owner.transaction(c.scope) as tx:
        if unknown:ProgramCommandRepository(tx).record_unknown(w.command_id)
        initial=b.reservations[0].reservation
        # Same economic hold; a later source watermark is a legitimate new
        # append-only reservation version, not collateral release or a fill.
        tx.reservations.append(replace(initial,evidence_watermark='later-read-watermark'),1)
    recovered=read(c,w.command_id)
    assert recovered.request==r and recovered.current_state==('UNKNOWN' if unknown else 'MAY_HAVE_SENT')
    from trading_system.recovery.original_creation import original_creation_case,original_query_targets
    budget=t.QueryBudget(maximum_requests=2,window_seconds=30,minimum_interval_ms=1000,retry_after=None)
    case=original_creation_case(recovered,now=c.c.facts.inputs.clock,budget=budget)
    assert case.scope==c.scope and case.command_ids==(w.command_id,)
    assert case.state=='OPEN' and case.missing==('CREATE_REQUEST_UNKNOWN',)
    assert original_query_targets(recovered)[0].client_id==r.assigned_ids.members[0].client_id
    assert recovered.can_resubmit is False


def test_reader_never_falls_back_to_another_scope_or_mints_a_new_id(database):
    c=real_case(database)
    w,r,b=c.service._transaction(lambda tx:attach(c,tx))
    other=real_case(database)
    assert read(other,w.command_id) is None
    assert read(c,'not-present') is None
