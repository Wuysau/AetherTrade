"""Observed, cashflow-adjusted PAPER daily loss over the original lock ledger.

Only trusted server composition may install read_report. It reads a protected
local archive inside the collateral mutex, proves complete group valuation and
cashflow/cost coverage, and binds its report to this transaction. Public callers
supply only scope, never equity, thresholds, completeness flags or approvals.
Default composition denies new risk. This module never resolves a lock, sends
orders, or creates a competing legacy lock authority.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from time import monotonic_ns
from uuid import uuid4
from zoneinfo import ZoneInfo

from jsonschema.exceptions import ValidationError

from trading_system.accounts.authorization import valid_evidence
from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, decimal_string, domain_payload, utc_timestamp
from trading_system.contracts.wire import ContractCodec, RiskPolicy
from trading_system.storage.events import EventStore
from trading_system.storage.models import account_key, decode, scope_id
from .locks import ACTIONS, POLICIES, ResolutionAudit, RiskLockService
from .pnl import adjusted_equities, exact_sum
from .risk_day import RiskDayService, day_bounds


COVERAGE_STREAMS = ('EQUITY', 'VALUATION_FX', 'EXTERNAL_CASHFLOWS', 'INTERNAL_TRANSFERS',
                    'FEES', 'FUNDING', 'BORROW_COSTS', 'SECURITY_CASHFLOWS')


def digest(value):
    return canonical_hash(domain_payload(value))


def policy_binding_key(environment, group_id):
    return 'daily-loss-policy:' + canonical_hash({'environment': str(environment), 'group': group_id})


@dataclass(frozen=True, kw_only=True)
class RiskAccount:
    environment: t.Environment
    venue_account_id: str
    product: t.Product


@dataclass(frozen=True, kw_only=True)
class DailyLossPolicyBinding:
    """Protected collateral_groups metadata, provisioned by policy authority.

    Runtime ledger_owner has SELECT, not INSERT, on collateral_groups/policies.
    This is a group policy pointer, not a user/model-provided threshold.
    """
    environment: t.Environment
    collateral_group_id: str
    policy_scope: t.ScopeKey
    policy_id: str
    policy_revision: int
    policy_hash: str
    evidence: t.EvidenceRef


@dataclass(frozen=True, kw_only=True)
class DailyLossCoverage:
    """Trusted archive's complete stream interval, including evidenced zero.

    All eight streams are required. Empty input is not proof of zero costs.
    Historical samples may be old; this interval's archive watermark must be
    fresh and must cover every group member and every observed equity point.
    """
    stream: str
    members: tuple[RiskAccount, ...]
    start_at: str
    through_at: str
    missing: tuple[str, ...]
    evidence: t.EvidenceRef


@dataclass(frozen=True, kw_only=True)
class DailyLossReport:
    transaction_id: str
    scope: t.ScopeKey
    collateral_group_id: str
    policy_hash: str
    members: tuple[RiskAccount, ...]
    series: tuple[t.EquityPoint, ...]
    cashflows: tuple[t.Cashflow, ...]
    coverage: tuple[DailyLossCoverage, ...]
    captured_at: str
    valid_until: str


@dataclass(frozen=True, kw_only=True)
class DailyLossBreach:
    lock: t.RiskLock
    risk_day: str
    policy_binding: DailyLossPolicyBinding
    policy: RiskPolicy
    report: DailyLossReport
    report_hash: str
    threshold: t.Money
    observed_loss: t.Money
    crossed_at: str
    latest_pnl: t.Money


@dataclass(frozen=True, kw_only=True)
class DailyLossAssessment:
    """Committed diagnostic action ceiling, never an execution authorization."""
    scope: t.ScopeKey
    allowed_actions: tuple[str, ...]
    locks: tuple[t.RiskLock, ...]
    rejection_codes: tuple[str, ...]
    risk_day: str | None = None
    threshold: t.Money | None = None
    observed_loss: t.Money | None = None
    latest_pnl: t.Money | None = None
    valid_until: str | None = None

    @property
    def new_risk_allowed(self):
        return 'NEW_RISK' in self.allowed_actions


class EvidenceDenied(ValueError):
    pass


def clock_now():
    return t.ClockReading(utc_now=datetime.now(timezone.utc).isoformat(),
                          monotonic_ns=monotonic_ns(), clock_health='HEALTHY')


class DailyLossMonitor:
    def __init__(self, uow, *, read_report=None, clock=clock_now, maximum_age_seconds=60):
        if type(maximum_age_seconds) is not int or maximum_age_seconds <= 0:
            raise ValueError('positive daily-loss observation age required')
        self.uow, self.read_report, self.clock = uow, read_report, clock
        self.maximum_age_seconds = maximum_age_seconds

    def assess(self, scope):
        if scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH):
            raise ValueError('DAILY_LOSS_ENVIRONMENT_NOT_ENABLED')
        with self.uow.transaction(scope) as tx:
            result = self._assess(tx, scope)
        # A breach is a committed business outcome, not an exception which would
        # roll back its own lock. A failed/lost DB commit never returns success.
        return result

    @staticmethod
    def _result(scope, active, *codes, **fields):
        actions = ACTIONS[t.RiskPermission.NORMAL]
        for lock in active:
            actions &= ACTIONS.get(lock.permission, frozenset({'RECONCILE'}))
        if codes:
            actions -= {'NEW_RISK'}
        return DailyLossAssessment(scope=scope, allowed_actions=tuple(sorted(actions)),
            locks=tuple(active), rejection_codes=tuple(codes), **fields)

    def _clock(self, previous=None):
        reading = self.clock()
        if (type(reading) is not t.ClockReading or reading.clock_health != 'HEALTHY'
                or type(reading.monotonic_ns) is not int or reading.monotonic_ns <= 0):
            raise EvidenceDenied('DAILY_LOSS_CLOCK_UNHEALTHY')
        at = utc_timestamp(reading.utc_now)
        if previous is not None and (reading.monotonic_ns < previous.monotonic_ns
                or at < utc_timestamp(previous.utc_now)):
            raise EvidenceDenied('DAILY_LOSS_CLOCK_REVERSED')
        return reading

    def _fresh_window(self, anchor, previous, expiry):
        reading = self._clock(previous)
        duration = expiry - utc_timestamp(anchor.utc_now)
        deadline = anchor.monotonic_ns + ((duration.days * 86400 + duration.seconds) * 1_000_000_000
                                         + duration.microseconds * 1000)
        if utc_timestamp(reading.utc_now) >= expiry or reading.monotonic_ns >= deadline:
            raise EvidenceDenied('DAILY_LOSS_REPORT_EXPIRED')
        return reading

    def _authority(self, tx, scope):
        rows = tx.execute('SELECT environment,account_id,product,collateral_group_id FROM ledger.account_bindings '
            'WHERE environment=%s AND collateral_group_id=%s FOR SHARE',
            (str(scope.environment), tx.group_id)).fetchall()
        members = tuple(sorted((RiskAccount(environment=t.Environment(env), venue_account_id=account,
            product=t.Product(product)) for env, account, product, group in rows
            if group == tx.group_id), key=lambda a: (str(a.environment), a.venue_account_id, str(a.product))))
        keys = [(str(a.environment), a.venue_account_id, str(a.product)) for a in members]
        if (not members or len(set(keys)) != len(rows) or account_key(scope) not in keys
                or any(not a.venue_account_id.strip() for a in members)):
            raise EvidenceDenied('DAILY_LOSS_GROUP_MEMBERS_UNVERIFIED')
        binding_rows = tx.execute('SELECT DISTINCT ON (scope_id,record_id) scope_id,record_id,payload '
            'FROM ledger.collateral_groups WHERE record_id=%s ORDER BY scope_id,record_id,sequence DESC',
            (policy_binding_key(scope.environment, tx.group_id),)).fetchall()
        if len(binding_rows) != 1:
            raise EvidenceDenied('DAILY_LOSS_POLICY_BINDING_REQUIRED')
        stored_scope, _, raw = binding_rows[0]
        binding = decode(DailyLossPolicyBinding, raw)
        if (binding.environment != scope.environment or binding.collateral_group_id != tx.group_id
                or stored_scope != scope_id(binding.policy_scope) or account_key(binding.policy_scope) not in keys
                or type(binding.policy_revision) is not int or binding.policy_revision < 1):
            raise EvidenceDenied('DAILY_LOSS_POLICY_BINDING_MISMATCH')
        row = tx.execute('SELECT payload FROM ledger.policies WHERE scope_id=%s AND record_id=%s '
            'ORDER BY sequence DESC LIMIT 1', (stored_scope, binding.policy_id)).fetchone()
        if row is None:
            raise EvidenceDenied('DAILY_LOSS_POLICY_MISSING')
        policy = decode(RiskPolicy, row[0])
        ContractCodec().encode(policy)  # validate the persisted approved wire policy
        if (policy.policy_id != binding.policy_id or policy.revision != binding.policy_revision
                or digest(policy) != binding.policy_hash or policy.environment != scope.environment
                or policy.collateral_group_id != tx.group_id):
            raise EvidenceDenied('DAILY_LOSS_POLICY_REVISION_MISMATCH')
        return binding, policy, members

    def _validate(self, tx, scope, binding, policy, members, report, reading):
        now = utc_timestamp(reading.utc_now)
        if not valid_evidence(binding.evidence, scope.environment, now):
            raise EvidenceDenied('DAILY_LOSS_POLICY_EVIDENCE_INVALID')
        if (type(report) is not DailyLossReport or report.transaction_id != tx.transaction_id
                or report.scope != scope or report.collateral_group_id != tx.group_id
                or report.policy_hash != digest(policy) or report.members != members
                or len(report.series) < 2):
            raise EvidenceDenied('DAILY_LOSS_REPORT_BINDING_MISMATCH')
        day = now.astimezone(ZoneInfo(policy.risk_day_timezone)).date().isoformat()
        start, end = day_bounds(day, policy.risk_day_timezone)
        last_at, captured = utc_timestamp(report.series[-1].at), utc_timestamp(report.captured_at)
        if (utc_timestamp(report.series[0].at) != start or not start < last_at <= captured <= now
                or (now - last_at).total_seconds() > self.maximum_age_seconds
                or (now - captured).total_seconds() > self.maximum_age_seconds):
            raise EvidenceDenied('DAILY_LOSS_OPENING_OR_OBSERVATION_INVALID')
        if report.series[0].equity.amount <= 0:
            raise EvidenceDenied('DAILY_LOSS_OPENING_EQUITY_NOT_POSITIVE')
        seen = set()
        for item in (*report.series, *report.cashflows):
            at, visible = utc_timestamp(item.at), utc_timestamp(item.evidence.visible_at)
            if (not valid_evidence(item.evidence, scope.environment, now) or item.evidence.id in seen
                    or not start <= at <= last_at or not at <= visible <= captured
                    or (visible - at).total_seconds() > self.maximum_age_seconds):
                raise EvidenceDenied('DAILY_LOSS_EVENT_EVIDENCE_INVALID')
            seen.add(item.evidence.id)
        if ({c.stream for c in report.coverage} != set(COVERAGE_STREAMS)
                or len(report.coverage) != len(COVERAGE_STREAMS)):
            raise EvidenceDenied('DAILY_LOSS_COST_OR_STREAM_COVERAGE_MISSING')
        coverage_times = []
        for coverage in report.coverage:
            visible = utc_timestamp(coverage.evidence.visible_at)
            if (type(coverage) is not DailyLossCoverage or coverage.members != members or coverage.missing
                    or utc_timestamp(coverage.start_at) != start or utc_timestamp(coverage.through_at) != last_at
                    or not valid_evidence(coverage.evidence, scope.environment, now)
                    or not last_at <= visible <= captured
                    or (now - visible).total_seconds() > self.maximum_age_seconds):
                raise EvidenceDenied('DAILY_LOSS_COST_OR_STREAM_COVERAGE_UNVERIFIED')
            coverage_times.append(visible)
        if any(p.equity.currency != policy.valuation_currency for p in report.series):
            raise EvidenceDenied('DAILY_LOSS_VALUATION_CURRENCY_MISMATCH')
        result = RiskDayService(missing_costs=()).pnl(list(report.series), list(report.cashflows),
                                                   policy.risk_day_timezone)
        if result.missing or result.adjusted_pnl is None:
            raise EvidenceDenied('DAILY_LOSS_PNL_INCOMPLETE:' + ','.join(result.missing))
        adjusted, _ = adjusted_equities(report.series, report.cashflows)
        losses = tuple(max(Decimal(0), exact_sum((adjusted[0], value.copy_negate()))) for value in adjusted)
        fraction, opening = decimal_string(policy.daily_loss_fraction), report.series[0].equity.amount
        with localcontext() as ctx:
            ctx.prec = max(80, len(opening.as_tuple().digits) + len(fraction.as_tuple().digits) + 2)
            threshold = opening * fraction
        expiry = min([utc_timestamp(report.valid_until), end] + [at + timedelta(seconds=self.maximum_age_seconds)
            for at in (last_at, captured, *coverage_times)])
        crossed = next((point.at for point, loss in zip(report.series, losses) if loss >= threshold), None)
        return result, threshold, max(losses), crossed, expiry

    def _assess(self, tx, scope):
        originals = tx.risk_locks.applicable(scope, include_resolved=True)
        active = tuple(lock for lock in originals if lock.state == 'ACTIVE')
        if any(lock.cause == 'DAILY_LOSS' for lock in active):
            return self._result(scope, active, 'ACTIVE_DAILY_LOSS')
        if self.read_report is None:
            return self._result(scope, active, 'DAILY_LOSS_TRUSTED_READER_REQUIRED')
        try:
            anchor = self._clock()
            binding, policy, members = self._authority(tx, scope)
            reading = self._clock(anchor)  # refresh after policy/account readers
            try:
                report = self.read_report(tx, scope, policy, members)
            except Exception:
                return self._result(scope, active, 'DAILY_LOSS_REPORT_UNAVAILABLE')
            reading = self._clock(reading)  # never reuse pre-reader time
            pnl, threshold, loss, crossed, expiry = self._validate(
                tx, scope, binding, policy, members, report, reading)
            fields = dict(risk_day=pnl.risk_day, threshold=t.Money(amount=threshold, currency=policy.valuation_currency),
                observed_loss=t.Money(amount=loss, currency=policy.valuation_currency), latest_pnl=pnl.adjusted_pnl,
                valid_until=expiry.isoformat())
            # The existing resolution audit cannot prove a complete reviewed
            # observation watermark. Do not replay a historical reviewed breach
            # or infer permission to rearm from a caller authorization string.
            # Rollover is not evidence of a separate new-day policy review.
            for lock in originals:
                if lock.state == 'RESOLVED' and lock.cause == 'DAILY_LOSS':
                    row = tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s '
                        'ORDER BY sequence DESC LIMIT 1',
                        (scope_id(lock.scope), 'risk-resolution:' + lock.lock_id)).fetchone()
                    reading = self._clock(reading)
                    audit = None if row is None else decode(ResolutionAudit, row[0])
                    if (audit is None or audit.lock != replace(lock, state='ACTIVE')
                            or audit.resolution.scope != lock.scope or audit.resolution.lock_id != lock.lock_id
                            or not audit.resolution.authorization_id
                            or not utc_timestamp(lock.raised_at) <= utc_timestamp(audit.resolved_at)
                                <= utc_timestamp(reading.utc_now)):
                        return self._result(scope, active, 'DAILY_LOSS_RESOLUTION_AUDIT_UNVERIFIED', **fields)
                    return self._result(scope, active, 'DAILY_LOSS_REVIEW_WATERMARK_REQUIRED', **fields)
            reading = self._fresh_window(anchor, reading, expiry)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError, ValidationError) as exc:
            code = str(exc) if isinstance(exc, EvidenceDenied) else 'DAILY_LOSS_EVIDENCE_INVALID'
            return self._result(scope, active, code)
        if crossed is None:
            return self._result(scope, active, **fields)
        permission, required = POLICIES['DAILY_LOSS']
        lock = t.RiskLock(lock_id=uuid4().hex, scope=scope, cause='DAILY_LOSS', state='ACTIVE',
            permission=permission, required_evidence=required, raised_at=reading.utc_now)
        breach = DailyLossBreach(lock=lock, risk_day=pnl.risk_day, policy_binding=binding, policy=policy,
            report=report, report_hash=digest(report), threshold=fields['threshold'],
            observed_loss=fields['observed_loss'], crossed_at=crossed, latest_pnl=pnl.adjusted_pnl)
        evidence = tuple(item.evidence for item in (*report.series, *report.cashflows, *report.coverage))
        try:
            # Large report hashing/serialization can itself take time. Refresh
            # after that work, immediately before the atomic write boundary.
            reading = self._fresh_window(anchor, reading, expiry)
        except EvidenceDenied as exc:
            return self._result(scope, active, str(exc), **fields)
        lock = replace(lock, raised_at=reading.utc_now)
        breach = replace(breach, lock=lock)
        tx.risk_locks.append(lock, 0)
        EventStore().append(tx, RiskLockService._event(lock, 1, evidence, reading.utc_now), 0)
        tx._append('audit_events', 'daily-loss-breach:' + lock.lock_id, breach, 0)
        return self._result(scope, (*active, lock), 'DAILY_LOSS_LIMIT_BREACHED', **fields)
