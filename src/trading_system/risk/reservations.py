"""Offline atomic reservation service. Never sends commands or enables LIVE.

load_inputs(tx, intent, book) is a trusted, local-only composition boundary:
reload authorization/capability and all group/strategy limits from durable facts
under the collateral mutex on EVERY attempt. It must not call a venue or LLM.
An absent input/proof loader fails closed; no implicit unlimited budgets.
"""
from dataclasses import dataclass, replace
from decimal import Decimal, localcontext
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.wire import TradeIntent, ExecutionCommand, RiskPolicy, ABSENT
from trading_system.accounts.authorization import valid_evidence
from trading_system.storage.events import EventStore
from trading_system.storage.models import ScopeMismatch, MissingEvidence, account_key
from .evaluator import RiskEvaluator, RiskInputs
from .reservation_repository import ReservationRepository, merge_holds, balance_watermark

D = Decimal


@dataclass(frozen=True, kw_only=True)
class ReservationInputs:
    risk: RiskInputs
    market: t.SnapshotPacket
    policy: RiskPolicy
    authorization: t.AuthorizationDecision
    command: ExecutionCommand
    # Remaining caps after actual positions/external obligations; local holds
    # are deducted here. Amounts are asset units, never mixed-currency sums.
    group_capacity: tuple[t.Money, ...]
    strategy_capacity: tuple[t.Money, ...]
    buffer: tuple[t.Money, ...]


@dataclass(frozen=True, kw_only=True)
class ReconciliationProof:
    """Trusted ledger reader's mapping, not user/venue payload authorization.

    The reader must establish order/fill coverage and actual balance inclusion.
    Public ReservationEvidence alone cannot establish this binding.
    """
    evidence: t.ReservationEvidence
    scope: t.ScopeKey
    intent_id: str
    asset: str
    command_refs: tuple[str, ...]
    book_sequence: int
    obligations_cleared: bool


def amounts(values):
    result = {}
    for value in values:
        if (not value.currency or value.currency in result or not isinstance(value.amount, Decimal)
                or not value.amount.is_finite()
                or value.amount < 0):
            raise ValueError('invalid or ambiguous asset amounts')
        result[value.currency] = value.amount
    return result


def complete(coverage, scope):
    return (coverage.scope == scope and coverage.query_succeeded and coverage.supported
            and coverage.pagination_complete and not coverage.missing)


