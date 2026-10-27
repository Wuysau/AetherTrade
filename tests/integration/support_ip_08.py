"""Offline input builders and opt-in real PostgreSQL/process probes.

These helpers never constitute platform or T/Q qualification evidence.
"""
from dataclasses import replace
from decimal import Decimal
import os
from pathlib import Path
import sys

import pytest

from integration.support_ip_03 import LedgerScenario
from trading_system.contracts import types as t
from trading_system.risk.locks import RiskLockService
from trading_system.risk.risk_day import RiskDayService
from trading_system.storage.uow import UnitOfWork

STAMP = '2026-10-20T00:00:00Z'
LATER = '2026-10-21T01:00:00Z'
ROOT = Path(__file__).resolve().parents[2]


def evidence(identity='evidence', at=STAMP):
    return t.EvidenceRef(id=identity, hash='a' * 64, environment=t.Environment.PAPER,
                         source='offline-fixture', visible_at=at)


def point(at, value):
    return t.EquityPoint(at=at, equity=t.Money(amount=Decimal(value), currency='USD'),
                         evidence=evidence(at, at))


def flow(value, *, at='2026-10-20T12:00:00Z', kind='EXTERNAL', pair=None, identity='flow'):
    return t.Cashflow(at=at, amount=t.Money(amount=Decimal(value), currency='USD'),
                     classification=kind, internal_pair_id=pair, evidence=evidence(identity, at))


class PnlCase:
    def day(self, start, end, net_external_flow):
        return RiskDayService(missing_costs=()).pnl(
            [point(STAMP, start), point('2026-10-21T00:00:00Z', end)],
            [flow(net_external_flow)], 'UTC')


@pytest.fixture
def pnl_case():
    return PnlCase()


@pytest.fixture(scope='module')
def ip08_postgres():
    if os.environ.get('IP08_ISOLATED_DATABASE') != 'YES' or not os.environ.get('IP08_DATABASE_DSN'):
        pytest.skip('NOT_RUN: explicitly isolated IP08 PostgreSQL not supplied')
    if sys.platform != 'linux':
        pytest.skip('NOT_RUN: process/database probes require isolated Linux')
    import psycopg
    dsn = os.environ['IP08_DATABASE_DSN']
    with psycopg.connect(dsn) as conn:
        if not conn.execute('SELECT current_database()').fetchone()[0].startswith('ip08_test_'):
            pytest.fail('requires disposable ip08_test_* database')
        if conn.execute("SELECT to_regnamespace('ledger')").fetchone()[0] is not None:
            pytest.fail('refuses existing ledger schema')
        conn.execute((ROOT / 'migrations/0001_business_ledger.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'deploy/db/roles.sql').read_text(encoding='utf-8'))
        conn.execute((ROOT / 'migrations/0002_risk_resolution_authority.sql').read_text(encoding='utf-8'))
    return dsn


@pytest.fixture
def risk_db(ip08_postgres):
    scenario = LedgerScenario(ip08_postgres)
    def connect_resolver():
        conn = scenario.connect_admin()
        conn.execute('SET ROLE ledger_risk_resolver')
        conn.commit()
        return conn
    # Isolated test administrator explicitly provisions this trusted identity.
    # Production composition must provide separate credentials; never SET ROLE
    # from an execution-owner login with resolver membership.
    scenario.resolver_uow = UnitOfWork(connect_resolver)
    return scenario


def resolution(lock, *, authorization_id=''):
    return t.ResolutionEvidence(lock_id=lock.lock_id, scope=lock.scope,
        evidence=(evidence('recovery', LATER),), authorization_id=authorization_id)


class OfflineVerifier:
    """Exact fixture receipt matching only, not an IP-04 authorization substitute."""
    def __init__(self, lock, supplied):
        self.lock = lock
        self.supplied = supplied

    def __call__(self, tx, lock, requirement, supplied):
        return (tx.scope == self.lock.scope and lock == self.lock
                and supplied == self.supplied and requirement in lock.required_evidence)


def crash_child(suffix):
    scenario = LedgerScenario(os.environ['IP08_DATABASE_DSN'], suffix, seed=False)
    service = RiskLockService(scenario.uow, clock=lambda: STAMP)
    for cause in ('DAILY_LOSS', 'TOTAL_DRAWDOWN', 'PROTECTION_GAP', 'MODEL_HEALTH'):
        service.raise_lock(scenario.scope, cause, evidence())
    os._exit(74)  # abrupt process termination after durable transactions


if __name__ == '__main__':
    if os.environ.get('IP08_ISOLATED_DATABASE') != 'YES' or sys.platform != 'linux':
        raise SystemExit('isolated Linux database required')
    crash_child(sys.argv[1])
