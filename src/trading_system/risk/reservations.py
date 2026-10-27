"""Offline atomic reservation service. Never sends commands or enables LIVE.

load_inputs(tx, intent, book) is a trusted, local-only composition boundary:
reload authorization/capability and all group/strategy limits from durable facts
under the collateral mutex on EVERY attempt. It must not call a venue or LLM.
An absent input/proof loader fails closed; no implicit unlimited budgets.
"""
from dataclasses import dataclass, replace
from datetime import timedelta
from decimal import Decimal, localcontext
from time import monotonic_ns
from uuid import uuid4

from trading_system.contracts import types as t
from trading_system.contracts.canonical import utc_timestamp
from trading_system.contracts.wire import TradeIntent, ExecutionCommand, RiskPolicy, ABSENT
from trading_system.accounts.authorization import valid_evidence
from trading_system.storage.events import EventStore
from trading_system.storage.models import ScopeMismatch, MissingEvidence, account_key, scope_id, decode
from trading_system.execution.commands import CommandRepository
from .evaluator import RiskEvaluator, RiskInputs
from .reservation_repository import ReservationRepository, merge_holds, balance_watermark
from .locked_actions import LockedActionVerifier
from .target_guard import TargetBasis, target_obligations
from trading_system.intents.semantic_validation import ProposalRejected
from trading_system.intents.target_compiler import TargetInputs, compile_locked

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
    target: TargetInputs | None = None


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


