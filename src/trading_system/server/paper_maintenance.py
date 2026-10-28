"""Independent PAPER safety maintenance and explicit next-risk-day review.

No network/model clients or event injection. Review policy is deliberately
conservative: next configured day, completely flat account, no working orders.
"""
from dataclasses import dataclass, replace
from decimal import Decimal
from zoneinfo import ZoneInfo
import time

@dataclass(frozen=True)
class ReviewArchive:
    data: dict


from trading_system.contracts import types as t
from trading_system.contracts.wire import ExecutionCommand
from trading_system.contracts.canonical import canonical_hash, domain_payload, utc_timestamp
from trading_system.engine.paper import PaperEngine
from trading_system.execution.commands import CommandRepository
from trading_system.risk.locks import RiskLockService
from trading_system.storage.models import decode, scope_id
from trading_system.storage.uow import UnitOfWork


def run_owner(runtime, *, stopped=lambda: False, sleep=time.sleep, report=print):
    """Survive transient DB/API failures; never suppress an operator-visible fault."""
    failed = False
    while not stopped():
        try:
            result = runtime.tick()
            problems = [r for r in result if r.get('error_type') or r.get('status') in ('RECONCILIATION_REQUIRED', 'UNKNOWN')]
            if problems:
                report('PAPER_OWNER_NEEDS_OPERATOR_REVIEW ' + str(len(problems)))
            if failed:
                report('PAPER_OWNER_STORAGE_RESTORED; durable locks and original IDs retained')
            failed = False
        except Exception as exc:
            report('PAPER_OWNER_NEEDS_OPERATOR_REVIEW ' + type(exc).__name__)
            failed = True
        sleep(1)


def retire_expired_unsent(runtime, scope):
    """Fresh absence plus SQL's unsent boundary proof; never infer from age alone."""
    from .paper_runtime import OWNER
    with runtime.control_guard(), runtime.uow.transaction(scope) as tx:
        repo = CommandRepository(tx)
        for identifier, in tx.execute("SELECT record_id FROM ledger.commands WHERE scope_id=%s AND sequence=1 "
                "AND payload->>'operation'='SET_PROTECTION'", (scope_id(scope),)).fetchall():
            command = repo.get(identifier)
            if command.state != 'DURABLE_READY':
                continue
            request = runtime._request(tx, identifier)
            if request is None or request.mode != 'MAINTENANCE':
                continue
            now = utc_timestamp(runtime.clock().utc_now)
            if now < min(utc_timestamp(request.intent.valid_until),
                    utc_timestamp(request.authorization.authorization.expires_at)):
                continue
            engine = PaperEngine(scope, dsn=runtime.venue_dsn, authority_guard=lambda _: None,
                owner_strategy_id=OWNER, quantity_unit='BASE', price_currency='USDT', clock=runtime.clock)
            source = engine.query(runtime._query_case(scope, identifier))
            if (not source.coverage.query_succeeded or not source.coverage.pagination_complete
                    or not source.coverage.supported
                    or set(source.coverage.missing) != {'COMMAND_NOT_OBSERVED',
                        'PAPER_BALANCES_POSITIONS_PROTECTION_NOT_COVERED'}
                    or source.orders or source.fills):
                raise ValueError('EXPIRED_READY_ORIGINAL_ID_ABSENCE_UNPROVEN')
            tx._append('audit_events', 'console:unsent-maintenance-query:' + identifier,
                ReviewArchive({'command_id': identifier, 'report': domain_payload(source),
                    'reason': 'EXPIRED_MAINTENANCE_GRANT_OR_INTENT'}), 0)
            # The definer function verifies immutable READY under this same
            # collateral mutex; MAY_HAVE_SENT/UNKNOWN cannot become unsent.
            repo.abort_unsent(identifier, 'EXPIRED_MAINTENANCE_QUERY_ABSENT_UNSENT_BOUNDARY')


