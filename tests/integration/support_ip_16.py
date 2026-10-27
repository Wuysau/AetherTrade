"""IP-16 offline scenarios, not T/Q qualification or platform write evidence.

MemoryRegistry models rollback/CAS for deterministic logic probes. The separate
PostgreSQL process probe uses the real production repository and a task-specific
opt-in isolated Linux fixture. Neither path creates a transport or trading account.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from threading import RLock

import pytest

from trading_system.contracts import types as t
from trading_system.ownership.registry import (
    OwnerRecord, OwnershipRegistry, PostgresOwnerCAS, RegistryTx, TransferRecord, digest,
)
from trading_system.ownership.transfers import OwnershipService, TransferFacts, handoff_case_id, transfer_key
from trading_system.ownership.manual import ManualInterventionService, ExternalAttestation
from trading_system.storage.models import SequenceConflict, decode, encode, scope_id

STAMP = '2026-10-23T00:00:00Z'


class InjectedCrash(RuntimeError):
    pass


class MemoryTx:
    def __init__(self, registry, scope):
        self.registry, self.scope = registry, scope

    def get(self, table, key, cls):
        values = self.registry.rows.get((self.scope, table, key), ())
        return values[-1] if values else None

    def append(self, table, key, value, expected):
        values = self.registry.rows.setdefault((self.scope, table, key), [])
        if len(values) != expected:
            raise SequenceConflict('STALE_SEQUENCE')
        # Exercise production typed serialization without claiming DB durability.
        values.append(decode(type(value), encode(value)))

    def global_get(self, table, key, cls):
        matches = [v[-1] for (scope, name, record_id), v in self.registry.rows.items()
                   if name == table and record_id == key and v]
        if len(matches) > 1:
            raise ValueError('AMBIGUOUS_GLOBAL_ID')
        return matches[0] if matches else None

    def serialize(self, key):
        pass  # transaction holds the fixture mutex, not a platform fence.

    def owner(self):
        return self.get('audit_events', 'ip16:owner', OwnerRecord)

    def active_owner(self):
        return self.registry.owners.get(self.scope)

    def commit_owner(self, old, new):
        p, q = old.position, new.position
        if self.active_owner() != (p.owner_strategy_id, p.ownership_epoch, old.policy_revision):
            raise SequenceConflict('STALE_OWNER_EPOCH')
        self.registry.crash('before_epoch')
        self.registry.owners[self.scope] = (q.owner_strategy_id, q.ownership_epoch, new.policy_revision)
        self.registry.crash('after_epoch_before_commit')

    save_owner = RegistryTx.save_owner

    def raise_lock(self, lock, evidence):
        old = self.get('risk_locks', lock.lock_id, t.RiskLock)
        if old:
            return old
        self.append('risk_locks', lock.lock_id, lock, 0)
        return lock

    def locks(self):
        return tuple(values[-1] for (scope, table, key), values in self.registry.rows.items()
                     if scope == self.scope and table == 'risk_locks' and values[-1].state == 'ACTIVE')


class MemoryRegistry(OwnershipRegistry):
    def __init__(self):
        self.rows, self.owners = {}, {}
        self.mutex = RLock()
        self.fail_at = None

    def crash(self, phase):
        if self.fail_at == phase:
            self.fail_at = None
            raise InjectedCrash(phase)

    @contextmanager
    def transaction(self, scope):
        from trading_system.ownership.registry import offline
        offline(scope)
        with self.mutex:
            snapshot = deepcopy((self.rows, self.owners))
            try:
                yield MemoryTx(self, scope)
            except BaseException:
                self.rows, self.owners = snapshot
                raise
            self.crash('after_commit')

    def locate(self, transfer_id):
        matches = [scope for scope, table, key in self.rows
                   if table == 'ownership_transfers' and key == transfer_key(transfer_id)]
        if len(matches) != 1:
            raise LookupError('TRANSFER_NOT_FOUND_OR_AMBIGUOUS')
        return matches[0]


@dataclass(frozen=True)
class GateProbe:
    reason: str | None
    allowed: bool
    wire_requests: list


class OwnershipCase:
    def __init__(self, registry=None, scope=None, *, seed=True, old_owner='old', stamp=STAMP):
        self.stamp = stamp
        self.registry = registry if registry is not None else MemoryRegistry()
        self.scope = scope or t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='synthetic-ip16',
            product=t.Product.USD_M_PERP, instrument_uid='synthetic:BTCUSDT', venue_leg='LONG')
        self.old_owner = old_owner
        self.transfer_id = 'transfer-1:' + self.scope.venue_account_id
        self.position = t.ManagedPosition(position_id='ip16-position', scope=self.scope,
            actual_quantity=self.quantity('10'), owner_strategy_id=old_owner, owner_book_id='portfolio-book',
            ownership_epoch=1, cost_evidence=t.Coverage(scope=self.scope, query_succeeded=True,
            pagination_complete=True, supported=True, missing=()), realized_pnl=None,
            unrealized_valuation=None, active_exit_plan_id='preserved-exit-plan', risk_locks=())
        self.allowed = True
        self.fact_changes = {}
        self.source_sequence = 1
        self.service = OwnershipService(self.registry, authorize=lambda tx, request, context: self.allowed,
            read_facts=self.facts, clock=lambda: self.stamp)
        self.manual = ManualInterventionService(self.registry, read_attestation=lambda tx, event:
            ExternalAttestation(scope=event.scope, event_hash=digest(event), source_sequence=self.source_sequence),
            clock=lambda: self.stamp)
        if seed:
            if isinstance(self.registry, MemoryRegistry):
                self.registry.owners[self.scope] = (old_owner, 1, 1)
            self.registry.register(self.position, 1, contributors=('sub-strategy-a', 'sub-strategy-b'))

    @staticmethod
    def quantity(value):
        return t.Quantity(value=Decimal(value), unit='BTC')

    def ref(self, name='report'):
        return t.EvidenceRef(id='synthetic-ip16:' + name, hash=digest(name), environment=self.scope.environment,
                             source='SYNTHETIC_OFFLINE_IP16', visible_at=self.stamp)

    def request(self, mode='TRANSFER_OWNER', key=None):
        return t.TransferRequest(transfer_id=key or self.transfer_id, scope=self.scope, mode=mode,
                                 old_owner=self.old_owner, new_owner='new', ownership_epoch=1)

    def context(self, key=None):
        return t.ControlContext(identity_id='synthetic-human', environment=self.scope.environment,
            deployment_id='synthetic-deployment', authorization_id='synthetic-authorization',
            policy_revision=1, ownership_epoch=1, scopes=(self.scope,), expected_sequence=1,
            idempotency_key=key or 'request-1:' + self.scope.venue_account_id)

    def start(self, mode='TRANSFER_OWNER'):
        return self.service.request_transfer(self.request(mode), self.context())

    def evidence(self, *, flat=False, exit_state=None):
        projection = self.service.projection(self.transfer_id)
        quantity = self.quantity('0' if flat else '10')
        return t.TransferEvidence(transfer_id=self.transfer_id, durable_step_refs=projection.durable_steps,
            reconciliation=t.ReconciliationResult(case_id=handoff_case_id(self.transfer_id), resolved=True,
                missing=(), residual=self.quantity('0'), evidence=(self.ref('reconciliation'),)),
            protection=t.ProtectionVerdict(state=t.ProtectionState.FLAT_VERIFIED if flat else
                t.ProtectionState.VERIFIED_NATIVE, covered_quantity=quantity, missing=(),
                evidence=(self.ref('protection'),)),
            exit=t.ExitReceipt(position_id=self.position.position_id, state=exit_state or 'FLAT_VERIFIED',
                command_ids=('durable-exit-1',), incomplete_reasons=() if not exit_state else ('MARKET_CLOSED',))
                if flat or exit_state else None)

    def facts(self, tx, record, owner, evidence):
        position = owner.position
        if evidence.exit and evidence.exit.state == 'FLAT_VERIFIED':
            position = replace(position, actual_quantity=self.quantity('0'))
        return replace(TransferFacts(scope=self.scope, transfer_id=record.projection.request.transfer_id,
            transfer_sequence=record.sequence, owner_sequence=owner.sequence, evidence_hash=digest(evidence),
            context_hash=digest(record.context), position=position,
            valid_until=(datetime.fromisoformat(self.stamp.replace('Z', '+00:00')) + timedelta(seconds=60)).isoformat(),
            authorization_current=self.allowed, pending_commands_resolved=True, orders_verified=True,
            positions_verified=True, balances_and_allocations_verified=True, protection_verified=True,
            exit_plan_accepted=True, accepted_exit_plan_id='preserved-exit-plan'), **self.fact_changes)

    def advance_to(self, state, *, flat=False):
        for _ in range(8):
            projection = self.service.projection(self.transfer_id)
            if projection.state == state:
                return projection
            projection = self.service.advance(self.transfer_id, self.evidence(flat=flat))
            if projection.state == t.TransferState.TRANSFER_BLOCKED:
                raise AssertionError(projection.blocked_reasons)
        raise AssertionError('did not reach requested local state')

    def probe(self, owner='old', epoch=1, action='NEW_RISK', book='portfolio-book'):
        verdict = self.registry.check(self.scope, book, owner, epoch, action)
        # This path exercises the production eligibility gate only. No EnginePort
        # is installed, so an allowed result is not a submit or fill claim.
        return GateProbe(reason=verdict.reason, allowed=verdict.allowed, wire_requests=[])

    def transfer_then_submit_old_epoch(self):
        self.start()
        self.advance_to(t.TransferState.ACTIVE)
        return self.probe()

    def event(self, kind='QUANTITY', quantity='4', name='manual-reduction'):
        return t.ExternalChange(scope=self.scope, kind=kind, evidence=self.ref(name),
            observed_quantity=self.quantity(quantity) if quantity is not None else None)


@pytest.fixture
def ownership_case():
    return OwnershipCase()


@pytest.fixture(scope='module')
def ip16_database():
    """Explicit disposable Linux database; no reuse of another task's fixture DB."""
    import os
    from pathlib import Path
    import sys

    if os.environ.get('IP16_ISOLATED_DATABASE') != 'YES' or not os.environ.get('IP16_DATABASE_DSN'):
        pytest.skip('NOT_RUN: explicitly isolated IP16 PostgreSQL not supplied')
    if sys.platform != 'linux':
        pytest.skip('NOT_RUN: native transaction/role/crash verification requires isolated Linux')
    import psycopg
    dsn = os.environ['IP16_DATABASE_DSN']
    root = Path(__file__).resolve().parents[2]
    with psycopg.connect(dsn) as conn:
        if not conn.execute('SELECT current_database()').fetchone()[0].startswith('ip16_test_'):
            pytest.fail('fresh dedicated ip16_test_* database required')
        if conn.execute("SELECT to_regnamespace('ledger')").fetchone()[0] is not None:
            pytest.fail('refusing an existing ledger; provision a fresh database')
        conn.execute((root / 'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        conn.execute((root / 'deploy/db/roles.sql').read_text(encoding='utf-8'))
        conn.execute((root / 'migrations/0002_risk_resolution_authority.sql').read_text(encoding='utf-8'))
        conn.execute((root / 'migrations/0004_command_abort_guard.sql').read_text(encoding='utf-8'))
        conn.execute((root / 'migrations/0005_owner_cas_authority.sql').read_text(encoding='utf-8'))
        conn.execute((root / 'migrations/0007_owner_controller_ddl_guard.sql').read_text(encoding='utf-8'))
    return dsn


def postgres_case(dsn, *, reviewed_cas=False):
    from integration.support_ip_03 import LedgerScenario
    scenario = LedgerScenario(dsn)
    registry = OwnershipRegistry(scenario.uow)
    if reviewed_cas:
        from trading_system.storage.uow import UnitOfWork
        registry = OwnershipRegistry(UnitOfWork(lambda: connect_role(dsn, 'ledger_owner_controller')),
            owner_commit=PostgresOwnerCAS(read_authority=lambda tx, old, new: 'reviewed:' + scope_id(tx.scope)))
    case = OwnershipCase(registry, scenario.scope, old_owner=scenario.intent.owner_strategy_id,
        stamp=datetime.now(timezone.utc).isoformat() if reviewed_cas else STAMP)
    return case, scenario


def connect_role(dsn, role):
    """Test administrator assigns a NOLOGIN role; production does not elevate."""
    import psycopg
    if role not in {'ledger_owner_controller', 'ledger_owner_verifier', 'ledger_owner', 'ledger_control', 'ledger_decision'}:
        raise ValueError('unsupported fixture role')
    conn = psycopg.connect(dsn)
    conn.execute('SET ROLE ' + role)
    conn.commit()
    return conn


def connect_untrusted_owner(dsn, *, role='ledger_owner'):
    """Real unprivileged session_user: SET ROLE cannot reuse admin identity."""
    import secrets
    from uuid import uuid4
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    if role not in {'ledger_owner', 'ledger_owner_controller'}:
        raise ValueError('unsupported unprivileged fixture role')
    login, password = 'cas_test_' + uuid4().hex[:20], secrets.token_urlsafe(32)
    with psycopg.connect(dsn) as admin:
        admin.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS PASSWORD {}')
            .format(sql.Identifier(login), sql.Literal(password)))
        admin.execute(sql.SQL('GRANT {} TO {}').format(sql.Identifier(role), sql.Identifier(login)))
    settings = conninfo_to_dict(dsn)
    settings.update(user=login, password=password)
    conn = psycopg.connect(make_conninfo(**settings))
    conn.execute(sql.SQL('SET ROLE {}').format(sql.Identifier(role)))
    conn.commit()
    return conn


def attest_cas(case, dsn, *, authority_id=None, mutate=None):
    """Synthetic archive verifier with a real, separate database role.

    This proves SQL transaction/security behavior, never venue qualification or
    production authorization/protection archives. No runtime DTO can call it.
    """
    import json
    with case.registry.transaction(case.scope) as tx:
        old = tx.owner()
        record = tx.get('ownership_transfers', transfer_key(case.transfer_id), TransferRecord)
    evidence = case.evidence()
    facts = case.facts(None, record, old, evidence)
    new = replace(old, position=replace(facts.position, owner_strategy_id=record.projection.request.new_owner,
        ownership_epoch=old.position.ownership_epoch+1, active_exit_plan_id=facts.accepted_exit_plan_id),
        mode='ACTIVE', transfer_id=None, sequence=old.sequence+1)
    proof = {'facts': json.loads(encode(facts)), 'context_json': encode(record.context),
        'evidence_json': encode(evidence)}
    values = {'authority_id': authority_id or 'reviewed:' + scope_id(case.scope),
        'sid': scope_id(case.scope), 'scope': json.loads(encode(case.scope)),
        'transfer': json.loads(encode(record)), 'old': json.loads(encode(old)),
        'new': json.loads(encode(new)), 'proof': proof, 'expiry': facts.valid_until}
    if mutate:
        mutate(values)
    with connect_role(dsn, 'ledger_owner_verifier') as conn:
        conn.execute('SELECT ledger.attest_owner_transfer(%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)',
            (values['authority_id'], values['sid'], json.dumps(values['scope']), json.dumps(values['transfer']),
             json.dumps(values['old']), json.dumps(values['new']), json.dumps(values['proof']), values['expiry']))
    return values


def crash_worker(dsn, scope, old_owner, phase):
    """Real OS exit against an explicitly provisioned isolated PostgreSQL DB."""
    import os
    import psycopg
    from trading_system.storage.uow import UnitOfWork

    def connect():
        return connect_role(dsn, 'ledger_owner_controller' if phase != 'before_epoch' else 'ledger_owner')

    class CrashRegistry(OwnershipRegistry):
        @contextmanager
        def transaction(self, current_scope):
            committing = False
            with super().transaction(current_scope) as tx:
                original = tx.commit_owner

                def commit(old, new):
                    nonlocal committing
                    committing = True
                    if phase == 'before_epoch':
                        os._exit(71)
                    original(old, new)
                    if phase == 'after_epoch_before_commit':
                        os._exit(72)

                tx.commit_owner = commit
                yield tx
            if committing and phase == 'after_commit':
                os._exit(73)

    registry = CrashRegistry(UnitOfWork(connect), owner_commit=PostgresOwnerCAS(
        read_authority=lambda tx, old, new: 'reviewed:' + scope_id(tx.scope)) if phase != 'before_epoch' else None)
    case = OwnershipCase(registry, scope, seed=False, old_owner=old_owner,
        stamp=datetime.now(timezone.utc).isoformat() if phase != 'before_epoch' else STAMP)
    case.service.advance(case.transfer_id, case.evidence())
