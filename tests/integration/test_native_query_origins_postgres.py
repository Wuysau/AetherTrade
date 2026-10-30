"""Original query identities are read from real protected history, not a DTO."""
from dataclasses import replace

import pytest

from integration.test_program_commands_postgres import isolated_postgres,database
from integration.test_program_consumed_boundary_postgres import ready


def test_actual_original_identity_reads_do_not_require_or_create_new_send_authority(database):
    c=ready(database)
    from trading_system.engine.nautilus.native_query_origins import load_native_query_origins
    result=load_native_query_origins(c.owner,c.scope,c.write.command_id)
    assert result.request==c.request and result.command_id==c.write.command_id
    assert result.request.assigned_ids==c.request.assigned_ids and result.consumption_ref
    assert not result.source_verified and not result.execution_authorized and not result.can_resubmit


def test_marked_but_unconsumed_original_still_yields_original_query_ids_without_resend(database):
    c=ready(database,consume=False)
    from trading_system.engine.nautilus.native_query_origins import load_native_query_origins
    result=load_native_query_origins(c.owner,c.scope,c.write.command_id)
    assert result.request==c.request and result.consumption_ref is None and not result.can_resubmit


def test_unknown_original_remains_queryable_and_cannot_cross_instrument_scope(database):
    c=ready(database)
    from trading_system.execution.program_commands import ProgramCommandRepository
    from trading_system.engine.nautilus.native_query_origins import load_native_query_origins
    with c.owner.transaction(c.scope) as tx:ProgramCommandRepository(tx).record_unknown(c.write.command_id)
    result=load_native_query_origins(c.owner,c.scope,c.write.command_id)
    assert result.request==c.request and result.current_state=='UNKNOWN' and not result.execution_authorized
    with pytest.raises(ValueError):load_native_query_origins(c.owner,replace(c.scope,instrument_uid='foreign-instrument'),c.write.command_id)


def test_actual_native_list_preserves_full_original_parent_and_member_ids_for_queries(database):
    from integration.test_program_financial_admission import real_case
    from integration.test_program_commands_postgres import attach,boundary,lease
    from trading_system.engine.nautilus.native_query_origins import load_native_query_origins
    c=real_case(database,template='OCO',mode='VENUE_NATIVE',actual='1',target='0',side='SELL')
    write,request,bundle=c.service._transaction(lambda tx:attach(c,tx))
    gate=boundary(c);current_lease=lease(c);permit=gate.mark_may_have_sent(write.command_id,current_lease)
    gate.consume(permit,current_lease)
    result=load_native_query_origins(c.owner,c.scope,write.command_id)
    assert result.request.assigned_ids==request.assigned_ids
    assert result.request.assigned_ids.parent_client_id and len(result.request.assigned_ids.members)==2
    assert not result.ownership_established and not result.source_verified and not result.can_resubmit
