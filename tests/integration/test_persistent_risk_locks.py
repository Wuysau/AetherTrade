"""Unrun IP-08 probes; local assertions do not change acceptance/Q statuses."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from decimal import Decimal
import os
import subprocess
import sys

import pytest

from integration.support_ip_08 import (
    ip08_postgres, risk_db, pnl_case, evidence, point, flow, resolution,
    OfflineVerifier, ROOT, STAMP, LATER,
)
from trading_system.contracts import types as t
from trading_system.risk.locks import RiskLockService
from trading_system.risk.risk_day import RiskDayService, day_bounds, Settlement
from trading_system.storage.models import scope_id


def test_cashflow_adjusted_pnl(pnl_case):
    assert pnl_case.day('100', '150', '40').trading_pnl == '10'
    assert pnl_case.day('100', '70', '-40').trading_pnl == '10'


def test_internal_transfers_and_fees():
    series = [point(STAMP, '100'), point('2026-10-21T00:00:00Z', '99')]
    flows = [flow('-30', kind='INTERNAL', pair='transfer', identity='out'),
             flow('30', kind='INTERNAL', pair='transfer', identity='in'),
             flow('-1', kind='FEE', identity='fee')]
    service = RiskDayService(missing_costs=())
    assert service.pnl(series, flows, 'UTC').trading_pnl == '-1'
    assert service.pnl(series, flows[1:], 'UTC').trading_pnl is None
    assert RiskDayService().pnl(series, flows, 'UTC').adjusted_pnl is None


@pytest.mark.parametrize('invalid', ['duplicate', 'currency', 'outside', 'classification', 'environment'])
def test_invalid_cashflows_fail_closed(invalid):
    series = [point(STAMP, '100'), point('2026-10-21T00:00:00Z', '150')]
    f = flow('40')
    flows = [f]
    if invalid == 'duplicate':
        flows *= 2
    elif invalid == 'currency':
        flows = [replace(f, amount=t.Money(amount=Decimal('40'), currency='EUR'))]
    elif invalid == 'outside':
        flows = [replace(f, at=STAMP)]
    elif invalid == 'classification':
        flows = [replace(f, classification='UNKNOWN')]
    else:
        flows = [replace(f, evidence=replace(f.evidence, environment=t.Environment.TESTNET))]
    with pytest.raises(ValueError):
        RiskDayService(missing_costs=()).pnl(series, flows, 'UTC')


def test_precision_and_invalid_equity_boundaries():
    service = RiskDayService(missing_costs=())
    series = [point(STAMP, '999999999999999999999999999999.00000001'),
              point('2026-10-21T00:00:00Z', '999999999999999999999999999999.00000002')]
    assert service.pnl(series, [], 'UTC').trading_pnl == '0.00000001'
    for invalid in (series[::-1], [series[0], series[0]],
                    [replace(series[0], at='2026-10-20T01:00:00Z'), series[1]]):
        with pytest.raises(ValueError):
            service.pnl(invalid, [], 'UTC')


@pytest.mark.parametrize('day,hours', [('2026-03-08', 23), ('2026-11-01', 25)])
def test_dst_risk_day_boundaries(day, hours):
    start, end = day_bounds(day, 'America/New_York')
    assert (end - start).total_seconds() == hours * 3600
    assert start.utcoffset().total_seconds() == end.utcoffset().total_seconds() == 0


@pytest.mark.parametrize('day', ['2026-03-08', '2026-11-01'])
def test_dst_risk_day(risk_db, day):
    start, end = day_bounds(day, 'America/New_York')
    series = [point(start.isoformat(), '100'), point(end.isoformat(), '105')]
    def settle():
        return RiskDayService(missing_costs=()).settle(risk_db.uow, risk_db.scope, series, [], 'America/New_York')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: settle(), range(2)))
    assert results[0] == results[1]
    with risk_db.uow.transaction(risk_db.scope) as tx:
        saved = tx._latest('audit_events', 'risk-day:America/New_York:' + day, Settlement)
        assert saved.start_at.endswith('+00:00') and saved.end_at.endswith('+00:00')
        assert tx.execute('SELECT count(*) FROM ledger.audit_events WHERE scope_id=%s',
                          (scope_id(risk_db.scope),)).fetchone()[0] == 2
    with pytest.raises(ValueError, match='silently revised'):
        RiskDayService(missing_costs=()).settle(risk_db.uow, risk_db.scope,
            [series[0], point(end.isoformat(), '106')], [], 'America/New_York')


def test_multi_locks_survive_restart(risk_db):
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT / 'tests')))
    completed = subprocess.run([sys.executable, '-m', 'integration.support_ip_08', risk_db.suffix],
                               env=env, cwd=ROOT, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 74, completed.stderr
    service = RiskLockService(risk_db.uow, clock=lambda: LATER)
    locks = service.active(risk_db.scope)
    assert {lock.cause for lock in locks} == {'DAILY_LOSS', 'TOTAL_DRAWDOWN', 'PROTECTION_GAP', 'MODEL_HEALTH'}
    model = next(lock for lock in locks if lock.cause == 'MODEL_HEALTH')
    supplied = resolution(model)
    service = RiskLockService(risk_db.uow, clock=lambda: LATER, verify=OfflineVerifier(model, supplied))
    assert service.resolve(model.lock_id, supplied).resolved
    assert {lock.cause for lock in service.active(risk_db.scope)} == {'DAILY_LOSS', 'TOTAL_DRAWDOWN', 'PROTECTION_GAP'}
    assert service.allowed_actions(risk_db.scope) == {'EXIT', 'RECONCILE'}


def test_drawdown_across_midnight(risk_db):
    service = RiskLockService(risk_db.uow, clock=lambda: STAMP)
    lock = service.raise_lock(risk_db.scope, 'TOTAL_DRAWDOWN', evidence())
    later = RiskLockService(risk_db.uow, clock=lambda: LATER)
    assert later.active(risk_db.scope) == (lock,)
    assert not later.resolve(lock.lock_id, resolution(lock, authorization_id='arbitrary-string')).resolved
    supplied = resolution(lock, authorization_id='offline-reviewed-new-authorization')
    verified = RiskLockService(risk_db.uow, clock=lambda: LATER, verify=OfflineVerifier(lock, supplied))
    assert verified.resolve(lock.lock_id, supplied).resolved
    assert verified.resolve(lock.lock_id, supplied).resolved  # idempotent, no duplicate writes
    assert later.active(risk_db.scope) == ()


@pytest.mark.parametrize('fault', ['scope', 'id', 'stale', 'environment', 'hash', 'future', 'missing'])
def test_resolution_negative_evidence(risk_db, fault):
    service = RiskLockService(risk_db.uow, clock=lambda: STAMP)
    lock = service.raise_lock(risk_db.scope, 'PROTECTION_GAP', evidence())
    supplied = resolution(lock)
    verifier = OfflineVerifier(lock, supplied)
    if fault == 'scope':
        supplied = replace(supplied, scope=replace(lock.scope, instrument_uid='other'))
    elif fault == 'id':
        supplied = replace(supplied, lock_id='wrong')
    elif fault == 'missing':
        supplied = replace(supplied, evidence=())
    else:
        ref = supplied.evidence[0]
        changes = {'stale': {'visible_at': '2026-10-19T00:00:00Z'},
                   'environment': {'environment': t.Environment.TESTNET},
                   'hash': {'hash': 'invalid'}, 'future': {'visible_at': '2027-01-01T00:00:00Z'}}
        supplied = replace(supplied, evidence=(replace(ref, **changes[fault]),))
    later = RiskLockService(risk_db.uow, clock=lambda: LATER, verify=verifier)
    assert not later.resolve(lock.lock_id, supplied).resolved
    assert service.active(risk_db.scope) == (lock,)


def test_unknown_cause_and_intersection(risk_db):
    service = RiskLockService(risk_db.uow, clock=lambda: STAMP)
    lock = service.raise_lock(risk_db.scope, 'UNKNOWN_CAUSE', evidence())
    supplied = resolution(lock)
    later = RiskLockService(risk_db.uow, clock=lambda: LATER, verify=OfflineVerifier(lock, supplied))
    assert not later.resolve(lock.lock_id, supplied).resolved
    assert later.allowed_actions(risk_db.scope) == {'RECONCILE'}


def test_durable_hwm_and_no_automatic_lock_release(risk_db):
    lock = RiskLockService(risk_db.uow, clock=lambda: STAMP).raise_lock(
        risk_db.scope, 'TOTAL_DRAWDOWN', evidence())
    first = [point(STAMP, '100'), point('2026-10-20T15:00:00Z', '160'), point('2026-10-21T00:00:00Z', '150')]
    result = RiskDayService(missing_costs=()).settle(risk_db.uow, risk_db.scope, first, [flow('40')], 'UTC')
    assert result.high_water_mark.amount == Decimal('120')
    second = [first[-1], point('2026-10-22T00:00:00Z', '140')]
    result = RiskDayService(missing_costs=()).settle(risk_db.uow, risk_db.scope, second, [], 'UTC')
    assert result.high_water_mark.amount == Decimal('120')
    assert result.trading_pnl == '-10'
    assert RiskLockService(risk_db.uow).active(risk_db.scope) == (lock,)


def test_resolution_failure_rolls_back(risk_db):
    service = RiskLockService(risk_db.uow, clock=lambda: STAMP)
    lock = service.raise_lock(risk_db.scope, 'MODEL_HEALTH', evidence())
    def fail(tx, lock, requirement, supplied):
        raise RuntimeError('injected verifier failure')
    with pytest.raises(RuntimeError, match='injected'):
        RiskLockService(risk_db.uow, clock=lambda: LATER, verify=fail).resolve(lock.lock_id, resolution(lock))
    assert service.active(risk_db.scope) == (lock,)


def test_resolution_event_failure_is_atomic(risk_db, monkeypatch):
    from trading_system.storage.events import EventStore
    service = RiskLockService(risk_db.uow, clock=lambda: STAMP)
    lock = service.raise_lock(risk_db.scope, 'MODEL_HEALTH', evidence())
    supplied = resolution(lock)
    original = EventStore.append
    def fail_after_event(self, tx, event, expected_sequence):
        original(self, tx, event, expected_sequence)
        raise RuntimeError('injected after event write')
    monkeypatch.setattr(EventStore, 'append', fail_after_event)
    with pytest.raises(RuntimeError, match='after event write'):
        RiskLockService(risk_db.uow, clock=lambda: LATER,
            verify=OfflineVerifier(lock, supplied)).resolve(lock.lock_id, supplied)
    assert service.active(risk_db.scope) == (lock,)
    with risk_db.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.domain_events WHERE scope_id=%s',
                            (scope_id(risk_db.scope),)).fetchone()[0] == 1


def test_concurrent_resolution_is_once(risk_db):
    lock = RiskLockService(risk_db.uow, clock=lambda: STAMP).raise_lock(
        risk_db.scope, 'MODEL_HEALTH', evidence())
    supplied = resolution(lock)
    def resolve():
        return RiskLockService(risk_db.uow, clock=lambda: LATER,
            verify=OfflineVerifier(lock, supplied)).resolve(lock.lock_id, supplied)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(result.resolved for result in pool.map(lambda _: resolve(), range(2)))
    with risk_db.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.risk_locks WHERE scope_id=%s',
                            (scope_id(risk_db.scope),)).fetchone()[0] == 2


def test_incomplete_costs_do_not_clear_current_risk(risk_db):
    service = RiskLockService(risk_db.uow, clock=lambda: STAMP)
    service.raise_lock(risk_db.scope, 'MODEL_HEALTH', evidence())
    series = [point(STAMP, '100'), point('2026-10-21T00:00:00Z', '150')]
    report = RiskDayService().pnl(series, [flow('40')], 'UTC')
    assert report.trading_pnl is None and report.high_water_mark is None
    assert 'MANAGE' in service.allowed_actions(risk_db.scope)
    assert 'NEW_RISK' not in service.allowed_actions(risk_db.scope)
    with pytest.raises(ValueError, match='incomplete history'):
        RiskDayService().settle(risk_db.uow, risk_db.scope, series, [flow('40')], 'UTC')


def test_risk_day_gap_and_zone_changes_require_review(risk_db):
    service = RiskDayService(missing_costs=())
    service.settle(risk_db.uow, risk_db.scope,
        [point(STAMP, '100'), point('2026-10-21T00:00:00Z', '110')], [], 'UTC')
    with pytest.raises(ValueError, match='discontinuity'):
        service.settle(risk_db.uow, risk_db.scope,
            [point('2026-10-22T00:00:00Z', '110'), point('2026-10-23T00:00:00Z', '115')], [], 'UTC')
    with pytest.raises(ValueError, match='discontinuity'):
        service.settle(risk_db.uow, risk_db.scope,
            [point('2026-10-21T00:00:00Z', '110'), point('2026-10-22T00:00:00Z', '115')], [], 'Etc/UTC')
