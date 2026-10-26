"""Append-only outbox consumption; delivery receipts are never send permits."""
from dataclasses import dataclass

from trading_system.contracts import types as t
from trading_system.storage.models import MissingEvidence, scope_id
from .commands import CommandRepository


@dataclass(frozen=True, kw_only=True)
class OutboxMessage:
    message_id: str
    command_id: str


class OutboxConsumer:
    def __init__(self, dispatcher, consumer_id: str):
        if not isinstance(consumer_id, str) or not consumer_id.strip():
            raise ValueError('consumer identity is required')
        self.dispatcher, self.consumer_id = dispatcher, consumer_id

    def pending(self, limit: int = 100) -> tuple[OutboxMessage, ...]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError('invalid batch limit')
        d = self.dispatcher
        with d.uow.transaction(d.scope) as tx:
            rows = tx.execute(
                'SELECT o.message_id,o.aggregate_id FROM ledger.outbox o '
                'WHERE o.scope_id=%s AND o.kind=\'COMMAND_READY\' AND NOT EXISTS '
                '(SELECT 1 FROM ledger.outbox_receipts r WHERE r.message_id=o.message_id '
                'AND r.consumer_id=%s) ORDER BY o.created_at,o.message_id LIMIT %s',
                (scope_id(d.scope), self.consumer_id, limit)).fetchall()
        return tuple(OutboxMessage(message_id=mid, command_id=cid) for mid, cid in rows)

    def consume(self, message_id: str, lease: t.OwnerLease) -> t.CommandReceipt:
        d = self.dispatcher
        with d.uow.transaction(d.scope) as tx:
            row = tx.execute('SELECT aggregate_id FROM ledger.outbox '
                "WHERE message_id=%s AND scope_id=%s AND kind='COMMAND_READY'",
                (message_id, scope_id(d.scope))).fetchone()
            if row is None:
                raise MissingEvidence('outbox message not found in consumer scope')
            command_id = row[0]
        receipt = d.dispatch(command_id, lease)
        with d.uow.transaction(d.scope) as tx:
            repo = CommandRepository(tx)
            persisted = repo.receipt(command_id)
            if persisted != receipt or persisted.status in (t.CommandState.DURABLE_READY, t.CommandState.MAY_HAVE_SENT):
                raise MissingEvidence('outbox requires a persisted outcome before acknowledgement')
            tx.execute('INSERT INTO ledger.outbox_receipts(message_id,consumer_id) VALUES (%s,%s) '
                       'ON CONFLICT DO NOTHING', (message_id, self.consumer_id))
        return receipt
