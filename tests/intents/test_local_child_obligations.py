from types import SimpleNamespace
from dataclasses import replace
import pytest
from test_local_child_quantity import commitment
from trading_system.contracts.canonical import domain_payload
from trading_system.execution.creation_requests import digest
from trading_system.storage.models import scope_id,MissingEvidence
from trading_system.storage.local_programs import LocalProgramObligation,local_child_obligation_ids


def case():
    item=commitment('0.4');parent=item.compilation.parent
    activation=SimpleNamespace(activation_id='activation',state=object(),
        authority=SimpleNamespace(scope=parent.scope,compilation_hash=digest(parent)))
    tx=SimpleNamespace(present=True,rows=[('one',digest(item),domain_payload(item))])
    def execute(sql,args=()):
        if 'to_regclass' in sql:return SimpleNamespace(fetchone=lambda:('ledger.local_child_commitments_v1' if tx.present else None,))
        assert args==(scope_id(parent.scope),'activation')
        return SimpleNamespace(fetchall=lambda:tx.rows)
    tx.execute=execute
    return tx,activation


def test_unchanged_parent_retains_unresolved_pre_ack_child():
    tx,a=case();ids=local_child_obligation_ids(tx,a)
    view=LocalProgramObligation(original=a,current=a.state,pending_child_command_ids=ids)
    assert ids==('one',) and view.unresolved
    assert view.identity=='LOCAL_PROGRAM:activation'


def test_historical_schema_without_child_table_keeps_existing_parent_semantics():
    tx,a=case();tx.present=False
    assert local_child_obligation_ids(tx,a)==()
    assert not LocalProgramObligation(original=a,current=a.state).unresolved

@pytest.mark.parametrize('fault',['hash','id','duplicate','payload'])
def test_child_row_corruption_blocks_instead_of_disappearing(fault):
    tx,a=case();row=list(tx.rows[0])
    if fault=='hash':row[1]='f'*64
    if fault=='id':row[0]='foreign'
    if fault=='payload':row[2]={}
    tx.rows=[tuple(row)]
    if fault=='duplicate':tx.rows*=2
    with pytest.raises((MissingEvidence,ValueError)):local_child_obligation_ids(tx,a)
