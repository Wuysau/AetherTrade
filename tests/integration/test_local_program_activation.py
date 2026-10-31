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
        if fault=='owner':
            from trading_system.intents.ingress import revision_key,IngressRevision
            key=revision_key(bundle.archive.source.context)
            previous=tx._latest('audit_events',key,IngressRevision)
            tx._append('audit_events',key,replace(previous,sequence=previous.sequence+1,enabled=False),previous.sequence)
        readings=0
        def clock(tx):
            nonlocal readings
            readings+=1
            current=c.c.facts.inputs.clock
            if fault=='clock':return replace(current,clock_health='UNHEALTHY')
            if fault=='expired':return replace(current,utc_now=bundle.reservations[0].reservation.expires_at)
            if fault=='expired_during_activation' and readings>1:
                return replace(current,utc_now=bundle.reservations[0].reservation.expires_at)
            return current
        receipt=activate_local_program(tx,bundle,load_clock=clock)
        if fault!='expired_during_activation':
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


@pytest.mark.parametrize('fault',['clock','expired','expired_during_activation','owner','rollback'])
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
    with pytest.raises(ValueError,match='PROGRAM_FINANCIAL_WITNESS_REQUIRED'):
        with c.owner.transaction(c.scope) as tx:
            activate_local_program(tx,bundle,load_clock=lambda tx:c.c.facts.inputs.clock)


def test_zero_asset_owned_derivative_reduction_retains_quantity_and_protection_duty(database):
    from trading_system.execution.local_program_activation import read_local_activation
    from trading_system.storage.local_programs import local_program_obligations
    c=real_case(database,template='TWAP',mode='LOCAL_SYNTHETIC',product=t.Product.USD_M_PERP,
        actual='1',target='0',quantity='1',side='SELL',reduce_only=True)
    receipt,bundle=admit(c)
    assert bundle.reservations==()
    with c.owner.transaction(c.scope) as tx:
        record=read_local_activation(tx,receipt.activation_id)
        assert record.authority.funding_basis=='OWNED_POSITION_REDUCTION'
        assert record.authority.position_allocation_hash==digest(bundle.archive.book.positions[0])
        assert record.authority.reservation_ids==record.authority.funding_requirements==()
        assert record.authority.nodes[0].side=='SELL' and record.state.pools[0].unallocated_quantity=='1'
        assert record.authority.protection_required and record.state.required_protection_quantity=='1'
        assert len(local_program_obligations(tx,scope_only=True))==1


@pytest.mark.parametrize('fault',['proof_role','proof_source','authority','state'])
def test_owner_authored_audit_objects_do_not_authenticate_source(database,fault):
    from trading_system.execution.local_program_activation import read_local_activation
    from trading_system.storage.local_programs import PROOF_PREFIX,STATE_PREFIX,local_program_obligations
    c=real_case(database,template='TWAP',mode='LOCAL_SYNTHETIC');receipt,bundle=admit(c)
    with c.owner.transaction(c.scope) as tx:
        record=read_local_activation(tx,receipt.activation_id)
        if fault in ('proof_role','proof_source'):
            proof=record.proofs[0]
            forged=replace(proof,role='PARENT_BINDING') if fault=='proof_role' else replace(proof,
                source_refs=(replace(proof.source_refs[0],hash='0'*64),))
            tx._append('audit_events',PROOF_PREFIX+proof.proof_id,forged,1)
            with pytest.raises(ValueError,match='LOCAL_PROOF_ARCHIVE_MISMATCH'):
                read_local_activation(tx,receipt.activation_id)
        elif fault=='authority':
            from trading_system.contracts.order_lifecycle_v3_reducer import prepare
            assert 'AUTHORITY_PROOF_UNVERIFIED' in prepare(record.authority,record.state.authority_proof_ref,
                now=record.activated_at).restrictions
        else:
            # Even an apparent terminal projection cannot remove its remaining pool.
            tx._append('audit_events',STATE_PREFIX+receipt.activation_id,replace(record.state,
                sequence=4,status='COMPLETED',pools=()),3)
            assert local_program_obligations(tx,scope_only=True)[0].unresolved


def test_unknown_local_pool_blocks_entire_collateral_group_and_preserves_original_owner(database):
    import psycopg
    from trading_system.execution.local_program_activation import read_local_activation
    from trading_system.storage.local_programs import STATE_PREFIX,local_program_obligations
    from trading_system.execution.commands import CommandRepository
    c=real_case(database,template='VP',mode='LOCAL_SYNTHETIC');receipt,bundle=admit(c)
    with c.owner.transaction(c.scope) as tx:
        record=read_local_activation(tx,receipt.activation_id)
        tx._append('audit_events',STATE_PREFIX+receipt.activation_id,replace(record.state,
            sequence=4,status='SUBMIT_UNKNOWN'),3)
    other=replace(c.scope,venue_account_id=c.scope.venue_account_id+'-other',product=t.Product.USD_M_PERP)
    with psycopg.connect(database) as db:
        db.execute('INSERT INTO ledger.account_bindings VALUES(%s,%s,%s,%s,NULL)',
            (str(other.environment),other.venue_account_id,str(other.product),bundle.intent.collateral_group_id))
    # A direct owner overwrite is forbidden; the original activation remains
    # attached to its original epoch and duty through restart.
    with pytest.raises(psycopg.errors.CheckViolation):
        with psycopg.connect(database) as db:
            db.execute('UPDATE ledger.active_owners SET ownership_epoch=2 WHERE account_id=%s',(c.scope.venue_account_id,))
    with c.owner.transaction(other) as tx:
        obligation=local_program_obligations(tx)[0]
        assert obligation.original.authority.ownership_epoch==1 and obligation.original.authority.owner_strategy_id==bundle.intent.owner_strategy_id
        assert obligation.identity in CommandRepository(tx).unresolved_in_group('')
        assert local_program_obligations(tx,scope_only=True)==()
    # No current-clock parameter: historical read restores the original bounded
    # authority; expiry and owner transfer cannot renew or discard its duty.
    with c.owner.transaction(c.scope) as tx:
        assert read_local_activation(tx,receipt.activation_id)==record


def test_copying_the_original_transaction_uuid_cannot_forge_physical_attachment(database):
    from trading_system.execution.local_program_activation import _materials,read_local_activation
    from trading_system.storage.local_programs import ACTIVATION_PREFIX
    from trading_system.storage.models import encode
    c=real_case(database,template='TWAP',mode='LOCAL_SYNTHETIC')
    bundle=c.service._transaction(lambda tx:c.service._reserve_program(tx,c.key,(),load_inputs=c.load,
        load_qualification=c.qualify,load_clock=lambda tx:c.c.facts.inputs.clock))
    old_id=bundle.archive.target_facts.transaction_id
    activation_id='local-program:'+bundle.intent.intent_id
    forged,_=_materials(bundle,activation_id,old_id,c.c.facts.inputs.clock.utc_now,bundle.reservations[0].reservation.expires_at)
    with c.owner.transaction(c.scope) as tx:
        tx.execute('INSERT INTO ledger.audit_events(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,%s,1,%s::jsonb,%s)',
            (scope_id(c.scope),ACTIVATION_PREFIX+activation_id,encode(forged),old_id))
        with pytest.raises(ValueError,match='LOCAL_FINANCIAL_SOURCE_REQUIRED'):
            read_local_activation(tx,activation_id)
