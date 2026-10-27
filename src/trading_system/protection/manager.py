"""Partial-fill lifecycle over durable commands; PAPER/RESEARCH only.

Trusted composition supplies current ledger inputs, a bound command compiler,
and CommandDispatcher. No transport, authority issuance, retry sender or lock
resolution lives here. Persisting an action is mandatory before dispatch.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

from trading_system.contracts import types as t
from trading_system.contracts.canonical import canonical_hash, utc_timestamp
from trading_system.contracts.wire import ABSENT, ContractCodec, ExecutionCommandCanonicalPayload
from trading_system.execution.commands import CommandRepository
from trading_system.recovery.reconciler import utc_now, valid_ref
from trading_system.storage.events import EventStore
from trading_system.storage.models import SequenceConflict, decode, scope_id
from .native import (NativeCapability, NativeVerifier, closing_side, digest, offline,
                     owned, position_reported, report_errors, settled)
from .replacement import ReplacementProgress, advance, start


@dataclass(frozen=True, kw_only=True)
class EntryRisk:
    order: t.OrderProjection
    command_id: str


@dataclass(frozen=True, kw_only=True)
class ProtectionInputs:
    """Fresh ledger projection, after FillLedger ingestion; never sum fill twice."""
    fill_hash: str
    position: t.ManagedPosition
    plan: t.ProtectionPlan
    capability: NativeCapability
    entries: tuple[EntryRisk, ...]
    evidence: t.EvidenceRef
    take_profit_requested: bool = False


@dataclass(frozen=True, kw_only=True)
class CommandRequest:
    key: str
    operation: str
    position: t.ManagedPosition
    plan: t.ProtectionPlan
    quantity: t.Quantity
    target: t.OwnedOrderRef | None = None
    order_type: str = 'STOP_MARKET'
    price: t.Price | None = None


@dataclass(frozen=True, kw_only=True)
class CompiledCommand:
    command: t.ExecutionCommand
    order: t.OwnedOrderRef


class CommandCompiler:
    """Compile from a server-bound existing intent/parent and IP-11 ID registry.

    The template is not authority. IP-10 rechecks its durable intent, owner,
    policy and all current facts. No intent or durable permit is invented here.
    """
    def __init__(self, template: t.ExecutionCommand, codec, *, clock=utc_now):
        self.template, self.codec, self.clock = template, codec, clock

    def __call__(self, request: CommandRequest) -> CompiledCommand:
        p, plan, target = request.position, request.plan, request.target
        offline(p.scope)
        key = 'ip15:' + canonical_hash({'action': request.key, 'scope': digest(p.scope)})
        role = {'SET_PROTECTION': 'PROTECTION', 'CLOSE': 'CLOSE',
                'CANCEL': 'CANCEL', 'AMEND': 'AMEND'}[request.operation]
        client = self.codec.encode(key, p.scope.product, role)
        identity = self.codec.decode(client)
        payload = ExecutionCommandCanonicalPayload(position_side=p.scope.venue_leg)
        if request.operation != 'CANCEL':
            payload = replace(payload, side=closing_side(p), order_type=request.order_type,
                quantity=format(request.quantity.value, 'f'),
                # Bounds/closing direction stay local; equity has no derivative flags.
                full_position_exit=ABSENT if p.scope.product == t.Product.US_EQUITY else False,
                reduce_only=(ABSENT if p.scope.product == t.Product.US_EQUITY
                             else p.scope.product == t.Product.USD_M_PERP),
                trigger_price=format(plan.stop_price.value, 'f')
                    if request.operation in {'SET_PROTECTION', 'AMEND'} else ABSENT,
                price=format(request.price.value, 'f') if request.price else ABSENT,
                tokenize=False if p.scope.product == t.Product.US_EQUITY else ABSENT)
        command = replace(self.template, command_id=key, attempt_id=key + ':attempt:1',
            parent_command_id=self.template.command_id, logical_client_order_id=key,
            venue_client_order_id=client, venue_order_id=None,
            target_order_id=target.identity.logical_client_order_id if target else None,
            operation=request.operation, canonical_payload=payload, state='DURABLE_READY',
            outcome_provenance='NONE', durable_record_ref='ip15:queued:' + key,
            created_at=self.clock())
        body = ContractCodec().encode(replace(command, payload_hash='0' * 64))
        command = replace(command, payload_hash=canonical_hash(body['canonical_payload']))
        result = CompiledCommand(command=command, order=t.OwnedOrderRef(identity=identity,
            owner_strategy_id=p.owner_strategy_id, ownership_epoch=p.ownership_epoch))
        validate_command(request, result)
        return result


def validate_command(request: CommandRequest, compiled: CompiledCommand) -> None:
    p, c, ref = request.position, compiled.command, compiled.order
    scope, payload = p.scope, c.canonical_payload
    body = ContractCodec().encode(c)
    if (not owned(ref, p) or c.environment != scope.environment
            or c.venue_account_id != scope.venue_account_id or c.product != scope.product
            or c.instrument_uid != scope.instrument_uid or c.ownership_epoch != p.ownership_epoch
            or c.operation != request.operation or c.state != 'DURABLE_READY'
            or c.logical_client_order_id != ref.identity.logical_client_order_id
            or c.venue_client_order_id != ref.identity.venue_client_order_id
            or c.payload_hash != canonical_hash(body['canonical_payload'])
            or payload.position_side != scope.venue_leg):
        raise ValueError('COMMAND_BINDING_MISMATCH')
    target = request.target
    if ((target is not None and (not owned(target, p)
            or c.target_order_id != target.identity.logical_client_order_id))
            or (target is None and c.target_order_id is not None)):
        raise ValueError('OWNED_TARGET_REQUIRED')
    if request.operation in {'CANCEL', 'AMEND'} and target is None:
        raise ValueError('OWNED_TARGET_REQUIRED')
    if request.operation == 'CANCEL':
        return
    if (request.quantity.unit != p.actual_quantity.unit or request.quantity.value <= 0
            or request.quantity.value > p.actual_quantity.value.copy_abs()
            or payload.quantity != format(request.quantity.value, 'f')
            or payload.side != closing_side(p) or payload.order_type != request.order_type
            or (scope.product == t.Product.US_EQUITY and
                (payload.full_position_exit is not ABSENT or payload.reduce_only is not ABSENT))
            or (scope.product != t.Product.US_EQUITY and payload.full_position_exit is not False)
            or payload.quote_notional is not ABSENT
            or (scope.product == t.Product.USD_M_PERP and payload.reduce_only is not True)
            or (scope.product == t.Product.US_EQUITY and payload.tokenize is not False)):
        raise ValueError('REDUCTION_PARAMETERS_INVALID')
    if request.operation in {'SET_PROTECTION', 'AMEND'}:
        # The approved command payload has no trigger-source selector. Do not
        # silently encode MARK/BID_ASK as an adapter's default LAST trigger.
        if request.plan.stop_trigger != 'LAST':
            raise ValueError('TRIGGER_SOURCE_BINDING_UNAVAILABLE')
        if payload.trigger_price != format(request.plan.stop_price.value, 'f'):
            raise ValueError('STOP_PRICE_MISMATCH')
    if request.order_type == 'LIMIT' and (request.price is None
            or payload.price != format(request.price.value, 'f')):
        raise ValueError('BOUNDED_EXIT_PRICE_REQUIRED')


@dataclass(frozen=True, kw_only=True)
class ProtectionRecord:
    position: t.ManagedPosition
    plan: t.ProtectionPlan
    revision: int
    fill_hashes: tuple[str, ...]
    entries: tuple[EntryRisk, ...]
    command_ids: tuple[str, ...]
    locks: tuple[t.RiskLock, ...]
    case: t.ReconciliationCase
    missing: tuple[str, ...]
    replacement: ReplacementProgress | None = None
    exit_receipt: t.ExitReceipt | None = None
    exit_context_hash: str | None = None
    exit_started_at: str | None = None


class ProtectionRepository:
    """IP-03 append-only storage with CAS; all writes commit together.

    Does not create a database or inspect environment variables. The supplied
    UnitOfWork already enforces the collateral-group transaction mutex.
    """
    def __init__(self, uow):
        self.uow = uow

    @staticmethod
    def key(position_id):
        return 'ip15:position:' + position_id

    def load(self, scope, position_id):
        offline(scope)
        with self.uow.transaction(scope) as tx:
            return tx._latest('audit_events', self.key(position_id), ProtectionRecord)

    def commands(self, scope, ids):
        with self.uow.transaction(scope) as tx:
            result = tuple(CommandRepository(tx).get(key) for key in ids)
            if any(command is None for command in result):
                raise ValueError('DURABLE_COMMAND_MISSING')
            return result

    def load_plan(self, scope, plan_id):
        offline(scope)
        with self.uow.transaction(scope) as tx:
            rows = tx.execute('SELECT DISTINCT ON (record_id) payload FROM ledger.audit_events '
                "WHERE scope_id=%s AND record_id LIKE 'ip15:position:%%' "
                'ORDER BY record_id,sequence DESC', (scope_id(scope),)).fetchall()
            records = [decode(ProtectionRecord, row[0]) for row in rows]
            matches = [r for r in records if r.plan.plan_id == plan_id]
            if len(matches) > 1:
                raise ValueError('AMBIGUOUS_PLAN_BINDING')
            return matches[0] if matches else None

    def save(self, record, previous, commands=()):
        scope = record.position.scope
        offline(scope)
        with self.uow.transaction(scope) as tx:
            key = self.key(record.position.position_id)
            current = tx._latest('audit_events', key, ProtectionRecord)
            expected = 0 if previous is None else previous.revision
            if current != previous or record.revision != expected + 1:
                raise SequenceConflict('stale protection lifecycle')
            if record.plan.scope != scope or record.case.scope != scope:
                raise ValueError('LIFECYCLE_SCOPE_MISMATCH')
            for command in commands:
                CommandRepository(tx).append(command)
            for lock in record.locks:
                old = tx._latest('risk_locks', lock.lock_id, t.RiskLock)
                if old is None:
                    tx.risk_locks.append(lock, 0)
                    # Keep the same aggregate contract consumed by IP-08's
                    # dedicated cause-resolution path. This is a local GAP
                    # observation, never evidence of successful protection.
                    ref = t.EvidenceRef(id=lock.lock_id, hash=digest(record),
                        environment=scope.environment, source='IP15_LOCAL_LIFECYCLE',
                        visible_at=lock.raised_at)
                    EventStore().append(tx, t.DomainEvent(event_id=lock.lock_id + ':raised',
                        scope=scope, aggregate_id='risk-lock:' + lock.lock_id, revision=1,
                        occurred_at=lock.raised_at, evidence=(ref,), payload=lock), 0)
                elif old != lock:
                    raise SequenceConflict('cause lock changed; explicit resolution review required')
            tx._append('audit_events', key, record, expected)
            for table, record_id, value in (
                    ('protection_plans', record.plan.plan_id, record.plan),
                    ('reconciliation_cases', record.case.case_id, record.case)):
                sequence = tx.execute(f'SELECT COALESCE(max(sequence),0) FROM ledger.{table} '
                    'WHERE scope_id=%s AND record_id=%s', (scope_id(scope), record_id)).fetchone()[0]
                tx._append(table, record_id, value, sequence)


class ProtectionManager:
    def __init__(self, repository, dispatcher, lease: t.OwnerLease, *, read_inputs,
                 compile_command, verifier=None, clock=utc_now):
        self.repository, self.dispatcher, self.lease = repository, dispatcher, lease
        self.read_inputs, self.compile_command, self.clock = read_inputs, compile_command, clock
        self.verifier = verifier or NativeVerifier(clock=clock)

    def _action(self, record, commands=(), extra=()):
        return t.ProtectionActionPlan(plan=record.plan, commands=tuple(commands), locks=record.locks,
            missing=tuple(dict.fromkeys((*record.missing, *extra))))

    def _gap(self, position, plan, previous, now):
        lock_id = 'ip15:gap:' + digest(position.scope) + ':' + position.position_id
        locks = previous.locks if previous else ()
        if not locks:
            locks = (t.RiskLock(lock_id=lock_id, scope=position.scope, cause='PROTECTION_GAP',
                state='ACTIVE', permission=t.RiskPermission.EXIT_ONLY,
                required_evidence=('PROTECTION_OR_EXIT_CONFIRMED',), raised_at=now),)
        return replace(plan, state=t.ProtectionState.GAP, last_verified_at=None,
                       coverage_gap_since=plan.coverage_gap_since or now), locks

    def _case(self, position, commands, missing, previous=None):
        if previous:
            return replace(previous, command_ids=tuple(dict.fromkeys((*previous.command_ids, *commands))),
                missing=tuple(dict.fromkeys(missing)), state='OPEN')
        return t.ReconciliationCase(case_id='ip15:reconcile:' + position.position_id,
            scope=position.scope, command_ids=tuple(commands), position_ids=(position.position_id,),
            missing=tuple(dict.fromkeys(missing)), query_budget=t.QueryBudget(maximum_requests=1,
                window_seconds=60, minimum_interval_ms=60000, retry_after=None),
            attempts=0, next_query_at=self.clock(), state='OPEN')

    def _commit_dispatch(self, record, previous, commands=()):
        """Save the recovery obligation before the first dispatcher invocation.

        After any failure retain the original IDs. Even a local exception may
        hide a committed MAY_HAVE_SENT, so this service never automatically retries.
        """
        try:
            self.repository.save(record, previous, commands)
        except SequenceConflict:
            raise
        except Exception:
            return self._action(record, extra=('STORAGE_UNAVAILABLE', 'NOT_DISPATCHED'))
        failures = []
        for command in commands:
            try:
                receipt = self.dispatcher.dispatch(command.command_id, self.lease)
                if (not isinstance(receipt, t.CommandReceipt) or receipt.command_id != command.command_id
                        or receipt.status != t.CommandState.VENUE_CONFIRMED):
                    failures.append('QUERY_ORIGINAL:' + command.command_id)
            except Exception:
                failures.append('DISPATCH_INCOMPLETE:' + command.command_id)
            if failures:
                break  # Do not continue a dependent sequence after an uncertain write.
        if failures:
            updated = replace(record, revision=record.revision + 1,
                missing=tuple(dict.fromkeys((*record.missing, *failures))))
            updated = replace(updated, case=self._case(updated.position, updated.command_ids,
                                                       updated.missing, record.case))
            try:
                self.repository.save(updated, record)
                record = updated
            except Exception:
                failures.append('STORAGE_UNAVAILABLE')
        return self._action(record, commands, failures)

    def on_fill(self, fill: t.Fill, position: t.ManagedPosition) -> t.ProtectionActionPlan:
        offline(position.scope)
        if (fill.scope != position.scope or fill.order.scope != position.scope
                or fill.quantity.unit != position.actual_quantity.unit
                or not position.owner_strategy_id or not position.owner_book_id
                or position.ownership_epoch < 1):
            raise ValueError('FILL_OR_OWNER_SCOPE_MISMATCH')
        now = self.clock()
        inputs = self.read_inputs(fill, position)
        if (not isinstance(inputs, ProtectionInputs) or inputs.fill_hash != digest(fill)
                or inputs.position != position or inputs.plan.scope != position.scope
                or position.active_exit_plan_id not in (None, inputs.plan.plan_id)
                or not valid_ref(inputs.evidence, position.scope, now, 60)):
            raise ValueError('CURRENT_FILL_PROJECTION_REQUIRED')
        try:
            previous = self.repository.load(position.scope, position.position_id)
        except Exception:
            plan, locks = self._gap(position, inputs.plan, None, now)
            return t.ProtectionActionPlan(plan=plan, commands=(), locks=locks,
                missing=('STORAGE_UNAVAILABLE', 'NOT_DISPATCHED', 'PROTECTION_UNVERIFIED'))
        if previous and digest(fill) in previous.fill_hashes:
            return self._action(previous, extra=('DUPLICATE_FILL_NO_DISPATCH',))
        if previous and (previous.position.owner_strategy_id != position.owner_strategy_id
                or previous.position.owner_book_id != position.owner_book_id
                or previous.position.ownership_epoch != position.ownership_epoch):
            raise ValueError('OWNERSHIP_CHANGED_RECONCILIATION_REQUIRED')
        quantity = t.Quantity(value=position.actual_quantity.value.copy_abs(), unit=position.actual_quantity.unit)
        template = previous.plan if previous else inputs.plan
        plan = replace(template, coverage_quantity=quantity)
        plan, locks = self._gap(position, plan, previous, now)
        missing = ['PROTECTION_UNVERIFIED']
        commands = []
        original_ids = list(previous.command_ids if previous else ())
        capability = inputs.capability
        missing.extend(capability.errors(position, now))
        if not capability.tradable(quantity):
            missing.append('BELOW_VENUE_MINIMUM_OR_STEP')
        if (plan.mode != 'NATIVE' or plan.monitor_id is not None or 'STOP_MARKET' not in capability.order_types
                or plan.stop_trigger not in capability.triggers):
            missing.append('NATIVE_PROTECTION_UNAVAILABLE')
        if inputs.take_profit_requested:
            missing.append('VERIFIED_OCO_COMPILER_REQUIRED')
        if plan.native_order_ids or (previous and previous.command_ids):
            missing.append('QUERY_ORIGINAL_THEN_REPLACE')
        if previous and previous.exit_receipt is not None:
            missing.append('EXIT_IN_PROGRESS')
            plan = replace(plan, state=t.ProtectionState.EXITING)
        if quantity.value == 0:
            missing.append('ZERO_REQUIRES_FLAT_PROOF')
        if len(missing) == 1:
            request = CommandRequest(key=plan.plan_id + ':initial', operation='SET_PROTECTION',
                                     position=position, plan=plan, quantity=quantity)
            try:
                compiled = self.compile_command(request)
                validate_command(request, compiled)
                commands.append(compiled.command)
                plan = replace(plan, native_order_ids=(compiled.order,), state=t.ProtectionState.PENDING)
            except Exception:
                missing.append('COMMAND_COMPILATION_FAILED')
        # Entry uncertainty is retained verbatim; no reservation writes occur.
        entries = {e.order.order.identity.logical_client_order_id: e
                   for e in (previous.entries if previous else ())}
        for entry in inputs.entries:
            key = entry.order.order.identity.logical_client_order_id
            old = entries.get(key)
            if old and (entry.command_id != old.command_id
                    or not owned(entry.order.order, position)
                    or entry.order.sequence < old.order.sequence
                    or (entry.order.sequence == old.order.sequence and entry != old)):
                missing.append('ENTRY_PROJECTION_CONFLICT:' + key)
                continue
            entries[key] = entry
        for entry in entries.values():
            if not owned(entry.order.order, position):
                missing.append('ENTRY_OWNERSHIP_UNVERIFIED')
                continue
            if not settled(entry.order):
                original_ids.append(entry.command_id)
                missing.append('ENTRY_REMAINDER_UNRESOLVED:' + entry.command_id)
                if entry.order.command_certainty == 'CONFIRMED' and entry.order.pending_operation == 'NONE':
                    key = plan.plan_id + ':cancel-entry:' + entry.order.order.identity.logical_client_order_id
                    # Do not mint another cancel on later fill notifications.
                    if previous is None:
                        request = CommandRequest(key=key, operation='CANCEL', position=position,
                            plan=plan, quantity=entry.order.leaves_quantity, target=entry.order.order)
                        try:
                            compiled = self.compile_command(request)
                            validate_command(request, compiled)
                            commands.append(compiled.command)
                        except Exception:
                            missing.append('ENTRY_CANCEL_COMPILATION_FAILED')
        ids = tuple(dict.fromkeys((*original_ids, *(c.command_id for c in commands))))
        record = ProtectionRecord(position=position, plan=plan, revision=1 if previous is None else previous.revision + 1,
            fill_hashes=(*(previous.fill_hashes if previous else ()), digest(fill)), entries=tuple(entries.values()),
            command_ids=ids, locks=locks, missing=tuple(missing),
            case=self._case(position, ids, missing, previous.case if previous else None),
            replacement=previous.replacement if previous else None,
            exit_receipt=previous.exit_receipt if previous else None,
            exit_context_hash=previous.exit_context_hash if previous else None,
            exit_started_at=previous.exit_started_at if previous else None)
        return self._commit_dispatch(record, previous, commands)

    def verify(self, plan: t.ProtectionPlan, evidence: t.VenueReport) -> t.ProtectionVerdict:
        try:
            record = self.repository.load_plan(plan.scope, plan.plan_id)
        except Exception:
            record = None
        if record is None or record.plan != plan:
            return t.ProtectionVerdict(state=t.ProtectionState.GAP,
                covered_quantity=t.Quantity(value=Decimal(0), unit=plan.coverage_quantity.unit),
                missing=('CURRENT_PLAN_BINDING_REQUIRED',), evidence=())
        if record.exit_receipt is not None:
            return t.ProtectionVerdict(state=record.plan.state,
                covered_quantity=t.Quantity(value=Decimal(0), unit=plan.coverage_quantity.unit),
                missing=('EXIT_REQUIRES_FLAT_VERIFICATION',), evidence=())
        verdict = self.verifier.verify(plan, evidence, record.position)
        now = self.clock()
        updated_plan = replace(plan, state=verdict.state,
            last_verified_at=evidence.as_of if not verdict.missing else None,
            coverage_gap_since=None if not verdict.missing else plan.coverage_gap_since or now)
        missing = verdict.missing or ('CAUSE_LOCK_RESOLUTION_REQUIRED',)
        updated = replace(record, plan=updated_plan, revision=record.revision + 1, missing=missing,
                          case=self._case(record.position, record.command_ids, missing, record.case))
        try:
            self.repository.save(updated, record)
        except Exception:
            return replace(verdict, state=t.ProtectionState.GAP,
                covered_quantity=t.Quantity(value=Decimal(0), unit=plan.coverage_quantity.unit),
                missing=(*verdict.missing, 'STORAGE_UNAVAILABLE'))
        # Deliberately never resolve an IP-08 cause lock on a successful response.
        return verdict

    def replace_protection(self, position: t.ManagedPosition, new: t.ProtectionPlan,
                           capability: NativeCapability, *, approved_flow=None,
                           report: t.VenueReport | None = None,
                           read_report_proof=None) -> t.ProtectionActionPlan:
        offline(position.scope)
        record = self.repository.load(position.scope, position.position_id)
        if record is None or record.position != position:
            raise ValueError('CURRENT_POSITION_REQUIRED')
        if record.exit_receipt is not None:
            return self._action(record, extra=('EXIT_IN_PROGRESS',))
        if capability.errors(position, self.clock()) or not capability.tradable(new.coverage_quantity):
            return self._action(record, extra=('CAPABILITY_OR_QUANTITY_INVALID',))
        if (new.coverage_quantity.value != position.actual_quantity.value.copy_abs()
                or new.coverage_quantity.unit != position.actual_quantity.unit
                or new.stop_trigger not in capability.triggers or 'STOP_MARKET' not in capability.order_types):
            return self._action(record, extra=('REPLACEMENT_PARAMETERS_INVALID',))
        commands = self.repository.commands(position.scope, record.command_ids)
        if any(c.state not in {'VENUE_CONFIRMED', 'VENUE_REJECTED', 'ABORTED_UNSENT', 'NOT_SENT'} for c in commands):
            return self._action(record, extra=('QUERY_ORIGINAL_BEFORE_REPLACEMENT',))
        progress = record.replacement
        if progress is not None and progress.phase in {'COMPLETE', 'BLOCKED'}:
            progress = None
        if progress is None:
            decision = start(record.plan, new, capability, approved_flow, self.clock())
        else:
            if (new.plan_id != progress.new.plan_id
                    or new.replacement_revision != progress.new.replacement_revision
                    or new.stop_price != progress.new.stop_price
                    or new.stop_trigger != progress.new.stop_trigger
                    or new.scope != progress.new.scope or new.mode != progress.new.mode
                    or new.coverage_quantity != progress.new.coverage_quantity):
                raise ValueError('REPLACEMENT_ALREADY_IN_PROGRESS')
            # Terminal old-order evidence also needs archive authentication,
            # not just plausible fields in a caller-supplied VenueReport.
            try:
                report_ok = (not report_errors(report, position.scope, self.clock(), 60)
                    and position_reported(report, position)
                    and utc_timestamp(report.as_of) >= utc_timestamp(progress.started_at)
                    and read_report_proof is not None
                    and read_report_proof(record, report) == digest(report))
            except Exception:
                report_ok = False
            if not report_ok:
                return self._action(record, extra=('REPLACEMENT_REPORT_UNVERIFIED',))
            verified = self.verifier.verify(progress.new, report, position)
            decision = advance(progress, report,
                new_verified=verified.state == t.ProtectionState.VERIFIED_NATIVE)
        plan = record.plan
        commands = []
        progress = decision.progress
        if decision.operation:
            request = CommandRequest(key=new.plan_id + ':replace:' + str(new.replacement_revision)
                + ':' + progress.phase, operation=decision.operation, position=position,
                plan=progress.new, quantity=new.coverage_quantity, target=decision.target)
            try:
                compiled = self.compile_command(request)
                validate_command(request, compiled)
            except Exception:
                plan, locks = self._gap(position, record.plan, record, self.clock())
                missing = ('COMMAND_COMPILATION_FAILED', 'REPLACEMENT_RECONCILIATION_REQUIRED')
                updated = replace(record, plan=plan, locks=locks, revision=record.revision + 1,
                    missing=missing, case=self._case(position, record.command_ids, missing, record.case))
                return self._commit_dispatch(updated, record)
            commands.append(compiled.command)
            if decision.operation in {'SET_PROTECTION', 'AMEND'}:
                order = decision.target if decision.operation == 'AMEND' else compiled.order
                progress = replace(progress, new=replace(progress.new, native_order_ids=(order,),
                    state=t.ProtectionState.PENDING, last_verified_at=None,
                    coverage_gap_since=progress.gap_since))
                plan = progress.new
        if progress.gap_since:
            plan = replace(plan, state=t.ProtectionState.GAP, last_verified_at=None,
                           coverage_gap_since=progress.gap_since)
        if progress.phase == 'COMPLETE':
            plan = replace(progress.new, state=t.ProtectionState.VERIFIED_NATIVE,
                last_verified_at=report.as_of, coverage_gap_since=None)
        ids = (*record.command_ids, *(c.command_id for c in commands))
        missing = decision.missing or (('CAUSE_LOCK_RESOLUTION_REQUIRED',)
            if progress.phase == 'COMPLETE' else ('REPLACEMENT_RECONCILIATION_REQUIRED',))
        updated = replace(record, plan=plan, replacement=progress, revision=record.revision + 1,
            command_ids=ids, missing=missing, case=self._case(position, ids, missing, record.case))
        return self._commit_dispatch(updated, record, commands)
