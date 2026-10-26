"""Durable control inbox; never an execution-command issuer or platform client.

Uses IP-03's append-only control_commands repository. No worker is installed: QUEUED means
awaiting execution-owner integration, not a promise that an action happened.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json, domain_payload
from trading_system.storage.models import decode
from .auth import ControlError, require_grant


@dataclass(frozen=True, kw_only=True)
class InboxRecord:
    identity_id: str
    authorization_id: str
    deployment_id: str
    scope: t.ScopeKey
    operation: str
    payload_hash: str
    payload_json: str
    idempotency_key: str
    receipt: t.ControlCommandReceipt
    request: t.ControlCommandRequest | None


class DurableInbox:
    def __init__(self, uow, authority):
        """authority(tx, actor, scope) -> current ControlGrant, or None.

        It must share this transaction's synchronization with grant writers.
        Missing dependencies are never replaced by an in-memory production store.
        """
        self.uow = uow
        self.authority = authority

    def enqueue(self, *, actor, scope, operation, body, key, expected_revision,
                ownership_epoch, request=None, review=False, validate=None):
        if not isinstance(key, str) or not key.strip() or len(key) > 256:
            raise ControlError(400, 'INVALID_IDEMPOTENCY_KEY')
        if (type(expected_revision) is not int or expected_revision < 0
                or type(ownership_epoch) is not int or ownership_epoch < 1):
            raise ControlError(422, 'INVALID_CONTROL_REQUEST')
        if scope.environment == t.Environment.LIVE:
            raise ControlError(403, 'LIVE_NOT_QUALIFIED')
        digest = canonical_hash({'operation': operation, 'body': body})
        record_id = 'control-key:' + canonical_hash({
            'identity': actor.identity_id, 'environment': str(actor.environment),
            'operation': operation, 'key': key})
        with self.uow.transaction(scope) as tx:
            # Serialize identical keys across all account/instrument scopes.
            # No Python hash(): it is process-randomized. Collisions only serialize.
            mutex = int.from_bytes(bytes.fromhex(record_id.split(':')[1])[:8], 'big', signed=True)
            tx.execute('SELECT pg_advisory_xact_lock(%s)', (mutex,))
            grant = require_grant(self.authority(tx, actor, scope), actor, scope, operation)
            row = tx.execute('SELECT payload FROM ledger.control_commands WHERE record_id=%s '
                             'ORDER BY sequence DESC LIMIT 1', (record_id,)).fetchone()
            if row is not None:
                old = decode(InboxRecord, row[0])
                if old.payload_hash != digest or old.scope != scope:
                    raise ControlError(409, 'IDEMPOTENCY_CONFLICT')
                if (old.authorization_id != actor.authorization_id
                        or old.deployment_id != actor.deployment_id):
                    raise ControlError(403, 'AUTHORIZATION_SCOPE_MISMATCH')
            if (expected_revision != grant.actor.policy_revision
                    or actor.policy_revision != grant.actor.policy_revision):
                raise ControlError(409, 'POLICY_CHANGED')
            if (ownership_epoch != grant.actor.ownership_epoch
                    or actor.ownership_epoch != grant.actor.ownership_epoch):
                raise ControlError(409, 'STALE_OWNER_EPOCH')
            if row is not None:
                return old.receipt
            if review and digest not in grant.reviewed_hashes:
                raise ControlError(403, 'USER_REVIEW_REQUIRED')
            if validate is not None:
                validate(tx, grant)
            receipt = t.ControlCommandReceipt(command_id=str(uuid4()), status='QUEUED',
                reason='EXECUTION_OWNER_UNCONFIRMED', existing_result_ref=None)
            record = InboxRecord(identity_id=actor.identity_id,
                authorization_id=actor.authorization_id, deployment_id=actor.deployment_id,
                scope=scope, operation=operation, payload_hash=digest,
                payload_json=canonical_json(body), idempotency_key=key,
                receipt=receipt, request=request)
            tx._append('control_commands', record_id, record, 0)
            tx._append('control_commands', 'control-id:' + receipt.command_id, record, 0)
        return receipt  # only after commit; no platform write/outbox ExecutionCommand


class ControlCommandService:
    def __init__(self, inbox):
        self.inbox = inbox

    def enqueue(self, request: t.ControlCommandRequest, actor: t.ActorContext,
                key: str) -> t.ControlCommandReceipt:
        if (request.action not in ('pause_new', 'cancel_entries', 'exit_owned', 'transfer', 'reconcile')
                or type(request.expected_revision) is not int or request.expected_revision < 0
                or type(request.ownership_epoch) is not int or request.ownership_epoch < 1):
            raise ControlError(422, 'INVALID_CONTROL_REQUEST')
        if request.action == 'transfer':
            transfer = request.transfer
            if (transfer is None or transfer.scope != request.scope
                    or transfer.ownership_epoch != request.ownership_epoch
                    or transfer.old_owner == transfer.new_owner
                    or not transfer.transfer_id or not transfer.old_owner or not transfer.new_owner
                    or transfer.mode not in ('TRANSFER_OWNER', 'RETAIN_OWNER', 'EXIT_THEN_SWITCH')):
                raise ControlError(422, 'INVALID_TRANSFER')
        elif request.transfer is not None:
            raise ControlError(422, 'UNEXPECTED_TRANSFER')
        return self.inbox.enqueue(actor=actor, scope=request.scope,
            operation='enqueueControl', body=domain_payload(request), key=key,
            expected_revision=request.expected_revision, ownership_epoch=request.ownership_epoch,
            request=request, review=request.action == 'transfer')


class ControlQuery:
    def __init__(self, inbox):
        self.inbox = inbox

    def get(self, command_id: str, actor: t.ActorContext) -> t.ControlCommandProjection:
        for scope in actor.scopes:
            if scope.environment != actor.environment or scope.environment == t.Environment.LIVE:
                continue
            with self.inbox.uow.transaction(scope) as tx:
                require_grant(self.inbox.authority(tx, actor, scope), actor, scope, 'readControl')
                record = tx._latest('control_commands', 'control-id:' + command_id, InboxRecord)
                if record is None:
                    continue
                if (record.identity_id != actor.identity_id
                        or record.scope != scope
                        or record.authorization_id != actor.authorization_id
                        or record.deployment_id != actor.deployment_id):
                    raise ControlError(404, 'CONTROL_NOT_FOUND')
                if record.request is None:
                    # Frozen projection has only ControlCommandRequest, not policy/deployment.
                    raise ControlError(503, 'CONTROL_PROJECTION_UNAVAILABLE')
                return t.ControlCommandProjection(receipt=record.receipt, request=record.request,
                    payload_hash=record.payload_hash, idempotency_key=record.idempotency_key,
                    sequence=1, incomplete_reasons=('EXECUTION_OWNER_UNCONFIRMED',))
        raise ControlError(404, 'CONTROL_NOT_FOUND')
