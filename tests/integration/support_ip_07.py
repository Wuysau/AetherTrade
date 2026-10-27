"""Synthetic facts + real PostgreSQL service probes; never venue qualification.

Runner supplies a disposable ip07_test_* database on isolated Linux. No host
credentials discovery, installation, service startup, or network venue calls.
"""
from dataclasses import replace
from decimal import Decimal
import os
import sys
from uuid import uuid4

import pytest

from integration.support_ip_03 import LedgerScenario, ROOT
from unit.support_ip_06 import make_case, money, coverage, NOW
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec, ABSENT
from trading_system.market.snapshots import snapshot_content_hash
from trading_system.risk.reservations import ReservationService, ReservationInputs, ReconciliationProof
from trading_system.risk.reservation_repository import ReservationRepository
from trading_system.storage.models import scope_id
from trading_system.execution.commands import CommandRepository
from trading_system.intents.target_compiler import TargetInputs
from trading_system.intents.ingress import IngressRevision, revision_key


@pytest.fixture(scope='module')
def ip07_database():
    if os.environ.get('IP07_ISOLATED_DATABASE') != 'YES' or not os.environ.get('IP07_DATABASE_DSN'):
        pytest.skip('NOT_RUN: IP07 isolated PostgreSQL not provisioned')
    if sys.platform != 'linux':
        pytest.skip('NOT_RUN: isolated Linux required for database probes')
    import psycopg
    dsn = os.environ['IP07_DATABASE_DSN']
    with psycopg.connect(dsn) as conn:
        if not conn.execute('SELECT current_database()').fetchone()[0].startswith('ip07_test_'):
            pytest.fail('dedicated ip07_test_* database required')
        if conn.execute("SELECT to_regnamespace('ledger')").fetchone()[0] is not None:
            pytest.fail('fresh database required; refusing existing ledger')
        conn.execute((ROOT / 'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'deploy/db/roles.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'migrations/0004_command_abort_guard.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'migrations/0006_proposal_intake_role.sql').read_text(encoding='utf-8'))
    return dsn


class ReservationCase:
    def __init__(self, dsn, *, group=None, available='100', sell=False,
                 product=t.Product.SPOT, account=None, instrument=None):
        self.ledger = LedgerScenario(dsn, seed=False)
        self.ledger.scope = replace(self.ledger.scope, product=product,
            venue_account_id=account or self.ledger.scope.venue_account_id,
            instrument_uid=instrument or self.ledger.scope.instrument_uid)
        self.scope = self.ledger.scope
        self.group = group or self.ledger.group
        self.owner = 'strategy-' + uuid4().hex
        with self.ledger.connect_admin() as conn:
            if group is None:
                conn.execute('INSERT INTO ledger.collateral_mutex VALUES (%s,%s)', ('PAPER', self.group))
            binding = conn.execute('SELECT collateral_group_id FROM ledger.account_bindings '
                'WHERE environment=%s AND account_id=%s AND product=%s',
                ('PAPER', self.scope.venue_account_id, str(product))).fetchone()
            if binding is None:
                conn.execute('INSERT INTO ledger.account_bindings VALUES (%s,%s,%s,%s,NULL)',
                    ('PAPER', self.scope.venue_account_id, str(product), self.group))
            else:
                assert binding == (self.group,)
            conn.execute('INSERT INTO ledger.active_owners VALUES (%s,%s,%s,%s,%s,%s,1,1)',
                ('PAPER', self.scope.venue_account_id, str(product), self.scope.instrument_uid, 'BOTH', self.owner))
        self.case = make_case(product)
        if sell:
            self.case.exiting('2')
        c = self.case
        self.currency = c.inputs.instrument.settlement_currency
        c.intent = replace(c.intent, intent_id=self.ledger.intent.intent_id, owner_strategy_id=self.owner,
            venue_account_id=self.scope.venue_account_id, collateral_group_id=self.group,
            instrument_uid=self.scope.instrument_uid,
            target_quantity='0' if sell else '0.7')
        c.policy = replace(c.policy, collateral_group_id=self.group, cash_buffer='0')
        c.book = replace(c.book, scope=self.scope, coverage=coverage(self.scope),
            balances=(money(available, self.currency), money('10', 'ASSET')),
            positions=tuple(replace(p, scope=self.scope, owner_strategy_id=self.owner,
                cost_evidence=coverage(self.scope)) for p in c.book.positions))
        c.market = replace(c.market, scope=self.scope, coverage=coverage(self.scope))
        c.market = replace(c.market, ref=replace(c.market.ref, hash=snapshot_content_hash(c.market)))
        c.inputs = replace(c.inputs, intent_id=c.intent.intent_id, owner_strategy_id=self.owner,
            scope=self.scope, collateral_group_id=self.group, snapshot_ref=c.market.ref,
            instrument=replace(c.inputs.instrument, instrument_uid=self.scope.instrument_uid),
            session=replace(c.inputs.session, instrument_uid=self.scope.instrument_uid),
            fee_reserve=(), funding_reserve=(), spendable=c.book.balances,
            marks=tuple(replace(m, scope=self.scope) for m in c.inputs.marks))
        payload = replace(self.ledger.command.canonical_payload, quantity='2' if sell else '0.7',
            side='SELL' if sell else 'BUY', order_type='MARKET', price=ABSENT,
            reduce_only=bool(sell and product == t.Product.USD_M_PERP))
        self.command = replace(self.ledger.command, canonical_payload=payload, created_at=NOW,
            venue_account_id=self.scope.venue_account_id, product=str(product), instrument_uid=self.scope.instrument_uid)
        encoded = ContractCodec().encode(self.command)
        self.command = replace(self.command, payload_hash=canonical_hash(encoded['canonical_payload']))
        self.calls = []
        self.proof = None
        self.group_capacity = (money('1000', self.currency), money('10', 'ASSET'))
        self.strategy_capacity = self.group_capacity
        self.buffer = (money('0', self.currency), money('0', 'ASSET'))
        self.service = ReservationService(self.ledger.uow, self.scope, self.inputs,
            clock=lambda: NOW, load_proof=lambda tx, evidence: self.proof)
        self.save_book(c.book)
        context = self.target_context(c.intent)
        with self.ledger.uow.transaction(self.scope) as tx:
            tx._append('audit_events', revision_key(context), IngressRevision(sequence=1,
                identity_id=context.identity_id, deployment_id=context.deployment_id,
                authorization_id=context.authorization_id, owner_strategy_id=self.owner,
                strategy_revision=context.strategy_revision, risk_policy_revision=context.policy_revision,
                ownership_epoch=context.ownership_epoch, enabled=True), 0)

    def target_context(self, intent):
        return t.DecisionContext(identity_id='synthetic-user', environment=self.scope.environment,
            deployment_id='synthetic-' + self.scope.venue_account_id,
            authorization_id=intent.authorization_id, policy_revision=intent.risk_policy_revision,
            ownership_epoch=intent.ownership_epoch, scopes=(self.scope,),
            strategy_revision=intent.strategy_revision, decision_run_id=intent.decision_run_id,
            snapshot_ref=self.case.market.ref)

    def inputs(self, tx, intent, book):
        self.calls.append(book)
        return ReservationInputs(risk=replace(self.case.inputs, book_sequence=book.sequence,
            spendable=book.balances), market=self.case.market, policy=self.case.policy,
            authorization=t.AuthorizationDecision(allowed=True, authorization_id=intent.authorization_id,
                rejection_codes=(), evidence=book.evidence), command=self.command,
            group_capacity=self.group_capacity, strategy_capacity=self.strategy_capacity, buffer=self.buffer,
            target=TargetInputs(instrument=self.case.inputs.instrument,
                expected_instrument=self.case.inputs.instrument, clock=self.case.inputs.clock,
                deadline_monotonic_ns=10**20, session=self.case.inputs.session,
                order_sides=self.case.inputs.order_sides, context=self.target_context(intent),
                allow_round_down=self.case.inputs.allow_round_down))

    def reserve(self, expected=1):
        return self.service.reserve(self.case.intent, expected)

    def next_intent(self, target='0.2'):
        """A new synthetic request; no modification of an existing intent."""
        suffix = uuid4().hex
        self.case.intent = replace(self.case.intent, intent_id='intent-' + suffix, target_quantity=target)
        self.case.inputs = replace(self.case.inputs, intent_id=self.case.intent.intent_id)
        self.command = replace(self.command, intent_id=self.case.intent.intent_id,
            command_id='command-' + suffix, logical_client_order_id='logical-' + suffix,
            venue_client_order_id='client-' + suffix,
            canonical_payload=replace(self.command.canonical_payload, quantity=target))
        self.rehash_command()

    def rehash_command(self):
        encoded = ContractCodec().encode(self.command)
        self.command = replace(self.command, payload_hash=canonical_hash(encoded['canonical_payload']))

    def command_state(self, state):
        """Persist synthetic outcomes through IP-10; never forge ledger rows."""
        assert state in ('UNKNOWN', 'NOT_SENT', 'ABORTED_UNSENT', 'VENUE_CONFIRMED', 'VENUE_REJECTED')
        with self.ledger.uow.transaction(self.scope) as tx:
            repo = CommandRepository(tx)
            if state in ('NOT_SENT', 'ABORTED_UNSENT'):
                repo.abort_unsent(self.command.command_id, 'SYNTHETIC_UNSENT_ABORT')
                return
            repo.mark_may_have_sent(tx, self.command.command_id, 'DURABLE_READY')
        with self.ledger.uow.transaction(self.scope) as tx:
            CommandRepository(tx).record_outcome(t.CommandReceipt(
                receipt_id='synthetic:' + self.command.command_id,
                command_id=self.command.command_id, status=t.CommandState(state),
                reason='SYNTHETIC_OFFLINE_OUTCOME', existing_result_ref='synthetic-venue-event'))

    def save_book(self, book):
        with self.ledger.uow.transaction(self.scope) as tx:
            tx.projections.save_book(book, book.sequence - 1)
        self.case.book = book

    def evidence(self, reservation, *, reflected='70', certainty='CONFIRMED', terminal=False,
                 mapped=True, available='30'):
        ref = t.EvidenceRef(id='freeze-' + uuid4().hex, hash='b' * 64,
            environment=t.Environment.PAPER, source='synthetic-mapping', visible_at=NOW)
        evidence = t.ReservationEvidence(reservation_id=reservation.reservation_id,
            venue_freeze_ref=ref, reflected_amount=Decimal(reflected), command_certainty=certainty,
            terminal_verified=terminal, watermark=ref.id + ':' + ref.hash)
        if mapped:
            book = replace(self.case.book, sequence=self.case.book.sequence + 1,
                balances=(money(available, self.currency), money('10', 'ASSET')), evidence=(ref,))
            self.save_book(book)
            self.proof = ReconciliationProof(evidence=evidence, scope=self.scope,
                intent_id=reservation.intent_id, asset=reservation.asset,
                command_refs=reservation.command_refs, book_sequence=book.sequence, obligations_cleared=terminal)
        else:
            self.proof = None
        return evidence

    def projection(self, reservation_id):
        with self.ledger.uow.transaction(self.scope) as tx:
            return ReservationRepository(tx).get(reservation_id)

    def count(self, table):
        assert table in ('intents', 'commands', 'reservations', 'audit_events', 'risk_locks', 'reconciliation_cases', 'outbox')
        with self.ledger.connect_admin() as conn:
            # Exclude the seeded deployment prerequisite from bundle counts.
            predicate = " AND record_id NOT LIKE 'ip09:revision:%%'" if table == 'audit_events' else ''
            return conn.execute(f'SELECT count(*) FROM ledger.{table} WHERE scope_id=%s' + predicate,
                                (scope_id(self.scope),)).fetchone()[0]


@pytest.fixture
def reservation_case(ip07_database):
    return ReservationCase(ip07_database)


def reserve_worker(dsn, suffix, case, command, group, barrier, output):
    """Spawned local process using the actual UoW and calculator, no platform."""
    c = ReservationCase.__new__(ReservationCase)
    c.ledger = LedgerScenario(dsn, suffix=suffix, seed=False)
    c.ledger.scope = case.book.scope
    c.scope, c.group, c.case, c.command = case.book.scope, group, case, command
    c.currency = case.inputs.instrument.settlement_currency
    c.calls = []
    c.group_capacity = (money('1000', c.currency), money('10', 'ASSET'))
    c.strategy_capacity = c.group_capacity
    c.buffer = (money('0', c.currency), money('0', 'ASSET'))
    c.service = ReservationService(c.ledger.uow, c.scope, c.inputs, clock=lambda: NOW)
    try:
        barrier.wait(timeout=20)
        result = c.reserve()
        output.put((result.accepted, len(c.calls[-1].reservations), result.rejection_codes))
    except Exception as exc:
        # Do not print DSNs or connection diagnostics containing credentials.
        output.put(('ERROR', type(exc).__name__))
        raise RuntimeError('reservation worker failed') from None
