"""PostgreSQL transactions. Connection factories must return fresh psycopg 3 connections.

No network/LLM work belongs inside this context. The factory is injected; this
module never discovers credentials, installs a driver, or opens a platform API.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from time import monotonic_ns
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, domain_payload
from trading_system.contracts.wire import ExecutionCommand, TradeIntent, ContractCodec
from .models import (encode, decode, scope_id, account_key, SequenceConflict,
                     ScopeMismatch, MissingEvidence, atomic_write)
from .command_state import CommandOutcome, command_hash


class LedgerTx:
    def __init__(self, connection, scope, group_id):
        self._connection = connection
        self.scope = scope
        self.group_id = group_id
        self.transaction_id = str(uuid4())
        self.active = True
        self.committed = False
        self.failed = False
        self.commands = CommandRepository(self)
        self.reservations = ReservationRepository(self)
        self.risk_locks = RiskLockRepository(self)
        self.accounts = AccountRepository(self)
        self.projections = ProjectionRepository(self)
        self.intents = IntentRepository(self)

    def execute(self, sql, params=()):
        if not self.active or self.failed:
            raise RuntimeError('transaction is closed or rollback-only')
        try:
            return self._connection.execute(sql, params)
        except BaseException:
            self.failed = True
            raise

    def require_scope(self, scope):
        if scope != self.scope:
            self.failed = True
            raise ScopeMismatch('record does not belong to transaction scope')

    def advance(self, aggregate_id, expected_sequence):
        if type(expected_sequence) is not int or expected_sequence < 0:
            self.failed = True
            raise SequenceConflict('invalid expected sequence')
        key = scope_id(self.scope)
        self.execute('INSERT INTO ledger.aggregate_heads(scope_id,aggregate_id,sequence) '
                     'VALUES (%s,%s,0) ON CONFLICT DO NOTHING', (key, aggregate_id))
        row = self.execute('UPDATE ledger.aggregate_heads SET sequence=sequence+1 '
                           'WHERE scope_id=%s AND aggregate_id=%s AND sequence=%s '
                           'RETURNING sequence', (key, aggregate_id, expected_sequence)).fetchone()
        if row is None:
            self.failed = True
            raise SequenceConflict('stale aggregate sequence')
        return row[0]

    def _append(self, table, record_id, record, expected_sequence):
        # Table names only originate in fixed repository implementations below.
        sequence = self.advance(table + ':' + record_id, expected_sequence)
        self.execute(f'INSERT INTO ledger.{table} '
                     '(scope_id,record_id,sequence,payload,transaction_id) '
                     'VALUES (%s,%s,%s,%s::jsonb,%s)',
                     (scope_id(self.scope), record_id, sequence, encode(record), self.transaction_id))
        return sequence

    def _latest(self, table, record_id, cls):
        row = self.execute(f'SELECT payload FROM ledger.{table} '
                           'WHERE scope_id=%s AND record_id=%s ORDER BY sequence DESC LIMIT 1',
                           (scope_id(self.scope), record_id)).fetchone()
        return None if row is None else decode(cls, row[0])

    def _commit(self):
        if self.failed:
            raise RuntimeError('transaction is rollback-only')
        self.execute('INSERT INTO ledger.transaction_observations '
                     '(transaction_id,scope_id,phase) VALUES (%s,%s,%s)',
                     (self.transaction_id, scope_id(self.scope), 'COMMIT_MARKER'))
        self._connection.commit()
        self.active = False
        self.committed = True


class UnitOfWork:
    def __init__(self, connection_factory):
        self.connection_factory = connection_factory

    @contextmanager
    def transaction(self, scope: t.ScopeKey):
        if scope.environment == t.Environment.LIVE:
            raise ValueError('LIVE is not enabled by this implementation stage')
        connection = self.connection_factory()
        tx = None
        try:
            if connection.autocommit:
                raise ValueError('autocommit connections are forbidden')
            connection.execute('SET TRANSACTION ISOLATION LEVEL READ COMMITTED')
            # A durable receipt must not inherit an asynchronous commit setting.
            connection.execute('SET LOCAL synchronous_commit = on')
            # Binding changes cannot race the transaction. Group lock serializes
            # all accounts/products sharing collateral, not just instruments.
            row = connection.execute('SELECT collateral_group_id FROM ledger.account_bindings '
                                     'WHERE environment=%s AND account_id=%s AND product=%s '
                                     'FOR SHARE', account_key(scope)).fetchone()
            if row is None:
                raise MissingEvidence('account collateral binding is required')
            group = row[0]
            mutex = connection.execute('SELECT group_id FROM ledger.collateral_mutex '
                               'WHERE environment=%s AND group_id=%s FOR UPDATE',
                               (str(scope.environment), group)).fetchone()
            if mutex is None:
                raise MissingEvidence('collateral mutex is required')
            tx = LedgerTx(connection, scope, group)
            yield tx
            if tx.active:
                tx._commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            if tx is not None:
                tx.active = False
            connection.close()


class IntentRepository:
    def __init__(self, tx):
        self.tx = tx

    @atomic_write
    def append(self, intent: TradeIntent, expected_sequence: int = 0):
        if expected_sequence != 0:
            raise SequenceConflict('intents are immutable; create a superseding intent')
        self.tx.require_scope(t.ScopeKey(environment=t.Environment(intent.environment),
            venue_account_id=intent.venue_account_id, product=t.Product(intent.product),
            instrument_uid=intent.instrument_uid, venue_leg=intent.venue_leg))
        if intent.collateral_group_id != self.tx.group_id:
            self.tx.failed = True
            raise ScopeMismatch('collateral group mismatch')
        self.tx._append('intents', intent.intent_id, intent, expected_sequence)


class CommandRepository:
    def __init__(self, tx):
        self.tx = tx

    def get(self, command_id):
        command = self.tx._latest('commands', command_id, ExecutionCommand)
        row = self.tx.execute('SELECT command_payload,outcome FROM ledger.command_aborts '
            'WHERE scope_id=%s AND command_id=%s', (scope_id(self.tx.scope), command_id)).fetchone()
        if row is None:
            return command
        abort = decode(CommandOutcome, row[1])
        if (command is None or command.state != 'DURABLE_READY'
                or ContractCodec().encode(command) != row[0]
                or abort.command_id != command_id or abort.receipt.command_id != command_id
                or abort.request_hash != command_hash(command)
                or abort.durable_record_ref != command.durable_record_ref
                or abort.receipt.status != t.CommandState.ABORTED_UNSENT
                or abort.provenance != 'LOCAL_UNSENT_ABORT'):
            raise MissingEvidence('authoritative abort binding mismatch')
        return replace(command, state='ABORTED_UNSENT', outcome_provenance='LOCAL_POLICY')

    @atomic_write
    def append(self, command: ExecutionCommand, expected_sequence: int):
        scope = self.tx.scope
        intent = self.tx._latest('intents', command.intent_id, TradeIntent)
        if (intent is None or command.environment != scope.environment
                or command.venue_account_id != scope.venue_account_id
                or command.product != scope.product or command.instrument_uid != scope.instrument_uid):
            self.tx.failed = True
            raise ScopeMismatch('command requires an intent in the same scope')
        if (command.ownership_epoch != intent.ownership_epoch
                or command.policy_revision != intent.risk_policy_revision):
            self.tx.failed = True
            raise SequenceConflict('command epoch/policy differs from intent')
        payload = ContractCodec().encode(command)
        if canonical_hash(payload['canonical_payload']) != command.payload_hash:
            self.tx.failed = True
            raise ValueError('command payload hash mismatch')
        if expected_sequence != 0 or command.state != 'DURABLE_READY':
            self.tx.failed = True
            raise ValueError('new commands must be DURABLE_READY; transitions are explicit')
        self.tx._append('commands', command.command_id, command, expected_sequence)
        self.tx.execute('INSERT INTO ledger.command_identities '
            '(environment,account_id,product,venue_client_order_id,command_id,scope_id,intent_id) '
            'VALUES (%s,%s,%s,%s,%s,%s,%s)',
            (*account_key(scope), command.venue_client_order_id, command.command_id,
             scope_id(scope), command.intent_id))
        self.tx.execute('INSERT INTO ledger.outbox(message_id,scope_id,aggregate_id,sequence,kind,transaction_id) '
            'VALUES (%s,%s,%s,1,\'COMMAND_READY\',%s)',
            (str(uuid4()), scope_id(scope), command.command_id, self.tx.transaction_id))

    @atomic_write
    def mark_may_have_sent(self, tx, command_id, expected_state) -> t.DurablePermit:
        """Commit the entire transaction before returning a short-lived receipt.

        This is an explicit terminal operation: further transaction use fails.
        A receipt is not authorization: the execution owner must still validate
        lease, policy, qualification and expiry before any controlled bridge.
        """
        if tx is not self.tx or expected_state != 'DURABLE_READY':
            raise ValueError('invalid transaction or expected state')
        command = self.get(command_id)
        if command is None or command.state != expected_state:
            tx.failed = True
            raise SequenceConflict('command is not eligible; UNKNOWN must reconcile')
        intent = tx._latest('intents', command.intent_id, TradeIntent)
        owner = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
            'WHERE environment=%s AND account_id=%s AND product=%s '
            'AND instrument_uid=%s AND venue_leg=%s FOR SHARE',
            (*account_key(tx.scope), tx.scope.instrument_uid, tx.scope.venue_leg)).fetchone()
        if intent is None or owner != (intent.owner_strategy_id, command.ownership_epoch, command.policy_revision):
            raise SequenceConflict('current owner/policy is missing or stale')
        record_ref = 'postgres:' + tx.transaction_id
        updated = replace(command, state='MAY_HAVE_SENT', durable_record_ref=record_ref)
        tx._append('commands', command_id, updated, 1)
        deadline = monotonic_ns() + 1_000_000_000
        expires = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
        tx._commit()
        return t.DurablePermit(durable_record_ref=record_ref, command_id=command_id,
            payload_hash=command.payload_hash, scope=tx.scope,
            ownership_epoch=command.ownership_epoch, execution_epoch=command.execution_epoch,
            policy_revision=command.policy_revision, expires_at=expires,
            deadline_monotonic_ns=deadline)


class ReservationRepository:
    def __init__(self, tx):
        self.tx = tx

    def for_intent(self, intent_id):
        rows = self.tx.execute('SELECT DISTINCT ON (record_id) payload FROM ledger.reservations '
            'WHERE scope_id=%s AND payload->>\'intent_id\'=%s ORDER BY record_id,sequence DESC',
            (scope_id(self.tx.scope), intent_id)).fetchall()
        return tuple(decode(t.CapitalReservation, row[0]) for row in rows)

    @atomic_write
    def append(self, reservation: t.CapitalReservation, expected_sequence: int):
        intent = self.tx._latest('intents', reservation.intent_id, TradeIntent)
        if (reservation.environment != self.tx.scope.environment
                or reservation.venue_account_id != self.tx.scope.venue_account_id
                or reservation.collateral_group_id != self.tx.group_id
                or intent is None or reservation.owner_strategy_id != intent.owner_strategy_id):
            self.tx.failed = True
            raise ScopeMismatch('reservation binding mismatch')
        if (not reservation.amount.is_finite() or not reservation.reflected_amount.is_finite()
                or not 0 <= reservation.reflected_amount <= reservation.amount):
            self.tx.failed = True
            raise ValueError('invalid reservation amounts')
        # Release/reconciliation requires IP-07 evidence; never auto-release on TTL.
        if expected_sequence != 0 or reservation.state != t.ReservationState.HELD:
            self.tx.failed = True
            raise ValueError('reservation changes require an evidence-backed transition')
        self.tx._append('reservations', reservation.reservation_id, reservation, expected_sequence)
        self.tx.execute('INSERT INTO ledger.reservation_assets '
            '(environment,intent_id,asset,reservation_id,amount,reflected_amount) '
            'VALUES (%s,%s,%s,%s,%s,%s)',
            (str(reservation.environment), reservation.intent_id, reservation.asset,
             reservation.reservation_id, reservation.amount, reservation.reflected_amount))


class RiskLockRepository:
    def __init__(self, tx):
        self.tx = tx

    def active(self, scope):
        self.tx.require_scope(scope)
        rows = self.tx.execute('SELECT DISTINCT ON (record_id) payload FROM ledger.risk_locks '
                              'WHERE scope_id=%s ORDER BY record_id,sequence DESC',
                              (scope_id(scope),)).fetchall()
        return tuple(lock for row in rows if (lock := decode(t.RiskLock, row[0])).state == 'ACTIVE')

    def applicable(self, scope):
        """Original local locks plus DAILY_LOSS in the bound collateral domain.

        The UoW already holds the group's mutex. Reacquire it and verify both
        endpoint bindings here so this read cannot silently trust caller lock
        IDs, payload group labels, or a transaction's unverified group string.
        Every returned lock retains its original source scope and identity.
        """
        from trading_system.contracts.canonical import utc_timestamp
        self.tx.require_scope(scope)
        binding = self.tx.execute('SELECT collateral_group_id FROM ledger.account_bindings '
            'WHERE environment=%s AND account_id=%s AND product=%s FOR SHARE', account_key(scope)).fetchall()
        if (len(binding) != 1 or not self.tx.group_id or binding[0][0] != self.tx.group_id):
            raise MissingEvidence('current risk collateral binding is missing or changed')
        mutex = self.tx.execute('SELECT group_id FROM ledger.collateral_mutex '
            'WHERE environment=%s AND group_id=%s FOR UPDATE',
            (str(scope.environment), self.tx.group_id)).fetchall()
        if len(mutex) != 1 or mutex[0][0] != self.tx.group_id:
            raise MissingEvidence('risk collateral mutex is missing')
        # Include historical DAILY_LOSS identities even if a later malformed row
        # changes the cause. Such a row must not make the shared lock disappear.
        rows = self.tx.execute(
            'SELECT DISTINCT ON (l.scope_id,l.record_id) l.scope_id,l.record_id,l.sequence,l.payload '
            'FROM ledger.risk_locks l WHERE l.scope_id=%s OR EXISTS '
            '(SELECT 1 FROM ledger.risk_locks d WHERE d.scope_id=l.scope_id AND d.record_id=l.record_id '
            "AND d.payload->>'cause'='DAILY_LOSS') ORDER BY l.scope_id,l.record_id,l.sequence DESC",
            (scope_id(scope),)).fetchall()
        result, identities = [], {}
        for stored_scope, record_id, sequence, raw in rows:
            try:
                lock = decode(t.RiskLock, raw)
                if (not lock.lock_id or record_id != lock.lock_id or stored_scope != scope_id(lock.scope)
                        or not lock.scope.venue_account_id.strip() or not lock.scope.instrument_uid.strip()
                        or not lock.cause.strip() or not lock.required_evidence
                        or len(set(lock.required_evidence)) != len(lock.required_evidence)):
                    raise ValueError('risk lock identity is inconsistent')
                utc_timestamp(lock.raised_at)
                history = self.tx.execute('SELECT sequence,payload FROM ledger.risk_locks '
                    'WHERE scope_id=%s AND record_id=%s ORDER BY sequence', (stored_scope, record_id)).fetchall()
                initial = decode(t.RiskLock, history[0][1])
                if (sequence != len(history) or len(history) not in (1, 2)
                        or initial.state != 'ACTIVE' or initial.lock_id != lock.lock_id
                        or initial.scope != lock.scope or replace(lock, state='ACTIVE') != initial
                        or any(number != index + 1 or decode(t.RiskLock, payload) != (
                            initial if index == 0 else replace(initial, state='RESOLVED'))
                            for index, (number, payload) in enumerate(history))):
                    raise ValueError('risk lock history is inconsistent')
                source = self.tx.execute('SELECT collateral_group_id FROM ledger.account_bindings '
                    'WHERE environment=%s AND account_id=%s AND product=%s FOR SHARE',
                    account_key(lock.scope)).fetchall()
                if len(source) != 1 or not source[0][0]:
                    raise ValueError('risk lock source collateral binding is missing')
                if lock.cause == 'DAILY_LOSS' and (
                        lock.permission != t.RiskPermission.NO_NEW_RISK
                        or lock.required_evidence != ('DAILY_LOSS_REVIEW',)):
                    raise ValueError('daily-loss lock policy is inconsistent')
                if (stored_scope != scope_id(scope) and not (lock.cause == 'DAILY_LOSS'
                        and lock.scope.environment == scope.environment and source[0][0] == self.tx.group_id)):
                    continue
                if lock.lock_id in identities and identities[lock.lock_id] != stored_scope:
                    raise ValueError('risk lock identity is ambiguous')
                identities[lock.lock_id] = stored_scope
                if lock.state == 'ACTIVE':
                    result.append(lock)
            except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
                raise MissingEvidence('risk lock applicability is unverified: ' + str(exc)) from exc
        return tuple(result)

    @atomic_write
    def append(self, lock, expected_sequence):
        self.tx.require_scope(lock.scope)
        if lock.state != 'ACTIVE':
            self.tx.failed = True
            raise ValueError('resolution requires a dedicated authorized resolution event')
        self.tx._append('risk_locks', lock.lock_id, lock, expected_sequence)


class AccountRepository:
    def __init__(self, tx):
        self.tx = tx

    def get(self, venue_account_id):
        if venue_account_id != self.tx.scope.venue_account_id:
            raise ScopeMismatch('cross-account read')
        row = self.tx.execute('SELECT payload FROM ledger.account_bindings '
            'WHERE environment=%s AND account_id=%s AND product=%s', account_key(self.tx.scope)).fetchone()
        return None if row is None or row[0] is None else decode(t.VenueAccount, row[0])


class ProjectionRepository:
    def __init__(self, tx):
        self.tx = tx

    def book(self, scope):
        self.tx.require_scope(scope)
        book = self.tx._latest('book_snapshots', 'book', t.BookSnapshot)
        if book is None:
            raise MissingEvidence('no reconciled book snapshot; absence is not zero balance')
        return book

    @atomic_write
    def save_book(self, book: t.BookSnapshot, expected_sequence: int):
        self.tx.require_scope(book.scope)
        if book.sequence != expected_sequence + 1:
            self.tx.failed = True
            raise SequenceConflict('book sequence mismatch')
        self.tx._append('book_snapshots', 'book', book, expected_sequence)
        for index, balance in enumerate(book.balances):
            self.tx.execute('INSERT INTO ledger.book_balances(scope_id,sequence,balance_index,amount,currency) '
                'VALUES (%s,%s,%s,%s,%s)',
                (scope_id(book.scope),book.sequence,index,balance.amount,balance.currency))

    @atomic_write
    def append(self, update: t.ProjectionUpdate, expected_sequence: int):
        from .events import EventStore
        projection = update.projection
        self.tx.require_scope(projection.order.identity.scope)
        if projection.sequence != expected_sequence + 1:
            self.tx.failed = True
            raise SequenceConflict('projection sequence mismatch')
        self.tx._append('order_projections', projection.order.identity.logical_client_order_id,
                        projection, expected_sequence)
        for event in update.events:
            EventStore().append(self.tx, event, event.revision - 1)
