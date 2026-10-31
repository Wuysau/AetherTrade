"""Real isolated PostgreSQL monitor probes; local synthetic PAPER evidence."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import json
import os
import subprocess
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from integration.support_ip_08 import ip08_postgres
from integration.test_group_daily_locks import (
    eth_case, local_action_proof, private_daily_review_archive, resolver_uow,
    verify_daily_review, DailyReview,
)
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, utc_timestamp
from trading_system.contracts.wire import ContractCodec
from trading_system.risk.daily_loss import (
    COVERAGE_STREAMS, DailyLossBreach, DailyLossCoverage, DailyLossMonitor,
    DailyLossPolicyBinding, DailyLossReport, RiskAccount, digest, policy_binding_key,
)
from trading_system.risk.locks import RiskLockService
from trading_system.risk.pnl import exact_sum
from trading_system.risk.risk_day import day_bounds
from trading_system.storage.models import decode, encode, scope_id
from trading_system.storage.uow import UnitOfWork
from unit.support_ip_06 import NOW, money, price, quantity


def evidence(identity, at):
    return t.EvidenceRef(id=identity, hash=canonical_hash({'identity': identity, 'at': at}),
        environment=t.Environment.PAPER, source='protected-synthetic-equity-archive', visible_at=at)


@pytest.fixture(scope='module')
def report_archive(ip08_postgres):
    import psycopg
    with psycopg.connect(ip08_postgres) as conn:
        conn.execute('CREATE TABLE ledger.test_daily_loss_reports (environment text NOT NULL,group_id text NOT NULL,'
            'sequence bigint NOT NULL,payload jsonb NOT NULL,PRIMARY KEY(environment,group_id,sequence))')
        conn.execute('REVOKE ALL ON ledger.test_daily_loss_reports FROM PUBLIC,ledger_owner,ledger_control,ledger_decision')
        conn.execute('GRANT SELECT ON ledger.test_daily_loss_reports TO ledger_owner,ledger_risk_resolver')
        conn.execute('CREATE TRIGGER immutable_history BEFORE UPDATE OR DELETE OR TRUNCATE '
            'ON ledger.test_daily_loss_reports FOR EACH STATEMENT EXECUTE FUNCTION ledger.reject_mutation()')


def archive_reader(tx, scope, policy, members):
    row = tx.execute('SELECT payload FROM ledger.test_daily_loss_reports WHERE environment=%s AND group_id=%s '
        'ORDER BY sequence DESC LIMIT 1', (str(scope.environment), tx.group_id)).fetchone()
    if row is None:
        return None
    report = decode(DailyLossReport, row[0])
    # The server reader attests this exact transaction/request to group-level
    # immutable source data. No request can write this protected archive.
    return replace(report, transaction_id=tx.transaction_id, scope=scope)


class MonitorCase:
    def __init__(self, dsn, *, now=NOW, zone='Asia/Shanghai', closing='97.9'):
        self.case = eth_case(dsn, sell=True)
        self.scope, self.uow = self.case.scope, self.case.ledger.uow
        self.now = now
        self.ns = 1_000_000_000
        self.sequence = 0
        self.policy = replace(self.case.case.policy, policy_id='monitor-policy-' + self.case.ledger.suffix,
            daily_loss_fraction='0.02', risk_day_timezone=zone)
        self.members = (RiskAccount(environment=self.scope.environment,
            venue_account_id=self.scope.venue_account_id, product=self.scope.product),)
        self.binding = DailyLossPolicyBinding(environment=self.scope.environment,
            collateral_group_id=self.case.group, policy_scope=self.case.btc_scope,
            policy_id=self.policy.policy_id, policy_revision=self.policy.revision,
            policy_hash=digest(self.policy), evidence=evidence('authorized-policy', now))
        with self.case.ledger.connect_admin() as conn:
            conn.execute('INSERT INTO ledger.policies(scope_id,record_id,sequence,payload,transaction_id) '
                'VALUES(%s,%s,1,%s::jsonb,%s)', (scope_id(self.binding.policy_scope), self.policy.policy_id,
                    encode(self.policy), 'policy-authority-fixture'))
            conn.execute('INSERT INTO ledger.collateral_groups(scope_id,record_id,sequence,payload,transaction_id) '
                'VALUES(%s,%s,1,%s::jsonb,%s)', (scope_id(self.binding.policy_scope),
                    policy_binding_key(self.scope.environment, self.case.group), encode(self.binding),
                    'policy-authority-fixture'))
        self.report = self.make_report(closing)
        self.save(self.report)
        self.monitor = DailyLossMonitor(self.uow, read_report=archive_reader, clock=self.clock)

    def clock(self):
        return t.ClockReading(utc_now=self.now, monotonic_ns=self.ns, clock_health='HEALTHY')

    def make_report(self, closing='97.9', *, intermediate=(), flows=()):
        day = utc_timestamp(self.now).astimezone(ZoneInfo(self.policy.risk_day_timezone)).date().isoformat()
        start, _ = day_bounds(day, self.policy.risk_day_timezone)
        prefix = uuid4().hex
        points = [(start.isoformat(), '100'), *intermediate, (self.now, closing)]
        series = tuple(t.EquityPoint(at=at, equity=money(value), evidence=evidence(prefix + '-eq-' + str(i), at))
                       for i, (at, value) in enumerate(points))
        return DailyLossReport(transaction_id='archive', scope=self.scope, collateral_group_id=self.case.group,
            policy_hash=digest(self.policy), members=self.members, series=series, cashflows=tuple(flows),
            coverage=tuple(DailyLossCoverage(stream=stream, members=self.members, start_at=start.isoformat(),
                through_at=self.now, missing=(), evidence=evidence(prefix + '-' + stream, self.now))
                for stream in COVERAGE_STREAMS), captured_at=self.now,
            valid_until=(utc_timestamp(self.now) + timedelta(seconds=30)).isoformat())

    def save(self, report):
        self.report = report
        self.sequence += 1
        with self.case.ledger.connect_admin() as conn:
            conn.execute('INSERT INTO ledger.test_daily_loss_reports VALUES(%s,%s,%s,%s::jsonb)',
                (str(self.scope.environment), self.case.group, self.sequence, encode(report)))

    def assess(self, scope=None):
        return self.monitor.assess(scope or self.scope)

    def locks(self):
        return RiskLockService(self.uow).active(self.scope)


def test_default_monitor_denies_new_risk_without_trusted_reader(ip08_postgres):
    case = eth_case(ip08_postgres)
    result = DailyLossMonitor(case.ledger.uow).assess(case.scope)
    assert not result.new_risk_allowed
    assert case.count('risk_locks') == 0


def test_observed_threshold_commits_original_lock_event_and_evidence(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres, closing='98')
    result = c.assess()
    assert not result.new_risk_allowed and result.rejection_codes == ('DAILY_LOSS_LIMIT_BREACHED',)
    assert result.threshold == result.observed_loss == money('2')
    assert len(result.locks) == 1
    original = result.locks[0]
    assert c.locks() == (original,)
    assert RiskLockService(c.uow).active(c.case.btc_scope) == (original,)
    with c.uow.transaction(c.scope) as tx:
        breach = tx._latest('audit_events', 'daily-loss-breach:' + original.lock_id, DailyLossBreach)
        assert breach.report_hash == digest(breach.report)
        assert breach.threshold == money('2') and breach.crossed_at == NOW
    with c.case.ledger.connect_admin() as conn:
        row = conn.execute('SELECT l.transaction_id, count(e.event_id),count(o.transaction_id) '
            'FROM ledger.risk_locks l JOIN ledger.domain_events e ON e.transaction_id=l.transaction_id '
            'JOIN ledger.transaction_observations o ON o.transaction_id=l.transaction_id '
            'WHERE l.record_id=%s GROUP BY l.transaction_id', (original.lock_id,)).fetchone()
    assert row is not None and row[1:] == (1, 1)  # independently visible committed bundle


def test_any_observed_breach_latches_even_when_latest_value_has_rebounded(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres, closing='110')
    at = (utc_timestamp(NOW) - timedelta(seconds=30)).isoformat()
    c.save(c.make_report('110', intermediate=((at, '97'),)))
    result = c.assess()
    assert result.latest_pnl == money('10') and result.observed_loss == money('3')
    assert not result.new_risk_allowed and len(c.locks()) == 1


def test_restart_recovery_and_risk_day_rollover_never_clear_lock(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres)
    original = c.assess().locks[0]
    c.now = (utc_timestamp(NOW) + timedelta(days=1)).isoformat()
    c.save(c.make_report('120'))
    assert not c.assess(c.case.btc_scope).new_risk_allowed
    assert c.locks() == (original,)
    # Actual new Python process, fresh connection and default reader composition.
    code = """import json,os,psycopg
