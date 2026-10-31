"""Offline protected local-child admission regressions (no PostgreSQL run).

The fake transaction records exact SQL boundaries and enforces append ordering.
Real row locks, SQL triggers, grant isolation, crash rollback and UNKNOWN restart
remain NOT_RUN until the shared DEV scheduler is available.
"""
from dataclasses import replace
from decimal import Decimal as D
from types import SimpleNamespace
from collections import defaultdict
import pytest

from test_local_child_request import setup
from test_program_compilation import inputs
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json, domain_payload
from trading_system.execution import local_child_admission as module
from trading_system.execution.creation_requests import digest
from trading_system.execution.local_child_admission import (
    LocalChildCurrentFacts, LocalChildPermit, admit_local_child,
    consume_local_child_permit, mark_local_child_may, read_local_child_command,
    record_local_child_unknown,
)
from trading_system.execution.local_child_quantity import LocalChildCommitment
from trading_system.storage.models import decode, scope_id


NOW = '2026-10-28T10:00:00Z'
TARGET_HASH = 't' * 64
FUNDING_HASH = 'f' * 64
RISK_HASH = 'r' * 64
BUNDLE_HASH = 'b' * 64


class Rows:
    def __init__(self, values=()):
        self.values = tuple(values)

    def fetchone(self):
        return self.values[0] if self.values else None

    def fetchall(self):
        return list(self.values)


class FakeTx:
    def __init__(self, scope, *, clock, lease, owner=('owner-1', 1, 7)):
        self.active = True
        self.scope = scope
        self.transaction_id = 'tx-1'
        self.group_id = 'group-1'
        self.clock, self.lease, self.owner = clock, lease, owner
        self.writes = defaultdict(list)
        self.commitments = []
        self.identities = []
        self.outbox = []
        self.marks = {}
        self.consumptions = []

    def require_scope(self, scope):
        if scope != self.scope:
            raise ValueError('FOREIGN_SCOPE')

    def execute(self, sql, args=()):
        if 'to_regclass' in sql:
            name = ('ledger.local_child_writes' if 'local_child_writes' in sql
                    else 'ledger.local_child_commitments_v1')
            return Rows(((name,),))
        if 'FROM ledger.active_owners' in sql:
            return Rows((self.owner,))
        if 'SELECT sequence,payload FROM ledger.local_child_writes' in sql:
            return Rows(tuple(self.writes.get(args[1], ())))
        if 'INSERT INTO ledger.local_child_commitments_v1' in sql:
            self.commitments.append(decode(LocalChildCommitment, args[4]))
            return Rows()
        if 'INSERT INTO ledger.program_client_identities' in sql:
            self.identities.append(args)
            return Rows()
        if 'INSERT INTO ledger.outbox' in sql:
            self.outbox.append(args)
            return Rows()
        if 'INSERT INTO ledger.local_child_dispatch_marks' in sql:
            self.marks[args[1]] = (args[2], args[3])
            return Rows()
        if 'SELECT token_hash FROM ledger.local_child_dispatch_marks' in sql:
            found = self.marks.get(args[1])
            return Rows(((found[1],),) if found else ())
        if 'SELECT 1 FROM ledger.local_child_permit_consumptions' in sql:
            return Rows(((1,),) if any(c[1] == args[1] for c in self.consumptions) else ())
        if 'INSERT INTO ledger.local_child_permit_consumptions' in sql:
            self.consumptions.append(args)
            return Rows()
        raise AssertionError('UNEXPECTED_SQL:' + sql)

    def _append(self, table, record_id, record, expected_sequence):
        assert table == 'local_child_writes'
        rows = self.writes[record_id]
        assert len(rows) == expected_sequence
        rows.append((expected_sequence + 1, canonical_json(domain_payload(record))))
        return expected_sequence + 1

    def _latest(self, table, record_id, cls):
        rows = self.writes.get(record_id, [])
        return decode(cls, rows[-1][1]) if rows else None


def activation_for(child, **changes):
    authority = SimpleNamespace(
        scope=child.parent.scope, compilation_hash=digest(child.parent),
        authorization_id='auth-1', owner_strategy_id='owner-1', ownership_epoch=1,
        submission_valid_until='2026-10-28T13:00:00Z')
    value = SimpleNamespace(activation_id='activation', bundle_hash=BUNDLE_HASH,
                            authority=authority)
    for name, item in changes.items():
        if name in ('authorization_id', 'compilation_hash', 'owner_strategy_id',
                    'ownership_epoch', 'submission_valid_until'):
            setattr(authority, name, item)
        else:
            setattr(value, name, item)
    return value


