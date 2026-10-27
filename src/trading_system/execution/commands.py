"""Durable command identities and conservative outcomes over the IP-03 ledger.

The existing SQL guard permits only READY -> MAY_HAVE_SENT in ledger.commands.
Later outcomes live in typed, append-only audit records. Consumers needing the
effective state MUST use this repository; the raw IP-03 row remains conservative.
No migration, credentials, transport, or automatic retry is provided here.
"""
from dataclasses import replace

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, canonical_json
from trading_system.contracts.wire import ContractCodec, ExecutionCommand, TradeIntent
from trading_system.storage.models import (
    MissingEvidence, ScopeMismatch, SequenceConflict, account_key, atomic_write,
    decode, encode, scope_id,
)
from trading_system.storage.command_state import CommandOutcome, command_hash, command_request
from trading_system.storage.uow import CommandRepository as LedgerCommands, LedgerTx


OUTCOME_PREFIX = 'ip10:outcome:'


class IdempotencyConflict(SequenceConflict):
    pass


class CommandRepository(LedgerCommands):
    def get(self, command_id: str) -> ExecutionCommand | None:
        command = super().get(command_id)
        outcome = self.tx._latest('audit_events', OUTCOME_PREFIX + command_id, CommandOutcome)
        if outcome is None:
            return command
        if (command is None or outcome.command_id != command_id
                or outcome.receipt.command_id != command_id
                or outcome.request_hash != command_hash(command)
                or outcome.durable_record_ref != command.durable_record_ref):
            raise MissingEvidence('command outcome binding mismatch')
        if outcome.receipt.status in (t.CommandState.ABORTED_UNSENT, t.CommandState.NOT_SENT):
            if (command.state != 'ABORTED_UNSENT'
                    or outcome.receipt.status != t.CommandState.ABORTED_UNSENT
                    or outcome.provenance != 'LOCAL_UNSENT_ABORT'):
                raise MissingEvidence('unsent outcome lacks authoritative abort')
        elif command.state == 'ABORTED_UNSENT':
            raise MissingEvidence('outcome contradicts authoritative abort')
        provenance = 'LOCAL_POLICY' if outcome.provenance == 'LOCAL_UNSENT_ABORT' else outcome.provenance
        return replace(command, state=str(outcome.receipt.status), outcome_provenance=provenance)

    def receipt(self, command_id: str, reason: str | None = None) -> t.CommandReceipt:
        command = self.get(command_id)
        if command is None:
            raise MissingEvidence('command not found in scope')
        outcome = self.tx._latest('audit_events', OUTCOME_PREFIX + command_id, CommandOutcome)
        if outcome is not None:
            return outcome.receipt
        return t.CommandReceipt(receipt_id='ip10:' + command_id, command_id=command_id,
            status=t.CommandState(command.state), reason=reason,
            existing_result_ref=command.durable_record_ref)

    @atomic_write
    def append(self, command: ExecutionCommand, expected_sequence: int = 0) -> None:
        if expected_sequence != 0 or command.state != 'DURABLE_READY':
            raise SequenceConflict('commands are immutable; no retry by append')
        body = ContractCodec().encode(command)
        if canonical_hash(body['canonical_payload']) != command.payload_hash:
            raise ValueError('command payload hash mismatch')
        existing = self.get(command.command_id)
        if existing is not None:
            if command_hash(existing) != command_hash(command):
                raise IdempotencyConflict('same command key with different request')
            return
        self.validate_binding(command)
        identity = self.tx.execute(
            'SELECT command_id FROM ledger.command_identities WHERE command_id=%s OR '
            '(environment=%s AND account_id=%s AND product=%s AND venue_client_order_id=%s)',
            (command.command_id, *account_key(self.tx.scope), command.venue_client_order_id)).fetchone()
        if identity is not None:
            raise IdempotencyConflict('command/client key already belongs to another request')
        # Changing attempt_id or venue ID cannot create another semantic command.
        duplicate = self.tx.execute(
            'SELECT 1 FROM ledger.commands WHERE scope_id=%s AND sequence=1 '
            "AND payload->>'intent_id'=%s AND payload->>'logical_client_order_id'=%s "
            "AND payload->>'operation'=%s AND payload->>'target_order_id' IS NOT DISTINCT FROM %s",
            (scope_id(self.tx.scope), command.intent_id, command.logical_client_order_id,
             command.operation, command.target_order_id)).fetchone()
        if duplicate is not None:
            raise IdempotencyConflict('semantic action already has a command identity')
        super().append(command, expected_sequence)

    def validate_binding(self, command: ExecutionCommand) -> TradeIntent:
        body = ContractCodec().encode(command)
        if canonical_hash(body['canonical_payload']) != command.payload_hash:
            raise ValueError('command payload hash mismatch')
        scope = self.tx.scope
        intent = self.tx._latest('intents', command.intent_id, TradeIntent)
        if (scope.environment not in (t.Environment.PAPER, t.Environment.RESEARCH)
                or intent is None or command.environment != scope.environment
                or command.venue_account_id != scope.venue_account_id
                or command.product != scope.product or command.instrument_uid != scope.instrument_uid
                or intent.environment != scope.environment or intent.venue_account_id != scope.venue_account_id
                or intent.product != scope.product or intent.instrument_uid != scope.instrument_uid
                or intent.venue_leg != scope.venue_leg or intent.collateral_group_id != self.tx.group_id):
            raise ScopeMismatch('offline command/intent scope mismatch')
        if (command.ownership_epoch != intent.ownership_epoch
                or command.policy_revision != intent.risk_policy_revision):
            raise SequenceConflict('command epoch/policy differs from intent')
        if command.operation in ('CANCEL', 'AMEND') and not command.target_order_id:
            raise MissingEvidence('cancel/amend requires an owned target order')
        owner = self.tx.execute(
            'SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
            'WHERE environment=%s AND account_id=%s AND product=%s '
            'AND instrument_uid=%s AND venue_leg=%s FOR SHARE',
            (*account_key(scope), scope.instrument_uid, scope.venue_leg)).fetchone()
        if owner != (intent.owner_strategy_id, command.ownership_epoch, command.policy_revision):
            raise SequenceConflict('current owner/policy is missing or stale')
        return intent

    @atomic_write
    def mark_may_have_sent(self, tx: LedgerTx, command_id: str,
                           expected_state: str) -> t.DurablePermit:
        if tx is not self.tx:
            raise ValueError('foreign transaction')
        command = self.get(command_id)
        if command is None:
            raise MissingEvidence('command not found')
        self.validate_binding(command)
        # The IP-03 implementation commits synchronously before issuing a permit.
        return super().mark_may_have_sent(tx, command_id, expected_state)

    def unresolved_in_group(self, excluding: str) -> tuple[str, ...]:
        """A missing outcome for MAY_HAVE_SENT is unresolved, including DB loss.

Use the collateral mutex held by the UoW, across accounts/products/instruments.
Unknown holds are never released here; every operation is conservatively blocked.
        """
        rows = self.tx.execute(
            'SELECT c.record_id,c.payload,o.payload FROM '
            '(SELECT DISTINCT ON (scope_id,record_id) scope_id,record_id,payload '
            'FROM ledger.commands ORDER BY scope_id,record_id,sequence DESC) c '
            'JOIN ledger.account_bindings b ON b.environment=c.payload->>\'environment\' '
            "AND b.account_id=c.payload->>'venue_account_id' AND b.product=c.payload->>'product' "
            'LEFT JOIN LATERAL (SELECT payload FROM ledger.audit_events '
            'WHERE scope_id=c.scope_id AND record_id=%s || c.record_id '
            'ORDER BY sequence DESC LIMIT 1) o ON TRUE '
            'WHERE b.environment=%s AND b.collateral_group_id=%s AND c.record_id<>%s '
            "AND c.payload->>'state' IN ('MAY_HAVE_SENT','UNKNOWN')",
            (OUTCOME_PREFIX, str(self.tx.scope.environment), self.tx.group_id, excluding)).fetchall()
        unresolved = []
        for cid, raw, outcome_raw in rows:
            command = decode(ExecutionCommand, raw)
            outcome = None if outcome_raw is None else decode(CommandOutcome, outcome_raw)
            if (outcome is None or outcome.command_id != cid
                    or outcome.request_hash != command_hash(command)
                    or outcome.durable_record_ref != command.durable_record_ref
                    or outcome.receipt.command_id != cid
                    or outcome.receipt.status not in (t.CommandState.VENUE_CONFIRMED, t.CommandState.VENUE_REJECTED)):
                unresolved.append(cid)
        return tuple(unresolved)

    @atomic_write
    def record_outcome(self, receipt: t.CommandReceipt) -> t.CommandReceipt:
        command = self.get(receipt.command_id)
        if command is None:
            raise MissingEvidence('missing command')
        old = self.tx._latest('audit_events', OUTCOME_PREFIX + command.command_id, CommandOutcome)
        if old is not None and old.receipt.status != t.CommandState.UNKNOWN:
            return old.receipt
        if command.state not in ('MAY_HAVE_SENT', 'UNKNOWN'):
            raise SequenceConflict('outcomes require a durable send boundary')
        if receipt.status not in (t.CommandState.UNKNOWN, t.CommandState.VENUE_CONFIRMED,
                                  t.CommandState.VENUE_REJECTED):
            raise ValueError('unproven local outcomes cannot resolve a command')
        if not receipt.receipt_id or (receipt.status != t.CommandState.UNKNOWN and not receipt.existing_result_ref):
            raise MissingEvidence('venue outcome requires a bridge evidence reference')
        if old is not None and old.receipt.status == receipt.status:
            return old.receipt
        self.tx._append('audit_events', OUTCOME_PREFIX + command.command_id,
            CommandOutcome(command_id=command.command_id, request_hash=command_hash(command),
                durable_record_ref=command.durable_record_ref, receipt=receipt,
                provenance='NONE' if receipt.status == t.CommandState.UNKNOWN else 'VENUE_EVENT'),
            0 if old is None else 1)
        return receipt

    @atomic_write
    def abort_unsent(self, command_id: str, reason: str) -> t.CommandReceipt:
        """Retire a ready command under the collateral mutex before any send.

        The raw command stays immutable. Once a send boundary exists, no local
        reason can prove NOT_SENT and the caller must reconcile the original ID.
        """
        command = self.get(command_id)
        if command is None or not reason or not reason.strip():
            raise MissingEvidence('command and abort reason required')
        if command.state == 'ABORTED_UNSENT':
            return self.receipt(command_id)
        if command.state != 'DURABLE_READY':
            raise SequenceConflict('cannot abort a potentially sent command')
        receipt = t.CommandReceipt(receipt_id='ip10:abort:' + command_id,
            command_id=command_id, status=t.CommandState.ABORTED_UNSENT,
            reason=reason, existing_result_ref=command.durable_record_ref)
        outcome = CommandOutcome(command_id=command_id, request_hash=command_hash(command),
            durable_record_ref=command.durable_record_ref, receipt=receipt,
            provenance='LOCAL_UNSENT_ABORT')
        self.tx.execute('SELECT ledger.abort_unsent_command(%s,%s,%s::jsonb,%s,%s::jsonb,%s)',
            (scope_id(self.tx.scope), command_id, encode(command), canonical_json(command_request(command)),
             encode(outcome), self.tx.transaction_id))
        self.tx._append('audit_events', OUTCOME_PREFIX + command_id, outcome, 0)
        return receipt

    @atomic_write
    def recover_unknown(self, command_id: str, now: str) -> t.CommandReceipt:
        command = self.get(command_id)
        # A concurrent consumer can observe the boundary and start recovery while
        # the original sender persists its receipt. Never downgrade that receipt
        # or manufacture a new unknown lock after the outcome is already known.
        if command is not None and command.state in ('VENUE_CONFIRMED', 'VENUE_REJECTED'):
            return self.receipt(command_id)
        if command is None or command.state not in ('MAY_HAVE_SENT', 'UNKNOWN'):
            raise SequenceConflict('only potentially sent commands require recovery')
        receipt = self.record_outcome(t.CommandReceipt(receipt_id='ip10:unknown:' + command_id,
            command_id=command_id, status=t.CommandState.UNKNOWN, reason='ORDER_OUTCOME_UNKNOWN',
            existing_result_ref=command.durable_record_ref))
        lock_id = 'ip10:unknown:' + command_id
        if self.tx._latest('risk_locks', lock_id, t.RiskLock) is None:
            self.tx.risk_locks.append(t.RiskLock(lock_id=lock_id, scope=self.tx.scope,
                cause='ORDER_OUTCOME_UNKNOWN', state='ACTIVE', permission=t.RiskPermission.RECOVERY_REQUIRED,
                required_evidence=('ORIGINAL_COMMAND_RECONCILIATION',), raised_at=now), 0)
        case_id = 'ip10:reconcile:' + command_id
        if self.tx._latest('reconciliation_cases', case_id, t.ReconciliationCase) is None:
            self.tx._append('reconciliation_cases', case_id, t.ReconciliationCase(
                case_id=case_id, scope=self.tx.scope, command_ids=(command_id,), position_ids=(),
                missing=('ORIGINAL_COMMAND_OUTCOME', 'FILLS_AND_PROTECTION_COVERAGE'),
                query_budget=t.QueryBudget(maximum_requests=1, window_seconds=60,
                    minimum_interval_ms=60000, retry_after=None), attempts=0, next_query_at=now, state='OPEN'), 0)
        return receipt
