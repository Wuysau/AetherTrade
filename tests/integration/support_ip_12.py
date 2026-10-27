"""Synthetic inputs over real IP-03 PostgreSQL; no exchange or native proof.

The runner must explicitly provision a fresh ip12_test_* database on isolated
Linux and supply IP12_ISOLATED_DATABASE=YES and IP12_DATABASE_DSN. Missing
provisioning is NOT_RUN. No services/environments/credentials are discovered.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
from hashlib import sha256
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from integration.support_ip_03 import LedgerScenario, ROOT, STAMP
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_json, domain_payload
from trading_system.execution.evidence import EvidenceIngestor, EvidenceRecord
from trading_system.execution.fills import FillLedger, FillNotice
from trading_system.execution.projections import OrderProjector
from trading_system.storage.events import EventStore
from trading_system.storage.models import decode, scope_id, account_key
from trading_system.storage.uow import UnitOfWork

AUTO_NATIVE_ID = object()

@pytest.fixture(scope='module')
def ip12_database():
    if os.environ.get('IP12_ISOLATED_DATABASE') != 'YES' or not os.environ.get('IP12_DATABASE_DSN'):
        pytest.skip('NOT_RUN: explicit isolated IP12 PostgreSQL not supplied')
    if sys.platform != 'linux':
        pytest.skip('NOT_RUN: PostgreSQL probes require isolated Linux')
    import psycopg
    dsn = os.environ['IP12_DATABASE_DSN']
    with psycopg.connect(dsn) as conn:
        if not conn.execute('SELECT current_database()').fetchone()[0].startswith('ip12_test_'):
            pytest.fail('fresh dedicated ip12_test_* database required')
        if conn.execute("SELECT to_regnamespace('ledger')").fetchone()[0] is not None:
            pytest.fail('refusing existing ledger; provision a fresh database')
        conn.execute((ROOT / 'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'deploy/db/roles.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'migrations/0004_command_abort_guard.sql').read_text(encoding='utf-8'))
    return dsn


def initial_projection(scope=None):
    scope = scope or t.ScopeKey(environment=t.Environment.PAPER, venue_account_id='synthetic',
        product=t.Product.SPOT, instrument_uid='synthetic:BTCUSDT', venue_leg='BOTH')
    identity = t.OrderIdentity(scope=scope, logical_client_order_id='logical',
        native_client_order_id='native', venue_client_order_id='venue-client', venue_order_id='venue-order')
    return t.OrderProjection(order=t.OwnedOrderRef(identity=identity,
        owner_strategy_id='synthetic-owner', ownership_epoch=1),
        lifecycle_state=t.OrderLifecycle.INITIALIZED, pending_operation='CANCEL',
        command_certainty='UNKNOWN', outcome_provenance='NONE',
        filled_quantity=t.Quantity(value=Decimal(0), unit='BTC'),
        leaves_quantity=t.Quantity(value=Decimal(1), unit='BTC'), lifecycle_revision=0, sequence=0)


def domain_event(previous, payload, *, evidence=(), revision=None):
    return t.DomainEvent(event_id=uuid4().hex, scope=previous.order.identity.scope,
        aggregate_id='order:' + previous.order.identity.logical_client_order_id,
        revision=previous.sequence + 1 if revision is None else revision,
        occurred_at=STAMP, evidence=evidence, payload=payload)


@dataclass(frozen=True)
class LateFillResult:
    actual_quantity: str
    protection_recheck_required: bool


class EvidenceCase:
    def __init__(self, dsn, archive, *, ledger=None, scope=None, trade_id_scope='INSTRUMENT'):
        self.ledger = ledger or LedgerScenario(dsn)
        self.scope = scope or self.ledger.scope
        self.archive = Path(archive)
        self.archive.mkdir(parents=True, exist_ok=True)
        self.uow = self.ledger.uow
        self.ingestor = EvidenceIngestor(self.uow, self.scope, read_raw=self.read_raw)
        self.fills = FillLedger(self.uow, self.scope, trade_id_scope=trade_id_scope)
        self.projector = OrderProjector(quantity_at=self.fills.quantity_at,
                                        require_evidence=self.ingestor.require_evidence)
        self.projection = initial_projection(self.scope)

    def scoped(self, **changes):
        scope = replace(self.scope, **changes)
        with self.ledger.connect_admin() as conn:
            conn.execute('INSERT INTO ledger.collateral_mutex VALUES (%s,%s) '
                         'ON CONFLICT DO NOTHING', (str(scope.environment), self.ledger.group))
            conn.execute('INSERT INTO ledger.account_bindings VALUES (%s,%s,%s,%s,NULL) '
                         'ON CONFLICT DO NOTHING', (*account_key(scope), self.ledger.group))
        return EvidenceCase(self.ledger.dsn, self.archive, ledger=self.ledger,
                            scope=scope, trade_id_scope=self.fills.trade_id_scope)

    def read_raw(self, ref):
        path = Path(ref.id).resolve()
        if path.parent != self.archive.resolve():
            raise ValueError('fixture archive scope mismatch')
        return path.read_bytes()

    def raw(self, payload=b'{"synthetic":"accepted"}', *, source='synthetic-ws'):
        digest = sha256(payload).hexdigest()
        path = self.archive / (digest + '.raw')
        path.write_bytes(payload)
        ref = t.EvidenceRef(id=str(path.resolve()), hash=digest,
            environment=self.scope.environment, source=source, visible_at=STAMP)
        return t.RawVenueMessage(scope=self.scope, source=source, payload_hash=digest,
                                  raw_payload_ref=ref, received_at=STAMP)

    def venue_event(self, raw):
        return t.VenueEvent(event_id=uuid4().hex, source=raw.source, source_event_id='source-id',
            source_sequence='99', account_id=self.scope.venue_account_id,
            instrument_uid=self.scope.instrument_uid, order_refs=(self.projection.order.identity,),
            occurred_at=STAMP, received_at=STAMP, processed_at=STAMP,
            payload_hash=raw.payload_hash, raw_payload_ref=raw.raw_payload_ref)

    def fill(self, quantity='0.2', *, native_id=AUTO_NATIVE_ID, correction_of=None, fees=()):
        fill = replace(self.ledger.fill(), scope=self.scope, order=self.projection.order.identity,
            quantity=t.Quantity(value=Decimal(quantity), unit='BTC'), fees=fees,
            native_trade_id=uuid4().hex if native_id is AUTO_NATIVE_ID else native_id,
            correction_of=correction_of)
        # Explicit synthetic normalized data, never a decoder for a real venue.
        raw = self.raw(canonical_json({'fill': domain_payload(fill)}).encode())
        event = self.venue_event(raw)
        assert self.ingestor.ingest(event).accepted
        return replace(fill, evidence=raw.raw_payload_ref)

    def event(self, receipt):
        with self.ledger.connect_admin() as conn:
            row = conn.execute('SELECT payload FROM ledger.domain_events WHERE event_id=%s',
                               (receipt.event_ref.id,)).fetchone()
        return decode(t.DomainEvent, row[0])

    def project(self, event):
        update = self.projector.apply(event, self.projection)
        with self.uow.transaction(self.scope) as tx:
            tx.projections.append(update, self.projection.sequence)
        self.projection = update.projection
        return self.projection

    def snapshot(self, *, lifecycle=t.OrderLifecycle.CANCELED, filled='0', leaves='0',
                 lifecycle_revision=1, provenance='VENUE_EVENT', source='synthetic-ws'):
        raw = self.raw(canonical_json({'lifecycle': str(lifecycle), 'filled': filled,
            'leaves': leaves, 'revision': lifecycle_revision, 'nonce': uuid4().hex}).encode(), source=source)
        assert self.ingestor.ingest(self.venue_event(raw)).accepted
        payload = replace(self.projection, lifecycle_state=lifecycle,
            outcome_provenance=provenance, lifecycle_revision=lifecycle_revision,
            filled_quantity=t.Quantity(value=Decimal(filled), unit='BTC'),
            leaves_quantity=t.Quantity(value=Decimal(leaves), unit='BTC'),
            pending_operation='NONE', command_certainty='CONFIRMED')
        event = domain_event(self.projection, payload, evidence=(raw.raw_payload_ref,))
        with self.uow.transaction(self.scope) as tx:
            EventStore(received_at=STAMP).append(tx, event, self.projection.sequence)
        return self.project(event)

    def notice(self, fill):
        with self.uow.transaction(self.scope) as tx:
            return tx._latest('audit_events', 'ip12:fill-notice:' + fill.fill_id, FillNotice)

    def evidence_records(self):
        with self.ledger.connect_admin() as conn:
            rows = conn.execute('SELECT payload FROM ledger.venue_events WHERE scope_id=%s '
                                "ORDER BY (payload->>'aggregate_sequence')::bigint",
                                (scope_id(self.scope),)).fetchall()
        return tuple(decode(EvidenceRecord, row[0]) for row in rows)

    def seed_unknown_command(self):
        """Create the durable offline boundary without invoking any transport."""
        from trading_system.execution.commands import CommandRepository
        with self.uow.transaction(self.scope) as tx:
            self.ledger.write_bundle(tx)
        with self.uow.transaction(self.scope) as tx:
            # The permit is deliberately discarded; the probe never sends.
            tx.commands.mark_may_have_sent(tx, self.ledger.command.command_id, 'DURABLE_READY')
        with self.uow.transaction(self.scope) as tx:
            CommandRepository(tx).recover_unknown(self.ledger.command.command_id, STAMP)
        identity = replace(self.projection.order.identity,
            logical_client_order_id=self.ledger.command.logical_client_order_id,
            venue_client_order_id=self.ledger.command.venue_client_order_id)
        self.projection = replace(self.projection, pending_operation='SUBMIT',
            order=replace(self.projection.order, identity=identity,
                          owner_strategy_id=self.ledger.intent.owner_strategy_id))

    def assert_unknown_still_held(self):
        from trading_system.execution.commands import CommandRepository
        with self.uow.transaction(self.scope) as tx:
            assert CommandRepository(tx).get(self.ledger.command.command_id).state == 'UNKNOWN'
            assert tx.reservations.for_intent(self.ledger.intent.intent_id)[0].state == t.ReservationState.HELD
            assert any(lock.cause == 'ORDER_OUTCOME_UNKNOWN' for lock in tx.risk_locks.active(self.scope))

    def cancel_then_late_fill(self, quantity):
        self.snapshot()
        fill = self.fill(quantity)
        receipt = self.fills.record(fill)
        self.project(self.event(receipt))
        return LateFillResult(actual_quantity=format(self.projection.filled_quantity.value, 'f'),
            protection_recheck_required=self.notice(fill).protection_recheck_required)

    def count(self, table):
        if table not in ('fills', 'fill_fees', 'domain_events', 'outbox', 'audit_events',
                         'venue_events', 'commands', 'reservations', 'order_projections'):
            raise ValueError('unsupported fixture table')
        with self.ledger.connect_admin() as conn:
            if table == 'fill_fees':
                return conn.execute('SELECT count(*) FROM ledger.fill_fees fee JOIN ledger.fills f '
                    'ON f.record_id=fee.fill_id WHERE f.scope_id=%s', (scope_id(self.scope),)).fetchone()[0]
            return conn.execute(f'SELECT count(*) FROM ledger.{table} WHERE scope_id=%s',
                                (scope_id(self.scope),)).fetchone()[0]


@pytest.fixture
def evidence_case(ip12_database, tmp_path):
    return EvidenceCase(ip12_database, tmp_path)


class FailCommitConnection:
    """Real transaction; inject a failure before commit, without mocking SQL."""
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        raise OSError('injected commit failure')


def failing_uow(case):
    return UnitOfWork(lambda: FailCommitConnection(case.ledger.connect_owner()))
