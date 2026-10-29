"""Actual financial kernel + local V3 lifecycle, isolated PostgreSQL only."""
from dataclasses import replace
import pytest

from integration.test_program_financial_admission import isolated_postgres,database,real_case,digest
from trading_system.contracts import types as t
from trading_system.storage.models import scope_id


def admit(c,*,fault=None):
    from trading_system.execution.local_program_activation import activate_local_program
    def operation(tx):
        bundle=c.service._reserve_program(tx,c.key,(),load_inputs=c.load,load_qualification=c.qualify,
            load_clock=lambda tx:c.c.facts.inputs.clock)
        clock=lambda tx:replace(c.c.facts.inputs.clock,clock_health='UNHEALTHY') if fault=='clock' else c.c.facts.inputs.clock
        receipt=activate_local_program(tx,bundle,load_clock=clock)
        assert activate_local_program(tx,bundle,load_clock=clock)==receipt
        if fault=='rollback':raise OSError('after actual activation')
        return receipt,bundle
    return c.service._transaction(operation)


@pytest.mark.parametrize('template',['TWAP','VP'])
def test_real_local_program_activates_with_sum_pool_without_external_parent(database,template):
    from trading_system.execution.local_program_activation import read_local_activation
    from trading_system.storage.local_programs import local_program_obligations
    from trading_system.risk.target_guard import target_obligations
    c=real_case(database,template=template,mode='LOCAL_SYNTHETIC')
    receipt,bundle=admit(c)
    with c.owner.transaction(c.scope) as tx:
        record=read_local_activation(tx,receipt.activation_id)
        assert record.financial_intent_id==bundle.intent.intent_id and record.bundle_hash==digest(bundle)
        assert record.authority.parent_command_id is record.authority.parent_client_id is None
        assert record.binding.parent_venue_id is None and record.binding.binding_kind=='LOCAL_PROGRAM_ACTIVATION'
        assert record.state.status=='ACTIVE' and record.state.pools[0].unallocated_quantity=='1'
        assert record.authority.groups[0].aggregation=='SUM' and record.authority.nodes[0].origin=='APPLICATION_GENERATED_CHILD'
        assert record.state.projections[0].effective_remaining_quantity=='1'
        assert record.authority.management_policy=='MANAGE_UNTIL_FLAT'
        assert local_program_obligations(tx,scope_only=True)[0].original==record
        for table in ('commands','order_writes_v2','control_commands'):
            assert tx.execute('SELECT count(*) FROM ledger.'+table+' WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()[0]==0
        assert tx.execute('SELECT count(*) FROM ledger.order_funding_links WHERE scope_id=%s',(scope_id(c.scope),)).fetchone()[0]==len(bundle.reservations)
        with pytest.raises(ValueError,match='LOCAL_PROGRAM_TARGET_ALLOCATION_REQUIRED'):
            target_obligations(tx,bundle.intent,c.c.book,bundle.reservations,None)
    assert not receipt.execution_authorized


@pytest.mark.parametrize('fault',['clock','rollback'])
def test_activation_failure_rolls_back_financial_bundle_and_lifecycle(database,fault):
    from trading_system.execution.local_program_activation import read_local_activation
    c=real_case(database,template='TWAP',mode='LOCAL_SYNTHETIC')
    with pytest.raises((ValueError,OSError)):admit(c,fault=fault)
    with c.owner.transaction(c.scope) as tx:
        assert tx.intents.get(c.record.intent_id) is None
        assert tx.reservations.for_intent(c.record.intent_id)==()
        assert read_local_activation(tx,'local-program:'+c.record.intent_id) is None


def test_historical_bundle_cannot_be_attached_after_commit(database):
    from trading_system.execution.local_program_activation import activate_local_program
    c=real_case(database,template='TWAP',mode='LOCAL_SYNTHETIC')
    bundle=c.service._transaction(lambda tx:c.service._reserve_program(tx,c.key,(),load_inputs=c.load,
        load_qualification=c.qualify,load_clock=lambda tx:c.c.facts.inputs.clock))
    with c.owner.transaction(c.scope) as tx:
        with pytest.raises(ValueError,match='PROGRAM_FINANCIAL_WITNESS_REQUIRED'):
            activate_local_program(tx,bundle,load_clock=lambda tx:c.c.facts.inputs.clock)
