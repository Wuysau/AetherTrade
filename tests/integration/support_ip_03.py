"""Real PostgreSQL fixture, explicitly opt-in on an isolated Linux runner.

Provision a fresh disposable cluster/database externally. The fixture never
reads home credentials, installs packages, starts services or uses SQLite.
Missing provisioning is a skip/NOT_RUN, never acceptance success.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash
from trading_system.contracts.wire import ContractCodec
from trading_system.storage.events import EventStore
from trading_system.storage.models import scope_id
from trading_system.storage.uow import UnitOfWork

ROOT = Path(__file__).resolve().parents[2]
STAMP = '2026-10-20T06:39:03Z'


@pytest.fixture(scope='module')
def isolated_postgres():
    if os.environ.get('IP03_ISOLATED_DATABASE') != 'YES' or not os.environ.get('IP03_DATABASE_DSN'):
        pytest.skip('NOT_RUN: explicitly isolated PostgreSQL not supplied')
    if sys.platform != 'linux':
        pytest.skip('NOT_RUN: native transaction/role/crash verification requires isolated Linux')
    # Once opted in, missing driver or failed connection is an error, not a skip.
    import psycopg
    dsn = os.environ['IP03_DATABASE_DSN']
    with psycopg.connect(dsn) as conn:
        name = conn.execute('SELECT current_database()').fetchone()[0]
        if not name.startswith('ip03_test_'):
            pytest.fail('fixture requires a fresh ip03_test_* disposable database')
        if conn.execute("SELECT to_regnamespace('ledger')").fetchone()[0] is not None:
            pytest.fail('fixture refuses an existing ledger; provision a fresh database')
        conn.execute((ROOT / 'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'deploy/db/roles.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'migrations/0004_command_abort_guard.sql').read_text(encoding='utf-8'))
    return dsn


@dataclass(frozen=True)
class Counts:
    intent_count: int
    reservation_count: int
    command_count: int


class LedgerScenario:
    def __init__(self, dsn, suffix=None, seed=True):
        import psycopg
        self.dsn = dsn
        self.suffix = suffix or uuid4().hex
        self.connect_admin = lambda: psycopg.connect(dsn)
        self.scope = t.ScopeKey(environment=t.Environment.PAPER,
            venue_account_id='paper-' + self.suffix, product=t.Product.SPOT,
            instrument_uid='paper-binance-spot-btcusdt', venue_leg='BOTH')
        self.group = 'group-' + self.suffix
        if seed:
            with self.connect_admin() as conn:
                conn.execute('INSERT INTO ledger.collateral_mutex VALUES (%s,%s)', ('PAPER', self.group))
                conn.execute('INSERT INTO ledger.account_bindings VALUES (%s,%s,%s,%s,NULL)',
                             ('PAPER', self.scope.venue_account_id, 'SPOT', self.group))
        self.uow = UnitOfWork(self.connect_owner)
        codec = ContractCodec()
        directory = ROOT / 'tests/fixtures/contracts/examples'
        intent = json.loads((directory / 'trade-intent.paper.json').read_text(encoding='utf-8'))
        intent.update(intent_id='intent-' + self.suffix, venue_account_id=self.scope.venue_account_id,
                      collateral_group_id=self.group)
        self.intent = codec.decode('trade-intent', intent)
        command = json.loads((directory / 'execution-command.paper.json').read_text(encoding='utf-8'))
        command.update(command_id='command-' + self.suffix, intent_id=self.intent.intent_id,
                       venue_account_id=self.scope.venue_account_id,
                       venue_client_order_id='client-' + self.suffix)
        command['payload_hash'] = canonical_hash(command['canonical_payload'])
        self.command = codec.decode('execution-command', command)
        if seed:
            with self.connect_admin() as conn:
                conn.execute('INSERT INTO ledger.active_owners VALUES (%s,%s,%s,%s,%s,%s,1,1)',
                    ('PAPER',self.scope.venue_account_id,'SPOT',self.scope.instrument_uid,'BOTH',self.intent.owner_strategy_id))
        self.reservation = t.CapitalReservation(reservation_id='reservation-' + self.suffix,
            environment=t.Environment.PAPER, collateral_group_id=self.group,
            venue_account_id=self.scope.venue_account_id, owner_strategy_id=self.intent.owner_strategy_id,
            intent_id=self.intent.intent_id, asset='USDT', amount=Decimal('9007199254740993.00000001'),
            reflected_amount=Decimal('0'), state=t.ReservationState.HELD, created_at=STAMP,
            expires_at='2026-10-20T06:40:00Z', command_refs=(), evidence_watermark='unverified')

    def connect_owner(self):
        conn = self.connect_admin()
        conn.execute('SET ROLE ledger_owner')
        conn.commit()
        return conn

    def counts(self):
        with self.connect_admin() as conn:
            return Counts(*(conn.execute(f'SELECT count(*) FROM ledger.{table} WHERE scope_id=%s',
                          (scope_id(self.scope),)).fetchone()[0]
                            for table in ('intents','reservations','commands')))

    def write_bundle(self, tx):
        tx.intents.append(self.intent)
        tx.reservations.append(self.reservation, 0)
        tx.commands.append(self.command, 0)

    def fail_between_reservation_and_command(self):
        with pytest.raises(RuntimeError, match='injected'):
            with self.uow.transaction(self.scope) as tx:
                tx.intents.append(self.intent)
                tx.reservations.append(self.reservation, 0)
                raise RuntimeError('injected before command')
        return self.counts()

    def event(self, aggregate='aggregate', revision=1, *, fill=None):
        payload = fill or t.RiskLock(lock_id='lock-' + self.suffix, scope=self.scope,
            cause='TEST_ONLY', state='ACTIVE', permission=t.RiskPermission.NO_NEW_RISK,
            required_evidence=('reconciliation',), raised_at=STAMP)
        return t.DomainEvent(event_id=uuid4().hex, scope=self.scope, aggregate_id=aggregate,
                            revision=revision, occurred_at=STAMP, evidence=(), payload=payload)

    def fill(self, correction_of=None, native_id=None):
        return t.Fill(fill_id=uuid4().hex, venue='BINANCE', scope=self.scope,
            native_trade_id=native_id or uuid4().hex,
            order=t.OrderIdentity(scope=self.scope, logical_client_order_id='logical',
                native_client_order_id='native', venue_client_order_id='venue', venue_order_id='order'),
            quantity=t.Quantity(value=Decimal('0.001'), unit='BTC'),
            price=t.Price(value=Decimal('50000.00000001'), currency='USDT'),
            fees=(t.Money(amount=Decimal('0.00000001'), currency='USDT'),),
            occurred_at=STAMP, correction_of=correction_of,
            evidence=t.EvidenceRef(id='fixture', hash='a'*64, environment=t.Environment.PAPER,
                                   source='isolated-db-fixture', visible_at=STAMP))


@pytest.fixture
def ledger_scenario(isolated_postgres):
    return LedgerScenario(isolated_postgres)


def crash_child(suffix, phase):
    """Run only by the opt-in test parent; abrupt exit releases the DB socket."""
    scenario = LedgerScenario(os.environ['IP03_DATABASE_DSN'], suffix, seed=False)
    with scenario.uow.transaction(scenario.scope) as tx:
        scenario.write_bundle(tx)
        print('BEFORE_COMMIT:' + tx.transaction_id, flush=True)
        if phase == 'before':
            os._exit(73)
    print('AFTER_COMMIT:' + tx.transaction_id, flush=True)
    os._exit(74)


if __name__ == '__main__':
    if os.environ.get('IP03_ISOLATED_DATABASE') != 'YES' or sys.platform != 'linux':
        raise SystemExit('isolated Linux database required')
    crash_child(sys.argv[1], sys.argv[2])
