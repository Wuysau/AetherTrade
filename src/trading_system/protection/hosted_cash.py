"""Offline hosted-stop planner. No dispatch, credentials or native-stop claims.

read_inputs must read current server-owned authorization and archived facts.
record must atomically retain observations, trigger latches, locks and alerts;
failure never claims persisted protection. An execution owner must consume the
pending ExitReceipt via IP-15 with fresh emergency authority and durable commands.
"""
from dataclasses import dataclass, replace
from typing import Callable

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.protection.native import digest, offline
from trading_system.recovery.reconciler import valid_ref


@dataclass(frozen=True, kw_only=True)
class HostedInputs:
    plan_hash: str
    market_hash: str
    session_hash: str
    position: t.ManagedPosition
    authorization: t.Authorization
    policy_revision: int
    fully_cash_funded: bool
    capital_at_risk: t.Money
    instrument_cap: t.Money
    allowed_sessions: tuple[str, ...]
    sellable: t.Quantity
    heartbeat_monotonic_ns: int
    heartbeat_monitor_id: str
    network_healthy: bool
    evidence: t.EvidenceRef
    exit_latched: bool


@dataclass(frozen=True, kw_only=True)
class HostedObservation:
    actions: t.ProtectionActionPlan
    alert: t.RiskAlert | None
    exit_receipt: t.ExitReceipt | None
    exit_latched: bool
    session: t.SessionState
    # Persist this entire observation atomically; it is never proof of flatness.
    flat_verified: bool = False


