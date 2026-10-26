"""Durable offline mode handoff, with no platform side effects.

Readers are trusted LOCAL ledger/archive readers, called under the collateral
mutex. They must not perform network I/O. DTO booleans and reference labels are
not evidence. Missing readers deny. No transfer resolves a risk cause lock:
IP-08 and recovery must explicitly review resumption after the handoff.
"""
from dataclasses import dataclass, replace

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.recovery.reconciler import utc_now, valid_ref
from .registry import OwnerCommitUnavailable, TransferRecord, digest, offline


@dataclass(frozen=True, kw_only=True)
class TransferFacts:
    """Archive attestation bound to one coherent, current ledger revision.

    authorization_current covers old/new deployments and explicit transfer
    authority. exit_plan_accepted requires the new owner to accept the preserved
    plan (including its deadline) or a reviewed replacement. No UI label counts.
    """
    scope: t.ScopeKey
    transfer_id: str
    transfer_sequence: int
    owner_sequence: int
    evidence_hash: str
    context_hash: str
    position: t.ManagedPosition
    valid_until: str
    authorization_current: bool
    pending_commands_resolved: bool
    orders_verified: bool
    positions_verified: bool
    balances_and_allocations_verified: bool
    protection_verified: bool
    exit_plan_accepted: bool
    accepted_exit_plan_id: str | None


@dataclass(frozen=True, kw_only=True)
class IdempotencyRecord:
    transfer_id: str
    request_hash: str
    scope: t.ScopeKey


def transfer_key(transfer_id):
    return 'ip16:transfer:' + transfer_id


def handoff_case_id(transfer_id):
    return 'ip16:reconcile:' + transfer_id