class ReservationService:
    def __init__(self, uow, scope: t.ScopeKey, load_inputs, *, clock,
                 load_proof=None, maximum_attempts=3):
        if scope.environment == t.Environment.LIVE:
            raise ValueError('LIVE_DISABLED')
        if type(maximum_attempts) is not int or not 1 <= maximum_attempts <= 10:
            raise ValueError('invalid retry bound')
        self.uow, self.scope, self.load_inputs = uow, scope, load_inputs
        self.clock, self.load_proof = clock, load_proof
        self.maximum_attempts = maximum_attempts

    def reserve(self, intent: TradeIntent, expected_book_sequence: int) -> t.ReservationOutcome:
        if type(expected_book_sequence) is not int or expected_book_sequence < 0:
            raise ValueError('invalid expected book sequence')
        if (intent.environment != self.scope.environment or intent.venue_account_id != self.scope.venue_account_id
                or intent.product != self.scope.product or intent.instrument_uid != self.scope.instrument_uid
                or intent.venue_leg != self.scope.venue_leg):
            raise ScopeMismatch('intent scope mismatch')
        return self._transaction(lambda tx: self._reserve(tx, intent, expected_book_sequence))

    def _transaction(self, operation):
        for attempt in range(self.maximum_attempts):
            try:
                with localcontext() as ctx:
                    ctx.prec = 80
                    with self.uow.transaction(self.scope) as tx:
                        result = operation(tx)
                return result  # only after commit succeeds
            except Exception as exc:
                if getattr(exc, 'sqlstate', None) not in ('40001', '40P01'):
                    raise
                if attempt + 1 == self.maximum_attempts:
                    raise
                # New transaction, book, holds, authority and risk calculation.
        raise AssertionError('unreachable')

    def _reserve(self, tx, intent, expected):
        repo = ReservationRepository(tx)
        book = tx.projections.book(self.scope)
        deny = lambda *codes: t.ReservationOutcome(accepted=False, reservations=(),
            book_sequence=book.sequence, rejection_codes=tuple(codes))
        if intent.collateral_group_id != tx.group_id:
            raise ScopeMismatch('collateral group mismatch')
        existing = repo.existing(intent)
        if existing is not None:
            risk = tx._latest('audit_events', 'ip07:risk:' + intent.intent_id, t.RiskDecision)
            if risk is None or risk.verdict != 'ALLOW':
                raise MissingEvidence('existing intent lacks an IP-07 atomic bundle')
            first_commands = repo.first_commands(intent.intent_id)
            if (len(first_commands) != 1 or first_commands[0].state != 'DURABLE_READY'
                    or amounts(risk.reserved_assets) != amounts(tuple(t.Money(
                    currency=p.reservation.asset, amount=p.reservation.amount) for p in existing))
                    or any(first_commands[0].command_id not in p.reservation.command_refs or any(
                        (command := tx.commands.get(cid)) is None or command.intent_id != intent.intent_id
                        for cid in p.reservation.command_refs) for p in existing)):
                raise MissingEvidence('existing intent has an incomplete atomic bundle')
            return t.ReservationOutcome(accepted=True, reservations=existing,
                book_sequence=book.sequence, rejection_codes=())
        if book.sequence != expected or book.basis_fingerprint != intent.basis_fingerprint:
            return deny('REPLAN_REQUIRED:STALE_BOOK')
        if not complete(book.coverage, self.scope) or not book.evidence:
            return deny('BOOK_INCOMPLETE')
        group = merge_holds(repo.group(), book)
        for p in group:
            r = p.reservation
            if (r.environment != self.scope.environment or r.collateral_group_id != tx.group_id
                    or not r.asset or not r.reservation_id
                    or any(not isinstance(v, Decimal) or not v.is_finite() or v < 0
                           for v in (r.amount, p.held, p.reflected, p.release_pending))
                    or p.reflected > p.held or p.held > r.amount or p.release_pending > p.held):
                return deny('INVALID_RESERVATION')
        book = replace(book, reservations=group)
        inputs = self.load_inputs(tx, intent, book)
        reading = self.clock()
        now = utc_timestamp(reading)
        if any(not valid_evidence(e, self.scope.environment, now) for e in book.evidence):
            return deny('BOOK_EVIDENCE_INVALID')
        if (inputs.authorization.allowed is not True or inputs.authorization.rejection_codes
                or inputs.authorization.authorization_id != intent.authorization_id
                or not inputs.authorization.evidence or any(not valid_evidence(e, self.scope.environment, now)
                    for e in inputs.authorization.evidence)):
            return deny('AUTHORIZATION_REQUIRED')
        if tx.risk_locks.active(self.scope):
            return deny('ACTIVE_RISK_LOCK')
        owner = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
            'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR SHARE',
            (*account_key(self.scope), self.scope.instrument_uid, self.scope.venue_leg)).fetchone()
        if owner != (intent.owner_strategy_id, intent.ownership_epoch, intent.risk_policy_revision):
            return deny('STALE_OWNER')
        if (now >= utc_timestamp(intent.valid_until) or now >= utc_timestamp(inputs.risk.expires_at)
                or utc_timestamp(inputs.risk.clock.utc_now) > now):
            return deny('EXPIRED_OR_UNBOUND_CLOCK')
        facts = replace(inputs.risk, clock=replace(inputs.risk.clock, utc_now=reading))
        decision = RiskEvaluator(facts).evaluate(intent, book, inputs.market, inputs.policy)
        if decision.verdict != 'ALLOW':
            return deny(*decision.rejection_codes)
        balances, limits, strategy, buffers = map(amounts,
            (book.balances, inputs.group_capacity, inputs.strategy_capacity, inputs.buffer))
        for asset, required in amounts(decision.reserved_assets).items():
            same = [p for p in group if p.reservation.asset == asset]
            held = sum((p.held for p in same), D(0))
            owned = sum((p.held for p in same if p.reservation.owner_strategy_id == intent.owner_strategy_id), D(0))
            unreflected = sum((max(p.held - p.reflected, p.release_pending) for p in same), D(0))
            if any(asset not in values for values in (balances, limits, strategy, buffers)) or required > min(
                    balances.get(asset, D(0)) - unreflected - buffers.get(asset, D(0)),
                    limits.get(asset, D(0)) - held, strategy.get(asset, D(0)) - owned):
                return deny('INSUFFICIENT_CAPACITY:' + asset)
        command = inputs.command
        if (command.intent_id != intent.intent_id or command.environment != intent.environment
                or command.venue_account_id != intent.venue_account_id or command.product != intent.product
                or command.instrument_uid != intent.instrument_uid or command.state != 'DURABLE_READY'
                or command.operation not in ('SUBMIT', 'CLOSE') or command.parent_command_id is not None
                or command.ownership_epoch != intent.ownership_epoch
                or command.policy_revision != intent.risk_policy_revision):
            raise ValueError('first command binding mismatch')
        # Quantity is delta, whereas IP-06 permitted_quantity is the target.
        actual = sum((p.actual_quantity.value for p in book.positions if p.scope == self.scope), D(0))
        target = decision.permitted_quantity.value * (-1 if intent.target_side == 'SHORT' else 1)
        delta = target - actual
        reducing = actual != 0 and actual * target >= 0 and abs(target) < abs(actual)
        if command.operation == 'CLOSE' and not reducing:
            raise ValueError('first command CLOSE requires a reduction')
        if (not delta or command.canonical_payload.quantity != format(abs(delta), 'f')
                or command.canonical_payload.side != ('BUY' if delta > 0 else 'SELL')
                or command.canonical_payload.order_type != intent.execution.order_preference):
            raise ValueError('first command differs from evaluated delta')
        if intent.execution.order_preference == 'LIMIT' and command.canonical_payload.price != intent.execution.limit_price:
            raise ValueError('first command price mismatch')
        payload = command.canonical_payload
        if (payload.quote_notional is not ABSENT or payload.trigger_price is not ABSENT
                or payload.full_position_exit is True
                or payload.position_side not in (ABSENT, intent.venue_leg)
                or (payload.position_side is ABSENT and intent.venue_leg != 'BOTH')
                or (intent.execution.order_preference == 'MARKET' and payload.price is not ABSENT)
                or command.target_order_id is not None or command.venue_order_id is not None):
            raise ValueError('unevaluated first command semantics')
        tx.intents.append(intent)
        repo.risk(decision)
        projections = []
        for money in decision.reserved_assets:
            reservation = t.CapitalReservation(reservation_id=str(uuid4()), environment=self.scope.environment,
                collateral_group_id=tx.group_id, venue_account_id=intent.venue_account_id,
                owner_strategy_id=intent.owner_strategy_id, intent_id=intent.intent_id,
                asset=money.currency, amount=money.amount, reflected_amount=D(0), state=t.ReservationState.HELD,
                created_at=reading, expires_at=decision.expires_at,
                command_refs=(command.command_id,), evidence_watermark='')
            tx.reservations.append(reservation, 0)
            projections.append(repo.initial(reservation))
        tx.commands.append(command, 0)
        return t.ReservationOutcome(accepted=True, reservations=tuple(projections),
                                   book_sequence=book.sequence, rejection_codes=())

    def reconcile(self, evidence: t.ReservationEvidence) -> t.ReservationProjection:
        return self._transaction(lambda tx: self._reconcile(tx, evidence))

    def _reconcile(self, tx, evidence):
        repo = ReservationRepository(tx)
        previous = repo.get(evidence.reservation_id)
        r = previous.reservation
        reading = self.clock()
        now = utc_timestamp(reading)
        if (not isinstance(evidence.reflected_amount, Decimal)
                or not evidence.reflected_amount.is_finite()
                or not 0 <= evidence.reflected_amount <= r.amount
                or evidence.venue_freeze_ref.environment != self.scope.environment
                or type(evidence.terminal_verified) is not bool
                or evidence.command_certainty not in ('UNKNOWN', 'NOT_SENT', 'CONFIRMED')):
            raise ValueError('invalid reconciliation evidence')
        book = tx.projections.book(self.scope)
        proof = self.load_proof(tx, evidence) if self.load_proof else None
        watermark = evidence.venue_freeze_ref.id + ':' + evidence.venue_freeze_ref.hash
        mapped = (isinstance(proof, ReconciliationProof) and proof.evidence == evidence
            and proof.scope == self.scope and proof.intent_id == r.intent_id and proof.asset == r.asset
            and proof.command_refs == r.command_refs and type(proof.book_sequence) is int
            and proof.book_sequence == book.sequence and complete(book.coverage, self.scope)
            and evidence.venue_freeze_ref in book.evidence and evidence.watermark == watermark
            and valid_evidence(evidence.venue_freeze_ref, self.scope.environment, now))
        # A ready outbox command is still sendable. A caller's terminal flag
        # cannot release its backing assets; IP-10 must first persist an abort
        # or a reconciled outcome. VENUE_CONFIRMED alone is not order terminal.
        commands = tuple(tx.commands.get(cid) for cid in r.command_refs)
        bound = bool(commands) and all(c is not None and c.intent_id == r.intent_id for c in commands)
        unknown = (evidence.command_certainty == 'UNKNOWN' or not bound
            or any(c.state in ('MAY_HAVE_SENT', 'UNKNOWN') for c in commands if c is not None))
        command_proven = bound and (
            evidence.command_certainty == 'NOT_SENT'
            and all(c.state in ('NOT_SENT', 'ABORTED_UNSENT') for c in commands)
            or evidence.command_certainty == 'CONFIRMED'
            and all(c.state in ('VENUE_CONFIRMED', 'VENUE_REJECTED') for c in commands))
        # A freeze cannot belong to an unsent/aborted/rejected command. Unknown
        # sends may have a mapped freeze but still keep their full pending hold.
        mapped = mapped and bound and (evidence.reflected_amount == 0 or all(
            c.state in ('MAY_HAVE_SENT', 'UNKNOWN', 'VENUE_CONFIRMED') for c in commands))
        if unknown:
            repo.uncertain(r, reading)
        released = (mapped and command_proven and not unknown and evidence.terminal_verified
                    and proof.obligations_cleared is True and evidence.reflected_amount == 0)
        reflected = evidence.reflected_amount if mapped else D(0)
        state = (t.ReservationState.RELEASED if released else t.ReservationState.VENUE_REFLECTED
                 if reflected else t.ReservationState.RELEASE_PENDING)
        updated = replace(r, state=state, reflected_amount=reflected,
                          evidence_watermark=balance_watermark(book, evidence.venue_freeze_ref) if mapped else '')
        projection = t.ReservationProjection(reservation=updated, held=D(0) if released else r.amount,
            reflected=reflected, release_pending=r.amount if not mapped or unknown else D(0),
            sequence=previous.sequence + 1)
        if replace(projection, sequence=previous.sequence) == previous:
            return previous
        repo.save(projection)
        # Preserve the input and mapping used for this exact transition.
        tx._append('audit_events', 'ip07:evidence:' + r.reservation_id, evidence, previous.sequence)
        if mapped:
            tx._append('audit_events', 'ip07:proof:' + r.reservation_id + ':' + str(projection.sequence), proof, 0)
        EventStore().append(tx, t.DomainEvent(event_id=str(uuid4()), scope=self.scope,
            aggregate_id='ip07:reservation:' + r.reservation_id, revision=projection.sequence,
            occurred_at=reading, evidence=(evidence.venue_freeze_ref,), payload=projection), previous.sequence)
        return projection