@dataclass(frozen=True, kw_only=True)
class ReleasedBalanceAttestation:
    """Trusted current collateral-account balance reader, never an API input.

    The reader must independently derive the current balances from the complete
    economic ledger, while the owner/control and collateral locks are held.
    Only an already RELEASED reservation can be re-attested; this cannot turn
    a held, pending, unknown or merely acknowledged command into a release.
    """
    transaction_id: str
    book_hash: str
    reservation_ids: tuple[str, ...]
    evidence: tuple[t.EvidenceRef, ...]


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
                 load_proof=None, maximum_attempts=3, load_action_proof=None,
                 monotonic_clock=monotonic_ns, load_released_balances=None):
        if scope.environment == t.Environment.LIVE:
            raise ValueError('LIVE_DISABLED')
        if type(maximum_attempts) is not int or not 1 <= maximum_attempts <= 10:
            raise ValueError('invalid retry bound')
        self.uow, self.scope, self.load_inputs = uow, scope, load_inputs
        self.clock, self.load_proof = clock, load_proof
        self.load_action_proof = load_action_proof
        self.load_released_balances = load_released_balances
        self.monotonic_clock = monotonic_clock
        self.maximum_attempts = maximum_attempts

    def reserve(self, intent: TradeIntent, expected_book_sequence: int) -> t.ReservationOutcome:
        if type(expected_book_sequence) is not int or expected_book_sequence < 0:
            raise ValueError('invalid expected book sequence')
        if (intent.environment != self.scope.environment or intent.venue_account_id != self.scope.venue_account_id
                or intent.product != self.scope.product or intent.instrument_uid != self.scope.instrument_uid
                or intent.venue_leg != self.scope.venue_leg):
            raise ScopeMismatch('intent scope mismatch')
        return self._transaction(lambda tx: self._reserve(tx, intent, expected_book_sequence))

    def reserve_proposal(self, intent_id: str) -> t.ReservationOutcome:
        """Revalidate one immutable durable intake; a receipt ID is no permit.

        The UI/AI/RULE runtime must explicitly compose this domain service.
        Existing production console shortcuts are not wired by this method.
        The original intake sequence/rules/context cannot be supplied anew by
        a caller after a manual position change or deployment revision.
        """
        from trading_system.intents.ingress import AcceptedProposal, accepted_intent
        from trading_system.storage.models import decode
        if not isinstance(intent_id, str) or not intent_id:
            raise ValueError('durable proposal intent ID required')

        def operation(tx):
            rows = tx.execute("SELECT payload FROM ledger.proposals WHERE scope_id=%s AND sequence=1 "
                "AND payload->'receipt'->>'intent_id'=%s", (scope_id(self.scope), intent_id)).fetchall()
            if len(rows) != 1:
                raise MissingEvidence('one durable accepted proposal is required')
            record = decode(AcceptedProposal, rows[0][0])
            intent = accepted_intent(record)
            if intent.intent_id != intent_id or record.receipt.status != t.IntentState.VALIDATING:
                raise MissingEvidence('proposal intent binding mismatch')
            return self._reserve(tx, intent, record.book_sequence, accepted=record)

        return self._transaction(operation)

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

    def _reserve(self, tx, intent, expected, *, accepted=None):
        repo = ReservationRepository(tx)
        started_ns = self.monotonic_clock()
        reading = self.clock()
        now = utc_timestamp(reading)
        started_at = now
        # Lock management before reading any target, authority or book facts.
        owner = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
            'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR UPDATE',
            (*account_key(self.scope), self.scope.instrument_uid, self.scope.venue_leg)).fetchone()
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
            basis = tx._latest('audit_events', 'ip07:target-basis:' + first_commands[0].command_id, TargetBasis)
            if basis is None or basis.command_id != first_commands[0].command_id:
                raise MissingEvidence('existing intent lacks its atomic target basis')
            # Replay reports the original atomic receipt. Current hold states
            # are separate projections and cannot renew this sendable intent.
            original = tuple(repo.initial(r) for r in tx.reservations.for_intent(intent.intent_id))
            return t.ReservationOutcome(accepted=True, reservations=original,
                book_sequence=basis.book_sequence, rejection_codes=())
        if owner != (intent.owner_strategy_id, intent.ownership_epoch, intent.risk_policy_revision):
            return deny('STALE_OWNER')
        if book.sequence != expected or book.basis_fingerprint != intent.basis_fingerprint:
            return deny('REPLAN_REQUIRED:STALE_BOOK')
        if not complete(book.coverage, self.scope) or not book.evidence:
            return deny('BOOK_INCOMPLETE')
        originals = repo.group()
        group = merge_holds(originals, book)
        released_proof = None
        if self.load_released_balances:
            from trading_system.risk.target_guard import digest
            proof = self.load_released_balances(tx, book, originals)
            if (type(proof) is not ReleasedBalanceAttestation or proof.transaction_id != tx.transaction_id
                    or proof.book_hash != digest(book) or proof.evidence != book.evidence
                    or len(set(proof.reservation_ids)) != len(proof.reservation_ids)):
                return deny('RELEASE_BALANCE_ATTESTATION_INVALID')
            restored = {}
            for original in originals:
                r = original.reservation
                if r.reservation_id not in proof.reservation_ids:
                    continue
                if (r.state != t.ReservationState.RELEASED or original.held or original.reflected
                        or original.release_pending or not r.command_refs):
                    return deny('RELEASE_BALANCE_ATTESTATION_INVALID')
                release_raw = tx.execute('SELECT scope_id,payload FROM ledger.audit_events WHERE record_id=%s',
                    ('ip07:proof:' + r.reservation_id + ':' + str(original.sequence),)).fetchone()
                release = decode(ReconciliationProof, release_raw[1]) if release_raw else None
                if (release is None or not release.obligations_cleared or not release.evidence.terminal_verified
                        or release.intent_id != r.intent_id or release.command_refs != r.command_refs
                        or release.asset != r.asset or release.evidence.reflected_amount != 0
                        or any(utc_timestamp(e.visible_at) < utc_timestamp(release.evidence.venue_freeze_ref.visible_at)
                            for e in book.evidence)):
                    return deny('RELEASE_BALANCE_ATTESTATION_INVALID')
                # A persisted release is still insufficient if any command has
                # become uncertain, or its current order is not terminal.
                for cid in r.command_refs:
                    source = tx.execute('SELECT scope_id FROM ledger.commands WHERE record_id=%s AND sequence=1', (cid,)).fetchone()
                    if source is None:
                        return deny('RELEASE_BALANCE_ATTESTATION_INVALID')
                    raw = tx.execute('SELECT payload FROM ledger.commands WHERE scope_id=%s AND record_id=%s ORDER BY sequence DESC LIMIT 1',
                        (source[0], cid)).fetchone()
                    stored = decode(ExecutionCommand, raw[0])
                    outcome = tx.execute('SELECT payload FROM ledger.audit_events WHERE scope_id=%s AND record_id=%s ORDER BY sequence DESC LIMIT 1',
                        (source[0], 'ip10:outcome:' + cid)).fetchone()
                    from trading_system.execution.commands import CommandOutcome, command_hash
                    result = decode(CommandOutcome, outcome[0]) if outcome else None
                    order_raw = tx.execute('SELECT payload FROM ledger.order_projections WHERE scope_id=%s AND record_id=%s ORDER BY sequence DESC LIMIT 1',
                        (source[0], stored.logical_client_order_id)).fetchone()
                    order = decode(t.OrderProjection, order_raw[0]) if order_raw else None
                    if (result is None or result.command_id != cid or result.receipt.command_id != cid
                            or stored.intent_id != r.intent_id or result.request_hash != command_hash(stored)
                            or result.durable_record_ref != stored.durable_record_ref
                            or result.receipt.status not in (t.CommandState.VENUE_CONFIRMED, t.CommandState.VENUE_REJECTED)
                            or order is None or order.lifecycle_state not in ('FILLED', 'CANCELED', 'EXPIRED', 'REJECTED')
                            or order.command_certainty != 'CONFIRMED' or order.pending_operation != 'NONE'
                            or order.leaves_quantity.value != 0 or scope_id(order.order.identity.scope) != source[0]
                            or order.order.identity.logical_client_order_id != stored.logical_client_order_id
                            or order.order.identity.venue_client_order_id != stored.venue_client_order_id
                            or order.order.ownership_epoch != stored.ownership_epoch
                            or order.order.owner_strategy_id != r.owner_strategy_id):
                        return deny('RELEASE_BALANCE_ATTESTATION_INVALID')
                restored[r.reservation_id] = original
            if set(restored) != set(proof.reservation_ids):
                return deny('RELEASE_BALANCE_ATTESTATION_INVALID')
            group = tuple(restored.get(p.reservation.reservation_id, p) for p in group)
            released_proof = proof
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
        if type(started_ns) is not int or started_ns < 0:
            return deny('EXPIRED_OR_UNBOUND_CLOCK')
        if any(not valid_evidence(e, self.scope.environment, now) for e in book.evidence):
            return deny('BOOK_EVIDENCE_INVALID')
        if (inputs.authorization.allowed is not True or inputs.authorization.rejection_codes
                or inputs.authorization.authorization_id != intent.authorization_id
                or not inputs.authorization.evidence or any(not valid_evidence(e, self.scope.environment, now)
                    for e in inputs.authorization.evidence)):
            return deny('AUTHORIZATION_REQUIRED')
        if (now >= utc_timestamp(intent.valid_until) or now >= utc_timestamp(inputs.risk.expires_at)
                or utc_timestamp(inputs.risk.clock.utc_now) > now):
            return deny('EXPIRED_OR_UNBOUND_CLOCK')
        if CommandRepository(tx).unresolved_in_group(inputs.command.command_id):
            return deny('ACTIVE_RISK_LOCK')
        action_until = LockedActionVerifier(self.load_action_proof).verify(
            tx, inputs.command, intent, reading, book=book)
        if action_until is None:
            return deny('ACTIVE_RISK_LOCK')
        expires = min(map(utc_timestamp, (action_until, intent.valid_until,
            intent.execution.entry_deadline, inputs.risk.expires_at)))
        last_at, last_ns = started_at, started_ns

        def current_time():
            nonlocal last_at, last_ns
            # Both clocks are trusted service readers, never intent fields.
            # A backwards wall-clock step cannot extend the monotonic window.
            duration = expires - started_at
            deadline_ns = started_ns + ((duration.days * 86400 + duration.seconds) * 1_000_000_000
                                       + duration.microseconds * 1000)
            current_reading = self.clock()
            current_at, current_ns = utc_timestamp(current_reading), self.monotonic_clock()
            if (type(current_ns) is not int or not last_ns <= current_ns < deadline_ns
                    or not last_at <= current_at < expires):
                return None
            last_at, last_ns = current_at, current_ns
            return current_reading, current_at, current_ns

        # Proof readers may be slow, including local database/archive reads.
        current = current_time()
        if current is None:
            return deny('EXPIRED_OR_UNBOUND_CLOCK')
        reading, now, checked_ns = current
        owner = tx.execute('SELECT owner_strategy_id,ownership_epoch,policy_revision FROM ledger.active_owners '
            'WHERE environment=%s AND account_id=%s AND product=%s AND instrument_uid=%s AND venue_leg=%s FOR SHARE',
            (*account_key(self.scope), self.scope.instrument_uid, self.scope.venue_leg)).fetchone()
        if owner != (intent.owner_strategy_id, intent.ownership_epoch, intent.risk_policy_revision):
            return deny('STALE_OWNER')
        facts = replace(inputs.risk, clock=replace(inputs.risk.clock, utc_now=reading, monotonic_ns=checked_ns))
        decision = RiskEvaluator(facts).evaluate(intent, book, inputs.market, inputs.policy)
        if decision.verdict != 'ALLOW':
            return deny(*decision.rejection_codes)
        if inputs.target is None:
            return deny('TARGET_INPUTS_REQUIRED')
        if accepted is not None and (inputs.target.context != accepted.context
                or inputs.target.expected_instrument != accepted.instrument):
            return deny('PROPOSAL_TARGET_BINDING_MISMATCH')
        if (inputs.target.instrument != facts.instrument or inputs.target.session != facts.session
                or inputs.target.order_sides != facts.order_sides
                or inputs.target.allow_round_down != facts.allow_round_down
                or (inputs.target.context is not None and inputs.target.context.snapshot_ref != inputs.market.ref)):
            return deny('TARGET_INPUT_BINDING_MISMATCH')
        target_book = None
        target_facts = None

        def load_target_book(locked_tx):
            nonlocal target_book, target_facts
            current_book = locked_tx.projections.book(self.scope)
            if (current_book.sequence != expected or current_book.basis_fingerprint != intent.basis_fingerprint):
                raise ProposalRejected('REPLAN_REQUIRED:STALE_BOOK')
            # Capital holds span the collateral pool. Quantity holds span only
            # this exact account/product/instrument/leg, found by durable scope.
            local_ids = {row[0] for row in locked_tx.execute(
                'SELECT record_id FROM ledger.reservations WHERE scope_id=%s',
                (scope_id(self.scope),)).fetchall()}
            external_ids = {p.reservation.reservation_id for p in current_book.reservations
                if p.reservation.reservation_id not in {p.reservation.reservation_id for p in repo.group()}}
            scope_holds = tuple(p for p in group if p.reservation.reservation_id in local_ids | external_ids)
            pending = target_obligations(locked_tx, intent, current_book, scope_holds, now)
            pending_ids = {identifier for p in pending for identifier in p.reservation_ids}
            # A verified terminal history contributes actual fills, not working
            # leaves. Financial release/reflection remains independently gated.
            target_book = replace(current_book, reservations=tuple(p for p in scope_holds
                if p.reservation.reservation_id in pending_ids | external_ids))
            duration = expires - started_at
            target_facts = replace(inputs.target, clock=facts.clock, pending=pending,
                deadline_monotonic_ns=min(inputs.target.deadline_monotonic_ns,
                    started_ns + ((duration.days * 86400 + duration.seconds)
                    * 1_000_000_000 + duration.microseconds * 1000)))
            return target_book

        try:
            plan = compile_locked(tx, intent, lambda *args: target_facts, load_book=load_target_book)
        except ProposalRejected as exc:
            return deny(str(exc))
        if plan.cancel_first:
            return deny('TARGET_CANCEL_AND_RECONCILE_REQUIRED')
        if plan.remaining.value == 0:
            return deny('TARGET_ALREADY_SATISFIED')
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
        actual = plan.actual.value
        target = decision.permitted_quantity.value * (-1 if intent.target_side == 'SHORT' else 1)
        if target != plan.target.value:
            return deny('TARGET_RISK_QUANTITY_MISMATCH')
        delta = plan.remaining.value
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
        age = (facts.emergency.maximum_quote_age_ms if reducing and facts.emergency is not None
               else inputs.policy.maximum_quote_age_ms)
        expires = min(expires, utc_timestamp(decision.expires_at),
            utc_timestamp(inputs.market.quote.occurred_at) + timedelta(milliseconds=age))
        decision = replace(decision, expires_at=expires.isoformat())
        # Recheck after owner/database reads, risk evaluation and capacity work,
        # immediately before the first write of the atomic intent bundle.
        current = current_time()
        if current is None or current[1] >= utc_timestamp(decision.expires_at):
            return deny('EXPIRED_OR_UNBOUND_CLOCK')
        reading = current[0]
        tx.intents.append(intent)
        if released_proof is not None:
            tx._append('audit_events', 'ip07:released-balances:' + intent.intent_id, released_proof, 0)
        repo.risk(decision)
        tx._append('audit_events', 'ip07:target-basis:' + command.command_id,
            TargetBasis(command_id=command.command_id, book_sequence=book.sequence,
                actual=plan.actual, instrument=inputs.target.instrument), 0)
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
        commands = tuple(CommandRepository(tx).get(cid) for cid in r.command_refs)
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