def maintain(runtime):
    from .paper_runtime import SYMBOLS, scope_for
    results = []
    for symbol in SYMBOLS:
        scope = scope_for(symbol)
        try:
            retire_expired_unsent(runtime, scope)
            with runtime.uow.transaction(scope) as tx:
                plan = tx._latest('protection_plans', 'protection:' + scope_id(scope), t.ProtectionPlan)
                if plan is None or plan.state not in ('GAP', 'REPAIRING') or plan.coverage_quantity.value <= 0:
                    continue
                if hasattr(runtime, 'pending_protected_adjustment') and runtime.pending_protected_adjustment(tx):
                    continue
                pending = False
                retired = []
                for raw, in tx.execute("SELECT payload FROM ledger.commands WHERE scope_id=%s AND sequence=1 "
                        "AND payload->>'operation'='SET_PROTECTION'", (scope_id(scope),)).fetchall():
                    command = CommandRepository(tx).get(decode(ExecutionCommand, raw).command_id)
                    if command.state == 'ABORTED_UNSENT':
                        retired.append(command.command_id)
                        continue
                    projected = tx._latest('order_projections', command.logical_client_order_id, t.OrderProjection)
                    if projected is None or projected.lifecycle_state not in ('FILLED','CANCELED','EXPIRED','REJECTED'):
                        pending = True
                if pending:
                    continue
                row = tx.execute("SELECT record_id FROM ledger.commands WHERE scope_id=%s AND sequence=1 "
                    "AND payload->>'operation'='SUBMIT' AND payload->'canonical_payload'->>'side'='BUY' "
                    "ORDER BY processed_at DESC LIMIT 1", (scope_id(scope),)).fetchone()
            if row:
                duty = runtime.protect(row[0], 'owner-duty:' + str(plan.replacement_revision)
                    + ':' + canonical_hash({'retired': sorted(retired)}))
                results.append(runtime.dispatch(duty['command_id']))
        except Exception as exc:
            results.append({'scope': scope_id(scope), 'status': 'RECONCILIATION_REQUIRED', 'error_type': type(exc).__name__})
    return results