def current_for(child, clock, lease, **changes):
    target = SimpleNamespace(compilation_hash=digest(child), facts_hash=TARGET_HASH,
                             unallocated_target_quantity=t.Quantity(value=D('0.9'), unit='BTC'))
    funding = SimpleNamespace(blockers=(), execution_authorized=False)
    value = LocalChildCurrentFacts(clock=clock, lease=lease, target=target, funding=funding,
                                   target_hash=TARGET_HASH, funding_hash=FUNDING_HASH,
                                   authorization_id='auth-1', risk_clear=True,
                                   risk_hash=RISK_HASH,
                                   valid_until='2026-10-28T13:00:00Z')
    for name, item in changes.items():
        value = replace(value, **{name: item})
    return value


def fixture(monkeypatch):
    child, rules, capabilities, context = setup()
    scope = child.parent.scope
    clock = t.ClockReading(utc_now=context['now'], monotonic_ns=100, clock_health='HEALTHY')
    lease = t.OwnerLease(shard=t.ExecutionShard(environment=scope.environment,
                                                venue_account_id=scope.venue_account_id,
                                                product=scope.product, node_id='node-1',
                                                execution_epoch=3),
                         instance_id='owner-1', expires_at='2026-10-28T12:00:00Z',
                         deadline_monotonic_ns=10 ** 12)
    tx = FakeTx(scope, clock=clock, lease=lease)
    activation = activation_for(child)
    monkeypatch.setattr(module, 'read_local_activation', lambda _tx, key: activation
                        if key == 'activation' else None)
    import trading_system.storage.local_child_commitments as storage
    monkeypatch.setattr(storage, 'read_local_child_commitments',
                        lambda _tx, key: SimpleNamespace(commitments=tuple(tx.commitments)))
    return child, rules, capabilities, context, tx, clock, lease, activation


def admit(tx, child, rules, capabilities, context, clock, lease, activation, *,
          command='child-1', loader=None):
    return admit_local_child(
        tx, activation_id='activation', child=child, command_id=command,
        rules=rules, capabilities=capabilities,
        load_clock=lambda _tx: clock, load_lease=lambda _tx: lease,
        load_current=loader or (lambda _tx, _a, _c, _h, _cmd: current_for(child, clock, lease)),
        **context)


def test_admission_persists_commitment_and_independent_command_once(monkeypatch):
    child, rules, capabilities, context, tx, clock, lease, activation = fixture(monkeypatch)
    result = admit(tx, child, rules, capabilities, context, clock, lease, activation)
    assert not result.duplicate and not result.execution_authorized
    assert result.command.state == 'DURABLE_READY'
    assert result.command.request_hash == digest(result.request)
    assert result.command.activation_id == 'activation'
    assert result.committed_quantity == '0.1'
    assert len(tx.commitments) == 1 and len(tx.outbox) == 1
    assert [cid for _, _, _, _, _, _, cid in tx.identities] == ['child-1']
    assert tx.outbox[0][3] == 'LOCAL_CHILD_COMMAND_READY'
    assert read_local_child_command(tx, 'child-1') == result.command


def test_replay_returns_original_command_without_second_allocation(monkeypatch):
    child, rules, capabilities, context, tx, clock, lease, activation = fixture(monkeypatch)
    first = admit(tx, child, rules, capabilities, context, clock, lease, activation)
    replay = admit(tx, child, rules, capabilities, context, clock, lease, activation)
    assert replay.duplicate and replay.command == first.command
    assert len(tx.commitments) == 1 and len(tx.outbox) == 1
    assert len(tx.writes['child-1']) == 1


def test_conflicting_replay_is_rejected_without_extra_writes(monkeypatch):
    child, rules, capabilities, context, tx, clock, lease, activation = fixture(monkeypatch)
    admit(tx, child, rules, capabilities, context, clock, lease, activation)
    from trading_system.intents.local_child_compilation import compile_local_child
    other = compile_local_child(child.parent, '0.2', rules, capabilities, **context)
    with pytest.raises(ValueError):
        admit(tx, other, rules, capabilities, context, clock, lease, activation)
    assert len(tx.commitments) == 1 and len(tx.writes['child-1']) == 1


def test_parent_pool_is_never_oversubscribed(monkeypatch):
    child, rules, capabilities, context, tx, clock, lease, activation = fixture(monkeypatch)
    admit(tx, child, rules, capabilities, context, clock, lease, activation)
    from trading_system.intents.local_child_compilation import compile_local_child
    other = compile_local_child(child.parent, '0.95', rules, capabilities, **context)
    with pytest.raises(ValueError, match='QUANTITY_POOL_EXCEEDED'):
        admit(tx, other, rules, capabilities, context, clock, lease, activation, command='child-2')


@pytest.mark.parametrize('fault', ['activation', 'expired_lease', 'expired_authority',
                                   'owner', 'risk', 'target', 'funding', 'authorization'])