from trading_system.storage.models import decode
from trading_system.storage.uow import UnitOfWork
from trading_system.contracts import types as t
from trading_system.risk.daily_loss import DailyLossMonitor
def connect():
    c=psycopg.connect(os.environ['IP08_DATABASE_DSN']); c.execute('SET ROLE ledger_owner'); c.commit(); return c
scope=decode(t.ScopeKey,json.loads(os.environ['DAILY_MONITOR_SCOPE']))
r=DailyLossMonitor(UnitOfWork(connect)).assess(scope)
print(json.dumps({'allowed':r.new_risk_allowed,'ids':[l.lock_id for l in r.locks]}))
"""
    env = dict(os.environ, DAILY_MONITOR_SCOPE=encode(c.case.btc_scope))
    restarted = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True, timeout=15)
    assert restarted.returncode == 0, restarted.stderr
    assert json.loads(restarted.stdout) == {'allowed': False, 'ids': [original.lock_id]}


@pytest.mark.parametrize('day,hours', [('2026-03-08', 23), ('2026-11-01', 25)])
def test_configured_iana_dst_opening_is_exact(ip08_postgres, report_archive, day, hours):
    start, end = day_bounds(day, 'America/New_York')
    assert (end - start).total_seconds() == hours * 3600
    c = MonitorCase(ip08_postgres, zone='America/New_York', now=(start + timedelta(hours=2)).isoformat())
    assert c.report.series[0].at == start.isoformat()
    result = c.assess()
    assert result.risk_day == day and result.observed_loss == money('2.1')
    assert len(result.locks) == 1


@pytest.mark.parametrize('external,closing,expected', [('50', '148.1', False), ('-40', '57.9', True)])
def test_external_cashflow_adjustment_controls_actual_trigger(ip08_postgres, report_archive, external, closing, expected):
    c = MonitorCase(ip08_postgres)
    at = (utc_timestamp(NOW) - timedelta(hours=1)).isoformat()
    flow = t.Cashflow(at=at, amount=money(external), classification='EXTERNAL', internal_pair_id=None,
                      evidence=evidence('external', at))
    c.save(c.make_report(closing, flows=(flow,)))
    result = c.assess()
    assert (len(result.locks) == 1) is expected
    assert result.new_risk_allowed is (not expected)


def test_fees_and_funding_already_in_equity_are_not_subtracted_twice(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres)
    at = (utc_timestamp(NOW) - timedelta(hours=1)).isoformat()
    flows = tuple(t.Cashflow(at=at, amount=money('-0.5'), classification=kind,
        internal_pair_id=None, evidence=evidence(kind, at)) for kind in ('FEE', 'FUNDING'))
    c.save(c.make_report('98.5', flows=flows))
    result = c.assess()
    assert result.new_risk_allowed and result.latest_pnl == money('-1.5') and not c.locks()


def test_concurrent_btc_eth_breach_creates_one_original_group_lock(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(c.assess, (c.scope, c.case.btc_scope)))
    ids = {lock.lock_id for result in results for lock in result.locks}
    assert len(ids) == 1 and all(not result.new_risk_allowed for result in results)
    assert {result.locks[0].scope for result in results} <= {c.scope, c.case.btc_scope}
    with c.case.ledger.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.risk_locks WHERE record_id=%s', (next(iter(ids)),)).fetchone()[0] == 1


@pytest.mark.parametrize('fault', ['missing_opening', 'stale', 'future_point', 'future_capture',
    'future_evidence', 'invalid_hash', 'unknown_cost', 'missing_funding', 'wrong_members',
    'wrong_policy', 'wrong_currency', 'zero_opening', 'duplicate_points', 'incomplete_internal'])
def test_unknown_or_unusable_evidence_denies_without_fabricated_breach(ip08_postgres, report_archive, fault):
    c = MonitorCase(ip08_postgres)
    r = c.report
    if fault == 'missing_opening':
        r = replace(r, series=(replace(r.series[0], at=(utc_timestamp(r.series[0].at) + timedelta(seconds=1)).isoformat()), r.series[-1]))
    elif fault == 'stale':
        c.now = (utc_timestamp(NOW) + timedelta(seconds=61)).isoformat()
    elif fault == 'future_point':
        r = replace(r, series=(*r.series[:-1], replace(r.series[-1], at=(utc_timestamp(NOW) + timedelta(seconds=1)).isoformat())))
    elif fault == 'future_capture':
        r = replace(r, captured_at=(utc_timestamp(NOW) + timedelta(seconds=1)).isoformat())
    elif fault == 'future_evidence':
        r = replace(r, series=(r.series[0], replace(r.series[-1], evidence=replace(r.series[-1].evidence,
            visible_at=(utc_timestamp(NOW) + timedelta(seconds=1)).isoformat()))))
    elif fault == 'invalid_hash':
        r = replace(r, series=(r.series[0], replace(r.series[-1], evidence=replace(r.series[-1].evidence, hash='invalid'))))
    elif fault == 'unknown_cost':
        r = replace(r, coverage=(replace(r.coverage[0], missing=('UNRECONCILED_FUNDING',)), *r.coverage[1:]))
    elif fault == 'missing_funding':
        r = replace(r, coverage=tuple(row for row in r.coverage if row.stream != 'FUNDING'))
    elif fault == 'wrong_members':
        r = replace(r, members=())
    elif fault == 'wrong_policy':
        r = replace(r, policy_hash='0' * 64)
    elif fault == 'wrong_currency':
        r = replace(r, series=tuple(replace(p, equity=replace(p.equity, currency='USD')) for p in r.series))
    elif fault == 'zero_opening':
        r = replace(r, series=(replace(r.series[0], equity=money('0')), r.series[-1]))
    elif fault == 'duplicate_points':
        r = replace(r, series=(r.series[0], r.series[0], r.series[-1]))
    else:
        at = (utc_timestamp(NOW) - timedelta(hours=1)).isoformat()
        r = replace(r, cashflows=(t.Cashflow(at=at, amount=money('-1'), classification='INTERNAL',
            internal_pair_id='missing-other-leg', evidence=evidence('internal', at)),))
    c.save(r)
    result = c.assess()
    assert not result.new_risk_allowed and result.rejection_codes
    assert not c.locks() and c.case.count('risk_locks') == 0


@pytest.mark.parametrize('fault', ['utc', 'monotonic', 'unhealthy'])
def test_reader_delay_refreshes_clock_before_trigger(ip08_postgres, report_archive, fault):
    c = MonitorCase(ip08_postgres)
    def delayed(*args):
        report = archive_reader(*args)
        if fault == 'utc':
            c.now = (utc_timestamp(NOW) + timedelta(seconds=61)).isoformat()
        elif fault == 'monotonic':
            c.ns += 61_000_000_000
        else:
            c.monitor.clock = lambda: t.ClockReading(utc_now=NOW, monotonic_ns=1, clock_health='UNKNOWN')
        return report
    c.monitor.read_report = delayed
    result = c.assess()
    assert not result.new_risk_allowed and not c.locks()


def test_real_database_transaction_loss_cannot_return_a_committed_latch(ip08_postgres, report_archive, monkeypatch):
    import psycopg
    from trading_system.storage.events import EventStore
    c = MonitorCase(ip08_postgres)
    append = EventStore.append
    def terminate_after_event(self, tx, event, expected):
        result = append(self, tx, event, expected)
        if event.scope == c.scope and event.payload.cause == 'DAILY_LOSS':
            with c.case.ledger.connect_admin() as conn:
                assert conn.execute('SELECT pg_terminate_backend(%s)', (tx._connection.info.backend_pid,)).fetchone()[0]
        return result
    monkeypatch.setattr(EventStore, 'append', terminate_after_event)
    with pytest.raises(psycopg.OperationalError):
        c.assess()
    assert not c.locks() and c.case.count('risk_locks') == 0
    with c.case.ledger.connect_admin() as conn:
        assert conn.execute('SELECT count(*) FROM ledger.domain_events WHERE scope_id=%s', (scope_id(c.scope),)).fetchone()[0] == 0


def test_authorized_source_resolution_does_not_invent_review_watermark_or_clear_other_causes(
        ip08_postgres, report_archive, private_daily_review_archive):
    c = MonitorCase(ip08_postgres)
    other = RiskLockService(c.uow, clock=lambda: NOW).raise_lock(c.scope, 'PROTECTION_GAP', evidence('gap', NOW))
    original = next(lock for lock in c.assess().locks if lock.cause == 'DAILY_LOSS')
    supplied = t.ResolutionEvidence(lock_id=original.lock_id, scope=original.scope,
        evidence=(evidence('authorized-review', NOW),), authorization_id='review-authorization')
    resolver = resolver_uow(c.case)
    service = RiskLockService(c.uow, clock=lambda: NOW, verify=verify_daily_review, resolver_uow=resolver)
    assert not service.resolve(original.lock_id, supplied).resolved
    with resolver.transaction(original.scope) as tx:
        tx.execute('INSERT INTO ledger.test_daily_reviews VALUES(%s,%s,%s::jsonb)',
            (scope_id(original.scope), original.lock_id, encode(DailyReview(original,
                supplied.authorization_id, supplied.evidence[0]))))
    assert service.resolve(original.lock_id, supplied).resolved
    assert c.locks() == (other,)
    c.now = (utc_timestamp(NOW) + timedelta(seconds=1)).isoformat()
    c.ns += 1_000_000_000
    c.save(c.make_report('110', intermediate=((NOW, '97.9'),)))
    result = c.assess()
    assert result.latest_pnl == money('10') and not result.new_risk_allowed
    assert result.rejection_codes == ('DAILY_LOSS_REVIEW_WATERMARK_REQUIRED',)
    assert result.locks == (other,) and c.case.count('risk_locks') == 3  # 1 gap, original active+resolved


def test_monitored_lock_preserves_a3_partial_protection_and_owned_exit(ip08_postgres, report_archive):
    from trading_system.risk.locked_actions import LockedActionVerifier
    c = MonitorCase(ip08_postgres)
    c.assess()
    c.case.service.load_action_proof = local_action_proof(c.case)
    plan = t.ProtectionPlan(plan_id='protection', mode='NATIVE', scope=c.scope,
        coverage_quantity=quantity('1', 'BASE'), stop_price=price('90'), stop_trigger='LAST',
        native_order_ids=(), monitor_id=None, state=t.ProtectionState.GAP, last_verified_at=None,
        coverage_gap_since=NOW, replacement_revision=0, on_gap_policy='EXIT')
    command = replace(c.case.command, operation='SET_PROTECTION', canonical_payload=replace(
        c.case.command.canonical_payload, order_type='STOP_MARKET', quantity='1', trigger_price='90'))
    command = replace(command, payload_hash=canonical_hash(ContractCodec().encode(command)['canonical_payload']))
    verifier = LockedActionVerifier(local_action_proof(c.case))
    with c.uow.transaction(c.scope) as tx:
        tx._append('protection_plans', 'protection', plan, 0)
    with c.uow.transaction(c.scope) as tx:
        assert verifier.verify(tx, command, c.case.case.intent, NOW) is not None
        assert tx._latest('protection_plans', 'protection', t.ProtectionPlan).state == t.ProtectionState.GAP
    outcome = c.case.reserve()
    assert outcome.accepted and not c.assess().new_risk_allowed


def test_processing_delay_after_validation_is_rechecked_before_writes(ip08_postgres, report_archive, monkeypatch):
    from trading_system.risk import daily_loss
    c = MonitorCase(ip08_postgres)
    original = daily_loss.digest
    def delayed_digest(value):
        result = original(value)
        if type(value) is DailyLossReport:
            c.now = (utc_timestamp(NOW) + timedelta(seconds=61)).isoformat()
        return result
    monkeypatch.setattr(daily_loss, 'digest', delayed_digest)
    result = c.assess()
    assert not result.new_risk_allowed and not c.locks()


@pytest.mark.parametrize('fault', ['policy_hash', 'policy_revision', 'policy_group', 'binding_source', 'missing_binding'])
def test_authoritative_policy_binding_cannot_be_replaced_by_report_values(ip08_postgres, report_archive, fault):
    c = MonitorCase(ip08_postgres, closing='110')
    binding = c.binding
    if fault == 'policy_hash':
        binding = replace(binding, policy_hash='0' * 64)
    elif fault == 'policy_revision':
        binding = replace(binding, policy_revision=2)
    elif fault == 'policy_group':
        binding = replace(binding, collateral_group_id='unbound-group')
    elif fault == 'binding_source':
        binding = replace(binding, policy_scope=replace(c.scope, venue_account_id='unbound-source'))
    else:
        binding = replace(binding, policy_id='missing-authorized-policy')
    with c.case.ledger.connect_admin() as conn:
        conn.execute('INSERT INTO ledger.collateral_groups(scope_id,record_id,sequence,payload,transaction_id) '
            'VALUES(%s,%s,2,%s::jsonb,%s)', (scope_id(c.binding.policy_scope),
                policy_binding_key(c.scope.environment, c.case.group), encode(binding), 'invalid-authority-fixture'))
    result = c.assess()
    assert not result.new_risk_allowed and result.rejection_codes
    assert not c.locks()


def test_execution_owner_cannot_forge_policy_or_archived_observation(ip08_postgres, report_archive):
    import psycopg
    c = MonitorCase(ip08_postgres, closing='110')
    for sql, args in [
        ('INSERT INTO ledger.test_daily_loss_reports VALUES(%s,%s,2,%s::jsonb)',
         ('PAPER', c.case.group, encode(c.report))),
        ('INSERT INTO ledger.policies(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,%s,2,%s::jsonb,%s)',
         (scope_id(c.binding.policy_scope), c.policy.policy_id, encode(c.policy), 'forged')),
        ('INSERT INTO ledger.collateral_groups(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,%s,2,%s::jsonb,%s)',
         (scope_id(c.binding.policy_scope), policy_binding_key(c.scope.environment, c.case.group), encode(c.binding), 'forged')),
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with c.uow.transaction(c.scope) as tx:
                tx.execute(sql, args)
    assert c.assess().new_risk_allowed


def test_fraction_threshold_and_boundary_are_exact_decimal(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres)
    c.policy = replace(c.policy, revision=2, daily_loss_fraction='0.000000000000000000000000000001')
    c.binding = replace(c.binding, policy_revision=2, policy_hash=digest(c.policy))
    with c.case.ledger.connect_admin() as conn:
        conn.execute('INSERT INTO ledger.policies(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,%s,2,%s::jsonb,%s)',
            (scope_id(c.binding.policy_scope), c.policy.policy_id, encode(c.policy), 'authorized-revision'))
        conn.execute('INSERT INTO ledger.collateral_groups(scope_id,record_id,sequence,payload,transaction_id) VALUES(%s,%s,2,%s::jsonb,%s)',
            (scope_id(c.binding.policy_scope), policy_binding_key(c.scope.environment, c.case.group), encode(c.binding), 'authorized-revision'))
    opening = Decimal('1000000000000000000000000000000.00000000000001')
    threshold = Decimal('1.' + '0' * 43 + '1')
    closing = exact_sum((opening, threshold.copy_negate()))
    report = c.make_report()
    c.save(replace(report, series=(replace(report.series[0], equity=money(str(opening))),
                                   replace(report.series[-1], equity=money(str(closing))))))
    result = c.assess()
    assert result.threshold == result.observed_loss == money(str(threshold))
    assert result.rejection_codes == ('DAILY_LOSS_LIMIT_BREACHED',)


def test_lost_commit_acknowledgement_never_reports_success_or_repeats_latch(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres)
    class LostAck:
        def __init__(self, conn):
            self.conn = conn
        def __getattr__(self, name):
            return getattr(self.conn, name)
        def commit(self):
            self.conn.commit()
            raise OSError('injected lost monitor commit acknowledgement')
    uow = UnitOfWork(lambda: LostAck(c.case.ledger.connect_owner()))
    with pytest.raises(OSError, match='lost monitor commit'):
        DailyLossMonitor(uow, read_report=archive_reader, clock=c.clock).assess(c.scope)
    original = c.locks()[0]
    result = c.assess(c.case.btc_scope)
    assert result.locks == (original,) and not result.new_risk_allowed
    assert c.case.count('risk_locks') == 1


@pytest.mark.parametrize('fake', [True, {'complete': True, 'equity': '999999'}, None])
def test_caller_shaped_flags_and_values_are_not_monitor_evidence(ip08_postgres, report_archive, fake):
    c = MonitorCase(ip08_postgres)
    c.monitor.read_report = lambda *args: fake
    result = c.assess()
    assert not result.new_risk_allowed and not c.locks()


def test_unknown_coverage_retains_independent_cause_intersection(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres)
    original = RiskLockService(c.uow, clock=lambda: NOW).raise_lock(c.scope, 'PROTECTION_GAP', evidence('gap', NOW))
    c.save(replace(c.report, coverage=()))
    result = c.assess()
    assert result.allowed_actions == ('EXIT', 'RECONCILE')
    assert result.locks == (original,) and c.locks() == (original,)


def test_independent_group_with_its_own_complete_policy_report_remains_available(ip08_postgres, report_archive):
    breached = MonitorCase(ip08_postgres)
    assert not breached.assess().new_risk_allowed
    other = MonitorCase(ip08_postgres, closing='110')
    assert other.case.group != breached.case.group
    result = other.assess()
    assert result.new_risk_allowed and not result.locks and not other.locks()


@pytest.mark.parametrize('environment', [t.Environment.TESTNET, t.Environment.LIVE])
def test_monitor_cannot_enable_testnet_or_live(ip08_postgres, environment):
    case = eth_case(ip08_postgres)
    with pytest.raises(ValueError, match='ENVIRONMENT_NOT_ENABLED'):
        DailyLossMonitor(case.ledger.uow).assess(replace(case.scope, environment=environment))


def test_malformed_stored_policy_returns_denial_not_a_fabricated_threshold(ip08_postgres, report_archive):
    c = MonitorCase(ip08_postgres)
    payload = ContractCodec().encode(c.policy)
    payload['daily_loss_fraction'] = '2'
    with c.case.ledger.connect_admin() as conn:
        conn.execute('INSERT INTO ledger.policies(scope_id,record_id,sequence,payload,transaction_id) '
            'VALUES(%s,%s,2,%s::jsonb,%s)', (scope_id(c.binding.policy_scope), c.policy.policy_id,
                json.dumps(payload), 'malformed-policy-fixture'))
    result = c.assess()
    assert result.rejection_codes == ('DAILY_LOSS_EVIDENCE_INVALID',)
    assert not result.new_risk_allowed and not c.locks()


def test_rollover_cannot_bypass_missing_review_watermark_after_source_resolution(
        ip08_postgres, report_archive, private_daily_review_archive):
    c = MonitorCase(ip08_postgres)
    original = c.assess().locks[0]
    supplied = t.ResolutionEvidence(lock_id=original.lock_id, scope=original.scope,
        evidence=(evidence('review-before-rollover', NOW),), authorization_id='review-before-rollover')
    resolver = resolver_uow(c.case)
    with resolver.transaction(original.scope) as tx:
        tx.execute('INSERT INTO ledger.test_daily_reviews VALUES(%s,%s,%s::jsonb)',
            (scope_id(original.scope), original.lock_id, encode(DailyReview(original,
                supplied.authorization_id, supplied.evidence[0]))))
    service = RiskLockService(c.uow, clock=lambda: NOW, verify=verify_daily_review, resolver_uow=resolver)
    assert service.resolve(original.lock_id, supplied).resolved
    c.now = (utc_timestamp(NOW) + timedelta(days=1)).isoformat()
    c.ns += 86_400_000_000_000
    c.save(c.make_report('120'))
    result = c.assess()
    assert not result.new_risk_allowed
    assert result.rejection_codes == ('DAILY_LOSS_REVIEW_WATERMARK_REQUIRED',)
    assert not c.locks() and c.case.count('risk_locks') == 2
