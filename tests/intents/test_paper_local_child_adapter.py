"""Offline PAPER local-child adapter sequencing; database execution NOT_RUN."""
from contextlib import nullcontext
from dataclasses import dataclass
from types import SimpleNamespace
import pytest

from test_local_child_request import setup
from trading_system.contracts import types as t
from trading_system.engine import paper_local_child as module
from trading_system.engine.paper import PaperEngine
from trading_system.engine.paper_local_child import submit_local_child
from trading_system.execution.creation_requests import digest
from trading_system.execution.local_child_admission import (
    LocalChildCommand, LocalChildCommandBoundary, LocalChildPermit,
)
from trading_system.execution.local_child_quantity import LocalChildCommitment
from trading_system.execution.local_child_requests import materialize_local_child_request
from trading_system.runtime.execution_owner import ExecutionOwner
from trading_system.storage.uow import UnitOfWork


@dataclass(frozen=True)
class Model:
    quote_fee_rate: str


class Rows:
    def __init__(self, value=None):
        self.value = value

    def fetchone(self):
        return self.value


class DB:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, args=()):
        self.calls.append((sql, args))
        return Rows(None)


def fixture(monkeypatch):
    child, rules, capabilities, context = setup()
    request = materialize_local_child_request(child, 'activation', 'child-1', rules, capabilities, **context)
    command = LocalChildCommand(
        contract_version='2026-10-31', record_kind='LOCAL_CHILD_COMMAND_V1',
        state='MAY_HAVE_SENT', command_id='child-1', scope=child.parent.scope,
        execution_epoch=1, activation_id='activation', activation_bundle_hash='bundle',
        parent_hash=digest(child.parent), request_hash=digest(request),
        commitment_hash=digest(LocalChildCommitment(compilation=child, request=request)),
        target_hash='t' * 64, funding_hash='f' * 64, risk_hash='r' * 64,
        authorization_id='auth', owner_strategy_id='owner-1', ownership_epoch=1,
        policy_revision=1, lease_hash='l' * 64, created_at='2026-10-28T10:00:00Z',
        valid_until='2026-10-28T11:00:00Z')
    permit = LocalChildPermit(command_id='child-1', scope=command.scope,
                              request_hash=command.request_hash,
                              commitment_hash=command.commitment_hash, token='token',
                              expires_at='2026-10-28T10:01:00Z',
                              deadline_monotonic_ns=10 ** 9)
    engine = object.__new__(PaperEngine)
    engine.scope = child.parent.scope
    engine._scope_key = 'scope-key'
    engine._matching_model = Model('0')
    db = DB()
    engine._connect = lambda: db
    engine.query_creation = lambda cid: SimpleNamespace(status='ACCEPTED', evidence=())
    owner = object.__new__(ExecutionOwner)
    owner.engine = engine
    owner.guard = lambda lease: nullcontext()
    uow = object.__new__(UnitOfWork)
    tx = SimpleNamespace()
    uow.transaction = lambda scope: nullcontext(tx)
    boundary = object.__new__(LocalChildCommandBoundary)
    boundary.scope = child.parent.scope
    boundary.uow = uow
    boundary.calls = []
    boundary.consume = lambda p, lease: boundary.calls.append(('consume', p.command_id)) or command
    monkeypatch.setattr(module, 'read_local_child_original',
                        lambda tx, cid: SimpleNamespace(current_state='MAY_HAVE_SENT',
                                                        command=command, request=request,
                                                        compilation=child))
    monkeypatch.setattr(module, 'read_local_child_command', lambda tx, cid: command)
    return engine, owner, boundary, command, request, permit, db


def test_adapter_consumes_once_and_uses_local_child_identity(monkeypatch):
    engine, owner, boundary, command, request, permit, db = fixture(monkeypatch)
    lease = SimpleNamespace()
    receipt = submit_local_child(engine, command, request, permit, boundary=boundary,
                                 lease=lease, owner=owner)
    assert receipt.status == 'ACCEPTED' and receipt.execution_authorized is False
    assert boundary.calls == [('consume', 'child-1')]
    sql = [statement for statement, _ in db.calls]
    assert any('paper_engine_orders' in s for s in sql)
    assert any('paper_order_terms' in s for s in sql)
    assert any('paper_engine_commands' in s for s in sql)
    assert any('paper_match_order' in s for s in sql)


def test_adapter_refuses_a_foreign_request_before_consume(monkeypatch):
    engine, owner, boundary, command, request, permit, db = fixture(monkeypatch)
    bad = LocalChildPermit(command_id='other', scope=permit.scope,
                           request_hash=permit.request_hash,
                           commitment_hash=permit.commitment_hash, token='x',
                           expires_at=permit.expires_at,
                           deadline_monotonic_ns=permit.deadline_monotonic_ns)
    with pytest.raises(ValueError):
        submit_local_child(engine, command, request, bad, boundary=boundary,
                           lease=SimpleNamespace(), owner=owner)
    assert boundary.calls == [] and db.calls == []