def review_daily(runtime, lock_id, *, resolver_dsn, operator_review_id):
    """Explicit CLI-only trusted review; ordinary runtime cannot resolve locks."""
    from .paper_runtime import scope_for, SYMBOLS, OWNER
    from .store import cause_locks, state
    from sqlalchemy import select, update
    if not isinstance(operator_review_id, str) or not operator_review_id.strip():
        raise ValueError('EXPLICIT_OPERATOR_REVIEW_REQUIRED')
    store = getattr(runtime, 'console_store', None)
    if store is None:
        raise ValueError('BOUND_CONSOLE_REQUIRED')
    scope = scope_for('BTCUSDT')
    # Serialize the complete review with commands and console mutations.
    with runtime.control_guard(), store.transaction() as connection:
        record = connection.execute(select(cause_locks.c.data).where(cause_locks.c.id == lock_id)).scalar_one_or_none()
        config = connection.execute(select(state.c.data).where(state.c.id == 1)).scalar_one()
        if not record:
            raise ValueError('DAILY_CAUSE_ARCHIVE_REQUIRED')
        zone = config['risk'].get('risk_day_timezone', 'Asia/Shanghai')
        today = utc_timestamp(runtime.clock().utc_now).astimezone(ZoneInfo(zone)).date().isoformat()
        if record.get('risk_day_timezone') != zone or not record.get('risk_day') or record['risk_day'] >= today:
            raise ValueError('NEXT_CONFIGURED_RISK_DAY_REVIEW_REQUIRED')
        reports = {}
        for symbol in SYMBOLS:
            current_scope = scope_for(symbol)
            engine = PaperEngine(current_scope, dsn=runtime.venue_dsn, authority_guard=lambda _: None,
                owner_strategy_id=OWNER, quantity_unit='BASE', price_currency='USDT', clock=runtime.clock)
            for command_id in runtime._command_ids(current_scope):
                runtime._reconcile(engine, current_scope, command_id)
            report = engine.query(replace(runtime._query_case(current_scope, ''), command_ids=()))
            if (not report.coverage.query_succeeded or not report.coverage.pagination_complete
                    or report.positions or any(o.lifecycle_state not in ('FILLED','CANCELED','EXPIRED','REJECTED')
                        or o.command_certainty != 'CONFIRMED' or o.pending_operation != 'NONE' for o in report.orders)):
                raise ValueError('FLAT_COMPLETE_ACCOUNT_REQUIRED')
            reports[current_scope] = report
        archive = {'cause': record, 'review_id': operator_review_id, 'reviewed_day': today,
            'policy': 'NEXT_CONFIGURED_DAY_FLAT_REVIEW_REQUIRED', 'risk_revision': config['risk']['revision'],
            'reports': [domain_payload(r) for r in reports.values()]}
        identifier = 'console:daily-review:' + __import__('uuid').uuid4().hex
        ref = runtime._ref(identifier, archive)
        with runtime.uow.transaction(scope) as tx:
            lock = tx._latest('risk_locks', lock_id, t.RiskLock)
            if lock is None or lock.cause != 'DAILY_LOSS':
                raise ValueError('DAILY_LOCK_REQUIRED')
            tx._append('audit_events', identifier, ReviewArchive(archive), 0)
            runtime._ensure_lock_event(tx, lock, ref)
        def verify(tx, lock, requirement, evidence):
            if requirement != 'DAILY_LOSS_REVIEW' or evidence.authorization_id != operator_review_id:
                return False
            if tx._latest('audit_events', 'console:daily-cause:' + lock_id, ReviewArchive) != ReviewArchive(record):
                return False
            if tx._latest('audit_events', identifier, ReviewArchive) != ReviewArchive(archive):
                return False
            for current_scope, report in reports.items():
                # Resolver transaction holds the shared collateral mutex. Query
                # every scope explicitly; UoW's default scope must not hide facts.
                saved = tuple(decode(t.Fill, row[0]) for row in tx.execute(
                    'SELECT payload FROM ledger.fills WHERE scope_id=%s', (scope_id(current_scope),)).fetchall())
                def economic(fill):
                    return replace(fill, evidence=replace(fill.evidence, visible_at='2000-01-01T00:00:00Z'))
                if sorted(map(economic,saved), key=lambda f:f.fill_id) != sorted(map(economic,report.fills),key=lambda f:f.fill_id):
                    return False
                bookrow = tx.execute('SELECT payload FROM ledger.book_snapshots WHERE scope_id=%s ORDER BY sequence DESC LIMIT 1',
                    (scope_id(current_scope),)).fetchone()
                if bookrow:
                    book = decode(t.BookSnapshot, bookrow[0])
                    if book.positions or book.active_orders:
                        return False
                if tx.execute("SELECT 1 FROM ledger.commands c WHERE scope_id=%s AND sequence=1 "
                    "AND NOT EXISTS (SELECT 1 FROM ledger.commands n WHERE n.scope_id=c.scope_id "
                    "AND n.record_id=c.record_id AND n.payload->>'state' IN ('VENUE_CONFIRMED','VENUE_REJECTED'))",
                    (scope_id(current_scope),)).fetchone():
                    return False
            cash = runtime._cash(tx)
            baseline = store._day_start(connection, config['account']['initial_cash'])
            return (baseline - cash < Decimal(config['risk']['max_daily_loss'])
                and all(lock.cause == 'DAILY_LOSS' for lock in tx.risk_locks.applicable(scope)))
        service = RiskLockService(runtime.uow, clock=lambda: runtime.clock().utc_now, verify=verify,
            resolver_uow=UnitOfWork(lambda: runtime._resolver_connection(resolver_dsn)))
        result = service.resolve(lock_id, t.ResolutionEvidence(lock_id=lock_id, scope=scope,
            evidence=(ref,), authorization_id=operator_review_id))
        if result.resolved:
            connection.execute(update(cause_locks).where(cause_locks.c.id == lock_id).values(data=dict(record, state='RESOLVED',
                resolution_ref=identifier, operator_review_id=operator_review_id)))
        return domain_payload(result)