class HostedCashProtection:
    def __init__(self, *, clock: Callable, read_inputs: Callable | None = None,
                 record: Callable | None = None, maximum_heartbeat_ns: int = 5_000_000_000):
        if type(maximum_heartbeat_ns) is not int or maximum_heartbeat_ns <= 0:
            raise ValueError('POSITIVE_HEARTBEAT_LIMIT_REQUIRED')
        self.clock = clock
        self.read_inputs = read_inputs
        self.record = record
        self.maximum_heartbeat_ns = maximum_heartbeat_ns

    def tick(self, plan: t.ProtectionPlan, market: t.SnapshotPacket,
             session: t.SessionState) -> t.ProtectionActionPlan:
        offline(plan.scope)
        now = self.clock()
        reasons = []
        facts = None
        triggered = False
        try:
            facts = self.read_inputs(plan, market, session) if self.read_inputs else None
            if not isinstance(facts, HostedInputs):
                raise ValueError('HOSTED_INPUTS_MISSING')
            p, auth = facts.position, facts.authorization
            triggered = facts.exit_latched is True
            if (plan.mode != 'HOSTED' or plan.scope.product not in (t.Product.SPOT, t.Product.US_EQUITY)
                    or plan.scope.venue_leg != 'BOTH' or plan.native_order_ids or not plan.monitor_id
                    or p.scope != plan.scope or p.actual_quantity.value <= 0
                    or not p.position_id or not p.owner_strategy_id or not p.owner_book_id
                    or type(p.ownership_epoch) is not int or p.ownership_epoch < 1
                    or plan.coverage_quantity != p.actual_quantity
                    or facts.sellable.unit != p.actual_quantity.unit
                    or not 0 <= facts.sellable.value <= p.actual_quantity.value
                    or facts.fully_cash_funded is not True):
                reasons.append('CASH_ONLY_COVERAGE_REQUIRED')
            if (not auth.authorization_id or not auth.deployment_id
                    or type(facts.policy_revision) is not int or facts.policy_revision < 1
                    or auth.environment != plan.scope.environment
                    or plan.scope not in auth.scopes or auth.owner_strategy_id != p.owner_strategy_id
                    or auth.policy_revision != facts.policy_revision or auth.revoked_at is not None
                    or 'HOSTED_ALLOWED_CASH_ONLY' not in auth.protection_options
                    or 'NATIVE_REQUIRED' in auth.protection_options
                    or not utc_timestamp(auth.valid_from) <= utc_timestamp(now.utc_now) < utc_timestamp(auth.expires_at)):
                reasons.append('EXPLICIT_HOSTED_AUTHORIZATION_REQUIRED')
            capital, cap = facts.capital_at_risk, facts.instrument_cap
            if (capital.currency != cap.currency or capital.currency != auth.maximum_capital.currency
                    or not 0 < capital.amount <= cap.amount <= auth.maximum_capital.amount
                    or session.session_id not in facts.allowed_sessions):
                reasons.append('HOSTED_CAPITAL_OR_SESSION_UNAUTHORIZED')
            if (facts.plan_hash != digest(plan) or facts.market_hash != digest(market)
                    or facts.session_hash != digest(session)
                    or not valid_ref(facts.evidence, plan.scope, now.utc_now, 60)):
                reasons.append('HOSTED_EVIDENCE_BINDING_INVALID')
            if (now.clock_health != 'HEALTHY' or type(now.monotonic_ns) is not int
                    or type(facts.heartbeat_monotonic_ns) is not int
                    or not 0 <= now.monotonic_ns - facts.heartbeat_monotonic_ns <= self.maximum_heartbeat_ns
                    or facts.heartbeat_monitor_id != plan.monitor_id or facts.network_healthy is not True):
                reasons.append('HOSTED_HEARTBEAT_LOST')
            coverage = market.coverage
            market_valid = True
            if (market.scope != plan.scope or coverage.scope != plan.scope
                    or coverage.query_succeeded is not True or coverage.pagination_complete is not True
                    or coverage.supported is not True or coverage.missing
                    or not valid_ref(market.ref, plan.scope, now.utc_now, 60)
                    or any(not 0 <= (utc_timestamp(now.utc_now) - utc_timestamp(stamp)).total_seconds() <= 60
                           for stamp in (market.quote.occurred_at, market.quote.received_at,
                                         market.published_at, market.revision_visible_at))):
                reasons.append('HOSTED_MARKET_DATA_UNAVAILABLE')
                market_valid = False
            if (session.instrument_uid != plan.scope.instrument_uid or session.state not in ('OPEN', 'CLOSED', 'HALTED')
                    or not valid_ref(session.evidence, plan.scope, now.utc_now, 60)
                    or not 0 <= (utc_timestamp(now.utc_now) - utc_timestamp(session.observed_at)).total_seconds() <= 60):
                reasons.append('SESSION_UNVERIFIED')
            quote = market.quote.last if plan.stop_trigger == 'LAST' else market.quote.bid
            if (plan.stop_trigger not in ('LAST', 'BID_ASK') or quote is None
                    or quote.currency != plan.stop_price.currency):
                reasons.append('HOSTED_TRIGGER_SOURCE_UNAVAILABLE')
            elif (market_valid and facts.market_hash == digest(market)
                    and quote.value <= plan.stop_price.value):
                # A fresh observed crossing stays latched even when another
                # gate (heartbeat, authorization or session) prevents action.
                # This is an observation only, never authority to dispatch.
                triggered = True
        except Exception:
            reasons.append('HOSTED_INPUTS_UNAVAILABLE')
        receipt = None
        if triggered and isinstance(facts, HostedInputs):
            closed = session.state != 'OPEN' or session.session_id not in facts.allowed_sessions
            exit_reasons = tuple(reasons) + ('EXECUTION_OWNER_EXIT_REQUIRED',)
            if facts.sellable.value < facts.position.actual_quantity.value:
                exit_reasons += ('UNSELLABLE_RESIDUAL',)
            receipt = t.ExitReceipt(position_id=facts.position.position_id,
                state='EXIT_PENDING_MARKET' if closed else 'EXITING', command_ids=(),
                incomplete_reasons=exit_reasons)
            reasons.extend((receipt.state, 'EXECUTION_OWNER_EXIT_REQUIRED'))
        state = t.ProtectionState.GAP if reasons else t.ProtectionState.VERIFIED_HOSTED
        locks = ()
        alert = None
        if reasons:
            key = 'hosted:' + digest(plan.scope) + ':' + plan.plan_id
            lock = t.RiskLock(lock_id=key, scope=plan.scope, cause='PROTECTION_GAP',
                state='ACTIVE', permission=t.RiskPermission.NO_NEW_RISK,
                required_evidence=('HOSTED_MONITOR_RECOVERY', 'EXIT_RECONCILIATION'),
                raised_at=plan.coverage_gap_since or now.utc_now)
            locks = (lock,)
            alert = t.RiskAlert(alert_id=key, scope=plan.scope, cause=';'.join(reasons),
                evidence=(), occurred_at=now.utc_now)
        updated = replace(plan, state=state,
            last_verified_at=now.utc_now if not reasons else None,
            coverage_gap_since=(plan.coverage_gap_since or now.utc_now) if reasons else plan.coverage_gap_since)
        actions = t.ProtectionActionPlan(plan=updated, commands=(), locks=locks, missing=tuple(reasons))
        observation = HostedObservation(actions=actions, alert=alert, exit_receipt=receipt,
            exit_latched=triggered, session=session)
        try:
            if self.record is None or self.record(observation) is not True:
                raise ValueError('HOSTED_STORAGE_UNAVAILABLE')
        except Exception:
            # Returned locks are proposed, not claimed durable. No fallback send.
            if not locks:
                locks = (t.RiskLock(lock_id='hosted:' + digest(plan.scope) + ':' + plan.plan_id,
                    scope=plan.scope, cause='PROTECTION_GAP', state='ACTIVE',
                    permission=t.RiskPermission.NO_NEW_RISK,
                    required_evidence=('HOSTED_STORAGE_RECOVERY', 'HOSTED_MONITOR_RECOVERY'),
                    raised_at=updated.coverage_gap_since or now.utc_now),)
            return replace(actions, plan=replace(updated, state=t.ProtectionState.GAP,
                last_verified_at=None, coverage_gap_since=updated.coverage_gap_since or now.utc_now),
                locks=locks,
                missing=actions.missing + ('HOSTED_STORAGE_UNAVAILABLE', 'ALERT_DELIVERY_UNCONFIRMED'))
        return actions