def test_untrusted_or_expired_current_facts_never_write(monkeypatch, fault):
    child, rules, capabilities, context, tx, clock, lease, activation = fixture(monkeypatch)
    if fault == 'activation':
        activation = activation_for(child, compilation_hash='f' * 64)
    loader = None
    if fault == 'expired_lease':
        lease = replace(lease, expires_at='2026-10-28T09:00:00Z')
    if fault == 'expired_authority':
        activation = activation_for(child, submission_valid_until='2026-10-28T09:00:00Z')
    if fault == 'owner':
        tx.owner = ('other-owner', 1, 7)
    if fault == 'risk':
        loader = lambda _tx, _a, _c, _h, _cmd: current_for(child, clock, lease, risk_clear=False)
    if fault == 'target':
        loader = lambda _tx, _a, _c, _h, _cmd: current_for(
            child, clock, lease,
            target=SimpleNamespace(compilation_hash='f' * 64, facts_hash=TARGET_HASH,
                                   unallocated_target_quantity=t.Quantity(value=D('0'),
                                                                          unit='BTC')))
    if fault == 'funding':
        loader = lambda _tx, _a, _c, _h, _cmd: current_for(
            child, clock, lease,
            funding=SimpleNamespace(blockers=('ALLOCATION_FUNDING_UNDERBOUND',),
                                    execution_authorized=False))
    if fault == 'authorization':
        loader = lambda _tx, _a, _c, _h, _cmd: current_for(child, clock, lease,
                                                     authorization_id='other-auth')
    monkeypatch.setattr(module, 'read_local_activation',
                        lambda _tx, key: activation if key == 'activation' else None)
    with pytest.raises(ValueError):
        admit(tx, child, rules, capabilities, context, clock, lease, activation,
              loader=loader)
    assert tx.commitments == [] and not tx.outbox and not any(tx.writes.values())


def test_permit_is_single_use_and_bound_to_original_command(monkeypatch):
    child, rules, capabilities, context, tx, clock, lease, activation = fixture(monkeypatch)
    result = admit(tx, child, rules, capabilities, context, clock, lease, activation)
    loader = lambda _tx, _a, _c, _h, _cmd: current_for(child, clock, lease)
    command, current_clock, ttl = mark_local_child_may(
        tx, 'child-1', lease, token_hash=module._token_hash('token-1'),
        load_clock=lambda _tx: clock, load_lease=lambda _tx: lease,
        load_current=loader, permit_ttl_ms=1000)
    assert command.state == 'MAY_HAVE_SENT' and ttl == 1000
    assert tx.marks['child-1'][1] == module._token_hash('token-1')
    permit = LocalChildPermit(command_id='child-1', scope=command.scope,
                              request_hash=command.request_hash,
                              commitment_hash=command.commitment_hash, token='token-1',
                              expires_at='2026-10-28T10:05:00Z',
                              deadline_monotonic_ns=10 ** 9)
    consumed = consume_local_child_permit(tx, permit, lease, load_clock=lambda _tx: clock,
                                          load_lease=lambda _tx: lease, load_current=loader)
    assert consumed == command and len(tx.consumptions) == 1
    with pytest.raises(ValueError, match='ALREADY_CONSUMED'):
        consume_local_child_permit(tx, permit, lease, load_clock=lambda _tx: clock,
                                   load_lease=lambda _tx: lease, load_current=loader)
    with pytest.raises(ValueError):
        mark_local_child_may(tx, 'child-1', lease, token_hash=module._token_hash('again'),
                             load_clock=lambda _tx: clock, load_lease=lambda _tx: lease,
                             load_current=loader, permit_ttl_ms=1000)
    assert result.command.state == 'DURABLE_READY'


def test_unknown_retains_original_identity_and_blocks_resend(monkeypatch):
    child, rules, capabilities, context, tx, clock, lease, activation = fixture(monkeypatch)
    admit(tx, child, rules, capabilities, context, clock, lease, activation)
    loader = lambda _tx, _a, _c, _h, _cmd: current_for(child, clock, lease)
    mark_local_child_may(tx, 'child-1', lease, token_hash=module._token_hash('token-1'),
                         load_clock=lambda _tx: clock, load_lease=lambda _tx: lease,
                         load_current=loader, permit_ttl_ms=1000)
    unknown = record_local_child_unknown(tx, 'child-1')
    assert unknown.state == 'MAY_HAVE_SENT'
    current = read_local_child_command(tx, 'child-1')
    assert current.state == 'UNKNOWN' and current.command_id == 'child-1'
    with pytest.raises(ValueError):
        mark_local_child_may(tx, 'child-1', lease, token_hash=module._token_hash('again'),
                             load_clock=lambda _tx: clock, load_lease=lambda _tx: lease,
                             load_current=loader, permit_ttl_ms=1000)


def test_final_clock_regression_aborts_before_return(monkeypatch):
    child, rules, capabilities, context, tx, clock, lease, activation = fixture(monkeypatch)
    calls = []

    def loader(_tx, _a, _c, _h, _cmd):
        calls.append(1)
        if len(calls) == 1:
            return current_for(child, clock, lease)
        return current_for(child, replace(clock, monotonic_ns=99), lease)

    with pytest.raises(ValueError, match='CURRENT_STATE_CHANGED'):
        admit(tx, child, rules, capabilities, context, clock, lease, activation,
              loader=loader)