class OwnershipService:
    def __init__(self, registry, *, authorize=None, read_facts=None, clock=utc_now):
        self.registry, self.authorize, self.read_facts = registry, authorize, read_facts
        self.clock = clock

    def request_transfer(self, request: t.TransferRequest,
                         context: t.ControlContext) -> t.TransferReceipt:
        offline(request.scope)
        def reject(reason):
            return t.TransferReceipt(transfer_id=request.transfer_id,
                                     state=t.TransferState.TRANSFER_BLOCKED, reason=reason)
        if (not request.transfer_id.strip() or not request.old_owner.strip()
                or not request.new_owner.strip() or request.old_owner == request.new_owner
                or request.mode not in {'TRANSFER_OWNER', 'RETAIN_OWNER', 'EXIT_THEN_SWITCH'}):
            return reject('INVALID_TRANSFER')
        if (request.scope not in context.scopes or context.environment != request.scope.environment
                or not context.identity_id or not context.authorization_id or not context.deployment_id
                or not context.idempotency_key or type(context.expected_sequence) is not int
                or type(context.policy_revision) is not int or context.policy_revision < 1
                or type(request.ownership_epoch) is not int or request.ownership_epoch < 1
                or type(context.ownership_epoch) is not int
                or context.ownership_epoch != request.ownership_epoch):
            return reject('INVALID_CONTROL_BINDING')
        request_hash = digest((request, context))
        key = 'ip16:idempotency:' + digest((context.identity_id, context.environment,
                                          'transfer', context.idempotency_key))
        with self.registry.transaction(request.scope) as tx:
            # Serialize the identity key before the transfer key in every request.
            tx.serialize(key)
            tx.serialize(transfer_key(request.transfer_id))
            try:
                authorized = self.authorize is not None and self.authorize(tx, request, context) is True
            except Exception:
                authorized = False
            if not authorized:
                return reject('TRANSFER_NOT_AUTHORIZED')
            prior = tx.global_get('audit_events', key, IdempotencyRecord)
            existing = tx.global_get('ownership_transfers', transfer_key(request.transfer_id), TransferRecord)
            if prior and (prior.request_hash != request_hash or prior.scope != request.scope):
                return reject('IDEMPOTENCY_CONFLICT')
            if existing:
                if existing.request_hash != request_hash:
                    return reject('TRANSFER_ID_CONFLICT')
                return self._receipt(existing.projection)
            if prior:
                return reject('IDEMPOTENCY_RECORD_INCOMPLETE')
            owner = tx.owner()
            if owner is None:
                return reject('EXTERNAL_NOT_OWNED')
            p = owner.position
            if p.ownership_epoch != request.ownership_epoch:
                return reject('STALE_OWNER_EPOCH')
            if p.owner_strategy_id != request.old_owner:
                return reject('OWNERSHIP_CONFLICT')
            if (owner.sequence != context.expected_sequence or owner.policy_revision != context.policy_revision
                    or tx.active_owner() != (request.old_owner, request.ownership_epoch, context.policy_revision)):
                return reject('STALE_CONTROL_REVISION')
            if owner.mode != 'ACTIVE' or owner.transfer_id:
                return reject('TRANSFER_ALREADY_PENDING')
            if owner.intervention_ids or tx.locks():
                return reject('RECONCILIATION_REQUIRED')
            now = self.clock()
            mode = {'TRANSFER_OWNER': 'TRANSFER', 'RETAIN_OWNER': 'DRAINING',
                    'EXIT_THEN_SWITCH': 'EXITING'}[request.mode]
            updated = replace(owner, sequence=owner.sequence + 1, mode=mode, transfer_id=request.transfer_id)
            projection = t.TransferProjection(request=request, state=t.TransferState.REQUESTED,
                ownership_epoch=p.ownership_epoch, durable_steps=(), blocked_reasons=())
            record = TransferRecord(projection=projection, context=context, request_hash=request_hash, sequence=1)
            record = self._with_step(record, now)
            tx.save_owner(owner, updated)
            tx.append('ownership_transfers', transfer_key(request.transfer_id), record, 0)
            tx.append('audit_events', key, IdempotencyRecord(transfer_id=request.transfer_id,
                request_hash=request_hash, scope=request.scope), 0)
            # Persist the freeze through restarts and legacy command paths. This
            # conservative cause is not automatically cleared by an epoch commit.
            lock = t.RiskLock(lock_id='ip16:freeze:' + request.transfer_id, scope=request.scope,
                cause='OWNERSHIP_TRANSFER', state='ACTIVE', permission=t.RiskPermission.NO_NEW_RISK,
                required_evidence=('OWNERSHIP_RECONCILED', 'PROTECTION_CONFIRMED', 'NEW_AUTHORIZATION'),
                raised_at=now)
            tx.raise_lock(lock, record.projection.durable_steps[-1])
            case = t.ReconciliationCase(case_id=handoff_case_id(request.transfer_id), scope=request.scope,
                command_ids=(), position_ids=(p.position_id,), missing=('PENDING_COMMANDS', 'POSITIONS',
                'ORDERS', 'PROTECTION', 'OWNERSHIP'), query_budget=t.QueryBudget(maximum_requests=1,
                window_seconds=60, minimum_interval_ms=1000, retry_after=None),
                attempts=0, next_query_at=now, state='OPEN')
            tx.append('reconciliation_cases', case.case_id, case, 0)
            return self._receipt(record.projection)

    @staticmethod
    def _receipt(projection):
        reason = projection.blocked_reasons[0] if projection.blocked_reasons else None
        if projection.state == t.TransferState.ACTIVE and projection.request.mode == 'RETAIN_OWNER':
            reason = 'RETAIN_OWNER_DRAINING'
        return t.TransferReceipt(transfer_id=projection.request.transfer_id, state=projection.state, reason=reason)

    @staticmethod
    def _with_step(record, now):
        ref = t.EvidenceRef(id=f'{transfer_key(record.projection.request.transfer_id)}:{record.sequence}',
            hash=digest(record), environment=record.projection.request.scope.environment,
            source='IP16_LOCAL_DURABLE_STEP', visible_at=now)
        return replace(record, projection=replace(record.projection,
                       durable_steps=(*record.projection.durable_steps, ref)))

    def projection(self, transfer_id):
        scope = self.registry.locate(transfer_id)
        with self.registry.transaction(scope) as tx:
            return tx.get('ownership_transfers', transfer_key(transfer_id), TransferRecord).projection

    def _facts(self, tx, record, owner, evidence, now):
        try:
            facts = self.read_facts(tx, record, owner, evidence) if self.read_facts else None
            valid = (isinstance(facts, TransferFacts) and facts.scope == tx.scope
                and facts.transfer_id == record.projection.request.transfer_id
                and facts.transfer_sequence == record.sequence and facts.owner_sequence == owner.sequence
                and facts.evidence_hash == digest(evidence) and facts.context_hash == digest(record.context)
                and utc_timestamp(facts.valid_until) > utc_timestamp(now)
                and facts.authorization_current is True
                and facts.position.scope == tx.scope
                and facts.position.position_id == owner.position.position_id
                and facts.position.owner_strategy_id == owner.position.owner_strategy_id
                and facts.position.owner_book_id == owner.position.owner_book_id
                and facts.position.ownership_epoch == owner.position.ownership_epoch
                and facts.position.actual_quantity.unit == owner.position.actual_quantity.unit)
        except Exception:
            valid, facts = False, None
        return facts if valid else None

    def advance(self, transfer_id: str, evidence: t.TransferEvidence) -> t.TransferProjection:
        scope = self.registry.locate(transfer_id)
        with self.registry.transaction(scope) as tx:
            record = tx.get('ownership_transfers', transfer_key(transfer_id), TransferRecord)
            projection, owner = record.projection, tx.owner()
            if projection.state in {t.TransferState.ACTIVE, t.TransferState.TRANSFER_BLOCKED}:
                return projection  # Terminal replay cannot reopen a blocked handoff.
            if owner is None:
                raise ValueError('OWNER_LEDGER_MISSING')
            now = self.clock()
            reasons = []
            if (owner.transfer_id != transfer_id or owner.position.ownership_epoch != projection.ownership_epoch
                    or owner.position.owner_strategy_id != projection.request.old_owner
                    or tx.active_owner() != (owner.position.owner_strategy_id,
                        owner.position.ownership_epoch, owner.policy_revision)):
                reasons.append('OWNER_LEDGER_MISMATCH')
            if (evidence.transfer_id != transfer_id or evidence.durable_step_refs != projection.durable_steps):
                reasons.append('TRANSFER_EVIDENCE_MISMATCH')
            facts = self._facts(tx, record, owner, evidence, now)
            if facts is None:
                reasons.append('CURRENT_TRANSFER_EVIDENCE_REQUIRED')
            if owner.intervention_ids:
                reasons.append('MANUAL_INTERVENTION')
            locks = tx.locks()
            freeze_id = 'ip16:freeze:' + transfer_id
            if not any(lock.lock_id == freeze_id and lock.scope == scope
                       and lock.cause == 'OWNERSHIP_TRANSFER'
                       and lock.permission == t.RiskPermission.NO_NEW_RISK for lock in locks):
                reasons.append('TRANSFER_FREEZE_REQUIRED')
            if any(lock.lock_id != freeze_id for lock in locks):
                reasons.append('OTHER_ACTIVE_RISK_LOCKS')
            state = projection.state
            next_state = state
            new_owner = owner
            plan_id = record.accepted_exit_plan_id
            if not reasons:
                if state == t.TransferState.REQUESTED:
                    next_state = t.TransferState.FREEZE_NEW_RISK
                elif state == t.TransferState.FREEZE_NEW_RISK:
                    if projection.request.mode == 'RETAIN_OWNER':
                        next_state = t.TransferState.ACTIVE
                    else:
                        next_state = t.TransferState.RECONCILE_PENDING
                elif state in {t.TransferState.RECONCILE_PENDING, t.TransferState.VERIFY_PROTECTION,
                               t.TransferState.COMMIT_OWNER_EPOCH}:
                    rec = evidence.reconciliation
                    refs = rec.evidence + evidence.protection.evidence
                    if (rec.case_id != handoff_case_id(transfer_id) or rec.resolved is not True or rec.missing
                            or not rec.evidence or not all(valid_ref(r, scope, now, 60) for r in refs)
                            or facts.pending_commands_resolved is not True or facts.orders_verified is not True
                            or facts.positions_verified is not True
                            or facts.balances_and_allocations_verified is not True):
                        reasons.append('RECONCILIATION_INCOMPLETE')
                    if projection.request.mode == 'EXIT_THEN_SWITCH' and not reasons:
                        exit_receipt = evidence.exit
                        if (exit_receipt is None or exit_receipt.position_id != owner.position.position_id
                                or exit_receipt.state != 'FLAT_VERIFIED' or exit_receipt.incomplete_reasons
                                or facts.position.actual_quantity.value != 0
                                or (rec.residual is not None and
                                    (rec.residual.unit != facts.position.actual_quantity.unit
                                     or rec.residual.value != 0))):
                            # IP-13 can return residual=None. Flatness comes from
                            # the bound current position AND the verified exit,
                            # never from interpreting a missing residual as zero.
                            # Market closure and unknown exits are unfinished, never flat.
                            return self._persist(tx, record, owner, replace(owner, sequence=owner.sequence + 1),
                                state, ('EXIT_NOT_FLAT_VERIFIED',), now).projection
                    if state in {t.TransferState.VERIFY_PROTECTION, t.TransferState.COMMIT_OWNER_EPOCH}:
                        protection = evidence.protection
                        flat = facts.position.actual_quantity.value == 0
                        if (facts.protection_verified is not True or protection.missing or not protection.evidence
                                or protection.covered_quantity.unit != facts.position.actual_quantity.unit
                                or protection.covered_quantity.value < abs(facts.position.actual_quantity.value)
                                or protection.state not in ({t.ProtectionState.FLAT_VERIFIED} if flat else
                                    {t.ProtectionState.VERIFIED_NATIVE, t.ProtectionState.VERIFIED_HOSTED})
                                or facts.exit_plan_accepted is not True or not facts.accepted_exit_plan_id):
                            reasons.append('PROTECTION_OR_ACCEPTED_EXIT_PLAN_REQUIRED')
                        plan_id = facts.accepted_exit_plan_id
                    if not reasons:
                        if state == t.TransferState.RECONCILE_PENDING:
                            next_state = t.TransferState.VERIFY_PROTECTION
                        elif state == t.TransferState.VERIFY_PROTECTION:
                            next_state = t.TransferState.COMMIT_OWNER_EPOCH
                        else:
                            next_state = t.TransferState.ACTIVE
                            # PortfolioBook identity remains the unique manager;
                            # only its delegated strategy and epoch change together.
                            new_owner = replace(owner, position=replace(facts.position,
                                owner_strategy_id=projection.request.new_owner,
                                ownership_epoch=owner.position.ownership_epoch + 1,
                                active_exit_plan_id=plan_id), mode='ACTIVE', transfer_id=None)
                            try:
                                tx.commit_owner(owner, new_owner)
                            except OwnerCommitUnavailable:
                                reasons.append('REVIEWED_OWNER_CAS_REQUIRED')
            if reasons:
                next_state = t.TransferState.TRANSFER_BLOCKED
                new_owner = replace(owner, mode='TRANSFER_BLOCKED')
            new_owner = replace(new_owner, sequence=owner.sequence + 1)
            return self._persist(tx, record, owner, new_owner, next_state, tuple(reasons), now, plan_id).projection

    def _persist(self, tx, record, owner, new_owner, state, reasons, now, plan_id=None):
        projection = replace(record.projection, state=state,
            ownership_epoch=new_owner.position.ownership_epoch, blocked_reasons=reasons)
        updated = self._with_step(replace(record, sequence=record.sequence + 1,
            projection=projection, accepted_exit_plan_id=plan_id), now)
        tx.save_owner(owner, new_owner)
        tx.append('ownership_transfers', transfer_key(projection.request.transfer_id), updated, record.sequence)
        return updated
