"""Reader contract tests with synthetic rows; no PostgreSQL authority proof."""
from types import SimpleNamespace
from dataclasses import replace
import pytest
from test_local_child_quantity import commitment
from trading_system.contracts.canonical import domain_payload
from trading_system.execution.creation_requests import digest
from trading_system.storage.models import scope_id,MissingEvidence
from trading_system.storage.local_child_commitments import read_local_child_commitments


def fixture(monkeypatch):
    from trading_system.execution import local_program_activation
    item=commitment('0.4');scope=item.compilation.parent.scope
    activation=SimpleNamespace(authority=SimpleNamespace(scope=scope,compilation_hash=digest(item.compilation.parent)))
    monkeypatch.setattr(local_program_activation,'read_local_activation',lambda tx,key:activation if key=='activation' else None)
    tx=SimpleNamespace(scope=scope,active=True,present=True,rows=[(scope_id(scope),'one','activation',digest(item),domain_payload(item))])
    def execute(sql,args=()):
        if 'to_regclass' in sql:return SimpleNamespace(fetchone=lambda:('ledger.local_child_commitments_v1',) if tx.present else (None,))
        assert args==(scope_id(scope),'activation')
        assert 'LIMIT 4097' in sql
        return SimpleNamespace(fetchall=lambda:tx.rows)
    tx.execute=execute
    return tx,item


def test_read_restores_original_complete_rows_without_a_send_grant(monkeypatch):
    tx,item=fixture(monkeypatch)
    result=read_local_child_commitments(tx,'activation')
    assert result.commitments==(item,) and not result.execution_authorized
    assert not result.source_verified

@pytest.mark.parametrize('fault',['missing_table','missing_activation','scope','command','hash','parent','duplicate','inactive'])
def test_invalid_storage_never_becomes_empty_available_pool(monkeypatch,fault):
    tx,item=fixture(monkeypatch);activation='activation';row=list(tx.rows[0])
    if fault=='missing_table':tx.present=False
    if fault=='missing_activation':activation='absent'
    if fault=='scope':row[0]='foreign'
    if fault=='command':row[1]='other'
    if fault=='hash':row[3]='f'*64
    if fault=='parent':
        item=replace(item,compilation=replace(item.compilation,parent=replace(item.compilation.parent,program_hash='f'*64)))
        row[3]=digest(item);row[4]=domain_payload(item)
    tx.rows=[tuple(row)]
    if fault=='duplicate':tx.rows*=2
    if fault=='inactive':tx.active=False
    with pytest.raises((MissingEvidence,ValueError)):read_local_child_commitments(tx,activation)
