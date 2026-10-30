"""Actual restricted BUSINESS role cannot turn consumption into application."""
import pytest
from integration.test_program_management_postgres import isolated_postgres,database,venue,venue_v2,management_database,management_venue,advanced_case,management,submit
from integration.test_effective_orders_postgres import admitted

def test_dispatch_verifier_cannot_resolve_consumed_unapplied_amend(management_database,venue_v2,management_venue,tmp_path):
    import psycopg
    from trading_system.storage.models import scope_id
    from trading_system.contracts.canonical import canonical_json
    c,s,w=admitted(management_database,venue_v2,tmp_path)
    permit=s.boundary.mark_may_have_sent(w.command_id,c.lease)
    s.boundary.consume(permit,c.lease)
    fake={'source_kind':'PAPER_MANAGED_CREATION_SOURCE_V1','receipt':{
        'record_kind':'PAPER_KEEP_PRIORITY_RECEIPT_V1','command_id':w.command_id,'binding_hash':w.binding_hash,
        'original_client_id':w.original_client_id,'target_venue_id':w.target_venue_id,'effective_quantity':'0.5',
        'funds_released':False,'children_terminal':False}}
    with pytest.raises(psycopg.Error):
        with s.verifier_uow.transaction(c.scope) as tx:
            tx.execute('SELECT ledger.resolve_effective_management_v1(%s,%s,%s::jsonb)',(scope_id(c.scope),w.command_id,canonical_json(fake)))
    with s.verifier_uow.transaction(c.scope) as tx:
        assert tx.execute('SELECT count(*) FROM ledger.management_effective_resolutions_v1 WHERE scope_id=%s AND command_id=%s',
            (scope_id(c.scope),w.command_id)).fetchone()==(0,)
    assert c.engine.query_creation(c.write.command_id).orders[0].quantity==1
